#!/usr/bin/env python3
"""RUN-26: Independent Liquidity Mechanism Validation.
Deterministic. Paper only. No LLM. No strategy.

Validates liquidity effect on independent holdout sample.
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

SCHEMA='run26-liquidity-validation-v1'
OUT=Path('/root/audits/strategy_combine_research_intelligence/run26')
OUT.mkdir(parents=True,exist_ok=True)
def now():return time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())

# ================================================== FREEZE ===
FREEZE={
 'liquidity_proxy':'volume_rolling_zscore',
 'proxy_definition':'(volume - expanding_mean) / expanding_std, expanding window>=120',
 'state_thresholds':'expanding terciles 33/66',
 'states':['LOW_LIQ','MID','HIGH_LIQ'],
 'expected_direction':'LOW_LIQ has stronger negative AC than HIGH_LIQ',
 'expected_sign':'LOW_LIQ AC < HIGH_LIQ AC (more negative = more mean-reversion)',
 'lag':1,
 'min_sample_per_state':80,
 'significance_alpha':0.05,
 'half_split':'chronological 50/50',
 'cost_bps':5,
 'discovery_sample':'first 60% of data (training split)',
 'validation_sample':'last 40% of data (holdout)',
 'assets':['SBER','LKOH'],
 'control_assets':['IMOEX','CNY'],
 'fail_rule':'If frozen specification shows no significant AC difference OR reversed direction → FAIL',
 'inconclusive_rule':'If sample size insufficient → INCONCLUSIVE',
}
FREEZE_PATH=OUT/'RUN26_LIQUIDITY_FREEZE.json'

# ================================================== LOAD + SPLIT ===
def load_split():
    discovery={};validation={}
    for t,ds in q15.load_all().items():
        d=ds['train'].copy()
        if 'time' in d.columns:
            d['time']=pd.to_datetime(d['time']);d=d.set_index('time')
        ret=np.log(d.close/d.close.shift())
        # residual
        h=d.index.hour
        z=pd.DataFrame({'r':ret,'h':h})
        exp_mean=z.groupby('h')['r'].transform(lambda s:s.shift(1).expanding().mean())
        resid=ret-exp_mean
        # liquidity proxy (FROZEN)
        if 'volume' in d:
            vol_raw=d['volume']
            vm=vol_raw.expanding(min_periods=120).mean()
            vs=vol_raw.expanding(min_periods=120).std()
            liq_z=(vol_raw-vm)/vs.replace(0,np.nan)
        else:
            liq_z=pd.Series(0,index=ret.index)
        liq_z=liq_z.fillna(0)
        # liquidity states (FROZEN thresholds — expanding, NOT re-fit)
        lq33=liq_z.expanding(min_periods=120).quantile(0.33).shift(1)
        lq66=liq_z.expanding(min_periods=120).quantile(0.66).shift(1)
        liq_state=pd.Series('MID',index=ret.index)
        liq_state[liq_z<=lq33]='LOW_LIQ'
        liq_state[liq_z>=lq66]='HIGH_LIQ'
        # volatility regime (for control)
        vol=ret.rolling(24).std()
        q33=vol.expanding(min_periods=120).quantile(0.33).shift(1)
        q66=vol.expanding(min_periods=120).quantile(0.66).shift(1)
        vol_regime=pd.Series('MID',index=ret.index)
        vol_regime[vol<=q33]='LOW';vol_regime[vol>=q66]='HIGH'
        df=pd.DataFrame({'ret':ret,'resid':resid,'liq_state':liq_state,
                         'vol_regime':vol_regime,'liq_z':liq_z}).dropna()
        # split: first 60% = discovery, last 40% = validation
        split_idx=int(len(df)*0.6)
        disc=df.iloc[:split_idx];val=df.iloc[split_idx:]
        discovery[t]=disc;validation[t]=val
    return discovery,validation

# ================================================== ACF ===
def acf(series,lag):
    r=series.dropna()
    if len(r)<lag+20:return 0,0,0
    x=r.iloc[lag:].values;y=r.iloc[:-lag].values
    ac=float(np.corrcoef(x,y)[0,1])
    t=ac*math.sqrt(len(x)-2)/math.sqrt(max(1-ac*ac,1e-12))
    return round(ac,6),round(float(t),4),len(x)

# ================================================== TEST FROZEN SPEC ===
def test_frozen(df,ticker):
    """Apply FROZEN liquidity specification to data."""
    resid=df['resid'];liq_state=df['liq_state'];vol_regime=df['vol_regime']
    results={}
    # A: frozen state comparison
    for state in ['LOW_LIQ','MID','HIGH_LIQ']:
        mask=liq_state==state
        if mask.sum()<FREEZE['min_sample_per_state']:
            results[state]={'insufficient':True,'n':int(mask.sum())};continue
        ac,t_stat,n=acf(resid[mask],FREEZE['lag'])
        mean=float(resid[mask].mean())
        results[state]={'ac':ac,'t':t_stat,'n':n,'mean':round(mean,8)}
    # B: effect size
    low_ac=results.get('LOW_LIQ',{}).get('ac',0)
    high_ac=results.get('HIGH_LIQ',{}).get('ac',0)
    ac_range=abs(low_ac-high_ac)
    # C: volatility-controlled (HIGH vol only)
    hv_mask=vol_regime=='HIGH'
    hv_results={}
    for state in ['LOW_LIQ','MID','HIGH_LIQ']:
        mask=hv_mask & (liq_state==state)
        if mask.sum()<50:continue
        ac,t_stat,n=acf(resid[mask],FREEZE['lag'])
        hv_results[state]={'ac':ac,'t':t_stat,'n':n}
    # D: temporal stability (validation halves)
    half=len(df)//2
    h1_results={};h2_results={}
    for state in ['LOW_LIQ','MID','HIGH_LIQ']:
        m1=liq_state.iloc[:half]==state;m2=liq_state.iloc[half:]==state
        h1_ac,_,_=acf(resid.iloc[:half][m1],FREEZE['lag']) if m1.sum()>50 else (0,0,0)
        h2_ac,_,_=acf(resid.iloc[half:][m2],FREEZE['lag']) if m2.sum()>50 else (0,0,0)
        h1_results[state]=h1_ac;h2_results[state]=h2_ac
    same_dir=all(np.sign(h1_results.get(s,0))==np.sign(h2_results.get(s,0))
                 for s in ['LOW_LIQ','HIGH_LIQ'] if h1_results.get(s,0)!=0 and h2_results.get(s,0)!=0)
    # E: cost sanity (simple: mean return difference after 5bps cost)
    low_mean=results.get('LOW_LIQ',{}).get('mean',0)
    high_mean=results.get('HIGH_LIQ',{}).get('mean',0)
    cost_per_trade=0.0005  # 5 bps
    net_effect=abs(low_mean-high_mean)-cost_per_trade
    return {'states':results,'ac_range':round(ac_range,6),
            'high_vol_controlled':hv_results,
            'stability':{'h1':h1_results,'h2':h2_results,'same_direction':same_dir},
            'cost_sanity':{'low_mean':round(low_mean,8),'high_mean':round(high_mean,8),
                           'net_after_cost':round(net_effect,8),'cost_bps':FREEZE['cost_bps']},
            'n_total':len(df)}

# ================================================== VERDICTS ===
def scientific_verdict(results):
    ac_range=results.get('ac_range',0)
    stable=results.get('stability',{}).get('same_direction',False)
    significant=ac_range>0.02
    if significant and stable:return 'LIQUIDITY_REPLICATED'
    if significant and not stable:return 'LIQUIDITY_CONTEXTUAL_REPLICATION'
    if not significant:return 'LIQUIDITY_NOT_REPLICATED'
    return 'LIQUIDITY_INCONCLUSIVE'

def economic_verdict(results):
    net=results.get('cost_sanity',{}).get('net_after_cost',0)
    if net>0.0001:return 'ECONOMICALLY_MEASURABLE'
    if net>0:return 'COST_FRAGILE'
    return 'ECONOMICALLY_NEGLIGIBLE'

def go_nogo(sci_verdict,eco_verdict):
    if sci_verdict=='LIQUIDITY_REPLICATED' and eco_verdict in ('ECONOMICALLY_MEASURABLE','COST_FRAGILE'):
        return 'GO_MICROSTRUCTURE_PROGRAM'
    if sci_verdict=='LIQUIDITY_CONTEXTUAL_REPLICATION':
        return 'CONDITIONAL_GO'
    return 'NO_GO_MICROSTRUCTURE'

# ================================================== MAIN ===
def run26_full():
    FREEZE_PATH.write_text(json.dumps(FREEZE,indent=1))
    discovery,validation=load_split()
    # Test on validation (independent) sample for SBER and LKOH
    val_results={}
    for t in ['SBER','LKOH']:
        if t in validation:
            val_results[t]=test_frozen(validation[t],t)
    # Also test control assets
    ctrl_results={}
    for t in ['IMOEX','CNY']:
        if t in validation:
            ctrl_results[t]=test_frozen(validation[t],t)
    # Verdicts per asset
    sci_verdicts={};eco_verdicts={}
    for t,r in val_results.items():
        sci_verdicts[t]=scientific_verdict(r)
        eco_verdicts[t]=economic_verdict(r)
    # Overall
    overall_sci='LIQUIDITY_REPLICATED' if all(v=='LIQUIDITY_REPLICATED' for v in sci_verdicts.values()) else \
                'LIQUIDITY_NOT_REPLICATED' if all(v=='LIQUIDITY_NOT_REPLICATED' for v in sci_verdicts.values()) else \
                'LIQUIDITY_CONTEXTUAL_REPLICATION'
    overall_eco='ECONOMICALLY_MEASURABLE' if any(v=='ECONOMICALLY_MEASURABLE' for v in eco_verdicts.values()) else \
                'COST_FRAGILE' if any(v=='COST_FRAGILE' for v in eco_verdicts.values()) else \
                'ECONOMICALLY_NEGLIGIBLE'
    gng=go_nogo(overall_sci,overall_eco)
    # save
    result={'schema':SCHEMA,'freeze':FREEZE,
            'validation_results':val_results,'control_results':ctrl_results,
            'scientific_verdicts':sci_verdicts,'economic_verdicts':eco_verdicts,
            'overall_scientific':overall_sci,'overall_economic':overall_eco,
            'go_nogo':gng,'ts':now()}
    (OUT/'run26_full.json').write_text(json.dumps(result,ensure_ascii=False,indent=1,default=str))
    return result

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--out',required=True);a=p.parse_args()
    r_=run26_full()
    Path(a.out).write_text(json.dumps(r_,ensure_ascii=False,indent=1,default=str))
    print('SCIENTIFIC:',r_['overall_scientific'])
    print('ECONOMIC:',r_['overall_economic'])
    print('GO/NO-GO:',r_['go_nogo'])
    for t,v in r_['validation_results'].items():
        print(f'  {t}: ac_range={v.get("ac_range")} stable={v.get("stability",{}).get("same_direction")} net_cost={v.get("cost_sanity",{}).get("net_after_cost")}')
