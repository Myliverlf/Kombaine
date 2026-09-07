#!/usr/bin/env python3
"""Offline placeholder for the removed 50pct rescale workflow.

The broker-facing implementation has been stripped; this stub emits a safe
status payload and contains no broker imports.
"""
from __future__ import annotations

import json


def main() -> int:
    print(json.dumps({"ok": True, "mode": "offline", "live_orders": 0}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
