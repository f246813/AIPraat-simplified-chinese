"""Installation-relative locations for managed downloads, caches and logs."""
from __future__ import annotations

import os
from pathlib import Path
from mfa_alignment_probe import mfa_root_directory


def installation_root() -> Path:
    override = os.environ.get('PRAAT_AI_INSTALL_ROOT', '').strip()
    return Path(override).expanduser().resolve() if override else Path(__file__).resolve().parents[2]


def dependency_environment() -> dict[str, str]:
    """Keep dependency state together while honoring explicit environment overrides."""
    root = installation_root()
    environment = dict(os.environ)
    for name, relative in {
        'HF_HOME': 'cache/huggingface',
        'TORCH_HOME': 'cache/torch',
        'NUMBA_CACHE_DIR': 'cache/numba',
    }.items():
        if not environment.get(name):
            environment[name] = str(root / relative)
        Path(environment[name]).mkdir(parents=True, exist_ok=True)
    if not environment.get('MFA_ROOT_DIR'):
        environment['MFA_ROOT_DIR'] = str(mfa_root_directory(root))
    Path(environment['MFA_ROOT_DIR']).mkdir(parents=True, exist_ok=True)
    return environment
