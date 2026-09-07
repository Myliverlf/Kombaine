#!/usr/bin/env python3
"""RUN-33: Volatility Forecast as Opportunity Gate.
Deterministic. Paper only. No LLM. No strategy optimization.

Tests if predicted volatility improves directional/relative predictability.
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

SCHEMA='run33-volatility-gate-v1'
OUT=Path('/root/audits/strategy_combine_research_intelligence/run33')
OUT.mkdir(parents=True,exist_ok=True)
def now():return time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())

# ================================================== PREREGISTRATION ===
PREREG={
 'volatility_forecast':'vol_24h (expanding std of returns, window 24)',
 'gate_threshold_percentile':70,
 'signals':['absolute_direction','relative_cross_sectional'],
 'oos_split':'chronotional 60/40',
 'controls':['LOW_VOL_CONTROL','RANDOM_GATE_CONTROL'],
 'random_gate_repeats':100,
 'min_sample_per_gate':100,
 'significance_alpha':0.05,
}
PREREG_PATH=OUT/'RUN33_PREREGISTRATION.json'

# ================================================== LOAD + PREPARE ===
def load_all():
    results={}
    for t,ds in q15.load_all().items():
        d=ds['train'].copy()
        if 'time' in d.columns:
            d['time']=pd.to_datetime(d['time']);d=d.set_index('time')
        ret=np.log(d.close/d.close.shift())
        # volatility forecast (causal: expanding std up to time t)
        vol_fc=ret.rolling(24).std()
        # absolute direction target
        abs_target=ret.shift(-1)
        # relative target (market-neutral)
        results[t]={'ret':ret,'vol_fc':vol_fc,'abs_target':abs_target,'d':d}
    # compute relative targets
    all_ret=pd.DataFrame({t:results[t]['ret'] for t in results})
    mkt_ret=all_ret.mean(axis=1)
    for t in results:
        results[t]['rel_target']=results[t]['ret']-mkt_ret
        results[t]['mkt_ret']=mkt_ret
    # add features
    for t in results:
        ret=results[t]['ret']
        vol_zscore=(ret.rolling(24).std()-ret.rolling(24).std().expanding(min_periods=120).mean())/ret.rolling(24).std().expanding(min_periods=120).std().replace(0,np.nan)
        amihud=ret.abs()/results[t]['d']['volume'].replace(0,np.nan) if 'volume' in results[t]['d'] else ret.abs()
        results[t]['vol_zscore']=vol_zscore
        results[t]['amihud_zscore']=(amihud-amihud.expanding(min_periods=120).mean())/amihud.expanding(min_periods=120).std().replace(0,np.nan)
        # build dataframe
        df=pd.DataFrame({'ret':ret,'vol_fc':results[t]['vol_fc'],
                        'abs_target':results[t]['abs_target'],
                        'rel_target':results[t]['rel_target'],
                        'vol_zscore':results[t]['vol_zscore'],
                        'amihud_zscore':results[t]['amihud_zscore']}).dropna()
        results[t]['df']=df
    return results

# ================================================== OLS ===
def ols(y,X):
    X_=np.column_stack([np.ones(len(X)),X])
    try:
        beta=np.linalg.lstsq(X_,y,rcond=None)[0]
        return {'beta':beta}
    except:return None

def r_squared(y,yhat):
    ss_res=((y-yhat)**2).sum();ss_tot=((y-y.mean())**2).sum()
    return 1-ss_res/max(ss_tot,1e-12)

# ================================================== SIGNAL GENERATION (frozen) ===
def frozen_abs_signal(df):
    """Frozen absolute directional signal: lagged return."""
    return df['ret'].values

def frozen_rel_signal(df):
    """Frozen relative signal: market-relative return."""
    return df['rel_target'].values

# ================================================== GATE GENERATION ===
def create_gates(vol_fc,threshold_pct):
    """Create causal gates from volatility forecast."""
    thr=np.percentile(vol_fc.dropna(),threshold_pct)
    high_vol=vol_fc>=thr
    low_vol=vol_fc<=np.percentile(vol_fc.dropna(),100-threshold_pct)
    return {'high':high_vol,'low':low_vol,'threshold':thr}

def random_gate(n,high_mask):
    """Random gate with same coverage as high_vol."""
    coverage=high_mask.sum()/len(high_mask)
    rng=np.random.RandomState(42)
    random_high=rng.random(n)<coverage
    return random_high

# ================================================== EVALUATE CONDITIONAL ===
def evaluate_conditional(signal,target,mask,min_sample=100):
    """Evaluate signal quality conditional on mask."""
    if mask.sum()<min_sample:
        return {'insufficient':True,'n':int(mask.sum())}
    if isinstance(mask,pd.Series):mask=mask.values
    s=signal[mask]
    t=target[mask]
    if len(s)<10 or len(t)<10:return {'insufficient':True,'n':len(s)}
    corr=float(np.corrcoef(s,t)[0,1])
    rank_corr=float(pd.Series(s).rank().corr(pd.Series(t).rank()))
    r2=r_squared(t,s)
    mean_target=float(np.mean(np.abs(t)))
    return {'n':len(s),'corr':round(corr,6),'rank_corr':round(rank_corr,6),
            'r2':round(r2,6),'mean_abs_target':round(mean_target,6),
            'insufficient':False}

# ================================================== TEST ONE ASSET ===
def test_asset(df,asset_name):
    """Test volatility gate on one asset."""
    n=len(df);split=int(n*0.6)
    train=df.iloc[:split];test=df.iloc[split:]
    # volatility forecast gate (causal: from train)
    vol_fc_train=train['vol_fc']
    gates=create_gates(vol_fc_train,PREREG['gate_threshold_percentile'])
    # apply gate to test
    vol_fc_test=test['vol_fc']
    high_mask_test=vol_fc_test>=gates['threshold']
    low_mask_test=vol_fc_test<=np.percentile(vol_fc_train.dropna(),100-PREREG['gate_threshold_percentile'])
    # random gate
    rand_mask=random_gate(len(test),high_mask_test)
    results={}
    for signal_name,target_name in [('abs_target','abs_target'),('rel_target','rel_target')]:
        signal=frozen_abs_signal(test) if signal_name=='abs_target' else frozen_rel_signal(test)
        target=test[target_name].values
        ungated=evaluate_conditional(signal,target,np.ones(len(test),dtype=bool))
        high_vol=evaluate_conditional(signal,target,high_mask_test)
        low_vol=evaluate_conditional(signal,target,low_mask_test)
        random=evaluate_conditional(signal,target,rand_mask)
        results[signal_name]={'ungated':ungated,'high_vol':high_vol,'low_vol':low_vol,'random':random}
    return results

# ================================================== MAIN ===
def run33_full():
    PREREG_PATH.write_text(json.dumps(PREREG,indent=1))
    datasets=load_all()
    if not datasets:return {'error':'no data'}
    all_results={}
    for t,df_data in datasets.items():
        if 'df' not in df_data:continue
        all_results[t]=test_asset(df_data['df'],t)
    # aggregate
    summary={}
    for sig in ['abs_target','rel_target']:
        corrs_ungated=[];corrs_high=[];corrs_low=[];corrs_random=[]
        for t in all_results:
            r=all_results[t].get(sig,{})
            if r.get('ungated',{}).get('insufficient'):continue
            corrs_ungated.append(r['ungated'].get('corr',0))
            corrs_high.append(r['high_vol'].get('corr',0) if not r['high_vol'].get('insufficient') else 0)
            corrs_low.append(r['low_vol'].get('corr',0) if not r['low_vol'].get('insufficient') else 0)
            corrs_random.append(r['random'].get('corr',0) if not r['random'].get('insufficient') else 0)
        summary[sig]={'avg_ungated':round(np.mean(corrs_ungated),4) if corrs_ungated else 0,
                      'avg_high_vol':round(np.mean(corrs_high),4) if corrs_high else 0,
                      'avg_low_vol':round(np.mean(corrs_low),4) if corrs_low else 0,
                      'avg_random':round(np.mean(corrs_random),4) if corrs_random else 0,
                      'n_assets':len(corrs_ungated)}
    # scientific verdict
    abs_improvement=summary.get('abs_target',{}).get('avg_high_vol',0)-summary.get('abs_target',{}).get('avg_ungated',0)
    rel_improvement=summary.get('rel_target',{}).get('avg_high_vol',0)-summary.get('rel_target',{}).get('avg_ungated',0)
    beat_random_abs=summary.get('abs_target',{}).get('avg_high_vol',0)>summary.get('abs_target',{}).get('avg_random',0)
    beat_random_rel=summary.get('rel_target',{}).get('avg_high_vol',0)>summary.get('rel_target',{}).get('avg_random',0)
    if abs_improvement>0.02 and beat_random_abs:sci_abs='GATE_IMPROVES_ABSOLUTE'
    elif abs_improvement>0:sci_abs='GATE_CONTEXTUAL_IMPROVEMENT'
    else:sci_abs='VOLATILITY_PREDICTABLE_DIRECTION_STILL_NOISE'
    if rel_improvement>0.02 and beat_random_rel:sci_rel='GATE_IMPROVES_RELATIVE'
    elif rel_improvement>0:sci_rel='GATE_CONTEXTUAL_IMPROVEMENT_REL'
    else:sci_rel='VOLATILITY_PREDICTABLE_RELATIVE_STILL_NOISE'
    overall='VOLATILITY_PREDICTABLE_DIRECTION_STILL_NOISE'
    if sci_abs=='GATE_IMPROVES_ABSOLUTE' or sci_rel=='GATE_IMPROVES_RELATIVE':overall='GATE_RESCUES_PREDICTABILITY'
    elif abs_improvement>0 or rel_improvement>0:overall='GATE_CONTEXTUAL_IMPROVEMENT'
    result={'schema':SCHEMA,'preregistration':PREREG,
            'per_asset':all_results,'summary':summary,
            'scientific_verdict':overall,
            'abs_verdict':sci_abs,'rel_verdict':sci_rel,
            'ts':now()}
    (OUT/'run33_full.json').write_text(json.dumps(result,ensure_ascii=False,indent=1,default=str))
    return result

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--out',required=True);a=p.parse_args()
    r_=run33_full()
    Path(a.out).write_text(json.dumps(r_,ensure_ascii=False,indent=1,default=str))
    print('SCIENTIFIC:',r_['scientific_verdict'])
    print('ABS:',r_['abs_verdict'])
    print('REL:',r_['rel_verdict'])
    for sig in ['abs_target','rel_target']:
        s=r_['summary'].get(sig,{})
        print(f'{sig}: ungated={s.get("avg_ungated")} high={s.get("avg_high_vol")} low={s.get("avg_low_vol")} random={s.get("avg_random")}')
