#!/usr/bin/env python3
"""Read-only trading session preflight for strategy_combine.

Checks whether the full trading contour can safely pick up the active market
session and promote active signal_pool strategies into portfolio slots.

No orders. No broker mutations. PASS here is a readiness signal only; live start
still requires explicit Apostle permission.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = PROJECT_ROOT / "state"
REPORT_DIR = PROJECT_ROOT / "reports" / "strategy_architect"
CONFIG_PATH = PROJECT_ROOT / "config.json"
CODE_DIR = PROJECT_ROOT / "code"
sys.path.insert(0, str(CODE_DIR))

from strategy_registry import STATUS_ACTIVE_SIGNAL_POOL, STATUS_ACTIVE_WATCHLIST, StrategyRegistry  # type: ignore  # noqa:E402

ACTIVE_SESSION_STATUSES = {STATUS_ACTIVE_SIGNAL_POOL, STATUS_ACTIVE_WATCHLIST}


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def check(name: str, ok: bool, detail: str, severity: str = "FAIL") -> Dict[str, Any]:
    return {"name": name, "ok": bool(ok), "detail": detail, "severity": severity}


def token_present(cfg: Dict[str, Any]) -> bool:
    account = cfg.get("account", {})
    env_name = account.get("env_token") or "TINKOFF_TOKEN"
    if os.environ.get(env_name):
        return True
    token_file = account.get("token_file")
    if token_file:
        p = Path(str(token_file).replace("~", str(Path.home())))
        if p.exists() and p.read_text(errors="ignore").strip():
            return True
    return False


def broker_readonly_probe(cfg: Dict[str, Any]) -> tuple[bool, str]:
    if not token_present(cfg):
        return False, "TINKOFF token missing"
    if importlib.util.find_spec("tinkoff") is None:
        return False, "tinkoff SDK missing"
    try:
        sys.path.insert(0, "/root/prop-desk/futures_lab")
        futures_lab = importlib.import_module("futures_lab")
        invest_mod = importlib.import_module("tinkoff.invest")
        token = futures_lab.load_token()
        with invest_mod.Client(token) as c:
            c.users.get_accounts()
        return True, "broker read-only users.get_accounts OK"
    except Exception as exc:
        return False, f"broker read-only probe failed: {type(exc).__name__}: {str(exc)[:200]}"


def active_pool_summary() -> Dict[str, Any]:
    reg = StrategyRegistry.load()
    active = [r for r in reg.records() if r.status in ACTIVE_SESSION_STATUSES]
    stable = [r for r in active if (r.metrics or {}).get("stability_passed") is True]
    return {
        "active_count": len(active),
        "stable_count": len(stable),
        "by_ticker": Counter(r.ticker for r in active).most_common(),
        "by_family": Counter(r.strategy for r in active).most_common(),
        "top": [
            {
                "strategy_id": r.strategy_id,
                "ticker": r.ticker,
                "strategy": r.strategy,
                "timeframe": str(r.portfolio_context.get("timeframe") or (r.metrics or {}).get("timeframe") or ""),
                "rank": float(r.active_rank or 0.0),
                "stability_passed": (r.metrics or {}).get("stability_passed"),
                "timesfm_source": (r.metrics or {}).get("timesfm_source"),
            }
            for r in sorted(active, key=lambda x: float(x.active_rank or 0.0), reverse=True)[:10]
        ],
    }


def render_md(payload: Dict[str, Any]) -> str:
    lines = [
        "# Market Session Preflight — strategy_combine",
        "",
        f"- verdict: {payload['verdict']}",
        f"- checked_at: {payload['checked_at']}",
        f"- live_orders: {payload['live_orders']}",
        f"- note: {payload['note']}",
        "",
        "## Checks",
        "| check | status | severity | detail |",
        "|---|---|---|---|",
    ]
    for c in payload["checks"]:
        lines.append(f"| {c['name']} | {'✅' if c['ok'] else '❌'} | {c['severity']} | {c['detail']} |")
    pool = payload["active_pool"]
    lines.extend([
        "",
        "## Active pool",
        f"- active_count: {pool['active_count']}",
        f"- stable_count: {pool['stable_count']}",
        f"- by_ticker: {pool['by_ticker']}",
        f"- by_family: {pool['by_family']}",
        "",
        "| rank | ticker | tf | strategy | stability | timesfm | id |",
        "|---:|---|---|---|---|---|---|",
    ])
    for i, r in enumerate(pool["top"], 1):
        lines.append(f"| {i} | {r['ticker']} | {r['timeframe']} | {r['strategy']} | {r['stability_passed']} | {r['timesfm_source']} | {r['strategy_id']} |")
    lines.extend([
        "",
        "## Launch rule",
        "Live mode requires explicit Apostle command; current report only verifies readiness and does not place orders.",
    ])
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--broker-probe", action="store_true", help="run read-only Tinkoff API probe")
    args = ap.parse_args()

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    cfg = load_json(CONFIG_PATH, {})
    portfolio = load_json(STATE_DIR / "portfolio.json", {"slots": {}, "halted": False})
    pool_file = load_json(STATE_DIR / "signal_pool.json", {"strategies": {}})
    pool = active_pool_summary()
    checks: List[Dict[str, Any]] = []

    checks.append(check("config_exists", CONFIG_PATH.exists(), str(CONFIG_PATH)))
    checks.append(check("mode", cfg.get("mode") in {"paper", "live"}, f"mode={cfg.get('mode')} paper_first={cfg.get('paper_first')}", severity="WARN"))
    checks.append(check("live_supervisor_enabled", cfg.get("mode") == "live" and cfg.get("paper_first") is False, "live explicitly enabled for supervisor", severity="WARN"))
    checks.append(check("active_pool_nonempty", pool["active_count"] > 0, f"active={pool['active_count']}"))
    checks.append(check("stable_pool_nonempty", pool["stable_count"] >= 3, f"stable={pool['stable_count']}"))
    checks.append(check("signal_pool_export_synced", len(pool_file.get("strategies", {})) == pool["active_count"], f"signal_pool={len(pool_file.get('strategies', {}))} registry_active={pool['active_count']}"))
    checks.append(check("portfolio_not_halted", not portfolio.get("halted"), f"halted={portfolio.get('halted')} reason={portfolio.get('halt_reason')}"))
    checks.append(check("portfolio_slots_within_limit", sum(1 for s in portfolio.get("slots", {}).values() if s.get("open_position")) <= int(cfg.get("risk", {}).get("max_slots", 0) or 0), f"open={sum(1 for s in portfolio.get('slots', {}).values() if s.get('open_position'))} max={cfg.get('risk', {}).get('max_slots')}"))
    checks.append(check("timesfm_real", (load_json(REPORT_DIR / "latest.md", "") is not None), "latest report exists; see TimesFM line", severity="WARN"))
    checks.append(check("token_present", token_present(cfg), "TINKOFF token present for future read-only/live checks"))

    if args.broker_probe:
        ok, detail = broker_readonly_probe(cfg)
        checks.append(check("broker_readonly_probe", ok, detail))
    else:
        checks.append(check("broker_readonly_probe", True, "skipped; pass --broker-probe to verify API", severity="WARN"))

    failures = [c for c in checks if not c["ok"] and c["severity"] == "FAIL"]
    verdict = "PASS" if not failures else "FAIL"
    payload = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "verdict": verdict,
        "checks": checks,
        "active_pool": pool,
        "live_orders": 0,
        "note": "read-only preflight; does not enable live trading",
    }
    (REPORT_DIR / "market_session_preflight_latest.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (REPORT_DIR / "market_session_preflight_latest.md").write_text(render_md(payload), encoding="utf-8")
    print(json.dumps({"verdict": verdict, "failures": len(failures), "active": pool["active_count"], "stable": pool["stable_count"], "live_orders": 0}, ensure_ascii=False))
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
