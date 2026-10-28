from __future__ import annotations

import json
import math
import os
import re
import threading
from dataclasses import dataclass
from copy import deepcopy
from uuid import uuid4
from pathlib import Path

from . import capabilities, environment
from .audio import read_wav
from .bridge import SelectedObject
from .config import default_config_path, load_config
from .models import AnalysisRequest, PhoneSpec
from .pronunciation_rules import PROFILES, resolve_profile
from .mfa_task import file_state
from .mfa_resources import MfaResourceService, validate_launcher
from .mfa_g2p import generate, choose_readings, check_launcher
from .normalization import VERSION, INVENTORIES, import_formant_calibration, resolve_measurement_settings
from .normalization_prosody import pitch_settings, import_pitch_baseline
from .service_lock import service_transition_lock


@dataclass(slots=True)
class TutorFormValues:
    request: AnalysisRequest | None


def _parse_phonemes(text: str) -> list[str]:
    return [item for item in re.split(r"[\s,，;；]+", text.strip()) if item]


def _equal_segments(phonemes: list[str], duration: float) -> list[PhoneSpec]:
    if not phonemes:
        return []
    step = duration / len(phonemes)
    return [PhoneSpec(ipa=phoneme, reference_start=index * step,
                      reference_end=(index + 1) * step, alignment_source="ui_equal_estimate")
            for index, phoneme in enumerate(phonemes)]


class TutorFormApplication:
    """Collect a request; analysis still runs through the existing tutor after close."""

    def __init__(self, sounds: list[SelectedObject], config_path=None):
        if len(sounds) < 2:
            raise ValueError("AI 纠音需要至少两个已选中的 Sound 对象。")
        self.sounds = sounds
        self.config_path = Path(config_path) if config_path else default_config_path()
        self.result = TutorFormValues(request=None)
        self.window = None
        self._guard = threading.Lock()
        self._closed = False
        self.resources = MfaResourceService(self.config_path)
        self._generation_cancel = None
        self._generations = {}
        self._lifetime_cancel = threading.Event()
        self._calibrations = {}
        self._formant_candidates = {}
        self._calibration_busy = False

    @staticmethod
    def _sound_summary(sound):
        import wave
        duration = None
        try:
            with wave.open(str(sound.file), 'rb') as handle:
                duration = handle.getnframes() / handle.getframerate()
        except (OSError, EOFError, wave.Error, ZeroDivisionError):
            pass
        return dict(id=str(sound.id), name=sound.name, duration=duration)

    def bootstrap(self):
        config = load_config(self.config_path)
        raw = json.loads(self.config_path.read_text(encoding="utf-8")) if self.config_path.is_file() else {}
        theme = raw.get("modern", {}).get("preferences", {}).get("theme", "system")
        return dict(
            sounds=[self._sound_summary(sound) for sound in self.sounds],
            profiles=[dict(id=profile.id, language=profile.language, name=profile.name) for profile in PROFILES],
            model=dict(source="云端 API" if config.qwen.provider == "api" else "本地模型",
                       name=config.qwen.model), theme=theme, mfa=self.resources.list(),
            normalization=dict(version=VERSION,
                **{key: raw.get('pronunciation', {}).get('normalization', {}).get(key, False) for key in ('formants', 'pitch', 'loudness')},
                measurement_settings={role: resolve_measurement_settings(raw.get('pronunciation', {}).get('normalization'), role)
                                      for role in ('reference', 'learner')},
                inventories={key: list(value) for key, value in INVENTORIES.items()}),
            # 运行环境缺件（Praat / ffmpeg / wav2vec2）随 bootstrap 一起给：前端不用为此多发一次 RPC，
            # 「重新检测」按钮才走 tutor.refresh_environment。
            environment=environment.report(config),
        )

    def build_request(self, params) -> AnalysisRequest:
        by_id = {str(sound.id): sound for sound in self.sounds}
        try:
            reference = by_id[str(params.get("reference", ""))]
            learner = by_id[str(params.get("learner", ""))]
        except KeyError:
            raise ValueError("请选择当前列表中的标准音范本和学习者录音。") from None
        phonemes = _parse_phonemes(str(params.get("phonemes", "")))
        if not phonemes:
            raise ValueError("请输入至少一个 IPA 音位。")
        language = str(params.get("language", "")).strip()
        profile_id = params.get("profile_id") or None
        resolve_profile(profile_id, language)
        snapshot, readings, raw_phones = None, [], []
        preset_id = str(params.get('preset_id') or '')
        generation_id = str(params.get('generation_id') or '')
        if preset_id:
            stored = self._generations.get(generation_id)
            if not stored:
                raise ValueError('请先自动生成并应用目标音位，或切换为手动 IPA。')
            generated, frozen = stored
            snapshot = self.resources.resolve(preset_id)
            if (generated['preset_id'] != preset_id
                    or generated['transcript'] != str(params.get('transcript', '')).strip()
                    or frozen['fingerprint'] != snapshot['fingerprint']):
                raise ValueError('文本或配套资源已改变，请重新生成音位。')
            preset = snapshot['preset']
            if language != preset['language'] or (profile_id and profile_id != preset['rule_id']):
                raise ValueError('目标语言或音位判据与所选读音不一致。')
            for kind in ('g2p', 'dictionary', 'acoustic'):
                if not snapshot['paths'].get(kind) or not Path(snapshot['paths'][kind]).is_file():
                    raise ValueError('MFA 分析缺少配套资源：' + kind + '；请配置配套资源。')
            readings, expected, raw_phones = choose_readings(generated, params.get('choices', {}))
            if phonemes != expected:
                raise ValueError('目标音位与已选读音不一致；修改 IPA 后请明确切换为手动模式。')
            snapshot['file_state'] = file_state(snapshot['paths'])
        try:
            threshold = 1.25 if profile_id else float(params.get("threshold", 1.25))
        except (ValueError, TypeError):
            raise ValueError("声学对比阈值必须是有效数字。") from None
        if not math.isfinite(threshold):
            raise ValueError("声学对比阈值必须是有效数字。")
        audio = read_wav(reference.file)
        phones = _equal_segments(phonemes, audio.duration)
        if snapshot:
            for phone, raw in zip(phones, raw_phones):
                phone.label = raw
        normalization = None
        if 'normalization' in params and params['normalization'] is not None:
            selected = params['normalization']
            if not isinstance(selected, dict) or selected.get('version') != VERSION:
                raise ValueError('归一化设置版本不匹配，请重新打开纠音窗口。')
            enabled = selected.get('formants') is True and profile_id is not None
            normalization = dict(version=VERSION, formants=enabled, confirmed=selected.get('confirmed') is True,
                pitch=selected.get('pitch') is True and profile_id is not None,
                loudness=selected.get('loudness') is True and profile_id is not None,
                pitch_confirmed=selected.get('pitch_confirmed') is True,
                pitch_baseline_mode=selected.get('pitch_baseline_mode', 'utterance'))
            normalization['measurement_settings'] = {role: resolve_measurement_settings(selected, role) for role in ('reference', 'learner')}
            normalization['formant_selections'] = {}
            if normalization['pitch']:
                normalization['pitch_settings'] = pitch_settings(selected)
                if normalization['pitch_baseline_mode'] not in {'utterance', 'independent'}:
                    raise ValueError('未知音高基线方式。')
            for role, sound in (('reference', reference), ('learner', learner)):
                identity = selected.get(role + '_calibration_id')
                if not identity:
                    continue
                record = self._calibrations.get(identity)
                binding = (role, str(sound.id), language, profile_id, preset_id or profile_id)
                if not record or record[0] != binding:
                    raise ValueError('校准与当前说话人或语言/读音不匹配，请重新导入并确认。')
                if record[1].get('measurement_settings') != normalization['measurement_settings'][role]:
                    raise ValueError('共振峰测量设置已改变，请重新导入该说话人的校准。')
                if record[1].get('formant_selection'):
                    from .normalization import audio_identity
                    selected_material = record[1]['formant_selection'].get('material', {}).get('audio_identity')
                    if not selected_material or selected_material in tuple(audio_identity(read_wav(s.file)) for s in (reference, learner)):
                        raise ValueError('候选材料缺少身份或与本次待测录音相同，请重新选参。')
                    normalization['formant_selections'][role] = deepcopy(record[1]['formant_selection'])
                if enabled:
                    normalization[role + '_calibration'] = deepcopy(record[1])
            if normalization['pitch'] and normalization['pitch_baseline_mode'] == 'independent':
                for role, sound in (('reference', reference), ('learner', learner)):
                    identity = selected.get(role + '_pitch_baseline_id')
                    if not identity:
                        continue
                    record = self._calibrations.get(identity)
                    binding = ('pitch_' + role, str(sound.id), language, profile_id, preset_id or profile_id,
                               json.dumps(normalization['pitch_settings'], sort_keys=True))
                    if not record or record[0] != binding:
                        raise ValueError('独立音高基线与当前说话人/读音/测量范围不匹配，请重新导入。')
                    normalization[role + '_pitch_baseline'] = deepcopy(record[1])
        return AnalysisRequest(
            reference_object=reference.id, learner_object=learner.id, language=language,
            phonemes=phones,
            transcript=str(params.get("transcript", "")).strip(), error_threshold=threshold,
            qwen_explain=bool(params.get("explain", True)), qwen_vision=bool(params.get("vision", False)),
            profile_id=profile_id, mfa_snapshot=deepcopy(snapshot), selected_readings=deepcopy(readings),
            generation_source=generated['source'] if snapshot else '', normalization=normalization,
        )

    def close(self, *args):
        with self._guard:
            self._closed = True
            self._lifetime_cancel.set()
            if self._generation_cancel:
                self._generation_cancel.set()
            self._generations.clear()
            self._calibrations.clear()
            self._formant_candidates.clear()

    def _import_calibration(self, params):
        role = params.get('role')
        sound = next((s for s in self.sounds if str(s.id) == str(params.get('sound_id'))), None)
        language, profile_id = str(params.get('language', '')), str(params.get('profile_id', ''))
        profile = resolve_profile(profile_id, language)
        if role not in {'reference', 'learner'} or sound is None or not profile or profile_id not in INVENTORIES:
            raise ValueError('请先选择说话人和受支持的音位判据。')
        reading_id = str(params.get('reading_id') or profile_id)
        with self._guard:
            if self._closed or self._calibration_busy or self._generation_cancel:
                raise ValueError('当前无法导入校准，请等待其他任务结束。')
            self._calibration_busy = True
        try:
            import webview
            paths = []
            for pattern in ('校准 WAV (*.wav)', 'IPA TextGrid (*.TextGrid)'):
                chosen = self.window.create_file_dialog(webview.FileDialog.OPEN, allow_multiple=False,
                                                       file_types=(pattern,)) or []
                if not chosen:
                    return None
                paths.append(chosen[0])
            calibration = import_formant_calibration(*paths, speaker_label=sound.name,
                language=profile.language, profile_id=profile_id, reading_id=reading_id,
                measurement_settings=resolve_measurement_settings(params, role))
            identity = uuid4().hex
            with self._guard:
                if self._closed:
                    raise ValueError('纠音窗口已关闭，未保存校准。')
                # Only the current import for a role is retained in this window.
                self._calibrations = {key: value for key, value in self._calibrations.items() if value[0][0] != role}
                self._calibrations[identity] = ((role, str(sound.id), language, profile_id, reading_id), deepcopy(calibration))
            return dict(id=identity, speaker_label=calibration['speaker_label'], status=calibration['status'],
                        formants=calibration['formants'], material=calibration['material'],
                        measurement_settings=calibration['measurement_settings'])
        finally:
            with self._guard:
                self._calibration_busy = False

    def _candidate_binding(self, params):
        role = params.get('role')
        sound = next((s for s in self.sounds if str(s.id) == str(params.get('sound_id'))), None)
        language, profile_id = str(params.get('language', '')), str(params.get('profile_id', ''))
        profile = resolve_profile(profile_id, language)
        if role not in {'reference', 'learner'} or sound is None or not profile or profile_id not in INVENTORIES:
            raise ValueError('请先选择说话人和受支持的音位判据。')
        return (role, str(sound.id), language, profile_id, str(params.get('reading_id') or profile_id)), sound, profile

    def _candidate_calibration(self, binding, analysis, candidate_id, *, automatic):
        from .formant_selection import select_candidate
        calibration = select_candidate(analysis, candidate_id, automatic=automatic)
        receipt_id = uuid4().hex
        with self._guard:
            if self._closed:
                raise ValueError('纠音窗口已关闭，未采用候选。')
            self._calibrations = {key: value for key, value in self._calibrations.items() if value[0][0] != binding[0]}
            self._calibrations[receipt_id] = (binding, deepcopy(calibration))
        return dict(id=receipt_id, speaker_label=calibration['speaker_label'], status=calibration['status'],
            formants=calibration['formants'], material=calibration['material'],
            measurement_settings=calibration['measurement_settings'],
            formant_selection={key: deepcopy(value) for key, value in calibration['formant_selection'].items() if key != 'candidates'})

    def _candidate_targets(self, params):
        ids = (params.get('reference_sound_id'), params.get('learner_sound_id'))
        if ids == (None, None) and len(self.sounds) == 2:
            ids = tuple(s.id for s in self.sounds)
        targets = tuple(next((s for s in self.sounds if str(s.id) == str(value)), None) for value in ids)
        if any(s is None for s in targets):
            raise ValueError('请明确本次标准音和学习者 Sound；未把其他选中对象当作待测录音。')
        position = 0 if params.get('role') == 'reference' else 1
        if str(targets[position].id) != str(params.get('sound_id')):
            raise ValueError('候选说话人与本次比较对象不匹配。')
        return targets

    def _analyze_formant_candidates(self, params):
        from .formant_selection import analyze_candidates, public_analysis
        from .normalization import audio_identity
        binding, sound, profile = self._candidate_binding(params)
        with self._guard:
            if self._closed or self._calibration_busy or self._generation_cancel:
                raise ValueError('请等待当前导入或分析结束。')
            self._calibration_busy = True
        try:
            import webview
            paths = []
            for pattern in ('独立元音 WAV (*.wav)', '可靠 IPA TextGrid (*.TextGrid)'):
                chosen = self.window.create_file_dialog(webview.FileDialog.OPEN, allow_multiple=False, file_types=(pattern,)) or []
                if not chosen:
                    return None
                paths.append(chosen[0])
            analysis = analyze_candidates(*paths, speaker_label=sound.name, language=profile.language,
                profile_id=binding[3], reading_id=binding[4],
                measurement_settings=resolve_measurement_settings(params, binding[0]),
                bandwidth_hz=params.get('bandwidth_hz'), cancel=self._lifetime_cancel,
                target_ids=tuple(audio_identity(read_wav(s.file)) for s in self._candidate_targets(params)))
            with self._guard:
                if self._closed:
                    raise ValueError('纠音窗口已关闭，未保存候选。')
                self._formant_candidates = {key: value for key, value in self._formant_candidates.items() if value[0][0] != binding[0]}
                self._formant_candidates[(binding, analysis['id'])] = (binding, analysis)
            result = public_analysis(analysis)
            if analysis['decision']['recommended_id'] is not None:
                result['applied_calibration'] = self._candidate_calibration(binding, analysis,
                    analysis['decision']['recommended_id'], automatic=True)
            return result
        finally:
            with self._guard:
                self._calibration_busy = False

    def _select_formant_candidate(self, params):
        binding, _, _ = self._candidate_binding(params)
        with self._guard:
            if self._closed or self._calibration_busy or self._generation_cancel:
                raise ValueError('请等待当前导入或分析结束。')
            record = self._formant_candidates.get((binding, params.get('analysis_id')))
            if not record or record[0] != binding:
                raise ValueError('候选与当前说话人或读音不匹配，请重新分析。')
            current = resolve_measurement_settings(params, binding[0])
            if any(current[key] != value for key, value in record[1]['base_parameters'].items() if key != 'maximum_formant_hz'):
                raise ValueError('Burg 参数已改变，请重新分析候选。')
            self._calibration_busy = True
        try:
            return self._candidate_calibration(binding, record[1], params.get('candidate_id'), automatic=False)
        finally:
            with self._guard:
                self._calibration_busy = False

    def _import_pitch_baseline(self, params):
        role = params.get('role')
        sound = next((s for s in self.sounds if str(s.id) == str(params.get('sound_id'))), None)
        language, profile_id = str(params.get('language', '')), str(params.get('profile_id', ''))
        profile = resolve_profile(profile_id, language)
        settings = pitch_settings(params)
        if role not in {'reference', 'learner'} or sound is None or not profile:
            raise ValueError('请先选择说话人和音位判据。')
        reading_id = str(params.get('reading_id') or profile_id)
        with self._guard:
            if self._closed or self._calibration_busy or self._generation_cancel:
                raise ValueError('当前无法导入基线，请等待其他任务结束。')
            self._calibration_busy = True
        try:
            import webview
            paths = self.window.create_file_dialog(webview.FileDialog.OPEN, allow_multiple=False,
                                                   file_types=('中性语句 WAV (*.wav)',)) or []
            if not paths:
                return None
            baseline = import_pitch_baseline(paths[0], settings=settings, language=profile.language,
                reading_id=reading_id, speaker_label=sound.name)
            identity = uuid4().hex
            binding = ('pitch_' + role, str(sound.id), language, profile_id, reading_id, json.dumps(settings, sort_keys=True))
            with self._guard:
                if self._closed:
                    raise ValueError('纠音窗口已关闭，未保存基线。')
                self._calibrations = {key: value for key, value in self._calibrations.items() if value[0][0] != binding[0]}
                self._calibrations[identity] = (binding, deepcopy(baseline))
            return dict(id=identity, baseline=baseline)
        finally:
            with self._guard:
                self._calibration_busy = False

    def _configure_runtime(self):
        import subprocess
        import sys
        executable = Path(__file__).resolve().parents[2] / 'AIPraat-paths.exe'
        if os.name != 'nt' or not executable.is_file():
            raise ValueError('未找到路径配置程序，请从 Praat 的“路径配置 → 配置运行库…”进入。')
        environment = os.environ.copy()
        environment.update(PRAAT_AI_CONFIG_PATH=str(self.config_path.resolve()),
                           PRAAT_AI_PATHS_AI_DIR=str(Path(__file__).resolve().parents[1]),
                           PRAAT_AI_PATHS_PYTHON=sys.executable)
        subprocess.Popen([str(executable)], env=environment, cwd=str(executable.parent))
        return dict(opened=True, message='已打开路径配置，请点击“配置运行库…”。若安装或修复了运行库，请关闭并重新打开纠音窗口后再提交。')

    def _remember_normalization(self, selection):
        import tempfile
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        with service_transition_lock(Path(str(self.config_path) + '.lock')):
            raw = json.loads(self.config_path.read_text(encoding='utf-8')) if self.config_path.is_file() else {}
            raw.setdefault('pronunciation', {})['normalization'] = dict(version=VERSION,
                **{key: selection.get(key) is True for key in ('formants', 'pitch', 'loudness')})
            raw['pronunciation']['normalization']['measurement_settings'] = {
                role: resolve_measurement_settings(selection, role) for role in ('reference', 'learner')}
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.config_path.parent,
                                                 prefix=self.config_path.name + '.', suffix='.tmp', delete=False) as handle:
                    temporary = Path(handle.name)
                    handle.write(json.dumps(raw, ensure_ascii=False, indent=2) + '\n')
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, self.config_path)
            finally:
                if temporary:
                    temporary.unlink(missing_ok=True)

    def _generate(self, params):
        with self._guard:
            if self._closed:
                raise ValueError('纠音窗口已关闭。')
            if self._generation_cancel is not None or self._calibration_busy:
                raise ValueError('已有音位生成或校准导入正在运行，请等待或取消。')
            cancel = threading.Event()
            self._generation_cancel = cancel
        try:
            snapshot = self.resources.resolve(str(params.get('preset_id') or ''))
            result = generate(snapshot, str(params.get('transcript') or ''), cancel)
            if snapshot['fingerprint'] != self.resources.resolve(result['preset_id'])['fingerprint']:
                raise ValueError('生成期间配套资源已改变，请重新生成。')
            result['id'] = uuid4().hex
            with self._guard:
                if self._closed or cancel.is_set():
                    raise ValueError('已取消音位生成。')
                self._generations = {result['id']: (deepcopy(result), deepcopy(snapshot))}
            return result
        finally:
            with self._guard:
                if self._generation_cancel is cancel:
                    self._generation_cancel = None

    def rpc(self, method, params):
        with self._guard:
            if self._closed:
                raise ValueError("纠音窗口已关闭。")
        if method == 'mfa.check_launcher':
            return check_launcher(validate_launcher(params.get('launcher', {})), self._lifetime_cancel)
        if method.startswith('mfa.'):
            return self.resources.rpc(method, params, self.window)
        if method == 'tutor.bootstrap':
            return self.bootstrap()
        if method == 'tutor.generate_phonemes':
            return self._generate(params)
        if method == 'tutor.import_calibration':
            return self._import_calibration(params)
        if method == 'tutor.analyze_formant_candidates':
            return self._analyze_formant_candidates(params)
        if method == 'tutor.select_formant_candidate':
            return self._select_formant_candidate(params)
        if method == 'tutor.import_pitch_baseline':
            return self._import_pitch_baseline(params)
        if method == 'tutor.configure_runtime':
            return self._configure_runtime()
        if method == 'tutor.capabilities':
            # 提交之前就告诉前端「这一步现在能不能跑」：缺依赖时禁用开关并给出可执行提示，
            # 而不是等分析跑完才在报告里说少了什么。
            return capabilities.analysis_capabilities()
        if method == 'tutor.environment':
            # 运行环境缺件（Praat / ffmpeg / wav2vec2）：「路径依赖」那一页直接渲染这几行。
            return environment.report()
        if method == 'tutor.refresh_environment':
            # 用户改完路径再点「重新检测」：先清 Praat 的探测缓存，再重新体检一次。
            environment.refresh()
            return environment.report()
        with self._guard:
            if self._closed:
                raise ValueError("纠音窗口已关闭。")
            if method == 'tutor.cancel_generation':
                if self._generation_cancel:
                    self._generation_cancel.set()
                self._generations.clear()
                return dict(cancelled=True)
            if method == "tutor.submit":
                if self._generation_cancel or self._calibration_busy:
                    raise ValueError('请等待音位生成或校准导入完成后再提交。')
                request = self.build_request(params)
                if request.normalization is not None:
                    self._remember_normalization(request.normalization)
                self.result.request = request
            elif method != "tutor.cancel":
                raise ValueError("未知的纠音窗口操作。")
            self._closed = True
            self._lifetime_cancel.set()
            if self._generation_cancel:
                self._generation_cancel.set()
            self._generations.clear()
        self.window.destroy()
        return dict(accepted=True)


def show_tutor_form(sound_objects: list[SelectedObject], config_path=None) -> TutorFormValues:
    app = TutorFormApplication(sound_objects, config_path)
    import webview
    from .modern_app import HostAPI
    from .modern_host import AssetServer, watch_native_parent
    assets = Path(__file__).resolve().parents[1] / "frontend" / "dist"
    if not (assets / "index.html").is_file():
        raise RuntimeError("纠音界面资产缺失，请重新构建 ai/frontend。")
    server = AssetServer(assets)
    try:
        api = HostAPI(app)
        app.window = webview.create_window(
            "AI 纠音 · AIPraat", server.url + "?window=pronunciation", js_api=api,
            width=1040, height=760, min_size=(600, 540), text_select=True,
        )
        api._origin = server.url.rsplit("/", 1)[0]
        try:
            app.window.events.closed += app.close
        except (AttributeError, TypeError):
            pass  # Simple test hosts may not implement WebView events.
        webview.start(lambda: watch_native_parent(app.window),
                      gui="edgechromium" if os.name == "nt" else None, private_mode=True)
    finally:
        app.close()
        server.close()
    return app.result
