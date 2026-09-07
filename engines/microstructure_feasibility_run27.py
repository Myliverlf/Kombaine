#!/usr/bin/env python3
"""RUN-27: Microstructure Data Feasibility & Liquidity Mechanism Resolution.
Deterministic. Paper only. No LLM. No strategy.

Audits microstructure data availability and tests OHLCV-based proxies.
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

SCHEMA='run27-microstructure-feasibility-v1'
OUT=Path('/root/audits/strategy_combine_research_intelligence/run27')
OUT.mkdir(parents=True,exist_ok=True)
def now():return time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())

# ================================================== FREEZE ===
FREEZE={
 'from':'RUN26_LIQUIDITY_FREEZE',
 'liquidity_proxy':'volume_rolling_zscore',
 'direction':'LOW_LIQ has stronger negative AC than HIGH_LIQ',
 'assets':['SBER','LKOH'],
}
FREEZE_PATH=OUT/'RUN27_FREEZE.json'

# ================================================== DATA AUDIT ===
def data_audit():
    audit={}
    for t,ds in q15.load_all().items():
        d=ds['train']
        cols=list(d.columns)
        audit[t]={
            'columns':cols,
            'n':len(d),
            'has_bid':'bid' in cols,
            'has_ask':'ask' in cols,
            'has_spread':'spread' in cols,
            'has_order_book':any(x in cols for x in ['depth','bid_size','ask_size']),
            'has_trade_direction':any(x in cols for x in ['aggressor','direction','buy_vol','sell_vol']),
            'has_trade_count':any(x in cols for x in ['trades','trade_count','deal_count']),
            'microstructure_available':False,
            'ohlcv_only':True,
        }
    return audit

# ================================================== OHLCV-BASED PROXIES ===
def compute_proxies(df):
    """Extract microstructure proxies from OHLCV only."""
    d=df.copy()
    ret=np.log(d.close/d.close.shift())
    # spread proxy: (high-low)/close — wider spread = more illiquid
    d['spread_proxy']=(d.high-d.low)/d.close
    # Amihud illiquidity: |return| / volume
    d['amihud']=ret.abs()/d.volume.replace(0,np.nan)
    # volume concentration: volume / rolling mean
    vol_mean=d.volume.rolling(24).mean()
    d['vol_concentration']=d.volume/vol_mean.replace(0,np.nan)
    # Kyle's lambda proxy: |return| / sqrt(volume)
    d['kyle_lambda']=ret.abs()/np.sqrt(d.volume.replace(0,1))
    return d

# ================================================== ACF ===
def acf(series,lag):
    r=series.dropna()
    if len(r)<lag+20:return 0,0,0
    x=r.iloc[lag:].values;y=r.iloc[:-lag].values
    ac=float(np.corrcoef(x,y)[0,1])
    t=ac*math.sqrt(len(x)-2)/math.sqrt(max(1-ac*ac,1e-12))
    return round(ac,6),round(float(t),4),len(x)

# ================================================== MEDIATION TEST ===
def mediation_test(df,ticker):
    """Test if OHLCV proxies explain the liquidity finding."""
    ret=np.log(df.close/df.close.shift())
    # residual
    h=df.index.hour if hasattr(df.index,'hour') else pd.Series(0,index=df.index)
    z=pd.DataFrame({'r':ret,'h':h})
    exp_mean=z.groupby('h')['r'].transform(lambda s:s.shift(1).expanding().mean())
    resid=ret-exp_mean
    # original liquidity proxy
    vol_raw=df['volume']
    vm=vol_raw.expanding(min_periods=120).mean()
    vs=vol_raw.expanding(min_periods=120).std()
    liq_z=(vol_raw-vm)/vs.replace(0,np.nan)
    lq33=liq_z.expanding(min_periods=120).quantile(0.33).shift(1)
    lq66=liq_z.expanding(min_periods=120).quantile(0.66).shift(1)
    liq_state=pd.Series('MID',index=df.index)
    liq_state[liq_z<=lq33]='LOW_LIQ'
    liq_state[liq_z>=lq66]='HIGH_LIQ'
    # microstructure proxies
    proxies=compute_proxies(df)
    results={'ticker':ticker,'n':len(df)}
    # Original effect
    low_ac,_,_=acf(resid[liq_state=='LOW_LIQ'],1)
    high_ac,_,_=acf(resid[liq_state=='HIGH_LIQ'],1)
    results['original_effect']={'low_ac':low_ac,'high_ac':high_ac,'range':round(abs(low_ac-high_ac),6)}
    # Test each proxy as mediator
    for proxy_name in ['spread_proxy','amihud','vol_concentration','kyle_lambda']:
        proxy=proxies[proxy_name].dropna()
        common=resid.index.intersection(proxy.index)
        if len(common)<200:continue
        r=resid.loc[common];p=proxy.loc[common]
        # split by proxy terciles
        q33=p.quantile(0.33);q66=p.quantile(0.66)
        low_mask=p<=q33;high_mask=p>=q66
        low_ac,_,_=acf(r[low_mask],1)
        high_ac,_,_=acf(r[high_mask],1)
        low_mean=float(r[low_mask].mean()) if low_mask.sum()>20 else 0
        high_mean=float(r[high_mask].mean()) if high_mask.sum()>20 else 0
        results[proxy_name]={'low_ac':low_ac,'high_ac':high_ac,
                             'range':round(abs(low_ac-high_ac),6),
                             'low_mean':round(low_mean,8),'high_mean':round(high_mean,8),
                             'n_low':int(low_mask.sum()),'n_high':int(high_mask.sum())}
    # Mediation: does controlling for best proxy reduce original effect?
    best_proxy='amihud'  # most likely mediator
    proxy=proxies[best_proxy].dropna()
    common=resid.index.intersection(proxy.index)
    r=resid.loc[common];p=proxy.loc[common]
    # regression: resid ~ liq_state + proxy
    liq_num=(liq_state.loc[common]=='LOW_LIQ').astype(float)-(liq_state.loc[common]=='HIGH_LIQ').astype(float)
    X=np.column_stack([np.ones(len(common)),liq_num.values,p.values])
    y=r.values
    try:
        beta=np.linalg.lstsq(X,y,rcond=None)[0]
        resid_after=y-X@beta
        # AC after controlling for proxy
        ac_after,_,_=acf(pd.Series(resid_after,index=common),1)
        results['mediation']={'proxy':best_proxy,
                              'ac_before':results['original_effect']['range'],
                              'ac_after':round(abs(ac_after),6),
                              'reduction':round(1-abs(ac_after)/max(results['original_effect']['range'],1e-12),4)}
    except:results['mediation']={'error':'ols_failed'}
    return results

# ================================================== MAIN ===
def run27_full():
    FREEZE_PATH.write_text(json.dumps(FREEZE,indent=1))
    audit=data_audit()
    # Check if any microstructure data exists
    any_micro=any(not v['ohlcv_only'] for v in audit.values())
    if any_micro:
        verdict='DATA_AVAILABLE'
    else:
        verdict='DATA_NOT_READY'
    # Test OHLCV proxies on SBER/LKOH
    proxy_results={}
    for t in ['SBER','LKOH']:
        ds=q15.load_all().get(t)
        if ds is None:continue
        d=ds['train'].copy()
        if 'time' in d.columns:
            d['time']=pd.to_datetime(d['time']);d=d.set_index('time')
        proxy_results[t]=mediation_test(d,t)
    # Friction floor estimation
    avg_spread_bps=50  # typical Russian equity spread ~50bps
    friction_floor=avg_spread_bps/10000
    best_effect=max(r.get('original_effect',{}).get('range',0) for r in proxy_results.values())
    above_floor=best_effect>friction_floor
    result={'schema':SCHEMA,'freeze':FREEZE,'data_audit':audit,
            'microstructure_available':any_micro,
            'data_verdict':verdict,
            'proxy_results':proxy_results,
            'friction_floor':{'avg_spread_bps':avg_spread_bps,'floor':friction_floor,
                              'best_effect':best_effect,'above_floor':above_floor},
            'ts':now()}
    (OUT/'run27_full.json').write_text(json.dumps(result,ensure_ascii=False,indent=1,default=str))
    return result

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--out',required=True);a=p.parse_args()
    r_=run27_full()
    Path(a.out).write_text(json.dumps(r_,ensure_ascii=False,indent=1,default=str))
    print('DATA VERDICT:',r_['data_verdict'])
    print('MICROSTRUCTURE AVAILABLE:',r_['microstructure_available'])
    for t,v in r_['proxy_results'].items():
        print(f'{t}: original_range={v.get("original_effect",{}).get("range")}')
        for p in ['spread_proxy','amihud','vol_concentration','kyle_lambda']:
            if p in v:print(f'  {p}: range={v[p].get("range")}')
        if 'mediation' in v:print(f'  mediation: reduction={v["mediation"].get("reduction")}')
    ff=r_['friction_floor']
    print(f'FLOOR: {ff["avg_spread_bps"]}bps, best_effect={ff["best_effect"]}, above={ff["above_floor"]}')
