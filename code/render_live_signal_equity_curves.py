#!/usr/bin/env python3
from __future__ import annotations
import json, pathlib, sys
ROOT=pathlib.Path('/root/prop-desk/strategy_combine')
FUT=pathlib.Path('/root/prop-desk/futures_lab')
DATA=FUT/'artifacts'/'tinkoff_futures_data'
REPORT=ROOT/'reports'/'strategy_architect'
REPORT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0,str(FUT))
import pandas as pd
from futures_lab import run_backtest, _synthetic_spec_for_file  # type: ignore

pool=json.loads((ROOT/'state'/'signal_pool.json').read_text()).get('strategies',{})
pickup=json.loads((REPORT/'supervisor_pickup_dryrun_latest.json').read_text())
portfolio=json.loads((ROOT/'state'/'portfolio.json').read_text())
config=json.loads((ROOT/'config.json').read_text())

decisions={r.get('strategy_id'):r.get('decision') for r in (pickup.get('would_promote') or [])+(pickup.get('vetoed_top') or [])}
rows=[]
initial=float(config.get('deposit_rub') or 100000)
for sid, rec in pool.items():
    m=rec.get('metrics') or {}
    tf=str(m.get('timeframe') or ('15m' if '15m' in sid else '1h'))
    ticker=rec.get('ticker'); strategy=rec.get('strategy')
    path=DATA/f'{ticker}_60d_{tf}_continuous.csv'
    if not path.exists():
        continue
    df=pd.read_csv(path)
    metrics,trades,eq=run_backtest(df, _synthetic_spec_for_file(ticker), strategy, rec.get('params') or {}, initial_cash=initial, contracts=1, commission_per_contract=0.0, slippage_bps=0.0, stop_atr=2.0, take_atr=3.0, max_hold_bars=192 if tf=='15m' else 48, risk_rub=0.0, max_contracts=1)
    rows.append({
        'sid':sid,'ticker':ticker,'strategy':strategy,'tf':tf,'rank':float(rec.get('rank_score') or 0),
        'decision':decisions.get(sid,'eligible/wait signal'),'eq':eq-initial,'metrics':metrics,
        'shape_score':float(m.get('equity_shape_score') or 0),'r2':float(m.get('equity_shape_r2') or 0),
        'pos':float(m.get('equity_shape_positive_window_ratio') or 0),'flat':float(m.get('equity_shape_flat_window_ratio') or 0)
    })
rows.sort(key=lambda r:r['rank'], reverse=True)

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
plt.style.use('dark_background')
fig, ax = plt.subplots(figsize=(14,8), dpi=170)
colors=['#00e5ff','#76ff03','#ffca28','#ff4081','#b388ff','#ff6e40','#40c4ff','#ffab91']
for i,r in enumerate(rows):
    y=r['eq'].reset_index(drop=True)
    step=max(1,len(y)//600)
    status='ACTIVE' if 'already active' in r['decision'] else ('RISK' if 'delta' in r['decision'] else 'WAIT')
    ax.plot(range(0,len(y),step), y.iloc[::step], linewidth=2.2, color=colors[i%len(colors)], label=f"{r['ticker']} {r['strategy']} {r['tf']}: {float(r['metrics'].get('total_pnl') or 0):+.0f}₽ | {status}")
ax.axhline(0,color='#888',linewidth=.9)
ax.set_title('Equity live signal-list — все 8 стратегий в текущем пуле',fontsize=16,weight='bold')
ax.set_xlabel('бары всей доступной истории 60d')
ax.set_ylabel('PnL к депозиту, ₽')
ax.grid(alpha=.25)
ax.legend(loc='best',fontsize=8)
open_slots=[]
for sid,s in portfolio.get('slots',{}).items():
    pos=s.get('open_position')
    if pos:
        open_slots.append(f"{s.get('ticker')} {s.get('strategy')} {pos.get('direction')}x{pos.get('qty')}")
footer=f"pool={len(rows)} | slots={pickup.get('open_slots')}/{pickup.get('max_slots')} free={pickup.get('free_slots')} | would_promote={len(pickup.get('would_promote') or [])} | live_orders={pickup.get('live_orders')} | open: {', '.join(open_slots)}"
fig.text(.01,.01,footer,fontsize=9,color='#ddd')
out=REPORT/'live_signal_equity_curves_all8.png'
fig.savefig(out,bbox_inches='tight',facecolor=fig.get_facecolor())
md=['# Live signal equity curves — all current pool','',f'- pool: {len(rows)}',f'- slots: {pickup.get("open_slots")}/{pickup.get("max_slots")} free={pickup.get("free_slots")}',f'- would_promote: {len(pickup.get("would_promote") or [])}',f'- live_orders: {pickup.get("live_orders")}','','| # | ticker | tf | strategy | decision | rank | pnl | R² | pos | flat |','|---:|---|---|---|---|---:|---:|---:|---:|---:|']
for i,r in enumerate(rows,1):
    md.append(f"| {i} | {r['ticker']} | {r['tf']} | {r['strategy']} | {r['decision']} | {r['rank']:.0f} | {float(r['metrics'].get('total_pnl') or 0):+.0f} | {r['r2']:.2f} | {r['pos']:.2f} | {r['flat']:.2f} |")
md.append('')
md.append(f'PNG: {out}')
(REPORT/'live_signal_equity_curves_all8.md').write_text('\n'.join(md),encoding='utf-8')
print(out)
print('\n'.join(md))
