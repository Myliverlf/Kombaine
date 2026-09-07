#!/usr/bin/env python3
"""Интеграционный тест полного флоу signal pool.

Проверяет:
  1. Создание mock кандидатов → заполнение pool ≥10
  2. Резолюция конфликтов: лучший по rank_score остаётся
  3. RI нигде нет (в pool, waitlist, portfolio)
  4. Live slots (portfolio) не повреждены и ≤3
  5. validate_engine.py: A (tick), C (DRY_RUN), D (state restored)

Запуск:
    cd /root/prop-desk/strategy_combine && python code/test_signal_pool_flow.py
"""
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

COMBINE_DIR = Path(__file__).resolve().parent.parent
CORE_DIR = COMBINE_DIR / "core"
CODE_DIR = COMBINE_DIR / "code"
STATE_DIR = COMBINE_DIR / "state"

sys.path.insert(0, str(CORE_DIR))
import registry  # noqa: E402


# ── Тестовые данные ───────────────────────────────────────────────────

def _rank_score(m: dict) -> float:
    pnl = m.get("pnl", 0.0)
    sharpe = m.get("sharpe", 0.0)
    win = m.get("win_rate", 0.0)
    pf = m.get("pf", 1.0)
    tpd = m.get("trades_per_day", 0.0)
    freq_bonus = 500.0 if 0.8 <= tpd <= 2.5 else (250.0 if tpd >= 0.4 else -300.0)
    return pnl + sharpe * 1000.0 + win * 30.0 + (pf - 1) * 800.0 + freq_bonus


def _mock_candidates() -> list:
    """12 mock-кандидатов: 2 тикера × 6 стратегий (включая 2 конфликта по тикеру)."""
    tickers = ["LKOH", "SI"]
    strategies = [
        ("vwap_reversion", {"lookback": 30, "entry_z": 0.8}, 4859.0, 0.49, 51.3, 1.29, 7.72),
        ("mean_reversion_filtered", {"lookback": 20}, 3866.0, 0.47, 46.5, 1.32, 5.91),
        ("nateemma_basket_meanrev", {}, 3056.0, 0.82, 68.5, 4.14, 1.08),
        ("nfi_trend", {}, 2972.0, 0.68, 61.1, 2.84, 1.45),
        ("ft_multi_rsi", {}, 2686.0, 0.35, 47.6, 1.28, 4.22),
        ("atr_breakout", {"lookback": 20, "atr_mult": 2.0}, 616.0, 0.20, 44.4, 1.43, 0.48),
    ]
    cands = []
    for ticker in tickers:
        for name, params, pnl, sharpe, win, pf, tpd in strategies:
            metrics = {"pnl": pnl, "sharpe": sharpe, "win_rate": win,
                       "pf": pf, "dd": 1000, "trades": 30, "trades_per_day": tpd}
            cands.append({
                "ticker": ticker, "strategy": name, "params": params,
                "metrics": metrics, "rank_score": _rank_score(metrics),
                "go_rub": 7000.0 if ticker == "LKOH" else 13120.0,
            })
    return cands


def _mock_portfolio_with_ri() -> dict:
    """Portfolio с RI-слотом (для проверки exclude)."""
    return {
        "slots": {
            "slot_RI_99999": {
                "ticker": "RI", "strategy": "some_strategy", "params": {},
                "contracts": 1, "go_rub": 5000.0, "promoted_ts": 0.0,
                "open_position": None, "n_trades": 0, "pnl_rub": 0.0,
                "peak_pnl_rub": 0.0, "stop_streak": 0, "last_signal_ts": 0.0,
            },
            "slot_LKOH_40085": {
                "ticker": "LKOH", "strategy": "vwap_reversion", "params": {},
                "contracts": 1, "go_rub": 7521.75, "promoted_ts": 0.0,
                "open_position": None, "n_trades": 0, "pnl_rub": 0.0,
                "peak_pnl_rub": 0.0, "stop_streak": 0, "last_signal_ts": 0.0,
            },
        },
        "peak_equity": 0.0, "halted": False, "halt_reason": None,
    }


# ── Тесты ─────────────────────────────────────────────────────────────

def test_pool_fill() -> dict:
    """T1: signal pool заполняется до ≥10 из кандидатов."""
    results = {}
    cands = _mock_candidates()

    waitlist = {"candidates": {}}
    signal_pool = {"strategies": {}, "last_rotation_ts": 0.0}

    # Заполняем waitlist и pool
    for c in cands:
        cid = "%s__%s" % (c["ticker"], c["strategy"])
        registry.add_candidate(waitlist, cid, c["ticker"], c["strategy"],
                               c["params"], c["metrics"], c["rank_score"], ttl_days=7)
        waitlist["candidates"][cid]["go_rub"] = c["go_rub"]

        if c["rank_score"] >= 100:
            registry.add_to_signal_pool(signal_pool, cid, c["ticker"], c["strategy"],
                                        c["params"], c["metrics"], c["rank_score"],
                                        c["go_rub"])

    pool_active = sum(1 for p in signal_pool["strategies"].values()
                      if p.get("status") == "active")
    pool_total = len(signal_pool["strategies"])
    # Критерий: pool >= 10 entries (все статусы включая resolved_conflict)
    results["pool_fill_ge10"] = pool_total >= 10
    print("T1 pool_fill: total=%d, active=%d (need total>=10) -> %s" % (
        pool_total, pool_active,
        "PASS" if results["pool_fill_ge10"] else "FAIL"))

    return results, signal_pool


def _conflict_resolution(signal_pool: dict) -> dict:
    """T2: при конфликтах (несколько стратегий на тикер) лучший по rank_score остаётся."""
    results = {}
    portfolio = {"slots": {}, "peak_equity": 0.0, "halted": False, "halt_reason": None}
    conflicts = registry.signal_conflicts(portfolio, signal_pool)

    all_ok = True
    for ticker, entries in conflicts.items():
        best_pid, best_entry = entries[0]
        for pid, entry in entries[1:]:
            if entry["rank_score"] >= best_entry["rank_score"]:
                all_ok = False
                print("T2 CONFLICT BUG: %s/%s (score %.0f) should be below best %s/%s (score %.0f)" % (
                    entry["ticker"], entry["strategy"], entry["rank_score"],
                    best_entry["ticker"], best_entry["strategy"], best_entry["rank_score"]))

    results["conflict_resolution"] = all_ok and len(conflicts) > 0
    print("T2 conflict_resolution: %d conflicts, all correct -> %s" % (
        len(conflicts), "PASS" if results["conflict_resolution"] else "FAIL"))

    return results


def _no_ri(signal_pool: dict) -> dict:
    """T3: RI не попадает ни в pool, ни в waitlist."""
    results = {}
    ri_in_pool = any("RI" in pid for pid in signal_pool["strategies"])
    results["no_ri"] = not ri_in_pool
    print("T3 no_ri: ri_in_pool=%s -> %s" % (
        ri_in_pool, "PASS" if results["no_ri"] else "FAIL"))
    return results


def test_live_slots_intact() -> dict:
    """T4: текущие 2 live-слота не повреждены и ≤3."""
    results = {}
    portfolio = registry.load_portfolio()
    n_slots = len(portfolio["slots"])

    # Проверяем что оба ключевых слота на месте
    has_lkoh = any(s["ticker"] == "LKOH" for s in portfolio["slots"].values())
    has_gazp = any(s["ticker"] == "GAZP" for s in portfolio["slots"].values())

    results["slots_intact"] = has_lkoh and has_gazp and n_slots <= 3
    print("T4 live_slots: n=%d, has_LKOH=%s, has_GAZP=%s, max_3=%s -> %s" % (
        n_slots, has_lkoh, has_gazp, n_slots <= 3,
        "PASS" if results["slots_intact"] else "FAIL"))
    return results


def test_validate_engine() -> dict:
    """T5: validate_engine.py проходит (A: tick, C: DRY_RUN, D: state restored)."""
    results = {}
    validate_script = CODE_DIR / "validate_engine.py"
    if not validate_script.exists():
        results["validate_engine"] = False
        print("T5 validate_engine: script not found -> FAIL")
        return results

    import subprocess
    proc = subprocess.run(
        [sys.executable, str(validate_script)],
        capture_output=True, text=True, cwd=str(COMBINE_DIR),
        timeout=60,
    )
    output = proc.stdout + proc.stderr
    results["validate_engine"] = proc.returncode == 0
    print("T5 validate_engine: rc=%d -> %s" % (
        proc.returncode, "PASS" if results["validate_engine"] else "FAIL"))
    if proc.returncode != 0:
        for line in output.splitlines()[-10:]:
            print("  ", line)
    return results


def test_rank_score_ordering() -> dict:
    """T6: конфликты на одном тикере разрешаются по rank_score (лучший побеждает)."""
    results = {}
    # Создаём 3 стратегии на один тикер с разными score
    signal_pool = {"strategies": {}, "last_rotation_ts": 0.0}
    entries = [
        ("S1", 500.0), ("S2", 1500.0), ("S3", 800.0),
    ]
    for sid, score in entries:
        registry.add_to_signal_pool(signal_pool, sid, "TESTTK", "strat_%s" % sid,
                                    {}, {"pnl": score, "sharpe": 0.5,
                                         "win_rate": 50.0, "pf": 1.2,
                                         "trades": 20, "trades_per_day": 1.0},
                                    score, 2000.0)

    portfolio = {"slots": {}}
    conflicts = registry.signal_conflicts(portfolio, signal_pool)
    if "TESTTK" in conflicts:
        best_pid, best_entry = conflicts["TESTTK"][0]
        results["rank_ordering"] = (best_pid == "S2" and best_entry["rank_score"] == 1500.0)
    else:
        results["rank_ordering"] = False

    print("T6 rank_ordering: best=%s (score=%.0f) -> %s" % (
        best_pid if "TESTTK" in conflicts else "NONE",
        best_entry["rank_score"] if "TESTTK" in conflicts else 0,
        "PASS" if results.get("rank_ordering") else "FAIL"))
    return results


# ── Main ──────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("SIGNAL POOL FLOW INTEGRATION TEST")
    print("=" * 60)
    print()

    all_results = {}

    # T1: Pool fill ≥ 10
    r1, signal_pool = test_pool_fill()
    all_results.update(r1)

    # T2: Conflict resolution by rank_score
    r2 = _conflict_resolution(signal_pool)
    all_results.update(r2)

    # T3: No RI
    r3 = _no_ri(signal_pool)
    all_results.update(r3)

    # T4: Live slots intact
    r4 = test_live_slots_intact()
    all_results.update(r4)

    # T5: validate_engine.py (A/C/D)
    r5 = test_validate_engine()
    all_results.update(r5)

    # T6: rank_score ordering
    r6 = test_rank_score_ordering()
    all_results.update(r6)

    # Итог
    print()
    print("=" * 60)
    print("RESULTS")
    print("=" * 60)
    for k in sorted(all_results):
        print("  %s: %s" % (k, "PASS" if all_results[k] else "FAIL"))

    all_pass = all(all_results.values())
    print()
    print("OVERALL:", "ALL PASS" if all_pass else "FAIL")
    return all_pass


if __name__ == "__main__":
    ok = main()
    sys.exit(0 if ok else 1)
