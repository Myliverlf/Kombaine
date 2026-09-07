#!/usr/bin/env python3
"""RUN-28: Unit Consistency & Economic Meaning Audit.
Deterministic. No LLM. No strategy.

Traces every number to validate dimensional consistency.
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

SCHEMA='run28-unit-consistency-audit-v1'
OUT=Path('/root/audits/strategy_combine_research_intelligence/run28')
OUT.mkdir(parents=True,exist_ok=True)
def now():return time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())

# ================================================== NUMBER LINEAGE ===
def number_lineage():
    """Trace every number from RUN-26/27 to source."""
    return {
        '0.093':{
            'name':'SBER original_effect range',
            'source':'run27/microstructure_feasibility_run27.py → mediation_test()',
            'formula':'abs(low_ac - high_ac) where ac = acf(resid[liq_state==X], 1)',
            'input_variables':'resid (hourly return - hour-of-day mean), liq_state (volume z-score terciles)',
            'units':'AUTOCORRELATION COEFFICIENT (dimensionless, range -1 to +1)',
            'scale':'per-observation, no annualization',
            'interpretation':'Difference in lag-1 autocorrelation between LOW_LIQ and HIGH_LIQ states',
            'is':'autocorrelation_difference'},
        '0.005':{
            'name':'friction_floor',
            'source':'run27/microstructure_feasibility_run27.py → run27_full()',
            'formula':'avg_spread_bps / 10000 = 50 / 10000',
            'input_variables':'avg_spread_bps = 50 (assumed constant)',
            'units':'RETURN (decimal, per observation)',
            'scale':'per hourly observation',
            'interpretation':'50 basis points spread cost per round-trip',
            'is':'cost_assumption'},
        '-0.00037':{
            'name':'SBER net_after_cost',
            'source':'run26/liquidity_validation_run26.py → test_frozen()',
            'formula':'abs(low_mean - high_mean) - cost_per_trade',
            'input_variables':'low_mean = mean(resid[LOW_LIQ]), high_mean = mean(resid[HIGH_LIQ]), cost=0.0005',
            'units':'RETURN (decimal, per observation)',
            'scale':'per hourly observation',
            'interpretation':'Difference in mean next-return between liquidity states, minus 5bps cost',
            'is':'return_difference'},
        '-0.00050':{
            'name':'LKOH net_after_cost',
            'source':'run26/liquidity_validation_run26.py → test_frozen()',
            'formula':'abs(low_mean - high_mean) - cost_per_trade',
            'input_variables':'same as SBER',
            'units':'RETURN (decimal, per observation)',
            'scale':'per hourly observation',
            'interpretation':'Same as SBER but for LKOH',
            'is':'return_difference'},
    }

# ================================================== DIMENSION CHECK ===
def dimension_check():
    """Check if 0.093 / 0.005 is valid."""
    return {
        'numerator':{
            'value':0.093,
            'units':'autocorrelation_coefficient',
            'dimension':'dimensionless_correlation'},
        'denominator':{
            'value':0.005,
            'units':'return_decimal',
            'dimension':'return_per_observation'},
        'ratio':{
            'value':0.093/0.005,
            'valid':False,
            'reason':'Cannot divide autocorrelation by return. Different physical dimensions.',
            'verdict':'INVALID_UNIT_COMPARISON'},
        'correct_comparison':{
            'effect_in_return':-0.00037,
            'friction_in_return':0.005,
            'ratio':-0.00037/0.005,
            'verdict':'Effect is NEGATIVE after costs. Below friction floor.'},
    }

# ================================================== RECONSTRUCT ACTUAL EFFECT ===
def reconstruct_effect():
    """Calculate effect in return units using frozen specification."""
    results={}
    for t in ['SBER','LKOH']:
        ds=q15.load_all().get(t)
        if ds is None:continue
        d=ds['train'].copy()
        if 'time' in d.columns:
            d['time']=pd.to_datetime(d['time']);d=d.set_index('time')
        ret=np.log(d.close/d.close.shift())
        # residual (frozen)
        h=d.index.hour
        z=pd.DataFrame({'r':ret,'h':h})
        exp_mean=z.groupby('h')['r'].transform(lambda s:s.shift(1).expanding().mean())
        resid=ret-exp_mean
        # liquidity state (frozen)
        vol_raw=d['volume']
        vm=vol_raw.expanding(min_periods=120).mean()
        vs=vol_raw.expanding(min_periods=120).std()
        liq_z=(vol_raw-vm)/vs.replace(0,np.nan)
        lq33=liq_z.expanding(min_periods=120).quantile(0.33).shift(1)
        lq66=liq_z.expanding(min_periods=120).quantile(0.66).shift(1)
        liq_state=pd.Series('MID',index=d.index)
        liq_state[liq_z<=lq33]='LOW_LIQ'
        liq_state[liq_z>=lq66]='HIGH_LIQ'
        # next return
        ret_next=ret.shift(-1)
        # conditional means
        common=resid.dropna().index.intersection(ret_next.dropna().index)
        low_mask=liq_state.loc[common]=='LOW_LIQ'
        high_mask=liq_state.loc[common]=='HIGH_LIQ'
        low_ret=float(ret_next.loc[common][low_mask].mean()) if low_mask.sum()>20 else 0
        high_ret=float(ret_next.loc[common][high_mask].mean()) if high_mask.sum()>20 else 0
        low_se=float(ret_next.loc[common][low_mask].std()/np.sqrt(low_mask.sum())) if low_mask.sum()>20 else 0
        high_se=float(ret_next.loc[common][high_mask].std()/np.sqrt(high_mask.sum())) if high_mask.sum()>20 else 0
        diff=low_ret-high_ret
        diff_se=math.sqrt(low_se**2+high_se**2)
        t_stat=diff/diff_se if diff_se>0 else 0
        # Also compute AC difference (what RUN-27 called 0.093)
        def acf_local(series,lag):
            r=series.dropna()
            if len(r)<lag+20:return 0
            x=r.iloc[lag:].values;y=r.iloc[:-lag].values
            return float(np.corrcoef(x,y)[0,1])
        low_ac=acf_local(resid.loc[common][low_mask],1)
        high_ac=acf_local(resid.loc[common][high_mask],1)
        ac_diff=abs(low_ac-high_ac)
        results[t]={'low_mean_return':round(low_ret,8),'high_mean_return':round(high_ret,8),
                    'return_difference':round(diff,8),'se':round(diff_se,8),'t_stat':round(t_stat,4),
                    'n_low':int(low_mask.sum()),'n_high':int(high_mask.sum()),
                    'low_ac':round(low_ac,6),'high_ac':round(high_ac,6),'ac_difference':round(ac_diff,6)}
    return results

# ================================================== TRADE MAPPING ===
def trade_mapping():
    """Check if frozen spec has valid trade mapping."""
    # RUN-26 freeze: direction = LOW_LIQ has stronger negative AC
    # This means: LOW_LIQ → more negative next returns
    # Trade mapping: SHORT when LOW_LIQ? But the mean return difference is tiny.
    return {
        'signal':'LOW_LIQ state',
        'direction':'Theoretically: LOW_LIQ has more negative returns → SHORT signal',
        'horizon':'1 hour (next observation)',
        'valid_mapping':True,
        'but':'The mean return difference is negative (LOW_LIQ mean < HIGH_LIQ mean in some assets)',
        'net_after_costs':-0.00037,
        'conclusion':'Mapping exists but net effect is NEGATIVE after costs'}

# ================================================== RECONCILIATION ===
def reconcile_run26_run27():
    """Explain discrepancy between RUN-26 and RUN-27."""
    return {
        'run26_said':'ECONOMICALLY_NEGLIGIBLE (net after 5bps = -0.00037)',
        'run27_said':'19x above friction floor (effect=0.093, floor=0.005)',
        'root_cause':'INVALID_UNIT_COMPARISON',
        'explanation':'RUN-27 compared autocorrelation difference (0.093, dimensionless) with return-based friction floor (0.005, return units). These are different physical quantities.',
        'correct_comparison':'Effect in return units = -0.00037. Friction = 0.005. Ratio = -0.074 (NEGATIVE, below floor).',
        'which_is_correct':'RUN-26 is correct. RUN-27 contained a unit error.',
        'bug_severity':'CRITICAL — invalidates "19x above friction" claim',}

# ================================================== MAIN ===
def run28_full():
    lineage=number_lineage()
    dims=dimension_check()
    effect=reconstruct_effect()
    mapping=trade_mapping()
    reconciliation=reconcile_run26_run27()
    # Scientific verdict: Amihud mechanism stands (it's real, just not economically useful)
    # Economic verdict: depends on return-unit analysis
    sber_effect=effect.get('SBER',{})
    lkoh_effect=effect.get('LKOH',{})
    sber_net=sber_effect.get('return_difference',0)-0.0005  # 5bps cost
    lkoh_net=lkoh_effect.get('return_difference',0)-0.0005
    if sber_net>0 or lkoh_net>0:
        eco_verdict='ECONOMIC_EFFECT_COST_FRAGILE'
    else:
        eco_verdict='ECONOMIC_EFFECT_NEGLIGIBLE'
    result={'schema':SCHEMA,
            'number_lineage':lineage,
            'dimension_check':dims,
            'actual_effect':effect,
            'trade_mapping':mapping,
            'reconciliation':reconciliation,
            'scientific_verdict':'AMIHUD_MECHANISM_STANDS',
            'economic_verdict':eco_verdict,
            'program_decision':'CLOSE_AS_SCIENTIFIC_ONLY',
            'ts':now()}
    (OUT/'run28_full.json').write_text(json.dumps(result,ensure_ascii=False,indent=1,default=str))
    return result

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--out',required=True);a=p.parse_args()
    r_=run28_full()
    Path(a.out).write_text(json.dumps(r_,ensure_ascii=False,indent=1,default=str))
    print('SCIENTIFIC:',r_['scientific_verdict'])
    print('ECONOMIC:',r_['economic_verdict'])
    print('PROGRAM:',r_['program_decision'])
    dc=r_['dimension_check']
    print('19x claim valid:',dc['ratio']['valid'])
    print('Correct ratio:',dc['correct_comparison']['ratio'])
    for t,v in r_['actual_effect'].items():
        print(f'{t}: return_diff={v["return_difference"]}, ac_diff={v["ac_difference"]}, net={v["return_difference"]-0.0005}')
    print('RECONCILIATION:',r_['reconciliation']['root_cause'])
