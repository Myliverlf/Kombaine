"""Live bridge to the adaptive replacement policy implementation.

FIX 2026-09-02: the previous ``from strategy_replacement_policy import *``
self-resolved to THIS file when ``core/`` was early on sys.path (silent
self-import -> zero names exported).  Now the code/ module is always loaded
explicitly by file spec under a canonical name, then its public names are
copied here.
"""
import sys
from pathlib import Path

_CODE_DIR = str(Path(__file__).resolve().parent.parent / "code")
if _CODE_DIR not in sys.path:
    sys.path.insert(0, _CODE_DIR)

_CANON = "_canonical_strategy_replacement_policy"
_TARGET = str(Path(__file__).resolve().parent.parent / "code" / "strategy_replacement_policy.py")

if _CANON in sys.modules:
    _mod = sys.modules[_CANON]
else:
    import importlib.util as _ilu
    _spec = _ilu.spec_from_file_location(_CANON, _TARGET)
    _mod = _ilu.module_from_spec(_spec)
    sys.modules[_CANON] = _mod
    _spec.loader.exec_module(_mod)

for _name in dir(_mod):
    if not _name.startswith("_"):
        globals()[_name] = getattr(_mod, _name)
del _name, _mod, _CODE_DIR, _TARGET
