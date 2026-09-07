#!/usr/bin/env python3
"""Equity diversity filter for strategy_combine.

Rejects live-pool strategies whose full-history equity curve is too similar to
another stronger strategy. The goal is a smoother portfolio: different rising
equity curves, not 5 clones of the same idea.

Safety: local CSV/backtest only, no broker calls, no live orders.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FUTURES_LAB = PROJECT_ROOT.parent / "futures_lab"
DATA_ROOT = FUTURES_LAB / "artifacts" / "tinkoff_futures_data"
REPORT_DIR = PROJECT_ROOT / "reports" / "strategy_architect"
sys.path.insert(0, str(FUTURES_LAB))
sys.path.insert(0, str(PROJECT_ROOT / "code"))

from futures_lab import run_backtest, _synthetic_spec_for_file  # type: ignore  # noqa:E402
from strategy_registry import STATUS_ACTIVE_SIGNAL_POOL, STATUS_ACTIVE_WATCHLIST, STATUS_ROTATED_OUT, StrategyRegistry  # type: ignore  # noqa:E402

ACTIVE = {STATUS_ACTIVE_SIGNAL_POOL, STATUS_ACTIVE_WATCHLIST}


def safe_float(v: Any, default: float = 0.0) -> float:
    try:
        x = float(v)
        if math.isnan(x) or math.isinf(x):
            return default
        return x
    except Exception:
        return default


def csv_path(ticker: str, timeframe: str) -> Path:
    return DATA_ROOT / f"{ticker}_60d_{timeframe}_continuous.csv"


def corr(a: list[float], b: list[float]) -> float:
    n = min(len(a), len(b))
    if n < 5:
        return 0.0
    if len(a) != n:
        step = max(1, len(a) / n)
        a = [a[int(i * step)] for i in range(n)]
    if len(b) != n:
        step = max(1, len(b) / n)
        b = [b[int(i * step)] for i in range(n)]
    ma = sum(a) / n
    mb = sum(b) / n
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((y - mb) ** 2 for y in b)
    if va <= 0 or vb <= 0:
        if len(a) == len(b) and len(a) >= 5 and all(abs(x - y) <= 1e-9 for x, y in zip(a, b)):
            return 1.0
        return 0.0
    return max(-1.0, min(1.0, sum((x - ma) * (y - mb) for x, y in zip(a, b)) / math.sqrt(va * vb)))


def normalize_curve(vals: list[float]) -> list[float]:
    if not vals:
        return []
    start = vals[0]
    arr = [v - start for v in vals]
    mn, mx = min(arr), max(arr)
    rng = mx - mn
    if rng <= 1e-9:
        return [0.0 for _ in arr]
    return [(v - mn) / rng for v in arr]


def returns(vals: list[float]) -> list[float]:
    return [vals[i] - vals[i - 1] for i in range(1, len(vals))]


def resample(vals: list[float], n: int = 240) -> list[float]:
    if not vals:
        return []
    if len(vals) <= n:
        return vals[:]
    return [vals[int(i * (len(vals) - 1) / (n - 1))] for i in range(n)]


def timeframe(rec) -> str:
    m = rec.metrics or {}
    return str(rec.portfolio_context.get("timeframe") or m.get("timeframe") or ("15m" if "15m" in rec.strategy_id else "1h"))


def backtest_curve(rec, initial_cash: float) -> tuple[list[float], dict[str, Any]]:
    tf = timeframe(rec)
    path = csv_path(rec.ticker, tf)
    if not path.exists():
        raise FileNotFoundError(path.name)
    df = pd.read_csv(path)
    metrics, _trades, eq = run_backtest(
        df,
        _synthetic_spec_for_file(rec.ticker),
        rec.strategy,
        rec.params or {},
        initial_cash=initial_cash,
        contracts=1,
        max_contracts=1,
        stop_atr=2.0,
        take_atr=3.0,
        max_hold_bars=192 if tf == "15m" else 48,
    )
    pnl = [float(v) - initial_cash for v in eq.tolist()]
    return pnl, metrics


def quality_score(rec, metrics: dict[str, Any] | None = None) -> float:
    m = dict(rec.metrics or {})
    if metrics:
        m.update(metrics)
    return (
        safe_float(m.get("equity_shape_score"))
        + safe_float(m.get("equity_shape_r2")) * 1800.0
        + safe_float(m.get("equity_shape_positive_window_ratio")) * 1200.0
        - safe_float(m.get("equity_shape_flat_window_ratio")) * 1400.0
        - safe_float(m.get("equity_shape_dd_ratio")) * 900.0
        + safe_float(m.get("total_pnl")) * 0.25
        + safe_float(m.get("profit_factor")) * 250.0
        + safe_float(rec.active_rank) * 0.15
    )


def family_name(rec: Any) -> str:
    return str(getattr(rec, "strategy", "") or getattr(rec, "strategy_id", ""))


def build_family_summary(
    items: list[dict[str, Any]],
    keep: list[dict[str, Any]],
    rotated: list[dict[str, Any]],
    decisions: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    summary: dict[str, dict[str, Any]] = {}
    for item in items:
        fam = family_name(item["rec"])
        entry = summary.setdefault(
            fam,
            {
                "strategy": fam,
                "samples": 0,
                "kept": 0,
                "rotated": 0,
                "cluster_duplicates": 0,
                "ticker_caps": 0,
                "score_sum": 0.0,
                "score_max": float("-inf"),
            },
        )
        entry["samples"] += 1
        entry["score_sum"] += safe_float(item.get("score"))
        entry["score_max"] = max(entry["score_max"], safe_float(item.get("score")))

    for item in keep:
        fam = family_name(item["rec"])
        summary[fam]["kept"] += 1

    for item in rotated:
        fam = family_name(item["rec"])
        entry = summary[fam]
        entry["rotated"] += 1
        decision = decisions.get(item["item_idx"], {})
        if decision.get("decision") == "cluster_duplicate":
            entry["cluster_duplicates"] += 1
        elif decision.get("decision") == "ticker_cap":
            entry["ticker_caps"] += 1

    family_rows: list[dict[str, Any]] = []
    for fam, entry in summary.items():
        samples = max(1, int(entry["samples"]))
        kept = int(entry["kept"])
        rotated_count = int(entry["rotated"])
        cluster_duplicates = int(entry["cluster_duplicates"])
        ticker_caps = int(entry["ticker_caps"])
        keep_rate = kept / samples
        rotated_rate = rotated_count / samples
        score_mean = entry["score_sum"] / samples
        score_max = entry["score_max"] if entry["score_max"] != float("-inf") else 0.0
        winner_boost = round(max(0.0, kept * 30.0 + keep_rate * 70.0 + score_max * 0.05), 4)
        sideways_penalty = round(max(0.0, 0.60 - keep_rate) * 120.0 + max(0.0, rotated_count - kept) * 12.0 + max(0.0, 0.45 - rotated_rate) * 8.0, 4)
        clone_penalty = round(cluster_duplicates * 40.0 + ticker_caps * 20.0, 4)
        policy_bias = round(winner_boost - sideways_penalty - clone_penalty, 4)
        if kept > 0 and policy_bias >= 0:
            family_role = "winner"
            policy_reason = "winner_boost"
        elif cluster_duplicates > 0:
            family_role = "clone"
            policy_reason = "clone_penalty"
        elif rotated_count > 0:
            family_role = "sideways"
            policy_reason = "sideways_penalty"
        else:
            family_role = "explore"
            policy_reason = "explore_keep"
        family_rows.append(
            {
                "strategy": fam,
                "samples": samples,
                "kept": kept,
                "rotated": rotated_count,
                "keep_rate": round(keep_rate, 4),
                "rotated_rate": round(rotated_rate, 4),
                "score_mean": round(score_mean, 4),
                "score_max": round(score_max, 4),
                "winner_boost": winner_boost,
                "sideways_penalty": sideways_penalty,
                "clone_penalty": clone_penalty,
                "policy_bias": policy_bias,
                "family_role": family_role,
                "policy_reason": policy_reason,
                "cluster_duplicates": cluster_duplicates,
                "ticker_caps": ticker_caps,
            }
        )

    return sorted(family_rows, key=lambda row: (row["policy_bias"], row["winner_boost"], row["keep_rate"], row["kept"], row["strategy"]), reverse=True)


class UnionFind:
    def __init__(self, size: int):
        self.parent = list(range(size))
        self.rank = [0] * size

    def find(self, item: int) -> int:
        parent = self.parent[item]
        if parent != item:
            self.parent[item] = self.find(parent)
        return self.parent[item]

    def union(self, left: int, right: int) -> None:
        root_left = self.find(left)
        root_right = self.find(right)
        if root_left == root_right:
            return
        if self.rank[root_left] < self.rank[root_right]:
            self.parent[root_left] = root_right
            return
        if self.rank[root_left] > self.rank[root_right]:
            self.parent[root_right] = root_left
            return
        self.parent[root_right] = root_left
        self.rank[root_left] += 1


def pair_similarity(left: dict[str, Any], right: dict[str, Any], same_ticker_level_corr: float, level_corr: float, return_corr: float) -> dict[str, Any]:
    level = corr(left["norm"], right["norm"])
    retc = corr(left["ret"], right["ret"])
    same_ticker = left["rec"].ticker == right["rec"].ticker
    threshold = same_ticker_level_corr if same_ticker else level_corr
    similar = level >= threshold and retc >= return_corr
    return {
        "a": left["rec"].strategy_id,
        "b": right["rec"].strategy_id,
        "same_ticker": same_ticker,
        "level_corr": round(level, 4),
        "return_corr": round(retc, 4),
        "threshold": threshold,
        "similar": similar,
    }


def build_clusters(items: list[dict[str, Any]], level_corr: float, return_corr: float, same_ticker_level_corr: float) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not items:
        return [], []

    uf = UnionFind(len(items))
    comparisons: list[dict[str, Any]] = []
    for i in range(len(items)):
        for j in range(i):
            comparison = pair_similarity(items[i], items[j], same_ticker_level_corr, level_corr, return_corr)
            comparisons.append(comparison)
            if comparison["similar"]:
                uf.union(i, j)

    cluster_map: dict[int, list[int]] = {}
    for idx in range(len(items)):
        cluster_map.setdefault(uf.find(idx), []).append(idx)

    clusters: list[dict[str, Any]] = []
    for root, indices in cluster_map.items():
        ordered = sorted(indices, key=lambda idx: (-items[idx]["score"], items[idx]["rec"].strategy_id))
        best_idx = ordered[0]
        clusters.append(
            {
                "root": root,
                "members": ordered,
                "best_idx": best_idx,
                "best_score": items[best_idx]["score"],
                "leader_id": items[best_idx]["rec"].strategy_id,
            }
        )

    clusters.sort(key=lambda cluster: (-cluster["best_score"], cluster["leader_id"], cluster["root"]))
    for cluster_order, cluster in enumerate(clusters, 1):
        cluster_id = f"cluster-{cluster_order:03d}"
        cluster["cluster_id"] = cluster_id
        cluster["cluster_order"] = cluster_order
        for idx in cluster["members"]:
            items[idx]["cluster_id"] = cluster_id
            items[idx]["cluster_order"] = cluster_order
    return clusters, comparisons


def choose_representatives(
    items: list[dict[str, Any]],
    clusters: list[dict[str, Any]],
    max_per_ticker: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[int, dict[str, Any]]]:
    kept: list[dict[str, Any]] = []
    rotated: list[dict[str, Any]] = []
    decisions: dict[int, dict[str, Any]] = {}
    ticker_counts: Counter[str] = Counter()

    for cluster in clusters:
        selected_idx: int | None = None
        for idx in cluster["members"]:
            ticker = items[idx]["rec"].ticker
            if ticker_counts[ticker] < max_per_ticker:
                selected_idx = idx
                break

        cluster_id = cluster["cluster_id"]
        if selected_idx is None:
            for idx in cluster["members"]:
                item = items[idx]
                reason = f"ticker concentration cap: {item['rec'].ticker}>{max_per_ticker} (cluster {cluster_id})"
                decisions[idx] = {
                    "kept": False,
                    "reason": reason,
                    "duplicate_of": "",
                    "decision": "ticker_cap",
                }
                rotated.append(item)
            continue

        chosen = items[selected_idx]
        ticker_counts[chosen["rec"].ticker] += 1
        kept.append(chosen)
        decisions[selected_idx] = {
            "kept": True,
            "reason": f"cluster representative: {cluster_id}",
            "duplicate_of": "",
            "decision": "kept",
        }
        cluster["selected_idx"] = selected_idx
        cluster["selected_strategy_id"] = chosen["rec"].strategy_id
        cluster["selected_ticker"] = chosen["rec"].ticker
        cluster["selected_score"] = chosen["score"]

        for idx in cluster["members"]:
            if idx == selected_idx:
                continue
            item = items[idx]
            reason = f"cluster duplicate: representative {chosen['rec'].strategy_id} (cluster {cluster_id})"
            decisions[idx] = {
                "kept": False,
                "reason": reason,
                "duplicate_of": chosen["rec"].strategy_id,
                "decision": "cluster_duplicate",
            }
            rotated.append(item)

    return kept, rotated, decisions


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--initial-cash", type=float, default=21281.0)
    ap.add_argument("--level-corr", type=float, default=0.88)
    ap.add_argument("--return-corr", type=float, default=0.25)
    ap.add_argument("--same-ticker-level-corr", type=float, default=0.84)
    ap.add_argument("--min-active", type=int, default=3)
    ap.add_argument("--max-per-ticker", type=int, default=3)
    args = ap.parse_args()

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    reg = StrategyRegistry.load()
    active = [r for r in reg.records() if r.status in ACTIVE and (r.metrics or {}).get("equity_shape_passed") is not False]
    items: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for rec in active:
        try:
            curve, bt_metrics = backtest_curve(rec, args.initial_cash)
        except Exception as exc:  # noqa: BLE001 - report local fixture/backtest failures as skipped records
            skipped.append({"strategy_id": rec.strategy_id, "reason": str(exc)[:160]})
            continue
        norm = normalize_curve(resample(curve, 240))
        ret = normalize_curve(resample(returns(curve), 240))
        items.append(
            {
                "rec": rec,
                "curve": curve,
                "norm": norm,
                "ret": ret,
                "score": quality_score(rec, bt_metrics),
                "bt_pnl": safe_float(bt_metrics.get("total_pnl")),
                "bt_pf": safe_float(bt_metrics.get("profit_factor")),
                "item_idx": len(items),
            }
        )

    clusters, comparisons = build_clusters(items, args.level_corr, args.return_corr, args.same_ticker_level_corr)
    keep, rotated, decisions = choose_representatives(items, clusters, args.max_per_ticker)

    for idx, item in enumerate(items):
        decision = decisions.get(idx)
        if decision is None:
            continue
        rec = item["rec"]
        rec.metrics = dict(rec.metrics or {})
        rec.metrics.update(
            {
                "equity_diversity_passed": bool(decision["kept"]),
                "equity_diversity_score": round(item["score"], 4),
                "equity_diversity_reason": decision["reason"],
                "equity_diversity_cluster_id": item.get("cluster_id", ""),
                "equity_diversity_cluster_order": item.get("cluster_order", 0),
                "equity_diversity_duplicate_of": decision["duplicate_of"],
            }
        )

    now_payload = {"live_orders": 0, "module": "equity_diversity_filter"}
    for item in keep:
        rec = item["rec"]
        cluster_id = item.get("cluster_id", "")
        rec.add_event(
            rec.status,
            reason="equity_diversity_keep",
            note=f"equity curve unique enough in {cluster_id}" if cluster_id else "equity curve unique enough",
            payload={**now_payload, "cluster_id": cluster_id},
        )
        reg._store_record(rec)
    for item in rotated:
        rec = item["rec"]
        idx = item["item_idx"]
        decision = decisions.get(idx, {"reason": "equity diversity filtered", "duplicate_of": ""})
        cluster_id = item.get("cluster_id", "")
        rec.add_event(
            STATUS_ROTATED_OUT,
            reason="governance_equity_similarity",
            note=decision["reason"],
            payload={**now_payload, "cluster_id": cluster_id, "duplicate_of": decision["duplicate_of"]},
        )
        reg._store_record(rec)

    reg.save()
    reg.export_legacy_state_files()

    cluster_reports = []
    kept_ids = {item["rec"].strategy_id for item in keep}
    for cluster in clusters:
        member_rows = []
        for idx in cluster["members"]:
            item = items[idx]
            decision = decisions.get(idx, {})
            member_rows.append(
                {
                    "strategy_id": item["rec"].strategy_id,
                    "ticker": item["rec"].ticker,
                    "timeframe": timeframe(item["rec"]),
                    "score": round(item["score"], 4),
                    "cluster_id": cluster["cluster_id"],
                    "decision": "kept" if item["rec"].strategy_id in kept_ids else "rotated",
                    "reason": decision.get("reason", ""),
                    "duplicate_of": decision.get("duplicate_of", ""),
                }
            )
        cluster_reports.append(
            {
                "cluster_id": cluster["cluster_id"],
                "size": len(cluster["members"]),
                "leader_id": cluster["leader_id"],
                "selected_strategy_id": cluster.get("selected_strategy_id", ""),
                "selected_ticker": cluster.get("selected_ticker", ""),
                "selected_score": round(cluster.get("selected_score", 0.0), 4) if cluster.get("selected_score") is not None else 0.0,
                "members": member_rows,
            }
        )

    rotated_cluster_dupes = sum(1 for idx, decision in decisions.items() if not decision["kept"] and decision["decision"] == "cluster_duplicate")
    rotated_ticker_caps = sum(1 for idx, decision in decisions.items() if not decision["kept"] and decision["decision"] == "ticker_cap")
    kept_rows = [row_dict(item, decisions.get(item["item_idx"], {})) for item in keep]
    rotated_rows = [rotated_dict(item, decisions.get(item["item_idx"], {})) for item in rotated]
    family_summary = build_family_summary(items, keep, rotated, decisions)
    payload = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "active_before": len(active),
        "evaluated": len(items),
        "clusters": len(clusters),
        "kept": len(keep),
        "rotated_total": len(rotated),
        "rotated_similar": rotated_cluster_dupes,
        "rotated_ticker_cap": rotated_ticker_caps,
        "skipped": skipped,
        "thresholds": {
            "level_corr": args.level_corr,
            "same_ticker_level_corr": args.same_ticker_level_corr,
            "return_corr": args.return_corr,
            "min_active": args.min_active,
            "max_per_ticker": args.max_per_ticker,
        },
        "clusters_report": cluster_reports,
        "family_summary": family_summary,
        "winners": [row for row in family_summary if row["family_role"] == "winner"],
        "sideways_families": [row for row in family_summary if row["family_role"] == "sideways"],
        "clone_families": [row for row in family_summary if row["family_role"] == "clone"],
        "kept_rows": kept_rows,
        "rotated_rows": rotated_rows,
        "comparisons": comparisons,
        "live_orders": 0,
    }
    (REPORT_DIR / "equity_diversity_filter_latest.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (REPORT_DIR / "equity_diversity_filter_latest.md").write_text(render_md(payload), encoding="utf-8")
    print(json.dumps({"active_before": len(active), "kept": len(keep), "rotated_total": len(rotated), "clusters": len(clusters), "live_orders": 0}, ensure_ascii=False))
    return 0


def row_dict(item: dict[str, Any], decision: dict[str, Any] | None = None) -> dict[str, Any]:
    rec = item["rec"]
    decision = decision or {}
    return {
        "strategy_id": rec.strategy_id,
        "ticker": rec.ticker,
        "timeframe": timeframe(rec),
        "strategy": rec.strategy,
        "cluster_id": item.get("cluster_id", ""),
        "cluster_order": item.get("cluster_order", 0),
        "score": round(item["score"], 4),
        "pnl": round(item["bt_pnl"], 2),
        "pf": round(item["bt_pf"], 4),
        "decision": decision.get("decision", "kept"),
        "reason": decision.get("reason", ""),
        "duplicate_of": decision.get("duplicate_of", ""),
    }


def rotated_dict(item: dict[str, Any], decision: dict[str, Any] | None = None) -> dict[str, Any]:
    out = row_dict(item, decision)
    return out


def render_md(payload: dict[str, Any]) -> str:
    lines = [
        "# Equity Diversity Filter — latest",
        "",
        f"- active_before: {payload['active_before']}",
        f"- evaluated: {payload['evaluated']}",
        f"- clusters: {payload.get('clusters', 0)}",
        f"- kept: {payload['kept']}",
        f"- rotated_total: {payload.get('rotated_total', payload['rotated_similar'])}",
        f"- rotated_similar: {payload['rotated_similar']}",
        f"- rotated_ticker_cap: {payload.get('rotated_ticker_cap', 0)}",
        f"- live_orders: {payload['live_orders']}",
        "",
        "## Cluster summary",
        "| cluster_id | size | selected | ticker | score |",
        "|---|---:|---|---|---:|",
    ]
    for cluster in payload.get("clusters_report", []):
        selected = cluster.get("selected_strategy_id") or "-"
        ticker = cluster.get("selected_ticker") or "-"
        score = cluster.get("selected_score", 0.0)
        lines.append(f"| {cluster['cluster_id']} | {cluster['size']} | {selected} | {ticker} | {score:.2f} |")
    lines.extend(["", "## Family summary for policy", "", "| family | samples | kept | rotated | winner_boost | sideways_penalty | clone_penalty | policy_bias | role | reason |", "|---|---:|---:|---:|---:|---:|---:|---:|---|---|"])
    for row in payload.get("family_summary", []):
        lines.append(
            f"| {row['strategy']} | {row['samples']} | {row['kept']} | {row['rotated']} | {row['winner_boost']:.2f} | {row['sideways_penalty']:.2f} | {row['clone_penalty']:.2f} | {row['policy_bias']:.2f} | {row['family_role']} | {row['policy_reason']} |"
        )
    lines.extend(["", "## Cluster members", ""])
    for cluster in payload.get("clusters_report", []):
        lines.extend(
            [
                f"### {cluster['cluster_id']}",
                "| strategy_id | ticker | tf | score | decision | reason |",
                "|---|---|---|---:|---|---|",
            ]
        )
        for member in cluster.get("members", []):
            lines.append(
                f"| {member['strategy_id']} | {member['ticker']} | {member['timeframe']} | {member['score']:.2f} | {member['decision']} | {member['reason']} |"
            )
        lines.append("")
    lines.extend(
        [
            "## Kept unique equity curves",
            "| # | cluster | ticker | tf | strategy | score | pnl | pf | reason |",
            "|---:|---|---|---|---|---:|---:|---:|---|",
        ]
    )
    for i, r in enumerate(payload["kept_rows"], 1):
        lines.append(
            f"| {i} | {r['cluster_id']} | {r['ticker']} | {r['timeframe']} | {r['strategy']} | {r['score']:.2f} | {r['pnl']:.2f} | {r['pf']:.2f} | {r['reason']} |"
        )
    lines.extend(
        [
            "",
            "## Rotated equity curves",
            "| # | cluster | ticker | tf | strategy | duplicate_of | reason |",
            "|---:|---|---|---|---|---|---|",
        ]
    )
    for i, r in enumerate(payload["rotated_rows"], 1):
        lines.append(
            f"| {i} | {r['cluster_id']} | {r['ticker']} | {r['timeframe']} | {r['strategy']} | {r['duplicate_of']} | {r['reason']} |"
        )
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
