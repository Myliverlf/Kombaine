#!/usr/bin/env python3
"""RUN-35: Breakout Conditional on Move Probability.
Deterministic. Paper only. No LLM. No strategy optimization.

Tests if frozen RUN-34 large-move probability improves breakout continuation.
"""
from __future__ import annotations
import json,time
import numpy as np
import pandas as pd
from pathlib import Path
import sys
SC=Path('/root/prop-desk/strategy_combine');FL=Path('/root/prop-desk/futures_lab')
sys.path[:0]=[str(SC),str(FL),str(SC/'engines')]
import question_loop_run15 as q15

SCHEMA='run35-breakout-conditional-v1'
OUT=Path('/root/audits/strategy_combine_research_intelligence/run35')
OUT.mkdir(parents=True,exist_ok=True)
def now():return time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())

# ================================================== PREREGISTRATION ===
PREREG={
 'forecast':'vol_24h (expanding std, window 24), large-move top 20% from train',
 'breakout':'price crosses prior 20-bar high/low',
 'breakout_window':20,
 'entry':'conservative: next-bar open after breakout close',
 'holding_horizon':4,
 'gate_threshold_percentile':80,
 'cost_bps':5,
 'random_gate_repeats':50,
 'oos_split':'chronological 60/40',
 'significance_alpha':0.05,
}
PREREG_PATH=OUT/'RUN35_PREREGISTRATION.json'

def r_squared(y,yhat):
    ss_res=((y-yhat)**2).sum();ss_tot=((y-y.mean())**2).sum()
    return 1-ss_res/max(ss_tot,1e-12)

# ================================================== LOAD + PREPARE ===
def load_all():
    results={}
    for t,ds in q15.load_all().items():
        d=ds['train'].copy()
        if 'time' in d.columns:
            d['time']=pd.to_datetime(d['time']);d=d.set_index('time')
        ret=np.log(d.close/d.close.shift())
        vol_fc=ret.rolling(24).std()
        # breakout: price crosses prior N-bar high/low
        N=PREREG['breakout_window']
        high_n=d.high.rolling(N).max().shift(1)
        low_n=d.low.rolling(N).min().shift(1)
        # breakout signals (conservative: only at close)
        breakout_up=(d.close>high_n).astype(int)
        breakout_down=(d.close<low_n).astype(int)
        breakout=breakout_up-breakout_down  # +1 up, -1 down, 0 none
        df=pd.DataFrame({'ret':ret,'close':d.close,'vol_fc':vol_fc,
                        'high_n':high_n,'low_n':low_n,
                        'breakout':breakout,'breakout_up':breakout_up,
                        'breakout_down':breakout_down}).dropna()
        if len(df)<200:continue
        results[t]=df
    return results

# ================================================== POST-BREAKOUT CONTINUATION ===
def post_breakout_return(df,horizon=4):
    """Compute post-breakout continuation for each breakout event."""
    results={'up':[],'down':[]}
    for i in range(len(df)-horizon):
        b=df['breakout'].iloc[i]
        if b==0:continue
        entry_price=df['close'].iloc[i+1] if i+1<len(df) else np.nan  # next-bar open
        exit_price=df['close'].iloc[i+horizon] if i+horizon<len(df) else np.nan
        if np.isnan(entry_price) or np.isnan(exit_price):continue
        cont=np.log(exit_price/entry_price)
        if b>0:results['up'].append(cont)
        else:results['down'].append(-cont)  # flip so positive = continuation
    return results

# ================================================== GATE ===
def create_gate(vol_fc,threshold_pct):
    thr=np.percentile(vol_fc.dropna(),threshold_pct)
    return vol_fc>=thr

def random_gate_mask(n,high_mask):
    coverage=high_mask.sum()/len(high_mask)
    rng=np.random.RandomState(42)
    return rng.random(n)<coverage

# ================================================== TEST ONE ASSET ===
def test_asset(df):
    n=len(df);split=int(n*0.6)
    train=df.iloc[:split];test=df.iloc[split:]
    # large move threshold from TRAIN
    thr_large=np.percentile(train['vol_fc'].dropna(),PREREG['gate_threshold_percentile'])
    # gates
    vol_fc_test=test['vol_fc'].values
    high_mask=vol_fc_test>=thr_large
    low_mask=vol_fc_test<=np.percentile(train['vol_fc'].dropna(),100-PREREG['gate_threshold_percentile'])
    random_mask=random_gate_mask(len(test),high_mask)
    # post-breakout continuation
    cont_ungated=post_breakout_return(test)
    cont_high=post_breakout_return(test[high_mask])
    cont_low=post_breakout_return(test[low_mask])
    cont_random=post_breakout_return(test[random_mask])
    # summarize
    def summarize(cont,name):
        all_cont=np.array(cont['up']+cont['down'])
        if len(all_cont)==0:return {'name':name,'n':0}
        return {'name':name,'n':len(all_cont),
                'mean':round(float(np.mean(all_cont)),6),
                'median':round(float(np.median(all_cont)),6),
                'cont_rate':round(float((all_cont>0).mean()),4),
                'rev_rate':round(float((all_cont<0).mean()),4),
                'std':round(float(np.std(all_cont)),6)}
    # volatility-normalized
    vol_norm_ungated=float(np.mean(np.abs(all_cont_test:=np.array(cont_ungated['up']+cont_ungated['down'])))/test['vol_fc'].mean()) if len(cont_ungated['up'])+len(cont_ungated['down'])>0 else 0
    vol_norm_high=float(np.mean(np.abs(np.array(cont_high['up']+cont_high['down'])))/test[high_mask]['vol_fc'].mean()) if len(cont_high['up'])+len(cont_high['down'])>0 and test[high_mask]['vol_fc'].mean()>0 else 0
    return {'ungated':summarize(cont_ungated,'ungated'),
            'high':summarize(cont_high,'high'),
            'low':summarize(cont_low,'low'),
            'random':summarize(cont_random,'random'),
            'vol_normalized':{'ungated':round(vol_norm_ungated,4),'high':round(vol_norm_high,4)},
            'n_breakouts_total':int((test['breakout']!=0).sum()),
            'n_high_gate':int(high_mask.sum())}

# ================================================== MAIN ===
def run35_full():
    PREREG_PATH.write_text(json.dumps(PREREG,indent=1))
    datasets=load_all()
    if not datasets:return {'error':'no data'}
    results={}
    for t,df in datasets.items():
        results[t]=test_asset(df)
    # aggregate
    summary={}
    for gate in ['ungated','high','low','random']:
        means=[results[t][gate]['mean'] for t in results if results[t][gate].get('n',0)>0]
        cont_rates=[results[t][gate]['cont_rate'] for t in results if results[t][gate].get('n',0)>0]
        summary[gate]={'avg_mean':round(np.mean(means),6) if means else 0,
                       'avg_cont_rate':round(np.mean(cont_rates),4) if cont_rates else 0,
                       'n_assets':len(means)}
    # scientific verdict
    high_beats_ungated=summary['high']['avg_mean']>summary['ungated']['avg_mean']
    high_beats_low=summary['high']['avg_mean']>summary['low']['avg_mean']
    high_beats_random=summary['high']['avg_mean']>summary['random']['avg_mean']
    vol_survives=results[list(results.keys())[0]]['vol_normalized']['high']>results[list(results.keys())[0]]['vol_normalized']['ungated'] if results else False
    if high_beats_ungated and high_beats_low and high_beats_random:sci='BREAKOUT_CONDITIONAL_EDGE_SUPPORTED'
    elif high_beats_ungated and high_beats_random:sci='BREAKOUT_CONDITIONAL_EFFECT_CONTEXTUAL'
    elif summary['high']['avg_mean']>0:sci='BREAKOUT_ACTIVITY_ONLY_NO_EDGE'
    else:sci='BREAKOUT_NOT_IMPROVED'
    # economic
    gross=summary['high']['avg_mean']
    cost=PREREG['cost_bps']/10000
    net=gross-cost
    if net>0.0001:econ='ECONOMIC_EDGE_SUPPORTED'
    elif net>0:econ='ECONOMIC_EDGE_COST_FRAGILE'
    else:econ='ECONOMIC_EDGE_NEGATIVE'
    breakeven_bps=round(gross*10000,1) if gross>0 else 0
    result={'schema':SCHEMA,'preregistration':PREREG,
            'per_asset':results,'summary':summary,
            'scientific_verdict':sci,'economic_verdict':econ,
            'gross_per_trade':round(gross,6),'cost_per_trade':round(cost,6),
            'net_per_trade':round(net,6),'breakeven_cost_bps':breakeven_bps,
            'ts':now()}
    (OUT/'run35_full.json').write_text(json.dumps(result,ensure_ascii=False,indent=1,default=str))
    return result

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--out',required=True);a=p.parse_args()
    r_=run35_full()
    Path(a.out).write_text(json.dumps(r_,ensure_ascii=False,indent=1,default=str))
    print('SCIENTIFIC:',r_['scientific_verdict'])
    print('ECONOMIC:',r_['economic_verdict'])
    print('GROSS/COST/NET:',r_['gross_per_trade'],'/',r_['cost_per_trade'],'/',r_['net_per_trade'])
    print('BREAKEVEN:',r_['breakeven_cost_bps'],'bps')
    for g in ['ungated','high','low','random']:
        s=r_['summary'][g]
        print(f'  {g}: mean={s["avg_mean"]} cont_rate={s["avg_cont_rate"]} n={s["n_assets"]}')
