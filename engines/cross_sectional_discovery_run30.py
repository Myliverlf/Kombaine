#!/usr/bin/env python3
"""RUN-30: Cross-Sectional / Relative Value Discovery.
Deterministic. Paper only. No LLM. No strategy optimization.

Tests relative return predictability across Russian equities.
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

SCHEMA='run30-cross-sectional-discovery-v1'
OUT=Path('/root/audits/strategy_combine_research_intelligence/run30')
OUT.mkdir(parents=True,exist_ok=True)
def now():return time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())

# ================================================== PREREGISTRATION ===
PREREG={
 'universe':['SBER','LKOH','GAZP'],
 'target_A':'market_relative_return = asset_ret - equal_weight_market_ret',
 'target_B':'cross_sectional_rank percentile',
 'neutralization':'equal_weight_market_return',
 'mechanisms':['M1_RELATIVE_MOMENTUM','M2_RELATIVE_REVERSION','M3_DISPERSION_CONDITIONING'],
 'horizon':'1h',
 'significance_alpha':0.05,
 'half_split':'chronological 50/50',
 'min_sample':200,
}
PREREG_PATH=OUT/'RUN30_PREREGISTRATION.json'

# ================================================== DATA AUDIT + LOAD ===
def load_universe():
    datasets={}
    for t in PREREG['universe']:
        ds=q15.load_all().get(t)
        if ds is None:continue
        d=ds['train'].copy()
        if 'time' in d.columns:
            d['time']=pd.to_datetime(d['time']);d=d.set_index('time')
        ret=np.log(d.close/d.close.shift())
        datasets[t]={'ret':ret,'d':d}
    # align on common index
    common_idx=None
    for t,ds in datasets.items():
        if common_idx is None:common_idx=ds['ret'].dropna().index
        else:common_idx=common_idx.intersection(ds['ret'].dropna().index)
    if common_idx is None or len(common_idx)<PREREG['min_sample']:
        return {}
    aligned={}
    for t,ds in datasets.items():
        aligned[t]={'ret':ds['ret'].loc[common_idx],'d':ds['d'].loc[common_idx]}
    return aligned

# ================================================== RELATIVE TARGETS ===
def compute_relative(universe):
    # equal-weight market return
    all_ret=pd.DataFrame({t:ds['ret'] for t,ds in universe.items()})
    mkt_ret=all_ret.mean(axis=1)
    results={}
    for t in universe:
        asset_ret=universe[t]['ret']
        # A: market-relative return
        rel_ret=asset_ret-mkt_ret
        # B: cross-sectional rank (percentile)
        rank=all_ret.rank(axis=1,pct=True)[t]
        results[t]={'ret':universe[t]['ret'],'rel_ret':rel_ret,'rank':rank,'mkt_ret':mkt_ret}
    return results,mkt_ret

# ================================================== OLS ===
def ols(y,X):
    X_=np.column_stack([np.ones(len(X)),X])
    try:
        beta=np.linalg.lstsq(X_,y,rcond=None)[0]
        resid=y-X_@beta
        return {'beta':beta,'resid':resid}
    except:return None

def r_squared(y,yhat):
    ss_res=((y-yhat)**2).sum();ss_tot=((y-y.mean())**2).sum()
    return 1-ss_res/max(ss_tot,1e-12)

# ================================================== TEST MECHANISM ===
def test_mechanism(rel_data,universe,mkt_ret,mechanism):
    """Test one mechanism across all assets."""
    n_assets=len(rel_data)
    all_ret=pd.DataFrame({t:rel_data[t]['ret'] for t in rel_data})
    all_rel=pd.DataFrame({t:rel_data[t]['rel_ret'] for t in rel_data})
    results={}
    for t in rel_data:
        ret=rel_data[t]['ret'].values
        rel=rel_data[t]['rel_ret'].values
        mkt=mkt_ret.values
        n=len(ret)
        split=int(n*0.6)
        if mechanism=='M1_RELATIVE_MOMENTUM':
            # feature: lagged relative return at t-1, target: relative return at t
            feat=rel[:-1].reshape(-1,1)  # t-1
            y=rel[1:]  # t
            feat_train=feat[:split-1];y_train=y[:split-1]
            feat_test=feat[split-1:];y_test=y[split-1:]
        elif mechanism=='M2_RELATIVE_REVERSION':
            feat=(-rel[:-1]).reshape(-1,1)
            y=rel[1:]
            feat_train=feat[:split-1];y_train=y[:split-1]
            feat_test=feat[split-1:];y_test=y[split-1:]
        elif mechanism=='M3_DISPERSION_CONDITIONING':
            cs_disp=all_rel.std(axis=1).values
            feat=np.column_stack([rel[:-1].reshape(-1,1),cs_disp[:-1].reshape(-1,1)])
            y=rel[1:]
            feat_train=feat[:split-1];y_train=y[:split-1]
            feat_test=feat[split-1:];y_test=y[split-1:]
        else:return {}
        if len(feat_train)<50 or len(feat_test)<20:continue
        m=ols(y_train,feat_train)
        if m is None:continue
        yhat=m['beta'][0]+feat_test@m['beta'][1:]
        r2_oos=r_squared(y_test,yhat)
        corr_oos=float(np.corrcoef(y_test,yhat)[0,1]) if len(y_test)>1 else 0
        # rank correlation
        rank_corr=float(pd.Series(y_test).rank().corr(pd.Series(yhat).rank())) if len(y_test)>10 else 0
        # coefficient for relative return feature
        beta_rel=float(m['beta'][1]) if len(m['beta'])>1 else 0
        results[t]={'r2_oos':round(r2_oos,6),'corr_oos':round(corr_oos,6),
                    'rank_corr':round(rank_corr,6),'beta_rel':round(beta_rel,6),
                    'n_train':len(y_train),'n_test':len(y_test)}
    return results

# ================================================== ABSOLUTE BASELINE ===
def absolute_baseline(universe):
    """Test absolute return prediction as baseline."""
    results={}
    for t in universe:
        ret=universe[t]['ret'].values
        n=len(ret);split=int(n*0.6)
        feat=ret[:-1].reshape(-1,1)  # lag 1
        y=ret[1:]
        feat_train=feat[:split-1];y_train=y[:split-1]
        feat_test=feat[split-1:];y_test=y[split-1:]
        if len(feat_train)<50:continue
        m=ols(y_train,feat_train)
        if m is None:continue
        yhat=m['beta'][0]+feat_test@m['beta'][1:]
        r2_oos=r_squared(y_test,yhat)
        corr_oos=float(np.corrcoef(y_test,yhat)[0,1]) if len(y_test)>1 else 0
        results[t]={'r2_oos':round(r2_oos,6),'corr_oos':round(corr_oos,6),'n_test':len(y_test)}
    return results

# ================================================== ECONOMIC SANITY ===
def economic_sanity(rel_results,mechanism):
    """Simple market-neutral mapping."""
    betas={t:r.get('beta_rel',0) for t,r in rel_results.items()}
    if not betas:return {}
    # sort by beta: long strongest, short weakest
    sorted_assets=sorted(betas.items(),key=lambda x:-x[1])
    if len(sorted_assets)<2:return {}
    long_asset=sorted_assets[0][0]
    short_asset=sorted_assets[-1][0]
    long_corr=rel_results[long_asset].get('corr_oos',0)
    short_corr=rel_results[short_asset].get('corr_oos',0)
    avg_corr=(long_corr+short_corr)/2 if short_corr!=0 else long_corr
    return {'long_asset':long_asset,'short_asset':short_asset,
            'avg_oos_corr':round(avg_corr,6),
            'mapping':'long stronger, short weaker'}

# ================================================== VERDICT ===
def verdict(per_mechanism_results,absolute_results):
    mech_verdicts={}
    for mech,results in per_mechanism_results.items():
        corrs=[r.get('corr_oos',0) for r in results.values() if r]
        avg_corr=np.mean(corrs) if corrs else 0
        stable=all(np.sign(r.get('beta_rel',0))!=0 for r in results.values() if r)
        if avg_corr>0.05 and stable:mech_verdicts[mech]='SUPPORTED'
        elif avg_corr>0.02:mech_verdicts[mech]='CONTEXTUAL'
        elif avg_corr>-0.01:mech_verdicts[mech]='REFUTED'
        else:mech_verdicts[mech]='INCONCLUSIVE'
    # compare with absolute
    abs_corrs=[r.get('corr_oos',0) for r in absolute_results.values()]
    avg_abs_corr=np.mean(abs_corrs) if abs_corrs else 0
    all_mech_corrs=[r.get('corr_oos',0) for res in per_mechanism_results.values() for r in res.values()]
    avg_rel_corr=np.mean(all_mech_corrs) if all_mech_corrs else 0
    if avg_rel_corr>avg_abs_corr+0.02:overall='CROSS_SECTIONAL_ADVANTAGE'
    elif avg_rel_corr>avg_abs_corr:overall='PARTIAL_CROSS_SECTIONAL_ADVANTAGE'
    else:overall='NO_CROSS_SECTIONAL_ADVANTAGE'
    return mech_verdicts,overall,{'avg_relative_corr':round(avg_rel_corr,6),'avg_absolute_corr':round(avg_abs_corr,6)}

# ================================================== MAIN ===
def run30_full():
    PREREG_PATH.write_text(json.dumps(PREREG,indent=1))
    universe=load_universe()
    if not universe:return {'error':'insufficient universe'}
    rel_data,mkt_ret=compute_relative(universe)
    abs_baseline=absolute_baseline(universe)
    mech_results={}
    econ_results={}
    for mech in PREREG['mechanisms']:
        mech_results[mech]=test_mechanism(rel_data,universe,mkt_ret,mech)
        econ_results[mech]=economic_sanity(mech_results[mech],mech)
    mech_verdicts,overall,comparison=verdict(mech_results,abs_baseline)
    # save
    result={'schema':SCHEMA,'preregistration':PREREG,
            'universe':list(universe.keys()),
            'n_common':len(rel_data[list(rel_data.keys())[0]]['ret']) if rel_data else 0,
            'mechanism_results':mech_results,'absolute_baseline':abs_baseline,
            'mechanism_verdicts':mech_verdicts,'overall_verdict':overall,
            'comparison':comparison,'economic_sanity':econ_results,'ts':now()}
    (OUT/'run30_full.json').write_text(json.dumps(result,ensure_ascii=False,indent=1,default=str))
    return result

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--out',required=True);a=p.parse_args()
    r_=run30_full()
    Path(a.out).write_text(json.dumps(r_,ensure_ascii=False,indent=1,default=str))
    print('OVERALL:',r_['overall_verdict'])
    print('COMPARISON:',r_['comparison'])
    for mech,v in r_['mechanism_verdicts'].items():
        print(f'  {mech}: {v}')
        for t,r in r_['mechanism_results'].get(mech,{}).items():
            print(f'    {t}: corr={r.get("corr_oos")} r2={r.get("r2_oos")} beta={r.get("beta_rel")}')
