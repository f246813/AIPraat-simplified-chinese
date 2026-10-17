"""Schema/gate tests only. ALL audio and labels here are SYNTHETIC fixtures.

The positive test declares source.kind=real_recording solely to exercise a
manifest declaration. This is NOT a true provenance statement or real speech
accuracy evidence. Fixtures exist only in TemporaryDirectory, never the corpus.
"""
from __future__ import annotations

from contextlib import contextmanager, redirect_stdout
import copy
import importlib.util
import io
import json
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import wave

TOOL_PATH = Path(__file__).resolve().parents[1]/'tools'/'validate_normalization_materials.py'
spec = importlib.util.spec_from_file_location('materials_gate', TOOL_PATH)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


class MaterialGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root/'manifest.json'
        self.data = gate.template()
        self.data['corpus_id'] = 'UNIT-TEST-SYNTHETIC-NOT-ACCURACY'
        self.data['speakers'] = [dict(id=s, language='ja', provenance='Synthetic unit-test declaration, NOT a real person')
                                 for s in ('reference', 'learner')]
        for index, slot in enumerate(('reference_target', 'learner_target', 'reference_calibration', 'learner_calibration')):
            side, role = slot.split('_')
            audio = self.root/(slot+'.wav')
            with wave.open(str(audio), 'wb') as handle:
                handle.setnchannels(1)
                handle.setsampwidth(2)
                handle.setframerate(8000)
                handle.writeframes(struct.pack('<h', 100+index)*16000)
            material = copy.deepcopy(self.data['material_template'])
            material.update(id=slot, speaker_id=side, role=role)
            material['source'] = dict(kind='real_recording', origin='UNIT TEST ONLY: SYNTHETIC fixture declaration',
                permission='unit test fixture', recorded_at='2026-10-16', recording_id=slot)
            material['audio'] = dict(path=audio.name, sha256=gate.digest(audio))
            annotation = copy.deepcopy(self.data['annotation_template'])
            annotation.update(audio_sha256=gate.digest(audio), speaker_id=side, annotator='unit-test-fixture-NOT-human',
                              reviewed_at='2026-10-16T00:00:00Z', method='manual')
            phones = []
            labels = gate.INVENTORIES['ja-JP-standard-v1'][1]*3 if role == 'calibration' else ('a', 'i')
            for phone_index, ipa in enumerate(labels):
                phones.append(dict(ipa=ipa, start=phone_index*0.1, end=phone_index*0.1+0.08,
                                   judgment='acceptable', rationale='Synthetic test value, NOT annotation evidence'))
            annotation['phones'] = phones
            label_file = self.root/(slot+'.json')
            label_file.write_text(json.dumps(annotation), encoding='utf-8')
            material['annotation'] = dict(path=label_file.name, sha256=gate.digest(label_file))
            self.data['materials'].append(material)
        self.data['cases'] = [dict(id='synthetic-schema-case', **{s:s for s in
            ('reference_target', 'learner_target', 'reference_calibration', 'learner_calibration')})]

    def save(self):
        self.path.write_text(json.dumps(self.data, ensure_ascii=False), encoding='utf-8')

    def validate(self):
        self.save()
        return gate.validate_manifest(self.path)[0]

    def annotation(self, index, mutate):
        material = self.data['materials'][index]
        path = self.root/material['annotation']['path']
        payload = gate.read_json(path)
        mutate(payload)
        path.write_text(json.dumps(payload), encoding='utf-8')
        material['annotation']['sha256'] = gate.digest(path)

    def assertBlocked(self, text):
        report = self.validate()
        self.assertEqual(report['status'], 'blocked')
        self.assertIn(text, json.dumps(report))
        self.assertEqual(report['accuracy']['status'], 'not_measured')

    def test_positive_schema_is_ready_not_accuracy(self):
        report = self.validate()
        self.assertEqual(report['status'], 'ready')
        self.assertEqual(report['accuracy']['status'], 'not_measured')
        self.assertIn('external audit', report['provenance_notice'])

    def test_template_is_empty_and_blocked(self):
        self.data = gate.template()
        self.assertEqual(self.data['annotation_template']['phones'], [])
        self.assertEqual(self.data['speakers'], [])
        self.assertBlocked('awaiting user materials')

    def test_no_source_assumed_real(self):
        self.data['materials'][0]['source'] = {}
        self.assertBlocked('not synthetic/unknown')

    def test_unknown_origin_is_missing_evidence(self):
        self.data['materials'][0]['source']['origin'] = 'unknown'
        self.assertBlocked('missing source origin')

    def test_explicit_synthetic_source_rejected(self):
        self.data['materials'][0]['source']['kind'] = 'synthetic'
        self.assertBlocked('not synthetic/unknown')

    def test_missing_annotator_blocked(self):
        self.annotation(0, lambda a:a.pop('annotator'))
        self.assertBlocked('missing human annotator')

    def test_auto_alignment_is_not_human_annotation(self):
        self.annotation(0, lambda a:a.update(method='mfa'))
        self.assertBlocked('never auto-only')

    def test_reviewed_alignment_requires_origin(self):
        self.annotation(0, lambda a:a.update(method='human_reviewed'))
        self.assertBlocked('original_alignment_source')

    def test_human_judgments_required(self):
        self.annotation(1, lambda a:a['phones'][0].pop('judgment'))
        self.assertBlocked('missing human correctness judgment')

    def test_reference_cannot_be_unacceptable(self):
        self.annotation(0, lambda a:a['phones'][0].update(judgment='local_deviation'))
        self.assertBlocked('reference target requires human acceptable')

    def test_target_label_mismatch(self):
        self.annotation(1, lambda a:a['phones'][0].update(ipa='e'))
        self.assertBlocked('target phone sequences do not match')

    def test_annotation_audio_binding(self):
        self.annotation(1, lambda a:a.update(audio_sha256='0'*64))
        self.assertBlocked('not bound to exact audio')

    def test_missing_calibration_never_defaults_pass(self):
        del self.data['cases'][0]['learner_calibration']
        self.assertBlocked('learner_calibration: missing material')

    def test_both_speakers_need_multivowel_coverage(self):
        for index in (2, 3):
            with self.subTest(index=index):
                original = copy.deepcopy(self.data)
                self.annotation(index, lambda a:a.update(phones=a['phones'][:3]))
                self.assertBlocked('calibration coverage')
                self.data = original

    def test_wrong_speaker_calibration_blocked(self):
        self.data['cases'][0]['learner_calibration'] = 'reference_calibration'
        self.assertBlocked('calibration belongs to another speaker')

    def test_same_speaker_pair_blocked(self):
        for index in (1, 3):
            self.data['materials'][index]['speaker_id'] = 'reference'
        self.assertBlocked('distinct speakers')

    def test_renamed_duplicate_pcm_rejected(self):
        duplicate = self.root/self.data['materials'][0]['audio']['path']
        target = self.root/self.data['materials'][2]['audio']['path']
        target.write_bytes(duplicate.read_bytes())
        self.data['materials'][2]['audio']['sha256'] = gate.digest(target)
        self.annotation(2, lambda a:a.update(audio_sha256=gate.digest(target)))
        self.assertBlocked('duplicate PCM')

    def test_declared_recording_id_duplicates_rejected(self):
        self.data['materials'][2]['source']['recording_id'] = 'reference_target'
        self.assertBlocked('distinct declared source recording_id')

    def test_each_configuration_field_must_match(self):
        for key in gate.CONFIG_KEYS:
            with self.subTest(key=key):
                original = copy.deepcopy(self.data)
                self.data['materials'][0].pop(key)
                self.assertBlocked('configuration mismatch: '+key)
                self.data = original

    def test_unsupported_language_preset(self):
        self.data['configuration'].update(language='en', profile_id='en-GB')
        self.assertBlocked('no supported fixed vowel')

    def test_settings_protocol_cannot_be_silently_changed(self):
        self.data['configuration']['measurement_settings']['maximum_formant_hz'] = 5000
        self.assertBlocked('measurement settings mismatch')

    def test_invalid_bounds_and_empty_labels(self):
        for mutation in (lambda a:a['phones'][0].update(start=True),
                         lambda a:a['phones'][0].update(end=100),
                         lambda a:a['phones'][1].update(start=0),
                         lambda a:a.update(phones=[])):
            with self.subTest(mutation=mutation):
                original = (self.root/self.data['materials'][0]['annotation']['path']).read_bytes()
                self.annotation(0, mutation)
                self.assertEqual(self.validate()['status'], 'blocked')
                path = self.root/self.data['materials'][0]['annotation']['path']
                path.write_bytes(original)
                self.data['materials'][0]['annotation']['sha256'] = gate.digest(path)

    def test_missing_or_tampered_hash(self):
        self.data['materials'][0]['audio']['sha256'] = '0'*64
        self.assertBlocked('SHA-256 missing or mismatch')

    def test_path_traversal_rejected(self):
        self.data['materials'][0]['audio']['path'] = '../out.wav'
        self.assertBlocked('must remain inside manifest directory')

    def test_no_implicit_prosody_and_explicit_utterance_supported(self):
        self.assertEqual(gate.normalization_options({}, []), {})
        report = self.validate()
        self.assertEqual(report['status'], 'ready')
        self.data['configuration']['normalization_options']['pitch_confirmed'] = False
        self.assertBlocked('pitch_confirmed=true')

    def test_independent_pitch_is_never_faked_by_utterance_baselines(self):
        self.data['configuration']['normalization_options']['pitch_baseline_mode'] = 'independent'
        self.assertBlocked('reference_pitch_baseline: missing material')
        self.assertBlocked('learner_pitch_baseline: missing material')

    def add_pitch_baselines(self):
        """One reviewed neutral phrase per side. Synthetic fixture, NOT real speech."""
        config = self.data['configuration']
        config['normalization_options'] = dict(config['normalization_options'], pitch_baseline_mode='independent')
        for material in self.data['materials']:
            material['normalization_options'] = copy.deepcopy(config['normalization_options'])
        for index, side in enumerate(('reference', 'learner')):
            slot = side + '_pitch_baseline'
            audio = self.root/(slot+'.wav')
            with wave.open(str(audio), 'wb') as handle:
                handle.setnchannels(1)
                handle.setsampwidth(2)
                handle.setframerate(8000)
                handle.writeframes(struct.pack('<h', 900+index)*16000)
            material = copy.deepcopy(self.data['pitch_baseline_material_template'])
            material.update(id=slot, speaker_id=side, role='pitch_baseline')
            for key in gate.CONFIG_KEYS:
                material[key] = copy.deepcopy(config[key])
            material['source'] = dict(kind='real_recording', origin='UNIT TEST ONLY: SYNTHETIC fixture declaration',
                permission='unit test fixture', recorded_at='2026-10-16', recording_id=slot)
            material['audio'] = dict(path=audio.name, sha256=gate.digest(audio))
            annotation = copy.deepcopy(self.data['annotation_template'])
            annotation.update(audio_sha256=gate.digest(audio), speaker_id=side, annotator='unit-test-fixture-NOT-human',
                              reviewed_at='2026-10-16T00:00:00Z', method='manual',
                              neutral_phrase_text='Synthetic unit-test neutral phrase, NOT real speech')
            annotation['phones'] = [dict(ipa='a', start=0.1, end=0.3)]
            label_file = self.root/(slot+'.json')
            label_file.write_text(json.dumps(annotation), encoding='utf-8')
            material['annotation'] = dict(path=label_file.name, sha256=gate.digest(label_file))
            self.data['materials'].append(material)
            self.data['cases'][0][slot] = slot
        return config

    def test_independent_pitch_ready_needs_six_independent_materials(self):
        self.add_pitch_baselines()
        report = self.validate()
        self.assertEqual(report['status'], 'ready')
        self.assertEqual(report['pitch_baseline_mode'], 'independent')
        self.assertEqual(report['isolation']['status'], 'audited')
        self.assertEqual(report['accuracy']['status'], 'not_measured')

    def test_baseline_slots_rejected_while_mode_is_utterance(self):
        self.data['cases'][0]['reference_pitch_baseline'] = 'reference_calibration'
        self.assertBlocked('must not be attached while pitch_baseline_mode is utterance')

    def test_neutral_phrase_text_is_required(self):
        self.add_pitch_baselines()
        self.annotation(4, lambda a:a.pop('neutral_phrase_text'))
        self.assertBlocked('declared neutral_phrase_text')

    def test_placeholder_neutral_phrase_is_missing_evidence(self):
        self.add_pitch_baselines()
        self.annotation(5, lambda a:a.update(neutral_phrase_text='待填'))
        self.assertBlocked('declared neutral_phrase_text')

    def test_baseline_must_belong_to_its_side_speaker(self):
        self.add_pitch_baselines()
        self.data['materials'][4]['speaker_id'] = 'learner'
        self.annotation(4, lambda a:a.update(speaker_id='learner'))
        self.assertBlocked('reference_pitch_baseline belongs to another speaker')

    def test_sixth_recording_copy_is_rejected(self):
        self.add_pitch_baselines()
        duplicate = (self.root/self.data['materials'][0]['audio']['path']).read_bytes()
        target = self.root/self.data['materials'][4]['audio']['path']
        target.write_bytes(duplicate)
        self.data['materials'][4]['audio']['sha256'] = gate.digest(target)
        self.annotation(4, lambda a:a.update(audio_sha256=gate.digest(target)))
        report = self.validate()
        self.assertIn('6 independent audio materials required', json.dumps(report))
        self.assertEqual(report['isolation']['status'], 'violations')

    def test_fitting_recording_reused_as_target_is_an_isolation_violation(self):
        duplicate = (self.root/self.data['materials'][0]['audio']['path']).read_bytes()
        target = self.root/self.data['materials'][2]['audio']['path']
        target.write_bytes(duplicate)
        self.data['materials'][2]['audio']['sha256'] = gate.digest(target)
        self.annotation(2, lambda a:a.update(audio_sha256=gate.digest(target)))
        report = self.validate()
        self.assertEqual(report['isolation']['status'], 'violations')
        self.assertTrue(any('to fit and to evaluate' in item for item in report['isolation']['violations']))

    def test_one_fitting_recording_cannot_fit_both_speakers(self):
        duplicate = (self.root/self.data['materials'][2]['audio']['path']).read_bytes()
        target = self.root/self.data['materials'][3]['audio']['path']
        target.write_bytes(duplicate)
        self.data['materials'][3]['audio']['sha256'] = gate.digest(target)
        self.annotation(3, lambda a:a.update(audio_sha256=gate.digest(target)))
        report = self.validate()
        self.assertTrue(any('attributed to both speakers' in item for item in report['isolation']['violations']))

    def test_isolation_summary_maps_speakers_without_claiming_identity(self):
        report = self.validate()
        self.assertEqual(report['isolation']['speakers']['reference']['sides'], ['reference'])
        self.assertIn('not on speaker verification', report['isolation']['notice'])

    def test_blocked_batch_does_not_call_analysis(self):
        self.data = gate.template()
        report = self.validate()
        with patch.dict('sys.modules', {'praat_ai.pronunciation': None}):
            gate.run_cases(report, self.data, {})
        self.assertEqual(report['status'], 'blocked')

    @contextmanager
    def mocked_runtime(self, prediction=None, raw_prediction=None, failure=None, calibration_status='applied',
                       baseline_status='applied', drift=False, prosody_drift=False):
        # No real extractor, audio evidence or scoring is produced in these unit tests.
        if prediction is None:
            prediction = dict(normalization=dict(formants={f:dict(status='applied') for f in ('f1', 'f2')},
                                                 pitch=dict(status='applied'), loudness=dict(status='applied')),
                              observations=[])
        if raw_prediction is None:
            raw_prediction = dict(observations=[])
        responses = [SimpleNamespace(to_dict=lambda payload=raw_prediction: payload),
                     SimpleNamespace(to_dict=lambda payload=prediction: payload)]
        analyze = Mock(side_effect=failure if failure is not None else responses)
        import_baseline = Mock(return_value=dict(status=baseline_status))
        core = SimpleNamespace(VERSION='drift' if drift else gate.VERSION, PROTOCOL=gate.PROTOCOL,
            MEASUREMENT_SETTINGS=gate.SETTINGS, INVENTORIES={k:v[1] for k,v in gate.INVENTORIES.items()},
            fit_formant_calibration=Mock(return_value=dict(status=calibration_status)),
            audio_identity=Mock(return_value='mocked-unit-test-only'))
        prosody = SimpleNamespace(PITCH_PROTOCOL='drifted' if prosody_drift else gate.PITCH_PROTOCOL,
            PITCH_EXTRACTION=gate.PITCH_EXTRACTION, PITCH_DEFAULTS=gate.PROSODY_OPTIONS['pitch_settings'],
            TARGET_LUFS=gate.TARGET_LUFS, import_pitch_baseline=import_baseline)
        package = SimpleNamespace(normalization=core, normalization_prosody=prosody)
        with patch.dict('sys.modules', {
            'praat_ai': package, 'praat_ai.normalization':core, 'praat_ai.normalization_prosody':prosody,
            'praat_ai.audio': SimpleNamespace(extract_phone_tracks=Mock(return_value=[]), read_wav=Mock()),
            'praat_ai.models': SimpleNamespace(PhoneSpec=SimpleNamespace, AnalysisRequest=SimpleNamespace),
            'praat_ai.pronunciation': SimpleNamespace(analyze_pronunciation=analyze),
            'parselmouth': SimpleNamespace(),
        }):
            yield SimpleNamespace(analyze=analyze, import_baseline=import_baseline)

    def test_local_runner_passes_explicit_options_without_cloud_or_alignment(self):
        self.save()
        report, data, materials = gate.validate_manifest(self.path)
        with self.mocked_runtime() as runtime:
            gate.run_cases(report, data, materials)
        request = runtime.analyze.call_args.args[0]
        self.assertFalse(request.qwen_explain)
        self.assertFalse(request.qwen_vision)
        self.assertTrue(request.normalization['formants'])
        self.assertTrue(request.normalization['pitch_confirmed'])
        self.assertTrue(request.normalization['loudness'])
        self.assertEqual(request.normalization['pitch_baseline_mode'], 'utterance')
        self.assertEqual(request.phonemes[0].alignment_source, 'manual')
        self.assertEqual(request.phonemes[0].learner_end, 0.08)
        self.assertEqual(report['status'], 'completed')
        self.assertEqual(report['accuracy']['status'], 'not_measured')
        self.assertIn('not a full independent', report['cases'][0]['pitch_scope'])
        self.assertEqual(report['cases'][0]['human_judgments'][0]['judgment'], 'acceptable')

    def test_runner_marks_missing_requested_feature_unavailable(self):
        self.save()
        report, data, materials = gate.validate_manifest(self.path)
        prediction = dict(normalization=dict(formants={f:dict(status='applied') for f in ('f1', 'f2')},
                                            pitch=dict(status='insufficient_evidence', reason='no voiced frames'),
                                            loudness=dict(status='not_applicable', reason='too short')))
        with self.mocked_runtime(prediction=prediction):
            gate.run_cases(report, data, materials)
        self.assertEqual(report['cases'][0]['status'], 'unavailable')
        self.assertIn('no voiced frames', str(report['cases'][0]['gaps']))
        self.assertIn('too short', str(report['cases'][0]['gaps']))

    def test_runner_blocks_live_protocol_drift(self):
        self.save()
        report, data, materials = gate.validate_manifest(self.path)
        with self.mocked_runtime(drift=True) as runtime:
            gate.run_cases(report, data, materials)
        runtime.analyze.assert_not_called()
        self.assertEqual(report['status'], 'unavailable')

    def test_runner_blocks_live_prosody_protocol_drift(self):
        self.save()
        report, data, materials = gate.validate_manifest(self.path)
        with self.mocked_runtime(prosody_drift=True) as runtime:
            gate.run_cases(report, data, materials)
        runtime.analyze.assert_not_called()
        self.assertIn('prosody protocol changed', str(report['cases'][0]['gaps']))
        self.assertEqual(report['status'], 'unavailable')

    def test_runner_insufficient_measured_calibration_cannot_pass(self):
        self.save()
        report, data, materials = gate.validate_manifest(self.path)
        with self.mocked_runtime(calibration_status='insufficient_evidence') as runtime:
            gate.run_cases(report, data, materials)
        runtime.analyze.assert_not_called()
        self.assertEqual(report['cases'][0]['status'], 'unavailable')

    def test_runner_rechecks_hashes_before_extraction(self):
        self.save()
        report, data, materials = gate.validate_manifest(self.path)
        audio = materials['learner_target']['audio_path']
        audio.write_bytes(b'changed after validation')
        with self.mocked_runtime() as runtime:
            gate.run_cases(report, data, materials)
        runtime.analyze.assert_not_called()
        self.assertIn('changed after validation', str(report['cases'][0]['gaps']))

    def test_runner_isolates_case_failures(self):
        self.save()
        report, data, materials = gate.validate_manifest(self.path)
        with self.mocked_runtime(failure=RuntimeError('local extractor failure')):
            gate.run_cases(report, data, materials)
        self.assertEqual(report['cases'][0]['status'], 'unavailable')
        self.assertIn('local extractor failure', report['cases'][0]['gaps'][0])

    def test_runner_imports_both_neutral_phrase_baselines(self):
        self.add_pitch_baselines()
        self.save()
        report, data, materials = gate.validate_manifest(self.path)
        self.assertEqual(report['status'], 'ready')
        with self.mocked_runtime() as runtime:
            gate.run_cases(report, data, materials)
        request = runtime.analyze.call_args.args[0]
        self.assertEqual(request.normalization['pitch_baseline_mode'], 'independent')
        self.assertEqual(request.normalization['reference_pitch_baseline']['status'], 'applied')
        self.assertEqual(request.normalization['learner_pitch_baseline']['status'], 'applied')
        self.assertEqual([call.kwargs['speaker_label'] for call in runtime.import_baseline.call_args_list],
                         ['reference', 'learner'])
        self.assertEqual(runtime.import_baseline.call_args_list[0].kwargs['settings'],
                         gate.PROSODY_OPTIONS['pitch_settings'])
        self.assertEqual(runtime.import_baseline.call_args_list[0].kwargs['reading_id'], 'ja-JP-standard-v1')
        self.assertIn('independent neutral-phrase baseline', report['cases'][0]['pitch_scope'])
        self.assertEqual(report['status'], 'completed')

    def test_runner_rejects_unapplied_neutral_phrase_baseline(self):
        self.add_pitch_baselines()
        self.save()
        report, data, materials = gate.validate_manifest(self.path)
        with self.mocked_runtime(baseline_status='insufficient_evidence') as runtime:
            gate.run_cases(report, data, materials)
        runtime.analyze.assert_not_called()
        self.assertEqual(report['cases'][0]['status'], 'unavailable')
        self.assertIn('neutral-phrase pitch baseline not applicable', str(report['cases'][0]['gaps']))

    def test_runner_runs_raw_and_normalized_paths_on_the_same_boundaries(self):
        self.save()
        report, data, materials = gate.validate_manifest(self.path)
        raw = dict(observations=[dict(phone_index=0, feature='f2', unit='Hz',
                                      reference_value=1.0, learner_value=2.0, rule_id='rule')])
        normalized = dict(normalization=dict(formants={f:dict(status='applied') for f in ('f1', 'f2')},
                                             pitch=dict(status='applied'), loudness=dict(status='applied')),
                          observations=[dict(phone_index=1, feature='f2_lobanov_z', unit='z',
                                             reference_value=0.1, learner_value=-0.2, rule_id='rule')])
        with self.mocked_runtime(prediction=normalized, raw_prediction=raw) as runtime:
            gate.run_cases(report, data, materials)
        case = report['cases'][0]
        self.assertEqual(case['status'], 'completed')
        self.assertNotIn('normalization', case['prediction_raw'])
        self.assertFalse(hasattr(runtime.analyze.call_args_list[0].args[0], 'normalization'))
        self.assertTrue(runtime.analyze.call_args_list[1].args[0].normalization['formants'])
        self.assertEqual(runtime.analyze.call_args_list[0].args[0].phonemes[0].learner_end,
                         runtime.analyze.call_args_list[1].args[0].phonemes[0].learner_end)
        comparison = case['comparison']
        self.assertEqual(comparison['metric'], 'not_defined')
        self.assertEqual(comparison['counts']['raw_observations'], 1)
        self.assertEqual(comparison['counts']['normalized_observations'], 1)
        self.assertEqual(comparison['features']['f2']['raw'], 1)
        self.assertEqual(comparison['features']['f2_lobanov_z']['normalized'], 1)
        self.assertEqual([row['raw_features'] for row in comparison['phones']], [['f2'], []])
        self.assertIn('no accuracy', comparison['notice'])
        self.assertEqual(report['accuracy']['status'], 'not_measured')

    def test_comparison_counts_human_deviations_without_converting_them(self):
        self.annotation(1, lambda a:a['phones'][0].update(judgment='local_deviation',
                                                          rationale='Synthetic fixture, NOT human evidence'))
        self.save()
        report, data, materials = gate.validate_manifest(self.path)
        normalized = dict(normalization=dict(formants={f:dict(status='applied') for f in ('f1', 'f2')},
                                             pitch=dict(status='applied'), loudness=dict(status='applied')),
                          observations=[dict(phone_index=0, feature='f1_lobanov_z', unit='z',
                                             reference_value=0.0, learner_value=1.0, rule_id='rule')])
        with self.mocked_runtime(prediction=normalized, raw_prediction=dict(observations=[])):
            gate.run_cases(report, data, materials)
        counts = report['cases'][0]['comparison']['counts']
        self.assertEqual(counts['local_deviation_phones'], 1)
        self.assertEqual(counts['local_deviation_with_normalized_observation'], 1)
        self.assertEqual(counts['local_deviation_with_raw_observation'], 0)
        self.assertEqual(counts['human_acceptable'], 1)
        self.assertEqual(report['cases'][0]['human_judgments'][0]['judgment'], 'local_deviation')
        self.assertEqual(report['accuracy']['status'], 'not_measured')

    def test_usability_summary_separates_applicability_from_correctness(self):
        self.save()
        report, data, materials = gate.validate_manifest(self.path)
        prediction = dict(normalization=dict(formants={'f1': dict(status='applied'),
                                                       'f2': dict(status='applied', reason=''),
                                                       'f3': dict(status='insufficient_evidence', reason='missing F3')},
                                             pitch=dict(status='insufficient_evidence', reason='no voiced frames'),
                                             loudness=dict(status='applied')), observations=[])
        with self.mocked_runtime(prediction=prediction):
            gate.run_cases(report, data, materials)
        summary = report['usability']
        self.assertEqual(summary['features']['f3'], dict(applied=0, unavailable=1,
                                                         reasons={'insufficient_evidence: missing F3': 1}))
        self.assertEqual(summary['features']['pitch']['unavailable'], 1)
        self.assertEqual(summary['features']['loudness']['applied'], 1)
        self.assertIn('not whether any pronunciation judgment is correct', summary['notice'])
        self.assertEqual(report['accuracy']['status'], 'not_measured')

    def test_comparison_keeps_observations_outside_the_phone_range_visible(self):
        self.save()
        report, data, materials = gate.validate_manifest(self.path)
        stray = dict(observations=[dict(phone_index=99, feature='f1', unit='Hz',
                                        reference_value=0.0, learner_value=0.0, rule_id='rule')])
        with self.mocked_runtime(prediction=stray, raw_prediction=dict(observations=[])):
            gate.run_cases(report, data, materials)
        counts = report['cases'][0]['comparison']['counts']
        self.assertEqual(counts['observations_outside_phone_range'], 1)
        self.assertEqual(counts['normalized_observations'], 0)

    def test_duplicate_json_and_nan_rejected(self):
        for payload in ('{"materials":[],"materials":[]}', '{"x":NaN}'):
            self.path.write_text(payload, encoding='utf-8')
            with self.assertRaises(ValueError):
                gate.validate_manifest(self.path)

    def test_batch_keeps_other_invalid_manifests(self):
        self.save()
        broken = self.root/'broken.json'
        broken.write_text('{', encoding='utf-8')
        output = self.root/'output.json'
        with redirect_stdout(io.StringIO()):
            code = gate.main([str(broken), str(self.path), '--output', str(output)])
        self.assertEqual(code, 2)
        report = gate.read_json(output)
        self.assertEqual([r['status'] for r in report['manifests']], ['invalid', 'ready'])

    def test_template_does_not_overwrite(self):
        self.save()
        before = self.path.read_bytes()
        with redirect_stdout(io.StringIO()), self.assertRaises(SystemExit):
            gate.main(['--template', str(self.path)])
        self.assertEqual(self.path.read_bytes(), before)

    def test_output_never_overwrites_materials(self):
        self.save()
        before = self.path.read_bytes()
        with redirect_stdout(io.StringIO()), self.assertRaises(SystemExit):
            gate.main([str(self.path), '--output', str(self.path)])
        self.assertEqual(self.path.read_bytes(), before)

    def test_english_requires_its_full_nine_vowel_inventory(self):
        config = self.data['configuration']
        config.update(language='en', profile_id='en-US-general-v1', reading_id='en-US-general-v1')
        for speaker in self.data['speakers']:
            speaker['language'] = 'en'
        for index, material in enumerate(self.data['materials']):
            material.update(language='en', profile_id=config['profile_id'], reading_id=config['reading_id'])
            def update(a, index=index):
                a.update(language='en', profile_id=config['profile_id'], reading_id=config['reading_id'])
                if index >= 2:
                    a['phones'] = [dict(ipa=ipa, start=i*0.06, end=i*0.06+0.05)
                                   for i, ipa in enumerate(gate.INVENTORIES[config['profile_id']][1]*3)]
            self.annotation(index, update)
        self.assertEqual(self.validate()['status'], 'ready')
        self.annotation(3, lambda a:a.update(phones=a['phones'][:-1]))
        self.assertBlocked('calibration coverage /')

    def test_inventory_does_not_certify_fixtures(self):
        result = gate.inventory(self.root)
        self.assertEqual(result['accuracy']['status'], 'not_measured')
        self.assertEqual(result['status'], 'awaiting_user_materials')
        self.assertTrue(all(item['acceptance_status'] == 'unverified' for item in result['files']))


class IndependentPitchContractTests(unittest.TestCase):
    """Real local core, synthetic WAVs. NOT acceptance evidence: no real speech, no judgments.

    Guards the seam the mocked runner cannot: the exact arguments the tool uses to import a
    neutral-phrase baseline must produce a snapshot that the shipped `apply_pitch` accepts.
    """
    @classmethod
    def setUpClass(cls):
        try:
            import parselmouth  # noqa: F401
            from praat_ai.normalization_prosody import apply_pitch, import_pitch_baseline
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise unittest.SkipTest(f'local runtime required: {exc}')
        cls.apply_pitch = staticmethod(apply_pitch)
        cls.import_pitch_baseline = staticmethod(import_pitch_baseline)

    def setUp(self):
        import numpy as np
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.np = np

    def wav(self, name, frequency, duration=1.0, gain=1.0):
        import wave as wave_module
        sample_rate = 48000
        times = self.np.arange(int(sample_rate*duration))/sample_rate
        samples = self.np.sin(times*2*self.np.pi*frequency)*.1*gain
        path = self.root/name
        with wave_module.open(str(path), 'wb') as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(sample_rate)
            handle.writeframes((samples*32767).astype('<i2').tobytes())
        return path

    def phone(self):
        from praat_ai.models import FeatureTrack, PhoneFeatureTracks
        times = [.05 + index*.01 for index in range(90)]
        return PhoneFeatureTracks(0, 'a', 0.0, 1.0, dict(
            rms=FeatureTrack('rms', times, [.07]*len(times), 'amplitude'),
            intensity=FeatureTrack('intensity', times, [70.0]*len(times), 'dB'),
            pitch=FeatureTrack('pitch', times, [220.0]*len(times), 'Hz')), alignment_source='provided')

    def import_baseline(self, path, side):
        return self.import_pitch_baseline(path, settings=gate.PROSODY_OPTIONS['pitch_settings'],
            language='ja', reading_id='ja-JP-standard-v1', speaker_label=side)

    def independent_options(self, **changes):
        options = dict(gate.PROSODY_OPTIONS, version=gate.VERSION, pitch_baseline_mode='independent',
                       pitch_settings=dict(gate.PROSODY_OPTIONS['pitch_settings']))
        options.update(changes)
        return options

    def test_tool_arguments_produce_a_baseline_apply_pitch_accepts(self):
        from praat_ai.audio import read_wav
        options = self.independent_options()
        left, right = self.wav('left.wav', 220), self.wav('right.wav', 330, gain=.2)
        options['reference_pitch_baseline'] = self.import_baseline(self.wav('ref-neutral.wav', 200), 'reference')
        options['learner_pitch_baseline'] = self.import_baseline(self.wav('learner-neutral.wav', 260), 'learner')
        for side, key in (('reference', 'reference_pitch_baseline'), ('learner', 'learner_pitch_baseline')):
            self.assertEqual(options[key]['status'], 'applied', side)
        result = self.apply_pitch(options, [self.phone()], [self.phone()], (read_wav(left), read_wav(right)),
                                  (str(left), str(right)), language='ja', reading_id='ja-JP-standard-v1')
        self.assertEqual(result['status'], 'applied')
        self.assertEqual(result['method'], 'independent_neutral_relative_pitch')
        self.assertEqual(result['source'], 'independent')
        self.assertAlmostEqual(result['reference_baseline']['baseline_hz'], 200, delta=2)
        self.assertAlmostEqual(result['learner_baseline']['baseline_hz'], 260, delta=3)

    def test_a_target_recording_cannot_be_smuggled_in_as_its_own_baseline(self):
        from praat_ai.audio import read_wav
        options = self.independent_options()
        left, right = self.wav('left.wav', 220), self.wav('right.wav', 330, gain=.2)
        options['reference_pitch_baseline'] = self.import_baseline(left, 'reference')
        options['learner_pitch_baseline'] = self.import_baseline(right, 'learner')
        result = self.apply_pitch(options, [self.phone()], [self.phone()], (read_wav(left), read_wav(right)),
                                  (str(left), str(right)), language='ja', reading_id='ja-JP-standard-v1')
        self.assertEqual(result['status'], 'insufficient_evidence')
        self.assertIn('独立音高基线与待测材料相同', result['reason'])

    def test_baseline_imported_with_other_settings_is_refused(self):
        options = self.independent_options(pitch_settings=dict(floor_hz=90, ceiling_hz=500))
        options['reference_pitch_baseline'] = self.import_baseline(self.wav('ref-neutral.wav', 200), 'reference')
        options['learner_pitch_baseline'] = self.import_baseline(self.wav('learner-neutral.wav', 260), 'learner')
        from praat_ai.audio import read_wav
        left, right = self.wav('left.wav', 220), self.wav('right.wav', 330, gain=.2)
        result = self.apply_pitch(options, [self.phone()], [self.phone()], (read_wav(left), read_wav(right)),
                                  (str(left), str(right)), language='ja', reading_id='ja-JP-standard-v1')
        self.assertEqual(result['status'], 'insufficient_evidence')
        self.assertIn('测量范围不匹配', result['reason'])


class RunnerIntegrationTests(unittest.TestCase):
    """Real gate + real core over one synthetic six-material case.

    Only phone extraction is patched (same seam the core's own tests patch) and all audio is
    synthetic sine: this is integration coverage for the runner, NOT acceptance evidence and
    NOT a statement about any real speaker, recording or pronunciation.
    """
    VOICE = ('a', 'i', 'ɯ')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root/'manifest.json'

    def wav(self, name, frequency):
        import numpy as np
        rate, duration = 48000, 2.0
        times = np.arange(int(rate*duration))/rate
        path = self.root/name
        with wave.open(str(path), 'wb') as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(rate)
            handle.writeframes((np.sin(times*2*np.pi*frequency)*.1*32767).astype('<i2').tobytes())
        return path

    def annotation(self, name, side, phones, role):
        payload = dict(schema_version=gate.ANNOTATION_SCHEMA, audio_sha256=gate.digest(self.root/(name+'.wav')),
                       speaker_id=side, language='ja', profile_id='ja-JP-standard-v1',
                       reading_id='ja-JP-standard-v1', annotator='unit-test-fixture-NOT-human',
                       reviewed_at='2026-10-16T00:00:00Z', method='manual', label_set='IPA', phones=phones)
        if role == 'pitch_baseline':
            payload['neutral_phrase_text'] = 'Synthetic unit-test neutral phrase, NOT real speech'
        path = self.root/(name+'.json')
        path.write_text(json.dumps(payload), encoding='utf-8')
        return path

    def material(self, name, side, role, label, frequency, phones, configuration):
        audio = self.wav(name+'.wav', frequency)
        labels = self.annotation(name, side, phones, role)
        return dict(id=name, speaker_id=side, role=role, **{k: copy.deepcopy(configuration[k]) for k in gate.CONFIG_KEYS},
                    source=dict(kind='real_recording', origin='UNIT TEST ONLY: SYNTHETIC fixture declaration',
                                permission='unit test fixture', recorded_at='2026-10-16', recording_id=name),
                    audio=dict(path=audio.name, sha256=gate.digest(audio)),
                    annotation=dict(path=labels.name, sha256=gate.digest(labels)))

    def build(self):
        inventory = gate.INVENTORIES['ja-JP-standard-v1'][1]
        options = dict(pitch=True, pitch_confirmed=True, pitch_settings=dict(floor_hz=75, ceiling_hz=600),
                       pitch_baseline_mode='independent', loudness=False)
        configuration = dict(language='ja', profile_id='ja-JP-standard-v1', reading_id='ja-JP-standard-v1',
                             normalization_version=gate.VERSION, protocol=gate.PROTOCOL,
                             measurement_settings=gate.SETTINGS.copy(), normalization_options=options)
        def token(index, ipa):
            start = 0.1 + index*0.07
            return dict(ipa=ipa, start=start, end=start+0.05)
        calibration = [token(index, ipa) for index, ipa in enumerate(inventory*3)]
        target = [dict(ipa=ipa, start=0.1 + index*0.2, end=0.1 + index*0.2 + 0.15,
                       judgment='acceptable' if index else 'local_deviation',
                       rationale='Synthetic fixture value, NOT human evidence')
                  for index, ipa in enumerate(self.VOICE)]
        reference_target = [dict(ipa=phone['ipa'], start=phone['start'], end=phone['end'],
                                 judgment='acceptable', rationale='Synthetic fixture value, NOT human evidence')
                            for phone in target]
        manifest = dict(schema_version=gate.SCHEMA, corpus_id='UNIT-TEST-SYNTHETIC-NOT-ACCURACY',
                        configuration=configuration,
                        speakers=[dict(id=side, language='ja', provenance='Synthetic unit-test declaration, NOT a real person')
                                  for side in ('reference', 'learner')],
                        materials=[
                            self.material('reference_target', 'reference', 'target', 'ref target', 220,
                                          reference_target, configuration),
                            self.material('learner_target', 'learner', 'target', 'learner target', 330,
                                          target, configuration),
                            self.material('reference_calibration', 'reference', 'calibration', 'ref calibration', 240,
                                          calibration, configuration),
                            self.material('learner_calibration', 'learner', 'calibration', 'learner calibration', 350,
                                          calibration, configuration),
                            self.material('reference_pitch_baseline', 'reference', 'pitch_baseline', 'ref neutral', 200,
                                          [dict(ipa='a', start=0.1, end=1.6)], configuration),
                            self.material('learner_pitch_baseline', 'learner', 'pitch_baseline', 'learner neutral', 260,
                                          [dict(ipa='a', start=0.1, end=1.6)], configuration)],
                        cases=[dict(id='synthetic-independent-case',
                                    reference_target='reference_target', learner_target='learner_target',
                                    reference_calibration='reference_calibration',
                                    learner_calibration='learner_calibration',
                                    reference_pitch_baseline='reference_pitch_baseline',
                                    learner_pitch_baseline='learner_pitch_baseline')])
        self.path.write_text(json.dumps(manifest, ensure_ascii=False), encoding='utf-8')

    @staticmethod
    def fake_extract(_path, specs, *args, **kwargs):
        # Mirrors the real seam: keyword-only learner plus optional reviewed measurement settings.
        learner = bool(kwargs.get('learner', False))
        from praat_ai.models import FeatureTrack, PhoneFeatureTracks
        base = 700.0 if learner else 500.0
        tracks = []
        for index, spec in enumerate(specs):
            start, end = ((spec.learner_start, spec.learner_end) if learner
                          else (spec.reference_start, spec.reference_end))
            times = [start + fraction*(end - start) for fraction in (.2, .5, .8)]
            value = base + index*11
            tracks.append(PhoneFeatureTracks(index, spec.ipa, start, end,
                {name: FeatureTrack(name, times, [value*multiplier]*3, 'Hz')
                 for name, multiplier in (('f1', 1), ('f2', 3), ('f3', 5))}, alignment_source='provided'))
        return tracks

    def test_one_independent_pitch_case_completes_and_reports_descriptively(self):
        self.build()
        with patch('praat_ai.audio.extract_phone_tracks', side_effect=self.fake_extract), \
             patch('praat_ai.pronunciation.extract_phone_tracks', side_effect=self.fake_extract):
            report, data, materials = gate.validate_manifest(self.path)
            self.assertEqual(report['status'], 'ready')
            self.assertEqual(report['pitch_baseline_mode'], 'independent')
            gate.run_cases(report, data, materials)
        case = report['cases'][0]
        self.assertEqual(case['status'], 'completed', case['gaps'])
        self.assertEqual(report['status'], 'completed')
        self.assertEqual(report['isolation']['status'], 'audited')
        self.assertEqual(case['pitch_scope'], 'independent neutral-phrase baseline, both sides, reviewed protocol')
        normalization = case['prediction']['normalization']
        self.assertEqual(normalization['pitch']['status'], 'applied')
        self.assertEqual(normalization['pitch']['method'], 'independent_neutral_relative_pitch')
        self.assertEqual(normalization['formants']['f1']['status'], 'applied')
        raw_features = {item['feature'] for item in case['prediction_raw']['observations']}
        normalized_features = {item['feature'] for item in case['prediction']['observations']}
        self.assertIn('f1', raw_features)
        self.assertNotIn('f1_lobanov_z', raw_features)
        self.assertIn('f1_lobanov_z', normalized_features)
        self.assertIn('pitch_relative_st', normalized_features)
        comparison = case['comparison']
        self.assertEqual(comparison['metric'], 'not_defined')
        self.assertGreaterEqual(comparison['counts']['normalized_observations'], 1)
        self.assertGreaterEqual(comparison['counts']['raw_observations'], 1)
        self.assertEqual(comparison['counts']['local_deviation_phones'], 1)
        self.assertEqual(case['human_judgments'][0]['judgment'], 'local_deviation')
        self.assertEqual(report['accuracy']['status'], 'not_measured')
        self.assertEqual(report['usability']['features']['f1']['applied'], 1)
        self.assertNotIn('loudness', report['usability']['features'])
        json.dumps(report, allow_nan=False)

    def test_runner_rejects_the_case_when_a_baseline_is_a_target_copy(self):
        self.build()
        duplicate = (self.root/'reference_target.wav').read_bytes()
        (self.root/'reference_pitch_baseline.wav').write_bytes(duplicate)
        manifest = json.loads(self.path.read_text(encoding='utf-8'))
        for material in manifest['materials']:
            if material['id'] == 'reference_pitch_baseline':
                material['audio']['sha256'] = gate.digest(self.root/'reference_pitch_baseline.wav')
                material['annotation']['sha256'] = gate.digest(self.root/'reference_pitch_baseline.json')
        self.path.write_text(json.dumps(manifest, ensure_ascii=False), encoding='utf-8')
        report, data, materials = gate.validate_manifest(self.path)
        self.assertEqual(report['status'], 'blocked')
        self.assertEqual(report['isolation']['status'], 'violations')
        self.assertIn('to fit and to evaluate', json.dumps(report['isolation']['violations']))
        with patch('praat_ai.audio.extract_phone_tracks', side_effect=self.fake_extract), \
             patch('praat_ai.pronunciation.extract_phone_tracks', side_effect=self.fake_extract):
            gate.run_cases(report, data, materials)
        self.assertNotIn('prediction', report['cases'][0])


if __name__ == '__main__':
    unittest.main()
