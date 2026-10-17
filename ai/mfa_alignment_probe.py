"""MFA alignment environment probe (stdlib only, never installs or edits anything).

The path/dependency dialog calls this before promising that pronunciation alignment
works: `mfa --version` succeeds even when the environment cannot align a single word.

Two modes:
  * orchestrator (default): resolves the launcher, runs the in-environment child probe,
    checks the configured dictionary/acoustic model, prints one JSON report.
  * `--child`: runs inside the MFA environment and reports its own importability.

Exit codes: 0 ready, 1 not ready/unavailable, 2 usage or input error.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

SCHEMA = 'mfa-alignment-probe-v1'
CHILD_TIMEOUT = 180

# Each child check returns a short detail string or raises; the orchestrator turns
# failures into a status plus a repair hint so the dialog can explain the fix.
CHILD_CHECKS = (
    ('soundfile', 'libsndfile 音频读写'),
    ('openfst', 'openfst（fstcompile）'),
    ('kalpy', 'kalpy/kaldi 绑定'),
    ('librosa', 'librosa + numba 缓存'),
    ('mfa', 'Montreal Forced Aligner'),
)

REPAIRS = {
    'launcher': '确认 MFA 可执行文件路径，或填写 conda 可执行文件与环境名后改用 conda run 启动。',
    'direct': '直接调用 Scripts\\mfa.exe 不会激活该环境：Library\\bin 不在 PATH，libsndfile 与 fstcompile 都会缺失。请填写 conda 可执行文件与环境名（conda run），或先激活环境再启动。',
    'soundfile': '该环境缺少 libsndfile 载荷：conda install -n {env} -c conda-forge libsndfile，或 pip install --force-reinstall soundfile。',
    'openfst': 'openfst 不在 PATH：改用 conda run -n {env} mfa（激活环境后 Library\\bin 才可用），或在对话中直接选择 Scripts\\mfa.exe 时补上该环境的 Library\\bin。',
    'kalpy': '缺少 kalpy/kaldi 绑定：在该环境执行 conda install -n {env} -c conda-forge kalpy kaldi。',
    'librosa': 'librosa 无法导入，通常是 numba 不能写缓存：把 NUMBA_CACHE_DIR 指到可写目录，或重装该环境的 librosa/numba。',
    'mfa': '该环境没有 Montreal Forced Aligner：conda install -n {env} -c conda-forge montreal-forced-aligner。',
    'temp': '临时目录不可写：把 TEMP/TMP 指到可写目录后重试（MFA 需要可写的临时目录）。',
    'ascii_temp': '临时目录路径含非 ASCII 字符时 MFA 的 FST 层会写失败：把 TEMP/TMP 指到纯 ASCII 路径（例如 C:\\mfa-temp）后重试。',
    'dictionary': '未配置或找不到 MFA 词典：在“语言与音位资源”中选择该语言的词典文件（如 japanese_mfa.dict）。',
    'acoustic': '未配置或找不到声学模型：在“语言与音位资源”中选择对应模型目录或 zip（如 japanese_mfa.zip）。',
    'child': '无法在该环境内执行 Python 探针：确认环境里有可用的 python.exe，或改用 conda run。',
}


def child_report():
    """Runs inside the MFA environment; never raises for a missing dependency."""
    report = dict(python=sys.version.split()[0], executable=sys.executable, checks={})

    def soundfile():
        import soundfile
        return f'{soundfile.__version__} / libsndfile {soundfile.__libsndfile_version__}'

    def openfst():
        import shutil
        found = shutil.which('fstcompile')
        if not found:
            raise RuntimeError('fstcompile 不在 PATH')
        import pywrapfst  # noqa: F401
        return found

    def kalpy():
        import kalpy
        return str(getattr(kalpy, '__version__', 'installed'))

    def librosa():
        import librosa
        return str(getattr(librosa, '__version__', 'installed'))

    def mfa():
        import importlib.metadata
        return importlib.metadata.version('montreal-forced-aligner')

    checks = dict(soundfile=soundfile, openfst=openfst, kalpy=kalpy, librosa=librosa, mfa=mfa)
    for name, _label in CHILD_CHECKS:
        try:
            report['checks'][name] = dict(status='ok', detail=str(checks[name]()))
        except Exception as error:  # missing dependency is evidence, not a crash
            report['checks'][name] = dict(status='error', detail=f'{type(error).__name__}: {error}'[:400])
    import tempfile
    directory = tempfile.gettempdir()
    report['temp'] = dict(directory=directory, ascii_only=directory.isascii())
    try:
        with tempfile.NamedTemporaryFile(delete=True) as handle:
            handle.write(b'probe')
        report['temp']['writable'] = True
    except OSError as error:
        report['temp']['writable'] = False
        report['temp']['error'] = f'{type(error).__name__}: {error}'
    report['numba_cache_dir'] = os.environ.get('NUMBA_CACHE_DIR', '')
    return report


def command_for(executable):
    """`.bat`/`.cmd` launchers need a shell; the dialog accepts them as MFA paths."""
    if Path(executable).suffix.lower() in ('.bat', '.cmd'):
        return [os.environ.get('COMSPEC', 'cmd.exe'), '/d', '/s', '/c', executable]
    return [executable]


def resolve_launcher(mfa_executable='', conda_executable='', conda_environment=''):
    """`conda run -n <env> mfa` when a conda env is configured, else the executable."""
    conda_executable = (conda_executable or '').strip()
    conda_environment = (conda_environment or '').strip()
    mfa_executable = (mfa_executable or '').strip()
    if conda_executable and conda_environment:
        prefix = command_for(conda_executable) + ['run', '-n', conda_environment]
        return dict(command=prefix + ['mfa'], python=prefix + ['python'],
                    label=f'conda run -n {conda_environment} mfa', environment=conda_environment,
                    kind='conda')
    if not mfa_executable:
        return dict(command=[], python=[], label='', environment='', kind='none')
    path = Path(mfa_executable)
    # A direct Scripts\mfa.exe call does not activate the environment, so neither
    # fstcompile (Library\bin) nor libsndfile is reachable; that is reported with its
    # own repair hint instead of hiding it. Conda envs may keep python.exe either in
    # Scripts or in the environment root.
    candidates = (path.parent/'python.exe', path.parent.parent/'python.exe')
    python = next((item for item in candidates if item.is_file()), None)
    return dict(command=command_for(mfa_executable), python=command_for(str(python)) if python else [],
                label=mfa_executable, environment=path.parent.parent.name if path.parent.name.lower() == 'scripts' else '',
                kind='direct')


def file_check(path, label, expect_directory=False):
    """Existence/readability only: never says whether a model suits the language."""
    value = (path or '').strip()
    if not value:
        return dict(id=label, label='MFA 词典' if label == 'dictionary' else '声学模型',
                    status='missing', detail='未配置路径', repair=REPAIRS[label].format(env='<环境名>'))
    target = Path(value)
    if expect_directory and target.is_dir():
        count = sum(1 for _ in target.iterdir())
        return dict(id=label, label='声学模型', status='ok' if count else 'error',
                    detail=f'目录，{count} 项', repair='' if count else REPAIRS[label].format(env='<环境名>'))
    if not target.is_file():
        return dict(id=label, label='MFA 词典' if label == 'dictionary' else '声学模型', status='error',
                    detail='文件不存在', repair=REPAIRS[label].format(env='<环境名>'))
    size = target.stat().st_size
    detail = f'{target.suffix or "文件"}，{size} 字节'
    if label == 'dictionary':
        try:
            entries = 0
            with target.open('r', encoding='utf-8', errors='replace') as handle:
                for entries, _line in enumerate(handle, start=1):
                    if entries >= 100000:
                        break
            detail += f'，约 {entries} 条'
        except OSError as error:
            return dict(id=label, label='MFA 词典', status='error', detail=f'无法读取：{error}',
                        repair=REPAIRS[label].format(env='<环境名>'))
    return dict(id=label, label='MFA 词典' if label == 'dictionary' else '声学模型',
                status='ok' if size else 'error', detail=detail,
                repair='' if size else REPAIRS[label].format(env='<环境名>'))


def aggregate(launcher, version, child, child_error, files):
    """Turns raw observations into per-item status plus one blocking summary."""
    checks = []
    environment = launcher.get('environment') or '<环境名>'

    def repair(name):
        return REPAIRS[name].format(env=environment)

    launcher_repair = repair('launcher') if launcher.get('kind') == 'conda' else repair('direct')
    if launcher['kind'] == 'none':
        checks.append(dict(id='launcher', label='MFA 启动方式', status='missing',
                           detail='未配置 MFA 可执行文件或 conda 环境', repair=repair('launcher')))
    elif version['status'] == 'ok':
        checks.append(dict(id='launcher', label='MFA 启动方式', status='ok',
                           detail=f"{launcher['label']} · {version['detail']}", repair=''))
    else:
        checks.append(dict(id='launcher', label='MFA 启动方式', status='error',
                           detail=version['detail'], repair=launcher_repair))

    if child is None:
        checks.append(dict(id='child', label='环境内依赖探针', status='error',
                           detail=child_error or '未运行环境内探针', repair=repair('child')))
    else:
        for name, label in CHILD_CHECKS:
            state = (child.get('checks') or {}).get(name) or dict(status='error', detail='未报告')
            checks.append(dict(id=name, label=label, status=state['status'], detail=state['detail'],
                               repair='' if state['status'] == 'ok' else repair(name)))
        temp = child.get('temp') or {}
        if temp.get('writable'):
            checks.append(dict(id='temp', label='临时目录可写', status='ok',
                               detail=temp.get('directory', ''), repair=''))
        else:
            checks.append(dict(id='temp', label='临时目录可写', status='error',
                               detail=temp.get('error') or temp.get('directory', '不可写'), repair=repair('temp')))
        if temp.get('ascii_only') is False:
            checks.append(dict(id='ascii_temp', label='临时目录为 ASCII 路径', status='error',
                               detail=temp.get('directory', ''), repair=repair('ascii_temp')))
    checks.extend(files)
    blocking = [item for item in checks if item['status'] != 'ok']
    ready = not blocking
    summary = ('对齐环境可用：' + (version['detail'] if version['status'] == 'ok' else '版本未知')
               if ready else
               f"对齐环境不完整（{len(blocking)} 项）：{blocking[0]['label']} — {blocking[0]['detail']}")
    return dict(schema=SCHEMA, ready=ready, summary=summary, launcher=launcher['label'],
                environment=environment, version=version['detail'] if version['status'] == 'ok' else '',
                python=(child or {}).get('executable', ''), checks=checks,
                blocking=[item['id'] for item in blocking])


def run_command(command, timeout):
    """Never raises; always keeps the full stdout so the child JSON can be parsed."""
    try:
        completed = subprocess.run(command, capture_output=True, encoding='utf-8',
                                   errors='replace', timeout=timeout)
    except (OSError, subprocess.SubprocessError) as error:
        return dict(status='error', detail=f'{type(error).__name__}: {error}', stdout='')
    stdout = (completed.stdout or '').strip()
    stderr = (completed.stderr or '').strip()
    if completed.returncode != 0:
        last = (stderr or stdout).splitlines()
        return dict(status='error', detail=last[-1][:400] if last else f'退出码 {completed.returncode}', stdout=stdout)
    return dict(status='ok', detail=stdout.splitlines()[-1][:200] if stdout else 'ok', stdout=stdout)


def probe(mfa_executable='', conda_executable='', conda_environment='', dictionary='',
          acoustic_model='', timeout=CHILD_TIMEOUT, script=None, runner=None):
    """Returns the report dict. Never raises for a broken environment."""
    run = runner or run_command
    launcher = resolve_launcher(mfa_executable, conda_executable, conda_environment)
    version = dict(status='error', detail='未配置')
    if launcher['kind'] != 'none':
        version = run(launcher['command'] + ['version'], timeout)
    child, child_error = None, ''
    if launcher['python']:
        target = str(script or Path(__file__).resolve())
        result = run(launcher['python'] + ['-I', target, '--child'], timeout)
        if result['status'] == 'ok':
            try:
                child = json.loads(result.get('stdout') or result['detail'])
            except ValueError as error:
                child_error = f'探针输出无法解析：{error}'
        else:
            child_error = result['detail']
    else:
        child_error = '未解析到 MFA 环境的 python.exe'
    files = [file_check(dictionary, 'dictionary'),
             file_check(acoustic_model, 'acoustic', expect_directory=True)]
    return aggregate(launcher, version, child, child_error, files)


def main(argv=None):
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--child', action='store_true', help='run inside the MFA environment')
    parser.add_argument('--mfa-executable', default='')
    parser.add_argument('--conda-executable', default='')
    parser.add_argument('--conda-environment', default='')
    parser.add_argument('--dictionary', default='')
    parser.add_argument('--acoustic-model', default='')
    parser.add_argument('--timeout', type=int, default=CHILD_TIMEOUT)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    if args.child:
        # ASCII-only JSON: the launcher (conda run, redirected console) may re-encode
        # the child's stdout, and a corrupted payload must not look like a missing tool.
        print(json.dumps(child_report(), ensure_ascii=True))
        return 0
    if args.output and args.output.exists():
        parser.error('output exists; refusing to overwrite evidence')
    if not any((args.mfa_executable, args.conda_executable and args.conda_environment)):
        report = aggregate(resolve_launcher('', '', ''), dict(status='error', detail='未配置'), None,
                           '未配置 MFA 启动方式', [file_check('', 'dictionary'), file_check('', 'acoustic', True)])
    else:
        report = probe(args.mfa_executable, args.conda_executable, args.conda_environment,
                       args.dictionary, args.acoustic_model, args.timeout)
    payload = json.dumps(report, ensure_ascii=False, indent=2) + '\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding='utf-8')
    print(payload, end='')
    return 0 if report['ready'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
