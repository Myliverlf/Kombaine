#!/usr/bin/env python3
"""RUN-23: Nonlinear Residual Dependence Test.
Deterministic. Paper only. No LLM. No strategy.

Tests leverage effect: |return[t]| → signed return[t+1].
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

SCHEMA='run23-nonlinear-dependence-v1'
OUT=Path('/root/audits/strategy_combine_research_intelligence/run23')
OUT.mkdir(parents=True,exist_ok=True)
def now():return time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())

# ================================================== PREREGISTRATION ===
PREREG={
 'theory':'T_NONLINEAR_LEVERAGE',
 'statement':'|return[t]| has significant relationship with signed return[t+1] beyond linear ret[t]',
 'predictions':{
  'P1':'corr(|ret[t]|, ret[t+1]) significantly different from zero',
  'P2':'|ret[t]| adds information beyond ret[t] in regression',
  'P3':'effect direction preserved across two time halves',
  'P4':'if only in extreme tail → CONTEXTUAL, not UNIVERSAL'},
 'extreme_threshold_quantile':0.9,
 'significance_alpha':0.05,
 'half_split':'chronological 50/50',
}
PREREG_PATH=OUT/'RUN23_PREREGISTRATION.json'

# ================================================== LOAD DATA ===
def load_all_assets():
    results={}
    for t,ds in q15.load_all().items():
        d=ds['train'].copy()
        if 'time' in d.columns:
            d['time']=pd.to_datetime(d['time']);d=d.set_index('time')
        ret=np.log(d.close/d.close.shift()).dropna()
        abs_ret=ret.abs()
        ret_next=ret.shift(-1)
        df=pd.DataFrame({'ret':ret,'abs_ret':abs_ret,'ret_next':ret_next}).dropna()
        if len(df)<200:continue
        results[t]=df
    return results

# ================================================== OLS ===
def ols(y,X):
    X_=np.column_stack([np.ones(len(X)),X])
    try:
        beta=np.linalg.lstsq(X_,y,rcond=None)[0]
        resid=y-X_@beta
        # t-stat for |ret| coefficient
        n=len(y);k=X_.shape[1]
        mse=(resid**2).sum()/(n-k)
        var_beta=mse*np.linalg.inv(X_.T@X_).diagonal()
        se=np.sqrt(var_beta)
        t_stat=beta/se
        return {'beta':beta,'t_stat':t_stat,'n':n,'r2':1-(resid**2).sum()/((y-y.mean())**2).sum() if ((y-y.mean())**2).sum()>0 else 0}
    except:return None

# ================================================== TEST PER ASSET ===
def test_asset(df):
    ret=df['ret'].values;abs_r=df['abs_ret'].values;ret_n=df['ret_next'].values
    n=len(df)
    # A: raw correlation
    corr_raw=float(np.corrcoef(abs_r,ret_n)[0,1])
    t_raw=corr_raw*math.sqrt(n-2)/math.sqrt(max(1-corr_raw**2,1e-12))
    # B: regression ret_next ~ ret + |ret|
    m=ols(ret_n,np.column_stack([ret,abs_r]))
    if m is None:return {'error':'ols_failed'}
    # linear-only model for comparison
    m_lin=ols(ret_n,ret.reshape(-1,1))
    # incremental R²
    r2_full=m['r2'];r2_lin=m_lin['r2'] if m_lin else 0
    incr_r2=r2_full-r2_lin
    # coefficient for |ret|
    abs_ret_coef=float(m['beta'][2]) if len(m['beta'])>2 else 0
    abs_ret_t=float(m['t_stat'][2]) if len(m['t_stat'])>2 else 0
    # C: time-half stability
    half=n//2
    m1=ols(ret_n[:half],np.column_stack([ret[:half],abs_r[:half]]))
    m2=ols(ret_n[half:],np.column_stack([ret[half:],abs_r[half:]]))
    h1_coef=float(m1['beta'][2]) if m1 and len(m1['beta'])>2 else 0
    h2_coef=float(m2['beta'][2]) if m2 and len(m2['beta'])>2 else 0
    same_direction=bool(np.sign(h1_coef)==np.sign(h2_coef)) if h1_coef!=0 and h2_coef!=0 else False
    # D: extreme vs normal
    thr=np.percentile(abs_r,PREREG['extreme_threshold_quantile']*100)
    extreme=abs_r>=thr;normal=abs_r<thr
    corr_extreme=float(np.corrcoef(abs_r[extreme],ret_n[extreme])[0,1]) if extreme.sum()>20 else 0
    corr_normal=float(np.corrcoef(abs_r[normal],ret_n[normal])[0,1]) if normal.sum()>20 else 0
    n_extreme=int(extreme.sum());n_normal=int(normal.sum())
    # significance
    sig=abs(abs_ret_t)>1.96
    return {'n':n,'corr_raw':round(corr_raw,6),'t_raw':round(float(t_raw),4),
            'abs_ret_coef':round(abs_ret_coef,6),'abs_ret_t':round(abs_ret_t,4),
            'r2_full':round(r2_full,6),'r2_linear':round(r2_lin,6),'incremental_r2':round(incr_r2,6),
            'half1_coef':round(h1_coef,6),'half2_coef':round(h2_coef,6),
            'same_direction':same_direction,
            'corr_extreme':round(corr_extreme,6),'corr_normal':round(corr_normal,6),
            'n_extreme':n_extreme,'n_normal':n_normal,
            'significant':sig}

# ================================================== VERDICT ===
def verdict(per_asset):
    """Preregistered verdict from all assets."""
    supported=0;contextual=0;refuted=0;inconclusive=0
    for t,r in per_asset.items():
        if r.get('error'):inconclusive+=1;continue
        if r['significant'] and r['same_direction']:
            if r['corr_extreme']!=0 and abs(r['corr_extreme'])>abs(r['corr_normal'])*2:
                contextual+=1
            else:supported+=1
        elif r['significant'] and not r['same_direction']:
            contextual+=1
        elif not r['significant']:
            refuted+=1
        else:inconclusive+=1
    total=len(per_asset)
    if supported>=2:return 'NONLINEAR_DEPENDENCE_SUPPORTED'
    if contextual>=1:return 'NONLINEAR_DEPENDENCE_CONTEXTUAL'
    if refuted>=3:return 'NONLINEAR_DEPENDENCE_REFUTED'
    return 'NONLINEAR_DEPENDENCE_INCONCLUSIVE'

# ================================================== MAIN ===
def run23_full():
    PREREG_PATH.write_text(json.dumps(PREREG,indent=1))
    assets=load_all_assets()
    per_asset={t:test_asset(df) for t,df in assets.items()}
    v=verdict(per_asset)
    result={'schema':SCHEMA,'preregistration':PREREG,'per_asset':per_asset,'verdict':v,'ts':now()}
    (OUT/'run23_full.json').write_text(json.dumps(result,ensure_ascii=False,indent=1,default=str))
    return result

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--out',required=True);a=p.parse_args()
    r_=run23_full()
    Path(a.out).write_text(json.dumps(r_,ensure_ascii=False,indent=1,default=str))
    print('VERDICT:',r_['verdict'])
    for t,v in r_['per_asset'].items():
        if v.get('error'):print(f'  {t}: {v["error"]}');continue
        print(f'  {t}: n={v["n"]} corr={v["corr_raw"]} t={v["t_raw"]} sig={v["significant"]} incr_r2={v["incremental_r2"]} stable={v["same_direction"]}')
