#!/usr/bin/env python3
"""Dry-run CLI validator for pipeline_ranker.

Читает config.json + state/portfolio.json + state/regime_snapshot.json.
Вызывает run_pipeline(). Выводит PASS/FAIL чеклист:
  (1) slots ≤3
  (2) RI absent
  (3) 1 contract max
  (4) scorecard present
  (5) lifecycle present (returns are synthetic)
  (6) no mutation of state/

Read-only, без ордеров.
"""
import json
import os
import sys
import time
import hashlib

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

# Locate project root (one level up from code/)
_PROJECT_ROOT = os.path.dirname(_HERE)
_CONFIG_PATH = os.path.join(_PROJECT_ROOT, "config.json")
_PORTFOLIO_PATH = os.path.join(_PROJECT_ROOT, "state", "portfolio.json")
_REGIME_PATH = os.path.join(_PROJECT_ROOT, "state", "regime_snapshot.json")


def _md5_of_file(path: str) -> str:
    """MD5 hash of a file for mutation detection."""
    if not os.path.exists(path):
        return "MISSING"
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _build_candidates_from_portfolio(portfolio: dict) -> list:
    """Extract candidate-like dicts from portfolio.json slots."""
    candidates = []
    for slot_id, slot in portfolio.get("slots", {}).items():
        ticker = slot.get("ticker", "")
        pos = slot.get("open_position")
        direction = pos.get("direction") if pos else None
        n_trades = slot.get("n_trades", 0)
        # Use real stats if available, otherwise defaults
        win_rate = slot.get("win_rate", 0.5)
        avg_win = slot.get("avg_win", 100.0)
        avg_loss = slot.get("avg_loss", 50.0)

        candidates.append({
            "ticker": ticker,
            "direction": direction,
            "win_rate": win_rate,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "contracts_requested": slot.get("contracts", 1),
        })
    return candidates


def _add_synthetic_candidates(candidates: list) -> list:
    """Add synthetic candidates to test ranking (ensure we have enough data points)."""
    existing_tickers = {c["ticker"] for c in candidates}
    synthetic = [
        {"ticker": "SBER", "direction": "LONG",
         "win_rate": 0.65, "avg_win": 120, "avg_loss": 80,
         "contracts_requested": 1},
        {"ticker": "BR", "direction": "SHORT",
         "win_rate": 0.55, "avg_win": 90, "avg_loss": 60,
         "contracts_requested": 1},
        {"ticker": "RI", "direction": "LONG",
         "win_rate": 0.95, "avg_win": 600, "avg_loss": 5,
         "contracts_requested": 1},
    ]
    for s in synthetic:
        if s["ticker"] not in existing_tickers:
            candidates.append(s)
    return candidates


def main():
    print("=" * 60)
    print("PIPELINE RANKER DRY-RUN VALIDATOR")
    print("=" * 60)

    # ── Load data ──
    try:
        with open(_CONFIG_PATH, "r") as f:
            config = json.load(f)
        print("[OK] config.json loaded: %s" % _CONFIG_PATH)
    except FileNotFoundError:
        print("[FAIL] config.json not found: %s" % _CONFIG_PATH)
        sys.exit(1)

    portfolio = {}
    if os.path.exists(_PORTFOLIO_PATH):
        with open(_PORTFOLIO_PATH, "r") as f:
            portfolio = json.load(f)
        print("[OK] portfolio.json loaded")
    else:
        print("[WARN] portfolio.json not found — using empty portfolio")

    regime = {}
    if os.path.exists(_REGIME_PATH):
        with open(_REGIME_PATH, "r") as f:
            regime = json.load(f)
        print("[OK] regime_snapshot.json loaded")
    else:
        print("[WARN] regime_snapshot.json not found — using empty regime")

    # ── Snapshot state/ for mutation detection ──
    state_dir = os.path.join(_PROJECT_ROOT, "state")
    md5_before = {}
    for fname in os.listdir(state_dir) if os.path.isdir(state_dir) else []:
        fpath = os.path.join(state_dir, fname)
        if os.path.isfile(fpath):
            md5_before[fname] = _md5_of_file(fpath)

    # ── Build candidates ──
    candidates = _build_candidates_from_portfolio(portfolio)
    candidates = _add_synthetic_candidates(candidates)
    print("[INFO] %d candidates (portfolio + synthetic)" % len(candidates))

    # ── Run pipeline ──
    from pipeline_ranker import run_pipeline

    # Synthetic returns for lifecycle
    synthetic_returns = [
        0.01, -0.005, 0.02, -0.01, 0.015, -0.002, 0.008,
        0.003, -0.001, 0.012, 0.005, -0.003, 0.009, 0.001,
    ]

    result = run_pipeline(
        config, candidates, regime,
        returns=synthetic_returns,
        now_ts=time.time(),
    )

    # ── PASS/FAIL checklist ──
    print()
    print("-" * 60)
    print("CHECKLIST")
    print("-" * 60)

    passed = 0
    total = 6

    # (1) slots ≤3
    n = result["meta"]["n_selected"]
    ok1 = n <= 3
    print("[%s] (1) slots ≤3: %d selected" % ("PASS" if ok1 else "FAIL", n))
    if ok1:
        passed += 1

    # (2) RI absent
    tickers = [s["ticker"] for s in result["selected"]]
    ok2 = "RI" not in tickers
    print("[%s] (2) RI absent: tickers=%s" % ("PASS" if ok2 else "FAIL", tickers))
    if ok2:
        passed += 1

    # (3) 1 contract max
    ok3 = all(s["contracts"] <= 1 for s in result["selected"])
    print("[%s] (3) 1 contract max: contracts=%s" % (
        "PASS" if ok3 else "FAIL",
        [s["contracts"] for s in result["selected"]]))
    if ok3:
        passed += 1

    # (4) scorecard present
    ok4 = result["scorecard"] is not None and "components" in (result["scorecard"] or {})
    print("[%s] (4) scorecard present: %s" % (
        "PASS" if ok4 else "FAIL",
        result["scorecard"]["verdict"] if ok4 else "None"))
    if ok4:
        passed += 1

    # (5) lifecycle present
    ok5 = result["lifecycle"] is not None and "hit_rate" in (result["lifecycle"] or {})
    print("[%s] (5) lifecycle present: verdict=%s" % (
        "PASS" if ok5 else "FAIL", result["lifecycle_verdict"]))
    if ok5:
        passed += 1

    # (6) no mutation of state/
    md5_after = {}
    for fname in os.listdir(state_dir) if os.path.isdir(state_dir) else []:
        fpath = os.path.join(state_dir, fname)
        if os.path.isfile(fpath):
            md5_after[fname] = _md5_of_file(fpath)

    mutations = []
    for fname in set(list(md5_before.keys()) + list(md5_after.keys())):
        if md5_before.get(fname) != md5_after.get(fname):
            mutations.append(fname)
    ok6 = len(mutations) == 0
    print("[%s] (6) no mutation of state/: %s" % (
        "PASS" if ok6 else "FAIL",
        "no changes" if ok6 else "mutated: %s" % mutations))
    if ok6:
        passed += 1

    # ── Summary ──
    print()
    print("=" * 60)
    print("RESULT: %d/%d passed" % (passed, total))
    if passed == total:
        print("ALL PASS — pipeline_ranker dry-run validated")
    else:
        print("SOME FAILURES — review above")
    print("=" * 60)

    # ── Per-slot detail ──
    print()
    print("SELECTED SLOTS:")
    for i, s in enumerate(result["selected"]):
        print("  [%d] %s %s  score=%.4f  E[R]=%.4f  risk_pen=%.4f  regime=%.3f" % (
            i + 1, s["ticker"], s.get("direction", "?"),
            s["score"], s["expectancy_r"],
            s["risk_penalty"], s["regime_bonus"]))

    if result["per_slot_scores"]:
        print()
        print("PER-_SLOT SCORES:")
        for ps in result["per_slot_scores"]:
            lc = ps.get("lifecycle") or {}
            print("  %s: hit_rate=%.2f stability=%s" % (
                ps["ticker"],
                lc.get("hit_rate", 0.0),
                "%.2f" % lc["stability"] if lc.get("stability") is not None else "N/A"))

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
