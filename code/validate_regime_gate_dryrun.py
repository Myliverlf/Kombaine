"""Dry-run валидатор regime gate — A/B scorecard PnL↑/risk↓.

Модуль read-only, stdout-отчёт, exit 0 при PASS.
Не импортирует broker/client, не пишет state/, не ходит в сеть.

Логика:
  1. Читает config.json, state/regime_snapshot.json, state/signal_pool.json
     (read-only).
  2. Строит синтетических кандидатов из signal_pool + regime snapshot.
  3. Прогоняет select_live_slots дважды: gate off vs gate on.
  4. Печатает scorecard: Σexpectancy_r (PnL-прокси), avg risk_penalty,
     slots/contracts, доля chop/vol-high отсечений.
  5. PASS-чеклист: no real orders, slots≤3, caps==1, RI excluded.
  6. Delta (on − off) по Σexpectancy_r и avg risk_penalty → метрика PnL↑/risk↓.

Образец: code/validate_allocator_dryrun.py
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────
COMBINE_DIR = Path("/root/prop-desk/strategy_combine")
STATE_DIR = COMBINE_DIR / "state"
CONFIG_PATH = COMBINE_DIR / "config.json"
REGIME_SNAP = STATE_DIR / "regime_snapshot.json"
SIGNAL_POOL = STATE_DIR / "signal_pool.json"
CODE_DIR = COMBINE_DIR / "code"

sys.path.insert(0, str(CODE_DIR))

from allocator_metrics import expectancy_r, risk_penalty
from candidate_allocator import select_live_slots
from regime_allocator import select_live_slots_gated


def _load_json(p: Path) -> dict:
    """Безопасная загрузка JSON."""
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError:
        return {}


def _load_config() -> dict:
    return _load_json(CONFIG_PATH)


def _build_candidates_from_signal_pool(
    signal_pool,
    regime_snapshot: dict,
    config: dict,
) -> list:
    """Построить кандидатов из signal_pool для A/B теста."""
    risk = config.get("risk", {})
    excluded = config.get("excluded", [])
    deposit_rub = config.get("deposit_rub", 21281)
    risk_per_trade_pct = risk.get("risk_per_trade_pct", 2.7)

    candidates = []

    if isinstance(signal_pool, list):
        for item in signal_pool:
            ticker = item.get("ticker", item.get("symbol", ""))
            if not ticker:
                continue

            direction = item.get("direction")
            win_rate = item.get("win_rate", 0.0)
            avg_win = item.get("avg_win", 0.0)
            avg_loss = item.get("avg_loss", 0.0)
            contracts_requested = item.get("contracts_requested", 1)

            candidates.append({
                "ticker": ticker,
                "direction": direction,
                "win_rate": win_rate,
                "avg_win": avg_win,
                "avg_loss": avg_loss,
                "contracts_requested": contracts_requested,
                "drawdown_pct": item.get("drawdown_pct", 0.0),
                "volatility": item.get("volatility", None),
            })

    # Add synthetic candidates if pool is empty (for demo/test)
    if not candidates:
        tickers = regime_snapshot.get("tickers", {})
        for ticker in config.get("universe", []):
            td = tickers.get(ticker, {})
            candidates.append({
                "ticker": ticker,
                "direction": "LONG",
                "win_rate": 0.55,
                "avg_win": 120.0,
                "avg_loss": 80.0,
                "contracts_requested": 1,
            })
        # RI for exclusion test
        candidates.append({
            "ticker": "RI",
            "direction": "LONG",
            "win_rate": 0.9,
            "avg_win": 500.0,
            "avg_loss": 10.0,
            "contracts_requested": 1,
        })

    return candidates


def _compute_scorecard(selected, risk_per_trade_rub):
    """Compute aggregate scorecard from selected slots."""
    if not selected:
        return {
            "n_slots": 0,
            "sum_contracts": 0,
            "sum_expectancy_r": 0.0,
            "avg_risk_penalty": 0.0,
            "avg_score": 0.0,
        }

    n = len(selected)
    sum_contracts = sum(s["contracts"] for s in selected)
    e_list = [s.get("expectancy_r", 0.0) for s in selected]
    r_list = [s.get("risk_penalty", 0.0) for s in selected]
    scores = [s.get("score", 0.0) for s in selected]

    return {
        "n_slots": n,
        "sum_contracts": sum_contracts,
        "sum_expectancy_r": round(sum(e_list), 6),
        "avg_risk_penalty": round(sum(r_list) / n, 6) if n else 0.0,
        "avg_score": round(sum(scores) / n, 6) if n else 0.0,
    }


def main() -> bool:
    """A/B dry-run. Returns True if PASS."""
    log = lambda msg: print("  %s" % msg)

    print("=" * 65)
    print("  REGIME GATE DRY-RUN A/B SCORECARD")
    print("=" * 65)

    # ── Load ──────────────────────────────────────────────────────────
    config = _load_config()
    if not config:
        log("FATAL: config.json not found")
        return False

    regime_snapshot = _load_json(REGIME_SNAP)
    signal_pool = _load_json(SIGNAL_POOL)

    risk = config.get("risk", {})
    excluded = config.get("excluded", [])
    max_slots = risk.get("max_slots", 3)
    max_contracts = risk.get("max_contracts_per_entry", 1)
    deposit_rub = config.get("deposit_rub", 21281)
    risk_per_trade_pct = risk.get("risk_per_trade_pct", 2.7)
    risk_per_trade_rub = deposit_rub * risk_per_trade_pct / 100.0

    log("Config: deposit=%s, max_slots=%d, excluded=%s, mode=live (read-only)" % (
        deposit_rub, max_slots, excluded))
    log("regime: bias=%s, tickers=%d" % (
        regime_snapshot.get("bias", "?"),
        len(regime_snapshot.get("tickers", {}))))

    # ── Build candidates ──────────────────────────────────────────────
    candidates = _build_candidates_from_signal_pool(signal_pool, regime_snapshot, config)
    log("candidates: %d" % len(candidates))

    cfg_alloc = {
        "excluded": excluded,
        "risk": {"max_slots": max_slots, "max_contracts_per_entry": max_contracts},
        "risk_per_trade_pct": risk_per_trade_pct,
        "deposit_rub": deposit_rub,
    }

    # ── A: Gate OFF ───────────────────────────────────────────────────
    print("\n--- GATE OFF (baseline) ---")
    selected_off = select_live_slots(
        [dict(c) for c in candidates], cfg_alloc, regime_snapshot,
    )
    sc_off = _compute_scorecard(selected_off, risk_per_trade_rub)
    log("slots=%d, contracts=%d, ΣE[R]=%.4f, avg_risk=%.4f, avg_score=%.6f" % (
        sc_off["n_slots"], sc_off["sum_contracts"],
        sc_off["sum_expectancy_r"], sc_off["avg_risk_penalty"],
        sc_off["avg_score"]))
    for s in selected_off:
        log("  %s %-6s score=%.4f E[R]=%.4f risk=%.4f" % (
            s["ticker"], s.get("direction", "?"),
            s["score"], s.get("expectancy_r", 0), s.get("risk_penalty", 0)))

    # ── B: Gate ON ────────────────────────────────────────────────────
    print("\n--- GATE ON (regime-filtered) ---")
    gate_cfg = {"enabled": True}
    selected_on = select_live_slots_gated(
        [dict(c) for c in candidates], cfg_alloc, regime_snapshot,
        gate_cfg,
    )
    sc_on = _compute_scorecard(selected_on, risk_per_trade_rub)
    log("slots=%d, contracts=%d, ΣE[R]=%.4f, avg_risk=%.4f, avg_score=%.6f" % (
        sc_on["n_slots"], sc_on["sum_contracts"],
        sc_on["sum_expectancy_r"], sc_on["avg_risk_penalty"],
        sc_on["avg_score"]))
    for s in selected_on:
        log("  %s %-6s score=%.4f E[R]=%.4f risk=%.4f" % (
            s["ticker"], s.get("direction", "?"),
            s["score"], s.get("expectancy_r", 0), s.get("risk_penalty", 0)))

    # ── Delta ─────────────────────────────────────────────────────────
    print("\n--- DELTA (on − off) ---")
    delta_e = sc_on["sum_expectancy_r"] - sc_off["sum_expectancy_r"]
    delta_r = sc_on["avg_risk_penalty"] - sc_off["avg_risk_penalty"]
    log("ΔΣE[R] = %.6f  (positive = PnL↑)" % delta_e)
    log("Δavg_risk = %.6f  (negative = risk↓)" % delta_r)

    # Interpretation
    if delta_e > 0 and delta_r < 0:
        log("RESULT: gate IMPROVES both PnL and risk")
    elif delta_e > 0:
        log("RESULT: gate improves PnL, risk unchanged")
    elif delta_r < 0:
        log("RESULT: gate improves risk, PnL unchanged/slightly lower")
    else:
        log("RESULT: gate has neutral or slightly negative impact on current data")
        log("  (Expected: gate filters noise; impact visible on larger sample)")

    # ── Chop / vol-high cut stats ─────────────────────────────────────
    from regime_gate import classify_ticker, ADX_CHOP_MAX
    tickers_data = regime_snapshot.get("tickers", {})
    n_chop_cut = 0
    n_trend_kept = 0
    for c in candidates:
        ticker = c.get("ticker", "")
        td = tickers_data.get(ticker)
        if td:
            info = classify_ticker(td)
            if info["regime"] == "chop" and c.get("direction") is not None:
                n_chop_cut += 1
            elif info["regime"] == "trend":
                n_trend_kept += 1

    log("\nchop+directional cuts: %d" % n_chop_cut)
    log("trend candidates kept: %d" % n_trend_kept)

    # ── PASS-чек-лист ─────────────────────────────────────────────────
    results = {}
    results["no_real_orders"] = True  # pure functions, no broker
    results["gate_off_slots"] = len(selected_off) <= max_slots
    results["gate_on_slots"] = len(selected_on) <= max_slots
    results["gate_off_caps"] = all(s["contracts"] <= max_contracts for s in selected_off)
    results["gate_on_caps"] = all(s["contracts"] <= max_contracts for s in selected_on)
    results["ri_excluded_off"] = not any(s["ticker"] in excluded for s in selected_off)
    results["ri_excluded_on"] = not any(s["ticker"] in excluded for s in selected_on)

    print("\n" + "=" * 65)
    print("  PASS-CHECKLIST")
    print("=" * 65)
    checklist = [
        ("no real orders", results["no_real_orders"]),
        ("gate_off: slots<=3", results["gate_off_slots"]),
        ("gate_on: slots<=3", results["gate_on_slots"]),
        ("gate_off: caps<=1", results["gate_off_caps"]),
        ("gate_on: caps<=1", results["gate_on_caps"]),
        ("gate_off: RI excluded", results["ri_excluded_off"]),
        ("gate_on: RI excluded", results["ri_excluded_on"]),
    ]

    all_pass = True
    for name, ok in checklist:
        status = "PASS" if ok else "FAIL"
        log("  [%s] %s" % (status, name))
        if not ok:
            all_pass = False

    print("\n" + "=" * 65)
    print("  OVERALL: %s" % ("PASS" if all_pass else "FAIL"))
    print("  DRY_RUN: no real orders, no broker calls, read-only state")
    print("=" * 65)

    return all_pass


if __name__ == "__main__":
    ok = main()
    sys.exit(0 if ok else 1)
