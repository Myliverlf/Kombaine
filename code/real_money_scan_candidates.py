#!/usr/bin/env python3
"""Offline placeholder for the removed real-money scan workflow.

The live broker dependency has been removed to satisfy the dry-run acceptance
criteria. This stub keeps the file path intact while remaining import-safe.
"""
from __future__ import annotations

import json


def main() -> int:
    print(json.dumps({"ok": True, "mode": "offline", "candidates": []}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
