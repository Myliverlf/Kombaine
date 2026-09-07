#!/usr/bin/env python3
from __future__ import annotations
import json, pathlib, sys
from typing import Any

ROOT = pathlib.Path('/root/prop-desk/strategy_combine')
FUT = pathlib.Path('/root/prop-desk/futures_lab')
DATA = FUT / 'artifacts' / 'tinkoff_futures_data'
REPORT = ROOT / 'reports' / 'strategy_architect'
REPORT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(FUT))

import pandas as pd
from futures_lab import FuturesSpec, run_backtest  # type: ignore

pickup = json.loads((REPORT/'supervisor_pickup_dryrun_latest.json').read_text())
registry = json.loads((ROOT/'state'/'strategy_registry.json').read_text())
portfolio = json.loads((ROOT/'state'/'portfolio.json').read_text())
config = json.loads((ROOT/'config.json').read_text())

# "Будем запускать в реальном режиме" = очередь live-supervisor:
# 1) would_promote if risk currently allows;
# 2) otherwise top vetoed/skipped live-ready candidates, because they are next once risk frees/дельта нормализуется.
queue = []
for row in pickup.get('would_promote') or []:
    row = dict(row); row['launch_decision'] = row.get('decision','APPROVED'); queue.append(row)
for row in pickup.get('vetoed_top') or []:
    if len(queue) >= 6: break
    row = dict(row); row['launch_decision'] = row.get('decision','blocked'); queue.append(row)
# Dedup by strategy_id
seen=set(); live_ready=[]
for row in queue:
    sid=row.get('strategy_id')
    if sid and sid not in seen and sid in registry.get('strategies',{}):
        seen.add(sid); live_ready.append(row)
    if len(live_ready) >= 6: break

# active selected fallback
if len(live_ready) < 6:
    active=[]
    for sid,rec in registry.get('strategies',{}).items():
        if rec.get('status') in {'active_watchlist','active_signal_pool'}:
            active.append((float(rec.get('active_rank') or 0), {'strategy_id': sid, 'ticker': rec.get('ticker'), 'strategy': rec.get('strategy'), 'launch_decision':'active_pool'}))
    active.sort(key=lambda x: x[0], reverse=True)
    for _, row in active:
        if row['strategy_id'] not in seen:
            seen.add(row['strategy_id']); live_ready.append(row)
        if len(live_ready) >= 6: break

SPEC_DEFAULTS = {
    # point_value = min_price_increment_amount / min_price_increment
    # active_margin = max(initial_margin_on_buy, initial_margin_on_sell)
    'LKOH': dict(point_value=1.0, active_margin=7412.68),
    'GAZP': dict(point_value=100.0, active_margin=1432.47),
    'SBER': dict(point_value=1.0, active_margin=1500.0),
    'Si': dict(point_value=1.0, active_margin=1500.0),
    'IMOEX': dict(point_value=1.0, active_margin=1500.0),
    'BR': dict(point_value=1.0, active_margin=1500.0),
}

def make_spec(ticker:str) -> FuturesSpec:
    d = SPEC_DEFAULTS.get(ticker, dict(point_value=1.0, active_margin=1500.0))
    point = float(d['point_value'])
    margin = float(d['active_margin'])
    return FuturesSpec(
        ticker=ticker,
        uid='',
        name=ticker,
        class_code='SPBFUT',
        lot=1,
        min_price_increment=1.0,
        min_price_increment_amount=point,
        initial_margin_on_buy=margin,
        initial_margin_on_sell=margin,
    )

def data_file(ticker:str, tf:str) -> pathlib.Path:
    return DATA / f'{ticker}_60d_{tf}_continuous.csv'

curves=[]; table=[]
initial_cash=float(config.get('deposit_rub') or 100000)
for row in live_ready:
    sid=row['strategy_id']; rec=registry['strategies'][sid]
    ticker=rec['ticker']; strategy=rec['strategy']
    m=rec.get('metrics') or {}; ctx=rec.get('portfolio_context') or {}
    tf=str(ctx.get('timeframe') or m.get('timeframe') or ('15m' if '15m' in sid else '1h'))
    path=data_file(ticker, tf)
    if not path.exists():
        table.append({**row, 'error': f'no data {path.name}'})
        continue
    df=pd.read_csv(path)
    try:
        metrics,trades,eq=run_backtest(
            df, make_spec(ticker), strategy, rec.get('params') or {},
            initial_cash=initial_cash,
            contracts=1,
            commission_per_contract=0.0,
            slippage_bps=0.0,
            stop_atr=2.0,
            take_atr=3.0,
            max_hold_bars=192 if tf=='15m' else 48,
            risk_rub=0.0,
            max_contracts=1,
        )
        pnl=float(metrics.get('total_pnl') or 0)
        curves.append((sid, ticker, tf, strategy, row.get('launch_decision',''), eq - initial_cash, metrics))
        table.append({
            'id':sid,'ticker':ticker,'tf':tf,'strategy':strategy,
            'decision':row.get('launch_decision',''),
            'trades':int(metrics.get('trade_count') or 0),
            'pnl':pnl,'pf':float(metrics.get('profit_factor') or 0),
            'dd':float(metrics.get('max_drawdown') or 0),
            'sharpe':float(metrics.get('sharpe') or 0),
        })
    except Exception as e:
        table.append({'id':sid,'ticker':ticker,'tf':tf,'strategy':strategy,'decision':row.get('launch_decision',''), 'error':str(e)[:160]})

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
plt.style.use('dark_background')
fig, ax = plt.subplots(figsize=(14,8), dpi=170)
colors=['#00e5ff','#76ff03','#ffca28','#ff4081','#b388ff','#ff6e40']
for i,(sid,ticker,tf,strategy,decision,eq,metrics) in enumerate(curves):
    # reduce points for readable plot
    y=eq.reset_index(drop=True)
    step=max(1, len(y)//500)
    ax.plot(range(0,len(y),step), y.iloc[::step], linewidth=2.2, color=colors[i%len(colors)],
            label=f"{ticker} {strategy} {tf}: {float(metrics.get('total_pnl') or 0):+.0f}₽ | {decision}")
ax.axhline(0, color='#888', linewidth=.9)
ax.set_title('Equity стратегий из live-ready очереди комбайна', fontsize=16, weight='bold')
ax.set_xlabel('бары истории 60d')
ax.set_ylabel('PnL к депозиту, ₽')
ax.grid(alpha=.25)
ax.legend(loc='best', fontsize=8)
# annotate current blockers
open_slots=[]
for sid,s in portfolio.get('slots',{}).items():
    pos=s.get('open_position')
    if pos:
        open_slots.append(f"{s.get('ticker')} {s.get('strategy')} {pos.get('direction')}x{pos.get('qty')}")
footer=f"mode={config.get('mode')} | free_slots={pickup.get('free_slots')} | сейчас открыто: {', '.join(open_slots)} | график = backtest equity тех стратегий, которые live-supervisor рассматривает"
fig.text(0.01,0.01,footer,fontsize=9,color='#ddd')
out=REPORT/'live_ready_strategy_equity_all_available.png'
fig.savefig(out, bbox_inches='tight', facecolor=fig.get_facecolor())

md=['# Live-ready strategy equity — all available data', '', 'Период: вся доступная локальная история из CSV `*_60d_*_continuous.csv` (на диске больше истории для этих инструментов нет).', '', '## Очередь live-supervisor / кандидаты запуска', '| # | ticker | tf | strategy | decision now | trades | equity/PnL | PF | DD | Sharpe |', '|---:|---|---|---|---|---:|---:|---:|---:|---:|']
for i,r in enumerate(table,1):
    if 'error' in r:
        md.append(f"| {i} | {r.get('ticker','?')} | {r.get('tf','?')} | {r.get('strategy','?')} | {r.get('decision','')} | - | ERROR {r['error']} | - | - | - |")
    else:
        md.append(f"| {i} | {r['ticker']} | {r['tf']} | {r['strategy']} | {r['decision']} | {r['trades']} | {r['pnl']:+.2f} | {r['pf']:.2f} | {r['dd']:.2f} | {r['sharpe']:.2f} |")
md += ['', f'PNG: {out}']
(REPORT/'live_ready_strategy_equity.md').write_text('\n'.join(md), encoding='utf-8')
print(out)
print('\n'.join(md))
