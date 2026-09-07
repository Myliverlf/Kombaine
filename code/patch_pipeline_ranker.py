"""Patch pipeline_ranker.py — замена import build_scorecard на bridge.

Одноразовый скрипт, который правит одну строку в pipeline_ranker.py:
  FROM: from risk_scorecard import build_scorecard
  TO:   from risk_scorecard_bridge import build_scorecard

После запуска pipeline_ranker импортирует bridge-функцию и Bug 1 исчезает.

Usage:
    python3 code/patch_pipeline_ranker.py
    # или
    python3 code/patch_pipeline_ranker.py --dry-run  # показать что будет заменено
"""
import sys
import os
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_COMBINE = _HERE.parent
_PIPELINE = _HERE / "pipeline_ranker.py"  # pipeline_ranker.py is inside code/

OLD_IMPORT = "from risk_scorecard import build_scorecard"
NEW_IMPORT = "from risk_scorecard_bridge import build_scorecard"


def patch_pipeline_ranker(dry_run: bool = False) -> bool:
    """Патчит pipeline_ranker.py, заменяя import.

    Returns: True if patched or already patched, False on error.
    """
    if not _PIPELINE.exists():
        print("ERROR: %s not found" % _PIPELINE)
        return False

    content = _PIPELINE.read_text(encoding="utf-8")

    # Already patched?
    if NEW_IMPORT in content:
        print("ALREADY PATCHED: '%s' found in pipeline_ranker.py" % NEW_IMPORT)
        return True

    # Old import not found?
    if OLD_IMPORT not in content:
        print("WARNING: old import '%s' not found in pipeline_ranker.py" % OLD_IMPORT)
        print("Pipeline may have been modified. Check manually.")
        return False

    if dry_run:
        print("DRY RUN: would replace:")
        print("  FROM: %s" % OLD_IMPORT)
        print("  TO:   %s" % NEW_IMPORT)
        return True

    # Patch
    new_content = content.replace(OLD_IMPORT, NEW_IMPORT, 1)
    _PIPELINE.write_text(new_content, encoding="utf-8")

    # Verify
    verify = _PIPELINE.read_text(encoding="utf-8")
    if NEW_IMPORT in verify:
        print("PATCHED OK: pipeline_ranker.py now imports from risk_scorecard_bridge")
        return True
    else:
        print("ERROR: patch written but verification failed")
        return False


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    ok = patch_pipeline_ranker(dry_run=dry_run)
    sys.exit(0 if ok else 1)
