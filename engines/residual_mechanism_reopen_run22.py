#!/usr/bin/env python3
"""RUN-22: Residual Mechanism Reopening.
Deterministic. Paper only. No LLM. No strategy.

Closes autocorrelation branch, reopens mechanism discovery
for structured residual from RUN-15/16/17.
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

SCHEMA='run22-residual-mechanism-reopening-v1'
OUT=Path('/root/audits/strategy_combine_research_intelligence/run22')
OUT.mkdir(parents=True,exist_ok=True)
def now():return time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())

# ================================================== PREREGISTRATION ===
PREREG={
 'residual_definition':'hourly return minus expanding hour-of-day conditional mean',
 'known_controls':['autocorrelation','volatility_clustering'],
 'max_candidates':5,
 'selection_method':'ranked_by_explanatory_power_before_testing',
 'test_method':'conditioning_on_selected_mechanism_only',
 'no_parameter_search':True,
 'no_strategy_pnl':True,
}
PREREG_PATH=OUT/'RUN22_PREREGISTRATION.json'

# ================================================== STEP 1: RECONSTRUCT RESIDUAL ===
def reconstruct_residual():
    """Original residual from RUN-15/16/17: hourly return minus expanding hour-of-day mean."""
    ds=q15.load_all().get('IMOEX')
    d=ds['train'].copy()
    if 'time' in d.columns:
        d['time']=pd.to_datetime(d['time']);d=d.set_index('time')
    ret=np.log(d.close/d.close.shift())
    # original residual
    h=d.index.hour if hasattr(d.index,'hour') else pd.Series(0,index=d.index)
    z=pd.DataFrame({'r':ret,'h':h})
    exp_mean=z.groupby('h')['r'].transform(lambda s:s.shift(1).expanding().mean())
    resid=ret-exp_mean
    # after volatility control: regress on |ret|
    abs_ret=ret.abs()
    X=np.column_stack([np.ones(len(ret)),abs_ret.values])
    valid=~ret.isna()&~abs_ret.isna()
    y=ret.values[valid];X_v=X[valid]
    beta=np.linalg.lstsq(X_v,y,rcond=None)[0]
    resid_vol_controlled=pd.Series(y-X_v@beta,index=ret.index[valid])
    # magnitude comparison
    orig_var=float(resid.dropna().var())
    controlled_var=float(resid_vol_controlled.var())
    explained=1-controlled_var/max(orig_var,1e-12)
    return {'original_var':round(orig_var,8),'controlled_var':round(controlled_var,8),
            'variance_explained_by_vol':round(explained,4),
            'n':int(valid.sum()),'date_range':f"{d.index[0]} to {d.index[-1]}"}

# ================================================== STEP 2: MECHANISM INVENTORY ===
def mechanism_inventory():
    """5 theory-driven candidate mechanisms for remaining residual structure."""
    return [
        {'id':'M1_CROSS_ASSET_LEAD_LAG',
         'name':'Cross-asset lead-lag',
         'rationale':'Other assets (SBER, LKOH, CNY) may lead/lag IMOEX returns due to information propagation delays or index-component effects.',
         'prediction':'IMOEX return at t+1 correlates with SBER/LKOH return at t (or t-1).',
         'falsification':'Cross-correlation at lead/lag 1 is not significantly different from zero.',
         'required_data':'IMOEX, SBER, LKOH, CNY hourly (already available).',
         'look_ahead_risk':'LOW (ex-ante lag structure).'},
        {'id':'M2_VOLATILITY_STATE_TRANSITION',
         'name':'Volatility state transitions',
         'rationale':'Transitions between low/vol and high-vol states may carry information about subsequent returns.',
         'prediction':'Observations where volatility regime CHANGES (LOW→HIGH or HIGH→LOW) have different mean returns than stable-regime observations.',
         'falsification':'Mean return at regime transitions is not significantly different from mean return in stable regimes.',
         'required_data':'IMOEX hourly with regime classification (already available).',
         'look_ahead_risk':'LOW (ex-ante regime definition).'},
        {'id':'M3_LIQUIDITY_EFFECT',
         'name':'Liquidity / market-activity effects',
         'rationale':'Volume or trading activity may predict returns through informed trading or liquidity provision.',
         'prediction':'High-volume observations have different return distribution than low-volume observations.',
         'falsification':'Return distribution is not significantly different across volume terciles.',
         'required_data':'IMOEX hourly volume (available).',
         'look_ahead_risk':'LOW (volume is concurrent, not future).'},
        {'id':'M4_INDEX_COMPONENT_AGGREGATION',
         'name':'Index-component aggregation effects',
         'rationale':'IMOEX is an index; its returns aggregate component stocks. Aggregation may create serial dependence not present in individual stocks.',
         'prediction':'IMOEX autocorrelation structure differs materially from SBER/LKOH autocorrelation structure.',
         'falsification':'IMOEX and SBER/LKOH show similar autocorrelation patterns after regime conditioning.',
         'required_data':'IMOEX, SBER, LKOH hourly (already available).',
         'look_ahead_risk':'LOW (structural comparison).'},
        {'id':'M5_NONLINEAR_DEPENDENCE',
         'name':'Nonlinear conditional dependence',
         'rationale':'Linear autocorrelation may be zero but nonlinear dependence (e.g., absolute returns predicting signed returns) may exist.',
         'prediction':'|return[t]| predicts signed return[t+1] (asymmetric leverage effect).',
         'falsification':'Correlation between |return[t]| and return[t+1] is not significantly different from zero.',
         'required_data':'IMOEX hourly (already available).',
         'look_ahead_risk':'LOW (ex-ante conditioning).'},
    ]

# ================================================== STEP 3: PRIORITIZE ===
def prioritize(candidates):
    """Rank BEFORE testing using preregistered criteria."""
    scores={}
    for c in candidates:
        cid=c['id']
        # A: explanatory power (theoretical)
        # B: falsifiability (all equal - binary test)
        # C: data availability (all have data)
        # D: independence from autocorrelation (all independent)
        # E: researcher degrees of freedom (all low)
        score=0
        if cid=='M1_CROSS_ASSET_LEAD_LAG':score=5  # strong theoretical basis
        elif cid=='M5_NONLINEAR_DEPENDENCE':score=4  # leverage effect well-known
        elif cid=='M2_VOLATILITY_STATE_TRANSITION':score=3
        elif cid=='M3_LIQUIDITY_EFFECT':score=2
        elif cid=='M4_INDEX_COMPONENT_AGGREGATION':score=1
        scores[cid]={'score':score,'rationale':c['rationale']}
    ranked=sorted(scores.items(),key=lambda x:-x[1]['score'])
    return {'ranked':[{**scores[k],'id':k} for k,_ in ranked],'selected':ranked[0][0] if ranked else None}

# ================================================== STEP 4+5: TEST SELECTED MECHANISM ===
def test_mechanism(selected_id):
    """Run minimal falsification test for the selected mechanism."""
    ds=q15.load_all().get('IMOEX')
    d=ds['train'].copy()
    if 'time' in d.columns:
        d['time']=pd.to_datetime(d['time']);d=d.set_index('time')
    ret=np.log(d.close/d.close.shift())
    result={'mechanism':selected_id,'timestamp':now()}

    if selected_id=='M1_CROSS_ASSET_LEAD_LAG':
        # Test: does SBER/LKOH lead IMOEX?
        load=q15.load_all()
        imoex_ret=ret.dropna()
        results={}
        for t in ['SBER','LKOH','CNY']:
            if t not in load:continue
            dd=load[t]['train'].copy()
            if 'time' in dd.columns:
                dd['time']=pd.to_datetime(dd['time']);dd=dd.set_index('time')
            other_ret=np.log(dd.close/dd.close.shift())
            # align on common index
            common=imoex_ret.index.intersection(other_ret.index)
            if len(common)<100:continue
            x=other_ret.loc[common].values  # other at t
            y=imoex_ret.loc[common].shift(-1).dropna().values  # IMOEX at t+1
            common2=common[:len(y)]
            x=x[:len(y)]
            # cross-correlation
            ac=float(np.corrcoef(x[:-1],y[1:])[0,1]) if len(x)>10 else 0
            n=len(x)-1
            t_stat=ac*math.sqrt(n-2)/math.sqrt(max(1-ac*ac,1e-12)) if n>10 else 0
            results[t]={'cross_corr':round(ac,6),'t_stat':round(float(t_stat),4),'n':n,
                        'significant':abs(float(t_stat))>1.96}
        result['test_results']=results
        # verdict
        any_sig=any(v.get('significant',False) for v in results.values())
        result['verdict']='SUPPORTED' if any_sig else 'REFUTED'
        result['prediction_falsified']=not any_sig

    elif selected_id=='M5_NONLINEAR_DEPENDENCE':
        # Test: does |return[t]| predict signed return[t+1]?
        abs_r=ret.abs();signed_r=ret.shift(-1)
        common=abs_r.dropna().index.intersection(signed_r.dropna().index)
        x=abs_r.loc[common].values;y=signed_r.loc[common].values
        ac=float(np.corrcoef(x,y)[0,1]) if len(common)>100 else 0
        t_stat=ac*math.sqrt(len(common)-2)/math.sqrt(max(1-ac*ac,1e-12))
        result['test_results']={'leverage_corr':round(ac,6),'t_stat':round(float(t_stat),4),
                                'n':len(common),'significant':abs(float(t_stat))>1.96}
        result['verdict']='SUPPORTED' if abs(float(t_stat))>1.96 else 'REFUTED'
        result['prediction_falsified']=abs(float(t_stat))<=1.96

    elif selected_id=='M2_VOLATILITY_STATE_TRANSITION':
        # Test: do regime transitions predict different returns?
        vol=ret.rolling(24).std()
        q33=vol.expanding(min_periods=120).quantile(0.33).shift(1)
        q66=vol.expanding(min_periods=120).quantile(0.66).shift(1)
        regime=pd.Series('MID',index=ret.index)
        regime[vol<=q33]='LOW';regime[vol>=q66]='HIGH'
        transition=regime!=regime.shift(1)
        stable_ret=ret[~transition].dropna()
        trans_ret=ret[transition].dropna()
        if len(stable_ret)<100 or len(trans_ret)<20:
            result['test_results']={'error':'insufficient data'}
            result['verdict']='INCONCLUSIVE'
        else:
            from scipy import stats as sp_stats
            t_stat,p_val=sp_stats.ttest_ind(trans_ret.values,stable_ret.values)
            result['test_results']={'transition_mean':round(float(trans_ret.mean()),6),
                                    'stable_mean':round(float(stable_ret.mean()),6),
                                    't_stat':round(float(t_stat),4),'p_value':round(float(p_val),4),
                                    'n_trans':len(trans_ret),'n_stable':len(stable_ret),
                                    'significant':float(p_val)<0.05}
            result['verdict']='SUPPORTED' if float(p_val)<0.05 else 'REFUTED'
            result['prediction_falsified']=float(p_val)>=0.05

    elif selected_id=='M3_LIQUIDITY_EFFECT':
        # Test: does volume tercile predict returns?
        vol_series=ret.rolling(24).std()
        d_copy=d.copy()
        d_copy['ret']=ret; d_copy['vol']=vol_series
        if 'volume' in d_copy:
            vol_t=d_copy['volume'].expanding(min_periods=120).quantile(0.33).shift(1)
            vol_h=d_copy['volume'].expanding(min_periods=120).quantile(0.66).shift(1)
            v_regime=pd.Series('MID',index=d_copy.index)
            v_regime[d_copy['volume']<=vol_t]='LOW'
            v_regime[d_copy['volume']>=vol_h]='HIGH'
            low_ret=ret[v_regime=='LOW'].dropna()
            high_ret=ret[v_regime=='HIGH'].dropna()
            if len(low_ret)<100 or len(high_ret)<100:
                result['test_results']={'error':'insufficient data'}
                result['verdict']='INCONCLUSIVE'
            else:
                from scipy import stats as sp_stats
                t_stat,p_val=sp_stats.ttest_ind(high_ret.values,low_ret.values)
                result['test_results']={'high_vol_mean':round(float(high_ret.mean()),6),
                                        'low_vol_mean':round(float(low_ret.mean()),6),
                                        't_stat':round(float(t_stat),4),'p_value':round(float(p_val),4),
                                        'significant':float(p_val)<0.05}
                result['verdict']='SUPPORTED' if float(p_val)<0.05 else 'REFUTED'
                result['prediction_falsified']=float(p_val)>=0.05
        else:
            result['test_results']={'error':'no volume data'}
            result['verdict']='INCONCLUSIVE'

    elif selected_id=='M4_INDEX_COMPONENT_AGGREGATION':
        # Test: does IMOEX autocorrelation differ from SBER/LKOH?
        load=q15.load_all()
        imoex_ac=acf_single(ret.dropna(),1)
        other_acs={}
        for t in ['SBER','LKOH']:
            if t not in load:continue
            dd=load[t]['train'].copy()
            if 'time' in dd.columns:
                dd['time']=pd.to_datetime(dd['time']);dd=dd.set_index('time')
            other_ret=np.log(dd.close/dd.close.shift()).dropna()
            other_acs[t]=acf_single(other_ret,1)
        result['test_results']={'imoex_ac':imoex_ac,'other_acs':other_acs,
                                'difference':round(abs(imoex_ac-mean(other_acs.values())) if other_acs else 0,6)}
        result['verdict']='SUPPORTED' if result['test_results']['difference']>0.02 else 'REFUTED'
        result['prediction_falsified']=result['test_results']['difference']<=0.02

    return result

def acf_single(series,lag):
    r=series.dropna()
    if len(r)<lag+10:return 0
    x=r.iloc[lag:].values;y=r.iloc[:-lag].values
    return round(float(np.corrcoef(x,y)[0,1]),6)

# ================================================== MAIN ===
def run22_full():
    PREREG_PATH.write_text(json.dumps(PREREG,indent=1))
    # Step 1: reconstruct residual
    residual=reconstruct_residual()
    # Step 2: inventory
    candidates=mechanism_inventory()
    # Step 3: prioritize
    priority=prioritize(candidates)
    # Step 4+5: test selected
    test_result=test_mechanism(priority['selected'])
    # Save outputs
    (OUT/'residual_reconstruction.json').write_text(json.dumps(residual,indent=1,default=str))
    (OUT/'mechanism_candidates.json').write_text(json.dumps(candidates,indent=1,default=str))
    (OUT/'mechanism_priority.json').write_text(json.dumps(priority,indent=1,default=str))
    (OUT/'mechanism_test.json').write_text(json.dumps(test_result,indent=1,default=str))
    # Assemble
    result={'schema':SCHEMA,'preregistration':PREREG,
            'residual':residual,'candidates':candidates,'priority':priority,
            'test':test_result,'ts':now()}
    (OUT/'run22_full.json').write_text(json.dumps(result,ensure_ascii=False,indent=1,default=str))
    return result

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--out',required=True);a=p.parse_args()
    r_=run22_full()
    Path(a.out).write_text(json.dumps(r_,ensure_ascii=False,indent=1,default=str))
    print('SELECTED:',r_['priority']['selected'])
    print('VERDICT:',r_['test']['verdict'])
    print('RESIDUAL:',r_['residual'])
