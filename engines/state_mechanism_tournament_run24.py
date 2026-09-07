#!/usr/bin/env python3
"""RUN-24: Residual State-Mechanism Tournament.
Deterministic. Paper only. No LLM. No strategy.

Tests volatility transitions AND liquidity effects on residual structure.
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

SCHEMA='run24-state-mechanism-tournament-v1'
OUT=Path('/root/audits/strategy_combine_research_intelligence/run24')
OUT.mkdir(parents=True,exist_ok=True)
def now():return time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())

# ================================================== PREREGISTRATION ===
PREREG={
 'vol_transitions':['LOW→MID','MID→HIGH','LOW→HIGH','HIGH→MID','MID→LOW','HIGH→LOW'],
 'post_transition_window':1,
 'min_sample_per_state':80,
 'liquidity_proxy':'volume_rolling_zscore',  # (vol - expanding_mean) / expanding_std
 'liquidity_quantiles':0.33,
 'significance_alpha':0.05,
 'half_split':'chronological 50/50',
}
PREREG_PATH=OUT/'RUN24_PREREGISTRATION.json'

# ================================================== LOAD + COMPUTE ===
def load_assets():
    results={}
    for t,ds in q15.load_all().items():
        d=ds['train'].copy()
        if 'time' in d.columns:
            d['time']=pd.to_datetime(d['time']);d=d.set_index('time')
        ret=np.log(d.close/d.close.shift())
        # residual: hour-of-day demeaned
        h=d.index.hour
        z=pd.DataFrame({'r':ret,'h':h})
        exp_mean=z.groupby('h')['r'].transform(lambda s:s.shift(1).expanding().mean())
        resid=ret-exp_mean
        # volatility regime
        vol=ret.rolling(24).std()
        q33=vol.expanding(min_periods=120).quantile(0.33).shift(1)
        q66=vol.expanding(min_periods=120).quantile(0.66).shift(1)
        vol_regime=pd.Series('MID',index=ret.index)
        vol_regime[vol<=q33]='LOW';vol_regime[vol>=q66]='HIGH'
        # volatility transition
        vol_transition=vol_regime!=vol_regime.shift(1)
        trans_type=vol_regime.shift(0)+'→'+vol_regime.shift(-1).fillna('MID')
        # liquidity proxy: volume z-score
        if 'volume' in d:
            vol_raw=d['volume']
            vol_mean=vol_raw.expanding(min_periods=120).mean()
            vol_std=vol_raw.expanding(min_periods=120).std()
            liq_z=(vol_raw-vol_mean)/vol_std.replace(0,np.nan)
        else:
            liq_z=pd.Series(0,index=ret.index)
        liq_z=liq_z.fillna(0)
        # liquidity state
        liq_q33=liq_z.expanding(min_periods=120).quantile(0.33).shift(1)
        liq_q66=liq_z.expanding(min_periods=120).quantile(0.66).shift(1)
        liq_state=pd.Series('MID',index=ret.index)
        liq_state[liq_z<=liq_q33]='LOW_LIQ'
        liq_state[liq_z>=liq_q66]='HIGH_LIQ'
        df=pd.DataFrame({'ret':ret,'resid':resid,'vol':vol,'vol_regime':vol_regime,
                         'vol_transition':vol_transition,'trans_type':trans_type,
                         'liq_z':liq_z,'liq_state':liq_state,
                         'resid_lag1':resid.shift(1)}).dropna()
        if len(df)<300:continue
        results[t]=df
    return results

# ================================================== ACF HELPER ===
def acf(series,lag):
    r=series.dropna()
    if len(r)<lag+20:return 0,0,0
    x=r.iloc[lag:].values;y=r.iloc[:-lag].values
    ac=float(np.corrcoef(x,y)[0,1])
    se=1/math.sqrt(len(x))
    t=ac*math.sqrt(len(x)-2)/math.sqrt(max(1-ac*ac,1e-12))
    return round(ac,6),round(float(t),4),len(x)

# ================================================== TEST VOL TRANSITIONS ===
def test_vol_transitions(df):
    resid=df['resid'];resid_lag=df['resid_lag1']
    results={}
    # baseline: all data
    base_ac,base_t,base_n=acf(resid,1)
    results['baseline']={'ac':base_ac,'t':base_t,'n':base_n}
    # transition vs stable
    trans_mask=df['vol_transition'].fillna(False)
    stable_mask=~trans_mask
    trans_ac,trans_t,trans_n=acf(resid[trans_mask],1)
    stable_ac,stable_t,stable_n=acf(resid[stable_mask],1)
    results['transition_vs_stable']={'trans_ac':trans_ac,'trans_t':trans_t,'trans_n':trans_n,
                                     'stable_ac':stable_ac,'stable_t':stable_t,'stable_n':stable_n}
    # conditional mean
    trans_mean=float(resid[trans_mask].mean()) if trans_mask.sum()>20 else 0
    stable_mean=float(resid[stable_mask].mean()) if stable_mask.sum()>20 else 0
    results['conditional_mean']={'trans_mean':round(trans_mean,8),'stable_mean':round(stable_mean,8),
                                 'diff':round(trans_mean-stable_mean,8),'trans_n':int(trans_mask.sum())}
    # time-half stability
    n=len(df);half=n//2
    h1_mask=trans_mask.iloc[:half];h2_mask=trans_mask.iloc[half:]
    h1_ac,_,_=acf(resid.iloc[:half][h1_mask],1)
    h2_ac,_,_=acf(resid.iloc[half:][h2_mask],1)
    results['stability']={'h1_trans_ac':h1_ac,'h2_trans_ac':h2_ac,
                          'same_direction':bool(np.sign(h1_ac)==np.sign(h2_ac)) if h1_ac!=0 and h2_ac!=0 else False}
    return results

# ================================================== TEST LIQUIDITY ===
def test_liquidity(df):
    resid=df['resid'];resid_lag=df['resid_lag1']
    liq_state=df['liq_state']
    results={}
    # baseline
    base_ac,base_t,base_n=acf(resid,1)
    results['baseline']={'ac':base_ac,'t':base_t,'n':base_n}
    # per liquidity state
    for state in ['LOW_LIQ','MID','HIGH_LIQ']:
        mask=liq_state==state
        if mask.sum()<PREREG['min_sample_per_state']:
            results[state]={'insufficient':True,'n':int(mask.sum())};continue
        ac,t_stat,n=acf(resid[mask],1)
        mean=float(resid[mask].mean())
        results[state]={'ac':ac,'t':t_stat,'n':n,'mean':round(mean,8)}
    # volatility-controlled: within HIGH vol, compare liquidity states
    high_vol=df['vol_regime']=='HIGH'
    if high_vol.sum()>PREREG['min_sample_per_state']:
        hv_results={}
        for state in ['LOW_LIQ','MID','HIGH_LIQ']:
            mask=high_vol & (liq_state==state)
            if mask.sum()<50:continue
            ac,t_stat,n=acf(resid[mask],1)
            hv_results[state]={'ac':ac,'t':t_stat,'n':n}
        results['high_vol_controlled']=hv_results
    # stability
    n=len(df);half=n//2
    h1_results={};h2_results={}
    for state in ['LOW_LIQ','MID','HIGH_LIQ']:
        mask1=liq_state.iloc[:half]==state
        mask2=liq_state.iloc[half:]==state
        h1_ac,_,_=acf(resid.iloc[:half][mask1],1) if mask1.sum()>50 else (0,0,0)
        h2_ac,_,_=acf(resid.iloc[half:][mask2],1) if mask2.sum()>50 else (0,0,0)
        h1_results[state]=h1_ac;h2_results[state]=h2_ac
    results['stability']={'h1':h1_results,'h2':h2_results}
    return results

# ================================================== VERDICTS ===
def vol_verdict(results):
    tvs=results.get('transition_vs_stable',{})
    trans_ac=tvs.get('trans_ac',0);stable_ac=tvs.get('stable_ac',0)
    stability=results.get('stability',{})
    cm=results.get('conditional_mean',{})
    diff=cm.get('diff',0)
    # significant difference between transition and stable?
    significant=abs(trans_ac-stable_ac)>0.01 or abs(diff)>1e-6
    stable=stability.get('same_direction',False)
    if significant and stable and abs(trans_ac)>0.01:
        return 'VOL_TRANSITION_SUPPORTED'
    if significant and not stable:
        return 'VOL_TRANSITION_CONTEXTUAL'
    if not significant:
        return 'VOL_TRANSITION_REFUTED'
    return 'VOL_TRANSITION_INCONCLUSIVE'

def liq_verdict(results):
    states=['LOW_LIQ','MID','HIGH_LIQ']
    acs={s:results.get(s,{}).get('ac',0) for s in states if not results.get(s,{}).get('insufficient')}
    ac_range=max(acs.values())-min(acs.values()) if len(acs)>=2 else 0
    stability=results.get('stability',{})
    # check if highest-AC state is stable across halves
    h1=stability.get('h1',{});h2=stability.get('h2',{})
    best_state=max(acs,key=lambda s:abs(acs[s])) if acs else None
    h1_best=h1.get(best_state,0) if best_state else 0
    h2_best=h2.get(best_state,0) if best_state else 0
    stable_direction=bool(np.sign(h1_best)==np.sign(h2_best)) if h1_best!=0 and h2_best!=0 else False
    if ac_range>0.02 and stable_direction:
        return 'LIQUIDITY_SUPPORTED'
    if ac_range>0.02 and not stable_direction:
        return 'LIQUIDITY_CONTEXTUAL'
    if ac_range<=0.02:
        return 'LIQUIDITY_REFUTED'
    return 'LIQUIDITY_INCONCLUSIVE'

# ================================================== MAIN ===
def run24_full():
    PREREG_PATH.write_text(json.dumps(PREREG,indent=1))
    assets=load_assets()
    vol_results={};liq_results={};vol_verdicts={};liq_verdicts={};rankings={}
    for t,df in assets.items():
        vol_results[t]=test_vol_transitions(df)
        liq_results[t]=test_liquidity(df)
        vol_verdicts[t]=vol_verdict(vol_results[t])
        liq_verdicts[t]=liq_verdict(liq_results[t])
    # comparative ranking
    vol_supported=sum(1 for v in vol_verdicts.values() if 'SUPPORTED' in v)
    liq_supported=sum(1 for v in liq_verdicts.values() if 'SUPPORTED' in v)
    vol_contextual=sum(1 for v in vol_verdicts.values() if 'CONTEXTUAL' in v)
    liq_contextual=sum(1 for v in liq_verdicts.values() if 'CONTEXTUAL' in v)
    vol_refuted=sum(1 for v in vol_verdicts.values() if 'REFUTED' in v)
    liq_refuted=sum(1 for v in liq_verdicts.values() if 'REFUTED' in v)
    if vol_supported>liq_supported:ranking='VOLATILITY_BETTER'
    elif liq_supported>vol_supported:ranking='LIQUIDITY_BETTER'
    elif vol_refuted<liq_refuted:ranking='VOLATILITY_BETTER'
    elif liq_refuted<vol_refuted:ranking='LIQUIDITY_BETTER'
    else:ranking='BOTH_REFUTED' if vol_refuted>=2 and liq_refuted>=2 else 'INCONCLUSIVE'
    # save
    (OUT/'vol_transitions.json').write_text(json.dumps(vol_results,indent=1,default=str))
    (OUT/'liquidity_effects.json').write_text(json.dumps(liq_results,indent=1,default=str))
    result={'schema':SCHEMA,'preregistration':PREREG,
            'vol_transitions':vol_results,'liquidity':liq_results,
            'vol_verdicts':vol_verdicts,'liq_verdicts':liq_verdicts,
            'ranking':ranking,'ts':now()}
    (OUT/'run24_full.json').write_text(json.dumps(result,ensure_ascii=False,indent=1,default=str))
    return result

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--out',required=True);a=p.parse_args()
    r_=run24_full()
    Path(a.out).write_text(json.dumps(r_,ensure_ascii=False,indent=1,default=str))
    print('RANKING:',r_['ranking'])
    for t in r_['vol_verdicts']:
        print(f'  {t}: VOL={r_["vol_verdicts"][t]} LIQ={r_["liq_verdicts"][t]}')
