"""Probe tests. Fixtures are synthetic command results; nothing here installs or aligns."""
from __future__ import annotations

from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest

PROBE_PATH = Path(__file__).resolve().parents[1]/'mfa_alignment_probe.py'
spec = importlib.util.spec_from_file_location('mfa_probe', PROBE_PATH)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)

CHILD_OK = dict(python='3.13.0', executable=r'C:\envs\aligner\python.exe',
                checks={name: dict(status='ok', detail='ok') for name, _ in probe.CHILD_CHECKS},
                temp=dict(directory=r'C:\Temp', ascii_only=True, writable=True), numba_cache_dir='')


def runner(version=None, child=CHILD_OK):
    """Fake run(): `version` for the launcher command, `child` for the probe command."""
    calls = []

    def run(command, timeout):
        calls.append(command)
        if command[-1] == '--child':
            if isinstance(child, dict) and 'stdout' in child:
                return child
            if isinstance(child, dict):
                return dict(status='ok', detail='ok', stdout=json.dumps(child))
            return dict(status='error', detail=str(child), stdout='')
        return version or dict(status='ok', detail='3.4.2', stdout='3.4.2')
    run.calls = calls
    return run


class LauncherTests(unittest.TestCase):
    def test_conda_takes_precedence_over_executable(self):
        launcher = probe.resolve_launcher(r'C:\envs\aligner\Scripts\mfa.exe', r'C:\conda.exe', 'aligner')
        self.assertEqual(launcher['kind'], 'conda')
        self.assertEqual(launcher['command'], [r'C:\conda.exe', 'run', '-n', 'aligner', 'mfa'])
        self.assertEqual(launcher['python'], [r'C:\conda.exe', 'run', '-n', 'aligner', 'python'])
        self.assertEqual(launcher['environment'], 'aligner')

    def test_conda_needs_both_executable_and_environment(self):
        self.assertEqual(probe.resolve_launcher('mfa.exe', r'C:\conda.exe', '')['kind'], 'direct')
        self.assertEqual(probe.resolve_launcher('mfa.exe', '', 'aligner')['kind'], 'direct')

    def test_direct_launcher_finds_env_python_in_scripts_or_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); scripts = root/'Scripts'; scripts.mkdir()
            executable = scripts/'mfa.exe'; executable.write_bytes(b'')
            (root/'python.exe').write_bytes(b'')          # conda keeps python.exe in the env root
            launcher = probe.resolve_launcher(str(executable), '', '')
            self.assertEqual(launcher['kind'], 'direct')
            self.assertEqual(launcher['python'], [str(root/'python.exe')])
            self.assertEqual(launcher['environment'], root.name)
            (root/'python.exe').unlink()
            (scripts/'python.exe').write_bytes(b'')
            self.assertEqual(probe.resolve_launcher(str(executable), '', '')['python'], [str(scripts/'python.exe')])

    def test_bat_launcher_is_run_through_the_shell(self):
        with tempfile.TemporaryDirectory() as directory:
            scripts = Path(directory)/'Scripts'; scripts.mkdir()
            launcher_file = scripts/'mfa.bat'; launcher_file.write_text('@echo 3.4.2\n', encoding='utf-8')
            launcher = probe.resolve_launcher(str(launcher_file), '', '')
            shell = [probe.os.environ.get('COMSPEC', 'cmd.exe'), '/d', '/s', '/c']
            self.assertEqual(launcher['command'][:4], shell)
            self.assertEqual(launcher['command'][-1], str(launcher_file))
            conda = probe.resolve_launcher('', str(launcher_file), 'aligner')
            self.assertEqual(conda['command'][:4], shell)
            self.assertEqual(conda['command'][5:7], ['run', '-n'])
            self.assertEqual(conda['command'][-1], 'mfa')

    def test_nothing_configured_is_not_ready(self):
        launcher = probe.resolve_launcher('', '', '')
        report = probe.aggregate(launcher, dict(status='error', detail='未配置'), None, '未配置 MFA 启动方式', [])
        self.assertEqual(launcher['kind'], 'none')
        self.assertFalse(report['ready'])
        self.assertIn('launcher', report['blocking'])


class AggregateTests(unittest.TestCase):
    def launcher(self, kind='conda'):
        return probe.resolve_launcher('', r'C:\conda.exe', 'aligner') if kind == 'conda' else \
            dict(command=['mfa'], python=['python'], label='mfa', environment='aligner', kind=kind)

    def test_all_ok_is_ready(self):
        files = [probe.file_check(__file__, 'dictionary')]
        report = probe.aggregate(self.launcher(), dict(status='ok', detail='3.4.2'), CHILD_OK, '', files)
        self.assertTrue(report['ready'])
        self.assertEqual(report['blocking'], [])
        self.assertIn('3.4.2', report['summary'])
        self.assertEqual(report['schema'], probe.SCHEMA)

    def test_first_blocking_item_drives_the_summary(self):
        child = json.loads(json.dumps(CHILD_OK))
        child['checks']['openfst'] = dict(status='error', detail="RuntimeError: fstcompile 不在 PATH")
        report = probe.aggregate(self.launcher(), dict(status='ok', detail='3.4.2'), child, '', [])
        self.assertFalse(report['ready'])
        self.assertEqual(report['blocking'], ['openfst'])
        self.assertIn('fstcompile', report['summary'])
        self.assertIn('conda run -n aligner mfa', [c['repair'] for c in report['checks'] if c['id'] == 'openfst'][0])

    def test_direct_launcher_failure_gets_the_activation_repair(self):
        report = probe.aggregate(self.launcher('direct'), dict(status='error', detail="OSError: cannot load library 'libsndfile.dll'"),
                                 None, '未解析到 MFA 环境的 python.exe', [])
        launcher = [item for item in report['checks'] if item['id'] == 'launcher'][0]
        self.assertEqual(launcher['status'], 'error')
        self.assertIn('不会激活该环境', launcher['repair'])
        self.assertIn('libsndfile', launcher['detail'])

    def test_unwritable_temp_blocks_and_is_reported(self):
        child = json.loads(json.dumps(CHILD_OK))
        child['temp'] = dict(directory=r'C:\Temp', ascii_only=True, writable=False, error='PermissionError: denied')
        report = probe.aggregate(self.launcher(), dict(status='ok', detail='3.4.2'), child, '', [])
        self.assertFalse(report['ready'])
        self.assertEqual(report['blocking'], ['temp'])
        self.assertIn('PermissionError', [c['detail'] for c in report['checks'] if c['id'] == 'temp'][0])

    def test_non_ascii_temp_is_flagged_even_when_writable(self):
        child = json.loads(json.dumps(CHILD_OK))
        child['temp'] = dict(directory='D:\\工作\\tmp', ascii_only=False, writable=True)
        report = probe.aggregate(self.launcher(), dict(status='ok', detail='3.4.2'), child, '', [])
        self.assertFalse(report['ready'])
        self.assertEqual(report['blocking'], ['ascii_temp'])
        self.assertIn('非 ASCII', [c['repair'] for c in report['checks'] if c['id'] == 'ascii_temp'][0])

    def test_missing_child_probe_is_not_silently_ready(self):
        report = probe.aggregate(self.launcher(), dict(status='ok', detail='3.4.2'), None, 'libsndfile 失败', [])
        self.assertFalse(report['ready'])
        self.assertEqual(report['blocking'], ['child'])


class FileCheckTests(unittest.TestCase):
    def test_missing_configuration_is_reported_per_file(self):
        self.assertEqual(probe.file_check('', 'dictionary')['status'], 'missing')
        model = probe.file_check('', 'acoustic', expect_directory=True)
        self.assertEqual(model['status'], 'missing')
        self.assertEqual(model['label'], '声学模型')

    def test_absent_and_empty_files_are_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); empty = root/'empty.dict'; empty.write_text('', encoding='utf-8')
            self.assertEqual(probe.file_check(str(root/'nope.dict'), 'dictionary')['status'], 'error')
            self.assertEqual(probe.file_check(str(empty), 'dictionary')['status'], 'error')

    def test_dictionary_counts_entries_and_model_accepts_zip_or_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); dictionary = root/'japanese_mfa.dict'
            dictionary.write_text('あなた\ta n a t a\n'.encode('utf-8').decode('utf-8')*3, encoding='utf-8')
            report = probe.file_check(str(dictionary), 'dictionary')
            self.assertEqual(report['status'], 'ok')
            self.assertIn('3 条', report['detail'])
            model = root/'model.zip'; model.write_bytes(b'x'*32)
            self.assertEqual(probe.file_check(str(model), 'acoustic', expect_directory=True)['status'], 'ok')
            folder = root/'model'; folder.mkdir()
            self.assertEqual(probe.file_check(str(folder), 'acoustic', expect_directory=True)['status'], 'error')


class ProbeFlowTests(unittest.TestCase):
    def test_full_probe_runs_launcher_then_child_and_never_leaks_a_crash(self):
        with tempfile.TemporaryDirectory() as directory:
            dictionary = Path(directory)/'japanese_mfa.dict'
            dictionary.write_text('あなた\ta n a t a\n', encoding='utf-8')
            model = Path(directory)/'japanese_mfa.zip'; model.write_bytes(b'x'*64)
            run = runner()
            report = probe.probe(r'C:\mfa.exe', r'C:\conda.exe', 'aligner', str(dictionary), str(model),
                                 runner=run, script=PROBE_PATH)
            self.assertTrue(report['ready'], report['summary'])
            self.assertEqual(run.calls[0], [r'C:\conda.exe', 'run', '-n', 'aligner', 'mfa', 'version'])
            self.assertEqual(run.calls[1][-1], '--child')
            self.assertEqual(run.calls[1][:5], [r'C:\conda.exe', 'run', '-n', 'aligner', 'python'])

    def test_missing_acoustic_model_alone_blocks_the_report(self):
        with tempfile.TemporaryDirectory() as directory:
            dictionary = Path(directory)/'japanese_mfa.dict'
            dictionary.write_text('あなた\ta n a t a\n', encoding='utf-8')
            report = probe.probe(r'C:\mfa.exe', r'C:\conda.exe', 'aligner', str(dictionary), '',
                                 runner=runner(), script=PROBE_PATH)
            self.assertFalse(report['ready'])
            self.assertEqual(report['blocking'], ['acoustic'])
            self.assertIn('语言与音位资源', [c['repair'] for c in report['checks'] if c['id'] == 'acoustic'][0])

    def test_unparsable_child_output_is_reported_not_ignored(self):
        report = probe.probe(r'C:\mfa.exe', r'C:\conda.exe', 'aligner', '', '',
                             runner=runner(child=dict(status='ok', detail='ok', stdout='not json')), script=PROBE_PATH)
        self.assertFalse(report['ready'])
        self.assertIn('无法解析', [c['detail'] for c in report['checks'] if c['id'] == 'child'][0])

    def test_probe_reports_launcher_failure_without_running_the_child(self):
        run = runner(version=dict(status='error', detail='not found', stdout=''))
        report = probe.probe(r'C:\mfa.exe', r'C:\conda.exe', 'aligner', '', '', runner=run, script=PROBE_PATH)
        self.assertFalse(report['ready'])
        self.assertIn('launcher', report['blocking'])


class ChildAndCliTests(unittest.TestCase):
    def test_child_mode_prints_ascii_json_with_every_check(self):
        output = io.StringIO()
        with redirect_stdout(output):
            code = probe.main(['--child'])
        self.assertEqual(code, 0)
        payload = json.loads(output.getvalue())
        self.assertEqual(output.getvalue().strip().isascii(), True)
        self.assertEqual(set(payload['checks']), {name for name, _ in probe.CHILD_CHECKS})
        self.assertIn('writable', payload['temp'])
        self.assertIn('ascii_only', payload['temp'])

    def test_cli_without_configuration_exits_one_with_a_reason(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)/'report.json'
            with redirect_stdout(io.StringIO()):
                code = probe.main(['--output', str(output)])
            report = json.loads(output.read_text(encoding='utf-8'))
            self.assertEqual(code, 1)
            self.assertFalse(report['ready'])
            self.assertIn('launcher', report['blocking'])

    def test_cli_refuses_to_overwrite_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)/'report.json'; output.write_text('keep', encoding='utf-8')
            with redirect_stdout(io.StringIO()), self.assertRaises(SystemExit):
                probe.main(['--conda-executable', 'conda', '--conda-environment', 'aligner',
                            '--output', str(output)])
            self.assertEqual(output.read_text(encoding='utf-8'), 'keep')


if __name__ == '__main__':
    unittest.main()
