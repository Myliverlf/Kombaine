#!/usr/bin/env python3
"""ITERATION 23I.4 bridge: MOEX ISS historical contracts -> continuous series.

Semantics (identical to 23I.3 proof, documented in the GPT dialogue):
  * roll_days = 5 (last 5 sessions of the front contract)
  * multiplicative back-adjustment of the back segment against the front at splice
  * front selection: volume-weighted by daily volume over the contract's listed life
  * output scale: T-Invest futures data style = price in points-of-underlying
    (contract price / lot_size), so splices cleanly against 23I.3 canonical data.

Safety: read-only ISS fetch, no orders, no live, no provider changes.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
import urllib.request
import zipfile
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

LOT_FALLBACK = 100
ISS_BASE = "http://iss.moex.com"
UA = {"User-Agent": "Mozilla/5.0 (23i4-bridge)"}

COMBINE = Path("/root/prop-desk/strategy_combine")
LAB = Path("/root/prop-desk/futures_lab")
RAW_DIR = COMBINE / "artifacts" / "history_raw" / "moex_23i4"
OUT_DIR = LAB / "artifacts" / "tinkoff_futures_data"
STATE_DIR = COMBINE / "state" / "backfill_23i4"

for d in (RAW_DIR, OUT_DIR, STATE_DIR):
    d.mkdir(parents=True, exist_ok=True)


def http_get_json(url: str, tries: int = 4) -> dict:
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 + i * 2)
    raise RuntimeError(f"GET failed: {url}: {last}")


def get_contract_meta(secid: str) -> dict:
    url = f"{ISS_BASE}/iss/securities/{secid}.json?iss.meta=off"
    d = http_get_json(url)
    desc = d.get("description", {})
    cols = desc.get("columns", [])
    row = (desc.get("data") or [[]])[0]
    meta = dict(zip(cols, row))
    lot = int(meta.get("lotsize") or LOT_FALLBACK)
    market = d.get("securities", {})
    mcols = market.get("columns", [])
    mrow = (market.get("data") or [[]])[0]
    secmeta = dict(zip(mcols, mrow)) if mrow else {}
    return {
        "secid": secid,
        "lot_size": lot,
        "title": meta.get("title") or secmeta.get("name"),
        "reg_number": meta.get("regnumber"),
    }


def fetch_candles(secid: str, interval: int, start: str, end: str,
                  raw_cache: Path) -> list[dict]:
    """Paginated ISS candles fetch with on-disk cache."""
    cache = raw_cache / f"{secid}_{interval}.json"
    if cache.exists():
        return json.loads(cache.read_text())
    out: list[dict] = []
    cur_start = datetime.strptime(start, "%Y-%m-%d").date()
    end_d = datetime.strptime(end, "%Y-%m-%d").date()
    while cur_start <= end_d:
        cur_end = min(cur_start + timedelta(days=366), end_d)
        url = (
            f"{ISS_BASE}/iss/engines/futures/markets/forts/securities/{secid}/candles.json"
            f"?iss.meta=off&from={cur_start}&till={cur_end}&interval={interval}&limit=5000"
        )
        d = http_get_json(url)
        c = d.get("candles", {})
        cols = c.get("columns", [])
        rows = c.get("data", [])
        if not rows:
            break
        for r in rows:
            rec = dict(zip(cols, r))
            out.append(rec)
        cur_start = cur_end + timedelta(days=1)
    out.sort(key=lambda x: x["begin"])
    cache.write_text(json.dumps(out))
    return out


def candles_to_daily(rows: list[dict], lot_size: int) -> dict[str, dict]:
    """Group intraday candles -> UTC-date daily bars in T-Invest scale (pts)."""
    by_date: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        d = r["begin"][:10]
        by_date[d].append(r)
    daily = {}
    for d, bars in by_date.items():
        bars.sort(key=lambda x: x["begin"])
        daily[d] = {
            "time_utc": f"{d}T00:00:00Z",
            "open": bars[0]["open"] / lot_size,
            "close": bars[-1]["close"] / lot_size,
            "high": max(b["high"] for b in bars) / lot_size,
            "low": min(b["low"] for b in bars) / lot_size,
            "volume": sum(b["volume"] for b in bars),
        }
    return daily


def volume_weighted_daily(daily: dict[str, dict], window: list[str]) -> float:
    """Average close weighted by volume over given dates."""
    num = den = 0.0
    for d in window:
        bar = daily.get(d)
        if bar and bar["volume"] > 0:
            num += bar["close"] * bar["volume"]
            den += bar["volume"]
    return num / den if den > 0 else 0.0


def build_chain(symbol: str, contracts: list[str], start: str, end: str) -> dict:
    """Stitch contracts in chronological order with roll_days=5 multiplicative splice.

    Returns dict with 'series' (date -> bar dict with 'source'), 'splice_log'.
    """
    segments = []
    for secid in contracts:
        meta = get_contract_meta(secid)
        rows = fetch_candles(secid, 60, start, end, RAW_DIR)  # 60m interval
        if not rows:
            print(f"  {secid}: no candles, skip")
            continue
        daily = candles_to_daily(rows, meta["lot_size"])
        if not daily:
            continue
        dates = sorted(daily)
        seg = {
            "secid": secid,
            "lot": meta["lot_size"],
            "daily": daily,
            "dates": dates,
            "first": dates[0],
            "last": dates[-1],
        }
        segments.append(seg)
        print(f"  {secid}: lot={meta['lot_size']} bars={len(daily)} "
              f"{seg['first']}..{seg['last']}")

    if not segments:
        return {"series": {}, "splice_log": [], "segments": []}

    segments.sort(key=lambda s: s["first"])

    # Build continuous series: start with earliest segment, splice forward.
    series: dict[str, dict] = {}
    splice_log = []
    cur = segments[0]
    for d in cur["dates"]:
        series[d] = dict(cur["daily"][d], source=cur["secid"])
    adj = 1.0  # multiplicative factor applied to back data

    for nxt in segments[1:]:
        # Overlap window: last N days of cur that also exist in nxt
        overlap = [d for d in cur["dates"][-8:] if d in nxt["daily"]]
        if len(overlap) < 2:
            # disjoint segments (e.g. listing gap) - just append
            for d in nxt["dates"]:
                if d not in series:
                    series[d] = dict(nxt["daily"][d], source=nxt["secid"])
            splice_log.append({
                "type": "append_disjoint",
                "from": cur["secid"], "to": nxt["secid"],
                "overlap_days": len(overlap),
            })
            cur = nxt
            continue
        # roll_days = 5: use last min(5, len) overlap days as splice zone
        roll = overlap[-5:]
        back_price = volume_weighted_daily(cur["daily"], roll)
        front_price = volume_weighted_daily(nxt["daily"], roll)
        if back_price <= 0 or front_price <= 0:
            # no volume in roll zone -> use last close
            back_price = cur["daily"][cur["dates"][-1]]["close"]
            front_price = nxt["daily"][roll[-1]]["close"] if nxt["daily"].get(roll[-1]) else back_price
        ratio = front_price / back_price if back_price else 1.0
        adj *= ratio
        # re-adjust all existing series (back segment) by ratio
        for d in series:
            b = series[d]
            for k in ("open", "close", "high", "low"):
                b[k] *= ratio
        # front contract replaces back from splice date onward
        splice_date = roll[0]
        replaced = 0
        for d in nxt["dates"]:
            if d >= splice_date and d in nxt["daily"]:
                series[d] = dict(nxt["daily"][d], source=nxt["secid"])
                replaced += 1
        splice_log.append({
            "type": "splice",
            "from": cur["secid"], "to": nxt["secid"],
            "splice_date": splice_date,
            "roll_days_used": len(roll),
            "back_price": round(back_price, 6),
            "front_price": round(front_price, 6),
            "ratio": round(ratio, 8),
            "cumulative_adj": round(adj, 8),
            "front_days_taken": replaced,
        })
        cur = nxt

    return {"series": series, "splice_log": splice_log, "segments": segments}


def write_csv(symbol: str, result: dict, out_dir: Path) -> Path:
    path = out_dir / f"{symbol}_23i4_moex_continuous_daily.csv"
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["time_utc", "open", "close", "high", "low", "volume", "source"])
        for d in sorted(result["series"]):
            b = result["series"][d]
            w.writerow([b["time_utc"], f"{b['open']:.6f}", f"{b['close']:.6f}",
                        f"{b['high']:.6f}", f"{b['low']:.6f}", int(b["volume"]), b["source"]])
    return path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", required=True, help="GAZPF or SBERF")
    ap.add_argument("--contracts", required=True, help="comma-separated ISS secids in chronological order")
    ap.add_argument("--start", default="2023-09-01")
    ap.add_argument("--end", default="2026-09-01")
    args = ap.parse_args()

    contracts = [c.strip() for c in args.contracts.split(",") if c.strip()]
    print(f"23I.4 bridge: {args.symbol} contracts={len(contracts)} "
          f"range={args.start}..{args.end}")
    result = build_chain(args.symbol, contracts, args.start, args.end)
    if not result["series"]:
        print("ERROR: empty series")
        return 2
    path = write_csv(args.symbol, result, OUT_DIR)
    dates = sorted(result["series"])
    first, last = dates[0], dates[-1]
    coverage = (datetime.strptime(last, "%Y-%m-%d") - datetime.strptime(first, "%Y-%m-%d")).days
    (STATE_DIR / f"splice_log_{args.symbol}.json").write_text(
        json.dumps({"symbol": args.symbol, "splice_log": result["splice_log"],
                    "segments": [{"secid": s["secid"], "lot": s["lot"],
                                  "first": s["first"], "last": s["last"],
                                  "bars": len(s["daily"])} for s in result["segments"]],
                    "output_csv": str(path), "first": first, "last": last,
                    "coverage_days": coverage, "rows": len(dates)}, indent=1, ensure_ascii=False))
    print(f"OK {args.symbol}: rows={len(dates)} {first}..{last} coverage={coverage}d -> {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
