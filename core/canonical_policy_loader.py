"""Canonical policy loader for the research qualification pipeline.

Loads config/research_qualification_policy.json and enforces:
  - Fail-closed: raises ValueError if file is missing or unparseable
  - Guard rails: verifies mode=paper, paper_first=true, LIVE_EXECUTE=denied
    from the project-level config.json

All qualification thresholds are read from the single source-of-truth JSON,
eliminating the hardcoded constants previously scattered across the codebase.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Optional


# ---------------------------------------------------------------------------
# Path resolution — mirrors conftest.py: project root is two levels up from
# this file (core/canonical_policy_loader.py → strategy_combine/)
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent.parent

_POLICY_PATH = _PROJECT_ROOT / "config" / "research_qualification_policy.json"
_CONFIG_PATH = _PROJECT_ROOT / "config.json"


# ---------------------------------------------------------------------------
# Module-level cache (loaded once per process, safe for import-time usage)
# ---------------------------------------------------------------------------
_policy_cache: Optional[Dict[str, Any]] = None
_policy_raw_bytes: Optional[bytes] = None


def _load_policy() -> Dict[str, Any]:
    """Load and cache the qualification policy JSON.  Fail-closed."""
    global _policy_cache, _policy_raw_bytes

    if _policy_cache is not None:
        return _policy_cache

    if not _POLICY_PATH.exists():
        raise ValueError(
            f"Canonical policy file not found: {_POLICY_PATH}. "
            "Pipeline cannot proceed without it (fail-closed)."
        )

    try:
        _policy_raw_bytes = _POLICY_PATH.read_bytes()
        _policy_cache = json.loads(_policy_raw_bytes)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(
            f"Canonical policy file is invalid ({_POLICY_PATH}): {exc}"
        ) from exc

    # Basic shape validation
    if not isinstance(_policy_cache, dict):
        raise ValueError("Policy JSON root must be a dict")

    if "thresholds" not in _policy_cache:
        raise ValueError("Policy JSON is missing required 'thresholds' section")

    return _policy_cache


# ---------------------------------------------------------------------------
# Config.json safety checks
# ---------------------------------------------------------------------------

def _load_config_json() -> Dict[str, Any]:
    """Load project-level config.json.  Fail-closed."""
    if not _CONFIG_PATH.exists():
        raise ValueError(
            f"config.json not found at {_CONFIG_PATH}. "
            "Cannot verify safety guardrails."
        )
    try:
        return json.loads(_CONFIG_PATH.read_bytes())
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(
            f"config.json is invalid: {exc}"
        ) from exc


def verify_safety_guardrails() -> None:
    """Verify three safety invariants from config.json.

    Raises ValueError if any guardrail is violated:
      1. mode must be 'paper'
      2. paper_first must be true
      3. LIVE_EXECUTE must be absent or explicitly 'denied'
    """
    cfg = _load_config_json()

    # 1. mode == paper
    mode = cfg.get("mode")
    if mode != "paper":
        raise ValueError(
            f"Safety guardrail violated: mode={mode!r} (must be 'paper'). "
            "LIVE trading is NOT permitted."
        )

    # 2. paper_first == true
    paper_first = cfg.get("paper_first")
    if paper_first is not True:
        raise ValueError(
            f"Safety guardrail violated: paper_first={paper_first!r} (must be true)."
        )

    # 3. LIVE_EXECUTE must be absent or 'denied'
    live_execute = cfg.get("LIVE_EXECUTE")
    if live_execute is not None and live_execute != "denied":
        raise ValueError(
            f"Safety guardrail violated: LIVE_EXECUTE={live_execute!r} "
            "(must be absent or 'denied')."
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_qualification_policy() -> Dict[str, Any]:
    """Return the full qualification policy dict."""
    return _load_policy()


def get_threshold(name: str) -> Any:
    """Return a single threshold value by name.

    Example::

        get_threshold("min_trades")  # -> 8
    """
    policy = _load_policy()
    thresholds = policy["thresholds"]
    if name not in thresholds:
        available = ", ".join(sorted(thresholds.keys()))
        raise KeyError(
            f"Unknown threshold '{name}'. Available: {available}"
        )
    return thresholds[name]


def get_cost_model() -> Dict[str, Any]:
    """Return the cost model section of the policy."""
    policy = _load_policy()
    if "cost_model" not in policy:
        raise ValueError("Policy is missing 'cost_model' section")
    return policy["cost_model"]


def get_stages() -> Dict[str, Any]:
    """Return the qualification stages section of the policy."""
    policy = _load_policy()
    if "stages" not in policy:
        raise ValueError("Policy is missing 'stages' section")
    return policy["stages"]


def policy_hash() -> str:
    """Return the SHA-256 hex digest of the policy JSON file."""
    _load_policy()  # ensure loaded
    assert _policy_raw_bytes is not None
    return hashlib.sha256(_policy_raw_bytes).hexdigest()


def policy_version() -> str:
    """Return the policy version string."""
    policy = _load_policy()
    version = policy.get("version")
    if version is None:
        raise ValueError("Policy is missing 'version' field")
    return str(version)


# ---------------------------------------------------------------------------
# Convenience: load once at import, verify guardrails
# ---------------------------------------------------------------------------

def initialize() -> Dict[str, Any]:
    """Full initialization: load policy + verify safety guardrails.

    Call this at application startup.  Raises ValueError on any failure.
    Returns the loaded policy dict on success.
    """
    verify_safety_guardrails()
    return _load_policy()
