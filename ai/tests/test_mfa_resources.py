import json
import os
import sys
import tempfile
import types
import unittest
import zipfile
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from unittest.mock import Mock, patch

from praat_ai.config import load_config, pronunciation_presets
from praat_ai.mfa_resources import (MfaResourceService, alignment_probe, legacy_resource_id,
                                   parse_dictionary, read_model_metadata, dictionary_inventory,
                                   dictionary_pronunciations)


class MfaResourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / 'config.json'
        self.dictionary = self.root / '中文 British filename.dict'
        self.dictionary.write_text('hello HH AH0 L OW1\n', encoding='utf-8-sig')
        self.launcher_path = self.root / 'fixture mfa.exe'
        self.launcher_path.write_bytes(b'not executed')
        self.launcher = dict(executable=str(self.launcher_path), conda_executable='', conda_environment='')
        self.raw = dict(api={'api_key': 'fixture-only'}, custom={'keep': 17},
                        alignment={'mfa': {'dictionary_path': str(self.dictionary), 'beam': 31}},
                        modern={'preferences': {'theme': 'dark'}})
        self.write(self.raw)
        self.service = MfaResourceService(self.path)

    def write(self, raw):
        self.path.write_text(json.dumps(raw, ensure_ascii=False), encoding='utf-8')

    def read(self):
        return json.loads(self.path.read_text(encoding='utf-8'))

    def model(self, name='model.zip', **metadata):
        path = self.root / name
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('nested/meta.json', json.dumps(metadata))
        return path

    def imported(self, kind, path):
        data = self.service.add(kind, [path, path])
        return next(r['id'] for r in data['resources'][kind] if r['path'] == str(path))

    def preset(self, **changes):
        preset = deepcopy(self.service.list()['presets'][0])
        preset.update(changes)
        return preset

    def g2p(self, **metadata):
        defaults = dict(model_type='g2p', language='english', phones=['HH', 'AH0', 'L', 'OW1'])
        defaults.update(metadata)
        return self.imported('g2p', self.model(**defaults))

    def test_legacy_list_is_read_only_without_default_accent_or_fake_resources(self):
        before = self.path.read_bytes()
        with patch('subprocess.run', side_effect=AssertionError('must not launch')):
            data = self.service.rpc('mfa.list', {})
        self.assertEqual(before, self.path.read_bytes())
        self.assertEqual(data['default_preset'], '')
        self.assertEqual(data['resources']['g2p'], [])
        self.assertEqual(data['resources']['acoustic'], [])
        self.assertEqual(data['theme'], 'dark')
        self.assertEqual(data['resources']['dictionary'][0]['id'], legacy_resource_id('dictionary', self.dictionary))
        self.assertEqual(data['resources']['dictionary'][0]['used_by'], [])
        self.assertEqual([p['name'] for p in data['presets']], ['英语（英式）', '英语（美式）', '日语（标准语）'])
        self.assertIsNone(data['presets'][0]['rule_id'])
        self.assertEqual(data['presets'][1]['rule_id'], 'en-US-general-v1')
        for p in data['presets']:
            self.assertEqual(p['g2p'], '')
            self.assertEqual(p['dictionary'], '')
            self.assertFalse(p['status']['can_generate'])

    def test_config_defaults_and_dataclass_serialization_preserve_namespace(self):
        old = load_config(self.path)
        self.assertEqual(old.pronunciation.default_preset, '')
        self.assertEqual(old.pronunciation.presets, pronunciation_presets())
        identity = self.g2p()
        preset = self.preset(g2p=identity, phone_set='arpabet', confirmed=True)
        self.service.save(preset, self.launcher, True)
        config = load_config(self.path)
        self.assertEqual(config.to_dict()['pronunciation'], asdict(config.pronunciation))
        self.assertEqual(config.pronunciation.presets[0]['g2p'], identity)
        self.assertEqual(config.pronunciation.launcher, self.launcher)

    def test_import_is_separate_deduplicated_and_does_not_bind_any_preset(self):
        identity = self.g2p()
        data = self.service.list()
        self.assertEqual(len(data['resources']['g2p']), 1)
        self.assertEqual(data['presets'][0]['g2p'], '')
        self.assertEqual(self.read()['alignment'], self.raw['alignment'])
        self.assertEqual(self.service.add('g2p', [self.root / 'model.zip'])['resources']['g2p'][0]['id'], identity)

    def test_save_partial_and_preserve_latest_raw_unknown_fields(self):
        raw = self.read()
        raw['custom']['keep'] = 29
        raw['pronunciation'] = {'future': {'keep': True}, 'launcher': {'future_launcher': 42},
                                'presets': [{**pronunciation_presets()[0], 'future_preset': 'retained'}]}
        self.write(raw)
        data = self.service.rpc('mfa.save', dict(preset=self.preset(), launcher=self.launcher, make_default=True))
        saved = self.read()
        self.assertEqual(saved['custom']['keep'], 29)
        self.assertEqual(saved['api'], self.raw['api'])
        self.assertEqual(saved['alignment'], self.raw['alignment'])
        self.assertEqual(saved['pronunciation']['future'], {'keep': True})
        self.assertEqual(saved['pronunciation']['launcher']['future_launcher'], 42)
        self.assertEqual(saved['pronunciation']['presets'][0]['future_preset'], 'retained')
        self.assertEqual(data['default_preset'], 'en-GB')
        self.assertFalse(data['presets'][0]['status']['can_generate'])

    def test_generation_requires_model_mapping_and_launcher_without_a_manual_declaration(self):
        preset = self.preset(g2p=self.g2p(), phone_set='arpabet')
        for legacy_value in (None, False, True, 'true'):
            with self.subTest(legacy_value=legacy_value):
                self.assertTrue(self.service.check(dict(preset, confirmed=legacy_value), self.launcher)['can_generate'])
        status = self.service.check(preset, self.launcher)
        self.assertTrue(status['can_generate'])
        self.assertEqual(status['dictionary'], '未配置')
        self.assertEqual(status['acoustic'], '未配置')
        self.assertEqual(status['compatibility'], '未验证')
        with patch('praat_ai.mfa_resources.shutil.which', return_value=None):
            self.assertFalse(self.service.check(preset, dict(executable='not-found'))['can_generate'])
        with patch('praat_ai.mfa_resources.shutil.which', return_value='somewhere'):
            self.assertTrue(self.service.check(preset, dict(executable='mfa'))['can_generate'])
        self.assertFalse(self.service.check(preset, dict(executable=str(self.launcher_path),
                                                       conda_environment='missing-conda'))['can_generate'])

    def test_custom_mapping_known_inventory_must_be_complete(self):
        preset = self.preset(g2p=self.g2p(), confirmed=True, phone_set='custom', phone_map={'HH': 'h'})
        self.assertFalse(self.service.check(preset, self.launcher)['can_generate'])
        preset['phone_map'].update(AH0='ə', L='l', OW1='oʊ')
        self.assertTrue(self.service.check(preset, self.launcher)['can_generate'])
        preset['phone_map']['HH'] = 'h ə'
        with self.assertRaises(ValueError):
            self.service.check(preset, self.launcher)

    def test_unknown_metadata_is_unverified_even_when_file_exists(self):
        path = self.root / 'unproven British.zip'
        path.write_bytes(b'marker, not a real model')
        preset = self.preset(g2p=self.imported('g2p', path), confirmed=True, phone_set='ipa')
        status = self.service.check(preset, self.launcher)
        self.assertEqual(status['compatibility'], '未验证')
        self.assertIn('未验证', status['g2p'])
        self.assertTrue(status['can_generate'])  # Runtime still validates actual output/model.
        self.assertEqual(preset['variant'], 'en-GB')

    def test_known_g2p_and_dictionary_coverage_conflicts_reject_save(self):
        g2p = self.g2p()
        acoustic = self.imported('acoustic', self.model('acoustic.zip', model_type='acoustic',
                                                       language='en', phones=['HH', 'L']))
        dictionary = self.service.list()['resources']['dictionary'][0]['id']
        preset = self.preset(g2p=g2p, dictionary=dictionary, acoustic=acoustic,
                             phone_set='arpabet', confirmed=True)
        before = self.path.read_bytes()
        status = self.service.check(preset, self.launcher)
        self.assertEqual(status['compatibility'], '冲突')
        self.assertFalse(status['can_generate'])
        self.assertTrue(any('g2p 音位' in issue for issue in status['issues']))
        self.assertTrue(any('dictionary 音位' in issue for issue in status['issues']))
        with self.assertRaises(ValueError):
            self.service.save(preset, self.launcher)
        self.assertEqual(before, self.path.read_bytes())

    def test_static_coverage_is_not_claimed_as_actual_validation(self):
        g2p = self.g2p()
        acoustic = self.imported('acoustic', self.model('acoustic.zip', model_type='acoustic',
            language='en-US', phones=['HH', 'AH0', 'L', 'OW1', 'sil']))
        preset = self.preset(g2p=g2p, acoustic=acoustic,
            dictionary=self.service.list()['resources']['dictionary'][0]['id'], phone_set='arpabet', confirmed=True)
        self.assertEqual(self.service.check(preset, self.launcher)['compatibility'], '静态检查通过')
        self.service.save(preset, self.launcher)

    def test_model_type_and_language_conflicts(self):
        for metadata in (dict(model_type='acoustic', language='en'), dict(model_type='g2p', language='japanese')):
            with self.subTest(metadata=metadata):
                path = self.model(str(len(self.service.list()['resources']['g2p'])) + '.zip', phones=['a'], **metadata)
                preset = self.preset(g2p=self.imported('g2p', path), phone_set='ipa', confirmed=True)
                self.assertEqual(self.service.check(preset, self.launcher)['compatibility'], '冲突')
                with self.assertRaises(ValueError):
                    self.service.save(preset, self.launcher)

    def test_missing_files_allow_partial_save_but_block_generation(self):
        identity = self.g2p()
        (self.root / 'model.zip').unlink()
        data = self.service.save(self.preset(g2p=identity, phone_set='arpabet', confirmed=True), self.launcher)
        self.assertFalse(data['resources']['g2p'][0]['exists'])
        self.assertEqual(data['presets'][0]['status']['g2p'], '文件缺失')
        self.assertFalse(data['presets'][0]['status']['can_generate'])

    def test_remove_preserves_disk_and_dangling_ids_even_after_same_path_import(self):
        identity = self.g2p()
        self.service.save(self.preset(g2p=identity, phone_set='arpabet', confirmed=True), self.launcher)
        data = self.service.rpc('mfa.remove', dict(kind='g2p', id=identity))
        self.assertTrue((self.root / 'model.zip').is_file())
        self.assertEqual(data['presets'][0]['g2p'], identity)
        self.assertEqual(data['presets'][0]['status']['g2p'], '登记缺失')
        self.assertEqual(self.service.resolve('en-GB')['paths']['g2p'], '')
        new_id = self.imported('g2p', self.root / 'model.zip')
        self.assertNotEqual(identity, new_id)
        self.assertFalse(self.service.list()['presets'][0]['status']['can_generate'])
        self.assertEqual(self.service.list()['presets'][1]['g2p'], '')

    def test_remove_legacy_does_not_mutate_active_global_and_never_restores_reference(self):
        identity = self.service.list()['resources']['dictionary'][0]['id']
        self.service.save(self.preset(dictionary=identity), self.launcher)
        self.assertEqual(self.service.list()['resources']['dictionary'][0]['used_by'], ['en-GB'])
        self.service.remove('dictionary', identity)
        self.assertEqual(self.read()['alignment'], self.raw['alignment'])
        self.assertEqual(self.service.list()['resources']['dictionary'], [])
        self.imported('dictionary', self.dictionary)
        self.assertEqual(self.service.list()['presets'][0]['status']['dictionary'], '登记缺失')

    def test_identities_and_unregistered_frontend_paths_are_rejected(self):
        for changes in (dict(id='custom'), dict(name='other'), dict(language='ja'),
                        dict(variant='en-US'), dict(rule_id='en-US-general-v1'), dict(g2p=str(self.dictionary))):
            before = self.path.read_bytes()
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.service.save(self.preset(**changes), self.launcher)
            self.assertEqual(before, self.path.read_bytes())
        with self.assertRaises(ValueError):
            self.service.resolve(str(self.dictionary))

    def test_fingerprint_tracks_config_content_and_resource_stat_not_unrelated_keys(self):
        identity = self.g2p()
        preset = self.preset(g2p=identity, phone_set='arpabet', confirmed=True)
        self.service.save(preset, self.launcher)
        original = self.service.resolve('en-GB')
        self.assertEqual(set(original), {'preset', 'launcher', 'paths', 'fingerprint'})
        self.assertEqual(original['paths']['g2p'], str(self.root / 'model.zip'))
        raw, stat = self.read(), self.path.stat()
        raw['custom']['keep'] = 100
        self.write(raw)
        self.assertEqual(original['fingerprint'], self.service.resolve('en-GB')['fingerprint'])
        raw['pronunciation']['presets'][0]['phone_map'] = {'HH': 'h'}
        self.write(raw)
        os.utime(self.path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        changed = self.service.resolve('en-GB')['fingerprint']
        self.assertNotEqual(original['fingerprint'], changed)
        model = self.root / 'model.zip'
        stat = model.stat()
        os.utime(model, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1000000000))
        self.assertNotEqual(changed, self.service.resolve('en-GB')['fingerprint'])
        changed = self.service.resolve('en-GB')['fingerprint']
        self.launcher_path.write_bytes(b'changed launcher executable')
        self.assertNotEqual(changed, self.service.resolve('en-GB')['fingerprint'])
        changed = self.service.resolve('en-GB')['fingerprint']
        raw['pronunciation']['launcher']['executable'] = 'different'
        self.write(raw)
        self.assertNotEqual(changed, self.service.resolve('en-GB')['fingerprint'])

    def test_native_pickers_import_only_selected_paths_and_launcher_is_not_saved(self):
        model = self.model()
        window, webview = Mock(), Mock()
        window.create_file_dialog.return_value = None
        before = self.path.read_bytes()
        with patch.dict(sys.modules, webview=webview):
            self.service.rpc('mfa.choose', {'kind': 'g2p', 'path': str(model)}, window)
            self.assertEqual(before, self.path.read_bytes())
            self.assertEqual(self.service.rpc('mfa.choose_launcher', {}, window), {'path': ''})
            window.create_file_dialog.return_value = [str(self.launcher_path)]
            self.assertEqual(self.service.rpc('mfa.choose_launcher', {}, window), {'path': str(self.launcher_path)})
            self.assertEqual(before, self.path.read_bytes())
            window.create_file_dialog.return_value = [str(model)]
            self.assertEqual(len(self.service.rpc('mfa.choose', {'kind': 'g2p'}, window)['resources']['g2p']), 1)
        self.assertEqual(self.read()['alignment'], self.raw['alignment'])

    def test_launcher_check_delegates_to_shared_runner_without_saving(self):
        module = types.ModuleType('praat_ai.mfa_g2p')
        module.check_launcher = Mock(return_value={'version': '3', 'help_order': 'input-model-output', 'status': 'ok'})
        before = self.path.read_bytes()
        with patch.dict(sys.modules, {'praat_ai.mfa_g2p': module}):
            result = self.service.rpc('mfa.check_launcher', {'launcher': self.launcher})
        module.check_launcher.assert_called_once_with(self.launcher)
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(before, self.path.read_bytes())

    def test_save_serializes_with_foreign_writer_and_merges_latest_config(self):
        import subprocess
        import time
        from praat_ai.service_lock import service_transition_lock
        code = ("import sys; from praat_ai.mfa_resources import MfaResourceService; "
                "from praat_ai.config import pronunciation_presets; "
                "print('ready', flush=True); "
                "MfaResourceService(sys.argv[1]).save(pronunciation_presets()[0], {}); "
                "print('done', flush=True)")
        child = None
        try:
            with service_transition_lock(Path(str(self.path) + '.lock')):
                child = subprocess.Popen([sys.executable, '-c', code, str(self.path)],
                                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                self.assertEqual(child.stdout.readline().strip(), 'ready')
                time.sleep(0.2)
                self.assertIsNone(child.poll(), 'resource writer ignored config.lock')
                raw = self.read()
                raw['custom']['keep'] = 123
                self.write(raw)
            output, error = child.communicate(timeout=15)
            self.assertEqual(child.returncode, 0, error)
            self.assertIn('done', output)
            self.assertEqual(self.read()['custom']['keep'], 123)
            self.assertEqual(self.read()['pronunciation']['presets'][0]['id'], 'en-GB')
        finally:
            if child is not None:
                if child.poll() is None:
                    child.kill()
                child.communicate()


    def test_atomic_save_failure_leaves_latest_config_and_cleans_temp(self):
        before = self.path.read_bytes()
        with patch('praat_ai.mfa_resources.os.replace', side_effect=OSError('fixture failure')):
            with self.assertRaises(OSError):
                self.service.save(self.preset(), self.launcher)
        self.assertEqual(before, self.path.read_bytes())
        self.assertEqual(list(self.root.glob('*.tmp')), [])

    def test_invalid_import_remove_and_unknown_rpc_are_lossless(self):
        before = self.path.read_bytes()
        for call in (lambda: self.service.add('g2p', [self.root / 'missing']),
                     lambda: self.service.add('bad-kind', []),
                     lambda: self.service.remove('g2p', 'unknown'),
                     lambda: self.service.rpc('mfa.exec', {'command': 'no'})):
            with self.assertRaises(ValueError):
                call()
        self.assertEqual(before, self.path.read_bytes())

    def test_zip_metadata_safe_json_yaml_unknown_and_size_bound(self):
        path = self.model(phones=['a'], model_type='g2p')
        self.assertEqual(read_model_metadata(path)['phones'], ['a'])
        yaml = self.root / 'yaml.zip'
        with zipfile.ZipFile(yaml, 'w') as archive:
            archive.writestr('../../outside.txt', 'must not extract')
            archive.writestr('meta.yaml', 'model_type: g2p\nlanguage: english\nphones:\n  - HH\n  - AH0\n')
        self.assertEqual(read_model_metadata(yaml)['phones'], ['HH', 'AH0'])
        self.assertFalse((self.root / 'outside.txt').exists())
        with zipfile.ZipFile(yaml, 'w') as archive:
            archive.writestr('meta.yaml', '!!python/object/apply:os.system [echo unsafe]')
        self.assertEqual(read_model_metadata(yaml), {})
        with zipfile.ZipFile(yaml, 'w') as archive:
            archive.writestr('meta.json', ' ' * (2 * 1024 * 1024 + 1))
        self.assertEqual(read_model_metadata(yaml), {})

    def test_dictionary_bom_unicode_multiple_probabilities_candidates_and_malformed(self):
        self.dictionary.write_text('# comment\nHELLO\t0.8 0.2 1.1 0.9 HH AH0 L OW1\n'
                                   'hello 0.2 HH EH L OW1\nhello HH AH0 L OW1\nipa t͡ʃ ã\n', encoding='utf-8-sig')
        parsed = parse_dictionary(self.dictionary)
        self.assertEqual(parsed['hello'], [['HH', 'AH0', 'L', 'OW1'], ['HH', 'EH', 'L', 'OW1']])
        self.assertEqual(parsed['ipa'], [['t͡ʃ', 'ã']])
        for row in ('hello', 'hello 0.8', 'hello 1.2 HH', 'hello -0.1 HH', 'hello HH 0.8',
                    'hello NaN HH', 'hello 0.1 0.2 0.3 0.4 0.5 HH', ''):
            with self.subTest(row=row):
                self.dictionary.write_text(row, encoding='utf-8')
                with self.assertRaises(ValueError):
                    parse_dictionary(self.dictionary)

    def test_dictionary_cache_is_isolated_and_invalidates_after_file_change(self):
        first = parse_dictionary(self.dictionary)
        first['hello'][0][0] = 'CHANGED'
        self.assertEqual(parse_dictionary(self.dictionary)['hello'][0][0], 'HH')
        self.dictionary.write_text('world W ER1 L D\n', encoding='utf-8')
        self.assertEqual(parse_dictionary(self.dictionary), {'world': [['W', 'ER1', 'L', 'D']]})

    def test_page_checks_cache_only_inventory_and_invalidate_on_dictionary_changes(self):
        preset = self.preset(dictionary=legacy_resource_id('dictionary', self.dictionary), phone_set='arpabet')
        self.service.save(preset, self.launcher)
        with patch('praat_ai.mfa_resources.parse_dictionary', side_effect=AssertionError('full dictionary allocated')):
            self.assertEqual(dictionary_inventory(self.dictionary), frozenset({'HH', 'AH0', 'L', 'OW1'}))
            self.assertIs(dictionary_inventory(self.dictionary), dictionary_inventory(self.dictionary))
            first = self.service.list()['presets'][0]['status']
            self.assertEqual(first['dictionary'], '静态检查通过')
            self.dictionary.write_text('hello HH\nextra UNKNOWN\n', encoding='utf-8')
            second = self.service.list()['presets'][0]['status']
            self.assertFalse(second['mapping_ready'])
            self.assertIn('UNKNOWN', ' '.join(second['issues']))

    def test_targeted_dictionary_lookup_keeps_candidates_and_validates_unrequested_rows(self):
        self.dictionary.write_text('Straße a\nOTHER b\nstraße i\nstraße a\n', encoding='utf-8')
        self.assertEqual(dictionary_pronunciations(self.dictionary, ['straße']), {'strasse': [['a'], ['i']]})
        self.assertEqual(dictionary_inventory(self.dictionary), frozenset({'a', 'b', 'i'}))
        self.dictionary.write_text('Straße a\nbroken 0.8\n', encoding='utf-8')
        for reader in (dictionary_inventory, lambda p: dictionary_pronunciations(p, ['straße'])):
            with self.assertRaisesRegex(ValueError, '第 2 行'):
                reader(self.dictionary)

    def test_custom_language_pairing_can_be_saved_selected_and_resolved(self):
        from praat_ai.mfa_resources import validate_preset
        g2p = self.g2p(language='german', phones=['a'], graphemes=list('straße'))
        preset = self.preset(id='custom-' + 'a' * 32, name='德语', language='de', variant='', rule_id=None,
                             g2p=g2p, dictionary='', acoustic='', phone_set='ipa', confirmed=True)
        data = self.service.save(preset, self.launcher, make_default=True)
        self.assertEqual(data['default_preset'], preset['id'])
        saved = next(p for p in data['presets'] if p['id'] == preset['id'])
        self.assertEqual(saved['language'], 'de')
        self.assertTrue(saved['status']['can_generate'], saved['status'])
        self.assertEqual(self.service.resolve(preset['id'])['preset']['name'], '德语')
        self.assertEqual(next(r for r in data['resources']['g2p'] if r['id'] == g2p)['used_by'], [preset['id']])
        for patch_value in ({'id':'unknown'}, {'language':''}, {'language':'bad/code'}, {'rule_id':'en-US-general-v1'}):
            with self.subTest(patch_value=patch_value), self.assertRaises(ValueError):
                validate_preset(dict(preset, **patch_value))

    def test_mapping_must_be_one_valid_ipa_unit(self):
        for value in ('hello', 'ab', 'HH', 'a,b'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.service.save(self.preset(phone_map={'X': value}), self.launcher)

    def test_unknown_language_and_declared_oov_mapping_do_not_create_false_conflicts(self):
        self.dictionary.write_text('hello HH AH0 L OW1\n[bracketed] spn\n', encoding='utf-8')
        g2p = self.g2p()
        acoustic = self.imported('acoustic', self.model('acoustic.zip', architecture='gmm-hmm', language='unknown',
                                phones=['HH', 'AH0', 'L', 'OW1'], phone_mapping={'spn': 2}, oov_phone='spn'))
        preset = self.preset(g2p=g2p, acoustic=acoustic, dictionary=legacy_resource_id('dictionary', self.dictionary),
                             phone_set='arpabet', confirmed=True)
        status = self.service.check(preset, self.launcher)
        self.assertEqual(status['compatibility'], '未验证')
        self.assertTrue(status['can_generate'])
        self.assertFalse(any('语言' in issue or '未被' in issue for issue in status['issues']))

    def test_known_ipa_or_supplemental_dictionary_mapping_gaps_block_readiness(self):
        g2p = self.g2p(phones=['HH'])
        self.assertFalse(self.service.check(self.preset(g2p=g2p, phone_set='ipa', confirmed=True), self.launcher)['can_generate'])
        self.dictionary.write_text('hello HH AH0 L OW1\nextra UNKNOWN\n', encoding='utf-8')
        preset = self.preset(g2p=g2p, dictionary=legacy_resource_id('dictionary', self.dictionary), phone_set='custom',
                             phone_map={'HH': 'h', 'AH0': 'ə', 'L': 'l', 'OW1': 'oʊ'}, confirmed=True)
        status = self.service.check(preset, self.launcher)
        self.assertFalse(status['can_generate'])
        self.assertIn('UNKNOWN', ' '.join(status['issues']))

    def test_default_preset_can_be_explicitly_unset_without_changing_another_default(self):
        preset = self.preset()
        self.service.save(preset, self.launcher, make_default=True)
        self.assertEqual(self.service.save(preset, self.launcher, make_default=False)['default_preset'], '')
        self.service.save(preset, self.launcher, make_default=True)
        other = self.service.list()['presets'][1]
        self.assertEqual(self.service.save(other, self.launcher, make_default=False)['default_preset'], preset['id'])


    def test_language_names_and_codes_are_normalized_before_matching(self):
        from praat_ai.mfa_resources import _language
        self.assertEqual(_language('japanese'), 'ja')
        self.assertEqual(_language('ja-JP'), 'ja')
        self.assertEqual(_language('eng'), 'en')
        self.assertEqual(_language('multilingual'), '')
        self.assertEqual(_language('klingon'), 'klingon')
        g2p = self.g2p(language='japanese')
        acoustic = self.imported('acoustic', self.model('ja.zip', model_type='acoustic', language='japanese',
                                                        phones=['HH', 'AH0', 'L', 'OW1']))
        preset = self.preset(g2p=g2p, acoustic=acoustic, dictionary=legacy_resource_id('dictionary', self.dictionary),
                             phone_set='arpabet', confirmed=True)
        resources = self.service.list()['resources']
        # What a Japanese preset would declare; validate_preset keeps the builtin language,
        # so the comparison itself is what must normalize both sides.
        preset['language'] = 'ja'
        status = MfaResourceService._check(preset, self.launcher, resources)
        self.assertFalse(any('语言' in issue for issue in status['issues']), status['issues'])
        preset['language'] = 'ko'
        mismatch = MfaResourceService._check(preset, self.launcher, resources)
        self.assertTrue(any('语言' in issue for issue in mismatch['issues']), mismatch['issues'])

    def test_builtin_presets_keep_fixed_identity_and_ship_a_japanese_preset(self):
        from praat_ai.mfa_resources import validate_preset
        presets = {p['id']: p for p in pronunciation_presets()}
        self.assertEqual(set(presets), {'en-GB', 'en-US', 'ja-JP'})
        # Stored configurations bind presets by id, so existing identities must not shift.
        self.assertEqual((presets['en-GB']['language'], presets['en-GB']['rule_id']), ('en', None))
        self.assertEqual((presets['en-US']['language'], presets['en-US']['rule_id']),
                         ('en', 'en-US-general-v1'))
        japanese = presets['ja-JP']
        self.assertEqual((japanese['language'], japanese['variant'], japanese['rule_id']),
                         ('ja', 'ja-JP', 'ja-JP-standard-v1'))
        self.assertEqual(japanese['phone_set'], 'ipa')
        self.assertNotIn('confirmed', japanese)
        self.assertEqual(japanese['phone_map'],
                         {'dzː': 'd͡zː', 'dʑː': 'd͡ʑː', 'tsː': 't͡sː', 'tɕː': 't͡ɕː'})
        for preset in pronunciation_presets():
            self.assertEqual(validate_preset(preset), preset)
        # Callers mutate presets (status, bindings); the mutable defaults must be copies.
        pronunciation_presets()[2]['phone_map']['X'] = 'x'
        self.assertNotIn('X', pronunciation_presets()[2]['phone_map'])

    def test_japanese_preset_reaches_ready_with_a_japanese_lexicon_and_model(self):
        preset = dict(next(p for p in pronunciation_presets() if p['id'] == 'ja-JP'))
        # tɕː is one of the labels the IPA table cannot split without the preset's map.
        lexicon = self.root / 'japanese.dict'
        lexicon.write_text('あなた a n a t a\nち tɕː i\n', encoding='utf-8')
        acoustic = self.imported('acoustic', self.model('ja-acoustic.zip', model_type='acoustic',
                                                        language='japanese', phones=['a', 'n', 't', 'i', 'tɕː']))
        preset.update(dictionary=self.imported('dictionary', lexicon), acoustic=acoustic, confirmed=True)
        result = self.service.environment(preset, self.conda_launcher(), runner=self.environment_runner())
        self.assertEqual(result['blocking'], [])
        self.assertTrue(result['ready'])
        self.assertEqual(result['status']['dictionary'], '静态检查通过')
        self.assertEqual(result['status']['acoustic'], '静态检查通过')
        self.assertEqual(result['preset']['language'], 'ja')
        # Without the shipped map the same lexicon cannot be mapped one-to-one.
        bare = dict(preset, phone_map={})
        blocked = self.service.environment(bare, self.conda_launcher(), runner=self.environment_runner())
        self.assertFalse(blocked['ready'])
        self.assertTrue(any('IPA 映射' in item for item in blocked['blocking']), blocked['blocking'])

    def environment_runner(self, child=None, version='3.4.2'):
        """Synthetic probe command results; nothing here runs MFA or aligns audio."""
        module = alignment_probe()
        checks = {name: dict(status='ok', detail='ok') for name, _ in module.CHILD_CHECKS}
        report = child if child is not None else dict(
            python='3.13.0', executable=r'C:\envs\aligner\python.exe', checks=checks,
            temp=dict(directory=r'C:\Temp', ascii_only=True, writable=True), numba_cache_dir='')
        calls = []

        def run(command, timeout):
            calls.append(list(command))
            if command[-1] == '--child':
                # A dict carrying 'stdout' is a failed command result, not a child report.
                if isinstance(report, dict) and 'stdout' in report:
                    return report
                return dict(status='ok', detail='ok', stdout=json.dumps(report))
            return dict(status='ok', detail=version, stdout=version)

        run.calls = calls
        return run

    def ready_preset(self):
        g2p = self.g2p()
        acoustic = self.imported('acoustic', self.model('acoustic.zip', model_type='acoustic', language='english',
                                                        phones=['HH', 'AH0', 'L', 'OW1']))
        return self.preset(g2p=g2p, acoustic=acoustic, dictionary=legacy_resource_id('dictionary', self.dictionary),
                           phone_set='arpabet', confirmed=True)

    def conda_launcher(self):
        conda = self.root / 'conda.exe'
        conda.write_bytes(b'not executed')
        return dict(executable='', conda_executable=str(conda), conda_environment='aligner')

    def stub_probe(self, ready=True):
        module = Mock()
        module.probe = Mock(return_value=dict(
            schema='mfa-alignment-probe-v1', ready=ready,
            summary='对齐环境可用：3.4.2' if ready else '对齐环境不完整（1 项）：MFA 启动方式 — 未配置',
            launcher='conda run -n aligner', environment='aligner', version='3.4.2', python='',
            checks=[dict(id='launcher', label='MFA 启动方式', status='ok' if ready else 'error',
                         detail='3.4.2', repair='')],
            blocking=[] if ready else ['launcher']))
        return module

    def test_environment_rpc_is_read_only_and_passes_the_resolved_inputs(self):
        preset = self.ready_preset()
        launcher = self.conda_launcher()
        self.service.save(preset, launcher)
        before = self.path.read_bytes()
        module = self.stub_probe()
        with patch('praat_ai.mfa_resources.alignment_probe', return_value=module):
            result = self.service.rpc('mfa.environment', {'preset_id': preset['id']})
        self.assertEqual(before, self.path.read_bytes())
        self.assertTrue(result['ready'])
        self.assertEqual(result['blocking'], [])
        self.assertEqual(result['preset']['id'], preset['id'])
        self.assertEqual(result['resources']['dictionary']['path'], str(self.dictionary))
        acoustic = next(r['path'] for r in self.service.list()['resources']['acoustic'] if r['id'] == preset['acoustic'])
        self.assertEqual(module.probe.call_args.kwargs, dict(
            mfa_executable='', conda_executable=str(self.root / 'conda.exe'), conda_environment='aligner',
            dictionary=str(self.dictionary), acoustic_model=acoustic, runner=None))

    def test_environment_reports_a_usable_conda_environment(self):
        preset = self.ready_preset()
        result = self.service.environment(preset, self.conda_launcher(), runner=self.environment_runner())
        self.assertTrue(result['ready'], result['blocking'])
        self.assertEqual(result['environment']['schema'], 'mfa-alignment-probe-v1')
        self.assertIn('对齐环境可用', result['summary'])
        self.assertEqual(result['resources']['acoustic']['exists'], True)
        self.assertEqual(result['preset']['language'], 'en')
        self.assertEqual(result['notes'], ['检测项目：MFA 启动、Python 依赖与资源兼容性'])

    def test_environment_uses_the_draft_launcher_and_reports_every_blocker(self):
        preset = self.ready_preset()
        runner = self.environment_runner(child=dict(status='error', detail='未解析到 MFA 环境的 python.exe', stdout=''))
        result = self.service.environment(preset, self.conda_launcher(), runner=runner)
        self.assertFalse(result['ready'])
        self.assertEqual(result['launcher']['conda_environment'], 'aligner')
        self.assertEqual([command[3] for command in runner.calls if command[-1] == '--child'], ['aligner'])
        self.assertIn('环境内依赖探针', result['blocking'][0])
        self.assertTrue(any('python.exe' in item for item in result['blocking']))
        self.assertIn('conda run -n aligner', result['environment']['launcher'])

    def test_environment_separates_a_working_environment_from_conflicting_resources(self):
        preset = self.ready_preset()
        acoustic = self.imported('acoustic', self.model('japanese.zip', model_type='acoustic', language='japanese',
                                                       phones=['a', 'n']))
        preset = self.preset(g2p=preset['g2p'], acoustic=acoustic, dictionary=preset['dictionary'],
                             phone_set='arpabet', confirmed=True)
        result = self.service.environment(preset, self.conda_launcher(), runner=self.environment_runner())
        self.assertTrue(result['environment']['ready'])
        self.assertEqual(result['status']['compatibility'], '冲突')
        self.assertFalse(result['ready'])
        self.assertIn('冲突', result['summary'])
        self.assertTrue(any('语言' in item for item in result['blocking']))

    def test_environment_reports_missing_alignment_files_even_with_a_working_launcher(self):
        result = self.service.environment(self.preset(confirmed=True), self.conda_launcher(),
                                          runner=self.environment_runner())
        self.assertFalse(result['environment']['ready'])
        self.assertFalse(result['ready'])
        self.assertTrue(any(item.startswith('MFA 词典') for item in result['blocking']), result['blocking'])
        self.assertTrue(any(item.startswith('声学模型') for item in result['blocking']), result['blocking'])

    def test_environment_rejects_unknown_preset_and_invalid_launcher_shape(self):
        with self.assertRaises(ValueError):
            self.service.rpc('mfa.environment', {'preset_id': 'nope'})
        with self.assertRaises(ValueError):
            self.service.environment(self.preset(), dict(executable=7))
        empty = self.service.environment(self.preset(), dict(executable='', conda_executable='', conda_environment=''),
                                         runner=self.environment_runner())
        self.assertFalse(empty['ready'])
        self.assertTrue(any(item.startswith('MFA 启动方式') for item in empty['blocking']), empty['blocking'])

    def test_environment_is_read_only_rpc_and_never_a_binding_writer(self):
        with self.assertRaises(ValueError):
            self.service.rpc('mfa.environment.write', {})
        with self.assertRaises(ValueError):
            self.service.rpc('mfa.environment', {})


if __name__ == '__main__':
    unittest.main()
