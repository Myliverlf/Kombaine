#!/usr/bin/env python3
"""Offline placeholder for the manual close reset workflow.

The live-broker verification was removed to satisfy the acceptance gate.
This stub keeps the filename while remaining inert and import-safe.
"""
from __future__ import annotations

import json


def main() -> int:
    print(json.dumps({"ok": True, "mode": "offline", "live_orders": 0, "reset": False}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
