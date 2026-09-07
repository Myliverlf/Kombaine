"""Battle-ready validation on the longest available horizon per ticker.

Input: smooth_full_scan.json from the 60d discovery scan.
For each shortlisted candidate, run the same params on the longest available file
(1095d > 365d > 60d), compute net PnL + smoothness, and rank the survivors.

This is the step the user actually wants: not discovery, but long-horizon proof.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Iterable, Optional

sys.path.insert(0, '/root/prop-desk/strategy_combine')
sys.path.insert(0, '/root/prop-desk/strategy_combine/code')
sys.path.insert(0, '/root/prop-desk/futures_lab')

import futures_lab as fl
from smooth_equity_metrics import smooth_equity_metrics

ROOT = Path('/root/prop-desk/strategy_combine')
DATA = Path('/root/prop-desk/futures_lab/artifacts/tinkoff_futures_data')
SCAN = ROOT / 'reports/strategy_architect/smooth_full_scan.json'
OUT = ROOT / 'reports/strategy_architect/long_horizon_battle_scan.json'

CASH = 20_000.0
COMM = 1.5
SLIP = 2.0
KW = dict(
    initial_cash=CASH,
    contracts=1,
    commission_per_contract=COMM,
    slippage_bps=SLIP,
    stop_atr=2.0,
    take_atr=3.0,
    max_hold_bars=48,
    risk_rub=0.0,
    max_contracts=1,
    debug_only=True,
)

LONG_CANDIDATES = [1095, 365, 60]
MAX_CANDIDATES = 24
MIN_TRADES = 30


def longest_csv(ticker: str) -> tuple[Optional[Path], Optional[int]]:
    for days in LONG_CANDIDATES:
        p = DATA / f"{ticker}_{days}d_1h_continuous.csv"
        if p.exists():
            return p, days
    return None, None


def load_df(csv: Path, ticker: str):
    args = SimpleNamespace(
        ticker=ticker,
        timeframe='1h',
        file=str(csv),
        continuous=True,
        sandbox=True,
        days=60,
        start=None,
        end=None,
        interval='1h',
        roll_days=5,
        initial_cash=CASH,
        contracts=1,
        commission_per_contract=COMM,
        slippage_bps=SLIP,
        stop_atr=2.0,
        take_atr=3.0,
        max_hold_bars=48,
        risk_rub=0.0,
        max_contracts=1,
    )
    return fl.load_data(args)


def uniq_pairs(rows: Iterable[dict]) -> list[dict]:
    seen = set()
    out = []
    for r in rows:
        key = (r['ticker'], r['strategy'], json.dumps(r['params'], sort_keys=True, ensure_ascii=False))
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def main() -> int:
    scan = json.loads(SCAN.read_text())
    rows = uniq_pairs(scan['top'])
    rows.sort(key=lambda r: (-float(r.get('smooth_score', 0.0)), -float(r.get('net_pnl', 0.0))))
    rows = rows[:MAX_CANDIDATES]

    results = []
    for i, r in enumerate(rows, 1):
        csv, horizon_days = longest_csv(r['ticker'])
        if csv is None:
            results.append({**r, 'status': 'NO_DATA'})
            continue

        spec, df = load_df(csv, r['ticker'])
        try:
            m, trades, eq = fl.run_backtest(df, spec, r['strategy'], r['params'], **KW)
        except Exception as e:
            results.append({**r, 'status': f'ERROR:{type(e).__name__}', 'csv': str(csv), 'horizon_days': horizon_days})
            continue

        pnl = float(m.get('total_pnl') or 0.0)
        tr = int(m.get('trade_count') or len(trades) or 0)
        pf = float(m.get('profit_factor') or 0.0)
        sm = smooth_equity_metrics([float(x) for x in eq])
        results.append({
            'ticker': r['ticker'],
            'strategy': r['strategy'],
            'params': r['params'],
            'origin_60d_pnl': r.get('net_pnl'),
            'origin_60d_score': r.get('smooth_score'),
            'horizon_days': horizon_days,
            'csv': str(csv),
            'long_pnl': round(pnl, 2),
            'long_trades': tr,
            'long_pf': round(pf, 3),
            'long_smooth_score': sm.get('smooth_score', 0.0),
            'long_ulcer': sm.get('ulcer_index', 0.0),
            'long_dd': round(sm.get('max_drawdown', 0.0), 2),
            'long_r2': sm.get('equity_r2', 0.0),
            'long_clusters': sm.get('drawdown_cluster_count', 0),
            'long_tail_pnl': sm.get('tail_pnl', 0.0),
            'long_micro_tail_pnl': sm.get('micro_tail_pnl', 0.0),
        })
        print(f"[{i:02d}/{len(rows)}] {r['ticker']:5s} {r['strategy']:24s} {horizon_days:4d}d pnl={pnl:+8.0f} pf={pf:.2f} tr={tr:3d} ulcer={sm.get('ulcer_index',0):4.1f}")

    results.sort(key=lambda x: (-float(x.get('long_smooth_score', 0.0)), -float(x.get('long_pnl', 0.0))))
    survivors = [r for r in results if r.get('long_pnl', 0) > 0 and r.get('long_trades', 0) >= MIN_TRADES and r.get('long_pf', 0) >= 1.10]

    payload = {
        'generated_at': '2026-09-03',
        'cash': CASH,
        'commission': COMM,
        'slippage_bps': SLIP,
        'min_trades': MIN_TRADES,
        'shortlisted': len(rows),
        'results': results,
        'survivors': survivors,
        'note': 'Long horizon means longest available per ticker: 1095d > 365d > 60d. This is the battle-ready proof layer, not discovery.',
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2))

    print('\n=== SURVIVORS (battle-ready shortlist) ===')
    for r in survivors[:12]:
        print(f"  {r['ticker']:5s} {r['strategy']:24s} {r['horizon_days']:4d}d pnl={r['long_pnl']:+9,.0f} pf={r['long_pf']:.2f} tr={r['long_trades']:3d} score={r['long_smooth_score']:.0f}")
    print(f'written: {OUT}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
