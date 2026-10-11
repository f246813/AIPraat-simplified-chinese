"""Offline Anki connection settings and exporter in the existing desktop UI."""
import os
from pathlib import Path
from . import anki_bridge


class AnkiApplication:
    def __init__(self):
        self.window = None

    def rpc(self, method, params):
        import webview
        if method == 'anki.settings':
            return dict(settings=anki_bridge.settings(), code=anki_bridge.entry_code())
        if method == 'anki.save':
            value = anki_bridge.save_settings(params)
            return dict(settings=value, code=anki_bridge.entry_code(), message='已保存连接设置。已有 Anki 插件请重新导出安装，或编辑插件目录的 entry.js。')
        if method == 'anki.choose':
            key = params['key']
            if key not in ('media_dir', 'ffmpeg', 'test_audio'):
                raise ValueError('未知路径字段')
            selected = self.window.create_file_dialog(
                webview.FileDialog.FOLDER if key == 'media_dir' else webview.FileDialog.OPEN,
                file_types=('音频 (*.wav;*.mp3;*.m4a;*.flac;*.ogg;*.opus)', '所有文件 (*.*)') if key == 'test_audio' else ('所有文件 (*.*)',))
            return dict(path=selected[0] if selected else '')
        if method in ('anki.export', 'anki.code'):
            filename = 'AIPraat.ankiaddon' if method == 'anki.export' else 'entry.js'
            selected = self.window.create_file_dialog(webview.FileDialog.SAVE, save_filename=filename)
            if not selected:
                return dict(message='已取消导出。')
            target = Path(selected if isinstance(selected, str) else selected[0])
            if method == 'anki.export':
                anki_bridge.export_addon(target)
            else:
                target.write_text(anki_bridge.entry_code(), encoding='utf-8')
            return dict(message=f'已导出：{target}')
        if method == 'anki.open':
            result = anki_bridge.open_audio(**params)
            return dict(**result, message='已打开语图' + ('，使用临时 WAV 备用解码。' if result['converted'] else '，使用原生音频读取。'))
        raise ValueError('未知 Anki 操作')


def main():
    server = None
    try:
        import webview
        from .modern_host import AssetServer, watch_native_parent
        from .modern_app import HostAPI
        app = AnkiApplication()
        server = AssetServer(Path(__file__).resolve().parents[1] / 'frontend' / 'dist')
        api = HostAPI(app)
        app.window = webview.create_window('Anki 连接 · AIPraat', server.url + '?window=anki', js_api=api,
                                          width=860, height=740, min_size=(660, 540), text_select=True)
        api._origin = server.url.rsplit('/', 1)[0]
        webview.start(lambda: watch_native_parent(app.window), gui='edgechromium' if os.name == 'nt' else None, private_mode=True)
        return 0
    except Exception as error:
        from .desktop_launch import report_failure
        return report_failure(error)
    finally:
        if server:
            server.close()
