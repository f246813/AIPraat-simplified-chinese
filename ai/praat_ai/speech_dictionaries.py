"""Configured speech dictionaries and acoustic models in one assistant window."""
from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path

from .config import default_config_path
from .service_lock import service_transition_lock
from .mfa_resources import MfaResourceService
from .paths import installation_root


def path_key(value):
    return os.path.normcase(str(Path(value).expanduser().resolve()))


class DictionaryService:
    section_key = 'mfa'
    directory = False
    active_key = 'dictionary_path'
    paths_key = 'dictionary_paths'
    result_key = 'dictionaries'
    label = '词典'
    file_filter = '语音词典 (*.dict;*.txt;*.lex;*.lexicon;*.tsv)'

    def __init__(self, config_path):
        self.path = Path(config_path)

    def _read(self):
        return json.loads(self.path.read_text(encoding='utf-8-sig')) if self.path.is_file() else {}

    def _paths(self, raw):
        mfa = raw.get('alignment', {}).get(self.section_key, {})
        values = [mfa.get(self.active_key, ''), *mfa.get(self.paths_key, [])]
        result, seen = [], set()
        for value in values:
            if not value:
                continue
            key = path_key(value)
            if key not in seen:
                result.append(str(value))
                seen.add(key)
        return result

    def _result(self, raw):
        active = raw.get('alignment', {}).get(self.section_key, {}).get(self.active_key, '')
        kind = 'dictionary' if self.active_key == 'dictionary_path' else 'acoustic'
        resources = MfaResourceService.catalog(raw)[kind] if self.section_key == 'mfa' else []

        def used_by(value):
            return sorted({preset for resource in resources if path_key(resource['path']) == path_key(value)
                           for preset in resource['used_by']})
        return {self.result_key: [dict(path=value, name=Path(value).name,
                                      exists=self._exists(value),
                                      active=bool(active) and path_key(value) == path_key(active),
                                      used_by=used_by(value))
                                  for value in self._paths(raw)],
                'theme': raw.get('modern', {}).get('preferences', {}).get('theme', 'system')}

    def _exists(self, value):
        return Path(value).is_dir() if self.directory else Path(value).is_file()

    def list(self):
        return self._result(self._read())

    def _change(self, update):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with service_transition_lock(Path(str(self.path) + '.lock')):
            raw = self._read()
            paths = self._paths(raw)
            mfa = raw.setdefault('alignment', {}).setdefault(self.section_key, {})
            update(paths, mfa)
            mfa[self.paths_key] = paths
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.path.parent,
                                                 prefix=self.path.name + '.', suffix='.tmp', delete=False) as handle:
                    temporary = Path(handle.name)
                    handle.write(json.dumps(raw, ensure_ascii=False, indent=2) + '\n')
                os.replace(temporary, self.path)
            finally:
                if temporary:
                    temporary.unlink(missing_ok=True)
            return self._result(raw)

    def add(self, values):
        values = [str(Path(value).expanduser().resolve()) for value in values]
        for value in values:
            if not self._exists(value):
                raise ValueError(self.label + ('目录' if self.directory else '文件') + '不存在：' + value)
        if not values:
            return self.list()

        def update(paths, mfa):
            seen = {path_key(value) for value in paths}
            for value in values:
                if path_key(value) not in seen:
                    paths.append(value)
                    seen.add(path_key(value))
            if not mfa.get(self.active_key):
                mfa[self.active_key] = paths[0]
        return self._change(update)

    def select(self, value):
        key = path_key(value)

        def update(paths, mfa):
            selected = next((path for path in paths if path_key(path) == key), None)
            if selected is None:
                raise ValueError('该' + self.label + '尚未配置，请先添加。')
            if not self._exists(selected):
                raise ValueError(self.label + ('目录' if self.directory else '文件') + '不存在：' + selected)
            mfa[self.active_key] = selected
        return self._change(update)

    def remove(self, value):
        key = path_key(value)

        def update(paths, mfa):
            paths[:] = [path for path in paths if path_key(path) != key]
            active = mfa.get(self.active_key, '')
            if active and path_key(active) == key:
                mfa[self.active_key] = paths[0] if paths else ''
        return self._change(update)


class AcousticModelService(DictionaryService):
    active_key = 'acoustic_model'
    paths_key = 'acoustic_models'
    result_key = 'models'
    label = '声学模型'
    file_filter = 'MFA 声学模型 (*.zip)'


class Wav2Vec2ModelService(DictionaryService):
    section_key = 'wav2vec2'
    directory = True
    active_key = 'model'
    paths_key = 'models'
    result_key = 'models'
    label = 'Wav2Vec2声学模型'


class DictionaryApplication:
    def __init__(self, config_path):
        self._service = DictionaryService(config_path)
        self._models = AcousticModelService(config_path)
        self._wav2vec2 = Wav2Vec2ModelService(config_path)
        self._mfa_resources = MfaResourceService(config_path)
        self.window = None
        self._guard = threading.Lock()

    def rpc(self, method, params):
        # Native pickers and launcher probing must not hold the close/UI guard.
        if method.startswith('mfa.'):
            return self._mfa_resources.rpc(method, params, window=self.window)
        with self._guard:
            namespace, _, action = method.partition('.')
            service = {'dictionaries': self._service, 'acoustic_models': self._models,
                       'wav2vec2_models': self._wav2vec2}.get(namespace)
            if service is None:
                raise ValueError('未知的词典与模型管理操作')
            if action == 'list':
                return service.list()
            if action == 'remove':
                return service.remove(params['path'])
            if action == 'select':
                return service.select(params['path'])
            if action == 'choose':
                import webview
                initial = installation_root() / 'models'
                if service.section_key == 'mfa':
                    initial = initial / 'mfa' / 'pretrained_models' / ('dictionary' if service.active_key == 'dictionary_path' else 'acoustic')
                initial = initial if initial.is_dir() else installation_root()
                if service.directory:
                    paths = self.window.create_file_dialog(webview.FileDialog.FOLDER, directory=str(initial)) or []
                else:
                    paths = self.window.create_file_dialog(webview.FileDialog.OPEN, allow_multiple=True,
                        directory=str(initial), file_types=(service.file_filter, '所有文件 (*.*)')) or []
                return service.add(paths)
            raise ValueError('未知的词典与模型管理操作')


def main():
    server = None
    try:
        import webview
        from .modern_host import AssetServer, watch_native_parent
        from .modern_app import HostAPI
        assets = Path(__file__).resolve().parents[1] / 'frontend' / 'dist'
        if not (assets / 'index.html').is_file():
            raise RuntimeError('词典与模型管理界面资产缺失，请重新构建前端。')
        app = DictionaryApplication(default_config_path())
        server = AssetServer(assets)
        api = HostAPI(app)
        app.window = webview.create_window('管理语音词典与模型', server.url + '?window=dictionaries',
            js_api=api, width=780, height=480, min_size=(620, 360), text_select=True)
        api._origin = server.url.rsplit('/', 1)[0]
        webview.start(lambda: watch_native_parent(app.window), gui='edgechromium' if os.name == 'nt' else None,
                      private_mode=True)
        return 0
    except Exception as error:
        from .desktop_launch import report_failure
        return report_failure(error, config_path=default_config_path())
    finally:
        if server:
            server.close()
