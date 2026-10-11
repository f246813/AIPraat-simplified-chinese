"""Real AAC decode and isolated Praat GUI/IPC verification on Windows."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
STAGE = ROOT / '.build-tools/anki-m4a-20261007'
sys.path[:0] = [str(ROOT / 'ai'), str(ROOT / '.build-tools/anki-test-deps')]
import imageio_ffmpeg
from praat_ai import anki_bridge, sendpraat


def quote(value): return '"' + str(value).replace('\\','/').replace('"','""') + '"'


def main():
    fixture = STAGE / '音声 fixtures'; fixture.mkdir(exist_ok=True)
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    executable = STAGE / 'Praat-link.exe'
    wav = fixture / '接する stereo 48k.wav'
    aac = fixture / '接する stereo 48k.M4A'
    alac = fixture / '接する ALAC.m4a'
    opus = fixture / '例文.opus'
    subprocess.run([ffmpeg,'-nostdin','-v','error','-f','lavfi','-i','aevalsrc=0.2*sin(2*PI*440*t)|0.3*sin(2*PI*660*t):s=48000:d=1.25','-c:a','pcm_s16le','-y',str(wav)],check=True)
    for codec, target in [('aac',aac),('alac',alac)]:
        subprocess.run([ffmpeg,'-nostdin','-v','error','-i',str(wav),'-c:a',codec,'-y',str(target)],check=True)
    subprocess.run([ffmpeg,'-nostdin','-v','error','-i',str(wav),'-c:a','libopus','-y',str(opus)],check=True)
    read = fixture / 'read.praat'
    stats = fixture / 'stats.txt'
    read.write_text(f'''Read from file: {quote(aac)}
rate = Get sampling frequency
channels = Get number of channels
duration = Get total duration
rms = Get root-mean-square: 0, 0
writeFileLine: {quote(stats)}, rate, " ", channels, " ", duration, " ", rms
''', encoding='utf-8')
    batch = subprocess.run([str(executable),'--no-plugins','--FULL-TRUST','--run',str(read)],capture_output=True,timeout=30)
    assert batch.returncode == 0, batch.stderr.decode('utf-16-le', errors='replace')
    rate, channels, duration, rms = map(float, stats.read_text(encoding='utf-8-sig').split())
    assert rate==48000 and channels==2 and abs(duration-1.25)<.06 and .15<rms<.22, (rate,channels,duration,rms)
    private_home=fixture/'home'
    profile=private_home/'AppData/Roaming/Praat'; profile.mkdir(parents=True,exist_ok=True)
    (profile/'Preferences.txt').write_text('AI.projectDirectory: '+str(fixture/'ai')+'\n',encoding='utf-8')
    opening=fixture/'open.praat'; result=fixture/'opened.txt'
    opening.write_text(f'Open Anki audio: {quote(aac)}, 0.2, 0.7, {quote(result)}, {quote(aac)}\n',encoding='utf-8')
    result.unlink(missing_ok=True)
    env=dict(os.environ,APPDATA=str(profile.parent),USERPROFILE=str(private_home))
    process=subprocess.Popen([str(executable),'--new-send','--no-plugins','--FULL-TRUST','--pref-dir='+str(profile),str(opening)],env=env)
    report=dict(native=dict(rate=rate,channels=channels,duration=duration,rms=rms))
    try:
        deadline=time.monotonic()+20
        while not result.exists() and time.monotonic()<deadline: time.sleep(.1)
        assert result.exists(), 'No initial GUI acknowledgement'
        assert result.read_text(encoding='utf-8-sig').startswith('ok\n'),result.read_text(encoding='utf-8-sig')
        with patch.object(sendpraat,'message_file_path',return_value=profile/'Message.txt'):
            assert not anki_bridge._open_native(aac,aac,.3,.9,process.pid,fixture)
            state=fixture/'gui-state.txt'
            query=fixture/'query.praat'
            state.unlink(missing_ok=True)
            query.write_text(f'''select all
count = numberOfSelected ("Sound")
editor: 1
start = Get start of selection
end = Get end of selection
endeditor
writeFileLine: {quote(state)}, count, " ", start, " ", end
''',encoding='utf-8')
            sent,note=sendpraat.deliver(ROOT/'ai',query,process_id=process.pid); assert sent,note
            until=time.monotonic()+15
            while not state.exists() and time.monotonic()<until: time.sleep(.1)
            assert state.exists(), 'No GUI selection query result'
            values=list(map(float,state.read_text(encoding='utf-8-sig').split()))
            assert values==[1,.3,.9], values
            report['gui']=dict(reused_sound_count=values[0],selection=values[1:])
            # A damaged M4A must report failure, rather than leaving a modal dialog.
            corrupt=fixture/'broken.m4a'; corrupt.write_bytes(b'not an audio file')
            failure=anki_bridge._open_native(corrupt,corrupt,0,0,process.pid,fixture)
            assert failure; report['corrupt_reported']=True
            # Confirm the retained conversion route creates an ordinary WAV,
            # preserves the original association, and supports another range.
            fallback=fixture/'decoded.wav'
            anki_bridge.decode_wav(alac,fallback,ffmpeg)
            assert not anki_bridge._open_native(fallback,alac,.1,.8,process.pid,fixture)
            report['fallback']=dict(source=str(alac),pcm='s24le')
            converted_paths=[]
            decode=anki_bridge.decode_wav
            def track_decode(source,target):
                converted_paths.append(target)
                return decode(source,target,ffmpeg)
            with patch.object(anki_bridge,'_target',return_value=process.pid), patch.object(anki_bridge,'decode_wav',side_effect=track_decode), patch.object(anki_bridge,'settings',return_value={**anki_bridge.DEFAULTS,'ffmpeg':ffmpeg}):
                response=anki_bridge.open_audio(str(opus),start=.2,end=.6)
            assert response['converted'],response
            assert converted_paths and all(not path.exists() for path in converted_paths)
            report['automatic_fallback']=dict(converted=True,temporary_wav_cleaned=True,original_unchanged=opus.is_file())
            windows=[w for w in sendpraat.praat_windows() if w.process_id==process.pid]
            report['windows']=[w.title for w in windows]
            assert any('Sound' in w.title for w in windows),report['windows']
            from PIL import ImageGrab
            import ctypes
            from ctypes import wintypes
            editor=next(w for w in windows if 'Sound' in w.title)
            rectangle=wintypes.RECT(); ctypes.windll.user32.GetWindowRect(editor.handle,ctypes.byref(rectangle))
            time.sleep(.5)
            ImageGrab.grab((rectangle.left,rectangle.top,rectangle.right,rectangle.bottom)).save(ROOT/'installer/verification/anki-m4a-editor.png')
    finally:
        process.terminate(); process.wait(timeout=10)
    output=ROOT/'installer/verification/anki-m4a-result.json'
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__': main()
