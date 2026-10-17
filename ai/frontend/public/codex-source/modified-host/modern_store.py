"""Resumable dialogue storage. Never an execution checkpoint or legacy migration."""
from __future__ import annotations

import json
import math
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .conversation_store import ConversationStore


def now():
    return datetime.now(timezone.utc).isoformat()


def identity():
    return uuid.uuid4().hex


class ModernStore:
    def __init__(self, path: Path, legacy: Path | None = None, *, secrets=(), project_path=None):
        self.path, self.legacy = Path(path), Path(legacy) if legacy else None
        self.project_path = str(project_path) if project_path else None
        if self.legacy and self.path.resolve() == self.legacy.resolve():
            raise ValueError('新会话库不能覆盖旧记录库')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.cleaner = ConversationStore.__new__(ConversationStore)
        self.cleaner.secrets = tuple(v for v in secrets if v and v != 'EMPTY')
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS sessions(
                  id TEXT PRIMARY KEY,title TEXT NOT NULL,created TEXT NOT NULL,updated TEXT NOT NULL,
                  draft TEXT NOT NULL DEFAULT '',scroll REAL NOT NULL DEFAULT 0,
                  anchor TEXT NOT NULL DEFAULT '',
                  summary TEXT NOT NULL DEFAULT '',summarized INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS session_pins(
                  session TEXT PRIMARY KEY NOT NULL REFERENCES sessions(id) ON DELETE CASCADE);
                CREATE TABLE IF NOT EXISTS messages(
                  seq INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT UNIQUE NOT NULL,
                  session TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,payload TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS messages_session ON messages(session,seq);
                CREATE TABLE IF NOT EXISTS evidence(
                  seq INTEGER PRIMARY KEY AUTOINCREMENT,session TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                  task TEXT NOT NULL,payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS model_events(
                  seq INTEGER PRIMARY KEY AUTOINCREMENT,
                  session TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                  task TEXT NOT NULL,phase TEXT NOT NULL,epoch INTEGER NOT NULL,
                  status TEXT NOT NULL,payload TEXT NOT NULL);
            ''')
            # An existing database predates the reading anchor; adding it is additive.
            if 'anchor' not in {column[1] for column in db.execute('PRAGMA table_info(sessions)')}:
                db.execute("ALTER TABLE sessions ADD COLUMN anchor TEXT NOT NULL DEFAULT ''")
            columns = {column[1] for column in db.execute('PRAGMA table_info(sessions)')}
            if 'context_epoch' not in columns:
                db.execute('ALTER TABLE sessions ADD COLUMN context_epoch INTEGER NOT NULL DEFAULT 0')
            for name, declaration in [('project_path','TEXT'),('connection_id',"TEXT NOT NULL DEFAULT 'local'"),('connection_label',"TEXT NOT NULL DEFAULT '本地'")]:
                if name not in columns: db.execute(f'ALTER TABLE sessions ADD COLUMN {name} {declaration}')
            if self.project_path:
                # Existing modern records belong to this host's project DB. Never
                # replace an already recorded origin or write the legacy database.
                db.execute('UPDATE sessions SET project_path=? WHERE project_path IS NULL AND connection_id=?',(self.project_path,'local'))
            # Restart means no task is resumed. Preserve partial text and delivery facts.
            # 除 running 外还要收掉 queued / cancelling：它们同样不会继续运行，
            # 留在库里会被当成正常助手回合回灌进下一轮历史。
            for mid, payload in db.execute('SELECT id,payload FROM messages').fetchall():
                message = json.loads(payload)
                if message.get('status') in {'running', 'queued', 'cancelling'}:
                    message['status'] = 'interrupted'
                    message['content'] += '\n\n[桌面进程已中断；未恢复或重放任何工具操作]'
                    db.execute('UPDATE messages SET payload=? WHERE id=?', (self.encode(message), mid))
        from .modern_organization import HistoryOrganization
        self.organization=HistoryOrganization(self)

    def clean(self, value):
        return self.cleaner.clean(value)

    def encode(self, value):
        return json.dumps(self.clean(value), ensure_ascii=False)

    @contextmanager
    def connect(self):
        # timeout 是等锁上限；过长会让持 guard 的写操作迟迟不返回，
        # 期间前端轮询一并被挡住。超时给出可读错误。
        try:
            db = sqlite3.connect(self.path, timeout=5)
        except sqlite3.OperationalError as error:
            raise ValueError(f'会话库暂时不可写（{error}）；请稍后重试。') from error
        db.execute('PRAGMA foreign_keys=ON')
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        except sqlite3.OperationalError as error:
            if 'locked' in str(error).lower() or 'busy' in str(error).lower():
                raise ValueError('会话库正被另一个操作占用；请稍后重试。') from error
            raise
        finally:
            db.close()

    @contextmanager
    def legacy_connect(self):
        if not self.legacy or not self.legacy.is_file():
            yield None
            return
        db = sqlite3.connect(self.legacy.resolve().as_uri() + '?mode=ro', uri=True, timeout=10)
        try:
            yield db
        finally:
            db.close()

    def sessions(self):
        with self.connect() as db:
            result = [self.session_row(row) for row in db.execute('''
                SELECT sessions.*, session_pins.session IS NOT NULL AS pinned
                FROM sessions LEFT JOIN session_pins ON session_pins.session=sessions.id
                ORDER BY pinned DESC, sessions.updated DESC
            ''')]
        with self.legacy_connect() as db:
            if db:
                for sid, created in db.execute('SELECT id,created FROM sessions ORDER BY created DESC'):
                    title = '旧记录 · ' + created[:16]
                    row = db.execute("SELECT payload FROM events WHERE session=? AND kind='user' ORDER BY id LIMIT 1", (sid,)).fetchone()
                    if row:
                        title = '旧记录 · ' + str(json.loads(row[0]).get('prompt', title))[:40]
                    result.append(dict(id='legacy:' + sid, title=title, created=created, updated=created,
                                       readOnly=True, pinned=False, draft='', scroll=0, anchor=None,
                                       projectPath=None,connectionId='local',connectionLabel='本地'))
        return self.clean(self.organization.project(result))

    @staticmethod
    def session_row(row):
        raw = row['anchor'] if 'anchor' in row.keys() else ''
        return {**{key: row[key] for key in ('id', 'title', 'created', 'updated', 'draft', 'scroll')},
                'anchor': json.loads(raw) if raw else None,
                'readOnly': False, 'pinned': bool(row['pinned']),
                'projectPath':row['project_path'],'connectionId':row['connection_id'],'connectionLabel':row['connection_label']}

    def new_session(self, title='新会话'):
        sid, stamp = identity(), now()
        with self.connect() as db:
            db.execute('INSERT INTO sessions(id,title,created,updated,project_path) VALUES (?,?,?,?,?)', (sid, self.clean(str(title)[:100]), stamp, stamp, self.project_path))
        return self.get(sid)['session']

    def discard_empty_sessions(self, session_ids):
        """Discard unused window-created sessions, preserving drafts and history."""
        with self.connect() as db:
            db.executemany('''
                DELETE FROM sessions
                WHERE id=? AND title='新会话' AND draft='' AND summary=''
                  AND NOT EXISTS (SELECT 1 FROM messages WHERE session=sessions.id)
                  AND NOT EXISTS (SELECT 1 FROM evidence WHERE session=sessions.id)
                  AND NOT EXISTS (SELECT 1 FROM model_events WHERE session=sessions.id)
                  AND NOT EXISTS (SELECT 1 FROM session_pins WHERE session=sessions.id)
                  AND NOT EXISTS (SELECT 1 FROM history_metadata WHERE session=sessions.id)
            ''', ((sid,) for sid in session_ids))

    def discard_all_empty_sessions(self):
        """Delete every unused session, including blanks left by a crashed window.

        Same conservative boundary as discard_empty_sessions: default title, no
        draft, no summary, no messages/evidence/model events, not pinned and not
        placed in any history section. Returns the deleted session ids.
        """
        with self.connect() as db:
            ids = [row[0] for row in db.execute('''
                SELECT id FROM sessions
                WHERE title='新会话' AND draft='' AND summary=''
                  AND NOT EXISTS (SELECT 1 FROM messages WHERE session=sessions.id)
                  AND NOT EXISTS (SELECT 1 FROM evidence WHERE session=sessions.id)
                  AND NOT EXISTS (SELECT 1 FROM model_events WHERE session=sessions.id)
                  AND NOT EXISTS (SELECT 1 FROM session_pins WHERE session=sessions.id)
                  AND NOT EXISTS (SELECT 1 FROM history_metadata WHERE session=sessions.id)
            ''').fetchall()]
            if ids:
                db.executemany('DELETE FROM sessions WHERE id=?', ((sid,) for sid in ids))
        return ids

    def writable(self, sid):
        if not isinstance(sid, str) or sid.startswith('legacy:'):
            raise ValueError('旧记录仅供只读查看')
        with self.connect() as db:
            if not db.execute('SELECT 1 FROM sessions WHERE id=?', (sid,)).fetchone():
                raise ValueError('会话不存在')

    def search(self, search_term, archived=False):
        from .modern_search import search_sessions
        return search_sessions(self, search_term, archived)

    def get(self, sid):
        if not isinstance(sid,str) or not sid.strip():raise ValueError('会话标识无效')
        if sid.startswith('legacy:'):
            sessions = {s['id']: s for s in self.sessions()}
            if sid not in sessions:
                raise ValueError('旧会话不存在')
            messages = []
            with self.legacy_connect() as db:
                rows = db.execute('SELECT id,kind,payload FROM events WHERE session=? ORDER BY id', (sid[7:],)).fetchall()
            for seq, kind, raw in rows:
                value = json.loads(raw)
                if kind == 'user':
                    messages.append(dict(id='legacy-user-' + str(seq), role='user', content=value.get('prompt', '')))
                elif kind == 'turn':
                    activities = [dict(id=f'legacy-tool-{seq}-{i}', type='tool', name=a.get('tool'), args=a.get('arguments', {}),
                                       result=a.get('rows', a.get('reason', '')), status=a.get('status'), execution=a.get('execution', ''))
                                  for i, a in enumerate(value.get('attempts', []))]
                    messages.append(dict(id='legacy-assistant-' + str(seq), role='assistant', content=value.get('report', ''),
                                         status=value.get('status', 'complete'), activities=activities))
            return dict(session=sessions[sid], messages=self.clean(messages))
        self.writable(sid)
        with self.connect() as db:
            row = db.execute('''
                SELECT sessions.*, session_pins.session IS NOT NULL AS pinned
                FROM sessions LEFT JOIN session_pins ON session_pins.session=sessions.id
                WHERE sessions.id=?
            ''', (sid,)).fetchone()
            messages = [json.loads(row['payload']) for row in db.execute('SELECT payload FROM messages WHERE session=? ORDER BY seq', (sid,))]
        return dict(session=self.organization.project([self.session_row(row)])[0], messages=messages)

    def pin(self, sid, pinned):
        if type(pinned) is not bool:
            raise ValueError('pinned 必须是布尔值')
        self.writable(sid)
        from .modern_organization import PINNED_SECTION
        current=self.get(sid)['session']
        if current['pinned']==pinned and (not pinned or current['sectionId']==PINNED_SECTION):return current
        self.organization.move(sid,PINNED_SECTION if pinned else None)
        return self.get(sid)['session']

    def rename(self, sid, title):
        self.writable(sid)
        if not str(title).strip():
            raise ValueError('会话名称不能为空')
        with self.connect() as db:
            db.execute('UPDATE sessions SET title=?,updated=? WHERE id=?', (self.clean(str(title).strip()[:100]), now(), sid))

    def delete(self, sid):
        if isinstance(sid,str) and sid.startswith('legacy:'):
            # Remove the entry through local metadata; keep the legacy source read-only.
            self.get(sid)
            with self.connect() as db:
                db.execute('INSERT INTO history_metadata(session,deleted) VALUES (?,1) ON CONFLICT(session) DO UPDATE SET deleted=1',(sid,))
            return
        self.writable(sid)
        with self.connect() as db:
            db.execute('DELETE FROM history_metadata WHERE session=?',(sid,))
            db.execute('DELETE FROM sessions WHERE id=?', (sid,))

    def view(self, sid, *, draft=None, scroll=None, anchor=None):
        self.writable(sid)
        with self.connect() as db:
            if draft is not None:
                db.execute('UPDATE sessions SET draft=? WHERE id=?', (self.clean(str(draft)[:100000]), sid))
            if scroll is not None:
                db.execute('UPDATE sessions SET scroll=? WHERE id=?', (max(0, float(scroll)), sid))
            # The anchor is the durable reading position; `scroll` is only a pixel
            # fallback that stops being meaningful once the transcript is windowed.
            if anchor is not None:
                db.execute('UPDATE sessions SET anchor=? WHERE id=?', (json.dumps(self.reading_anchor(anchor), ensure_ascii=False), sid))

    @staticmethod
    def reading_anchor(anchor):
        """Validate a reading anchor: a real message id plus a finite offset."""
        if not isinstance(anchor, dict):
            raise ValueError('阅读锚点必须是对象')
        message = anchor.get('messageId')
        offset = anchor.get('offset')
        if not isinstance(message, str) or not message.strip() or len(message) > 200:
            raise ValueError('阅读锚点的消息标识无效')
        if isinstance(offset, bool) or not isinstance(offset, (int, float)) or not math.isfinite(float(offset)):
            raise ValueError('阅读锚点的偏移无效')
        return {'messageId': message.strip(), 'offset': round(float(offset), 2)}

    def put_message(self, sid, message):
        self.writable(sid)
        with self.connect() as db:
            existing = db.execute('SELECT session FROM messages WHERE id=?', (message['id'],)).fetchone()
            if existing and existing['session'] != sid:
                raise ValueError('消息归属不匹配')
            db.execute('INSERT INTO messages(id,session,payload) VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload',
                       (message['id'], sid, self.encode(message)))
            db.execute('UPDATE sessions SET updated=? WHERE id=?', (now(), sid))

    def save_evidence(self, sid, task_id, payload):
        self.writable(sid)
        payload = self.bounded_evidence(payload)
        with self.connect() as db:
            row = db.execute('SELECT seq FROM evidence WHERE session=? AND task=? ORDER BY seq DESC LIMIT 1', (sid, task_id)).fetchone()
            if row:
                db.execute('UPDATE evidence SET payload=? WHERE seq=?', (self.encode(payload), row[0]))
            else:
                db.execute('INSERT INTO evidence(session,task,payload) VALUES (?,?,?)', (sid, task_id, self.encode(payload)))

    #: 单条证据记录的上限，用于兜底自定义脚本把大量行写入结果文件的情况。
    #: 超限则截断并标明，原始文件仍保留在任务目录中。
    EVIDENCE_PAYLOAD_LIMIT = 1_000_000
    EVIDENCE_ROW_LIMIT = 4000

    def bounded_evidence(self, payload):
        if not isinstance(payload, dict):
            return payload
        text = self.encode(payload)
        if len(text) <= self.EVIDENCE_PAYLOAD_LIMIT:
            return payload
        trimmed = dict(payload)
        for key in ('attempts', 'evidence'):
            items = trimmed.get(key)
            if not isinstance(items, list):
                continue
            trimmed[key] = [
                {**item,
                 'rows': ['【过长输出已截断，完整结果在任务目录里】'] if len(self.encode(item.get('rows') or [])) > self.EVIDENCE_ROW_LIMIT
                 else item.get('rows', [])}
                if isinstance(item, dict) else item
                for item in items
            ]
        trimmed['truncated'] = True
        return trimmed

    def task_evidence(self, sid, task_id):
        self.writable(sid)
        with self.connect() as db:
            row = db.execute('SELECT payload FROM evidence WHERE session=? AND task=? ORDER BY seq DESC LIMIT 1', (sid, task_id)).fetchone()
        if row is None:
            raise ValueError('原任务没有可复用的证据记录')
        return json.loads(row[0])

    def context(self, sid):
        self.writable(sid)
        with self.connect() as db:
            session = db.execute('SELECT summary,summarized FROM sessions WHERE id=?', (sid,)).fetchone()
            rows = db.execute('SELECT seq,payload FROM messages WHERE session=? ORDER BY seq', (sid,)).fetchall()
            evidence = [json.loads(row[0]) for row in db.execute('SELECT payload FROM evidence WHERE session=? ORDER BY seq', (sid,))]
        history = []
        if session['summary']:
            history.append(dict(role='user', content='较早对话摘要（非测量证据）：\n' + session['summary']))
        for row in rows:
            message = json.loads(row['payload'])
            if (row['seq'] > session['summarized'] and message.get('status') not in {'running', 'failed', 'cancelled', 'interrupted'}
                    and (message.get('role') != 'assistant' or message.get('status') != 'partial' or message.get('formal'))):
                history.append(dict(role=message['role'], content=message.get('formal_content', message['content']), _seq=row['seq']))
        return history, evidence

    def summarize(self, sid, summary, through):
        # Original history/evidence are never replaced or deleted by compaction.
        self.writable(sid)
        with self.connect() as db:
            db.execute('UPDATE sessions SET summary=?,summarized=?,context_epoch=context_epoch+1 WHERE id=?', (self.clean(summary), int(through), sid))

    def context_epoch(self, sid):
        with self.connect() as db:
            return db.execute('SELECT context_epoch FROM sessions WHERE id=?', (sid,)).fetchone()[0]

    def save_model_context(self, sid, task, phase, context, status='complete'):
        self.writable(sid)
        payload = context.payload()
        if payload is None:
            return  # Task-local binary chain cannot be recovered as accessible audio.
        with self.connect() as db:
            epoch = max(context.epoch, self.context_epoch(sid))
            db.execute('UPDATE sessions SET context_epoch=? WHERE id=?', (epoch, sid))
            payload['epoch'] = epoch
            db.execute('INSERT INTO model_events(session,task,phase,epoch,status,payload) VALUES (?,?,?,?,?,?)',
                       (sid, task, phase, epoch, status, self.encode(payload)))

    def model_context(self, sid, phase):
        from .session_context import PhaseContext
        epoch = self.context_epoch(sid)
        with self.connect() as db:
            row = db.execute('SELECT payload FROM model_events WHERE session=? AND phase=? AND epoch=? AND status=? ORDER BY seq DESC LIMIT 1',
                             (sid, phase, epoch, 'complete')).fetchone()
        return PhaseContext.restore(json.loads(row[0])) if row else None
