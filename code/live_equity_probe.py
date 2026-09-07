#!/usr/bin/env python3
"""Offline placeholder for the removed live equity probe.

The original script required broker connectivity and violated the dry-run
acceptance gate. It is kept as a no-op diagnostic stub so the file remains
importable and the repository stays free of live broker imports.
"""
from __future__ import annotations

import json


def main() -> int:
    print(json.dumps({"ok": True, "mode": "offline", "broker": None, "equity": None}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
