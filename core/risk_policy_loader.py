"""
risk_policy_loader.py — Loads and enforces LIVE_RISK_V1 policy.

Fail-closed: raises on missing/invalid policy.
Provides:
  - get_risk_policy() -> dict
  - check_risk_qualification(candidate) -> dict  {passed: bool, violations: list}
  - risk_policy_hash() -> str
  - risk_policy_version() -> str
"""

import hashlib
import json
import os
from pathlib import Path

# ── Path resolution ──────────────────────────────────────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_POLICY_PATH = _PROJECT_ROOT / "state" / "live_risk" / "LIVE_RISK_V1.json"

# ── Singleton cache ──────────────────────────────────────────────────────────
_policy_cache: dict | None = None
_policy_raw_bytes: bytes | None = None


def _load_raw() -> bytes:
    """Read the raw JSON file bytes; raise if missing."""
    if not _POLICY_PATH.exists():
        raise FileNotFoundError(
            f"LIVE_RISK_V1 policy file not found at {_POLICY_PATH} — FAIL-CLOSED"
        )
    return _POLICY_PATH.read_bytes()


def _normalize(data: dict) -> dict:
    """
    Flatten nested sub-objects into top-level convenience keys.
    The LIVE_RISK_V1.json nests limits inside sub-objects; we expose
    them at the top level as well so callers get a consistent interface.
    """
    d = dict(data)  # shallow copy

    # max_risk_per_trade_pct  ← per_trade_risk_limit.max_risk_per_trade_pct
    if "max_risk_per_trade_pct" not in d:
        ptrl = d.get("per_trade_risk_limit", {})
        d["max_risk_per_trade_pct"] = ptrl.get("max_risk_per_trade_pct", 0)

    # max_gross_exposure_pct  ← gross_exposure_limit.max_gross_exposure_pct
    if "max_gross_exposure_pct" not in d:
        gel = d.get("gross_exposure_limit", {})
        d["max_gross_exposure_pct"] = gel.get("max_gross_exposure_pct", 0)

    return d


def _parse_policy(raw: bytes) -> dict:
    """Parse and basic-validate the JSON; raise on corruption."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"LIVE_RISK_V1.json is not valid JSON — FAIL-CLOSED: {exc}"
        ) from exc

    # Normalize nested keys to flat top-level keys
    data = _normalize(data)

    required_top = [
        "policy_id",
        "max_risk_per_trade_pct",
        "max_gross_exposure_pct",
        "max_concurrent_live_positions",
        "shorting_allowed",
        "leverage_allowed",
        "averaging_down_allowed",
        "pyramiding_allowed",
    ]
    missing = [k for k in required_top if k not in data]
    if missing:
        raise ValueError(
            f"LIVE_RISK_V1.json missing required keys {missing} — FAIL-CLOSED"
        )

    if data["policy_id"] != "LIVE_RISK_V1":
        raise ValueError(
            f"policy_id is {data['policy_id']!r}, expected 'LIVE_RISK_V1' — FAIL-CLOSED"
        )

    return data


def _ensure_loaded() -> dict:
    """Load once, cache, return the policy dict."""
    global _policy_cache, _policy_raw_bytes
    if _policy_cache is None:
        _policy_raw_bytes = _load_raw()
        _policy_cache = _parse_policy(_policy_raw_bytes)
    return _policy_cache


# ── Public API ───────────────────────────────────────────────────────────────

def get_risk_policy() -> dict:
    """Return the full LIVE_RISK_V1 policy dict (cached)."""
    return dict(_ensure_loaded())


def risk_policy_hash() -> str:
    """SHA-256 hex digest of the raw JSON file bytes."""
    raw = _load_raw()
    return hashlib.sha256(raw).hexdigest()


def risk_policy_version() -> str:
    """Return the version string from the policy."""
    return str(_ensure_loaded()["version"])


def check_risk_qualification(candidate: dict) -> dict:
    """
    Validate a trade candidate dict against LIVE_RISK_V1 rules.

    Candidate keys expected:
      - side: "long" | "short"
      - leverage: bool
      - risk_per_trade_pct: float  (planned max loss as % of equity)
      - concurrent_live_positions: int
      - averaging_down: bool       (optional, default False)
      - pyramiding: bool           (optional, default False)
      - risk_identity: str         (optional; if missing, blocked)

    Returns:
      {"passed": bool, "violations": [str, ...]}
    """
    p = _ensure_loaded()
    violations: list[str] = []

    # --- side check ---
    side = str(candidate.get("side", "")).lower()
    if side == "short" and not p["shorting_allowed"]:
        violations.append("shorting not allowed (shorting_allowed=False)")

    # --- leverage check ---
    leverage = bool(candidate.get("leverage", False))
    if leverage and not p["leverage_allowed"]:
        violations.append("leverage not allowed (leverage_allowed=False)")

    # --- risk per trade ---
    risk_pct = candidate.get("risk_per_trade_pct", 0)
    if risk_pct > p["max_risk_per_trade_pct"]:
        violations.append(
            f"risk_per_trade_pct {risk_pct} exceeds max {p['max_risk_per_trade_pct']}"
        )

    # --- concurrent positions ---
    concurrent = candidate.get("concurrent_live_positions", 1)
    if concurrent > p["max_concurrent_live_positions"]:
        violations.append(
            f"concurrent_live_positions {concurrent} exceeds max {p['max_concurrent_live_positions']}"
        )

    # --- averaging down ---
    if bool(candidate.get("averaging_down", False)) and not p["averaging_down_allowed"]:
        violations.append("averaging down not allowed (averaging_down_allowed=False)")

    # --- pyramiding ---
    if bool(candidate.get("pyramiding", False)) and not p["pyramiding_allowed"]:
        violations.append("pyramiding not allowed (pyramiding_allowed=False)")

    # --- risk_identity (fail-closed) ---
    if not candidate.get("risk_identity"):
        violations.append("missing risk_identity — FAIL-CLOSED")

    return {"passed": len(violations) == 0, "violations": violations}
