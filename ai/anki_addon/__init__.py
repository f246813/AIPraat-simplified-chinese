"""AIPraat desktop entry. Audio is resolved by Anki, never guessed from the word."""
import json
import os
import re
import subprocess
from pathlib import Path

from anki.sound import SoundOrVideoTag
from aqt import mw, gui_hooks
from aqt.qt import QAction, QInputDialog
from aqt.utils import showWarning, tooltip

EXTENSIONS = {'.wav', '.mp3', '.m4a', '.flac', '.ogg', '.opus', '.oga', '.aac', '.aiff', '.aif'}


def config():
    values = mw.addonManager.getConfig(__name__) or {}
    # Editable file overrides these two settings; Anki's Config dialog can
    # configure the rest without editing a card template.
    text = (Path(__file__).parent / 'entry.js').read_text(encoding='utf-8')
    for name, key in [('AIPRAAT_ENTRY', 'mode'), ('AIPRAAT_MAGNIFIER', 'magnifier_selector')]:
        match = re.search(r'const\s+' + name + r'\s*=\s*("(?:[^"\\]|\\.)*")\s*;', text)
        if match:
            values[key] = json.loads(match.group(1))
    return values


def audio_files(card):
    cfg = config()
    field = cfg.get('audio_field')
    if field:
        note = card.note()
        if field not in note:
            return []
        names = mw.col.media.files_in_str(note.mid, note[field])
    else:
        names = [tag.filename for tag in card.question_av_tags() + card.answer_av_tags() if isinstance(tag, SoundOrVideoTag)]
    return list(dict.fromkeys(name for name in names if Path(name).suffix.lower() in EXTENSIONS))


def resolve_paths(values):
    bridge = Path(values.get('bridge_script') or '')
    python = Path(values.get('python_executable') or '')
    if bridge.is_file() and python.is_file():
        return bridge, python

    preferences = {}
    profile = os.environ.get('APPDATA', '')
    preference_file = Path(profile) / 'Praat' / 'Preferences.txt' if profile else None
    if preference_file and preference_file.is_file():
        for line in preference_file.read_text(encoding='utf-8', errors='replace').splitlines():
            key, separator, value = line.partition(':')
            if separator:
                preferences[key.strip()] = value.strip()

    directories = []
    configured_ai = preferences.get('AI.projectDirectory', '')
    if configured_ai:
        directories.append(Path(configured_ai))
    directories.append(Path(r'C:\AIPraat\ai'))

    for ai_directory in directories:
        candidate_bridge = ai_directory / 'run_anki_bridge.py'
        candidate_python = Path(preferences.get('Python.executablePath', '') or '')
        settings_file = ai_directory.parent / 'install-settings.json'
        if not candidate_python.is_file() and settings_file.is_file():
            try:
                installed = json.loads(settings_file.read_text(encoding='utf-8-sig'))
                candidate_python = Path(installed.get('python_path') or '')
            except (OSError, ValueError, AttributeError):
                candidate_python = Path('')
        if candidate_bridge.is_file() and candidate_python.is_file():
            return candidate_bridge, candidate_python
    return None, None


def card_from_context(context):
    card = getattr(context, 'card', None)
    return card() if callable(card) else card


def open_card(card):
    if card is None:
        showWarning('请先打开一张卡片。'); return
    names = audio_files(card)
    if not names:
        showWarning('当前卡片没有可打开的音频。TTS 没有对应的本地声音文件。'); return
    filename = names[0]
    if len(names) > 1:
        filename, accepted = QInputDialog.getItem(mw, '选择卡片音频', '要查看哪段声音的语图？', names, 0, False)
        if not accepted:
            return
    cfg = config()
    note = card.note()
    request = dict(audio=filename, media_dir=mw.col.media.dir())
    for param, key in [('start', 'start_field'), ('end', 'end_field')]:
        field = cfg.get(key)
        if field:
            if field not in note:
                showWarning(f'当前笔记没有配置的时间字段：{field}'); return
            request[param] = note[field].strip()
    bridge, python = resolve_paths(cfg)
    if bridge is None:
        showWarning('未找到 AIPraat 或其 Python 环境。请先运行 AIPraat 并完成 Python 配置，再重启 Anki。'); return

    def work():
        process = subprocess.run([str(python), str(bridge), '--request', json.dumps(request, ensure_ascii=False)],
                                 capture_output=True, timeout=300,
                                 creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        lines = process.stdout.decode('utf-8', errors='replace').strip().splitlines()
        if not lines:
            raise RuntimeError(process.stderr.decode('utf-8', errors='replace') or 'AIPraat 没有返回结果。')
        response = json.loads(lines[-1])
        if not response.get('ok'):
            raise RuntimeError(response.get('error', '打开音频失败'))
        return response

    def complete(future):
        try:
            result = future.result()
            tooltip('已在 AIPraat 中打开语图' + ('（临时 WAV 解码）' if result.get('converted') else ''))
        except Exception as error:
            showWarning(str(error))
    mw.taskman.run_in_background(work, complete)


def render(text, card, kind):
    # Anki reuses the same document when the card or entry setting changes.
    text += '<script>if(window.aipraatMagnifierHandler){document.removeEventListener("click",window.aipraatMagnifierHandler,true);window.aipraatMagnifierHandler=null;}</script>'
    if not audio_files(card):
        return text
    cfg = config()
    if cfg.get('mode', 'button') in ('button', 'both'):
        text += '<div style="margin:12px 0"><button onclick="pycmd(\'aipraat:open\');return false;">查看语图 · AIPraat</button></div>'
    if cfg.get('mode') in ('magnifier', 'both'):
        script = (Path(__file__).parent / 'entry.js').read_text(encoding='utf-8')
        # Constants are read in Python; avoid redeclaration as cards change.
        script = script[script.index('function aipraatAttachMagnifier'):]
        text += '<script>' + script + ';setTimeout(()=>aipraatAttachMagnifier(' + json.dumps(cfg.get('magnifier_selector', '.search a')) + '),0);</script>'
    return text


def receive(handled, message, context):
    if message != 'aipraat:open':
        return handled
    open_card(card_from_context(context))
    return (True, None)


gui_hooks.card_will_show.append(render)
gui_hooks.webview_did_receive_js_message.append(receive)
action = QAction('在 AIPraat 中查看当前卡片语图', mw)
action.setShortcut('Ctrl+Alt+P')
action.triggered.connect(lambda: open_card(getattr(mw.reviewer, 'card', None)))
mw.form.menuTools.addAction(action)
