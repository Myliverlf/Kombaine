#!/usr/bin/env python3
"""RUN-34: Volatility → Move Probability & Range Calibration.
Deterministic. Paper only. No LLM. No strategy.

Converts volatility forecast into calibrated probability/range estimates.
"""
from __future__ import annotations
import json,hashlib,math,time
import numpy as np
import pandas as pd
from pathlib import Path
import sys
SC=Path('/root/prop-desk/strategy_combine');FL=Path('/root/prop-desk/futures_lab')
sys.path[:0]=[str(SC),str(FL),str(SC/'engines')]
import question_loop_run15 as q15

SCHEMA='run34-move-probability-calibration-v1'
OUT=Path('/root/audits/strategy_combine_research_intelligence/run34')
OUT.mkdir(parents=True,exist_ok=True)
def now():return time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())

# ================================================== PREREGISTRATION ===
PREREG={
 'forecast':'vol_24h (expanding std, window 24)',
 'targets':{'T1':'abs_return_1h','T2':'range_1h','T3':'large_move_binary'},
 'horizon':'1h',
 'large_move_threshold_percentile':80,
 'calibration_method':'monotonic_bin_calibration',
 'n_calibration_bins':5,
 'oos_split':'chronological 60/40',
 'half_split':'chronological 50/50',
 'significance_alpha':0.05,
}
PREREG_PATH=OUT/'RUN34_PREREGISTRATION.json'

# ================================================== LOAD + PREPARE ===
def load_all():
    results={}
    for t,ds in q15.load_all().items():
        d=ds['train'].copy()
        if 'time' in d.columns:
            d['time']=pd.to_datetime(d['time']);d=d.set_index('time')
        ret=np.log(d.close/d.close.shift())
        vol_fc=ret.rolling(24).std()
        # T1: absolute future return
        abs_ret=ret.abs().shift(-1)
        # T2: future range
        if all(x in d for x in ['high','low']):
            fut_range=((d.high.shift(-1)-d.low.shift(-1))/d.close.shift(-1))
        else:
            fut_range=ret.abs().shift(-1)
        # T3: large move binary (from train quantile only)
        # will be computed after split
        df=pd.DataFrame({'ret':ret,'vol_fc':vol_fc,'abs_ret':abs_ret,'fut_range':fut_range}).dropna()
        if len(df)<200:continue
        results[t]=df
    return results

# ================================================== CALIBRATION ===
def r_squared(y,yhat):
    ss_res=((y-yhat)**2).sum();ss_tot=((y-y.mean())**2).sum()
    return 1-ss_res/max(ss_tot,1e-12)

def calibrate_bins(forecast,actual,n_bins=5):
    """Monotonic bin calibration."""
    thr=np.percentile(forecast.dropna(),np.linspace(0,100,n_bins+1))
    bins=pd.cut(forecast,bins=thr,include_lowest=True)
    results=[]
    for i in range(n_bins):
        mask=bins==bins.cat.categories[i]
        if mask.sum()<20:continue
        pred_mean=float(forecast[mask].mean())
        actual_mean=float(actual[mask].mean())
        actual_freq=float((actual[mask]>0).mean()) if actual.dtype==bool or actual.nunique()<=2 else actual_mean
        results.append({'bin':i+1,'pred_vol':round(pred_mean,6),'actual_mean':round(actual_mean,6),
                        'actual_freq':round(actual_freq,4),'n':int(mask.sum())})
    return results

def brier_score(y_true,y_prob):
    return float(np.mean((y_true-y_prob)**2))

def log_loss(y_true,y_prob):
    eps=1e-10
    y_prob=np.clip(y_prob,eps,1-eps)
    return float(-np.mean(y_true*np.log(y_prob)+(1-y_true)*np.log(1-y_prob)))

# ================================================== TEST ONE ASSET ===
def test_asset(df):
    n=len(df);split=int(n*0.6)
    train=df.iloc[:split];test=df.iloc[split:]
    # large move threshold from TRAIN only
    thr_large=np.percentile(train['abs_ret'].dropna(),PREREG['large_move_threshold_percentile'])
    # add large_move binary
    df_test=test.copy()
    df_test['large_move']=(test['abs_ret']>=thr_large).astype(int)
    # continuous calibration
    forecast_test=test['vol_fc'].values
    abs_ret_test=test['abs_ret'].values
    range_test=test['fut_range'].values
    large_move_test=df_test['large_move'].values
    # OOS correlation
    corr_abs=float(np.corrcoef(forecast_test,abs_ret_test)[0,1])
    corr_range=float(np.corrcoef(forecast_test,range_test)[0,1])
    rank_abs=float(pd.Series(forecast_test).rank().corr(pd.Series(abs_ret_test).rank()))
    # R²
    yhat_abs=np.full(len(abs_ret_test),train['abs_ret'].mean())
    r2_abs=r_squared(abs_ret_test,yhat_abs)
    r2_range=r_squared(range_test,np.full(len(range_test),train["fut_range"].mean()))
    # calibration
    cal=calibrate_bins(test['vol_fc'],df_test['large_move'],PREREG['n_calibration_bins'])
    # Brier score
    # simple probability: normalized forecast
    vol_norm=(forecast_test-forecast_test.min())/(forecast_test.max()-forecast_test.min()+1e-12)
    brier=brier_score(large_move_test,vol_norm)
    ll=log_loss(large_move_test,vol_norm)
    # quantile lift
    q80=np.percentile(forecast_test,80)
    high_mask=forecast_test>=q80
    low_mask=forecast_test<=np.percentile(forecast_test,20)
    lift=float(large_move_test[high_mask].mean()/large_move_test.mean()) if large_move_test.mean()>0 else 0
    low_freq=float(large_move_test[low_mask].mean())
    high_freq=float(large_move_test[high_mask].mean())
    base_rate=float(large_move_test.mean())
    # directional independence
    signed_ret=test['ret'].shift(-1).dropna().values[:len(forecast_test)]
    dir_corr=float(np.corrcoef(forecast_test[:len(signed_ret)],signed_ret)[0,1]) if len(signed_ret)==len(forecast_test) else 0
    # temporal stability
    half=len(test)//2
    h1_corr=float(np.corrcoef(forecast_test[:half],abs_ret_test[:half])[0,1])
    h2_corr=float(np.corrcoef(forecast_test[half:],abs_ret_test[half:])[0,1])
    return {'corr_abs':round(corr_abs,6),'corr_range':round(corr_range,6),
            'rank_abs':round(rank_abs,6),
            'r2_abs':round(r2_abs,6),'r2_range':round(r2_range,6),
            'calibration':cal,'brier':round(brier,6),'log_loss':round(ll,6),
            'lift':round(lift,4),'high_freq':round(high_freq,4),
            'low_freq':round(low_freq,4),'base_rate':round(base_rate,4),
            'dir_corr':round(dir_corr,6),
            'stability':{'h1_corr':round(h1_corr,6),'h2_corr':round(h2_corr,6)},
            'n_test':len(test),'thr_large':round(thr_large,6)}

# ================================================== MAIN ===
def run34_full():
    PREREG_PATH.write_text(json.dumps(PREREG,indent=1))
    datasets=load_all()
    if not datasets:return {'error':'no data'}
    results={}
    for t,df in datasets.items():
        results[t]=test_asset(df)
    # aggregate
    corrs=[r['corr_abs'] for r in results.values()]
    lifts=[r['lift'] for r in results.values()]
    briers=[r['brier'] for r in results.values()]
    avg_corr=np.mean(corrs) if corrs else 0
    avg_lift=np.mean(lifts) if lifts else 0
    avg_brier=np.mean(briers) if briers else 1
    # monotonicity
    monotonic=True
    for t,r in results.items():
        cal=r.get('calibration',[])
        if len(cal)>=3:
            actual_means=[c['actual_mean'] for c in cal]
            for i in range(len(actual_means)-1):
                if actual_means[i]>actual_means[i+1]+0.001:monotonic=False
    # verdict
    if avg_corr>0.2 and avg_lift>1.5 and monotonic:overall='MOVE_DISTRIBUTION_PREDICTABLE_STRONG'
    elif avg_corr>0.1:overall='MOVE_DISTRIBUTION_PREDICTABLE_CONTEXTUAL'
    elif avg_corr>0.05:overall='MOVE_DISTRIBUTION_PREDICTABLE_WEAK'
    else:overall='NO_USEFUL_MOVE_PROBABILITY'
    result={'schema':SCHEMA,'preregistration':PREREG,
            'per_asset':results,'summary':{'avg_corr':round(avg_corr,4),'avg_lift':round(avg_lift,4),
            'avg_brier':round(avg_brier,4),'monotonic':monotonic},
            'overall':overall,'ts':now()}
    (OUT/'run34_full.json').write_text(json.dumps(result,ensure_ascii=False,indent=1,default=str))
    return result

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--out',required=True);a=p.parse_args()
    r_=run34_full()
    Path(a.out).write_text(json.dumps(r_,ensure_ascii=False,indent=1,default=str))
    print('OVERALL:',r_['overall'])
    print('SUMMARY:',r_['summary'])
    for t,v in r_['per_asset'].items():
        print(f'{t}: corr={v["corr_abs"]} lift={v["lift"]} brier={v["brier"]} dir={v["dir_corr"]}')
