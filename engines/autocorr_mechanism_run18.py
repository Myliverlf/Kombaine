#!/usr/bin/env python3
"""RUN-18: Residual Autocorrelation Mechanism Test.
Deterministic. Paper only. No LLM.

Tests whether autocorrelation_structure explains the structured residual
(ac_t < -3 on IMOEX/SBER/LKOH) found in RUN-15/16/17.
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

SCHEMA='run18-autocorr-mechanism-v1'
OUT=Path('/root/audits/strategy_combine_research_intelligence/run18')
OUT.mkdir(parents=True,exist_ok=True)
def now():return time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())
def sid(s,x):return s+'_'+hashlib.sha256(json.dumps(x,sort_keys=True,separators=(',',':')).encode()).hexdigest()[:16]

# ================================================== PREREGISTRATION ===
PREREG={
 'theory':'T_AUTOCORR_STRUCTURE',
 'statement':'Residual process contains real short-term serial dependence beyond noise',
 'lags_to_test':[1,2,4,8],
 'min_sample':200,
 'direction':-1,  # negative autocorrelation expected
 'falsification_rule':'If any lag 1-4 has autocorr >= 0 or |t| < 1.0, theory weakened. If all lags 1-4 >= 0, REFUTED.',
 'inconclusive_rule':'If |t| < 1.0 for lag 1 AND lag 2, INCONCLUSIVE.',
 'replication_min_assets':2,
 'vol_control_fails_if':'Volatility-regressed autocorr changes sign or drops |t| below 0.5',
 'baseline':'NO_SERIAL_STRUCTURE (residual is IID noise after current knowledge layer)',
 'verdicts':['AUTOCORR_MECHANISM_SUPPORTED','AUTOCORR_MECHANISM_CONTEXTUAL',
             'AUTOCORR_MECHANISM_REFUTED','AUTOCORR_MECHANISM_INCONCLUSIVE'],
}
PREREG_PATH=OUT/'RUN18_PREREGISTRATION.json'

# ================================================== LOAD DATA + COMPUTE RESIDUALS ===
def load_residuals():
    """Load datasets, compute hour-of-day residuals exactly as RUN-15."""
    datasets=q15.load_all()
    results={}
    for t,ds in datasets.items():
        d=ds['train'];ret=np.log(d.close/d.close.shift())
        if len(ret.dropna())<500:continue
        if 'time' in d:
            h=d.time.dt.hour
            z=pd.DataFrame({'r':ret,'h':h})
            exp_mean=z.groupby('h')['r'].transform(lambda s:s.shift(1).expanding().mean())
            resid=ret-exp_mean
        else:
            resid=ret-ret.expanding(120).mean().shift(1)
        valid=pd.DataFrame({'r':ret,'resid':resid}).dropna()
        if len(valid)<500:continue
        results[t]={'resid':resid,'ret':ret,'d':d,'n':len(valid),'valid':valid}
    return results

# ================================================== LAG AUTOCORRELATION ===
def lag_autocorr(resid,lags):
    """Measure autocorrelation at specific lags with t-statistics."""
    r=resid.dropna()
    if len(r)<PREREG['min_sample']:return {}
    results={}
    for lag in lags:
        x=r.iloc[lag:].values;y=r.iloc[:-lag].values
        n=len(x)
        ac=np.corrcoef(x,y)[0,1]
        # Fisher z-transform for t-stat
        se=1/math.sqrt(n-3)
        z=0.5*np.log((1+ac)/(1-ac)) if abs(ac)<1 else float('inf')
        t_stat=ac*math.sqrt(n-2)/math.sqrt(max(1-ac*ac,1e-12))
        results[lag]={'autocorr':round(float(ac),6),'t_stat':round(float(t_stat),4),
                      'n':n,'significant':abs(float(t_stat))>1.0}
    return results

# ================================================== DECAY PROFILE ===
def decay_profile(resid,max_lag=12):
    """Full decay profile from lag 1 to max_lag."""
    r=resid.dropna()
    if len(r)<PREREG['min_sample']:return {}
    results={}
    for lag in range(1,max_lag+1):
        x=r.iloc[lag:].values;y=r.iloc[:-lag].values
        ac=float(np.corrcoef(x,y)[0,1])
        se=1/math.sqrt(len(x)-3)
        t=ac*math.sqrt(len(x)-2)/math.sqrt(max(1-ac*ac,1e-12))
        results[lag]={'autocorr':round(ac,6),'t_stat':round(float(t),4)}
    return results

# ================================================== VOLATILITY CONTROL ===
def volatility_control(resid,ret,lags):
    """Autocorrelation after controlling for volatility (regress on |ret|, take residual)."""
    r=resid.dropna();ret_v=ret.dropna()
    common=r.index.intersection(ret_v.index)
    r=r.loc[common];ret_v=ret_v.loc[common]
    vol=np.abs(ret_v)
    # regress resid on vol: resid = a + b*vol + e
    X=np.column_stack([np.ones(len(r)),vol.values])
    y=r.values
    # OLS
    try:
        beta=np.linalg.lstsq(X,y,rcond=None)[0]
        resid_detr=y-X@beta
    except:return {}
    results={}
    for lag in lags:
        x=resid_detr[lag:];y_=resid_detr[:-lag]
        n=len(x)
        if n<PREREG['min_sample']:continue
        ac=float(np.corrcoef(x,y_)[0,1])
        t=ac*math.sqrt(n-2)/math.sqrt(max(1-ac*ac,1e-12))
        results[lag]={'autocorr_vol_controlled':round(ac,6),'t_stat':round(float(t),4),
                      'n':n,'significant':abs(float(t))>1.0}
    return results

# ================================================== CROSS-ASSET REPLICATION ===
def cross_asset_replication(residuals,lags):
    """Check if autocorrelation replicates across assets."""
    results={}
    for t,res in residuals.items():
        results[t]=lag_autocorr(res['resid'],lags)
    return results

# ================================================== THEORY EVALUATION ===
def evaluate_predictions(lag_results,decay,vol_control,cross_asset):
    """Evaluate preregistered predictions."""
    predictions={}
    lag1=lag_results.get(1,{})
    lag2=lag_results.get(2,{})
    lag4=lag_results.get(4,{})
    lag8=lag_results.get(8,{})
    # P1: lag 1-4 negative and significant
    p1_significant=lag1.get('significant',False) and lag1.get('autocorr',0)<0
    p1_lags_ok=all(lag_results.get(l,{}).get('autocorr',0)<0 for l in [1,2,4] if l in lag_results)
    predictions['P1_lag_negative']={'confirmed':p1_significant and p1_lags_ok,
                                     'lag1':lag1.get('autocorr'), 'lag2':lag2.get('autocorr'),
                                     'lag4':lag4.get('autocorr')}
    # P2: decay with lag
    ac1=abs(lag1.get('autocorr',0));ac2=abs(lag2.get('autocorr',0));ac4=abs(lag4.get('autocorr',0))
    p2_decay=ac1>ac2 and ac2>ac4 if ac1>0 and ac2>0 else False
    # also check if higher lags are weaker
    predictions['P2_decay']={'confirmed':p2_decay,
                             'lag1_abs':round(ac1,4),'lag2_abs':round(ac2,4),'lag4_abs':round(ac4,4)}
    # P3: cross-asset replication (>1 asset with negative ac at lag 1)
    n_assets_negative=sum(1 for t,lr in cross_asset.items()
                         if lr.get(1,{}).get('autocorr',0)<0 and lr.get(1,{}).get('significant',False))
    predictions['P3_cross_asset']={'confirmed':n_assets_negative>=PREREG['replication_min_assets'],
                                    'n_assets':n_assets_negative,'total':len(cross_asset)}
    # P4: survives volatility control
    vc1=vol_control.get(1,{})
    p4_survives=vc1.get('autocorr_vol_controlled',0)<0 and vc1.get('significant',False)
    predictions['P4_vol_control']={'confirmed':p4_survives,
                                    'vc_ac':vc1.get('autocorr_vol_controlled'),
                                    'vc_t':vc1.get('t_stat')}
    return predictions

def theory_verdict(predictions):
    """Determine verdict from preregistered rules."""
    confirmed=sum(1 for p in predictions.values() if p['confirmed'])
    total=len(predictions)
    p1=predictions['P1_lag_negative']
    p3=predictions['P3_cross_asset']
    # REFUTED: lag 1-4 not all negative
    if not p1['confirmed']:
        return 'AUTOCORR_MECHANISM_REFUTED'
    # CONTEXTUAL: only on some assets
    if p3['confirmed'] and p3['n_assets']>=2:
        # check if all 4 predictions pass
        if confirmed>=3:
            return 'AUTOCORR_MECHANISM_SUPPORTED'
        else:
            return 'AUTOCORR_MECHANISM_CONTEXTUAL'
    if confirmed>=3 and p3['n_assets']>=2:
        return 'AUTOCORR_MECHANISM_SUPPORTED'
    if confirmed>=2:
        return 'AUTOCORR_MECHANISM_CONTEXTUAL'
    if all(not p['confirmed'] for p in predictions.values()):
        return 'AUTOCORR_MECHANISM_REFUTED'
    return 'AUTOCORR_MECHANISM_INCONCLUSIVE'

# ================================================== BASELINE COMPARISON ===
def baseline_comparison(lag_results,decay,n):
    """Compare with NO_SERIAL_STRUCTURE baseline."""
    # baseline: for IID noise, expected autocorr ≈ 0 with se ≈ 1/sqrt(n)
    se=1/math.sqrt(max(n-3,1))
    # how many lags deviate significantly from 0?
    significant_deviations=0
    for lag,lr in lag_results.items():
        if abs(lr.get('t_stat',0))>1.0:significant_deviations+=1
    # baseline expects ~0 significant deviations out of 4 lags (by chance ~1)
    baseline_expected_significant=0.1*len(lag_results)  # 10% false positive rate
    explanation_strength=significant_deviations/max(baseline_expected_significant,0.01)
    return {'significant_deviations':significant_deviations,
            'baseline_expected':round(baseline_expected_significant,2),
            'explanation_strength':round(explanation_strength,2),
            'outperforms_baseline':explanation_strength>2.0}

# ================================================== FULL RUN ===
def run18_full():
    # Preregister
    PREREG_PATH.write_text(json.dumps(PREREG,indent=1))
    # Load residuals
    residuals=load_residuals()
    # Core tests on IMOEX (primary finding from RUN-15)
    primary=residuals.get('IMOEX',{})
    if not primary:return {'error':'no IMOEX data'}
    resid=primary['resid']
    lag_results=lag_autocorr(resid,PREREG['lags_to_test'])
    decay=decay_profile(resid,max_lag=12)
    vol_control=volatility_control(resid,primary['ret'],PREREG['lags_to_test'])
    cross_asset=cross_asset_replication(residuals,PREREG['lags_to_test'])
    # Evaluate predictions
    predictions=evaluate_predictions(lag_results,decay,vol_control,cross_asset)
    verdict=theory_verdict(predictions)
    # Baseline comparison
    bl=baseline_comparison(lag_results,decay,primary['n'])
    # MechanismRegistry update
    registry_path=OUT.parent/'run17/mechanism_registry.json'
    try:
        registry=json.loads(registry_path.read_text())
    except:registry={}
    registry['autocorrelation_structure']={
        'class':'temporal','question_types':['STRUCTURED_RESIDUAL'],
        'required_info':['return_series'],'status':verdict.replace('AUTOCORR_MECHANISM_',''),
        'tested_contexts':list(cross_asset.keys()),
        'refuted_contexts':[] if verdict!='AUTOCORR_MECHANISM_REFUTED' else list(cross_asset.keys()),
        'evidence':predictions,'verdict':verdict,'tested_at':now()}
    (OUT.parent/'run17/mechanism_registry_updated.json').write_text(json.dumps(registry,indent=1))
    # Assemble
    result={'schema':SCHEMA,'preregistration':PREREG,
            'primary_asset':'IMOEX','lag_results':lag_results,'decay_profile':decay,
            'volatility_control':vol_control,'cross_asset_replication':cross_asset,
            'predictions':predictions,'verdict':verdict,'baseline_comparison':bl,
            'n_samples':{t:r['n'] for t,r in residuals.items()},
            'registry_updated':verdict.replace('AUTOCORR_MECHANISM_',''),
            'ts':now()}
    (OUT/'run18_full.json').write_text(json.dumps(result,ensure_ascii=False,indent=1,default=str))
    return result

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--out',required=True);a=p.parse_args()
    r_=run18_full()
    Path(a.out).write_text(json.dumps(r_,ensure_ascii=False,indent=1,default=str))
    print('VERDICT:',r_['verdict'])
    for k,v in r_['predictions'].items():print(' ',k,'CONFIRMED' if v['confirmed'] else 'REFUTED',v)
    print('baseline:',r_['baseline_comparison'])
