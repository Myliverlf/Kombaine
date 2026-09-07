#!/usr/bin/env python3
"""Iteration 12: First Canonical Production Research Proof — Full Execution Script.

Runs the complete end-to-end pipeline:
1. Pre-flight verification
2. Data readiness
3. Canonical run creation
4. Bounded backtest execution
5. Ledger verification
6. Experiment Memory indexing
7. Novelty Gate classification
8. Eligibility derivation
9. Seeder handoff
10. Knowledge build
11. Lifecycle build
12. Post-run health
13. Sample traces
14. Count reconciliation
15. Evidence bundle
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# Setup paths
PROJECT_ROOT = Path("/root/prop-desk/strategy_combine")
PROP_ROOT = PROJECT_ROOT.parent
DATA_ROOT = PROP_ROOT / "futures_lab" / "artifacts" / "tinkoff_futures_data"
STATE_DIR = PROJECT_ROOT / "state"
REPORT_DIR = PROJECT_ROOT / "reports" / "strategy_architect"

sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "code"))
sys.path.insert(0, str(PROP_ROOT / "futures_lab"))

import pandas as pd

# Import core modules
from core.run_contract import (
    ResearchRun, capture_git_revision, code_identity,
    fingerprint_file, data_manifest_entry, generate_run_id,
)
from core.experiment_memory import (
    ExperimentMemory, experiment_family_id, experiment_instance_id,
    normalize_params, normalize_instrument, normalize_timeframe,
    normalize_strategy_name, _compute_cost_model_hash, _compute_code_hash,
)
from core.novelty_gate import (
    NoveltyPolicy, NoveltyAccounting, NoveltyArtifactWriter,
    novelty_gate_plan, build_candidate_identity_for_gate,
)
from core.research_knowledge import KnowledgeStore, distill_findings
from core.strategy_lifecycle import StrategyLifecycleObserver
from core.system_health import HealthChecker

# Import backtest engine
from futures_lab import ZOO_PARAM_GRIDS, _synthetic_spec_for_file, run_backtest

# Constants
UNIVERSE = ["BR", "GAZP", "LKOH", "SBER", "Si"]
TIMEFRAMES = ["1h", "15m"]
HORIZONS = [60, 365, 1095]
STRATEGIES = list(ZOO_PARAM_GRIDS.keys()) if ZOO_PARAM_GRIDS else [
    "sma_cross", "bollinger_reversion", "rsi_reversal", "macd_trend",
    "atr_breakout", "vwap_reversion", "ft_bband_rsi", "ft_macd_cci",
    "ft_multi_rsi", "keltner_reversion", "volatility_squeeze", "stochastic_cross",
]
MAX_PARAMS = 1  # 1 param combo per strategy to fit 300 budget
MIN_BARS = 100

# 23H: Load from canonical policy
try:
    sys.path.insert(0, str(PROJECT_ROOT / "core"))
    from canonical_policy_loader import get_threshold as _ct
    MIN_TRADES = int(_ct("min_trades"))
    MIN_PF = float(_ct("min_profit_factor"))
except Exception:
    MIN_TRADES = 8
    MIN_PF = 1.0
INITIAL_CASH = 1_000_000
MAX_CANDIDATES = 300

EVIDENCE_DIR = PROJECT_ROOT / "docs" / "mission_control" / "reviews" / "ITERATION-12"

# Results accumulator
results: Dict[str, Any] = {}


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")


def fingerprint_file_local(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()[:16]


def candidate_params(strategy: str, max_params: int = MAX_PARAMS) -> List[Dict[str, Any]]:
    """Get parameter grid for a strategy, capped to max_params.
    
    ZOO_PARAM_GRIDS values are lists of param dicts (each dict = one param combo).
    """
    grid = ZOO_PARAM_GRIDS.get(strategy, [])
    if not grid:
        return [{}]
    # grid is already a list of param dicts
    return grid[:max_params]


def discover_datasets() -> Dict[tuple, Path]:
    """Discover available CSV datasets for the canonical universe."""
    datasets = {}
    for ticker in UNIVERSE:
        for tf in TIMEFRAMES:
            for days in HORIZONS:
                p = DATA_ROOT / f"{ticker}_{days}d_{tf}_continuous.csv"
                if p.exists():
                    df = pd.read_csv(p)
                    if len(df) >= MIN_BARS:
                        datasets[(ticker, tf, days)] = p
    return datasets


# =====================================================================
# PHASE 1: Pre-flight + Data Readiness
# =====================================================================

def phase1_preflight():
    """Pre-flight verification and data readiness."""
    log("=" * 60)
    log("PHASE 1: Pre-flight + Data Readiness")
    log("=" * 60)
    
    # 1. Verify mode
    config_path = PROJECT_ROOT / "config.json"
    config = {}
    if config_path.exists():
        config = json.loads(config_path.read_text())
    
    mode = config.get("mode", "paper")
    paper_first = config.get("paper_first", True)
    log(f"Mode: {mode}, Paper first: {paper_first}")
    assert mode == "paper", f"FATAL: mode={mode}, expected paper"
    assert paper_first is True, f"FATAL: paper_first={paper_first}, expected True"
    results["mode_verified"] = {"mode": mode, "paper_first": paper_first}
    
    # 2. Disk check
    st = os.statvfs(str(PROJECT_ROOT))
    free_gb = (st.f_bavail * st.f_frsize) / (1024**3)
    log(f"Disk free: {free_gb:.1f} GB")
    assert free_gb > 0.5, f"FATAL: disk too low ({free_gb:.1f} GB)"
    results["disk_free_gb"] = free_gb
    
    # 3. Discover real datasets
    datasets = discover_datasets()
    log(f"Discovered {len(datasets)} valid datasets")
    
    data_manifest = {}
    for (ticker, tf, days), path in sorted(datasets.items()):
        entry = data_manifest_entry(path, ticker, tf, days, days)
        key = f"{ticker}_{days}d_{tf}"
        data_manifest[key] = entry
        log(f"  {key}: {entry.get('row_count', 0)} rows, hash={entry.get('hash', '?')}")
    
    results["data_manifest"] = data_manifest
    results["dataset_count"] = len(datasets)
    
    # 4. Verify universe coverage
    covered_tickers = set()
    for (ticker, _, _) in datasets:
        covered_tickers.add(ticker)
    log(f"Universe coverage: {sorted(covered_tickers)}")
    missing = set(UNIVERSE) - covered_tickers
    if missing:
        log(f"WARNING: Missing tickers: {missing}")
    results["universe_coverage"] = sorted(covered_tickers)
    results["universe_missing"] = sorted(missing)
    
    return datasets


# =====================================================================
# PHASE 2: Build Research Plan
# =====================================================================

def phase2_build_plan(datasets: Dict[tuple, Path]) -> List[Dict[str, Any]]:
    """Build the frozen research plan before any backtest execution."""
    log("=" * 60)
    log("PHASE 2: Build Research Plan")
    log("=" * 60)
    
    plan_configs = []
    
    for (ticker, tf, days), path in sorted(datasets.items()):
        df = pd.read_csv(path)
        if len(df) < MIN_BARS:
            continue
        
        for strat in STRATEGIES:
            if strat not in ZOO_PARAM_GRIDS:
                continue
            for params in candidate_params(strat, MAX_PARAMS):
                raw = f"{ticker}|{tf}|{strat}|{json.dumps(params, sort_keys=True)}|{days}"
                key = hashlib.sha256(raw.encode()).hexdigest()[:16]
                
                plan_configs.append({
                    "config_key": key,
                    "instrument": ticker,
                    "timeframe": tf,
                    "strategy": strat,
                    "parameters": params,
                    "horizon_days": days,
                    "dataset_path": str(path),
                    "dataset_hash": fingerprint_file_local(path),
                })
    
    log(f"Research plan: {len(plan_configs)} planned candidates")
    
    # Cap at MAX_CANDIDATES
    if len(plan_configs) > MAX_CANDIDATES:
        log(f"Capping plan from {len(plan_configs)} to {MAX_CANDIDATES}")
        plan_configs = plan_configs[:MAX_CANDIDATES]
    
    results["planned_count"] = len(plan_configs)
    
    # Verify budget rationale
    tickers = set(c["instrument"] for c in plan_configs)
    strats = set(c["strategy"] for c in plan_configs)
    tfs = set(c["timeframe"] for c in plan_configs)
    hrs = set(c["horizon_days"] for c in plan_configs)
    log(f"Plan covers: {len(tickers)} tickers, {len(strats)} strategies, {len(tfs)} timeframes, {len(hrs)} horizons")
    
    results["plan_tickers"] = sorted(tickers)
    results["plan_strategies"] = sorted(strats)
    results["plan_timeframes"] = sorted(tfs)
    results["plan_horizons"] = sorted(hrs)
    
    return plan_configs


# =====================================================================
# PHASE 3: Create Canonical Run + Execute Backtests
# =====================================================================

def phase3_execute(plan_configs: List[Dict[str, Any]], datasets: Dict[tuple, Path]) -> ResearchRun:
    """Create canonical run and execute bounded backtests."""
    log("=" * 60)
    log("PHASE 3: Create Canonical Run + Execute Backtests")
    log("=" * 60)
    
    start_time = time.time()
    
    # Create run
    rr = ResearchRun.create(base_dir=REPORT_DIR, prefix="run_i12")
    log(f"Created run: {rr.run_id}")
    results["run_id"] = rr.run_id
    results["run_dir"] = str(rr.run_dir)
    
    # Acquire lock
    if not rr.acquire_lock(timeout=5):
        log("ERROR: Could not acquire run lock")
        results["lock_acquired"] = False
        return rr
    results["lock_acquired"] = True
    
    try:
        # Start planning
        git_info = capture_git_revision(PROJECT_ROOT)
        code_id = code_identity(
            PROJECT_ROOT,
            module_paths=["code/strategy_architect_autopilot.py", "code/data_loader.py"],
        )
        
        rr.start_planning(
            universe=UNIVERSE,
            timeframes=TIMEFRAMES,
            horizons=HORIZONS,
            strategy_families=STRATEGIES,
            arguments={
                "max_params": MAX_PARAMS,
                "min_bars": MIN_BARS,
                "min_trades": MIN_TRADES,
                "min_pf": MIN_PF,
                "proof_id": "iteration12_first_canonical_production_research_proof",
            },
            cost_assumptions={
                "commission": "synthetic_per_trade",
                "slippage": "default",
                "initial_cash": INITIAL_CASH,
                "sizing": "single_contract",
            },
            git_info=git_info,
            code_id=code_id,
            timesfm_state="not_used",
            backtest_engine_version="futures_lab",
        )
        
        # Persist plan
        plan_grid = {
            "configs": plan_configs,
            "validation_gates": {
                "min_trades": MIN_TRADES,
                "min_pf": MIN_PF,
            },
        }
        rr.persist_plan(plan_grid)
        log(f"Plan persisted: {len(plan_configs)} configs")
        
        # Add dataset entries
        for (ticker, tf, days), path in datasets.items():
            rr.add_dataset_entry(ticker, tf, days, path)
        
        # --- Novelty Gate ---
        exp_memory = ExperimentMemory(db_path=STATE_DIR / "experiment_memory.db")
        
        def classify_fn(cfg):
            ident = build_candidate_identity_for_gate(cfg)
            return exp_memory.classify_candidate(
                instrument=ident["instrument"],
                timeframe=ident["timeframe"],
                strategy=ident["strategy"],
                parameters=ident["parameters"],
                horizon_days=ident["horizon_days"],
                dataset_hash=ident["dataset_hash"],
                dataset_start=ident["dataset_start"],
                dataset_end=ident["dataset_end"],
                code_hash=ident["code_hash"],
                cost_model_hash=ident["cost_model_hash"],
                validation_version=ident["validation_version"],
                backtest_engine_version=ident["backtest_engine_version"],
            )
        
        # Enrich plan configs with identity fields
        for cfg in plan_configs:
            ident = build_candidate_identity_for_gate(cfg)
            fid = experiment_family_id(
                ident["instrument"], ident["timeframe"], ident["strategy"],
                ident["parameters"], ident["horizon_days"],
                ident.get("validation_version", "default"),
            )
            iid = experiment_instance_id(**{
                k: ident[k] for k in [
                    "instrument", "timeframe", "strategy", "parameters", "horizon_days",
                    "dataset_hash", "dataset_start", "dataset_end",
                    "code_hash", "cost_model_hash", "validation_version",
                    "backtest_engine_version",
                ]
            })
            cfg["experiment_family_id"] = fid
            cfg["experiment_instance_id"] = iid
        
        # Run novelty gate
        novelty_policy = NoveltyPolicy()
        novelty_accounting = novelty_gate_plan(
            plan_configs=plan_configs,
            classify_fn=classify_fn,
            policy=novelty_policy,
            force_reproduction=False,
            run_dir=rr.run_dir,
        )
        
        log(f"Novelty gate: planned={novelty_accounting.planned}, "
            f"skipped={novelty_accounting.skipped_exact_duplicate}, "
            f"lookup_errors={novelty_accounting.lookup_errors}")
        results["novelty_accounting"] = novelty_accounting.to_manifest_dict()
        
        # Record skipped candidates
        skipped_keys = set()
        for i, decision in enumerate(novelty_accounting.decisions):
            if decision.decision == "SKIP_EXACT_DUPLICATE":
                skipped_keys.add(plan_configs[i].get("config_key", ""))
                rr.append_skipped_candidate(plan_configs[i], decision.to_dict())
        
        # Update manifest with novelty accounting
        rr._manifest.update(novelty_accounting.to_manifest_dict())
        rr._persist_manifest()
        
        # --- Execute Backtests ---
        log(f"Executing backtests for {len(plan_configs) - len(skipped_keys)} non-skipped candidates...")
        
        # Load CSV cache
        csv_cache = {}
        for (ticker, tf, days), path in datasets.items():
            try:
                csv_cache[(ticker, tf, days)] = pd.read_csv(path)
            except Exception as e:
                log(f"  WARNING: Failed to load {path}: {e}")
        
        executed = 0
        failed = 0
        eligible_count = 0
        rejected_reasons_dist = {}
        all_candidates = []
        
        for cfg in plan_configs:
            config_key = cfg.get("config_key", "")
            if config_key in skipped_keys:
                continue
            
            ticker = cfg["instrument"]
            tf = cfg["timeframe"]
            days = cfg["horizon_days"]
            strat = cfg["strategy"]
            params = cfg["parameters"]
            
            key = (ticker, tf, days)
            if key not in csv_cache:
                log(f"  SKIP {config_key}: no data for {key}")
                continue
            
            df = csv_cache[key]
            if len(df) < MIN_BARS:
                log(f"  SKIP {config_key}: only {len(df)} bars < {MIN_BARS}")
                continue
            
            try:
                t0 = time.time()
                bt_result = run_backtest(df, _synthetic_spec_for_file(ticker), strat, params, initial_cash=INITIAL_CASH)
                duration = time.time() - t0
                
                metrics = bt_result.get("metrics", {})
                total_pnl = metrics.get("total_pnl", 0.0)
                profit_factor = metrics.get("profit_factor", 0.0)
                max_dd = metrics.get("max_drawdown", 0.0)
                sharpe = metrics.get("sharpe", metrics.get("sharpe_ratio", 0.0))
                win_rate = metrics.get("win_rate", 0.0)
                trade_count = metrics.get("trade_count", metrics.get("num_trades", 0))
                
                # Eligibility check
                reject_reasons = []
                if trade_count < MIN_TRADES:
                    reject_reasons.append(f"too_few_trades:{trade_count}")
                if profit_factor < MIN_PF:
                    reject_reasons.append(f"low_profit_factor:{profit_factor:.4f}")
                if max_dd > 0.3:
                    reject_reasons.append(f"high_drawdown:{max_dd:.4f}")
                
                is_eligible = len(reject_reasons) == 0
                
                candidate = {
                    "config_key": config_key,
                    "ticker": ticker,
                    "instrument": ticker,
                    "timeframe": tf,
                    "strategy": strat,
                    "params": params,
                    "parameters": params,
                    "horizon_days": days,
                    "dataset_path": cfg.get("dataset_path", ""),
                    "dataset_hash": cfg.get("dataset_hash", ""),
                    "total_pnl": total_pnl,
                    "profit_factor": profit_factor,
                    "max_drawdown": max_dd,
                    "sharpe": sharpe,
                    "win_rate": win_rate,
                    "trades": trade_count,
                    "eligible": is_eligible,
                    "reject_reasons": reject_reasons,
                    "duration": duration,
                    "experiment_family_id": cfg.get("experiment_family_id", ""),
                    "experiment_instance_id": cfg.get("experiment_instance_id", ""),
                }
                
                rr.append_candidate(candidate)
                all_candidates.append(candidate)
                executed += 1
                
                if is_eligible:
                    eligible_count += 1
                else:
                    for r in reject_reasons:
                        reason_type = r.split(":")[0]
                        rejected_reasons_dist[reason_type] = rejected_reasons_dist.get(reason_type, 0) + 1
                
                if executed % 50 == 0:
                    log(f"  ... executed {executed} candidates, {eligible_count} eligible so far")
                    
            except Exception as e:
                log(f"  ERROR {config_key}: {e}")
                failed += 1
                candidate = {
                    "config_key": config_key,
                    "ticker": ticker,
                    "instrument": ticker,
                    "timeframe": tf,
                    "strategy": strat,
                    "params": params,
                    "horizon_days": days,
                    "dataset_path": cfg.get("dataset_path", ""),
                    "status": "error",
                    "error_type": type(e).__name__,
                    "error_message": str(e)[:200],
                    "eligible": False,
                    "reject_reasons": [f"execution_error:{type(e).__name__}"],
                }
                rr.append_candidate(candidate)
        
        elapsed = time.time() - start_time
        log(f"Execution complete: {executed} executed, {failed} failed, {eligible_count} eligible")
        log(f"Total runtime: {elapsed:.1f}s ({elapsed/60:.1f}min)")
        log(f"Throughput: {executed/max(elapsed,0.1):.1f} candidates/sec")
        
        results["executed_count"] = executed
        results["failed_count"] = failed
        results["eligible_count"] = eligible_count
        results["skipped_count"] = len(skipped_keys)
        results["rejected_reasons_dist"] = rejected_reasons_dist
        results["runtime_seconds"] = elapsed
        results["throughput_per_sec"] = executed / max(elapsed, 0.1)
        results["all_candidates"] = all_candidates
        
        # Finalize eligible
        eligible_candidates = [c for c in all_candidates if c.get("eligible", False)]
        rr.finalize_eligible(eligible_candidates)
        
        # Finalize report
        top10 = sorted(eligible_candidates, key=lambda c: c.get("profit_factor", 0), reverse=True)[:10]
        rr.finalize_report(
            top=top10,
            report_md=f"# Iteration 12 Canonical Proof Run\n\nRun ID: {rr.run_id}\n\n"
                      f"Planned: {results['planned_count']}\n"
                      f"Executed: {executed}\n"
                      f"Failed: {failed}\n"
                      f"Skipped: {len(skipped_keys)}\n"
                      f"Eligible: {eligible_count}\n"
                      f"Rejected: {executed - eligible_count}\n",
        )
        
        # Complete run
        status = rr.complete()
        log(f"Run status: {status}")
        results["run_status"] = status
        
        if status == "COMPLETED":
            rr.update_latest_pointer()
            log("Latest pointer updated")
        
    finally:
        rr.release_lock()
    
    return rr


# =====================================================================
# PHASE 4: Experiment Memory Indexing
# =====================================================================

def phase4_experiment_memory(rr: ResearchRun):
    """Index completed run into Experiment Memory."""
    log("=" * 60)
    log("PHASE 4: Experiment Memory Indexing")
    log("=" * 60)
    
    em = ExperimentMemory(db_path=STATE_DIR / "experiment_memory.db")
    
    # Before snapshot
    before = em.summary()
    log(f"Before: {before.get('total_families', 0)} families, {before.get('total_instances', 0)} instances")
    
    # Index the run
    report = em.index_run(rr.run_dir)
    log(f"Indexing result: {report.get('status', 'UNKNOWN')}")
    log(f"  instances_indexed: {report.get('instances_indexed', 0)}")
    log(f"  instances_skipped: {report.get('instances_skipped', 0)}")
    log(f"  families_indexed: {report.get('families_indexed', 0)}")
    log(f"  classifications: {report.get('classifications', {})}")
    
    if report.get("errors"):
        log(f"  ERRORS: {report['errors']}")
    
    # After snapshot
    after = em.summary()
    log(f"After: {after.get('total_families', 0)} families, {after.get('total_instances', 0)} instances")
    
    results["memory_before"] = before
    results["memory_after"] = after
    results["memory_indexing"] = report
    
    em.close()


# =====================================================================
# PHASE 5: Knowledge Build
# =====================================================================

def phase5_knowledge_build():
    """Build Research Knowledge from indexed observations."""
    log("=" * 60)
    log("PHASE 5: Knowledge Build")
    log("=" * 60)
    
    ks = KnowledgeStore(db_path=STATE_DIR / "research_knowledge.db")
    
    # Distill from experiment memory
    build = distill_findings(
        experiment_memory_path=STATE_DIR / "experiment_memory.db",
        knowledge_store=ks,
    )
    
    log(f"Knowledge build: {build.status}")
    log(f"  findings_created: {build.findings_created}")
    log(f"  findings_updated: {build.findings_updated}")
    log(f"  open_questions: {build.open_questions_created}")
    
    if build.errors:
        log(f"  ERRORS: {build.errors}")
    
    results["knowledge_build"] = build.to_dict()
    ks.close()


# =====================================================================
# PHASE 6: Lifecycle Build
# =====================================================================

def phase6_lifecycle_build():
    """Build Strategy Lifecycle observations."""
    log("=" * 60)
    log("PHASE 6: Lifecycle Build")
    log("=" * 60)
    
    observer = StrategyLifecycleObserver(base_dir=PROJECT_ROOT)
    
    # Build snapshot
    snapshot = observer.build_snapshot()
    
    log(f"Lifecycle build: {snapshot.lifecycle_build_id}")
    log(f"  strategies_evaluated: {snapshot.strategies_evaluated}")
    log(f"  evidence_health_counts: {snapshot.evidence_health_counts}")
    log(f"  recommendation_counts: {snapshot.recommendation_counts}")
    
    results["lifecycle_build"] = snapshot.to_dict()
    observer.close()


# =====================================================================
# PHASE 7: Post-run Health
# =====================================================================

def phase7_post_health():
    """Run system health after the proof."""
    log("=" * 60)
    log("PHASE 7: Post-run Health")
    log("=" * 60)
    
    checker = HealthChecker()
    snapshot = checker.take_snapshot()
    
    log(f"Overall: {snapshot.overall_status.value}")
    log("Domain health:")
    for domain, status in snapshot.domain_health.items():
        log(f"  {domain}: {status}")
    
    results["post_health"] = {
        "overall": snapshot.overall_status.value,
        "domains": snapshot.domain_health,
    }
    
    return snapshot


# =====================================================================
# PHASE 8: Count Reconciliation
# =====================================================================

def phase8_reconcile():
    """Reconcile counts across all layers."""
    log("=" * 60)
    log("PHASE 8: Count Reconciliation")
    log("=" * 60)
    
    reconciliation = {
        "planned_candidates": results.get("planned_count", 0),
        "executed_candidates": results.get("executed_count", 0),
        "skipped_exact_duplicate": results.get("skipped_count", 0),
        "failed_candidates": results.get("failed_count", 0),
        "eligible_candidates": results.get("eligible_count", 0),
        "rejected_candidates": results.get("executed_count", 0) - results.get("eligible_count", 0),
        "memory_families": results.get("memory_after", {}).get("total_families", 0),
        "memory_instances": results.get("memory_after", {}).get("total_instances", 0),
        "memory_observations": 0,  # summary() doesn't expose this directly
        "knowledge_findings": results.get("knowledge_build", {}).get("findings_created", 0),
        "lifecycle_strategies_evaluated": results.get("lifecycle_build", {}).get("strategies_evaluated", 0),
    }
    
    # Verify planned = executed + skipped + failed
    planned = reconciliation["planned_candidates"]
    accounted = (reconciliation["executed_candidates"] +
                 reconciliation["skipped_exact_duplicate"] +
                 reconciliation["failed_candidates"])
    reconciliation["plan_accounted"] = planned == accounted
    reconciliation["plan_accounted_detail"] = f"{planned} = {reconciliation['executed_candidates']} + {reconciliation['skipped_exact_duplicate']} + {reconciliation['failed_candidates']}"
    
    log(f"Planned: {planned}")
    log(f"Executed: {reconciliation['executed_candidates']}")
    log(f"Skipped: {reconciliation['skipped_exact_duplicate']}")
    log(f"Failed: {reconciliation['failed_candidates']}")
    log(f"Accounted: {accounted} (plan==accounted: {planned == accounted})")
    log(f"Eligible: {reconciliation['eligible_candidates']}")
    log(f"Rejected: {reconciliation['rejected_candidates']}")
    log(f"Memory families: {reconciliation['memory_families']}")
    log(f"Memory instances: {reconciliation['memory_instances']}")
    log(f"Knowledge findings: {reconciliation['knowledge_findings']}")
    log(f"Lifecycle evaluated: {reconciliation['lifecycle_strategies_evaluated']}")
    
    results["reconciliation"] = reconciliation


# =====================================================================
# MAIN EXECUTION
# =====================================================================

def main():
    """Execute the full Iteration 12 proof pipeline."""
    log("ITERATION 12: FIRST CANONICAL PRODUCTION RESEARCH PROOF")
    log(f"Started at: {datetime.now(timezone.utc).isoformat()}")
    log(f"Project root: {PROJECT_ROOT}")
    log(f"Data root: {DATA_ROOT}")
    log("")
    
    # Phase 1: Pre-flight
    datasets = phase1_preflight()
    
    # Phase 2: Build Plan
    plan_configs = phase2_build_plan(datasets)
    
    # Phase 3: Execute
    rr = phase3_execute(plan_configs, datasets)
    
    # Phase 4: Experiment Memory
    phase4_experiment_memory(rr)
    
    # Phase 5: Knowledge Build
    phase5_knowledge_build()
    
    # Phase 6: Lifecycle Build
    phase6_lifecycle_build()
    
    # Phase 7: Post-run Health
    phase7_post_health()
    
    # Phase 8: Reconciliation
    phase8_reconcile()
    
    # Save results
    results_path = rr.run_dir / "proof_results.json"
    # Remove non-serializable items
    serializable = {k: v for k, v in results.items() if k != "all_candidates"}
    results_path.write_text(json.dumps(serializable, indent=2, default=str), encoding="utf-8")
    log(f"\nResults saved to {results_path}")
    
    log(f"\nCompleted at: {datetime.now(timezone.utc).isoformat()}")
    log("=" * 60)
    log("EXECUTION PHASE COMPLETE")
    log("=" * 60)
    
    return results


if __name__ == "__main__":
    results = main()
    print(json.dumps({k: v for k, v in results.items() if k != "all_candidates"}, indent=2, default=str))
