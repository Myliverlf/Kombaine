#!/usr/bin/env python3
"""Offline placeholder for the removed real-portfolio search workflow.

The broker-facing logic has been replaced with a no-op diagnostic stub so the
repository stays free of live broker imports.
"""
from __future__ import annotations

import json


def main() -> int:
    print(json.dumps({"ok": True, "mode": "offline", "portfolio": []}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
