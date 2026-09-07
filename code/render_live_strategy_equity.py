#!/usr/bin/env python3
from __future__ import annotations
import sqlite3, json, pathlib
from collections import defaultdict

root=pathlib.Path('/root/prop-desk/strategy_combine')
reports=root/'reports'/'strategy_architect'
reports.mkdir(parents=True, exist_ok=True)
portfolio=json.loads((root/'state'/'portfolio.json').read_text())
registry=json.loads((root/'state'/'strategy_registry.json').read_text())
con=sqlite3.connect(root/'analytics.db')
con.row_factory=sqlite3.Row
rows=list(con.execute("""
select id, ts_open, ts_close, slot_id, ticker, strategy, direction, contracts, entry_price, exit_price, pnl_rub, exit_reason, regime, status
from trades
where status='closed' and pnl_rub is not null
order by coalesce(ts_close, ts_open), id
"""))
clean=[]
for r in rows:
    reason=(r['exit_reason'] or '').lower()
    if 'phantom' in reason or 'duplicate_cleanup' in reason:
        continue
    clean.append(r)
by=defaultdict(list)
for r in clean:
    by[f"{r['ticker']} {r['strategy']}"].append(r)
summary=[]
for key, rs in by.items():
    pnl=sum(float(r['pnl_rub'] or 0) for r in rs)
    wins=sum(1 for r in rs if (r['pnl_rub'] or 0)>0)
    losses=sum(1 for r in rs if (r['pnl_rub'] or 0)<0)
    summary.append((pnl, key, len(rs), wins, losses))
summary.sort(reverse=True)
active=[]
for sid, rec in registry.get('strategies',{}).items():
    if rec.get('status') in {'active_signal_pool','active_watchlist'}:
        m=rec.get('metrics') or {}
        active.append({
            'id': sid,
            'ticker': rec.get('ticker'),
            'strategy': rec.get('strategy'),
            'tf': (rec.get('portfolio_context') or {}).get('timeframe') or m.get('timeframe') or '',
            'rank': float(rec.get('active_rank') or 0),
            'pnl': float(m.get('total_pnl') or m.get('pnl') or 0),
            'pf': float(m.get('profit_factor') or m.get('pf') or 0),
            'sharpe': float(m.get('sharpe') or 0),
            'stable': m.get('stability_passed'),
        })
active.sort(key=lambda x:x['rank'], reverse=True)
active6=active[:6]

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
plt.style.use('dark_background')
fig=plt.figure(figsize=(13,8), dpi=160)
gs=fig.add_gridspec(2,1, height_ratios=[1.35,1], hspace=0.35)
ax=fig.add_subplot(gs[0])
colors=['#00e5ff','#76ff03','#ffca28','#ff4081','#b388ff','#ff6e40']
if summary:
    for idx,(pnl,key,n,w,l) in enumerate(summary[:6]):
        eq=[]; x=[]; cum=0.0
        for i,r in enumerate(by[key],1):
            cum += float(r['pnl_rub'] or 0)
            eq.append(cum); x.append(i)
        ax.plot(x, eq, marker='o', linewidth=2.4, label=f"{key}: {cum:+.0f}₽", color=colors[idx%len(colors)])
    ax.axhline(0,color='#777',linewidth=0.8)
    ax.set_title('Реальная live equity по закрытым сделкам', fontsize=15, weight='bold')
    ax.set_xlabel('номер закрытой сделки')
    ax.set_ylabel('PnL / equity, ₽')
    ax.grid(alpha=.25)
    ax.legend(loc='best', fontsize=9)
else:
    ax.text(.5,.5,'Нет закрытых live trades',ha='center',va='center')
ax2=fig.add_subplot(gs[1])
labels=[f"{a['ticker']}\n{a['strategy']}\n{a['tf']}" for a in active6]
vals=[a['pnl'] for a in active6]
bar_colors=['#00c853' if v>=0 else '#ff1744' for v in vals]
bars=ax2.bar(range(len(vals)), vals, color=bar_colors, alpha=.88)
ax2.axhline(0,color='#777',linewidth=.8)
ax2.set_title('Топ-6 активного пула по backtest PnL', fontsize=15, weight='bold')
ax2.set_ylabel('Backtest PnL, ₽')
ax2.set_xticks(range(len(labels)))
ax2.set_xticklabels(labels, fontsize=8)
ax2.grid(axis='y', alpha=.25)
for bar,v,a in zip(bars,vals,active6):
    ax2.text(bar.get_x()+bar.get_width()/2, v, f"{v:+.0f}₽\nPF {a['pf']:.2f}", ha='center', va='bottom' if v>=0 else 'top', fontsize=8)
open_slots=[]
for sid,s in portfolio.get('slots',{}).items():
    pos=s.get('open_position')
    if pos:
        open_slots.append(f"{s.get('ticker')} {s.get('strategy')} {pos.get('direction')} x{pos.get('qty')} | slotPnL {float(s.get('pnl_rub') or 0):+.0f}₽")
fig.text(0.01,0.01,'Сейчас открыто: '+(' ; '.join(open_slots) if open_slots else 'нет')+'   |   график: closed trades + active pool', fontsize=9, color='#ddd')
out=reports/'live_strategy_equity_top.png'
fig.savefig(out, bbox_inches='tight', facecolor=fig.get_facecolor())
md=[]
md.append('# Live strategy equity top')
md.append('')
md.append('## Сейчас открыто')
for s in open_slots: md.append(f'- {s}')
md.append('')
md.append('## Реальный closed-trades PnL')
md.append('| # | strategy | trades | W/L | PnL |')
md.append('|---:|---|---:|---:|---:|')
for i,(pnl,key,n,w,l) in enumerate(summary[:6],1):
    md.append(f'| {i} | {key} | {n} | {w}/{l} | {pnl:+.2f} |')
md.append('')
md.append('## Топ-6 активного пула')
md.append('| # | ticker | tf | strategy | rank | backtest PnL | PF | Sharpe | stable |')
md.append('|---:|---|---|---|---:|---:|---:|---:|---|')
for i,a in enumerate(active6,1):
    md.append(f"| {i} | {a['ticker']} | {a['tf']} | {a['strategy']} | {a['rank']:.2f} | {a['pnl']:+.2f} | {a['pf']:.2f} | {a['sharpe']:.2f} | {a['stable']} |")
(reports/'live_strategy_equity_top.md').write_text('\n'.join(md), encoding='utf-8')
print(out)
print('\n'.join(md))
