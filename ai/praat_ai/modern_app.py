"""UI-independent application service and single allowlisted RPC facade."""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import mimetypes
import threading
import time
from collections import deque
from dataclasses import asdict
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

from .config import default_config_path
from .modern_store import ModernStore, identity, now
from .modern_settings import SettingsService, assert_endpoint
from . import modern_budget as budget
from .model_capabilities import safe_error

ACTIVE = {'running', 'queued', 'cancelling'}
#: 一次 events.poll 最多返回多少条事件（见 ModernApplication.poll）。
POLL_EVENT_LIMIT = 400
#: RPC 参数的中文名：缺参数时不能把裸 KeyError（``'sessionId'``）抛给前端。
PARAM_LABELS = {'sessionId': '会话标识', 'taskId': '任务标识', 'title': '标题',
                'text': '消息内容', 'sourceTaskId': '原任务标识', 'mode': '动作',
                'settings': '设置内容', 'kind': '测试类型', 'data': '附件内容',
                'name': '附件名', 'url': '链接地址', 'attachmentId': '附件标识',
                'sectionId': '分区标识', 'settingsId': '设置标识'}


def param(params, key, *, default=...):
    """取 RPC 参数；缺失就报中文错误，不把 ``KeyError('sessionId')`` 漏给前端。"""

    if key in params:
        return params[key]
    if default is not ...:
        return default
    raise ValueError(f'缺少{ PARAM_LABELS.get(key, key) }（{key}）')


class LocalResources:
    """Serialize local inference/load leases. Never unload an unowned or in-use service."""
    def __init__(self):
        self.gate = threading.Lock()
        self.guard = threading.Lock()
        self.users = 0
        self.manager = None
        self.key = None
        self.stop_pending = False

    @contextmanager
    def lease(self, config, cancel, emit):
        while not self.gate.acquire(timeout=.1):
            if cancel.is_set():
                raise RuntimeError('已取消等待本地模型')
        try:
            if cancel.is_set():
                raise RuntimeError('已取消等待本地模型')
            with self.guard:
                self.users += 1
            key = (config.qwen.base_url, config.qwen.model, config.qwen.max_context_tokens,
                   config.qwen.vision_when_requested, json.dumps(asdict(config.server), sort_keys=True))
            endpoint = urlsplit(config.qwen.base_url)
            configured_local = (endpoint.hostname in {'127.0.0.1', 'localhost', '::1'}
                and endpoint.port == config.server.port
                and bool(config.server.llama_server.strip() and config.server.model_path.strip()))
            # A submitted local task is an explicit request to use its configured
            # model. Legacy auto_start=False must not bypass readiness now that
            # opening the desktop no longer eagerly starts the model service.
            if not config.api.enabled and (config.server.auto_start or configured_local):
                from .server import QwenServerManager
                from .vram import detect_gpu, select_runtime_profile
                if self.manager and key != self.key:
                    # Owned process only; leases above are serialized.
                    self.manager.stop()
                    self.manager = None
                if self.manager is None:
                    gpu = detect_gpu()
                    profile = select_runtime_profile(gpu.free_mb if gpu else None, config.qwen.vision_when_requested)
                    profile.context_tokens = config.qwen.max_context_tokens
                    launch_config = copy.deepcopy(config)
                    launch_config.server.auto_start = True
                    self.manager = QwenServerManager(launch_config, profile, progress=lambda fraction, text: emit('activity', dict(
                        id='local-load', type='progress', text=text, status='running')))
                    self.key = key
                self.manager.ensure_started()
            yield
        finally:
            with self.guard:
                self.users = max(0, self.users - 1)
                if self.stop_pending and self.users == 0 and self.manager:
                    self.manager.stop()
                    self.manager, self.key = None, None
                    self.stop_pending = False
            self.gate.release()

    def request_stop(self):
        with self.guard:
            self.stop_pending = True
            if self.users == 0 and self.manager:
                self.manager.stop()
                self.manager, self.key = None, None
                self.stop_pending = False


class Attachments:
    LIMIT = 20 * 1024 * 1024
    TYPES = {'.txt': 'text/plain', '.md': 'text/markdown', '.csv': 'text/csv', '.json': 'application/json',
             '.wav': 'audio/wav', '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp'}
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.guard = threading.RLock()

    def import_bytes(self, name, data):
        name = str(name).replace('\\', '/').split('/')[-1][:200]
        suffix = Path(name).suffix.lower()
        if suffix not in self.TYPES:
            raise ValueError('附件支持文本、CSV/JSON、PNG/JPEG/WebP 与 PCM WAV；不执行附件内容')
        if not data or len(data) > self.LIMIT:
            raise ValueError('附件须为 1 字节–20 MiB')
        mime = self.TYPES[suffix]
        if mime.startswith('image/'):
            import io
            from PIL import Image
            with Image.open(io.BytesIO(data)) as image:
                if image.width * image.height > 20000000:
                    raise ValueError('图像尺寸过大')
                image.verify()
        elif mime == 'audio/wav':
            import io, wave
            with wave.open(io.BytesIO(data), 'rb') as audio:
                if audio.getnframes() / audio.getframerate() > 120:
                    raise ValueError('音频最多 120 秒')
        else:
            data.decode('utf-8-sig')
        aid = identity()
        meta = dict(id=aid, name=name, mime=mime, size=len(data))
        with self.guard:
            (self.root / (aid + suffix)).write_bytes(data)
            (self.root / (aid + '.meta')).write_text(json.dumps(meta, ensure_ascii=False), encoding='utf-8')
        return meta

    def resolve(self, aid):
        if not isinstance(aid, str) or len(aid) != 32 or any(c not in '0123456789abcdef' for c in aid):
            raise ValueError('附件标识无效')
        with self.guard:
            meta_path = self.root / (aid + '.meta')
            if not meta_path.is_file():
                # 以前这里直接把 FileNotFoundError 抛给前端，里面带着绝对路径。
                raise ValueError('这个附件已经不存在了；请重新选择文件。')
            meta = json.loads(meta_path.read_text(encoding='utf-8'))
            path = self.root / (aid + Path(meta['name']).suffix.lower())
            if not path.is_file():
                raise ValueError('这个附件已经不存在了；请重新选择文件。')
            if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()) or path.resolve().parent != self.root.resolve():
                raise ValueError('附件路径无效')
            return {**meta, 'path': str(path)}

    def preview(self, aid):
        meta = self.resolve(aid)
        path = Path(meta.pop('path'))
        if meta['mime'].startswith('text/') or meta['mime'] == 'application/json':
            return {**meta, 'text': path.read_bytes()[:20000].decode('utf-8-sig', errors='replace')}
        if meta['mime'].startswith('image/') or meta['mime'] == 'audio/wav':
            return {**meta, 'dataUrl': 'data:' + meta['mime'] + ';base64,' + base64.b64encode(path.read_bytes()).decode('ascii')}
        return meta


class ModernApplication:
    def __init__(self, root: Path, config_path: Path | None = None, legacy_path: Path | None = None,
                 *, allow_cloud=False, configured_cloud=False, executor=None):
        self.root = Path(root)
        self.settings = SettingsService(config_path or default_config_path())
        cfg = self.settings.candidate()
        self.store = ModernStore(self.root / 'sessions.sqlite3', legacy_path, secrets=(cfg.api.api_key, cfg.qwen.api_key),project_path=Path(__file__).resolve().parents[2])
        if executor is None:
            from .modern_execution import ModernExecutor
            executor = ModernExecutor(runtime_directory=self.root, config_path=self.settings.path)
        self.executor = executor
        self.allow_cloud = allow_cloud
        # Interactive desktop requests honor the enabled API configuration.
        # This is host policy, not an editable setting or a network probe.
        self.configured_cloud = configured_cloud
        self.attachments = Attachments(self.root / 'attachments')
        self.local = LocalResources()
        self.guard = threading.RLock()
        self.tasks = {}
        self.events = deque(maxlen=4000)
        self.cursor = 0
        self.closed = False
        self.window = None
        self.navigation_id = ''
        self._created_sessions = set()

    def event(self, task, kind, payload):
        with self.guard:
            self.cursor += 1
            self.events.append(dict(seq=self.cursor, sessionId=task['sessionId'], taskId=task['id'],
                                    type=kind, payload=self.store.clean(copy.deepcopy(payload))))

    def public_task(self, task):
        return {k: task[k] for k in ('id', 'sessionId', 'status', 'model', 'created', 'workflow') if k in task}

    def bootstrap(self):
        from .api_settings import PROVIDERS
        from .desktop_launch import praat_snapshot
        with self.guard:
            return dict(sessions=self.store.sessions(), sections=self.store.organization.sections(), settings=self.settings.get(),
                        tasks=[self.public_task(t) for t in self.tasks.values()], providers=list(PROVIDERS), models=budget.MODELS,
                        host=dict(name='pywebview / WebView2', version='6.2.1', cloudAllowed=self.allow_cloud or self.configured_cloud,
                                  praat=praat_snapshot()))

    def get_session(self, sid):
        # RPC snapshots must include unflushed deltas. The UI uses authoritative
        # snapshots to reconcile live streams; persisted snapshots can lag .3s.
        with self.guard:
            result = self.store.get(sid)
            live = {task['message']['id']: self.store.clean(copy.deepcopy(task['message']))
                    for task in self.tasks.values()
                    if task['sessionId'] == sid and task['status'] in ACTIVE}
            result['messages'] = [live.get(message['id'], message) for message in result['messages']]
            actionable = [m for m in result['messages'] if m.get('followup_actions') and m.get('taskId')]
            if actionable:
                from .workflow_policy import WorkflowPolicy
                cfg = self.settings.candidate()
                for message in actionable:
                    try:
                        record = self.store.task_evidence(sid, message['taskId'])
                        cfg.workflow = WorkflowPolicy.validate(record.get('metrics', {}).get('workflow_policy'))
                        message['followup_actions'] = self.followup_actions(cfg, record) if cfg.api.enabled else []
                    except (OSError, ValueError):
                        message['followup_actions'] = []
            result['cursor'] = self.cursor
            return result

    def context_status(self, sid, text='', attachment_ids=None):
        """Read-only next-turn estimate. Never capture/dispatch a Praat target."""
        text = str(text)
        attachment_ids = [] if attachment_ids is None else attachment_ids
        if len(text) > 100000 or not isinstance(attachment_ids, list) or len(attachment_ids) > 8:
            raise ValueError('上下文预览最多 100000 字符、8 个附件')
        with self.guard:
            history, evidence = self.store.context(sid)
            cfg = self.settings.candidate()
            section = cfg.api if cfg.api.enabled else cfg.qwen
            raw = self.settings.raw().get('api' if cfg.api.enabled else 'qwen', {})
            reason = ''
            try:
                cfg = budget.prepare(cfg, explicit_window='max_context_tokens' in raw)
                section = cfg.api if cfg.api.enabled else cfg.qwen
                window = section.max_context_tokens
                reserve = section.plan_max_tokens if section.token_mode != 'provider' else max(512, window // 10)
            except ValueError as error:
                window, reserve, reason = None, None, str(error)
            material_bytes = 0
            for aid in attachment_ids:
                item = self.attachments.resolve(aid)
                if item['mime'].startswith('text/') or item['mime'] == 'application/json':
                    data = Path(item['path']).read_bytes()
                    material_bytes += len(data)
                    if material_bytes > 1024 * 1024:
                        raise ValueError('文本附件总量须不超过 1 MiB')
                    text += '\n用户附件 ' + item['name'] + ':\n' + data.decode('utf-8-sig')
            estimate = budget.context_estimate(cfg, history, evidence, text)
            used = estimate['inputTokens']
            return dict(sessionId=sid, model=section.model, estimated=True,
                        **estimate, contextWindow=window, reservedTokens=reserve,
                        availableTokens=window - used - reserve if window else None,
                        percent=round(used / window * 100, 1) if window else None,
                        tokenMode=section.token_mode or ('manual' if section.limit_tokens else 'provider'),
                        windowSource='metadata' if window and budget.metadata(section) and section.token_mode != 'manual' else 'configured' if window else 'unknown',
                        reason=reason, exclusions=['尚未捕获的 Praat 对象与范围', '音频／图片编码', '正在生成的正文'])

    def prepared(self, settings=None):
        cfg = self.settings.candidate(settings)
        if cfg.api.enabled and (not cfg.api.base_url.strip() or not cfg.api.model.strip()):
            raise ValueError('请填写云端 API 地址和模型名')
        assert_endpoint(cfg.qwen.base_url, allow_cloud=self.allow_cloud or (self.configured_cloud and cfg.api.enabled))
        raw_section = self.settings.raw().get('api' if cfg.api.enabled else 'qwen', {})
        incoming = (settings or {}).get('api' if cfg.api.enabled else 'local', {})
        return budget.prepare(cfg, explicit_window='max_context_tokens' in raw_section or 'max_context_tokens' in incoming)

    def submit(self, sid, text, attachment_ids=(), *, continuation=None):
        text = str(text).strip()
        if not text or len(text) > 100000:
            raise ValueError('请输入 1–100000 字符')
        # 目标捕获要在 PRAAT_LOCK 下等 Praat 回应，上限 EXECUTION_TIMEOUT_SEC（25 秒）。
        # 以前整段都在 self.guard 里，期间 events.poll（前端 220ms 心跳）一起被挡住，
        # 界面会显示成断线（2026-10-07）。所以先在 guard 外把配置、历史和目标都备好，
        # 再进 guard 只做需要原子性的检查与登记；并发提交仍由 guard 内的 ACTIVE 检查挡住。
        cfg = self.prepared()
        values = [self.attachments.resolve(aid) for aid in attachment_ids]
        if len(values) > 8 or sum(a['size'] for a in values) > Attachments.LIMIT:
            raise ValueError('每轮最多 8 个附件、总计 20 MiB')
        history, evidence = self.store.context(sid)
        from .workflow_policy import explicit_action, WorkflowPolicy
        if cfg.api.enabled and continuation is None and explicit_action(text) == 'L2' and evidence:
            prior = evidence[-1]
            if prior.get('evidence') and prior.get('goal'):
                continuation = {**prior, 'mode':'L2'}
        if continuation:
            cfg.workflow = WorkflowPolicy.validate(continuation['metrics']['workflow_policy'])
            target = continuation['target']
        else:
            target = (self.executor.capture_workflow_target(text, cfg.workflow)
                      if cfg.api.enabled and hasattr(self.executor, 'capture_workflow_target') else self.executor.capture_target(text))
        with self.guard:
            if self.closed:
                raise RuntimeError('桌面服务正在关闭')
            self.store.writable(sid)
            if self.store.get(sid)['session']['archived']:
                raise ValueError('请先恢复已归档会话再继续聊天')
            if any(t['sessionId'] == sid and t['status'] in ACTIVE for t in self.tasks.values()):
                raise ValueError('本会话已有任务；请等待完成或取消，其他会话仍可提交')
            if sum(t['status'] in ACTIVE for t in self.tasks.values()) >= 8:
                raise ValueError('最多 8 个后台任务，请等待已有任务')
            self.store.cleaner.secrets = tuple(v for v in set(self.store.cleaner.secrets) | {cfg.api.api_key, cfg.qwen.api_key} if v and v != 'EMPTY')
            task = dict(id=identity(), sessionId=sid, status='running', model=cfg.qwen.model, created=now(), cancel=threading.Event())
            task['continuation'] = continuation
            task['workflow'] = asdict(cfg.workflow)
            message = dict(id=identity(), role='assistant', content='', status='running', taskId=task['id'], activities=[])
            task['message'] = message
            self.tasks[task['id']] = task
            meta = [{k: a[k] for k in ('id', 'name', 'mime', 'size')} for a in values]
            user = dict(id=identity(), role='user', content=text, attachments=meta, taskId=task['id'])
            self.store.put_message(sid, user)
            self.store.put_message(sid, message)
            self.store.view(sid, draft='')
            self.event(task, 'message', user)
            self.event(task, 'message', message)
            self.event(task, 'task', self.public_task(task))
            thread = threading.Thread(target=self.run_task, args=(task, cfg, text, history, evidence, target, values),
                                      name='AI-task-' + task['id'][:8], daemon=True)
            task['thread'] = thread
            thread.start()
            return self.public_task(task)

    def run_task(self, task, cfg, text, history, evidence, target, attachments):
        sid, message, cancel = task['sessionId'], task['message'], task['cancel']
        last_save = 0.0
        def emit(kind, payload):
            nonlocal last_save
            with self.guard:
                if self.closed:
                    return
                if kind == 'delta':
                    message['content'] += str(payload.get('text', ''))
                    self.event(task, 'delta', dict(messageId=message['id'], text=payload.get('text', '')))
                elif kind == 'results':
                    record = self.store.clean(payload)
                    message['result_content'] = record.pop('content')
                    explanation = record.pop('verified_explanation', '')
                    if explanation:
                        message['content'] = explanation
                        message['analysis_content'] = explanation
                    message['results'] = record['results']
                    self.store.save_evidence(sid, task['id'], record)
                    # Durable facts precede the visible event and any explanation request.
                    self.store.put_message(sid, message)
                    self.event(task, 'message', message)
                elif kind == 'activity':
                    activity = self.store.clean(payload)
                    activity.setdefault('id', identity())
                    prior = next((i for i, a in enumerate(message['activities']) if a['id'] == activity['id']), None)
                    if prior is None:
                        message['activities'].append(activity)
                    else:
                        message['activities'][prior] = activity
                    self.event(task, 'activity', dict(messageId=message['id'], activity=activity))
                if kind == 'activity' or time.monotonic() - last_save > .3:
                    self.store.put_message(sid, message)
                    last_save = time.monotonic()
        try:
            def execute():
                nonlocal history
                task_metrics = dict(session_id=sid, task_id=task['id'], context_epoch=self.store.context_epoch(sid))
                material_input, material_bytes = text, 0
                for attachment in attachments:
                    if attachment['mime'].startswith('text/') or attachment['mime'] == 'application/json':
                        data = Path(attachment['path']).read_bytes()
                        material_bytes += len(data)
                        if material_bytes > 1024 * 1024:
                            raise ValueError('文本附件总量须不超过 1 MiB')
                        material_input += '\n用户附件 ' + attachment['name'] + ':\n' + data.decode('utf-8-sig')
                from .dialogue_policy import dialogue_kind, direct_measurement
                phase = 'dialogue' if not attachments and dialogue_kind(text) else 'planner'
                deterministic = not attachments and direct_measurement(text, statistics=True)
                runtime = (self.executor.session_runtime(sid) if cfg.api.enabled and not deterministic
                           and not task.get('continuation') and hasattr(self.executor, 'session_runtime') else None)
                # Duration fast path still makes zero model calls, including summaries.
                if not deterministic and not task.get('continuation'):
                    history = budget.compact(self.store, sid, cfg, history, evidence, material_input, target,
                                             cancel=cancel, emit=emit, phase=phase, runtime=runtime, metrics=task_metrics)
                if evidence and not cfg.api.enabled:
                    history = [*history, dict(role='user', content='历史专业证据与投递事实（保留来源/原范围；不能当作本轮新测量，不能重放操作）：\n' + json.dumps(evidence, ensure_ascii=False))]
                extra = dict(session_id=sid, task_id=task['id'], phase_context=self.store.model_context(sid, 'dialogue'),
                             context_epoch=self.store.context_epoch(sid), historical_evidence=evidence,
                             task_metrics=task_metrics, continuation=task.get('continuation')) if cfg.api.enabled else {}
                return self.executor.run(config=cfg, text=text, history=history, target=target, cancel=cancel, emit=emit, attachments=attachments, **extra)
            if not cfg.api.enabled:
                with self.local.lease(cfg, cancel, emit):
                    result = execute()
            else:
                if cfg.api.stop_local_service:
                    self.local.request_stop()
                result = execute()
            message['content'] = result.get('content', message['content'])
            for key in ('results', 'result_content', 'analysis_content'):
                if key in result:
                    message[key] = result[key]
            message['followup_actions'] = self.followup_actions(cfg, result) if cfg.api.enabled else []
            message['status'] = result.get('status', 'complete')
            message['formal_content'] = result.get('formal_content', message['content'])
            message['formal'] = result.get('formal', message['status'] == 'complete')
            # UI cancellation does not rewrite the separate execution facts.
            if cancel.is_set():
                message['status'] = 'cancelled'
                message['formal'] = False
            self.store.save_evidence(sid, task['id'], {k: result[k] for k in ('evidence', 'attempts', 'target', 'metrics', 'material_metadata', 'goal', 'deliveries', 'results') if k in result})
            for phase, context in result.get('model_contexts', {}).items():
                status = 'complete' if context.complete and message['formal'] and not cancel.is_set() else 'incomplete'
                self.store.save_model_context(sid, task['id'], phase, context, status=status)
        except Exception as error:
            message['status'] = 'cancelled' if cancel.is_set() else 'failed'
            message['content'] += '\n\n' + safe_error(error, cfg.qwen.api_key)
        finally:
            with self.guard:
                if cancel.is_set():
                    message['status'] = 'cancelled'
                if self.closed:
                    message['status'] = 'interrupted'
                self.store.put_message(sid, message)
                task['status'] = message['status']
                self.event(task, 'message', message)
                self.event(task, 'task', self.public_task(task))

    def followup_actions(self, cfg, record):
        actions = []
        if record.get('evidence'):
            actions.append('L2')  # Explicit one-shot explanation works even when globally off.
        metadata = record.get('material_metadata', {})
        source = metadata.get('recording_source', '')
        from .model_capabilities import config_identity
        actual_audio = bool(record.get('metrics', {}).get('audio_received')
                            and record['metrics'].get('capability_identity') == config_identity(cfg.api))
        available = False
        if cfg.api.audio_input_enabled and (cfg.api.audio_input_source in {'preset','verified'} or actual_audio) and source:
            try:
                available = (Path(source).is_file() and Path(source).stat().st_size <= Attachments.LIMIT
                             and hashlib.sha256(Path(source).read_bytes()).hexdigest() == metadata.get('source_sha256'))
            except OSError:
                pass
        if available:
            actions.append('L3')
        if cfg.workflow.l4_enabled:
            actions.append('L4')
        return actions

    def followup(self, sid, source_task, mode):
        if mode not in {'L2','L3','L4'}:
            raise ValueError('本次动作必须是 L2/L3/L4')
        with self.guard:
            record = self.store.task_evidence(sid, source_task)
            cfg = self.prepared()
            if not cfg.api.enabled:
                raise ValueError('本次继续动作适用于云端 API；当前启用的是本地模型')
            from .workflow_policy import WorkflowPolicy
            cfg.workflow = WorkflowPolicy.validate(record.get('metrics', {}).get('workflow_policy'))
            if mode not in self.followup_actions(cfg, record):
                raise ValueError('原证据、材料或当前模型能力不支持此动作')
            if not record.get('goal'):
                raise ValueError('旧任务没有原目标绑定；请明确提出新目标')
            record['mode'] = mode
            return self.submit(sid, {'L2':'本次解释结果','L3':'本次分析原材料','L4':'基于这些结果进行通用讨论'}[mode], continuation=record)

    def cancel(self, task_id):
        with self.guard:
            task = self.tasks.get(task_id)
            if task is None:
                raise ValueError('任务不存在')
            # 重复取消要幂等：已经在 cancelling 的任务不再发一次事件（2026-10-07 实测
            # 连续三次取消会往事件流里塞三条同样的 task 事件）。
            if task['status'] in {'running', 'queued'}:
                task['cancel'].set()
                task['status'] = 'cancelling'
                self.event(task, 'task', self.public_task(task))
        return dict(ok=True)

    def poll(self, after):
        with self.guard:
            from .desktop_launch import model_settings_request
            navigation = model_settings_request(self.navigation_id)
            if navigation:
                self.navigation_id = navigation['id']
            try:
                after = int(after)
            except (TypeError, ValueError):
                raise ValueError('events.poll 的 after 必须是整数游标') from None
            reset = after > self.cursor or bool(self.events and after < self.events[0]['seq'] - 1)
            pending = [e for e in self.events if e['seq'] > after]
            # 单次返回量要封顶：events 最多留 4000 条，一次全量序列化约 2 MB，
            # 前端 220ms 一次轮询会把这 2 MB 反复搬过 WebView2 桥（2026-10-07）。
            # 截断时游标跟着最后一条返回的事件走，客户端下一次轮询自然续上。
            if len(pending) > POLL_EVENT_LIMIT:
                reset = True
                pending = pending[:POLL_EVENT_LIMIT]
            cursor = pending[-1]['seq'] if pending else min(after, self.cursor)
            result = dict(events=pending, cursor=cursor, reset=reset)
            if navigation:
                result['navigation'] = navigation
            return result

    def test_connection(self, kind, settings):
        cfg = self.prepared(settings)
        with self.guard:
            self.store.cleaner.secrets = tuple(v for v in set(self.store.cleaner.secrets) | {cfg.api.api_key, cfg.qwen.api_key} if v and v != 'EMPTY')
        if kind == 'audio':
            if not cfg.api.enabled:
                raise ValueError('音频验证入口仅用于当前云端 API 连接')
            from .audio_probe import probe_audio
            result = probe_audio(cfg, self.root / 'tasks')
        elif kind == 'text':
            try:
                if cfg.api.enabled:
                    response = budget.text_request(cfg, [dict(role='user', content='Reply OK.')])
                else:
                    with self.local.lease(cfg, threading.Event(), lambda *args: None):
                        response = budget.text_request(cfg, [dict(role='user', content='Reply OK.')])
                result = dict(status='verified' if response.strip() else 'unverified', reason='文字连接返回真实响应（不表示音频能力通过）', details=response)
            except Exception as error:
                result = dict(status='failed', reason=safe_error(error, cfg.qwen.api_key))
        else:
            raise ValueError('验证类型无效')
        from .api_diagnostics import request_identity
        result['identity'] = request_identity(cfg.qwen.base_url, cfg.qwen.model, cfg.qwen.api_key)
        return self.store.clean(result)

    def close(self):
        with self.guard:
            self.closed = True
            active = [t for t in self.tasks.values() if t['status'] in ACTIVE]
            for task in active:
                task['cancel'].set()
                task['message']['status'] = 'interrupted'
                try:
                    self.store.put_message(task['sessionId'], task['message'])
                except (OSError, ValueError) as error:
                    # 退出时库里写不进去（临时目录已删、库被占用）不该让 close() 崩掉：
                    # 之前这里会抛出 sqlite3.OperationalError，现在是中文 ValueError。
                    self.store_last_error = str(error)
            if self._created_sessions:
                try:
                    self.store.discard_empty_sessions(self._created_sessions)
                    self._created_sessions.clear()
                except (OSError, ValueError) as error:
                    self.store_last_error = str(error)
        for task in active:
            thread = task.get('thread')
            if thread is not None:
                thread.join(timeout=2)
        self.local.request_stop()
        if hasattr(self.executor, 'close'):
            self.executor.close()

    def rpc(self, method, params):
        """Methods explicitly listed here; never getattr/eval/exec of caller input."""
        params = params or {}
        if not isinstance(params, dict):
            raise ValueError('参数必须是对象')
        if method == 'sessions.search':
            # Read-only metadata result; same guard captures unflushed stream text.
            from .modern_search import literal_matcher
            term, archived = params.get('searchTerm'), params.get('archived', False)
            matcher = literal_matcher(term, archived)
            with self.guard:
                result = self.store.search(term, archived)
                if matcher and not archived:
                    for task in self.tasks.values():
                        if task['status'] in ACTIVE and matcher.search(self.store.clean(task['message']['content'])) and task['sessionId'] not in result['sessionIds']:
                            result['sessionIds'].append(task['sessionId'])
                return result
        if method == 'sessions.pin':
            # Metadata only, including errors: no task gate or config/provider access.
            with self.guard:
                return self.store.pin(params.get('sessionId'), params.get('pinned'))
        if method in {'sections.create','sections.update','sections.delete','sections.archive','sessions.section','sessions.archive','sessions.fork','sessions.group'}:
            with self.guard:
                organization=self.store.organization
                if method=='sessions.group':
                    ids=params.get('sessionIds');action=params.get('action')
                    if isinstance(ids,list) and action in ('delete','archive') and any(t['sessionId'] in ids and t['status'] in ACTIVE for t in self.tasks.values()):
                        raise ValueError('分组中有运行会话，不能删除或归档')
                    return organization.group_action(ids,action)
                if method=='sections.create':return organization.create(params.get('name'),params.get('appearance'))
                if method=='sections.update':return organization.update(params.get('sectionId'),params.get('name'),params.get('appearance',...))
                if method=='sections.delete':return organization.delete(params.get('sectionId'))
                if method=='sessions.section':
                    if 'sectionId' not in params:raise ValueError('缺少目标分区标识')
                    return organization.move(params.get('sessionId'),params.get('sectionId'),params.get('beforeSessionId'))
                if method=='sessions.fork':return organization.fork(self.get_session(params.get('sessionId')))
                if method=='sections.archive' and 'sectionId' not in params:raise ValueError('缺少分区标识')
                ids=organization.members(params.get('sectionId')) if method=='sections.archive' else [params.get('sessionId')]
                if params.get('archived') is True and any(t['sessionId'] in ids and t['status'] in ACTIVE for t in self.tasks.values()):
                    raise ValueError('分区中有运行会话，不能归档')
                return organization.archive(ids,params.get('archived'))
        try:
            if method == 'bootstrap': return self.bootstrap()
            if method == 'sessions.create':
                with self.guard:
                    session = self.store.organization.create_session(params.get('title', '新会话'),params.get('sectionId'))
                    self._created_sessions.add(session['id'])
                    return session
            if method == 'sessions.get': return self.get_session(param(params, 'sessionId'))
            if method == 'sessions.context': return self.context_status(param(params, 'sessionId'), params.get('text', ''), params.get('attachmentIds', []))
            if method == 'sessions.rename':
                self.store.rename(param(params, 'sessionId'), param(params, 'title')); return dict(ok=True)
            if method == 'sessions.delete':
                with self.guard:
                    if any(t['sessionId'] == param(params, 'sessionId') and t['status'] in ACTIVE for t in self.tasks.values()):
                        raise ValueError('运行中的会话不能删除')
                    self.store.delete(param(params, 'sessionId'))
                    if hasattr(self.executor, 'release_session'):
                        self.executor.release_session(param(params, 'sessionId'))
                return dict(ok=True)
            if method == 'sessions.view':
                self.store.view(param(params, 'sessionId'), draft=params.get('draft'), scroll=params.get('scroll'), anchor=params.get('anchor')); return dict(ok=True)
            if method == 'tasks.followup': return self.followup(param(params, 'sessionId'), param(params, 'sourceTaskId'), param(params, 'mode'))
            if method == 'tasks.submit': return self.submit(param(params, 'sessionId'), param(params, 'text'), params.get('attachmentIds', []))
            if method == 'tasks.cancel': return self.cancel(param(params, 'taskId'))
            if method == 'events.poll': return self.poll(params.get('after', 0))
            if method == 'settings.get': return self.settings.get()
            if method == 'settings.save': return self.settings.save(param(params, 'settings'))
            if method == 'settings.test': return self.test_connection(param(params, 'kind'), param(params, 'settings'))
            if method == 'attachments.import':
                raw = str(param(params, 'data'))
                if len(raw) > Attachments.LIMIT * 4 // 3 + 4:
                    raise ValueError('附件过大')
                return self.attachments.import_bytes(param(params, 'name'), base64.b64decode(raw, validate=True))
            if method == 'attachments.preview': return self.attachments.preview(param(params, 'attachmentId'))
            if method == 'attachments.choose':
                if self.window is None: raise RuntimeError('原生文件选择器需要桌面宿主')
                import webview
                paths = self.window.create_file_dialog(webview.FileDialog.OPEN, allow_multiple=True, file_types=('材料 (*.txt;*.md;*.csv;*.json;*.wav;*.png;*.jpg;*.jpeg;*.webp)',)) or []
                result = []
                for path in paths:
                    path = Path(path)
                    if path.stat().st_size > Attachments.LIMIT: raise ValueError('附件过大')
                    result.append(self.attachments.import_bytes(path.name, path.read_bytes()))
                return result
            if method == 'links.open':
                url = str(param(params, 'url'))
                assert_endpoint(url, allow_cloud=True)
                if self.window is None or not self.window.create_confirmation_dialog('打开外部链接', url):
                    return dict(ok=False)
                import webbrowser
                webbrowser.open(url); return dict(ok=True)
            raise ValueError('未批准的宿主方法')
        except Exception as error:
            cfg = self.settings.candidate()
            raise ValueError(safe_error(error, cfg.qwen.api_key)) from None


class HostAPI:
    # pywebview recursively exposes public attributes: keep service PRIVATE.
    def __init__(self, application):
        self._application = application

    def rpc(self, method, params):
        if hasattr(self, '_origin'):
            from urllib.parse import urlsplit
            current = urlsplit(self._application.window.get_current_url())
            if current.scheme + '://' + current.netloc != self._origin:
                raise ValueError('非本地应用页面不能访问宿主')
        result = self._application.rpc(method, params)
        if method == 'bootstrap' and hasattr(self, '_on_connected'):
            callback = self._on_connected
            del self._on_connected
            callback()
        return result
