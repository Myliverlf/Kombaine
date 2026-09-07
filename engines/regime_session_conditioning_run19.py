#!/usr/bin/env python3
"""RUN-19: Autocorrelation Regime & Session Conditioning.
Deterministic. Paper only. No LLM.

Distinguishes: H1 (regime), H2 (session), H3 (robust mechanism).
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

SCHEMA='run19-regime-session-conditioning-v1'
OUT=Path('/root/audits/strategy_combine_research_intelligence/run19')
OUT.mkdir(parents=True,exist_ok=True)
def now():return time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())
def sid(s,x):return s+'_'+hashlib.sha256(json.dumps(x,sort_keys=True,separators=(',',':')).encode()).hexdigest()[:16]

# ================================================== PREREGISTRATION ===
PREREG={
 'lags_tested':[1,2,3,4,5,6],
 'min_sample_per_group':100,
 'vol_terciles':'expanding median splits (33%/66%) causal',
 'trend_definition':'rolling 48h cumulative return sign',
 'trend_window':48,
 'subperiods':3,
 'session_hours':list(range(10,19)),  # MOEX 10:00-18:59
 'boundary_hours':[9,10,18,19],  # hours around session edges
 'vol_control_method':'regress resid on |ret|, take residual',
 'significance_alpha':0.05,
 'falsification':'If lag1/lag3 collapse after session demeaning → SESSION_EFFECT. If regime diff not significant → no regime conditioning.',
 'verdicts':['SESSION_EFFECT','REGIME_CONDITIONING','ROBUST_CONTEXTUAL_MECHANISM','WEAK_OR_ARTIFACT'],
}
PREREG_PATH=OUT/'RUN19_PREREGISTRATION.json'

# ================================================== LOAD RESIDUALS ===
def load_residuals():
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

# ================================================== AUTOCORR HELPER ===
def acf_lags(resid,lags):
    r=resid.dropna();n=len(r)
    out={}
    for lag in lags:
        if n-lag<PREREG['min_sample_per_group']:
            out[lag]={'ac':0,'t':0,'n':0,'sig':False};continue
        x=r.iloc[lag:].values;y=r.iloc[:-lag].values
        ac=float(np.corrcoef(x,y)[0,1])
        t=ac*math.sqrt(len(x)-2)/math.sqrt(max(1-ac*ac,1e-12))
        out[lag]={'ac':round(ac,6),'t':round(float(t),4),'n':len(x),'sig':abs(float(t))>1.96}
    return out

# ================================================== TEST A: SESSION STRUCTURE ===
def session_test(res):
    d=res['d'];ret=res['ret'];resid=res['resid']
    if 'time' not in d:return {}
    hour=d.time.dt.hour
    # raw acf
    raw=acf_lags(resid,PREREG['lags_tested'])
    # hour-of-session demeaning
    z=pd.DataFrame({'r':ret,'resid':resid,'hour':hour})
    hour_mean=z.groupby('hour')['resid'].transform(lambda s:s.shift(1).expanding().mean())
    resid_adj=resid-hour_mean
    adj=acf_lags(resid_adj,PREREG['lags_tested'])
    # boundary exclusion
    is_boundary=hour.isin(PREREG['boundary_hours'])
    resid_no_bound=resid[~is_boundary]
    no_bound=acf_lags(resid_no_bound,PREREG['lags_tested'])
    return {'raw':raw,'seasonally_adjusted':adj,'no_boundary':no_bound,
            'n_raw':len(resid),'n_adj':len(resid_adj.dropna()),
            'n_no_boundary':len(resid_no_bound.dropna())}

# ================================================== TEST B: VOLATILITY REGIMES ===
def vol_regime_test(res):
    ret=res['ret'];resid=res['resid']
    vol=ret.rolling(24).std()
    # expanding terciles (causal)
    q33=vol.expanding(min_periods=120).quantile(0.33).shift(1)
    q66=vol.expanding(min_periods=120).quantile(0.66).shift(1)
    regime=pd.Series('MID',index=ret.index)
    regime[vol<=q33]='LOW'
    regime[vol>=q66]='HIGH'
    results={}
    for r_name in ['LOW','MID','HIGH']:
        mask=regime==r_name
        r=resid[mask].dropna()
        if len(r)<PREREG['min_sample_per_group']:
            results[r_name]={'n':len(r),'insufficient':True};continue
        results[r_name]={'n':len(r),**acf_lags(r,PREREG['lags_tested'])}
    # interaction test: is lag1 ac different across regimes?
    acs={k:v.get(1,{}).get('ac',0) for k,v in results.items() if not v.get('insufficient')}
    diff=max(acs.values())-min(acs.values()) if len(acs)>=2 else 0
    results['interaction']={'ac_range':round(diff,6),'regime_acs':{k:round(v,6) for k,v in acs.items()}}
    return results

# ================================================== TEST C: TREND REGIMES ===
def trend_regime_test(res):
    ret=res['ret'];resid=res['resid']
    cum=ret.rolling(PREREG['trend_window']).sum()
    trend=pd.Series('FLAT',index=ret.index)
    trend[cum>0]='UP'
    trend[cum<0]='DOWN'
    results={}
    for t_name in ['UP','FLAT','DOWN']:
        mask=trend==t_name
        r=resid[mask].dropna()
        if len(r)<PREREG['min_sample_per_group']:
            results[t_name]={'n':len(r),'insufficient':True};continue
        results[t_name]={'n':len(r),**acf_lags(r,PREREG['lags_tested'])}
    return results

# ================================================== TEST D: VOL × TREND INTERACTION ===
def vol_trend_interaction(res):
    ret=res['ret'];resid=res['resid']
    vol=ret.rolling(24).std()
    q33=vol.expanding(min_periods=120).quantile(0.33).shift(1)
    q66=vol.expanding(min_periods=120).quantile(0.66).shift(1)
    vol_regime=pd.Series('MID',index=ret.index)
    vol_regime[vol<=q33]='LOW';vol_regime[vol>=q66]='HIGH'
    cum=ret.rolling(PREREG['trend_window']).sum()
    trend_regime=pd.Series('FLAT',index=ret.index)
    trend_regime[cum>0]='UP';trend_regime[cum<0]='DOWN'
    results={}
    for v in ['LOW','MID','HIGH']:
        for t in ['UP','FLAT','DOWN']:
            mask=(vol_regime==v)&(trend_regime==t)
            r=resid[mask].dropna()
            key=f'{v}_{t}'
            if len(r)<PREREG['min_sample_per_group']:
                results[key]={'n':len(r),'insufficient':True};continue
            results[key]={'n':len(r),**acf_lags(r,[1,3])}
    return results

# ================================================== TEST E: TEMPORAL STABILITY ===
def temporal_stability(res,residuals_all):
    """Split IMOEX into chronological thirds, test each."""
    d=res['d'];ret=res['ret'];resid=res['resid']
    n=len(resid)
    third=n//3
    splits={'first':resid.iloc[:third],'second':resid.iloc[third:2*third],'third':resid.iloc[2*third:]}
    results={}
    for name,r in splits.items():
        if len(r)<PREREG['min_sample_per_group']:
            results[name]={'n':len(r),'insufficient':True};continue
        results[name]={'n':len(r),**acf_lags(r,[1,3])}
    # also test all assets for context
    all_assets={}
    for t,r_ in residuals_all.items():
        all_assets[t]=acf_lags(r_['resid'],[1,3])
    results['all_assets']=all_assets
    return results

# ================================================== VERDICT ===
def verdict(session,vol_reg,trend_reg,vol_trend,temporal):
    """Preregistered verdict from all tests."""
    # Check session effect
    raw_l1=session.get('raw',{}).get(1,{}).get('ac',0)
    adj_l1=session.get('seasonally_adjusted',{}).get(1,{}).get('ac',0)
    session_collapse=abs(adj_l1)<abs(raw_l1)*0.5 if raw_l1!=0 else False
    raw_l3=session.get('raw',{}).get(3,{}).get('ac',0)
    adj_l3=session.get('seasonally_adjusted',{}).get(3,{}).get('ac',0)
    session_collapse_l3=abs(adj_l3)<abs(raw_l3)*0.5 if raw_l3!=0 else False
    if session_collapse and session_collapse_l3:
        return 'SESSION_EFFECT'
    # Check regime conditioning
    vol_acs=vol_reg.get('interaction',{}).get('regime_acs',{})
    vol_range=vol_reg.get('interaction',{}).get('ac_range',0)
    regime_diff_significant=vol_range>0.02  # meaningful difference
    # Check temporal stability
    stable_signs=[]
    for s in ['first','second','third']:
        sdata=temporal.get(s,{})
        if not sdata.get('insufficient'):
            l1=sdata.get(1,{}).get('ac',0)
            stable_signs.append(l1<0)
    temporally_stable=all(stable_signs) if len(stable_signs)==3 else False
    # Verdict
    if regime_diff_significant and not session_collapse and temporally_stable:
        return 'REGIME_CONDITIONING'
    if not session_collapse and temporally_stable and not regime_diff_significant:
        return 'ROBUST_CONTEXTUAL_MECHANISM'
    if session_collapse:
        return 'SESSION_EFFECT'
    return 'WEAK_OR_ARTIFACT'

# ================================================== MAIN ===
def run19_full():
    PREREG_PATH.write_text(json.dumps(PREREG,indent=1))
    residuals=load_residuals()
    imoex=residuals.get('IMOEX',{})
    if not imoex:return {'error':'no IMOEX data'}
    # Tests
    session=session_test(imoex)
    vol_reg=vol_regime_test(imoex)
    trend_reg=trend_regime_test(imoex)
    vol_trend=vol_trend_interaction(imoex)
    temporal=temporal_stability(imoex,residuals)
    # Verdict
    v=verdict(session,vol_reg,trend_reg,vol_trend,temporal)
    # Save individual outputs
    (OUT/'session_conditioning.json').write_text(json.dumps(session,indent=1,default=str))
    (OUT/'regime_conditioning.json').write_text(json.dumps(vol_reg,indent=1,default=str))
    (OUT/'temporal_stability.json').write_text(json.dumps(temporal,indent=1,default=str))
    # Assemble
    result={'schema':SCHEMA,'preregistration':PREREG,
            'session':session,'vol_regime':vol_reg,'trend_regime':trend_reg,
            'vol_trend_interaction':vol_trend,'temporal_stability':temporal,
            'verdict':v,'ts':now()}
    (OUT/'run19_full.json').write_text(json.dumps(result,ensure_ascii=False,indent=1,default=str))
    return result

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--out',required=True);a=p.parse_args()
    r_=run19_full()
    Path(a.out).write_text(json.dumps(r_,ensure_ascii=False,indent=1,default=str))
    print('VERDICT:',r_['verdict'])
    s=r_['session']
    print('session raw_l1:',s.get('raw',{}).get(1,{}).get('ac'),'adj_l1:',s.get('seasonally_adjusted',{}).get(1,{}).get('ac'))
    print('vol regimes:',r_['vol_regime'].get('interaction',{}))
    for sp in ['first','second','third']:
        td=r_['temporal_stability'].get(sp,{})
        if not td.get('insufficient'):print(f'  {sp}: l1={td.get(1,{}).get("ac")} l3={td.get(3,{}).get("ac")}')
