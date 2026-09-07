"""Pytest bootstrap for strategy_combine.

Ensure the project root is on sys.path so tests can import sibling packages
such as core/ when pytest runs in importlib mode.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
root_str = str(ROOT)
if root_str not in sys.path:
    sys.path.insert(0, root_str)
