#!/usr/bin/env python3
from __future__ import annotations
import json, pathlib, collections, sys
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
ROOT=pathlib.Path('/root/prop-desk/strategy_combine')
sys.path.insert(0,str(ROOT/'code'))
from portfolio_equity_analyzer import backtest_curve, align_curves, sum_curves, equity_profile
base=ROOT/'reports/strategy_architect'
cycles=sorted(base.glob('cycle_*.json'), key=lambda p:p.stat().st_mtime, reverse=True)[:30]
best={}
for p in cycles:
    d=json.loads(p.read_text())
    for key in ['top','tail_vetoed']:
        for r in d.get(key,[]):
            k=(r.get('ticker'),r.get('strategy'))
            pnl=float(r.get('total_pnl') or 0); pf=float(r.get('profit_factor') or 0); tr=int(r.get('trades') or 0)
            if tr<8 or pnl<=0 or pf<1.05: continue
            if k not in best or pnl>float(best[k].get('total_pnl') or 0):
                rr=dict(r); rr['_cycle']=p.name; rr['_set']=key; best[k]=rr
rows=sorted(best.values(), key=lambda r: float(r.get('total_pnl') or 0), reverse=True)
sel=[]; counts=collections.Counter()
for r in rows:
    if counts[r.get('ticker')]>=2: continue
    sel.append(r); counts[r.get('ticker')]+=1
    if len(sel)>=5: break
curves=[]
for r in sel:
    c=backtest_curve(r, initial_cash=report_capital())['pnl']
    curves.append(c)
aligned=align_curves(curves,n=500)
port=sum_curves(aligned)
prof=equity_profile(port)
stamp='50pct_candidates_20260824'
out=base/f'portfolio_{stamp}.png'
plt.figure(figsize=(14,7),facecolor='white')
plt.plot(port,color='#111111',linewidth=2.8,label='portfolio sum')
plt.axhline(0,color='#888',linewidth=.8,alpha=.35)
plt.grid(True,alpha=.18)
plt.title('50%+/month candidate portfolio — synthetic PnL')
plt.xlabel('normalized ~60d')
plt.ylabel('synthetic PnL, sum of selected strategies')
plt.legend()
plt.tight_layout(); plt.savefig(out,dpi=180)
out2=base/f'portfolio_{stamp}_components.png'
plt.figure(figsize=(14,7),facecolor='white')
for r,c in zip(sel,aligned):
    plt.plot(c,linewidth=1.4,alpha=.85,label=f"{r['ticker']} {r['timeframe']} {r['strategy']}")
plt.grid(True,alpha=.18); plt.axhline(0,color='#888',linewidth=.8,alpha=.35)
plt.title('50%+/month candidates — components')
plt.xlabel('normalized ~60d'); plt.ylabel('synthetic PnL')
plt.legend(fontsize=8); plt.tight_layout(); plt.savefig(out2,dpi=180)
summary={'selected':[{k:r.get(k) for k in ['ticker','timeframe','strategy','params','trades','win_rate','total_pnl','profit_factor','max_drawdown','_cycle','_set']} for r in sel], 'profile':prof, 'total2m':sum(float(r.get('total_pnl') or 0) for r in sel), 'monthly':sum(float(r.get('total_pnl') or 0) for r in sel)/2, 'pct_month_on_20k':sum(float(r.get('total_pnl') or 0) for r in sel)/2/20000*100, 'png':str(out), 'components_png':str(out2)}
(base/f'portfolio_{stamp}.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(summary,ensure_ascii=False))
