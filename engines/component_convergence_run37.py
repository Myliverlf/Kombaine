#!/usr/bin/env python3
"""RUN-37: IMOEX Component Divergence / Convergence.
Deterministic. Paper only. No LLM. No strategy optimization.

Tests if component stocks converge after abnormal divergence from IMOEX.
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

SCHEMA='run37-component-convergence-v1'
OUT=Path('/root/audits/strategy_combine_research_intelligence/run37')
OUT.mkdir(parents=True,exist_ok=True)
def now():return time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())

PREREG={
 'universe':['SBER','LKOH','CNY'],  # GAZP not available
 'market_factor':'IMOEX equal-weight log return',
 'beta_method':'expanding rolling OLS (24-bar minimum)',
 'divergence_lookback':24,
 'forecast_horizon':4,
 'extreme_threshold_percentile':20,
 'oos_split':'chronological 60/40',
 'frequency':'1h',
}
PREREG_PATH=OUT/'RUN37_PREREGISTRATION.json'

# ================================================== LOAD ===
def load_all():
    all_data={}
    raw=q15.load_all()
    if 'IMOEX' not in raw:return {}
    imoex=raw['IMOEX']['train'].copy()
    if 'time' in imoex.columns:
        imoex['time']=pd.to_datetime(imoex['time']);imoex=imoex.set_index('time')
    imoex_ret=np.log(imoex.close/imoex.close.shift())
    all_data['IMOEX']={'ret':imoex_ret,'close':imoex.close}
    for t in PREREG['universe']:
        if t not in raw:continue
        d=raw[t]['train'].copy()
        if 'time' in d.columns:
            d['time']=pd.to_datetime(d['time']);d=d.set_index('time')
        ret=np.log(d.close/d.close.shift())
        # align with IMOEX
        common=ret.index.intersection(imoex_ret.index)
        if len(common)<200:continue
        ret_c=ret.loc[common];imoex_c=imoex_ret.loc[common];close_c=d.close.loc[common]
        # expanding beta (causal)
        df=pd.DataFrame({'asset_ret':ret_c,'mkt_ret':imoex_c,'close':close_c}).dropna()
        betas=[];residuals=[];divs=[];div_zs=[]
        mkt_cumret=df['mkt_ret'].cumsum()
        asset_cumret=df['asset_ret'].cumsum()
        # beta estimation
        for i in range(len(df)):
            if i<24:
                betas.append(np.nan);residuals.append(np.nan)
                divs.append(np.nan);div_zs.append(np.nan)
                continue
            hist=df.iloc[:i]
            X=hist['mkt_ret'].values.reshape(-1,1)
            y=hist['asset_ret'].values
            X_=np.column_stack([np.ones(len(X)),X])
            try:
                beta=np.linalg.lstsq(X_,y,rcond=None)[0][1]
            except:
                betas.append(np.nan);residuals.append(np.nan)
                divs.append(np.nan);div_zs.append(np.nan);continue
            resid_i=df['asset_ret'].iloc[i]-beta*df['mkt_ret'].iloc[i]
            betas.append(beta);residuals.append(resid_i)
            # cumulative divergence over lookback
            lookback=min(PREREG['divergence_lookback'],i)
            cum_div=df['asset_ret'].iloc[i-lookback:i].sum()-beta*df['mkt_ret'].iloc[i-lookback:i].sum()
            divs.append(cum_div)
            div_zs.append(np.nan)  # will compute after
        df['beta']=betas;df['residual']=residuals;df['divergence']=divs
        # divergence z-score (expanding)
        div_series=pd.Series(divs,index=df.index)
        df['div_z']=(div_series-div_series.expanding(min_periods=50).mean())/div_series.expanding(min_periods=50).std().replace(0,np.nan)
        # future relative residual
        df['fut_rel_return']=df['residual'].shift(-PREREG['forecast_horizon'])
        df=df.dropna(subset=['div_z','fut_rel_return'])
        if len(df)<200:continue
        all_data[t]=df
    return all_data

# ================================================== TEST ===
def test_asset(df,asset_name):
    n=len(df);split=int(n*0.6)
    train=df.iloc[:split];test=df.iloc[split:]
    # thresholds from train
    thr_pos=np.percentile(train['div_z'].dropna(),100-PREREG['extreme_threshold_percentile'])
    thr_neg=np.percentile(train['div_z'].dropna(),PREREG['extreme_threshold_percentile'])
    # test sets
    div_test=test['div_z'].values
    fut_test=test['fut_rel_return'].values
    pos_mask=div_test>=thr_pos
    neg_mask=div_test<=thr_neg
    normal_mask=~pos_mask&~neg_mask
    # continuous
    corr=float(np.corrcoef(div_test,fut_test)[0,1])
    rank_corr=float(pd.Series(div_test).rank().corr(pd.Series(fut_test).rank()))
    # extreme tests
    def stats(mask,label):
        if mask.sum()<10:return {'label':label,'n':int(mask.sum())}
        s=fut_test[mask]
        return {'label':label,'n':int(mask.sum()),
                'mean':round(float(np.mean(s)),6),
                'median':round(float(np.median(s)),6),
                'std':round(float(np.std(s)),6)}
    pos_stats=stats(pos_mask,'positive_divergence')
    neg_stats=stats(neg_mask,'negative_divergence')
    normal_stats=stats(normal_mask,'normal')
    # convergence fraction
    def convergence_fraction(mask):
        if mask.sum()<5:return None
        divs_sel=div_test[mask]
        futs_sel=fut_test[mask]
        # fraction = -future / divergence (negative future relative = convergence for pos divergence)
        fractions=-futs_sel/divs_sel
        return {'mean':round(float(np.mean(fractions)),4),
                'median':round(float(np.median(fractions)),4),
                'full_conv_pct':round(float((np.abs(fractions)>=0.5).mean()),4)}
    pos_cf=convergence_fraction(pos_mask)
    neg_cf=convergence_fraction(neg_mask)
    # self-inclusion: compare raw vs beta-corrected
    # crude: correlation of raw relative vs corrected
    raw_relative=test['asset_ret'].values-test['mkt_ret'].values
    corrected_residual=test['residual'].values
    self_incl_corr=float(np.corrcoef(raw_relative[:len(corrected_residual)],corrected_residual)[0,1]) if len(corrected_residual)==len(raw_relative) else np.nan
    # temporal stability
    half=len(test)//2
    h1_corr=float(np.corrcoef(div_test[:half],fut_test[:half])[0,1])
    h2_corr=float(np.corrcoef(div_test[half:],fut_test[half:])[0,1])
    # convergence decomposition
    def decompose_convergence(mask):
        if mask.sum()<5:return None
        # after divergence, who moves?
        # asset moves toward market = asset goes opposite to divergence
        # market moves toward asset = market goes in divergence direction
        asset_futs=test['asset_ret'].iloc[mask.nonzero()[0]+PREREG['forecast_horizon']:mask.nonzero()[0]+2*PREREG['forecast_horizon']].mean() if mask.sum()>0 else 0
        mkt_futs=test['mkt_ret'].iloc[mask.nonzero()[0]+PREREG['forecast_horizon']:mask.nonzero()[0]+2*PREREG['forecast_horizon']].mean() if mask.sum()>0 else 0
        return {'asset_contribution':round(float(asset_futs),6),'market_contribution':round(float(mkt_futs),6)}
    return {'corr':round(corr,6),'rank_corr':round(rank_corr,6),
            'positive_div':pos_stats,'negative_div':neg_stats,'normal':normal_stats,
            'pos_cf':pos_cf,'neg_cf':neg_cf,
            'self_inclusion_corr':round(self_incl_corr,4) if not np.isnan(self_incl_corr) else None,
            'stability':{'h1':round(h1_corr,6),'h2':round(h2_corr,6)},
            'n_test':len(test)}

# ================================================== MAIN ===
def run37_full():
    PREREG_PATH.write_text(json.dumps(PREREG,indent=1))
    datasets=load_all()
    if not datasets or len(datasets)<2:return {'error':'insufficient data'}
    results={}
    for t,df in datasets.items():
        if t=='IMOEX':continue
        results[t]=test_asset(df,t)
    # aggregate
    corrs=[r['corr'] for r in results.values()]
    avg_corr=np.mean(corrs) if corrs else 0
    pos_means=[r['positive_div'].get('mean',0) for r in results.values() if r['positive_div'].get('n',0)>10]
    neg_means=[r['negative_div'].get('mean',0) for r in results.values() if r['negative_div'].get('n',0)>10]
    avg_pos_mean=np.mean(pos_means) if pos_means else 0
    avg_neg_mean=np.mean(neg_means) if neg_means else 0
    # convergence check: pos divergence → negative future return
    convergence_signal=avg_pos_mean<0 and avg_neg_mean>0
    # self-inclusion
    si_corrs=[r['self_inclusion_corr'] for r in results.values() if r['self_inclusion_corr'] is not None]
    avg_si=np.mean(si_corrs) if si_corrs else 0
    # verdict
    if convergence_signal and avg_corr<-0.05:sci='COMPONENT_CONVERGENCE_SUPPORTED'
    elif avg_corr<-0.02:sci='COMPONENT_CONVERGENCE_CONTEXTUAL'
    elif avg_corr>0.05:sci='DIVERGENCE_CONTINUES'
    else:sci='NO_COMPONENT_RELATIVE_PREDICTABILITY'
    if avg_si>0.9:sci+=' (SELF_INCLUSION_RISK_HIGH)'
    econ='EXECUTION_MAPPING_NOT_READY'
    result={'schema':SCHEMA,'preregistration':PREREG,
            'per_asset':results,'summary':{'avg_corr':round(avg_corr,4),
            'avg_pos_mean':round(avg_pos_mean,6),'avg_neg_mean':round(avg_neg_mean,6),
            'convergence_signal':convergence_signal,'avg_self_inclusion':round(avg_si,4)},
            'scientific_verdict':sci,'economic_verdict':econ,'ts':now()}
    (OUT/'run37_full.json').write_text(json.dumps(result,ensure_ascii=False,indent=1,default=str))
    return result

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--out',required=True);a=p.parse_args()
    r_=run37_full()
    Path(a.out).write_text(json.dumps(r_,ensure_ascii=False,indent=1,default=str))
    print('SCIENTIFIC:',r_['scientific_verdict'])
    print('SUMMARY:',r_['summary'])
    for t,v in r_['per_asset'].items():
        print(f'{t}: corr={v["corr"]} pos_div={v["positive_div"].get("mean")} neg_div={v["negative_div"].get("mean")} si={v["self_inclusion_corr"]}')
