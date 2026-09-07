#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path('/root/prop-desk/strategy_combine')
REGISTRY = ROOT / 'state' / 'strategy_registry.json'
BACKUP = ROOT / 'state' / 'strategy_registry.json.bak2'
ARCHIVE_DIR = ROOT / 'reports' / 'archive'


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    if not REGISTRY.exists():
        raise SystemExit(f'missing registry: {REGISTRY}')

    data = json.loads(REGISTRY.read_text(encoding='utf-8'))
    if not isinstance(data, dict) or 'strategies' not in data:
        raise SystemExit('unexpected registry shape; expected top-level object with strategies')

    strategies = data.get('strategies', {})
    if not isinstance(strategies, dict):
        raise SystemExit('registry.strategies must be an object')

    rotated_keys = [k for k, v in strategies.items() if isinstance(v, dict) and v.get('status') == 'rotated_out']
    keep_keys = [k for k in strategies.keys() if k not in rotated_keys]
    removed = {k: strategies[k] for k in rotated_keys}

    ts = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    archive_path = ARCHIVE_DIR / f'registry_rotated_out_{ts}.json'
    payload = {
        'archived_at': datetime.now(timezone.utc).isoformat(),
        'source_registry': str(REGISTRY),
        'source_backup': str(BACKUP),
        'removed_count': len(removed),
        'removed_keys': rotated_keys,
        'removed_entries': removed,
    }

    if args.dry_run:
        print(json.dumps({'removed_count': len(removed), 'archive_path': str(archive_path)}, ensure_ascii=False))
        return 0

    shutil.copy2(REGISTRY, BACKUP)
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    archive_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')

    data['strategies'] = {k: strategies[k] for k in keep_keys}
    data['updated_ts'] = datetime.now(timezone.utc).timestamp()
    REGISTRY.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=False), encoding='utf-8')

    print(json.dumps({'backup': str(BACKUP), 'archive': str(archive_path), 'removed_count': len(removed), 'kept_count': len(keep_keys)}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
