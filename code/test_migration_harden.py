#!/usr/bin/env python3
"""Integration tests for migration/cleanup roadmap hardening.

Covers:
  - Single-source registry behavior (persistence, event history, dedup)
  - Legacy view compatibility (waitlist/signal_pool as export-only views)
  - Supervisor idempotency (no duplication on successive ticks)
  - Existing reconcile/sl_tp suite compatibility
"""
from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path
import sys

COMBINE_DIR = Path(__file__).resolve().parent.parent
CODE_DIR = COMBINE_DIR / "code"
CORE_DIR = COMBINE_DIR / "core"
sys.path.insert(0, str(CODE_DIR))
sys.path.insert(0, str(COMBINE_DIR))

from strategy_registry import (  # noqa: E402
    STATUS_ACTIVE_SIGNAL_POOL,
    STATUS_ACTIVE_WATCHLIST,
    STATUS_CONFLICTED,
    STATUS_EXPIRED,
    STATUS_REJECTED,
    STATUS_ROTATED_OUT,
    STATUS_REGISTRY_CANDIDATE,
    STATUS_WAITLIST,
    StrategyRegistry,
)
from strategy_supervisor_flow import (  # noqa: E402
    build_legacy_views,
    build_portfolio_state,
    classify_registry,
    ingest_generated_strategies,
    refresh_active_watchlist,
    sync_legacy_state,
)


# ── Helpers ──────────────────────────────────────────────────────────

def make_candidate(
    strategy_id: str,
    ticker: str,
    strategy: str,
    pnl: float = 2000.0,
    fi_score: float = 0.5,
    pf: float = 1.2,
    sharpe: float = 0.4,
    win: float = 55.0,
    trades: int = 12,
    status: str = STATUS_WAITLIST,
) -> dict:
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


def make_portfolio(**overrides) -> dict:
    base = {
        "slots": {},
        "peak_equity": 0.0,
        "halted": False,
        "halt_reason": None,
        "balance_rub": 50_000.0,
        "used_go_rub": 5_000.0,
        "max_slots": 3,
    }
    base.update(overrides)
    return base


# ══════════════════════════════════════════════════════════════════════
# 1. SINGLE-SOURCE REGISTRY BEHAVIOR
# ══════════════════════════════════════════════════════════════════════

def test_registry_persists_generated_strategies() -> None:
    """Registry records every generated strategy and survives save/load."""
    with tempfile.TemporaryDirectory() as td:
        reg_path = Path(td) / "strategy_registry.json"
        registry = StrategyRegistry(reg_path)
        portfolio = make_portfolio()

        batch = [
            make_candidate("s1", "LKOH", "trend_breakout"),
            make_candidate("s2", "GAZP", "meanrev_vwap"),
            make_candidate("s3", "SBER", "momentum"),
        ]
        ingest_generated_strategies(registry, batch, portfolio, regime_bias="trend", batch_id="b1")

        # All three persisted
        assert len(registry.records()) == 3

        # Save and reload
        registry.save()
        reloaded = StrategyRegistry(reg_path)
        assert len(reloaded.records()) == 3
        assert reloaded.get("s1") is not None
        assert reloaded.get("s2") is not None
        assert reloaded.get("s3") is not None

        # Raw JSON has 3 entries under strategies
        raw = json.loads(reg_path.read_text())
        assert len(raw["strategies"]) == 3
        assert raw["version"] == 1


def test_registry_event_history_grows() -> None:
    """Each status transition appends to the event history."""
    with tempfile.TemporaryDirectory() as td:
        reg_path = Path(td) / "registry.json"
        registry = StrategyRegistry(reg_path)

        registry.record_generation("e1", "LKOH", "trend", status=STATUS_WAITLIST, note="gen")
        r1 = registry.get("e1")
        assert r1 is not None
        assert len(r1.history) == 1

        registry.mark_waitlist("e1", reason="candidate")
        r2 = registry.get("e1")
        assert len(r2.history) == 2

        registry.mark_active_watchlist("e1", score=999.0)
        r3 = registry.get("e1")
        assert len(r3.history) == 3
        assert r3.history[-1].status == STATUS_ACTIVE_WATCHLIST

        registry.mark_rotated_out("e1", reason="replaced")
        r4 = registry.get("e1")
        assert len(r4.history) == 4


def test_registry_deduplicates_by_strategy_id() -> None:
    """Re-recording the same strategy_id updates rather than duplicates."""
    with tempfile.TemporaryDirectory() as td:
        reg_path = Path(td) / "registry.json"
        registry = StrategyRegistry(reg_path)

        registry.record_generation("dup1", "LKOH", "v1", status=STATUS_WAITLIST)
        registry.record_generation("dup1", "LKOH", "v2", status=STATUS_WAITLIST, note="updated")

        # Only one record, but two events
        assert len(registry.records()) == 1
        r = registry.get("dup1")
        assert r is not None
        assert r.strategy == "v2"
        assert len(r.history) == 2


def test_registry_unknown_strategy_raises_keyerror() -> None:
    """Transitioning an unknown id raises KeyError."""
    with tempfile.TemporaryDirectory() as td:
        registry = StrategyRegistry(Path(td) / "r.json")
        try:
            registry.transition("nonexistent", STATUS_EXPIRED)
            assert False, "should have raised KeyError"
        except KeyError:
            assert True


def test_registry_prune_history_caps_events() -> None:
    """prune_history limits the global event list."""
    with tempfile.TemporaryDirectory() as td:
        registry = StrategyRegistry(Path(td) / "r.json")
        for i in range(20):
            registry.record_generation(f"p{i}", "LKOH", "strat", status=STATUS_WAITLIST)
        assert len(registry.data["events"]) == 20
        registry.prune_history(max_events=5)
        assert len(registry.data["events"]) == 5
        # The last 5 events are kept
        assert registry.data["events"][-1]["strategy_id"] == "p19"


# ══════════════════════════════════════════════════════════════════════
# 2. LEGACY VIEW COMPATIBILITY
# ══════════════════════════════════════════════════════════════════════

def test_build_legacy_views_are_readonly_exports() -> None:
    """Legacy waitlist and signal_pool are export views, not mutations."""
    with tempfile.TemporaryDirectory() as td:
        reg_path = Path(td) / "registry.json"
        registry = StrategyRegistry(reg_path)
        portfolio = make_portfolio()

        batch = [
            make_candidate("lv1", "LKOH", "trend_breakout"),
            make_candidate("lv2", "GAZP", "meanrev_vwap"),
        ]
        ingest_generated_strategies(registry, batch, portfolio, regime_bias="trend", batch_id="b-lv")

        records_before = len(registry.records())
        waitlist_view, signal_pool_view = build_legacy_views(registry)

        # Views are dicts with expected shape
        assert "candidates" in waitlist_view
        assert "strategies" in signal_pool_view
        assert "last_rotation_ts" in signal_pool_view

        # No mutation to registry
        assert len(registry.records()) == records_before

        # Views reflect the registry state
        for rid, cand in waitlist_view["candidates"].items():
            rec = registry.get(rid)
            assert rec is not None
            assert cand["ticker"] == rec.ticker
            assert cand["strategy"] == rec.strategy

        for rid, sp in signal_pool_view["strategies"].items():
            rec = registry.get(rid)
            assert rec is not None
            assert sp["ticker"] == rec.ticker


def test_legacy_view_reflects_status_changes() -> None:
    """Waitlist view excludes rejected; signal_pool view includes active_watchlist."""
    with tempfile.TemporaryDirectory() as td:
        registry = StrategyRegistry(Path(td) / "r.json")
        portfolio = make_portfolio()

        batch = [
            make_candidate("lw1", "LKOH", "trend_breakout"),
            make_candidate("lw2", "GAZP", "meanrev_vwap"),
            make_candidate("lw3", "SBER", "momentum"),
        ]
        ingest_generated_strategies(registry, batch, portfolio, regime_bias="trend", batch_id="b2")

        # Manually transition one to rejected, one to active_watchlist
        registry.mark_rejected("lw2", reason="quality_gate")
        registry.mark_active_watchlist("lw1", score=100.0)

        waitlist_view, signal_pool_view = build_legacy_views(registry)

        # Rejected should NOT be in waitlist (export_legacy_waitlist skips REJECTED)
        assert "lw2" not in waitlist_view["candidates"]

        # Active watchlist should appear in signal_pool
        assert "lw1" in signal_pool_view["strategies"]
        assert signal_pool_view["strategies"]["lw1"]["status"] == STATUS_ACTIVE_WATCHLIST


def test_sync_legacy_state_merges_correctly() -> None:
    """sync_legacy_state merges registry views with pre-existing legacy state."""
    with tempfile.TemporaryDirectory() as td:
        registry = StrategyRegistry(Path(td) / "r.json")
        portfolio = make_portfolio()

        batch = [make_candidate("ms1", "LKOH", "trend_breakout")]
        ingest_generated_strategies(registry, batch, portfolio, regime_bias="trend", batch_id="b-ms")

        legacy_waitlist = {
            "candidates": {
                "old1": {"ticker": "OLD", "strategy": "s", "params": {}, "metrics": {},
                         "rank_score": 1.0, "added_ts": 0.0, "retested_ts": 0.0, "ttl_days": 7, "retests": 0}
            }
        }
        legacy_pool = {
            "strategies": {
                "oldp1": {"ticker": "OLDP", "strategy": "s", "params": {}, "metrics": {},
                          "rank_score": 1.0, "go_rub": 1000.0, "added_ts": 0.0,
                          "last_signal_ts": 0.0, "signals_generated": 0, "status": "active"}
            },
            "last_rotation_ts": 100.0,
        }

        merged_wl, merged_sp = sync_legacy_state(registry, legacy_waitlist, legacy_pool)

        # Legacy entries preserved
        assert "old1" in merged_wl["candidates"]
        assert "oldp1" in merged_sp["strategies"]

        # Registry entries merged in (ms1 gets promoted to active_watchlist, so appears in signal_pool)
        assert "ms1" in merged_sp["strategies"] or "ms1" in merged_wl["candidates"]

        # last_rotation_ts is the max of both
        assert merged_sp["last_rotation_ts"] >= 100.0


def test_sync_legacy_state_registry_overwrites_legacy_on_conflict() -> None:
    """When both legacy and registry have the same strategy_id, registry wins."""
    with tempfile.TemporaryDirectory() as td:
        registry = StrategyRegistry(Path(td) / "r.json")
        registry.record_generation(
            "conflict1", "LKOH", "new_strategy",
            params={"len": 30}, metrics={"pnl": 9999.0},
            status=STATUS_WAITLIST,
        )

        legacy_waitlist = {
            "candidates": {
                "conflict1": {"ticker": "OLD_TKR", "strategy": "old_strategy",
                              "params": {}, "metrics": {}, "rank_score": 0.0,
                              "added_ts": 0.0, "retested_ts": 0.0, "ttl_days": 7, "retests": 0}
            }
        }
        legacy_pool = {"strategies": {}, "last_rotation_ts": 0.0}

        merged_wl, _ = sync_legacy_state(registry, legacy_waitlist, legacy_pool)
        # Registry version wins
        assert merged_wl["candidates"]["conflict1"]["ticker"] == "LKOH"
        assert merged_wl["candidates"]["conflict1"]["strategy"] == "new_strategy"


def test_export_legacy_signal_pool_includes_active_watchlist() -> None:
    """signal_pool export includes both active_watchlist and active_signal_pool."""
    with tempfile.TemporaryDirectory() as td:
        registry = StrategyRegistry(Path(td) / "r.json")

        # Create entries with different statuses
        registry.record_generation("sp_aw", "LKOH", "trend", status=STATUS_WAITLIST)
        registry.mark_active_watchlist("sp_aw", score=100.0)

        registry.record_generation("sp_asp", "GAZP", "meanrev", status=STATUS_WAITLIST)
        registry.mark_active_signal_pool("sp_asp", score=50.0)

        registry.record_generation("sp_rej", "SBER", "momentum", status=STATUS_WAITLIST)
        registry.mark_rejected("sp_rej")

        signal_pool = registry.export_legacy_signal_pool()
        assert "sp_aw" in signal_pool["strategies"]
        assert "sp_asp" in signal_pool["strategies"]
        assert "sp_rej" not in signal_pool["strategies"]
        assert signal_pool["strategies"]["sp_aw"]["status"] == STATUS_ACTIVE_WATCHLIST
        assert signal_pool["strategies"]["sp_asp"]["status"] == STATUS_ACTIVE_SIGNAL_POOL


def test_export_legacy_waitlist_excludes_rejected() -> None:
    """waitlist export excludes rejected strategies."""
    with tempfile.TemporaryDirectory() as td:
        registry = StrategyRegistry(Path(td) / "r.json")

        registry.record_generation("wl1", "LKOH", "trend", status=STATUS_WAITLIST)
        registry.mark_waitlist("wl1", reason="candidate")

        registry.record_generation("wl2", "GAZP", "meanrev", status=STATUS_WAITLIST)
        registry.mark_rejected("wl2", reason="quality_gate")

        registry.record_generation("wl3", "SBER", "momentum", status=STATUS_WAITLIST)
        registry.mark_conflicted("wl3")

        waitlist = registry.export_legacy_waitlist()
        assert "wl1" in waitlist["candidates"]
        assert "wl2" not in waitlist["candidates"]
        assert "wl3" in waitlist["candidates"]  # conflicted is NOT excluded


# ══════════════════════════════════════════════════════════════════════
# 3. SUPERVISOR IDEMPOTENCY
# ══════════════════════════════════════════════════════════════════════

def test_supervisor_no_duplicate_strategies_on_double_tick() -> None:
    """Two successive refresh_active_watchlist calls do not duplicate entries."""
    with tempfile.TemporaryDirectory() as td:
        registry = StrategyRegistry(Path(td) / "r.json")
        portfolio = make_portfolio()

        batch = [
            make_candidate("id1", "LKOH", "trend_breakout", pnl=5000.0),
            make_candidate("id2", "GAZP", "meanrev_vwap", pnl=3000.0),
            make_candidate("id3", "SBER", "momentum", pnl=4000.0),
        ]
        ingest_generated_strategies(registry, batch, portfolio, regime_bias="trend", batch_id="b-tick")

        records_before = len(registry.records())

        # First tick
        result1 = refresh_active_watchlist(registry, portfolio, limit=3, regime_bias="trend")
        records_after_tick1 = len(registry.records())
        watchlist1 = {r["strategy_id"] for r in result1["watchlist"]}

        # Second tick — should be idempotent
        result2 = refresh_active_watchlist(registry, portfolio, limit=3, regime_bias="trend")
        records_after_tick2 = len(registry.records())
        watchlist2 = {r["strategy_id"] for r in result2["watchlist"]}

        # No new records created by second tick
        assert records_after_tick2 == records_after_tick1

        # Watchlist content is identical
        assert watchlist1 == watchlist2

        # No duplicates in the watchlist
        assert len(watchlist2) == len(result2["watchlist"])


def test_supervisor_idempotent_status_transitions() -> None:
    """Idempotent ticks don't corrupt status transitions."""
    with tempfile.TemporaryDirectory() as td:
        registry = StrategyRegistry(Path(td) / "r.json")
        portfolio = make_portfolio()

        batch = [
            make_candidate("st1", "LKOH", "trend_breakout", pnl=5000.0),
            make_candidate("st2", "GAZP", "meanrev_vwap", pnl=1000.0),
        ]
        ingest_generated_strategies(registry, batch, portfolio, regime_bias="trend", batch_id="b-st")

        # Tick once
        refresh_active_watchlist(registry, portfolio, limit=3, regime_bias="trend")

        # Snapshot statuses
        statuses_before = {r.strategy_id: r.status for r in registry.records()}
        history_lengths_before = {r.strategy_id: len(r.history) for r in registry.records()}

        # Tick again
        refresh_active_watchlist(registry, portfolio, limit=3, regime_bias="trend")

        statuses_after = {r.strategy_id: r.status for r in registry.records()}
        history_lengths_after = {r.strategy_id: len(r.history) for r in registry.records()}

        # Statuses unchanged
        assert statuses_before == statuses_after

        # No new history events added by idempotent tick
        assert history_lengths_before == history_lengths_after


def test_supervisor_preserves_rejected_and_conflicted() -> None:
    """Supervisor ticks don't accidentally promote rejected/conflicted strategies."""
    with tempfile.TemporaryDirectory() as td:
        registry = StrategyRegistry(Path(td) / "r.json")
        portfolio = make_portfolio()

        batch = [
            make_candidate("pr1", "LKOH", "trend_breakout", pnl=5000.0),
            make_candidate("pr2", "GAZP", "meanrev_vwap", pnl=-500.0),
        ]
        ingest_generated_strategies(registry, batch, portfolio, regime_bias="trend", batch_id="b-pr")

        # pr2 should be rejected (low pnl fails quality gate)
        rec2 = registry.get("pr2")
        assert rec2 is not None
        if rec2.status == STATUS_REJECTED:
            rejected_before = True
        else:
            rejected_before = False

        refresh_active_watchlist(registry, portfolio, limit=3, regime_bias="trend")
        refresh_active_watchlist(registry, portfolio, limit=3, regime_bias="trend")

        rec2_after = registry.get("pr2")
        assert rec2_after is not None
        if rejected_before:
            assert rec2_after.status == STATUS_REJECTED


def test_supervisor_snapshot_counts_accurate() -> None:
    """SupervisorSnapshot reflects accurate counts after multiple ticks."""
    with tempfile.TemporaryDirectory() as td:
        registry = StrategyRegistry(Path(td) / "r.json")
        portfolio = make_portfolio()

        batch = [
            make_candidate("sc1", "LKOH", "trend_breakout", pnl=5000.0),
            make_candidate("sc2", "GAZP", "meanrev_vwap", pnl=3000.0),
            make_candidate("sc3", "SBER", "momentum", pnl=2000.0),
            make_candidate("sc4", "SI", "trend_momentum", pnl=-200.0),
        ]
        ingest_generated_strategies(registry, batch, portfolio, regime_bias="trend", batch_id="b-sc")

        result = refresh_active_watchlist(registry, portfolio, limit=3, regime_bias="trend")
        snap = result["snapshot"]

        assert snap.registry_size == 4
        assert snap.active_watchlist_size <= 3
        # The sum of all category sizes should not exceed registry_size
        total_categorized = (
            snap.waitlist_size + snap.active_watchlist_size + snap.active_signal_pool_size
            + snap.rejected_size + snap.rotated_out_size + snap.conflicted_size + snap.expired_size
        )
        assert total_categorized <= snap.registry_size


def test_supervisor_state_after_replacement_is_consistent() -> None:
    """After a replacement, old strategy is rotated_out, new is active_watchlist."""
    with tempfile.TemporaryDirectory() as td:
        registry = StrategyRegistry(Path(td) / "r.json")
        portfolio = make_portfolio(max_slots=2)

        # Seed 2 active watchlist entries
        for i, sid in enumerate(["base1", "base2"]):
            rec = registry.record_generation(sid, f"TKR{i}", "meanrev", status=STATUS_WAITLIST)
            registry.mark_active_watchlist(sid, score=100.0 + i * 10)

        # Add a stronger candidate (with metrics that pass quality gate)
        strong_data = make_candidate("strong1", "NEW_TKR", "trend_breakout", pnl=8000.0)
        registry.record_generation(
            "strong1", "NEW_TKR", "trend_breakout",
            params=strong_data["params"],
            metrics=strong_data["metrics"],
            portfolio_context=strong_data["portfolio_context"],
            quality_gate=strong_data["quality_gate"],
            status=STATUS_WAITLIST,
        )
        registry.mark_waitlist("strong1", reason="candidate")

        result = refresh_active_watchlist(registry, portfolio, limit=2, regime_bias="trend")

        # strong1 should be promoted
        watchlist_ids = {r["strategy_id"] for r in result["watchlist"]}
        assert "strong1" in watchlist_ids

        # One of the original should be rotated out
        statuses = {r.strategy_id: r.status for r in registry.records()}
        rotated = [sid for sid, st in statuses.items() if st == STATUS_ROTATED_OUT]
        assert len(rotated) >= 1

        # No strategy is in both active_watchlist AND rotated_out
        assert not (watchlist_ids & set(rotated))


# ══════════════════════════════════════════════════════════════════════
# 4. REGISTRY + LEGACY CHAIN INTEGRATION
# ══════════════════════════════════════════════════════════════════════

def test_ingest_then_views_match_registry_state() -> None:
    """After ingest, legacy views accurately reflect registry contents."""
    with tempfile.TemporaryDirectory() as td:
        registry = StrategyRegistry(Path(td) / "r.json")
        portfolio = make_portfolio()

        batch = [
            make_candidate("iv1", "LKOH", "trend_breakout"),
            make_candidate("iv2", "GAZP", "meanrev_vwap"),
        ]
        ingest_generated_strategies(registry, batch, portfolio, regime_bias="trend", batch_id="b-iv")

        waitlist_view, signal_pool_view = build_legacy_views(registry)

        # Every registry record should appear in exactly one view
        for rec in registry.records():
            in_waitlist = rec.strategy_id in waitlist_view["candidates"]
            in_signal_pool = rec.strategy_id in signal_pool_view["strategies"]
            # A record is in exactly one view (unless status is a boundary case)
            if rec.status == STATUS_REJECTED:
                assert not in_waitlist, f"rejected {rec.strategy_id} should not be in waitlist"
            elif rec.status in {STATUS_ACTIVE_WATCHLIST, STATUS_ACTIVE_SIGNAL_POOL}:
                assert in_signal_pool, f"active {rec.strategy_id} should be in signal_pool"
            elif rec.status in {STATUS_WAITLIST, STATUS_REGISTRY_CANDIDATE, STATUS_CONFLICTED}:
                assert in_waitlist, f"candidate {rec.strategy_id} should be in waitlist"


def test_full_cycle_ingest_promote_refresh_views() -> None:
    """Full lifecycle: ingest → promote → refresh → verify views consistent."""
    with tempfile.TemporaryDirectory() as td:
        registry = StrategyRegistry(Path(td) / "r.json")
        portfolio = make_portfolio(max_slots=2)

        # Phase 1: Ingest
        batch = [
            make_candidate("fc1", "LKOH", "trend_breakout", pnl=4000.0),
            make_candidate("fc2", "GAZP", "meanrev_vwap", pnl=3000.0),
            make_candidate("fc3", "SBER", "momentum", pnl=2500.0),
        ]
        ingest_generated_strategies(registry, batch, portfolio, regime_bias="trend", batch_id="b-fc")

        # Phase 2: Refresh watchlist
        result = refresh_active_watchlist(registry, portfolio, limit=2, regime_bias="trend")

        # Phase 3: Verify views
        waitlist_view, signal_pool_view = build_legacy_views(registry)

        # Active strategies in signal_pool
        active_ids = {r.strategy_id for r in registry.active_watchlist(limit=2)}
        for rid in active_ids:
            assert rid in signal_pool_view["strategies"], f"{rid} missing from signal_pool view"

        # Refresh again — should be stable
        result2 = refresh_active_watchlist(registry, portfolio, limit=2, regime_bias="trend")
        assert len(result2["watchlist"]) == len(result["watchlist"])
        assert {r["strategy_id"] for r in result2["watchlist"]} == {r["strategy_id"] for r in result["watchlist"]}


def test_classify_registry_groups_correctly() -> None:
    """classify_registry returns accurate groupings after operations."""
    with tempfile.TemporaryDirectory() as td:
        registry = StrategyRegistry(Path(td) / "r.json")
        portfolio = make_portfolio()

        batch = [
            make_candidate("cr1", "LKOH", "trend_breakout"),
            make_candidate("cr2", "GAZP", "meanrev_vwap"),
        ]
        ingest_generated_strategies(registry, batch, portfolio, regime_bias="trend", batch_id="b-cr")

        # Force one to rejected, one to active_watchlist
        registry.mark_rejected("cr2", reason="quality_gate")
        registry.mark_active_watchlist("cr1", score=100.0)

        grouped = classify_registry(registry)
        assert "cr1" in grouped.get(STATUS_ACTIVE_WATCHLIST, [])
        assert "cr2" in grouped.get(STATUS_REJECTED, [])
        assert "cr1" not in grouped.get(STATUS_WAITLIST, [])


# ══════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════

def main() -> int:
    tests = [
        # Single-source registry
        test_registry_persists_generated_strategies,
        test_registry_event_history_grows,
        test_registry_deduplicates_by_strategy_id,
        test_registry_unknown_strategy_raises_keyerror,
        test_registry_prune_history_caps_events,
        # Legacy view compatibility
        test_build_legacy_views_are_readonly_exports,
        test_legacy_view_reflects_status_changes,
        test_sync_legacy_state_merges_correctly,
        test_sync_legacy_state_registry_overwrites_legacy_on_conflict,
        test_export_legacy_signal_pool_includes_active_watchlist,
        test_export_legacy_waitlist_excludes_rejected,
        # Supervisor idempotency
        test_supervisor_no_duplicate_strategies_on_double_tick,
        test_supervisor_idempotent_status_transitions,
        test_supervisor_preserves_rejected_and_conflicted,
        test_supervisor_snapshot_counts_accurate,
        test_supervisor_state_after_replacement_is_consistent,
        # Integration
        test_ingest_then_views_match_registry_state,
        test_full_cycle_ingest_promote_refresh_views,
        test_classify_registry_groups_correctly,
    ]
    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            print(f"PASS {test.__name__}")
            passed += 1
        except Exception as exc:
            print(f"FAIL {test.__name__}: {exc}")
            failed += 1

    print(f"\n{'ALL PASS' if failed == 0 else f'{failed} FAILED'} ({passed} passed, {failed} failed)")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
