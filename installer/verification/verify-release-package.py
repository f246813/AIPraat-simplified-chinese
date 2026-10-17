"""Verify the release EXE against all current payload files and frontend sources."""
import hashlib
import argparse
import importlib.util
import io
import json
from pathlib import Path
import re
import zipfile

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('delivery', Path(__file__).with_name('verify-delivery-package.py'))
delivery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(delivery)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'test-records/installer/release-package-audit.json')
    args = parser.parse_args()
    build = ROOT / 'installer/build'
    manifest = json.loads((build / 'payload-manifest.json').read_text(encoding='utf-8-sig'))
    payload = (build / 'payload.zip').read_bytes()
    assert delivery.embedded_payload(ROOT / 'AIPraat-install.exe') == payload, 'Embedded ZIP differs from build ZIP'
    special = {
        'AIPraat.exe': build / 'AIPraat.exe',
        'AIPraat-使用说明.md': ROOT / 'installer/README.zh-CN.md',
        'ai/ai_config.example.json': build / 'ai_config.example.json',
        'ai/ConfigurePython.ps1': ROOT / 'installer/src/ConfigurePython.ps1',
    }
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = archive.namelist()
        assert len(names) == len(set(names))
        assert set(names) == set(manifest) | {'payload-manifest.json'}
        assert json.loads(archive.read('payload-manifest.json')) == manifest
        for name, expected in manifest.items():
            assert not re.search(r'(^|/)(ai_config\.json|anki_config\.json|logs|__pycache__)(/|$)|^ai/runtime(/|$)|\.pyc$', name), name
            assert sha(archive.read(name)) == expected, name
            assert sha(special.get(name, ROOT / name).read_bytes()) == expected, 'Stale: ' + name
    expected_files = set()
    for directory in ('praat_ai', 'anki_addon', 'plugin', 'tools', 'skills', 'third_party/pi-context', 'third_party/prompt-cache-skills'):
        for path in (ROOT / 'ai' / directory).rglob('*'):
            if path.is_file() and '__pycache__' not in path.parts and (path.suffix in ('.py', '.js', '.tsv', '.csv', '.praat', '.in', '.ps1', '.md') or path.name == 'LICENSE'):
                expected_files.add(path.relative_to(ROOT).as_posix())
    for path in (ROOT / 'ai').iterdir():
        if path.is_file() and (path.suffix == '.py' or re.match(r'^(requirements.*\.txt|(?:Download|Install).*\.ps1|README\.zh-CN\.md|Anki连接说明\.md|request\.example\.json)$', path.name)):
            expected_files.add(path.relative_to(ROOT).as_posix())
    expected_files.update(path.relative_to(ROOT).as_posix() for path in (ROOT / 'ai/frontend/dist').rglob('*') if path.is_file())
    assert not expected_files - set(manifest), 'Omitted runtime files: ' + str(sorted(expected_files - set(manifest)))
    frontend = ROOT / 'ai/frontend'
    for directory in ('src', 'tests', 'scripts'):
        for path in (frontend / directory).rglob('*'):
            if path.is_file():
                copy = frontend / 'dist/pi-desktop-source/rebuild' / path.relative_to(frontend)
                assert copy.is_file() and copy.read_bytes() == path.read_bytes(), 'Stale frontend source copy: ' + str(path)
    report = json.loads((build / 'build-report.json').read_text(encoding='utf-8-sig'))
    installer = ROOT / 'AIPraat-install.exe'
    assert report['sha256'] == sha(installer.read_bytes())
    assert report['praat_sha256'] == sha((ROOT / 'Praat.exe').read_bytes())
    result = {'payload_files': len(manifest), 'runtime_and_frontend_files_checked': len(expected_files), 'installer_size': installer.stat().st_size, 'installer_sha256': report['sha256'], 'omitted_files': [], 'stale_files': []}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
