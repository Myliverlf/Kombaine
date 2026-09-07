#!/usr/bin/env python3
"""RUN-36: High-Vol Failed Breakout / Reversal Independent Validation.
Deterministic. Paper only. No LLM. No strategy optimization.

Independent test of reversal anomaly discovered in RUN-35.
Uses temporal separation: discovery and validation periods do not overlap.
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

SCHEMA='run36-reversal-independent-validation-v1'
OUT=Path('/root/audits/strategy_combine_research_intelligence/run36')
OUT.mkdir(parents=True,exist_ok=True)
def now():return time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())

# ================================================== PREREGISTRATION ===
PREREG={
 'forecast':'vol_24h (expanding std, window 24)',
 'breakout':'price crosses prior 20-bar high/low',
 'breakout_window':20,
 'entry':'next-bar open after breakout close',
 'holding_horizon':4,
 'gate_threshold_percentile':80,
 'cost_bps':5,
 'random_gate_seeds':50,
 'random_gate_seeds_validation':50,
 'data_split':'40% train / 20% discovery / 40% validation (non-overlapping)',
 'significance_alpha':0.05,
}
PREREG_PATH=OUT/'RUN36_PREREGISTRATION.json'
DISCOVERY_SAMPLE='middle 20% of each asset time series'
VALIDATION_SAMPLE='last 40% of each asset time series (never used in RUN-35 test)'

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
        N=PREREG['breakout_window']
        high_n=d.high.rolling(N).max().shift(1)
        low_n=d.low.rolling(N).min().shift(1)
        breakout_up=(d.close>high_n).astype(int)
        breakout_down=(d.close<low_n).astype(int)
        breakout=breakout_up-breakout_down
        df=pd.DataFrame({'ret':ret,'close':d.close,'vol_fc':vol_fc,
                        'high_n':high_n,'low_n':low_n,
                        'breakout':breakout}).dropna()
        if len(df)<200:continue
        results[t]=df
    return results

# ================================================== REVERSAL CONTINUATION ===
def post_breakout_events(df,horizon=4):
    """Returns list of dicts with breakout events and their continuation."""
    events=[]
    for i in range(len(df)-horizon):
        b=df['breakout'].iloc[i]
        if b==0:continue
        entry_price=df['close'].iloc[i+1] if i+1<len(df) else np.nan
        exit_price=df['close'].iloc[i+horizon] if i+horizon<len(df) else np.nan
        if np.isnan(entry_price) or np.isnan(exit_price):continue
        continuation=np.log(exit_price/entry_price)
        reversal=-continuation  # positive = price moved against breakout
        events.append({'idx':i,'breakout_dir':b,'continuation':continuation,
                      'reversal':reversal,'entry':entry_price,'exit':exit_price,
                      'vol_fc':df['vol_fc'].iloc[i]})
    return events

# ================================================== GATE + RANDOM NULL ===
def create_gate(vol_fc,threshold_pct):
    thr=np.percentile(vol_fc.dropna(),threshold_pct)
    return vol_fc>=thr

def random_gates(n,n_repeats,high_count):
    """Deterministic random gates matching HIGH gate coverage."""
    rng=np.random.RandomState(42)
    masks=[]
    coverage=high_count/n
    for i in range(n_repeats):
        rng2=np.random.RandomState(42+i*137)
        masks.append(rng2.random(n)<coverage)
    return masks

def summarize_events(events,label):
    if not events:
        return {'label':label,'n':0}
    reversals=np.array([e['reversal'] for e in events])
    continuations=np.array([e['continuation'] for e in events])
    return {'label':label,'n':len(events),
            'mean_reversal':round(float(np.mean(reversals)),6),
            'median_reversal':round(float(np.median(reversals)),6),
            'reversal_rate':round(float((reversals>0).mean()),4),
            'mean_continuation':round(float(np.mean(continuations)),6),
            'std_reversal':round(float(np.std(reversals)),6),
            'se':round(float(np.std(reversals)/np.sqrt(len(reversals))),6)}

# ================================================== TEST ONE ASSET (SPLIT) ===
def test_asset_split(df,asset_name):
    n=len(df)
    # Three-way split: 40% train / 20% discovery / 40% validation
    train_end=int(n*0.4)
    discovery_end=int(n*0.6)
    train=df.iloc[:train_end]
    discovery=df.iloc[train_end:discovery_end]
    validation=df.iloc[discovery_end:]
    if len(discovery)<50 or len(validation)<50:
        return {'error':'insufficient data'}
    # gate threshold from TRAIN ONLY
    thr_high=np.percentile(train['vol_fc'].dropna(),PREREG['gate_threshold_percentile'])
    thr_low=np.percentile(train['vol_fc'].dropna(),100-PREREG['gate_threshold_percentile'])
    # ---- DISCOVERY SAMPLE (for reference only, NOT confirmatory) ----
    disc_events=post_breakout_events(discovery)
    disc_high_mask=discovery['vol_fc'].values>=thr_high
    disc_low_mask=discovery['vol_fc'].values<=thr_low
    disc_high_events=[e for e in disc_events if disc_high_mask[e['idx']]]
    disc_low_events=[e for e in disc_events if disc_low_mask[e['idx']]]
    disc_ungated=summarize_events(disc_events,'DISC_ungated')
    disc_high=summarize_events(disc_high_events,'DISC_high')
    disc_low=summarize_events(disc_low_events,'DISC_low')
    # ---- VALIDATION SAMPLE (independent) ----
    val_events=post_breakout_events(validation)
    val_high_mask=validation['vol_fc'].values>=thr_high
    val_low_mask=validation['vol_fc'].values<=thr_low
    val_high_events=[e for e in val_events if val_high_mask[e['idx']]]
    val_low_events=[e for e in val_events if val_low_mask[e['idx']]]
    val_ungated=summarize_events(val_events,'VAL_ungated')
    val_high=summarize_events(val_high_events,'VAL_high')
    val_low=summarize_events(val_low_events,'VAL_low')
    # random null distribution on validation
    val_n_random=len(val_events)
    random_reversals=[]
    for mask in random_gates(val_n_random,PREREG['random_gate_seeds_validation'],len(val_high_events)):
        mask_list=list(mask)
        # align mask to event indices
        random_ev=[e for i,e in enumerate(val_events) if i<len(mask_list) and mask_list[i]]
        random_reversals.append(float(np.mean([e['reversal'] for e in random_ev])) if random_ev else 0)
    random_null_mean=np.mean(random_reversals)
    random_null_5=np.percentile(random_reversals,5)
    random_null_95=np.percentile(random_reversals,95)
    high_reversal=val_high.get('mean_reversal',0)
    high_percentile=round(float(np.mean(np.array(random_reversals)<high_reversal)*100),1)
    # vol-normalized
    val_vol_mean=validation['vol_fc'].mean()
    val_norm_ungated=val_ungated.get('mean_reversal',0)/val_vol_mean if val_vol_mean>0 else 0
    val_norm_high=val_high.get('mean_reversal',0)/val_vol_mean if val_vol_mean>0 else 0
    # false breakout: reversal > 50% of breakout move
    false_breakout_rate_ungated=0
    false_breakout_rate_high=0
    if val_events:
        fbr_ung=sum(1 for e in val_events if abs(e['reversal'])>abs(e['continuation']))/len(val_events)
        false_breakout_rate_ungated=round(fbr_ung,4)
    if val_high_events:
        fbr_h=sum(1 for e in val_high_events if abs(e['reversal'])>abs(e['continuation']))/len(val_high_events)
        false_breakout_rate_high=round(fbr_h,4)
    # path diagnostics
    def path_diag(events):
        if not events:return {}
        revs=[e['reversal'] for e in events]
        conts=[e['continuation'] for e in events]
        return {'mfe':round(max(conts),6) if conts else 0,
                'mae':round(min(conts),6) if conts else 0,
                'max_rev':round(max(revs),6) if revs else 0}
    path=path_diag(val_high_events)
    return {
        'discovery':{'ungated':disc_ungated,'high':disc_high,'low':disc_low},
        'validation':{'ungated':val_ungated,'high':val_high,'low':val_low},
        'random_null':{'mean':round(random_null_mean,6),'p5':round(random_null_5,6),
                      'p95':round(random_null_95,6),'high_percentile':high_percentile},
        'vol_normalized':{'ungated':round(val_norm_ungated,4),'high':round(val_norm_high,4)},
        'false_breakout':{'ungated':false_breakout_rate_ungated,'high':false_breakout_rate_high},
        'path':path,
        'n_train':len(train),'n_discovery':len(discovery),'n_validation':len(validation)}

# ================================================== MAIN ===
def run36_full():
    PREREG_PATH.write_text(json.dumps(PREREG,indent=1))
    datasets=load_all()
    if not datasets:return {'error':'no data'}
    results={}
    for t,df in datasets.items():
        results[t]=test_asset_split(df,t)
    # aggregate validation
    val_high_means=[]
    val_ungated_means=[]
    for t,r in results.items():
        if 'error' in r:continue
        val_high_means.append(r['validation']['high'].get('mean_reversal',0))
        val_ungated_means.append(r['validation']['ungated'].get('mean_reversal',0))
    avg_val_high=np.mean(val_high_means) if val_high_means else 0
    avg_val_ungated=np.mean(val_ungated_means) if val_ungated_means else 0
    # scientific verdict
    high_beats_ungated=avg_val_high>avg_val_ungated
    high_beats_zero=avg_val_high>0
    high_beats_random=all(r['validation']['high'].get('mean_reversal',0)>r['random_null']['mean']
                          for t,r in results.items() if 'error' not in r)
    if high_beats_ungated and high_beats_zero and high_beats_random:sci='FAILED_BREAKOUT_REVERSAL_REPLICATED'
    elif high_beats_zero and high_beats_ungated:sci='FAILED_BREAKOUT_REVERSAL_CONTEXTUAL'
    elif high_beats_zero:sci='ACTIVITY_MAGNITUDE_ONLY'
    else:sci='REVERSAL_NOT_REPLICATED'
    # economic (only if replicated)
    gross=avg_val_high
    cost=PREREG['cost_bps']/10000
    net=gross-cost
    if sci in ['FAILED_BREAKOUT_REVERSAL_REPLICATED','FAILED_BREAKOUT_REVERSAL_CONTEXTUAL']:
        if net>0.0001:econ='REVERSAL_EDGE_CANDIDATE'
        elif net>0:econ='REVERSAL_COST_FRAGILE'
        else:econ='REVERSAL_ECONOMICALLY_NEGATIVE'
    else:econ='NO_ECONOMIC_TEST_JUSTIFIED'
    breakeven=round(gross*10000,1) if gross>0 else 0
    result={'schema':SCHEMA,'preregistration':PREREG,
            'discovery_sample':DISCOVERY_SAMPLE,'validation_sample':VALIDATION_SAMPLE,
            'per_asset':results,
            'summary':{'avg_val_high':round(avg_val_high,6),'avg_val_ungated':round(avg_val_ungated,6)},
            'scientific_verdict':sci,'economic_verdict':econ,
            'gross_per_trade':round(gross,6),'cost_per_trade':round(cost,6),
            'net_per_trade':round(net,6),'breakeven_cost_bps':breakeven,
            'ts':now()}
    (OUT/'run36_full.json').write_text(json.dumps(result,ensure_ascii=False,indent=1,default=str))
    return result

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--out',required=True);a=p.parse_args()
    r_=run36_full()
    Path(a.out).write_text(json.dumps(r_,ensure_ascii=False,indent=1,default=str))
    print('SCIENTIFIC:',r_['scientific_verdict'])
    print('ECONOMIC:',r_['economic_verdict'])
    print('SUMMARY:',r_['summary'])
    print('GROSS/COST/NET:',r_['gross_per_trade'],'/',r_['cost_per_trade'],'/',r_['net_per_trade'])
    print('BREAKEVEN:',r_['breakeven_cost_bps'],'bps')
    for t,v in r_['per_asset'].items():
        if 'error' in v:print(f'  {t}: ERROR');continue
        vh=v['validation']['high']
        vu=v['validation']['ungated']
        rn=v['random_null']
        print(f'  {t}: VAL high_rev={vh.get("mean_reversal")} ungated={vu.get("mean_reversal")} random_pctl={rn.get("high_percentile")}')
