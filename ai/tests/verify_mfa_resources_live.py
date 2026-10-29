"""Manual acceptance with installed resources and an isolated configuration.

--mode generation runs real English/Japanese MFA, including the tutor request
and selected-dictionary handoff. --mode ui exercises the production WebView2
resource pages with the installed large dictionaries. Never writes live config.
"""
import argparse
import copy
import cProfile
import io
import json
from pathlib import Path
import pstats
import sys
import tempfile
import time
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from praat_ai.bridge import SelectedObject
from praat_ai.config import AlignmentConfig
from praat_ai.mfa_g2p import choose_readings
from praat_ai.mfa_resources import MfaResourceService, _dictionary_inventory
from praat_ai.mfa_task import task_alignment
from praat_ai.ui import TutorFormApplication

PROJECT = Path(__file__).resolve().parents[2]
RECORDS = PROJECT / 'test-records' / 'installer'


def generation(config):
    service = MfaResourceService(config)
    profiler = cProfile.Profile()
    _dictionary_inventory.cache_clear()
    start = time.perf_counter()
    profiler.runcall(service.list)
    cold = time.perf_counter() - start
    stream = io.StringIO()
    pstats.Stats(profiler, stream=stream).sort_stats('cumulative').print_stats(20)
    (RECORDS / 'resource-page-profile-after.txt').write_text(stream.getvalue(), encoding='utf-8')
    start = time.perf_counter()
    data = service.list()
    warm = time.perf_counter() - start
    report = dict(cold_profiled_seconds=cold, warm_seconds=warm, generations=[])
    for preset_id, transcript in [('en-US', 'Hello world hello'), ('ja-JP', '日本語、こんにちは。東京 日本語')]:
        preset = next(p for p in data['presets'] if p['id'] == preset_id)
        # Declaration is only for this disposable test configuration.
        service.save(dict(preset, confirmed=True), data['launcher'])
        wav = config.parent / 'test.wav'
        with wave.open(str(wav), 'wb') as handle:
            handle.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
            handle.writeframes(b'\0\0' * 16000)
        app = TutorFormApplication([SelectedObject(1, 'reference', 'Sound', wav),
                                    SelectedObject(2, 'learner', 'Sound', wav)], config)
        start = time.perf_counter()
        result = app.rpc('tutor.generate_phonemes', dict(preset_id=preset_id, transcript=transcript))
        choices = {word['word']: 0 for word in result['words']}
        readings, ipa, raw = choose_readings(result, choices)
        request = app.build_request(dict(reference='1', learner='2', language=preset['language'],
            preset_id=preset_id, generation_id=result['id'], transcript=transcript,
            phonemes=' '.join(ipa), choices=choices, explain=False))
        with task_alignment(request, AlignmentConfig()) as alignment:
            selected = Path(alignment.mfa.dictionary_path).read_text(encoding='utf-8')
            assert alignment.backend == 'mfa-strict'
            assert all(word in selected for word in result['sequence'])
            assert [p.label for p in request.phonemes] == raw
        report['generations'].append(dict(preset=preset_id, seconds=time.perf_counter()-start,
            result=result, request_and_selected_dictionary=True))
        print('PASS actual tutor generation + request + selected dictionary: ' + preset_id, flush=True)
    return report


def ui(config):
    import webview
    from praat_ai.speech_dictionaries import DictionaryApplication
    from praat_ai.modern_app import HostAPI
    from praat_ai.modern_host import AssetServer
    app = DictionaryApplication(config)
    server = AssetServer(PROJECT / 'ai' / 'frontend' / 'dist')
    api = HostAPI(app)
    window = webview.create_window('G2P 模型选择验收', server.url + '?window=dictionaries',
                                  js_api=api, width=1000, height=720, min_size=(620, 360))
    app.window = window
    api._origin = server.url.rsplit('/', 1)[0]
    report = dict(checks=[], timings={}, errors=[])

    def verify():
        def js(source):
            return window.evaluate_js(source)
        def wait(source):
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if js(source):
                    return
                time.sleep(.05)
            raise AssertionError('Timed out: ' + source)
        def click(text, selector='button'):
            js(f"Array.from(document.querySelectorAll({json.dumps(selector)})).find(e=>e.textContent.trim()==={json.dumps(text)}).click()")
        def select(selector, value):
            js(f"(()=>{{const e=document.querySelector({json.dumps(selector)}); e.value={json.dumps(value)}; e.dispatchEvent(new Event('change',{{bubbles:true}}));}})()")
        def screenshot(name):
            from PIL import ImageGrab
            import System.Drawing as drawing
            # DOM updates precede the native compositor's next presentation.
            js('new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
            time.sleep(.2)
            point = window.native.PointToScreen(drawing.Point(0, 0))
            size = window.native.ClientSize
            ImageGrab.grab((point.X, point.Y, point.X+size.Width, point.Y+size.Height)).save(RECORDS / name)
        try:
            wait("!!document.querySelector('.dictionary-row')")
            click('MFA G2P 模型')
            start = time.perf_counter()
            wait("!!document.querySelector('[aria-label=\"用于哪个纠音配套\"]')")
            report['timings']['g2p_cold_page_seconds'] = time.perf_counter()-start
            select('[aria-label="用于哪个纠音配套"]', 'ja-JP')
            wait("!!document.querySelector('.dictionary-current')")
            screenshot('g2p-model-selection-live.png')
            report['checks'].append('G2P target preset and current model are visible')
            click('纠音资源配套')
            start = time.perf_counter()
            wait("document.querySelectorAll('.mfa-preset-card').length>=3")
            report['timings']['pairing_warm_page_seconds'] = time.perf_counter()-start
            click('配置 日语（标准语） 配套资源')
            wait("!!document.querySelector('dialog[open]')")
            select('dialog .mfa-resource select', '')
            click('保存配套', 'dialog button')
            wait("!document.querySelector('dialog')")
            assert next(p for p in app._mfa_resources.list()['presets'] if p['id']=='ja-JP')['g2p'] == ''
            click('MFA G2P 模型')
            wait("!!document.querySelector('[aria-label=\"用于哪个纠音配套\"]')")
            select('[aria-label="用于哪个纠音配套"]', 'ja-JP')
            model = next(r for r in app._mfa_resources.list()['resources']['g2p'] if Path(r['path']).name=='japanese_mfa.zip')
            selector = '[aria-label="选用 ' + model['name'] + '"]'
            js(f"document.querySelector({json.dumps(selector)}).click()")
            wait("!!document.querySelector('dialog[open]')")
            assert js("document.querySelector('dialog .mfa-resource select').value") == model['id']
            click('检查兼容性', 'dialog button')
            wait("document.querySelector('dialog').innerText.includes('检查完成')")
            click('保存配套', 'dialog button')
            wait("!document.querySelector('dialog') && !!document.querySelector('.dictionary-current')")
            assert next(p for p in app._mfa_resources.list()['presets'] if p['id']=='ja-JP')['g2p'] == model['id']
            report['checks'].append('Library selection checks and saves the chosen model to its preset')
            window.resize(620, 480)
            wait("innerWidth<700")
            assert js('document.documentElement.scrollWidth<=innerWidth')
            screenshot('g2p-model-selection-compact-live.png')
            click('纠音资源配套')
            wait("!!document.querySelector('.mfa-preset-card')")
            click('新建语言配套')
            wait("!!document.querySelector('dialog[open] .mfa-custom-identity')")
            js("(()=>{const fields=document.querySelectorAll('.mfa-custom-identity input'); const setter=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set; ['德语','de',''].forEach((value,index)=>{setter.call(fields[index],value); fields[index].dispatchEvent(new Event('input',{bubbles:true}));});})()")
            assert js('document.querySelector("dialog").scrollWidth<=document.querySelector("dialog").clientWidth')
            screenshot('mfa-custom-language-live.png')
            click('保存配套', 'dialog button')
            wait("!document.querySelector('dialog') && Array.from(document.querySelectorAll('.mfa-preset-card h2')).some(e=>e.textContent.includes('德语'))")
            assert any(p['language']=='de' and p['id'].startswith('custom-') for p in app._mfa_resources.list()['presets'])
            report['checks'].append('Custom language pairing saves through actual RPC; compact viewport has no horizontal overflow')
            print('PASS actual WebView2 pages and model selection', flush=True)
        except Exception as error:
            report['errors'].append(str(error))
            print('FAIL ' + str(error), flush=True)
        finally:
            window.destroy()
    try:
        webview.start(verify, gui='edgechromium', private_mode=True)
    finally:
        server.close()
    if report['errors']:
        raise AssertionError(report)
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=['generation', 'ui'], required=True)
    args = parser.parse_args()
    source = json.loads((PROJECT / 'ai' / 'ai_config.json').read_text(encoding='utf-8-sig'))
    isolated = {key: copy.deepcopy(source[key]) for key in ('alignment', 'pronunciation', 'modern') if key in source}
    with tempfile.TemporaryDirectory(prefix='mfa-resource-acceptance-') as directory:
        config = Path(directory) / 'config.json'
        config.write_text(json.dumps(isolated, ensure_ascii=False), encoding='utf-8')
        report = generation(config) if args.mode == 'generation' else ui(config)
        (RECORDS / f'mfa-resources-{args.mode}-live.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
