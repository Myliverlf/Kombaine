#!/usr/bin/env python3
from __future__ import annotations
import json, pathlib, sys
root=pathlib.Path('/root/prop-desk/strategy_combine')
fl=root.parent/'futures_lab'
sys.path.insert(0,str(fl))
from futures_lab import run_backtest, _synthetic_spec_for_file
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

def main():
    out_dir=root/'reports'/'strategy_architect'
    cycles=sorted(out_dir.glob('cycle_*.json'), key=lambda p:p.stat().st_mtime, reverse=True)
    if not cycles:
        raise SystemExit('no cycle_*.json')
    cycle=cycles[0]
    d=json.loads(cycle.read_text())
    data_root=fl/'artifacts'/'tinkoff_futures_data'
    initial=1_000_000.0
    seen=set(); rows=[]
    for row in d.get('top',[]):
        key=(row['ticker'],row['timeframe'],row['strategy'],json.dumps(row.get('params',{}),sort_keys=True))
        if key in seen: continue
        seen.add(key); rows.append(row)
    rows=rows[:12]
    curves=[]
    for rank,row in enumerate(rows,1):
        ticker=row['ticker']; tf=row['timeframe']; strategy=row['strategy']; params=row.get('params') or {}
        path=data_root/f'{ticker}_60d_{tf}_continuous.csv'
        df=pd.read_csv(path)
        metrics,trades,eq=run_backtest(df,_synthetic_spec_for_file(ticker),strategy,params,initial_cash=initial,contracts=1,max_contracts=1,stop_atr=2.0,take_atr=3.0,max_hold_bars=192 if tf=='15m' else 48)
        pnl=(eq.astype(float)-initial).reset_index(drop=True)
        curves.append((rank,row,pnl,metrics))
    stamp=d.get('ts') or cycle.stem.replace('cycle_','')
    png=out_dir/f'top_equity_curves_{stamp}.png'
    fig,axes=plt.subplots(3,4,figsize=(20,12),sharex=False)
    axes=axes.ravel()
    for ax,(rank,row,pnl,metrics) in zip(axes,curves):
        ax.plot(pnl.values, linewidth=1.6)
        ax.axhline(0,color='black',linewidth=0.7,alpha=0.4)
        color='green' if row.get('equity_shape_passed') else 'red'
        ax.set_title(f"#{rank} {row['ticker']} {row['timeframe']} {row['strategy']}\nPnL {row['total_pnl']:.0f} DD {row['max_drawdown']:.0f} PF {row['profit_factor']:.2f} WR {row['win_rate']:.1f}%",fontsize=9,color=color)
        ax.grid(True,alpha=0.25)
        ax.tick_params(labelsize=8)
        ax.set_ylabel('PnL RUB',fontsize=8)
    for ax in axes[len(curves):]: ax.axis('off')
    fig.suptitle('Strategy Architect top equity curves — local 60d backtest, top 12 unique candidates, live_orders=0',fontsize=14)
    fig.tight_layout(rect=[0,0,1,0.96])
    fig.savefig(png,dpi=150)

    portfolio_png=out_dir/f'top_equity_portfolio_{stamp}.png'
    N=500; aligned=[]
    for _,row,pnl,_ in curves:
        vals=pnl.values.astype(float)
        if len(vals)==0: continue
        idx=np.linspace(0,len(vals)-1,N)
        aligned.append(np.interp(idx,np.arange(len(vals)),vals))
    arr=np.vstack(aligned)
    mean=arr.mean(axis=0)
    plt.figure(figsize=(14,7))
    for vals in aligned: plt.plot(vals, alpha=0.25, linewidth=0.8)
    plt.plot(mean, color='black', linewidth=2.5, label='equal-weight avg top12')
    plt.axhline(0,color='black',linewidth=0.7,alpha=0.4)
    plt.grid(True,alpha=0.25)
    plt.title('Top-list equity bundle — average of top 12 unique curves (local 60d backtest)')
    plt.ylabel('PnL RUB')
    plt.legend(); plt.tight_layout(); plt.savefig(portfolio_png,dpi=150)

    summary=[]
    for rank,row,pnl,metrics in curves:
        summary.append({'rank':rank,'ticker':row['ticker'],'tf':row['timeframe'],'strategy':row['strategy'],'pnl':round(float(row['total_pnl']),2),'pf':round(float(row['profit_factor']),3),'dd':round(float(row['max_drawdown']),2),'wr':round(float(row['win_rate']),2),'shape_pass':bool(row.get('equity_shape_passed')),'rank_score':round(float(row['rank_score']),2)})
    summary_path=out_dir/f'top_equity_curves_{stamp}_summary.json'
    summary_path.write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'cycle':str(cycle),'png':str(png),'portfolio_png':str(portfolio_png),'summary':str(summary_path),'curves':len(curves),'top1':summary[0]},ensure_ascii=False))
if __name__=='__main__': main()
