"""Local, cancellable MFA G2P. No model downloads or cloud inference."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import unicodedata
from pathlib import Path

from .phone_labels import map_sequence


def launcher_command(launcher: dict) -> list[str]:
    conda = str(launcher.get('conda_executable') or '').strip()
    environment = str(launcher.get('conda_environment') or '').strip()
    if conda or environment:
        if not conda or not environment or environment.startswith('-'):
            raise ValueError('请同时填写 Conda 可执行文件和有效环境名。')
        executable = conda
        args = ['run', '--no-capture-output', '-n', environment, 'mfa']
    else:
        executable = str(launcher.get('executable') or 'mfa').strip()
        args = []
    executable = str(Path(executable).expanduser())
    resolved = shutil.which(executable) or (executable if Path(executable).is_file() else '')
    if not resolved:
        raise ValueError('未找到 MFA/Conda 可执行文件，请配置本地安装路径。')
    return [str(Path(resolved).resolve()), *args]


def _terminate(process, tree=None):
    if tree is not None:
        tree.close()  # Includes descendants after the launcher itself has exited.
    elif os.name != 'nt':
        import signal
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if process.poll() is None:
        process.kill()
    process.wait(timeout=10)


def run_command(command: list[str], cancel: threading.Event | None = None,
                timeout: float = 120, cwd: str | Path | None = None) -> str:
    cancel = cancel or threading.Event()
    if cancel.is_set():
        raise ValueError('已取消音位生成。')
    environment = {**os.environ, 'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8'}
    options = dict(stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                   encoding='utf-8', errors='replace', cwd=cwd, env=environment)
    if os.name == 'nt':
        options['creationflags'] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    else:
        options['start_new_session'] = True
    tree = None
    if os.name == 'nt':
        from .mfa_process import WindowsProcessTree
        tree = WindowsProcessTree()
    try:
        process = subprocess.Popen(command, **options)
    except OSError as error:
        if tree:
            tree.close()
        raise ValueError(f'MFA 启动失败：{error}') from error
    deadline = time.monotonic() + timeout
    try:
        if tree:
            tree.assign(process)
        while True:
            if cancel.is_set():
                raise ValueError('已取消音位生成。')
            if time.monotonic() >= deadline:
                raise ValueError('MFA 操作超时，请检查环境和模型。')
            try:
                stdout, stderr = process.communicate(timeout=.1)
                break
            except subprocess.TimeoutExpired:
                continue
        if cancel.is_set():
            raise ValueError('已取消音位生成。')
        if process.returncode:
            raise ValueError('MFA 操作失败：' + (stderr.strip() or stdout.strip())[-4000:])
        return stdout + '\n' + stderr
    finally:
        _terminate(process, tree)


def check_launcher(launcher: dict, cancel: threading.Event | None = None) -> dict:
    command = launcher_command(launcher)
    try:
        version = run_command([*command, '--version'], cancel, 30).strip()
    except ValueError as error:
        # MFA 3.4 exposes `mfa version`, not the global --version option.
        if '--version' not in str(error) or not re.search(r'No such option|unrecognized arguments', str(error), re.I):
            raise
        version = run_command([*command, 'version'], cancel, 30).strip()
    help_text = run_command([*command, 'g2p', '--help'], cancel, 30)
    # Older MFA versions put the model first. Support only advertised layouts.
    cleaned = re.sub(r'\x1b\[[0-9;]*m', '', help_text).upper()
    usage = re.search(r'INPUT_(?:PATH|DIRECTORY)|G2P_MODEL_(?:PATH|NAME)', cleaned)
    if not usage or 'OUTPUT_PATH' not in cleaned or '--NUM_PRONUNCIATIONS' not in cleaned:
        raise ValueError('此 MFA G2P 接口不受支持，请安装支持词表和 --num_pronunciations 的 MFA 版本。')
    order = 'input-first' if usage[0].startswith('INPUT_') else 'model-first'
    return dict(version=version, help_order=order, status='MFA G2P 接口检查通过（尚未验证模型）')


def english_words(text: str) -> list[str]:
    if not isinstance(text, str) or not text.strip() or len(text) > 2000:
        raise ValueError('请输入参考文本（最多 2000 个字符）。')
    normalized = unicodedata.normalize('NFKC', text).replace('’', "'").lower()
    # Deliberately reject numbers, abbreviations and unknown graphemes rather than erase them.
    if re.search(r"[^a-z\s'.,!?;:\-\"()]", normalized):
        raise ValueError('首版仅支持英语文字和常见标点；数字、缩写或特殊字符请写出完整读法。')
    if re.search(r'(?<=[a-z])\.(?=[a-z])', normalized):
        raise ValueError('请将缩写写成完整读法后生成音位。')
    words = re.findall(r"[a-z]+(?:'[a-z]+)*", normalized)
    if not words or len(words) > 200:
        raise ValueError('参考文本需含英语单词，且不超过 200 个词。')
    return words


def generate(snapshot: dict, transcript: str, cancel: threading.Event) -> dict:
    from .mfa_resources import parse_dictionary, read_model_metadata, _phones
    preset = snapshot['preset']
    if preset['language'] != 'en':
        raise ValueError('首版 MFA G2P 自动生成仅支持英语；其他语言请使用手动 IPA。')
    if not preset.get('status', {}).get('can_generate'):
        raise ValueError('G2P 资源尚未就绪：' + '；'.join(preset.get('status', {}).get('issues', [])))
    sequence = english_words(transcript)
    unique = list(dict.fromkeys(sequence))
    capability = check_launcher(snapshot['launcher'], cancel)
    with tempfile.TemporaryDirectory(prefix='praat_g2p_') as directory:
        root = Path(directory)
        words_file = root / 'words.txt'
        result_file = root / 'pronunciations.dict'
        words_file.write_text('\n'.join(unique) + '\n', encoding='utf-8')
        model = snapshot['paths']['g2p']
        args = [str(words_file), model] if capability['help_order'] == 'input-first' else [model, str(words_file)]
        command = launcher_command(snapshot['launcher']) + ['g2p', *args, str(result_file), '--num_pronunciations', '3']
        run_command(command, cancel, 180, root)
        if not result_file.is_file():
            raise ValueError('MFA G2P 没有输出读音文件。')
        generated = parse_dictionary(result_file)
        missing = [word for word in unique if not generated.get(word)]
        if missing:
            raise ValueError('MFA G2P 未生成这些词的读音：' + '、'.join(missing))
        dictionary = snapshot['paths'].get('dictionary', '')
        known = parse_dictionary(Path(dictionary)) if dictionary and Path(dictionary).is_file() else {}
        acoustic_path = snapshot['paths'].get('acoustic', '')
        acoustic_phones = _phones(read_model_metadata(acoustic_path)) if acoustic_path else None
        words = []
        for word in unique:
            candidates, seen = [], set()
            for readings, source in ((generated[word], 'MFA G2P'), (known.get(word, []), '配套词典')):
                for raw in readings:
                    key = tuple(raw)
                    if key in seen:
                        continue
                    seen.add(key)
                    if acoustic_phones and set(raw) - acoustic_phones:
                        raise ValueError('生成音位未被配套声学模型覆盖：' + ', '.join(sorted(set(raw) - acoustic_phones)))
                    candidate = map_sequence(raw, preset)
                    candidate['source'] = source
                    candidates.append(candidate)
                    if len(candidates) >= 6:
                        break
                if len(candidates) >= 6:
                    break
            words.append(dict(word=word, candidates=candidates))
    if cancel.is_set():
        raise ValueError('已取消音位生成。')
    return dict(preset_id=preset['id'], transcript=transcript.strip(), words=words, sequence=sequence,
                source='MFA G2P · ' + Path(model).name)


def choose_readings(result: dict, choices: dict) -> tuple[list[dict], list[str], list[str]]:
    if not isinstance(choices, dict):
        raise ValueError('请选择每个词的读音候选。')
    selected = {}
    for item in result['words']:
        index = choices.get(item['word'], 0 if len(item['candidates']) == 1 else None)
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(item['candidates']):
            raise ValueError('请选择有效读音候选：' + item['word'])
        selected[item['word']] = item['candidates'][index]
    if set(choices) - set(selected):
        raise ValueError('读音候选包含不属于本次文本的词。')
    readings, ipa, raw = [], [], []
    for word in result['sequence']:
        candidate = selected[word]
        readings.append(dict(word=word, **candidate))
        ipa.extend(candidate['ipa'])
        raw.extend(candidate['raw'])
    return readings, ipa, raw
