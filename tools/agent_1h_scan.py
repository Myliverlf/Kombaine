#!/usr/bin/env python3
"""1h strategy-combine net scan with robustness checks.

Scans a focused futures universe on 1h continuous CSVs, ranks by net return,
then checks top candidates for parameter-neighborhood fragility and longer-horizon
profitability on 365d / 1095d histories where available.
"""

from __future__ import annotations

import csv
import importlib.util
import itertools
import json
import sys
import warnings
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

ROOT = Path("/root/prop-desk/strategy_combine")
REPO_ROOT = Path("/root/prop-desk")
FUTURES_ROOT = Path("/root/prop-desk/futures_lab")
DATA_ROOT = Path("/root/prop-desk/futures_lab/artifacts/tinkoff_futures_data")
REPORT_PATH = ROOT / "reports/strategy_architect/agent_1h_net_scan.json"

sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(FUTURES_ROOT))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "code"))


def _ensure_config_package() -> None:
    """Force the real config package to win over the legacy config.py module."""
    if getattr(sys.modules.get("config"), "__path__", None):
        return
    pkg_init = REPO_ROOT / "config" / "__init__.py"
    spec = importlib.util.spec_from_file_location(
        "config",
        pkg_init,
        submodule_search_locations=[str(pkg_init.parent)],
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load config package from {pkg_init}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["config"] = module
    spec.loader.exec_module(module)

    acc_path = REPO_ROOT / "config" / "account_profile.py"
    acc_spec = importlib.util.spec_from_file_location("config.account_profile", acc_path)
    if acc_spec is None or acc_spec.loader is None:
        raise RuntimeError(f"Unable to load config.account_profile from {acc_path}")
    acc_module = importlib.util.module_from_spec(acc_spec)
    sys.modules["config.account_profile"] = acc_module
    acc_spec.loader.exec_module(acc_module)


_ensure_config_package()

warnings.filterwarnings("ignore", category=FutureWarning)

_futures_impl = FUTURES_ROOT / "futures_lab.py"
_futures_spec = importlib.util.spec_from_file_location("live_futures_lab", _futures_impl)
if _futures_spec is None or _futures_spec.loader is None:
    raise RuntimeError(f"Unable to load futures_lab implementation from {_futures_impl}")
_futures_mod = importlib.util.module_from_spec(_futures_spec)
sys.modules[_futures_spec.name] = _futures_mod
_futures_spec.loader.exec_module(_futures_mod)
run_backtest = _futures_mod.run_backtest
_synthetic_spec_for_file = _futures_mod._synthetic_spec_for_file

from strategy_zoo import PARAM_GRIDS  # noqa: E402

UNIVERSE = ["BR", "GAZP", "SBER", "CNY", "EURRUB", "USDRUB", "IMOEX", "NG", "LKOH", "Si"]
SCAN_HORIZON = 60
LONG_HORIZONS = [365, 1095]
SCAN_HORIZONS = [SCAN_HORIZON]
ALL_HORIZONS = [SCAN_HORIZON, *LONG_HORIZONS]
INITIAL_CASH = 20_000.0
COMMISSION = 1.5
SLIPPAGE_BPS = 2.0
MAX_PARAMS_PER_STRATEGY = 8
TOP_N = 10


@dataclass
class ScanRow:
    ticker: str
    horizon: int
    strategy: str
    params: dict[str, Any]
    param_index: int
    trade_count: int
    total_pnl: float
    pnl_pct: float
    profit_factor: float
    max_drawdown: float
    max_drawdown_pct: float
    sharpe: float
    final_equity: float
    file_path: str


def json_default(obj: Any):
    if isinstance(obj, (pd.Timestamp,)):
        return obj.isoformat()
    if isinstance(obj, (pd.Timedelta,)):
        return str(obj)
    if isinstance(obj, (set, frozenset)):
        return sorted(list(obj))
    if hasattr(obj, "item"):
        try:
            return obj.item()
        except Exception:
            pass
    if hasattr(obj, "tolist"):
        try:
            return obj.tolist()
        except Exception:
            pass
    return str(obj)


def load_csv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    if "time" in df.columns:
        df["time"] = pd.to_datetime(df["time"], utc=True, errors="coerce")
    return df


def available_files() -> dict[str, dict[int, Path]]:
    out: dict[str, dict[int, Path]] = {ticker: {} for ticker in UNIVERSE}
    for ticker in UNIVERSE:
        for horizon in ALL_HORIZONS:
            path = DATA_ROOT / f"{ticker}_{horizon}d_1h_continuous.csv"
            if path.exists():
                out[ticker][horizon] = path
    return out


def make_param_grids() -> dict[str, list[dict[str, Any]]]:
    return {strategy: list(grid) for strategy, grid in PARAM_GRIDS.items()}


def unique_sorted_values(grid: list[dict[str, Any]], key: str) -> list[Any]:
    vals = []
    for row in grid:
        if key in row and row[key] not in vals:
            vals.append(row[key])
    try:
        return sorted(vals)
    except Exception:
        return vals


def param_neighbors(strategy: str, params: dict[str, Any], full_grid: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return one-step neighbors in the parameter grid.

    A neighbor changes exactly one parameter by one grid step (previous/next value)
    and must exist in the strategy's explicit grid.
    """
    if not full_grid:
        return []

    keys = list(params.keys())
    value_sets = {k: unique_sorted_values(full_grid, k) for k in keys}
    grid_lookup = {tuple(sorted(row.items())) for row in full_grid}
    neighbors: list[dict[str, Any]] = []

    for key in keys:
        vals = value_sets.get(key, [])
        if len(vals) < 2:
            continue
        cur = params[key]
        if cur not in vals:
            continue
        idx = vals.index(cur)
        adjacent = []
        if idx - 1 >= 0:
            adjacent.append(vals[idx - 1])
        if idx + 1 < len(vals):
            adjacent.append(vals[idx + 1])
        for new_val in adjacent:
            cand = dict(params)
            cand[key] = new_val
            if tuple(sorted(cand.items())) in grid_lookup:
                neighbors.append(cand)
    # dedupe while preserving order
    seen = set()
    uniq: list[dict[str, Any]] = []
    for row in neighbors:
        key = tuple(sorted(row.items()))
        if key not in seen:
            seen.add(key)
            uniq.append(row)
    return uniq


def run_one(df: pd.DataFrame, ticker: str, strategy: str, params: dict[str, Any], file_path: Path):
    spec = _synthetic_spec_for_file(ticker)
    metrics, trades, _eq = run_backtest(
        df,
        spec,
        strategy,
        params,
        initial_cash=INITIAL_CASH,
        commission_per_contract=COMMISSION,
        slippage_bps=SLIPPAGE_BPS,
        debug_only=True,
    )
    total_pnl = float(metrics.get("total_pnl", 0.0))
    dd_abs = float(metrics.get("max_drawdown", 0.0))
    row = ScanRow(
        ticker=ticker,
        horizon=int(file_path.name.split("_")[1].replace("d", "")),
        strategy=strategy,
        params=params,
        param_index=-1,
        trade_count=int(metrics.get("trade_count", 0)),
        total_pnl=total_pnl,
        pnl_pct=total_pnl / INITIAL_CASH * 100.0,
        profit_factor=float(metrics.get("profit_factor", 0.0)),
        max_drawdown=dd_abs,
        max_drawdown_pct=dd_abs / INITIAL_CASH * 100.0,
        sharpe=float(metrics.get("sharpe", 0.0)),
        final_equity=float(metrics.get("final_equity", INITIAL_CASH)),
        file_path=str(file_path),
    )
    return row, metrics, trades


def candidate_tasks(files: dict[str, dict[int, Path]], grids: dict[str, list[dict[str, Any]]]):
    return [
        {
            "ticker": ticker,
            "files": {h: str(files[ticker][h]) for h in SCAN_HORIZONS if h in files[ticker]},
            "grids": grids,
        }
        for ticker in UNIVERSE
        if any(h in files[ticker] for h in SCAN_HORIZONS)
    ]


def _worker_backtest(task: dict[str, Any]) -> list[dict[str, Any]]:
    ticker = task["ticker"]
    files = task["files"]
    grids = task["grids"]
    cached = {int(h): load_csv(Path(path_str)) for h, path_str in files.items()}
    outputs = []
    for strategy, grid in grids.items():
        for idx, params in enumerate(grid[:MAX_PARAMS_PER_STRATEGY]):
            for horizon, df in cached.items():
                path = Path(files[horizon])
                try:
                    row, _metrics, _trades = run_one(df, ticker, strategy, params, path)
                    row.param_index = idx
                    outputs.append(asdict(row))
                except Exception as exc:
                    bad = asdict(
                        ScanRow(
                            ticker=ticker,
                            horizon=horizon,
                            strategy=strategy,
                            params=params,
                            param_index=idx,
                            trade_count=0,
                            total_pnl=float("nan"),
                            pnl_pct=float("nan"),
                            profit_factor=float("nan"),
                            max_drawdown=float("nan"),
                            max_drawdown_pct=float("nan"),
                            sharpe=float("nan"),
                            final_equity=float("nan"),
                            file_path=str(path),
                        )
                    )
                    bad["error"] = str(exc)
                    outputs.append(bad)
    return outputs


def scan_universe():
    files = available_files()
    grids = make_param_grids()
    rows: list[ScanRow] = []
    missing_files: list[str] = []
    data_cache: dict[Path, pd.DataFrame] = {}

    for ticker in UNIVERSE:
        for horizon, path in sorted(files[ticker].items()):
            data_cache[path] = load_csv(path)

    tasks = candidate_tasks(files, grids)
    max_workers = min(8, len(tasks) or 1)
    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(_worker_backtest, task) for task in tasks]
        for fut in as_completed(futures):
            for item in fut.result():
                row = ScanRow(
                    ticker=item["ticker"],
                    horizon=int(item["horizon"]),
                    strategy=item["strategy"],
                    params=item["params"],
                    param_index=int(item["param_index"]),
                    trade_count=int(item["trade_count"]),
                    total_pnl=float(item["total_pnl"]),
                    pnl_pct=float(item["pnl_pct"]),
                    profit_factor=float(item["profit_factor"]),
                    max_drawdown=float(item["max_drawdown"]),
                    max_drawdown_pct=float(item["max_drawdown_pct"]),
                    sharpe=float(item["sharpe"]),
                    final_equity=float(item["final_equity"]),
                    file_path=item["file_path"],
                )
                rows.append(row)
                if "error" in item:
                    missing_files.append(f"{row.ticker} {row.horizon}d {row.strategy} {row.params} :: {item['error']}")
    return rows, grids, files, missing_files, data_cache


def scan_long_horizons(rows: list[ScanRow], data_cache: dict[Path, pd.DataFrame], files: dict[str, dict[int, Path]]) -> list[dict[str, Any]]:
    long_rows: list[dict[str, Any]] = []
    combos = {(r.ticker, r.strategy, tuple(sorted(r.params.items()))) for r in rows if r.horizon == SCAN_HORIZON}
    for ticker, strategy, _param_key in combos:
        base_rows = [r for r in rows if r.horizon == SCAN_HORIZON and r.ticker == ticker and r.strategy == strategy]
        if not base_rows:
            continue
        for base in base_rows[:MAX_PARAMS_PER_STRATEGY]:
            for horizon in LONG_HORIZONS:
                path = files.get(ticker, {}).get(horizon)
                if path is None:
                    continue
                df = data_cache[path]
                try:
                    row, _metrics, _trades = run_one(df, ticker, strategy, base.params, path)
                    long_rows.append(asdict(row))
                except Exception as exc:
                    bad = asdict(
                        ScanRow(
                            ticker=ticker,
                            horizon=horizon,
                            strategy=strategy,
                            params=base.params,
                            param_index=base.param_index,
                            trade_count=0,
                            total_pnl=float("nan"),
                            pnl_pct=float("nan"),
                            profit_factor=float("nan"),
                            max_drawdown=float("nan"),
                            max_drawdown_pct=float("nan"),
                            sharpe=float("nan"),
                            final_equity=float("nan"),
                            file_path=str(path),
                        )
                    )
                    bad["error"] = str(exc)
                    long_rows.append(bad)
    return long_rows


def annualized_pct(row: ScanRow) -> float | None:
    """Normalize pnl_pct to %/year so different horizons are comparable."""
    if pd.isna(row.pnl_pct) or row.horizon <= 0:
        return None
    return float(row.pnl_pct) * 365.0 / float(row.horizon)


def rank_rows(rows: list[ScanRow]) -> list[ScanRow]:
    return sorted(
        [r for r in rows if pd.notna(r.total_pnl)],
        key=lambda r: (r.pnl_pct, r.trade_count, -r.max_drawdown_pct),
        reverse=True,
    )


def rank_by_horizon(rows: list[ScanRow]) -> dict[int, list[ScanRow]]:
    """Rank each horizon separately — never mix 60d with 365d/1095d."""
    out: dict[int, list[ScanRow]] = {}
    for horizon in ALL_HORIZONS:
        subset = [r for r in rows if r.horizon == horizon]
        if subset:
            out[horizon] = rank_rows(subset)
    return out


def stability_check(candidate: ScanRow, grids: dict[str, list[dict[str, Any]]], data_cache: dict[Path, pd.DataFrame], files: dict[str, dict[int, Path]]):
    grid = grids.get(candidate.strategy, [])
    neighbors = param_neighbors(candidate.strategy, candidate.params, grid)
    neighbor_results = []
    fragile = False
    for params in neighbors:
        path = files[candidate.ticker].get(candidate.horizon)
        if path is None:
            continue
        df = data_cache[path]
        try:
            row, metrics, _trades = run_one(df, candidate.ticker, candidate.strategy, params, path)
            neighbor_results.append(
                {
                    "params": params,
                    "total_pnl": row.total_pnl,
                    "pnl_pct": row.pnl_pct,
                    "trade_count": row.trade_count,
                    "profit_factor": row.profit_factor,
                    "max_drawdown": row.max_drawdown,
                    "max_drawdown_pct": row.max_drawdown_pct,
                }
            )
            if row.total_pnl < 0:
                fragile = True
        except Exception as exc:
            neighbor_results.append({"params": params, "error": str(exc)})
            fragile = True
    return {
        "neighbor_count": len(neighbor_results),
        "fragile": fragile,
        "neighbor_results": neighbor_results,
        "min_neighbor_pnl": min((r.get("total_pnl", 0.0) for r in neighbor_results if "total_pnl" in r), default=None),
    }


def long_history_check(candidate: ScanRow, data_cache: dict[Path, pd.DataFrame], files: dict[str, dict[int, Path]]):
    outcomes = {}
    profit_flags = []
    for horizon in (365, 1095):
        path = files[candidate.ticker].get(horizon)
        if path is None:
            continue
        df = data_cache[path]
        try:
            row, _metrics, _trades = run_one(df, candidate.ticker, candidate.strategy, candidate.params, path)
            outcome = {
                "available": True,
                "total_pnl": row.total_pnl,
                "pnl_pct": row.pnl_pct,
                "trade_count": row.trade_count,
                "profit_factor": row.profit_factor,
                "max_drawdown": row.max_drawdown,
                "max_drawdown_pct": row.max_drawdown_pct,
                "profitable": row.total_pnl > 0,
            }
            profit_flags.append(row.total_pnl > 0)
        except Exception as exc:
            outcome = {"available": True, "error": str(exc), "profitable": False}
            profit_flags.append(False)
        outcomes[str(horizon)] = outcome
    outcomes["profitable_all_available"] = bool(profit_flags) and all(profit_flags)
    outcomes["profitable_any_available"] = any(profit_flags)
    return outcomes


def summarize(rows: list[ScanRow], grids, files, data_cache):
    ranked = rank_rows(rows)
    per_horizon = rank_by_horizon(rows)
    scan_ranked = per_horizon.get(SCAN_HORIZON, [])
    positives = [r for r in ranked if r.total_pnl > 0]
    trades_15 = [r for r in ranked if r.trade_count >= 15]
    # TOP-10 is defined on the SCAN horizon (60d) only — long horizons are
    # robustness evidence, never the headline ranking.
    scan_positives = [r for r in scan_ranked if r.total_pnl > 0]
    top10 = scan_positives[:TOP_N]
    if len(top10) < TOP_N:
        top10 = scan_ranked[:TOP_N]

    top10_payload = []
    for r in top10:
        top10_payload.append(
            {
                "ticker": r.ticker,
                "horizon": r.horizon,
                "strategy": r.strategy,
                "params": r.params,
                "trade_count": r.trade_count,
                "total_pnl": r.total_pnl,
                "pnl_pct": r.pnl_pct,
                "profit_factor": r.profit_factor,
                "max_drawdown": r.max_drawdown,
                "max_drawdown_pct": r.max_drawdown_pct,
                "sharpe": r.sharpe,
                "file_path": r.file_path,
            }
        )

    stability = []
    for r in top10:
        check = stability_check(r, grids, data_cache, files)
        long_hist = long_history_check(r, data_cache, files)
        stability.append(
            {
                "ticker": r.ticker,
                "horizon": r.horizon,
                "strategy": r.strategy,
                "params": r.params,
                "trade_count": r.trade_count,
                "net_pnl": r.total_pnl,
                "net_pnl_pct": r.pnl_pct,
                "fragile": check["fragile"],
                "neighbor_count": check["neighbor_count"],
                "min_neighbor_pnl": check["min_neighbor_pnl"],
                "neighbor_results": check["neighbor_results"],
                "long_history": long_hist,
            }
        )

    stable_survivors = [s for s in stability if (not s["fragile"]) and s["long_history"].get("profitable_any_available")]
    fragile_survivors = [s for s in stability if s["fragile"]]

    recommendations = []
    for s in stable_survivors:
        if s["trade_count"] >= 15 and s["net_pnl"] > 0:
            recommendations.append(
                {
                    "ticker": s["ticker"],
                    "strategy": s["strategy"],
                    "params": s["params"],
                    "reason": "positive on 1h, non-fragile neighbors, and profitable on at least one longer horizon",
                }
            )

    payload = {
        "meta": {
            "universe": UNIVERSE,
            "scan_horizons": SCAN_HORIZONS,
            "all_horizons": ALL_HORIZONS,
            "initial_cash": INITIAL_CASH,
            "commission_per_contract": COMMISSION,
            "slippage_bps": SLIPPAGE_BPS,
            "max_params_per_strategy": MAX_PARAMS_PER_STRATEGY,
            "scan_rows": len(rows),
            "positive_rows": sum(1 for r in rows if pd.notna(r.total_pnl) and r.total_pnl > 0),
            "trades_ge_15_rows": len(trades_15),
            "top10_count": len(top10_payload),
            "top10_horizon": SCAN_HORIZON,
            "ranking_note": "top10 is drawn from the 60d scan horizon only; 365d/1095d are robustness evidence, never mixed into the headline ranking",
        },
        "ranking": {
            "by_horizon": {
                str(h): [
                    {**asdict(r), "annualized_pct_per_year": annualized_pct(r)}
                    for r in rlist
                ]
                for h, rlist in per_horizon.items()
            },
            "all_rows": [asdict(r) for r in ranked],
            "positive_rows": [asdict(r) for r in positives],
            "trades_ge_15_rows": [asdict(r) for r in trades_15],
            "top10": top10_payload,
        },
        "stability": stability,
        "recommendations": recommendations,
    }
    return payload, ranked, positives, trades_15, top10, stability


def main():
    rows, grids, files, missing_files, data_cache = scan_universe()
    payload, ranked, positives, trades_15, top10, stability = summarize(rows, grids, files, data_cache)
    payload["issues"] = {"errors": missing_files[:200], "error_count": len(missing_files)}

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=json_default), encoding="utf-8")

    print(json.dumps(
        {
            "report_path": str(REPORT_PATH),
            "scan_rows": len(rows),
            "positive_rows": len(positives),
            "trades_ge_15_rows": len(trades_15),
            "top10": [
                {
                    "ticker": r.ticker,
                    "horizon": r.horizon,
                    "strategy": r.strategy,
                    "params": r.params,
                    "trade_count": r.trade_count,
                    "total_pnl": r.total_pnl,
                    "pnl_pct": r.pnl_pct,
                    "profit_factor": r.profit_factor,
                    "max_drawdown": r.max_drawdown,
                    "max_drawdown_pct": r.max_drawdown_pct,
                    "sharpe": r.sharpe,
                }
                for r in top10
            ],
            "stable_survivors": [
                {
                    "ticker": s["ticker"],
                    "strategy": s["strategy"],
                    "params": s["params"],
                    "fragile": s["fragile"],
                    "long_history_profitable_any": s["long_history"].get("profitable_any_available"),
                }
                for s in stability
                if (not s["fragile"]) and s["long_history"].get("profitable_any_available")
            ],
            "recommendations": payload["recommendations"],
        },
        ensure_ascii=False,
        indent=2,
        default=json_default,
    ))


if __name__ == "__main__":
    main()
