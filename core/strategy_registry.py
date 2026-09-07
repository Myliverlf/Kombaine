"""Live bridge to the adaptive strategy registry implementation.

The production ``core/`` package imports this module so the live supervisor and
seeder can use the same adaptive registry logic as the tests in ``code/``.

The adaptive registry (strategy_registry.json) is the CANONICAL SOURCE OF TRUTH
for all strategy state.  Legacy files (waitlist.json, signal_pool.json) are
derived views exported from the registry for backward compatibility.
"""
import importlib.util
import sys
from pathlib import Path

_CODE_DIR = Path(__file__).resolve().parent.parent / "code"
_CODE_MODULE = _CODE_DIR / "strategy_registry.py"

# Load the canonical code/strategy_registry.py directly to avoid
# module-name collision (both core/ and code/ have strategy_registry.py).
_spec = importlib.util.spec_from_file_location("_canonical_strategy_registry", str(_CODE_MODULE))
_mod = importlib.util.module_from_spec(_spec)
sys.modules["_canonical_strategy_registry"] = _mod
_spec.loader.exec_module(_mod)

# Re-export every public name from the canonical module
for _name in dir(_mod):
    if not _name.startswith("_"):
        globals()[_name] = getattr(_mod, _name)

del _spec, _mod, _name, _CODE_DIR, _CODE_MODULE  # clean up namespace
