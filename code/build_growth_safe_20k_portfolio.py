#!/usr/bin/env python3
"""Offline placeholder for the removed growth-safe portfolio builder.

The original workflow depended on live broker specs. This stub remains
pure and safe for AST scans.
"""
from __future__ import annotations

import json


def main() -> int:
    print(json.dumps({"ok": True, "mode": "offline", "portfolio": []}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
