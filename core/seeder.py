"""Seeder: заполняет waitlist комбайна кандидатами из готового скана
и валидирует якорь на свежих 15м-данных. Также заполняет signal pool.

Этап: read-only бэктест (никаких ордеров).

Iteration 06: Canonical handoff from completed research run is the default
intake path. Legacy fixed-scan path requires explicit --use-legacy opt-in.
"""
import argparse
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, "/root/prop-desk/futures_lab")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # project root — needed so `from core.state_router import ...` works when run as a bare script (systemd)
sys.path.insert(0, str(Path(__file__).resolve().parent))  # core/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "code"))  # code/ — must be BEFORE core/ so bare imports find code/strategy_supervisor_flow.py first

from futures_lab import run_backtest, resolve_spec, load_token, STRATEGY_FUNCS  # noqa
from tinkoff.invest import Client  # noqa
from capital_context import DEFAULT_REPORT_CAPITAL_RUB
from strategy_architect_autopilot import capital_normalized_score

import registry

# --- Spec cache: загружаем один раз на запуск, экономим API-запросы ---
_SPEC_CACHE: dict = {}

try:
    from .strategy_registry import (  # noqa: E402
        STATUS_ACTIVE_SIGNAL_POOL,
        STATUS_WAITLIST,
        StrategyRegistry as AdaptiveRegistry,
        load_registry,
    )
    from .strategy_supervisor_flow import build_legacy_views, ingest_generated_strategies  # noqa: E402
except ImportError:  # pragma: no cover - direct execution from core/
    from strategy_registry import (  # type: ignore  # noqa: E402
        STATUS_ACTIVE_SIGNAL_POOL,
        STATUS_WAITLIST,
        StrategyRegistry as AdaptiveRegistry,
        load_registry,
    )
    from strategy_supervisor_flow import build_legacy_views, ingest_generated_strategies  # type: ignore  # noqa: E402

# --- Iteration 06: Canonical handoff ---
try:
    from .seeder_handoff import (  # noqa: E402
        validate_handoff,
        seed_from_eligible,
        legacy_fallback,
        HandoffSeedResult,
        UNIVERSE_GATE_DEFAULT,
    )
except ImportError:  # pragma: no cover - direct execution from core/
    from seeder_handoff import (  # type: ignore  # noqa: E402
        validate_handoff,
        seed_from_eligible,
        legacy_fallback,
        HandoffSeedResult,
        UNIVERSE_GATE_DEFAULT,
    )


def _seed_registry(waitlist: dict, signal_pool: dict) -> None:
    """Persist legacy waitlist/signal pool into the adaptive registry layer."""
    adaptive = AdaptiveRegistry.load()
    generated = []
    for cid, cand in waitlist.get("candidates", {}).items():
        generated.append({
            "strategy_id": cid,
            "ticker": cand.get("ticker"),
            "strategy": cand.get("strategy"),
            "params": cand.get("params", {}),
            "metrics": cand.get("metrics", {}),
            "portfolio_context": {"regime_ok": True, "stale_ok": True, "contract_risk_ok": True},
            "quality_gate": {"ttl_days": cand.get("ttl_days", 7)},
            "status": cand.get("status", "waitlist"),
        })
    if generated:
        ingest_generated_strategies(adaptive, generated, {"slots": {}, "balance_rub": 0.0, "used_go_rub": 0.0, "max_slots": 10}, regime_bias="neutral", batch_id="seeder-sync")
    adaptive.save()

SCAN_RESULTS = Path("/root/prop-desk/strategies/futures_top5_20260818_v2.scan_results.json")
DATA_DIR = Path("/root/prop-desk/futures_lab/artifacts/tinkoff_futures_data")
STATE_DIR = Path(__file__).resolve().parent.parent / "state"
REPORTS_DIR = Path(__file__).resolve().parent.parent / "reports" / "strategy_architect"

# живое ГО по тикерам (read-only probe 2026-08-18), рублей на контракт
GO_LIVE = {
    "SBER": 4702.0,
    "GAZP": 1433.0,
    "LKOH": 7000.0,
    "Si": 13120.0,
    "BR": 3500.0,   # оценочно; уточнится live-probe
}

# тикеры наших данных -> префикс для поиска фьючерса на MOEX
API_PREFIX = {"LKOH": "LK"}


def rank_score(m: dict) -> float:
    """Ранжирующий скот: профит + качество, штрафы за редкость."""
    pnl = m.get("pnl", 0.0)
    sharpe = m.get("sharpe", 0.0)
    win = m.get("win_rate", 0.0)
    pf = m.get("pf", 1.0)
    tpd = m.get("trades_per_day", 0.0)
    # частотный бонус: 1-2 сделки/день — идеал
    freq_bonus = 500.0 if 0.8 <= tpd <= 2.5 else (250.0 if tpd >= 0.4 else -300.0)
    return pnl + sharpe * 1000.0 + win * 30.0 + (pf - 1) * 800.0 + freq_bonus


def load_candidates():
    """LEGACY: load candidates from fixed scan file.

    Only used when --use-legacy flag is passed.
    """
    data = json.loads(SCAN_RESULTS.read_text())
    rows = data if isinstance(data, list) else data.get("results", [])
    out = []
    for r in rows:
        if r.get("ticker") == "RI":
            continue
        if not r.get("wf_quality_passed"):
            continue
        out.append(r)
    return out


def load_canonical_candidates() -> list:
    """Load candidates from the latest completed canonical run.

    Returns candidates from eligible_candidates.json of the latest
    COMPLETED run that passed all integrity checks.
    """
    validation = validate_handoff(REPORTS_DIR, universe=list(GO_LIVE.keys()))
    if not validation.valid:
        return []
    run_dir = Path(validation.run_dir)
    eligible_path = run_dir / "eligible_candidates.json"
    if not eligible_path.exists():
        return []
    eligible = json.loads(eligible_path.read_text(encoding="utf-8"))
    # Normalize to seeder-compatible format
    out = []
    for c in eligible:
        instrument = c.get("instrument", "")
        strategy = c.get("strategy", "")
        parameters = c.get("parameters", {})
        metrics = c.get("metrics", {})
        out.append({
            "ticker": instrument,
            "strategy": strategy,
            "best_params": parameters,
            "wf_quality_passed": True,  # Already validated by canonical gate
            "run_id": c.get("run_id"),
            "config_key": c.get("config_key"),
            "eligible_metrics": metrics,
        })
    return out


def validate_on_15m(ticker: str, strategy: str, params: dict) -> dict | None:
    """Быстрый бэктест на 15м: возвращает метрики или None."""
    f = DATA_DIR / f"{ticker}_60d_15m_continuous.csv"
    if not f.exists():
        return None
    if strategy not in STRATEGY_FUNCS:
        return None
    df = pd.read_csv(f, parse_dates=["time"])
    if len(df) < 200:
        return None
    # --- spec из кэша (1 API-запуск на все 5 тикеров вместо 15) ---
    api_ticker = API_PREFIX.get(ticker, ticker)
    if api_ticker not in _SPEC_CACHE:
        try:
            token = load_token()
            with Client(token) as c:
                _SPEC_CACHE[api_ticker] = resolve_spec(c, api_ticker)
        except Exception as e:
            print("  spec через API не найден (%s) — синтетика" % e)
            from futures_lab import FuturesSpec
            go = GO_LIVE.get(ticker, 2000.0)
            _SPEC_CACHE[api_ticker] = FuturesSpec(
                ticker=ticker, uid="synthetic-%s" % ticker,
                name="SYN %s" % ticker, class_code="SPBFUT", lot=1,
                min_price_increment=1.0, min_price_increment_amount=1.0,
                initial_margin_on_buy=go, initial_margin_on_sell=go)
    spec = _SPEC_CACHE[api_ticker]
    # честный risk-sizing: риск 2.7% депозита на сделку, ГО ограничено бюджетом
    deposit = 100000.0
    risk_rub = deposit * 0.027
    go_budget = deposit * 0.40 * 0.70   # 40% бюджет, 30% резерв на маржин-колл
    max_contracts = max(1, int(go_budget // spec.active_margin))
    try:
        metrics, trades, _eq = run_backtest(df, spec, strategy, params,
                                            initial_cash=100_000,
                                            risk_rub=risk_rub,
                                            max_contracts=max_contracts,
                                            stop_atr=2.0, take_atr=3.0,
                                            max_hold_bars=192)
    except Exception as e:
        print("  %s/%s бэктест упал: %s" % (ticker, strategy, e))
        return None
    n = len(trades)
    days = max((df["time"].iloc[-1] - df["time"].iloc[0]).days, 1)
    return {
        "pnl": metrics["total_pnl"],
        "sharpe": metrics["sharpe"],
        "win_rate": metrics["win_rate_pct"],
        "pf": metrics["profit_factor"] if metrics["profit_factor"] != float("inf") else 5.0,
        "dd": metrics["max_drawdown"],
        "trades": n,
        "trades_per_day": round(n / days, 2),
        "days": days,
    }


def main():
    parser = argparse.ArgumentParser(description="Seeder: canonical handoff or legacy scan")
    parser.add_argument(
        "--use-legacy", action="store_true",
        help="LEGACY opt-in: use fixed scan file instead of canonical run. "
             "This is NOT the default path — explicit opt-in required."
    )
    parser.add_argument(
        "--legacy-scan", type=str, default=None,
        help="Path to legacy scan file (used with --use-legacy)"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Validate handoff without writing to registry"
    )
    args = parser.parse_args()

    # CANONICAL: load registry as single source of truth
    adaptive_registry = load_registry()

    # === ITERATION 06: Canonical handoff or legacy opt-in ===
    source_type = "canonical_research_run"
    if args.use_legacy:
        # EXPLICIT LEGACY OPT-IN only
        source_type = "legacy_scan"
        print("⚠️  LEGACY MODE: explicit opt-in. This is NOT the canonical path.")
        legacy_path = Path(args.legacy_scan) if args.legacy_scan else SCAN_RESULTS
        print(f"LEGACY scan path: {legacy_path}")
        cands = load_candidates()
        print(f"кандидатов из legacy скана (без RI): {len(cands)}")
    else:
        # CANONICAL: load from latest completed run
        print("=== CANONICAL HANDOFF (Iteration 06) ===")
        cands = load_canonical_candidates()
        if not cands:
            print("⚠️  Canonical handoff: no valid candidates from latest completed run.")
            print("   Use --use-legacy to opt in to legacy scan fallback.")
            print("   Legacy fallback is NOT automatic.")
            print("\nзаполнено в registry (canonical): 0")
            print("signal pool: 0 стратегий")
            return
        print(f"кандидатов из canonical run (eligible): {len(cands)}")

    # Legacy views for backward compatibility
    waitlist, signal_pool = build_legacy_views(adaptive_registry)
    seeded = 0
    anchor = None
    anchor_score = -1e18

    for r in sorted(cands, key=lambda x: capital_normalized_score({
        "total_pnl": x.get("wf_avg_total_pnl", x.get("eligible_metrics", {}).get("total_pnl", 0.0)),
        "profit_factor": x.get("eligible_metrics", {}).get("profit_factor", x.get("eligible_metrics", {}).get("pf", 1.0)),
        "sharpe": x.get("eligible_metrics", {}).get("sharpe", 0.0),
        "max_drawdown": x.get("eligible_metrics", {}).get("max_drawdown", x.get("eligible_metrics", {}).get("dd", 0.0)),
        "trades": x.get("eligible_metrics", {}).get("trades", 1),
        "capital_rub": x.get("eligible_metrics", {}).get("capital_rub", DEFAULT_REPORT_CAPITAL_RUB),
    }), reverse=True)[:15]:
        ticker = r["ticker"]
        strategy = r["strategy"]
        params = r.get("best_params", {}) or {}
        cid = "%s__%s" % (ticker, strategy)

        # Provenance from canonical run (Iteration 06)
        source_run_id = r.get("run_id", "unknown")
        source_config_key = r.get("config_key", cid)
        source_manifest_version = "1.0.0"

        if cid in waitlist["candidates"] or adaptive_registry.get(cid) is not None:
            continue
        print("валидация 15м: %s/%s ... (source: %s)" % (ticker, strategy, source_type))
        m15 = validate_on_15m(ticker, strategy, params)
        if m15 is None:
            print("  -> пропуск (нет данных/стратегии/ошибка)")
            continue
        print("  15м: pnl=%.0f sharpe=%.2f win=%.0f%% сделок=%d (%.2f/день)" % (
            m15["pnl"], m15["sharpe"], m15["win_rate"], m15["trades"], m15["trades_per_day"]))
        # фильтр качества на 15м
        if m15["pnl"] <= 0 or m15["trades"] < 8 or m15["pf"] < 1.05:
            print("  -> не прошёл 15м quality gate")
            continue
        score = rank_score(m15)
        go = GO_LIVE.get(ticker, 2000.0)

        # CANONICAL: write directly to registry with provenance (Iteration 06)
        note = "seeded_from_canonical_run" if source_type == "canonical_research_run" else "LEGACY_seed_from_scan"
        adaptive_registry.record_generation(
            strategy_id=cid,
            ticker=ticker,
            strategy=strategy,
            params=params,
            metrics=m15,
            portfolio_context={"regime_ok": True, "stale_ok": True, "contract_risk_ok": True, "go_rub": go},
            quality_gate={"ttl_days": 7},
            source=source_type,
            status=STATUS_WAITLIST,
            note=note,
        )
        # Apply score and provenance to the record
        record = adaptive_registry.get(cid)
        if record:
            record.active_rank = score
            # Iteration 06: attach provenance if supported
            if hasattr(record, "source_run_id"):
                record.source_run_id = source_run_id
            if hasattr(record, "source_config_key"):
                record.source_config_key = source_config_key
            if hasattr(record, "source_manifest_version"):
                record.source_manifest_version = source_manifest_version
            if hasattr(record, "source_type"):
                record.source_type = source_type
            adaptive_registry._store_record(record)
        seeded += 1

        # addTo signal pool if meets threshold
        pool_id = "%s__%s" % (ticker, strategy)
        if adaptive_registry.get(pool_id) is None:
            adaptive_registry.record_generation(
                strategy_id=pool_id,
                ticker=ticker,
                strategy=strategy,
                params=params,
                metrics=m15,
                portfolio_context={"regime_ok": True, "stale_ok": True, "contract_risk_ok": True, "go_rub": go},
                quality_gate={"signals_generated": 0},
                source=f"{source_type}_signal_pool",
                status=STATUS_ACTIVE_SIGNAL_POOL,
                note="seeded_to_signal_pool",
            )
            record = adaptive_registry.get(pool_id)
            if record:
                record.active_rank = score
                adaptive_registry._store_record(record)
            print("  -> добавлен в signal pool (score=%.0f)" % score)

        if score > anchor_score:
            anchor_score = score
            anchor = (cid, ticker, strategy, params, m15, go)

    # --- CANONICAL SAVE: registry first, then derive legacy views ---
    adaptive_registry.save()
    adaptive_registry.export_legacy_state_files()
    adaptive_registry.prune_history()

    # Refresh legacy views for log output
    waitlist, signal_pool = build_legacy_views(adaptive_registry)
    pool_active = sum(1 for p in signal_pool["strategies"].values() if p.get("status") == "active")
    print("\nзаполнено в registry (%s):" % source_type, seeded,
          "| всего:", len(adaptive_registry.records()))
    print("signal pool:", pool_active, "стратегий")
    print("legacy files exported for backward compatibility")

    if anchor:
        cid, ticker, strategy, params, m15, go = anchor
        print("\n=== ЯКОРЬ (лучший по скору на 15м) ===")
        print("%s/%s: pnl=%.0f, сделок/день=%.2f, GO=%.0f" % (ticker, strategy, m15["pnl"], m15["trades_per_day"], go))
        (STATE_DIR).mkdir(exist_ok=True)
        (STATE_DIR / "anchor_candidate.json").write_text(json.dumps({
            "cand_id": cid, "ticker": ticker, "strategy": strategy,
            "params": params, "metrics": m15, "go_rub": go,
        }, indent=2, ensure_ascii=False))
        print("записан в state/anchor_candidate.json")
    else:
        print("\nякорь не найден — ждём генерацию пайплайна")


if __name__ == "__main__":
    main()
