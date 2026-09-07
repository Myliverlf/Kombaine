#!/usr/bin/env python3
from __future__ import annotations
import json, pathlib

ROOT=pathlib.Path('/root/prop-desk/strategy_combine')
REPORT=ROOT/'reports'/'strategy_architect'
REPORT.mkdir(parents=True, exist_ok=True)
pickup=json.loads((REPORT/'supervisor_pickup_dryrun_latest.json').read_text())
pool=json.loads((ROOT/'state'/'signal_pool.json').read_text()).get('strategies',{})
portfolio=json.loads((ROOT/'state'/'portfolio.json').read_text())

rows=[]
# Map pickup decisions
pickup_decisions={r['strategy_id']:r for r in (pickup.get('would_promote') or [])+(pickup.get('vetoed_top') or []) if r.get('strategy_id')}
for sid, rec in pool.items():
    m=rec.get('metrics') or {}
    dec=pickup_decisions.get(sid, {})
    decision=dec.get('decision') or 'eligible/waiting signal'
    status='PUSH' if sid in {r.get('strategy_id') for r in pickup.get('would_promote') or []} else ('BLOCKED' if sid in pickup_decisions else 'WAIT')
    rows.append({
        'id':sid,
        'ticker':rec.get('ticker'),
        'strategy':rec.get('strategy'),
        'rank':float(rec.get('rank_score') or 0),
        'status':status,
        'decision':decision,
        'pnl':float(m.get('equity_shape_total_pnl') or m.get('total_pnl') or 0),
        'shape':float(m.get('equity_shape_score') or 0),
        'r2':float(m.get('equity_shape_r2') or 0),
        'pos':float(m.get('equity_shape_positive_window_ratio') or 0),
        'flat':float(m.get('equity_shape_flat_window_ratio') or 0),
        'pf':float(m.get('profit_factor') or 0),
        'sharpe':float(m.get('sharpe') or 0),
    })
rows.sort(key=lambda r:r['rank'], reverse=True)
open_slots=[]
for sid,s in portfolio.get('slots',{}).items():
    pos=s.get('open_position')
    if pos:
        open_slots.append(f"{s.get('ticker')} {s.get('strategy')} {pos.get('direction')}x{pos.get('qty')} PnL {float(s.get('pnl_rub') or 0):+.0f}₽")

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
plt.style.use('dark_background')
fig=plt.figure(figsize=(15,9), dpi=170)
gs=fig.add_gridspec(2,1,height_ratios=[1.25,1],hspace=.34)
ax=fig.add_subplot(gs[0])
colors=[]
for r in rows:
    if r['status']=='PUSH': colors.append('#00e676')
    elif 'already active' in r['decision']: colors.append('#42a5f5')
    elif 'delta' in r['decision']: colors.append('#ffca28')
    else: colors.append('#b388ff')
labels=[f"{r['ticker']}\n{r['strategy']}" for r in rows]
vals=[r['rank'] for r in rows]
bars=ax.bar(range(len(rows)), vals, color=colors, alpha=.9)
ax.set_title('Live signal-list: что сейчас в пуле и что может пушиться', fontsize=16, weight='bold')
ax.set_ylabel('live rank')
ax.set_xticks(range(len(rows)))
ax.set_xticklabels(labels, fontsize=8)
ax.grid(axis='y', alpha=.25)
for b,r in zip(bars,rows):
    txt = 'PUSH' if r['status']=='PUSH' else ('ACTIVE' if 'already active' in r['decision'] else ('RISK' if 'delta' in r['decision'] else 'WAIT'))
    ax.text(b.get_x()+b.get_width()/2, b.get_height(), f"{txt}\n{r['rank']:.0f}", ha='center', va='bottom', fontsize=8)

ax2=fig.add_subplot(gs[1])
x=range(len(rows))
ax2.plot(x, [r['r2'] for r in rows], marker='o', label='R² equity trend', color='#00e5ff', linewidth=2)
ax2.plot(x, [r['pos'] for r in rows], marker='o', label='positive windows', color='#76ff03', linewidth=2)
ax2.plot(x, [r['flat'] for r in rows], marker='o', label='flat windows', color='#ff4081', linewidth=2)
ax2.set_ylim(-0.05,1.05)
ax2.set_ylabel('shape quality')
ax2.set_xticks(range(len(rows)))
ax2.set_xticklabels(labels, fontsize=8)
ax2.grid(alpha=.25)
ax2.legend(loc='lower left', fontsize=9)
for i,r in enumerate(rows):
    ax2.text(i, .04, f"PnL {r['pnl']:+.0f}₽\nPF {r['pf']:.2f}", ha='center', va='bottom', fontsize=7, color='#ddd')
footer=f"mode={pickup.get('mode')} | slots={pickup.get('open_slots')}/{pickup.get('max_slots')} free={pickup.get('free_slots')} | fresh={pickup.get('fresh_candidates')} | live_orders={pickup.get('live_orders')} | open: {'; '.join(open_slots)}"
fig.text(.01,.01,footer,fontsize=9,color='#ddd')
out=REPORT/'live_signal_list_now.png'
fig.savefig(out,bbox_inches='tight',facecolor=fig.get_facecolor())
md=['# Live signal-list now','',f"- verdict: {pickup.get('verdict')}",f"- slots: {pickup.get('open_slots')}/{pickup.get('max_slots')} free={pickup.get('free_slots')}",f"- fresh_candidates: {pickup.get('fresh_candidates')}",f"- would_promote: {len(pickup.get('would_promote') or [])}",f"- live_orders: {pickup.get('live_orders')}",'','| # | ticker | strategy | rank | status | decision | pnl | R² | pos | flat |','|---:|---|---|---:|---|---|---:|---:|---:|---:|']
for i,r in enumerate(rows,1):
    md.append(f"| {i} | {r['ticker']} | {r['strategy']} | {r['rank']:.0f} | {r['status']} | {r['decision']} | {r['pnl']:+.0f} | {r['r2']:.2f} | {r['pos']:.2f} | {r['flat']:.2f} |")
md.append('')
md.append(f'PNG: {out}')
(REPORT/'live_signal_list_now.md').write_text('\n'.join(md),encoding='utf-8')
print(out)
print('\n'.join(md))
