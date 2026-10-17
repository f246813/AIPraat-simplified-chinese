"""Offline provenance check. Supply the scratch download directory, not a user env.
Expected: pyloudnorm-0.2.0.tar.gz, upstream.zip, wheels/*.whl.
No downloads or installs; the caller explicitly provisions the development venv.
"""
import hashlib
import email
import io
import json
from pathlib import Path
import sys
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parents[2]
COMMIT = "b8d67bfd3ce5deef872f688fcfa491a0ca69fddd"


def main(folder):
    folder = Path(folder)
    recorded = json.loads((ROOT/"ai/third_party/pyloudnorm/provenance.json").read_text(encoding="utf-8"))
    for entry in recorded["artifacts"]:
        path = folder/entry["path"]
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        assert actual == entry["sha256"], (path.name, actual)
        print("SHA256 OK", path.name, actual)
        if path.name.startswith("scipy-"):
            with zipfile.ZipFile(path) as scipy_wheel:
                info = email.message_from_bytes(scipy_wheel.read("scipy-1.14.1.dist-info/METADATA"))
                assert info["Version"] == "1.14.1" and info["Requires-Python"] == ">=3.10"
                assert "numpy<2.3,>=1.23.5" in info.get_all("Requires-Dist")
                license_text = scipy_wheel.read("scipy-1.14.1.dist-info/LICENSE.txt").decode()
                assert "Redistribution and use in source and binary forms" in license_text and "SciPy Developers" in license_text
                print("SCIPY wheel metadata and bundled license OK", path.name)
    with zipfile.ZipFile(folder/"wheels/pyloudnorm-0.2.0-py3-none-any.whl") as wheel, zipfile.ZipFile(folder/"upstream.zip") as upstream, tarfile.open(folder/"pyloudnorm-0.2.0.tar.gz") as source:
        for name, expected in recorded["python_sources_sha256"].items():
            data = wheel.read(name)
            assert hashlib.sha256(data).hexdigest() == expected
            assert data == upstream.read(f"pyloudnorm-{COMMIT}/"+name)
            assert data == source.extractfile("pyloudnorm-0.2.0/"+name).read()
            print("SOURCE IDENTICAL wheel/sdist/commit", name)
        license_data = wheel.read("pyloudnorm-0.2.0.dist-info/licenses/LICENSE")
        assert license_data == upstream.read(f"pyloudnorm-{COMMIT}/LICENSE")
        assert license_data.replace(b"\r\n", b"\n") == (ROOT/"ai/third_party/pyloudnorm/LICENSE").read_bytes().replace(b"\r\n", b"\n")
        # Reuse upstream reference WAVs/targets, decoded using scipy rather than
        # adding the upstream test-only soundfile dependency to the runtime.
        import numpy as np
        import pyloudnorm as pyln
        from scipy.io import wavfile
        cases = {"sine_1000.wav": -3.0523438444331137,
                 "1770-2_Comp_RelGateTest.wav": -10.,
                 "1770-2_Comp_AbsGateTest.wav": -69.5,
                 "1770-2_Comp_24LKFS_1000Hz_2ch.wav": -24.}
        for name, target in cases.items():
            rate, data = wavfile.read(io.BytesIO(upstream.read(f"pyloudnorm-{COMMIT}/tests/data/"+name)))
            if np.issubdtype(data.dtype, np.integer):
                data = data.astype(np.float64)/2**(data.dtype.itemsize*8-1)
            else:
                data = data.astype(np.float64)
            meter = pyln.Meter(rate)
            loudness = meter.integrated_loudness(data)
            tolerance = 1e-6 if name == "sine_1000.wav" else .1
            assert abs(loudness-target) < tolerance, (name, loudness, target)
            if name == "sine_1000.wav":
                adjusted = pyln.normalize.loudness(data, loudness, -6.)
                assert abs(meter.integrated_loudness(adjusted)+6.) < 1e-6
                print("UPSTREAM NORMALIZE reference -6 LUFS passed")
            # Do not assert gain invariance on absolute-gate fixtures: gain may
            # legitimately change which blocks clear the fixed absolute gate.
            print("UPSTREAM REFERENCE", name, float(loudness))
    print("PASS release provenance, MIT license, upstream reference WAVs")


if __name__ == "__main__":
    main(sys.argv[1])
