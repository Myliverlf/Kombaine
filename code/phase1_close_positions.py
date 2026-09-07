#!/usr/bin/env python3
"""Offline placeholder for manual close workflow.

Live order placement has been removed to satisfy the no-live-orders gate.
Use the dry-run autocontour pipeline instead.
"""
from __future__ import annotations

import json


def main() -> int:
    print(json.dumps({"ok": True, "mode": "offline", "live_orders": 0, "changed": False}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
