#!/usr/bin/env python3
"""RUN-32: Forward Volatility / Range Predictability.
Deterministic. Paper only. No LLM. No strategy.

Tests if future volatility/range is predictable from OHLCV.
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

SCHEMA='run32-volatility-predictability-v1'
OUT=Path('/root/audits/strategy_combine_research_intelligence/run32')
OUT.mkdir(parents=True,exist_ok=True)
def now():return time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())

# ================================================== PREREGISTRATION ===
PREREG={
 'targets':{
  'T1':'causal_realized_volatility_1h = std(next 1h returns)',
  'T2':'causal_price_range_1h = (next_high - next_low) / next_close'},
 'horizons':['1h','3h'],
 'mechanisms':['M1_VOL_PERSISTENCE','M2_COMPRESSION_EXPANSION','M3_ACTIVITY_CONDITIONING'],
 'features':{
  'M1':['current_vol_24h'],
  'M2':['current_range_24h','current_vol_24h_percentile'],
  'M3':['volume_zscore','amihud_zscore','current_vol_24h']},
 'oos_split':'chronological 60/40',
 'quantile_for_classification':0.8,
 'significance_alpha':0.05,
 'half_split':'chronological 50/50',
 'min_sample':200,
}
PREREG_PATH=OUT/'RUN32_PREREGISTRATION.json'

# ================================================== LOAD + PREPARE ===
def load_all():
    results={}
    for t,ds in q15.load_all().items():
        d=ds['train'].copy()
        if 'time' in d.columns:
            d['time']=pd.to_datetime(d['time']);d=d.set_index('time')
        ret=np.log(d.close/d.close.shift())
        # future volatility (1h forward, causal)
        fut_ret_1h=ret.shift(-1)
        fut_vol_1h=fut_ret_1h.abs()  # absolute return as volatility proxy
        # future range (1h forward)
        if all(x in d for x in ['high','low']):
            fut_range_1h=((d.high.shift(-1)-d.low.shift(-1))/d.close.shift(-1))
        else:
            fut_range_1h=ret.abs().shift(-1)
        # 3h horizon
        fut_ret_3h=ret.rolling(3).sum().shift(-3)
        fut_vol_3h=ret.rolling(3).std().shift(-3)
        if all(x in d for x in ['high','low']):
            fut_range_3h=((d.high.rolling(3).max().shift(-3)-d.low.rolling(3).min().shift(-3))/d.close.shift(-3))
        else:
            fut_range_3h=ret.abs().rolling(3).mean().shift(-3)
        # features: current state
        vol_24h=ret.rolling(24).std()
        range_24h=ret.abs().rolling(24).mean()
        vol_percentile=vol_24h.expanding(min_periods=120).rank(pct=True)
        # volume / activity
        if 'volume' in d:
            vol_raw=d['volume']
            vm=vol_raw.expanding(min_periods=120).mean()
            vs=vol_raw.expanding(min_periods=120).std()
            vol_zscore=(vol_raw-vm)/vs.replace(0,np.nan)
            amihud=ret.abs()/vol_raw.replace(0,np.nan)
            amihud_zscore=(amihud-amihud.expanding(min_periods=120).mean())/amihud.expanding(min_periods=120).std().replace(0,np.nan)
        else:
            vol_zscore=pd.Series(0,index=d.index)
            amihud_zscore=pd.Series(0,index=d.index)
        df=pd.DataFrame({'ret':ret,
                         'fut_vol_1h':fut_vol_1h,'fut_range_1h':fut_range_1h,
                         'fut_vol_3h':fut_vol_3h,'fut_range_3h':fut_range_3h,
                         'vol_24h':vol_24h,'range_24h':range_24h,
                         'vol_pctile':vol_percentile,
                         'vol_zscore':vol_zscore,'amihud_zscore':amihud_zscore}).dropna()
        if len(df)<PREREG['min_sample']:continue
        results[t]=df
    return results

# ================================================== OLS ===
def ols(y,X):
    X_=np.column_stack([np.ones(len(X)),X])
    try:
        beta=np.linalg.lstsq(X_,y,rcond=None)[0]
        resid=y-X_@beta
        return {'beta':beta,'resid':resid}
    except:return None

def r_squared(y,yhat):
    ss_res=((y-yhat)**2).sum();ss_tot=((y-y.mean())**2).sum()
    return 1-ss_res/max(ss_tot,1e-12)

# ================================================== TEST MECHANISM ===
def test_mechanism(df,mechanism,target_col,horizon):
    n=len(df);split=int(n*0.6)
    train=df.iloc[:split];test=df.iloc[split:]
    y_train=train[target_col].values
    y_test=test[target_col].values
    if mechanism=='M1_VOL_PERSISTENCE':
        feat_cols=['vol_24h']
    elif mechanism=='M2_COMPRESSION_EXPANSION':
        feat_cols=['range_24h','vol_pctile']
    elif mechanism=='M3_ACTIVITY_CONDITIONING':
        feat_cols=['vol_24h','vol_zscore','amihud_zscore']
    else:return {}
    X_train=train[feat_cols].values
    X_test=test[feat_cols].values
    # baseline B0: mean
    yhat_b0=np.full(len(y_test),y_train.mean())
    r2_b0=r_squared(y_test,yhat_b0)
    corr_b0=float(np.corrcoef(y_test,yhat_b0)[0,1]) if len(y_test)>1 else 0
    # baseline B1: persistence (current vol = future vol proxy)
    if 'vol_24h' in df.columns:
        yhat_b1=test['vol_24h'].values
        r2_b1=r_squared(y_test,yhat_b1)
        corr_b1=float(np.corrcoef(y_test,yhat_b1)[0,1]) if len(y_test)>1 else 0
    else:
        r2_b1=r2_b0;corr_b1=corr_b0
    # model
    m=ols(y_train,X_train)
    if m is None:return {}
    yhat=m['beta'][0]+X_test@m['beta'][1:]
    r2=r_squared(y_test,yhat)
    corr=float(np.corrcoef(y_test,yhat)[0,1]) if len(y_test)>1 else 0
    # rank correlation
    rank_corr=float(pd.Series(y_test).rank().corr(pd.Series(yhat).rank())) if len(y_test)>10 else 0
    # quantile lift: top 20% forecast vs all
    thr=np.percentile(yhat,80)
    mask_forecast=yhat>=thr
    lift=float(y_test[mask_forecast].mean()/y_test.mean()) if y_test.mean()>0 and mask_forecast.sum()>0 else 0
    # large move classification
    thr_actual=np.percentile(y_test,PREREG['quantile_for_classification']*100)
    actual_large=y_test>=thr_actual
    predicted_large=mask_forecast
    tp=((actual_large)&(predicted_large)).sum()
    fp=((~actual_large)&(predicted_large)).sum()
    fn=((actual_large)&(~predicted_large)).sum()
    precision=tp/max(tp+fp,1)
    recall=tp/max(tp+fn,1)
    base_rate=actual_large.mean()
    return {'r2_oos':round(r2,6),'corr_oos':round(corr,6),'rank_corr':round(rank_corr,6),
            'r2_b0':round(r2_b0,6),'r2_b1':round(r2_b1,6),
            'corr_b0':round(corr_b0,6),'corr_b1':round(corr_b1,6),
            'lift':round(lift,4),
            'precision':round(precision,4),'recall':round(recall,4),
            'base_rate':round(float(base_rate),4),
            'n_train':len(y_train),'n_test':len(y_test)}

# ================================================== CROSS-ASSET ===
def cross_asset_stability(all_results):
    """Check if mechanism works across assets."""
    stability={}
    for mech in PREREG['mechanisms']:
        corrs=[all_results[t][mech]['corr_oos'] for t in all_results if mech in all_results[t]]
        r2s=[all_results[t][mech]['r2_oos'] for t in all_results if mech in all_results[t]]
        positive_corr=sum(1 for c in corrs if c>0)
        positive_r2=sum(1 for r in r2s if r>0)
        stability[mech]={'avg_corr':round(np.mean(corrs),4) if corrs else 0,
                         'avg_r2':round(np.mean(r2s),4) if r2s else 0,
                         'n_positive_corr':positive_corr,'n_total':len(corrs),
                         'n_positive_r2':positive_r2}
    return stability

# ================================================== VERDICT ===
def verdict(all_results,cross_asset):
    mech_verdicts={}
    for mech in PREREG['mechanisms']:
        corrs=[all_results[t][mech].get('corr_oos',0) for t in all_results if mech in all_results[t]]
        r2s=[all_results[t][mech].get('r2_oos',0) for t in all_results if mech in all_results[t]]
        avg_corr=np.mean(corrs) if corrs else 0
        avg_r2=np.mean(r2s) if r2s else 0
        n_pos_r2=sum(1 for r in r2s if r>0)
        if avg_corr>0.1 and avg_r2>0 and n_pos_r2>=2:mech_verdicts[mech]='SUPPORTED'
        elif avg_corr>0.05:mech_verdicts[mech]='CONTEXTUAL'
        elif avg_corr>-0.01:mech_verdicts[mech]='REFUTED'
        else:mech_verdicts[mech]='INCONCLUSIVE'
    # overall
    all_corrs=[all_results[t][m].get('corr_oos',0) for t in all_results for m in all_results[t]]
    all_r2=[all_results[t][m].get('r2_oos',0) for t in all_results for m in all_results[t]]
    avg_corr=np.mean(all_corrs) if all_corrs else 0
    avg_r2=np.mean(all_r2) if all_r2 else 0
    if avg_corr>0.15 and avg_r2>0.05:overall='VOLATILITY_PREDICTABILITY_STRONG'
    elif avg_corr>0.08:overall='VOLATILITY_PREDICTABILITY_CONTEXTUAL'
    elif avg_corr>0.03:overall='VOLATILITY_PREDICTABILITY_WEAK'
    else:overall='NO_VOLATILITY_PREDICTABILITY'
    return mech_verdicts,overall,{'avg_corr':round(avg_corr,4),'avg_r2':round(avg_r2,4)}

# ================================================== MAIN ===
def run32_full():
    PREREG_PATH.write_text(json.dumps(PREREG,indent=1))
    datasets=load_all()
    if not datasets:return {'error':'no data'}
    all_results={}
    for t,df in datasets.items():
        all_results[t]={}
        for mech in PREREG['mechanisms']:
            for target in ['fut_vol_1h','fut_range_1h']:
                for horizon in ['1h']:
                    all_results[t][mech]=test_mechanism(df,mech,target,horizon)
    cross=cross_asset_stability(all_results)
    mech_v,overall,comparison=verdict(all_results,cross)
    # comparison with old targets
    old_comparison={'absolute_return_corr':-0.012,'relative_return_corr':0.024,
                    'volatility_corr':comparison['avg_corr']}
    result={'schema':SCHEMA,'preregistration':PREREG,
            'n_assets':len(datasets),
            'results':all_results,'cross_asset':cross,
            'mechanism_verdicts':mech_v,'overall':overall,'comparison':comparison,
            'old_vs_new':old_comparison,'ts':now()}
    (OUT/'run32_full.json').write_text(json.dumps(result,ensure_ascii=False,indent=1,default=str))
    return result

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--out',required=True);a=p.parse_args()
    r_=run32_full()
    Path(a.out).write_text(json.dumps(r_,ensure_ascii=False,indent=1,default=str))
    print('OVERALL:',r_['overall'])
    print('COMPARISON:',r_['comparison'])
    print('OLD vs NEW:',r_['old_vs_new'])
    for mech,v in r_['mechanism_verdicts'].items():
        print(f'  {mech}: {v}')
