"""Delete unused blank sessions from the modern dialogue library.

The desktop host runs the same scan when the chat window closes. This standalone
entry is for manual repair (for example after a forced kill) or for a shutdown
hook::

    python -m praat_ai.prune_blank_sessions

Only the modern store is touched. Legacy read-only history is never opened for
writing.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def default_sessions_db() -> Path:
    return Path(__file__).resolve().parents[1] / 'runtime' / 'modern' / 'sessions.sqlite3'


def prune(db_path: Path, legacy_path=None) -> list:
    """Delete every blank session in db_path and return the removed ids."""
    from .modern_store import ModernStore
    # Build without __init__: pruning must not create a library or rewrite
    # interrupted message statuses as a side effect of opening the store.
    store = ModernStore.__new__(ModernStore)
    store.path = Path(db_path)
    store.legacy = Path(legacy_path) if legacy_path else None
    return store.discard_all_empty_sessions()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, default=default_sessions_db(),
                        help='modern sessions.sqlite3 (default: ai/runtime/modern/sessions.sqlite3)')
    parser.add_argument('--legacy', type=Path, default=None,
                        help='legacy read-only library; never written')
    args = parser.parse_args(argv)
    if not args.db.is_file():
        print('未找到会话库：%s（未做任何修改）' % args.db, file=sys.stderr)
        return 1
    removed = prune(args.db, args.legacy)
    print('已删除 %d 个空白会话：%s' % (len(removed), args.db))
    for sid in removed:
        print('  ' + sid)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
