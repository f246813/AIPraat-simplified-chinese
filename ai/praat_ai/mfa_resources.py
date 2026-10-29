"""Shared, local-only MFA resource registry and pronunciation preset validation.

Resource paths enter through a native picker (or trusted Python ``add``), never
through binding RPCs. Listing is read-only and never launches MFA.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import os
import re
import shutil
import tempfile
import uuid
import zipfile
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any

from .config import pronunciation_presets
from .service_lock import service_transition_lock
from .paths import installation_root

KINDS = ('g2p', 'dictionary', 'acoustic')
IDENTITY_FIELDS = ('id', 'name', 'language', 'variant', 'rule_id')
LAUNCHER_DEFAULT = dict(executable='mfa', conda_executable='', conda_environment='')
# The installer embeds this same stdlib-only file as a resource, so both UIs report
# identical facts about one environment.
ALIGNMENT_PROBE = Path(__file__).resolve().parent.parent / 'mfa_alignment_probe.py'
# These public aliases describe the JSON-compatible API, not dataclass instances.
Preset = dict[str, Any]
Launcher = dict[str, str]
Resource = dict[str, Any]
_NUMBER = re.compile(r'^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$')


@lru_cache(maxsize=1)
def alignment_probe():
    """Loads the shipped alignment-environment probe by path.

    It must stay importable without this package (the installer runs it standalone),
    so it is loaded by path instead of becoming a package module.
    """
    if not ALIGNMENT_PROBE.is_file():
        raise FileNotFoundError('对齐环境探针缺失：' + str(ALIGNMENT_PROBE))
    spec = importlib.util.spec_from_file_location('praat_ai_alignment_probe', ALIGNMENT_PROBE)
    if spec is None or spec.loader is None:
        raise ImportError('无法加载对齐环境探针：' + str(ALIGNMENT_PROBE))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _path_key(path):
    return os.path.normcase(str(Path(path).expanduser().resolve()))


def legacy_resource_id(kind, path):
    return 'legacy-' + kind + '-' + hashlib.sha256(_path_key(path).encode('utf-8')).hexdigest()[:24]


def parse_dictionary(path) -> dict[str, list[list[str]]]:
    candidate = Path(path).resolve()
    stat = candidate.stat()
    return deepcopy(_parse_dictionary(str(candidate), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns))


@lru_cache(maxsize=4)
def _parse_dictionary(path, size, mtime, ctime) -> dict[str, list[list[str]]]:
    result = {}
    for word, phones in _dictionary_rows(path):
        candidates = result.setdefault(word, [])
        if phones not in candidates:
            candidates.append(phones)
    return result


def _dictionary_rows(path):
    """Read UTF-8/BOM MFA dictionaries, including 1--4 probability columns.

    Numeric columns are only legal between the word and first phone. Invalid
    rows fail loudly instead of treating a probability as a phone or dropping it.
    """
    checked_phones, found = set(), False
    with Path(path).open(encoding='utf-8-sig') as handle:
        for number, line in enumerate(handle, 1):
            line = line.strip()
            if not line or line.startswith(('#', ';')):
                continue
            fields = line.split()
            if len(fields) < 2:
                raise ValueError(f'词典第 {number} 行缺少音位')
            word, *phones = fields
            probabilities = []
            while phones and _NUMBER.fullmatch(phones[0]):
                probabilities.append(float(phones.pop(0)))
            invalid = len(probabilities) > 4 or any(not math.isfinite(p) or p < 0 for p in probabilities)
            invalid = invalid or (bool(probabilities) and probabilities[0] > 1) or not phones
            for phone in phones:
                if phone not in checked_phones:
                    invalid = invalid or bool(_NUMBER.fullmatch(phone)) or phone.casefold() in {'nan', 'inf', '+inf', '-inf'} or any(ord(c) < 32 for c in phone)
                    checked_phones.add(phone)
            if invalid:
                raise ValueError(f'词典第 {number} 行的概率或音位格式无效')
            found = True
            yield word.casefold(), phones
    if not found:
        raise ValueError('词典没有可用的读音')


def dictionary_inventory(path):
    """Page checks retain only unique labels, never the complete lexicon."""
    candidate = Path(path).resolve()
    stat = candidate.stat()
    return _dictionary_inventory(str(candidate), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino)


@lru_cache(maxsize=16)
def _dictionary_inventory(path, size, mtime, ctime, inode):
    phones = set()
    for _word, labels in _dictionary_rows(path):
        phones.update(labels)
    return frozenset(phones)


def dictionary_pronunciations(path, words):
    """Read requested entries without allocating/copying a full dictionary."""
    requested, result = {word.casefold() for word in words}, {}
    for word, phones in _dictionary_rows(path):
        if word in requested:
            candidates = result.setdefault(word, [])
            if phones not in candidates:
                candidates.append(phones)
    return result


def _simple_yaml(text):
    """Conservative fallback for ordinary scalar/list model metadata.

    No tags, constructors, anchors, or arbitrary objects are interpreted. More
    complex YAML is deliberately reported unknown when PyYAML is unavailable.
    """
    result, key = {}, None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue
        if any(marker in stripped for marker in ('!!', '&', '*', '!<')):
            raise ValueError('Unsupported YAML metadata')
        if stripped.startswith('- ') and key:
            if not isinstance(result.get(key), list):
                raise ValueError('Unsupported YAML metadata')
            result.setdefault(key, []).append(stripped[2:].strip().strip('\"\''))
            continue
        match = re.match(r'^([\w]+):\s*(.*)$', line)
        if not match:
            raise ValueError('Unsupported YAML metadata')
        key, value = match.groups()
        value = value.split(' #', 1)[0].strip()
        if not value:
            result[key] = []
        elif value.startswith('[') and value.endswith(']'):
            result[key] = [p.strip().strip('\"\'') for p in value[1:-1].split(',') if p.strip()]
        else:
            result[key] = value.strip('\"\'')
    return result


def read_model_metadata(path) -> dict[str, Any]:
    """Read bounded metadata from ZIP members without ever extracting a file."""
    try:
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            if len(members) > 10000:
                return {}
            candidates = [m for m in members if Path(m.filename).name.casefold()
                          in {'meta.json', 'meta.yaml', 'meta.yml', 'metadata.json', 'metadata.yaml'}]
            candidates.sort(key=lambda m: (not m.filename.casefold().endswith('.json'), m.filename))
            for member in candidates:
                if member.file_size > 2 * 1024 * 1024 or member.flag_bits & 1:
                    continue
                try:
                    with archive.open(member) as handle:
                        content = handle.read(2 * 1024 * 1024 + 1)
                    if len(content) > 2 * 1024 * 1024:
                        continue
                    text = content.decode('utf-8-sig')
                    if member.filename.casefold().endswith('.json'):
                        data = json.loads(text)
                    else:
                        try:
                            import yaml
                        except ImportError:
                            data = _simple_yaml(text)
                        else:
                            try:
                                data = yaml.safe_load(text)
                            except yaml.YAMLError:
                                continue
                    if isinstance(data, dict):
                        return data
                except (ValueError, UnicodeError, RuntimeError):
                    continue
    except (OSError, zipfile.BadZipFile, RuntimeError):
        pass
    return {}


def _phones(metadata):
    value = metadata.get('phones', metadata.get('phone_inventory', metadata.get('phone_set')))
    if isinstance(value, dict):
        value = list(value)
    result = set(value) if isinstance(value, (list, tuple)) and value and all(isinstance(p, str) and p.strip() for p in value) else set()
    mapping = metadata.get('phone_mapping')
    if isinstance(mapping, dict) and all(isinstance(p, str) and p.strip() for p in mapping):
        result.update(mapping)
    for key in ('oov_phone', 'optional_silence_phone'):
        if isinstance(metadata.get(key), str) and metadata[key]:
            result.add(metadata[key])
    return result or None


# Model metadata writes language names ("japanese"), presets write codes ("ja"). Without
# one shared normalization every non-English pair reported "模型语言与所选语言不符".
LANGUAGE_CODES = {
    'english': 'en', 'eng': 'en', 'japanese': 'ja', 'jpn': 'ja', 'chinese': 'zh', 'cmn': 'zh',
    'mandarin': 'zh', 'korean': 'ko', 'kor': 'ko', 'german': 'de', 'deu': 'de', 'ger': 'de',
    'french': 'fr', 'fra': 'fr', 'fre': 'fr', 'spanish': 'es', 'spa': 'es', 'russian': 'ru',
    'rus': 'ru', 'portuguese': 'pt', 'por': 'pt', 'italian': 'it', 'ita': 'it', 'dutch': 'nl',
    'nld': 'nl', 'polish': 'pl', 'pol': 'pl', 'turkish': 'tr', 'tur': 'tr', 'arabic': 'ar',
    'ara': 'ar', 'hindi': 'hi', 'hin': 'hi', 'thai': 'th', 'tha': 'th', 'vietnamese': 'vi',
    'vie': 'vi', 'swedish': 'sv', 'swe': 'sv', 'finnish': 'fi', 'fin': 'fi', 'danish': 'da',
    'norwegian': 'no', 'greek': 'el', 'ell': 'el', 'hebrew': 'he', 'heb': 'he', 'czech': 'cs',
    'ces': 'cs', 'ukrainian': 'uk', 'ukr': 'uk', 'indonesian': 'id', 'ind': 'id', 'malay': 'ms',
}


def _language(value):
    if isinstance(value, str):
        primary = value.strip().lower().replace('_', '-').split('-')[0]
        if primary in {'unknown', 'und', 'none', 'null', 'multilingual', 'n/a'}:
            return ''
        return LANGUAGE_CODES.get(primary, primary)
    return ''


def validate_launcher(value) -> Launcher:
    if not isinstance(value, dict):
        raise ValueError('MFA 启动配置必须是对象')
    result = {}
    for key in LAUNCHER_DEFAULT:
        item = value.get(key, LAUNCHER_DEFAULT[key])
        if not isinstance(item, str) or any(ord(c) < 32 for c in item):
            raise ValueError('MFA 启动配置无效')
        result[key] = item.strip()
    return result


def launcher_exists(launcher):
    # An explicit Conda selection takes priority, exactly as the execution API.
    if launcher['conda_executable'] or launcher['conda_environment']:
        executable = launcher['conda_executable']
        if not executable or not launcher['conda_environment']:
            return False
    else:
        executable = launcher['executable']
    return bool(executable and (Path(executable).expanduser().is_file() or shutil.which(executable)))


def validate_preset(value, *, enforce_identity=True) -> Preset:
    if not isinstance(value, dict):
        raise ValueError('读音预设必须是对象')
    builtin = next((p for p in pronunciation_presets() if p['id'] == value.get('id')), None)
    if builtin is None:
        if not isinstance(value.get('id'), str) or not re.fullmatch(r'custom-[a-f0-9]{32}', value['id']):
            raise ValueError('未知的语言读音预设 ID')
        for key in ('name', 'language', 'variant'):
            item = value.get(key, '')
            if (not isinstance(item, str) or len(item) > 120 or any(ord(c) < 32 for c in item)
                    or key != 'variant' and not item.strip()):
                raise ValueError('请填写有效的配套名称和语言代码')
        if not re.fullmatch(r'[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*', value['language'].strip()):
            raise ValueError('语言代码格式无效，例如 cmn、en、ja、de-DE')
        if value.get('rule_id') is not None:
            raise ValueError('自定义语言配套使用声学对比；音位判据需独立准备')
        result = {key: value.get(key, None if key == 'rule_id' else '') for key in IDENTITY_FIELDS}
        result.update(name=result['name'].strip(), language=result['language'].strip(), variant=result['variant'].strip())
    elif enforce_identity:
        for key in IDENTITY_FIELDS:
            if key in value and value[key] != builtin[key]:
                raise ValueError('语言读音预设的标识、名称与规则不可改写')
        result = deepcopy(builtin)
    else:
        result = deepcopy(builtin)
    for key in KINDS:
        item = value.get(key, '')
        if not isinstance(item, str):
            raise ValueError('资源引用必须是登记 ID')
        result[key] = item
    phone_set = value.get('phone_set', 'custom')
    if phone_set not in ('ipa', 'arpabet', 'custom'):
        raise ValueError('未知的音位体系')
    mapping = value.get('phone_map', {})
    if not isinstance(mapping, dict) or any(
            not isinstance(k, str) or not k or not isinstance(v, str) or not v
            or any(c.isspace() or ord(c) < 32 for c in k + v)
            or _NUMBER.fullmatch(v) for k, v in mapping.items()):
        raise ValueError('音位映射必须是原始标签到单个 IPA 单位的对象')
    from .phone_labels import validate_ipa
    for ipa in mapping.values():
        validate_ipa(ipa)
    result.update(phone_set=phone_set, phone_map=dict(mapping))
    return result


class MfaResourceService:
    def __init__(self, config_path):
        self.path = Path(config_path)

    def _read(self):
        return json.loads(self.path.read_text(encoding='utf-8-sig')) if self.path.is_file() else {}

    @staticmethod
    def _presets(raw):
        stored = raw.get('pronunciation', {}).get('presets', [])
        builtins = pronunciation_presets()
        result = [validate_preset(next((p for p in stored if isinstance(p, dict) and p.get('id') == b['id']), b),
                                  enforce_identity=False) for b in builtins]
        seen = {p['id'] for p in result}
        for entry in stored:
            if (isinstance(entry, dict) and isinstance(entry.get('id'), str)
                    and entry['id'].startswith('custom-') and entry['id'] not in seen):
                result.append(validate_preset(entry))
                seen.add(entry['id'])
        return result

    @staticmethod
    def _launcher(raw):
        return validate_launcher(raw.get('pronunciation', {}).get('launcher', LAUNCHER_DEFAULT))

    @staticmethod
    def catalog(raw):
        """Build a catalog from supplied raw JSON; no I/O on config, no list recursion."""
        namespace = raw.get('pronunciation', {})
        removed = set(namespace.get('removed_resource_ids', []))
        presets = MfaResourceService._presets(raw)
        result = {kind: [] for kind in KINDS}
        legacy = raw.get('alignment', {}).get('mfa', {})
        for kind in KINDS:
            entries = deepcopy(namespace.get('resources', {}).get(kind, []))
            active, multiple = {'dictionary': ('dictionary_path', 'dictionary_paths'),
                                'acoustic': ('acoustic_model', 'acoustic_models')}.get(kind, ('', ''))
            for path in [legacy.get(active, ''), *legacy.get(multiple, [])]:
                if path:
                    entries.append(dict(id=legacy_resource_id(kind, path), name=Path(path).name, path=str(path)))
            seen = set()
            for item in entries:
                if not isinstance(item, dict) or not item.get('id') or not item.get('path'):
                    continue
                identity = item['id']
                if identity in removed or identity in seen:
                    continue
                seen.add(identity)
                path = str(item['path'])
                result[kind].append(dict(id=identity, name=str(item.get('name') or Path(path).name),
                                         path=path, exists=Path(path).is_file(),
                                         used_by=[p['id'] for p in presets if p[kind] == identity]))
        return result

    @staticmethod
    def _check(preset, launcher, resources):
        issues, conflicts, inventories, states = [], [], {}, {}
        verified = {}
        for kind in KINDS:
            identity = preset[kind]
            resource = next((r for r in resources[kind] if r['id'] == identity), None)
            if not identity:
                states[kind] = '未配置'
                continue
            if resource is None or not resource['exists']:
                states[kind] = '文件缺失' if resource else '登记缺失'
                issues.append(f'{kind}：{states[kind]}')
                continue
            metadata = {}
            if kind == 'dictionary':
                try:
                    inventories[kind] = dictionary_inventory(resource['path'])
                    verified[kind] = True
                except (OSError, UnicodeError, ValueError) as error:
                    conflicts.append('词典格式无效：' + str(error))
            else:
                metadata = read_model_metadata(resource['path'])
                inventories[kind] = _phones(metadata)
                model_type = str(metadata.get('model_type', metadata.get('type', metadata.get('architecture', '')))).lower()
                known_type = ('g2p' if model_type in {'g2p', 'pynini', 'phonetisaurus'}
                              else 'acoustic' if model_type in {'acoustic', 'am', 'kaldi', 'gmm-hmm', 'gmm_hmm'}
                              else model_type if model_type in {'dictionary', 'language_model', 'ivector', 'tokenizer'}
                              else '')
                if known_type and known_type != kind:
                    conflicts.append(f'{kind}：模型类型不符（{known_type}）')
                language = metadata.get('language', metadata.get('languages'))
                languages = language if isinstance(language, list) else [language]
                known_languages = {_language(v) for v in languages if _language(v)}
                if known_languages and _language(preset['language']) not in known_languages:
                    conflicts.append(f'{kind}：模型语言与所选语言不符')
                verified[kind] = bool(inventories.get(kind) and known_type and known_languages)
            states[kind] = '静态检查通过' if verified.get(kind) else '未验证（元数据不足）'
        acoustic = inventories.get('acoustic')
        if acoustic:
            for kind in ('g2p', 'dictionary'):
                missing = (inventories.get(kind) or set()) - acoustic
                if missing:
                    conflicts.append(f'{kind} 音位未被声学模型覆盖：' + ', '.join(sorted(missing)))
        mapping_ready = preset['phone_set'] in ('ipa', 'arpabet') or bool(preset['phone_map'])
        from .phone_labels import map_phone
        ignored = {'<eps>', '<unk>', 'sil', 'sp', 'spn'}
        for kind in ('g2p', 'dictionary'):
            invalid = []
            for raw in sorted((inventories.get(kind) or set()) - ignored):
                try:
                    map_phone(raw, preset['phone_set'], preset['phone_map'])
                except ValueError:
                    invalid.append(raw)
            if invalid:
                mapping_ready = False
                states[kind] = '映射未就绪'
                issues.append(kind + ' 缺少或无效 IPA 映射：' + ', '.join(invalid))
        if not mapping_ready:
            issues.append('音位体系或 IPA 映射未就绪')
        launcher_ready = launcher_exists(launcher)
        if not launcher_ready:
            issues.append('MFA 启动程序未找到或 Conda 配置不完整')
        if conflicts:
            compatibility = '冲突'
        elif mapping_ready and all(verified.get(k) for k in KINDS):
            compatibility = '静态检查通过'
        else:
            compatibility = '未验证'
        g2p = next((r for r in resources['g2p'] if r['id'] == preset['g2p']), None)
        return dict(**states, compatibility=compatibility, issues=issues + conflicts,
                    mapping_ready=mapping_ready,
                    can_generate=bool(g2p and g2p['exists'] and mapping_ready and launcher_ready and not conflicts))

    def _result(self, raw):
        resources, launcher = self.catalog(raw), self._launcher(raw)
        presets = self._presets(raw)
        for preset in presets:
            preset['status'] = self._check(preset, launcher, resources)
        default = raw.get('pronunciation', {}).get('default_preset', '')
        return dict(presets=presets, default_preset=default if default in {p['id'] for p in presets} else '',
                    resources=resources, launcher=launcher,
                    theme=raw.get('modern', {}).get('preferences', {}).get('theme', 'system'))

    def list(self):
        return self._result(self._read())

    def check(self, preset, launcher):
        return self._check(validate_preset(preset), validate_launcher(launcher), self.catalog(self._read()))

    def environment(self, preset, launcher=None, runner=None):
        """Detects the local MFA alignment environment for one preset.

        Read-only: it runs the shipped probe (which may start MFA, conda and one child
        Python) and never installs, downloads, or saves anything. ``runner`` is the
        probe's command seam, used by tests. ``preset`` is a draft preset dict or a
        stored preset id; ``launcher`` is the draft launcher when the editor has unsaved
        launcher edits.
        """
        raw = self._read()
        resources = self.catalog(raw)
        if isinstance(preset, dict):
            target = validate_preset(preset, enforce_identity=False)
        else:
            target = next((p for p in self._presets(raw) if p['id'] == preset), None)
        if target is None:
            raise ValueError('未知的语言读音预设 ID')
        active = validate_launcher(launcher if launcher is not None else self._launcher(raw))
        chosen = {kind: next((r for r in resources[kind] if r['id'] == target[kind]), None) for kind in KINDS}
        report = alignment_probe().probe(
            mfa_executable=active['executable'], conda_executable=active['conda_executable'],
            conda_environment=active['conda_environment'],
            dictionary=(chosen['dictionary'] or {}).get('path', ''),
            acoustic_model=(chosen['acoustic'] or {}).get('path', ''), runner=runner)
        status = self._check(target, active, resources)
        conflicts = status['compatibility'] == '冲突'
        files_ready = all((chosen[kind] or {}).get('exists') for kind in ('dictionary', 'acoustic'))
        # "Ready" means this preset could be aligned end to end: the environment runs,
        # both alignment inputs exist, no static conflict was found, and every phone of the
        # dictionary can be mapped (otherwise MFA cannot even be given a phone sequence).
        # G2P generation and the user's accent declaration stay separate on purpose.
        ready = bool(report['ready']) and files_ready and not conflicts and status['mapping_ready']
        if not report['ready']:
            summary = report['summary']
        elif not files_ready:
            summary = '对齐环境可用，但词典或声学模型文件缺失'
        elif conflicts:
            summary = '对齐环境可用，但资源静态检查发现冲突：' + next(
                (issue for issue in status['issues'] if '：' in issue), status['issues'][0] if status['issues'] else '冲突')
        elif not status['mapping_ready']:
            summary = '对齐环境可用，但音位体系或 IPA 映射未就绪'
        else:
            summary = f"对齐环境可用（{report['launcher']}）；资源静态检查：{status['compatibility']}"
        blocking = [f"{item['label']}：{item['detail']}" for item in report['checks'] if item['status'] != 'ok']
        blocking += list(status['issues'])
        return dict(
            schema=report.get('schema', ''),
            preset=dict(id=target['id'], name=target['name'], language=target['language'], variant=target['variant'],
                        dictionary=target['dictionary'], acoustic=target['acoustic']),
            launcher=active, ready=ready, summary=summary, environment=report,
            resources={kind: (dict(id=chosen[kind]['id'], name=chosen[kind]['name'], path=chosen[kind]['path'],
                                   exists=chosen[kind]['exists']) if chosen[kind] else None) for kind in KINDS},
            status=status, blocking=blocking,
            notes=['检测项目：MFA 启动、Python 依赖与资源兼容性'])

    def resolve(self, preset_id):
        raw = self._read()
        data = self._result(raw)
        preset = next((p for p in data['presets'] if p['id'] == preset_id), None)
        if preset is None:
            raise ValueError('未知的语言读音预设 ID')
        paths, stats = {}, {}
        for kind in KINDS:
            resource = next((r for r in data['resources'][kind] if r['id'] == preset[kind]), None)
            path = resource['path'] if resource else ''
            paths[kind] = path
            try:
                stat = Path(path).stat() if path else None
                stats[kind] = [stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino] if stat else None
            except OSError:
                stats[kind] = None
        launcher = data['launcher']
        executable = (launcher['conda_executable'] if launcher['conda_executable'] or launcher['conda_environment']
                      else launcher['executable'])
        resolved_executable = (str(Path(executable).expanduser().resolve())
                               if executable and Path(executable).expanduser().is_file()
                               else shutil.which(executable) if executable else None)
        try:
            stat = Path(resolved_executable).stat() if resolved_executable else None
            launcher_stat = [resolved_executable, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns] if stat else None
        except OSError:
            launcher_stat = None
        # Include actual configuration content, not just config file timestamp.
        # Unrelated credentials/settings never enter the snapshot or fingerprint.
        configured = next((p for p in raw.get('pronunciation', {}).get('presets', [])
                           if isinstance(p, dict) and p.get('id') == preset_id), None)
        payload = dict(preset={k: v for k, v in preset.items() if k != 'status'},
                       configured=configured, launcher=launcher, launcher_stat=launcher_stat,
                       paths=paths, stats=stats)
        fingerprint = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                                separators=(',', ':')).encode('utf-8')).hexdigest()
        return dict(preset=preset, launcher=data['launcher'], paths=paths, fingerprint=fingerprint)

    def _change(self, update):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with service_transition_lock(Path(str(self.path) + '.lock')):
            raw = self._read()
            update(raw)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.path.parent,
                                                 prefix=self.path.name + '.', suffix='.tmp', delete=False) as handle:
                    temporary = Path(handle.name)
                    handle.write(json.dumps(raw, ensure_ascii=False, indent=2) + '\n')
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, self.path)
            finally:
                if temporary:
                    temporary.unlink(missing_ok=True)
            return self._result(raw)

    def add(self, kind, paths):
        """Trusted host import. Frontend import RPC only accepts a kind."""
        if kind not in KINDS:
            raise ValueError('未知的资源类型')
        paths = [str(Path(p).expanduser().resolve()) for p in paths]
        if any(not Path(p).is_file() for p in paths):
            raise ValueError('资源文件不存在')
        if not paths:
            return self.list()
        def update(raw):
            entries = raw.setdefault('pronunciation', {}).setdefault('resources', {}).setdefault(kind, [])
            seen = {_path_key(r['path']) for r in self.catalog(raw)[kind]}
            for path in paths:
                if _path_key(path) not in seen:
                    entries.append(dict(id=kind + '-' + uuid.uuid4().hex, name=Path(path).name, path=path))
                    seen.add(_path_key(path))
        return self._change(update)

    def save(self, preset, launcher, make_default=False):
        preset, launcher = validate_preset(preset), validate_launcher(launcher)
        def update(raw):
            catalog = self.catalog(raw)
            existing = next((p for p in self._presets(raw) if p['id'] == preset['id']), None)
            for kind in KINDS:
                # Retain a dangling reference in an otherwise partial save, but
                # never accept a new unregistered path/ID from the frontend.
                if (preset[kind] and (existing is None or preset[kind] != existing[kind])
                        and not any(r['id'] == preset[kind] for r in catalog[kind])):
                    raise ValueError('资源尚未登记：' + kind)
            status = self._check(preset, launcher, catalog)
            if status['compatibility'] == '冲突':
                raise ValueError('资源兼容性冲突：' + '；'.join(status['issues']))
            namespace = raw.setdefault('pronunciation', {})
            stored = namespace.setdefault('presets', [])
            previous = next((p for p in stored if p.get('id') == preset['id']), None)
            if previous is None:
                stored.append(deepcopy(preset))
            else:
                previous.update(deepcopy(preset))
            namespace.setdefault('launcher', {}).update(launcher)
            if make_default:
                namespace['default_preset'] = preset['id']
            elif namespace.get('default_preset') == preset['id']:
                namespace['default_preset'] = ''
        return self._change(update)

    def remove(self, kind, identity):
        if kind not in KINDS:
            raise ValueError('未知的资源类型')
        def update(raw):
            if not any(r['id'] == identity for r in self.catalog(raw)[kind]):
                raise ValueError('资源尚未登记')
            namespace = raw.setdefault('pronunciation', {})
            entries = namespace.setdefault('resources', {}).setdefault(kind, [])
            entries[:] = [r for r in entries if r['id'] != identity]
            removed = namespace.setdefault('removed_resource_ids', [])
            if identity not in removed:
                removed.append(identity)
            # Preset IDs deliberately remain dangling; never bind a replacement.
        return self._change(update)

    def rpc(self, method, params, window=None):
        if method == 'mfa.list':
            return self.list()
        if method == 'mfa.check':
            return self.check(params['preset'], params['launcher'])
        if method == 'mfa.environment':
            return self.environment(params.get('preset') or params.get('preset_id', ''), params.get('launcher'))
        if method == 'mfa.save':
            return self.save(params['preset'], params['launcher'], params.get('make_default') is True)
        if method == 'mfa.remove':
            return self.remove(params['kind'], params['id'])
        if method == 'mfa.check_launcher':
            from .mfa_g2p import check_launcher
            return check_launcher(validate_launcher(params['launcher']))
        if method in ('mfa.choose', 'mfa.choose_launcher'):
            if window is None:
                raise ValueError('本地文件选择窗口不可用')
            import webview
            if method == 'mfa.choose_launcher':
                paths = window.create_file_dialog(webview.FileDialog.OPEN, allow_multiple=False,
                                                  file_types=('可执行文件 (*.exe;*.bat;*.cmd)', '所有文件 (*.*)')) or []
                return {'path': str(paths[0]) if paths else ''}
            kind = params.get('kind')
            if kind not in KINDS:
                raise ValueError('未知的资源类型')
            pattern = ('MFA 发音词典 (*.dict;*.txt;*.lex;*.lexicon;*.tsv)' if kind == 'dictionary'
                       else 'MFA 模型 (*.zip)')
            initial = installation_root() / 'models' / 'mfa' / 'pretrained_models' / kind
            paths = window.create_file_dialog(webview.FileDialog.OPEN, allow_multiple=True,
                                              directory=str(initial if initial.is_dir() else installation_root()),
                                              file_types=(pattern, '所有文件 (*.*)')) or []
            return self.add(kind, paths)
        raise ValueError('未知的 MFA 资源操作')
