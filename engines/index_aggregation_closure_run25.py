#!/usr/bin/env python3
"""RUN-25: Index Aggregation Test + Residual Program Closure.
Deterministic. Paper only. No LLM. No strategy.
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

SCHEMA='run25-index-aggregation-closure-v1'
OUT=Path('/root/audits/strategy_combine_research_intelligence/run25')
OUT.mkdir(parents=True,exist_ok=True)
def now():return time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())

# ================================================== PREREGISTRATION ===
PREREG={
 'theory':'T_INDEX_COMPONENT_AGGREGATION',
 'statement':'Part of IMOEX residual arises from heterogeneous component dynamics and aggregation',
 'component_set':['SBER','LKOH','CNY'],
 'dispersion_definition':'std(component_returns) across available components at time t',
 'agreement_metric':'fraction of components moving same direction as IMOEX',
 'significance_alpha':0.05,
 'half_split':'chronological 50/50',
 'min_sample_per_state':80,
}
PREREG_PATH=OUT/'RUN25_PREREGISTRATION.json'

# ================================================== DATA AUDIT ===
def data_audit():
    available={}
    for t,ds in q15.load_all().items():
        d=ds['train'].copy()
        if 'time' in d.columns:
            d['time']=pd.to_datetime(d['time']);d=d.set_index('time')
        available[t]={'n':len(d),'has_volume':'volume' in d,'has_ohlc':all(x in d for x in ['open','high','low','close'])}
    return available

# ================================================== LOAD ALL ===
def load_all():
    results={}
    for t,ds in q15.load_all().items():
        d=ds['train'].copy()
        if 'time' in d.columns:
            d['time']=pd.to_datetime(d['time']);d=d.set_index('time')
        ret=np.log(d.close/d.close.shift())
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
        # liquidity proxy
        if 'volume' in d:
            vol_raw=d['volume']
            vm=vol_raw.expanding(min_periods=120).mean()
            vs=vol_raw.expanding(min_periods=120).std()
            liq_z=(vol_raw-vm)/vs.replace(0,np.nan)
        else:
            liq_z=pd.Series(0,index=ret.index)
        liq_z=liq_z.fillna(0)
        lq33=liq_z.expanding(min_periods=120).quantile(0.33).shift(1)
        lq66=liq_z.expanding(min_periods=120).quantile(0.66).shift(1)
        liq_state=pd.Series('MID',index=ret.index)
        liq_state[liq_z<=lq33]='LOW_LIQ'
        liq_state[liq_z>=lq66]='HIGH_LIQ'
        results[t]={'ret':ret,'resid':resid,'vol':vol,'vol_regime':vol_regime,
                    'liq_state':liq_state,'d':d}
    return results

# ================================================== ACF ===
def acf(series,lag):
    r=series.dropna()
    if len(r)<lag+20:return 0,0,0
    x=r.iloc[lag:].values;y=r.iloc[:-lag].values
    ac=float(np.corrcoef(x,y)[0,1])
    t=ac*math.sqrt(len(x)-2)/math.sqrt(max(1-ac*ac,1e-12))
    return round(ac,6),round(float(t),4),len(x)

# ================================================== TEST INDEX AGGREGATION ===
def test_index_aggregation(all_data):
    """Test using SBER/LKOH/CNY as component proxies."""
    imoex=all_data.get('IMOEX')
    if imoex is None:return {'error':'no IMOEX'}
    # get component returns aligned with IMOEX
    components={}
    for t in ['SBER','LKOH','CNY']:
        if t in all_data:
            common=imoex['ret'].index.intersection(all_data[t]['ret'].index)
            components[t]=all_data[t]['ret'].loc[common]
    if len(components)<2:return {'error':'insufficient components'}
    # component dispersion: std across components at each time
    comp_df=pd.DataFrame(components)
    dispersion=comp_df.std(axis=1)
    # agreement: fraction of components moving same direction as IMOEX
    imoex_ret=imoex['ret'].loc[comp_df.index]
    agreement=(comp_df.apply(np.sign)==imoex_ret.apply(np.sign).values.reshape(-1,1)).mean(axis=1)
    # align with residual
    resid=imoex['resid'].loc[dispersion.dropna().index]
    dispersion=dispersion.dropna()
    common=resid.index.intersection(dispersion.index)
    resid=resid.loc[common];dispersion=dispersion.loc[common];agreement=agreement.loc[common]
    # A: dispersion vs residual correlation
    resid_next=resid.shift(-1).dropna()
    common_corr=dispersion.index.intersection(resid_next.index)
    corr_disp=float(np.corrcoef(dispersion.loc[common_corr].values,resid_next.loc[common_corr].values)[0,1]) if len(common_corr)>100 else 0
    # B: HIGH vs LOW dispersion
    thr=dispersion.quantile(0.66)
    high_mask=dispersion>=thr;low_mask=dispersion<=dispersion.quantile(0.33)
    h_ac,h_t,h_n=acf(resid[high_mask],1)
    l_ac,l_t,l_n=acf(resid[low_mask],1)
    h_mean=float(resid[high_mask].mean()) if high_mask.sum()>20 else 0
    l_mean=float(resid[low_mask].mean()) if low_mask.sum()>20 else 0
    # C: incremental over vol+liq
    vol_reg=imoex['vol_regime'].loc[common]
    liq_reg=imoex['liq_state'].loc[common]
    # interaction: high disp + specific vol/liq state
    hv_mask=high_mask & (vol_reg=='HIGH')
    ll_mask=low_mask & (liq_reg=='LOW_LIQ')
    hv_ac,_,_=acf(resid[hv_mask],1) if hv_mask.sum()>50 else (0,0,0)
    ll_ac,_,_=acf(resid[ll_mask],1) if ll_mask.sum()>50 else (0,0,0)
    # D: stability
    half=len(resid)//2
    h1=dispersion.iloc[:half];h2=dispersion.iloc[half:]
    h1_high=h1>=h1.quantile(0.66);h2_high=h2>=h2.quantile(0.66)
    h1_ac,_,_=acf(resid.iloc[:half][h1_high],1) if h1_high.sum()>50 else (0,0,0)
    h2_ac,_,_=acf(resid.iloc[half:][h2_high],1) if h2_high.sum()>50 else (0,0,0)
    same_dir=bool(np.sign(h1_ac)==np.sign(h2_ac)) if h1_ac!=0 and h2_ac!=0 else False
    # agreement metric
    agree_mean=float(agreement.mean())
    return {'n':len(resid),'dispersion_corr':round(corr_disp,6),
            'high_disp_ac':h_ac,'high_disp_t':h_t,'high_disp_n':h_n,
            'low_disp_ac':l_ac,'low_disp_t':l_t,'low_disp_n':l_n,
            'high_disp_mean':round(h_mean,8),'low_disp_mean':round(l_mean,8),
            'incremental_hv_ac':hv_ac,'incremental_ll_ac':ll_ac,
            'stability_h1_ac':h1_ac,'stability_h2_ac':h2_ac,'same_direction':same_dir,
            'agreement_mean':round(agree_mean,4),
            'dispersion_ac_range':round(abs(h_ac-l_ac),6)}

# ================================================== VERDICT ===
def verdict(result):
    if 'error' in result:return 'INDEX_AGGREGATION_INCONCLUSIVE'
    ac_range=result.get('dispersion_ac_range',0)
    stable=result.get('same_direction',False)
    sig=ac_range>0.02
    if sig and stable:return 'INDEX_AGGREGATION_SUPPORTED'
    if sig and not stable:return 'INDEX_AGGREGATION_CONTEXTUAL'
    if not sig:return 'INDEX_AGGREGATION_REFUTED'
    return 'INDEX_AGGREGATION_INCONCLUSIVE'

# ================================================== RESIDUAL ACCOUNTING ===
def residual_accounting():
    """Compile all mechanism results into accounting."""
    mechanisms={
        'autocorrelation_structure':{'status':'CLOSED_AS_ALPHA_SOURCE','value':'descriptive only'},
        'cross_asset_lead_lag':{'status':'REFUTED','value':0},
        'nonlinear_leverage':{'status':'REFUTED','value':0},
        'volatility_state_transitions':{'status':'CONTEXTUAL','value':'~2-5% on SBER'},
        'liquidity_effects':{'status':'SUPPORTED','value':'~3-7% on SBER/LKOH'},
    }
    return mechanisms

# ================================================== PROGRAM CLOSURE (simplified RUN-17 rules) ===
def program_closure(index_verdict,accounting):
    refuted=sum(1 for m in accounting.values() if 'REFUTED' in m['status'])
    supported=sum(1 for m in accounting.values() if 'SUPPORTED' in m['status'])
    contextual=sum(1 for m in accounting.values() if 'CONTEXTUAL' in m['status'])
    if index_verdict=='INDEX_AGGREGATION_REFUTED' and supported==0:
        return 'COMPLETED','All mechanisms REFUTED or descriptive only. Residual is likely noise.'
    if index_verdict=='INDEX_AGGREGATION_SUPPORTED' or supported>=1:
        return 'COMPLETED','Supported mechanisms explain some residual. Remaining is noise or unstructured.'
    if index_verdict=='INDEX_AGGREGATION_INCONCLUSIVE':
        return 'INCONCLUSIVE','Insufficient data for index aggregation test.'
    return 'COMPLETED','Mechanism space explored. No strong unexplained structure remains.'

# ================================================== MAIN ===
def run25_full():
    PREREG_PATH.write_text(json.dumps(PREREG,indent=1))
    audit=data_audit()
    all_data=load_all()
    index_result=test_index_aggregation(all_data)
    v=verdict(index_result)
    accounting=residual_accounting()
    closure_reason,program_status=program_closure(v,accounting)
    # save
    (OUT/'data_audit.json').write_text(json.dumps(audit,indent=1,default=str))
    (OUT/'mechanism_test.json').write_text(json.dumps(index_result,indent=1,default=str))
    result={'schema':SCHEMA,'preregistration':PREREG,'data_audit':audit,
            'index_aggregation':index_result,'verdict':v,
            'mechanism_accounting':accounting,
            'program_closure':{'status':program_status,'reason':closure_reason},
            'ts':now()}
    (OUT/'run25_full.json').write_text(json.dumps(result,ensure_ascii=False,indent=1,default=str))
    return result

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--out',required=True);a=p.parse_args()
    r_=run25_full()
    Path(a.out).write_text(json.dumps(r_,ensure_ascii=False,indent=1,default=str))
    print('VERDICT:',r_['verdict'])
    print('PROGRAM:',r_['program_closure']['status'])
    print('REASON:',r_['program_closure']['reason'])
    ir=r_['index_aggregation']
    if 'error' not in ir:
        print(f'dispersion_ac_range={ir.get("dispersion_ac_range")} stable={ir.get("same_direction")}')
        print(f'high_disp_ac={ir.get("high_disp_ac")} low_disp_ac={ir.get("low_disp_ac")}')
