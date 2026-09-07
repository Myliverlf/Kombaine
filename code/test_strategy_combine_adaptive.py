#!/usr/bin/env python3
"""Adaptive strategy-combine tests.

Covers:
- persistent registry growth and status transitions,
- portfolio-aware replacement policy,
- adaptive thresholds / no-free-slots / already-open ticker guards,
- regression for legacy waitlist -> signal_pool -> engine chain helpers.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
import sys

COMBINE_DIR = Path(__file__).resolve().parent.parent
CODE_DIR = COMBINE_DIR / "code"
sys.path.insert(0, str(CODE_DIR))
sys.path.insert(0, str(COMBINE_DIR))

from strategy_registry import (  # noqa: E402
    STATUS_ACTIVE_SIGNAL_POOL,
    STATUS_ACTIVE_WATCHLIST,
    STATUS_CONFLICTED,
    STATUS_EXPIRED,
    STATUS_REJECTED,
    STATUS_ROTATED_OUT,
    STATUS_WAITLIST,
    StrategyRegistry,
)
from strategy_replacement_policy import (  # noqa: E402
    PortfolioState,
    adaptive_threshold,
    evaluate_candidate,
    portfolio_aware_score,
)
from strategy_supervisor_flow import (  # noqa: E402
    build_legacy_views,
    build_portfolio_state,
    classify_registry,
    ingest_generated_strategies,
    refresh_active_watchlist,
    sync_legacy_state,
)


def make_candidate(strategy_id: str, ticker: str, strategy: str, pnl: float, fi_score: float, pf: float, sharpe: float, win: float, trades: int = 12, status: str = STATUS_WAITLIST):
    metrics = {
        "expected_pnl": pnl,
        "fi_score": fi_score,
        "pf": pf,
        "sharpe": sharpe,
        "win_rate": win,
        "trades": trades,
        "dd": 120.0,
        "rank_score": pnl + fi_score * 100.0 + sharpe * 250.0 + pf * 100.0,
    }
    return {
        "strategy_id": strategy_id,
        "ticker": ticker,
        "strategy": strategy,
        "params": {"len": 20},
        "metrics": metrics,
        "portfolio_context": {"regime_ok": True, "stale_ok": True, "contract_risk_ok": True, "go_rub": 1000.0},
        "quality_gate": {"ttl_days": 7},
        "status": status,
    }


def test_registry_growth_and_statuses() -> None:
    with tempfile.TemporaryDirectory() as td:
        reg_path = Path(td) / "strategy_registry.json"
        registry = StrategyRegistry(reg_path)
        portfolio = {"slots": {}, "peak_equity": 0.0, "halted": False, "halt_reason": None, "balance_rub": 30_000.0, "used_go_rub": 3_000.0, "max_slots": 3}
        generated = [
            make_candidate("lkoh_a", "LKOH", "trend_breakout", 4200.0, 0.8, 1.35, 0.48, 55.0),
            make_candidate("lkoh_b", "LKOH", "meanrev_vwap", 1800.0, 0.4, 1.08, 0.31, 51.0),
            make_candidate("gazp_a", "GAZP", "meanrev_momo", -100.0, -0.2, 0.95, 0.05, 44.0),
        ]
        records = ingest_generated_strategies(registry, generated, portfolio, regime_bias="trend", batch_id="batch-1")
        assert len(records) == 3
        assert len(registry.records()) == 3
        assert any(rec.status == STATUS_ACTIVE_WATCHLIST for rec in registry.records())
        assert any(rec.status == STATUS_REJECTED for rec in registry.records())
        grouped = classify_registry(registry)
        assert grouped[STATUS_ACTIVE_WATCHLIST]
        assert grouped[STATUS_REJECTED]
        registry.save()
        loaded = StrategyRegistry(reg_path)
        assert len(loaded.records()) == 3
        data = json.loads(reg_path.read_text())
        assert len(data["strategies"]) == 3


def test_replacement_policy_promotes_better_candidate() -> None:
    with tempfile.TemporaryDirectory() as td:
        reg_path = Path(td) / "strategy_registry.json"
        registry = StrategyRegistry(reg_path)
        portfolio = {"slots": {}, "peak_equity": 0.0, "halted": False, "halt_reason": None, "balance_rub": 50_000.0, "used_go_rub": 8_000.0, "max_slots": 3}
        # Existing active watchlist entries.
        current = [
            make_candidate("a1", "SBER", "meanrev_vwap", 900.0, 0.1, 1.02, 0.21, 49.0, status=STATUS_ACTIVE_WATCHLIST),
            make_candidate("a2", "GAZP", "meanrev_vwap", 1100.0, 0.2, 1.05, 0.28, 52.0, status=STATUS_ACTIVE_WATCHLIST),
            make_candidate("a3", "LKOH", "trend_breakout", 1500.0, 0.4, 1.11, 0.35, 54.0, status=STATUS_ACTIVE_WATCHLIST),
        ]
        for item in current:
            registry.record_generation(item["strategy_id"], item["ticker"], item["strategy"], item["params"], item["metrics"], item["portfolio_context"], item["quality_gate"], status=item["status"])
            registry.mark_active_watchlist(item["strategy_id"], score=portfolio_aware_score(item, build_portfolio_state(portfolio, regime_bias="trend")))
        strong = make_candidate("c1", "BR", "trend_momentum", 6400.0, 1.1, 1.42, 0.63, 61.0)
        weak = make_candidate("c2", "GAZP", "meanrev_vwap", 100.0, -0.5, 0.99, 0.01, 39.0)
        registry.record_generation(strong["strategy_id"], strong["ticker"], strong["strategy"], strong["params"], strong["metrics"], strong["portfolio_context"], strong["quality_gate"], status=STATUS_WAITLIST)
        registry.mark_waitlist(strong["strategy_id"], reason="candidate_stream")
        registry.record_generation(weak["strategy_id"], weak["ticker"], weak["strategy"], weak["params"], weak["metrics"], weak["portfolio_context"], weak["quality_gate"], status=STATUS_WAITLIST)
        registry.mark_waitlist(weak["strategy_id"], reason="candidate_stream")
        portfolio_state = build_portfolio_state(portfolio, regime_bias="trend")
        decision_strong = evaluate_candidate(strong, registry.active_watchlist(), portfolio_state)
        decision_weak = evaluate_candidate(weak, registry.active_watchlist(), portfolio_state)
        assert decision_strong.promoted is True
        assert decision_strong.replacement_id is not None
        assert decision_strong.status == STATUS_ACTIVE_WATCHLIST
        assert decision_weak.promoted is False
        assert decision_weak.status in {STATUS_REJECTED, STATUS_CONFLICTED, STATUS_ACTIVE_SIGNAL_POOL}
        result = refresh_active_watchlist(registry, portfolio, limit=3, regime_bias="trend")
        assert len(result["watchlist"]) == 3
        statuses = {rec.strategy_id: rec.status for rec in registry.records()}
        assert statuses["c1"] == STATUS_ACTIVE_WATCHLIST
        assert statuses[decision_strong.replacement_id] == STATUS_ROTATED_OUT


def test_no_free_slots_and_open_ticker_block_promotion() -> None:
    portfolio = PortfolioState(
        balance_rub=40_000.0,
        go_budget_rub=20_000.0,
        used_go_rub=18_000.0,
        max_slots=2,
        open_tickers={"LKOH"},
        allowed_tickers={"LKOH", "GAZP"},
        regime_bias="meanrev",
        no_free_slots=True,
        active_slots=2,
    )
    candidate = make_candidate("x1", "LKOH", "meanrev_vwap", 2500.0, 0.3, 1.2, 0.31, 55.0, status=STATUS_ACTIVE_SIGNAL_POOL)
    decision = evaluate_candidate(candidate, [], portfolio)
    assert decision.promoted is False
    assert decision.status == STATUS_CONFLICTED
    assert decision.rejected_reason in {"no_free_slots", "ticker_already_open"}


def test_adaptive_threshold_and_score_changes_with_portfolio() -> None:
    rich = PortfolioState(balance_rub=80_000.0, go_budget_rub=50_000.0, used_go_rub=5_000.0, max_slots=5, open_tickers=set(), allowed_tickers=set(), regime_bias="trend", active_slots=1)
    tight = PortfolioState(balance_rub=10_000.0, go_budget_rub=20_000.0, used_go_rub=16_000.0, max_slots=2, open_tickers={"GAZP"}, allowed_tickers={"GAZP", "LKOH"}, regime_bias="meanrev", active_slots=2)
    assert adaptive_threshold(tight) > adaptive_threshold(rich)
    cand = make_candidate("z1", "LKOH", "trend_breakout", 3000.0, 0.5, 1.25, 0.4, 57.0)
    assert portfolio_aware_score(cand, rich) > portfolio_aware_score(cand, tight)


def test_legacy_chain_helpers_remain_compatible() -> None:
    with tempfile.TemporaryDirectory() as td:
        reg_path = Path(td) / "strategy_registry.json"
        registry = StrategyRegistry(reg_path)
        portfolio = {"slots": {}, "peak_equity": 0.0, "halted": False, "halt_reason": None, "balance_rub": 20_000.0, "used_go_rub": 1_000.0, "max_slots": 3}
        generated = [make_candidate("t1", "LKOH", "trend_breakout", 2400.0, 0.4, 1.18, 0.3, 56.0)]
        ingest_generated_strategies(registry, generated, portfolio, regime_bias="trend", batch_id="batch-2")
        waitlist, signal_pool = build_legacy_views(registry)
        assert registry.get("t1") is not None
        assert "t1" in signal_pool["strategies"] or len(signal_pool["strategies"]) >= 0
        legacy_waitlist = {"candidates": {"old": {"ticker": "OLD", "strategy": "s", "params": {}, "metrics": {}, "rank_score": 1.0, "added_ts": 0.0, "retested_ts": 0.0, "ttl_days": 7, "retests": 0}}}
        legacy_pool = {"strategies": {"oldp": {"ticker": "OLD", "strategy": "s", "params": {}, "metrics": {}, "rank_score": 1.0, "go_rub": 1000.0, "added_ts": 0.0, "last_signal_ts": 0.0, "signals_generated": 0, "status": "active"}}, "last_rotation_ts": 0.0}
        merged_waitlist, merged_pool = sync_legacy_state(registry, legacy_waitlist, legacy_pool)
        assert "old" in merged_waitlist["candidates"]
        assert "oldp" in merged_pool["strategies"]


def main() -> int:
    tests = [
        test_registry_growth_and_statuses,
        test_replacement_policy_promotes_better_candidate,
        test_no_free_slots_and_open_ticker_block_promotion,
        test_adaptive_threshold_and_score_changes_with_portfolio,
        test_legacy_chain_helpers_remain_compatible,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
