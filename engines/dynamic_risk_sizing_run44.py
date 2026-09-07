#!/usr/bin/env python3
"""RUN-44: MARKET_ACTIVITY_FORECAST_V1 Dynamic Risk Sizing Validation.
Deterministic. Paper only. No LLM. No strategy optimization.

Tests if V1 forecast improves risk control vs fixed/naive-vol sizing.
"""
from __future__ import annotations
import json,time
import numpy as np
import pandas as pd
from pathlib import Path
import sys
SC=Path('/root/prop-desk/strategy_combine');FL=Path('/root/prop-desk/futures_lab')
sys.path[:0]=[str(SC),str(FL),str(SC/'engines')]
import question_loop_run15 as q15

SCHEMA='run44-dynamic-risk-sizing-v1'
OUT=Path('/root/audits/strategy_combine_research_intelligence/run44')
OUT.mkdir(parents=True,exist_ok=True)
def now():return time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())

PREREG={
 'target_vol':'expanding median of abs_return from train',
 'min_multiplier':0.2,'max_multiplier':3.0,
 'rebalance':'every bar (aligned with 1h forecast horizon)',
 'exceedance_threshold':1.5,
 'size_methods':['R0_fixed','R1_naive_vol','R2_V1_forecast','R3_V1_state'],
}
PREREG_PATH=OUT/'RUN44_PREREGISTRATION.json'

def r_squared(y,yhat):
    ss_res=((y-yhat)**2).sum();ss_tot=((y-y.mean())**2).sum()
    return 1-ss_res/max(ss_tot,1e-12)

def load_ret():
    raw=q15.load_all()
    rets={};raw_data={}
    for t in ['IMOEX','SBER','LKOH','CNY']:
        if t not in raw:continue
        d=raw[t]['train'].copy()
        if 'time' in d.columns:
            d['time']=pd.to_datetime(d['time']);d=d.set_index('time')
        rets[t]=np.log(d.close/d.close.shift())
        raw_data[t]=d
    return rets,raw_data

def build_v1_features(rets,raw_data):
    imoex=rets['IMOEX']
    target=imoex.abs().shift(-1)
    vol_24h=imoex.rolling(24).std()
    range_24h=imoex.abs().rolling(24).mean()
    vol_pctile=vol_24h.expanding(min_periods=120).rank(pct=True)
    vol_zscore=(vol_24h-vol_24h.expanding(min_periods=120).mean())/vol_24h.expanding(min_periods=120).std().replace(0,np.nan)
    amihud_zscore=pd.Series(0,index=imoex.index)
    comp_ret=pd.DataFrame({t:rets[t] for t in ['SBER','LKOH','CNY'] if t in rets}).reindex(imoex.index)
    mag_het=comp_ret.abs().std(axis=1)
    mag_het_z=(mag_het-mag_het.expanding(min_periods=120).mean())/mag_het.expanding(min_periods=120).std().replace(0,np.nan)
    vol_raw=raw_data['IMOEX']['volume'].reindex(imoex.index)
    vol_mean=vol_raw.expanding(min_periods=120).mean()
    vol_std=vol_raw.expanding(min_periods=120).std().replace(0,np.nan)
    rel_vol=(vol_raw-vol_mean)/vol_std
    vol_cs=pd.DataFrame({t:raw_data[t]['volume'].reindex(imoex.index) for t in ['SBER','LKOH','CNY'] if t in raw_data and 'volume' in raw_data[t].columns}).reindex(imoex.index)
    flow_het=vol_cs.std(axis=1)
    flow_het_z=(flow_het-flow_het.expanding(min_periods=120).mean())/flow_het.expanding(min_periods=120).std().replace(0,np.nan)
    hour=pd.Series(imoex.index.hour,index=imoex.index)
    hour_sin=np.sin(2*np.pi*hour/24);hour_cos=np.cos(2*np.pi*hour/24)
    df=pd.DataFrame({'target':target,'signed_return':imoex.shift(-1),
                    'vol_24h':vol_24h,'range_24h':range_24h,'vol_pctile':vol_pctile,
                    'vol_zscore':vol_zscore,'amihud_zscore':amihud_zscore,
                    'mag_het':mag_het_z,'participation':rel_vol,'flow_het':flow_het_z,
                    'hour_sin':hour_sin,'hour_cos':hour_cos}).dropna()
    return df

def ols(X_train,y_train,X_test):
    X_=np.column_stack([np.ones(len(X_train)),X_train])
    try:
        beta=np.linalg.lstsq(X_,y_train,rcond=None)[0]
        X_t=np.column_stack([np.ones(len(X_test)),X_test])
        return X_t@beta
    except:return np.full(len(X_test),y_train.mean())

def risk_metrics(sizes,returns,target_vol):
    """Compute risk metrics for a sizing strategy."""
    if len(sizes)==0 or len(returns)==0:return {}
    portfolio_returns=sizes*returns
    realized_vol=float(np.std(portfolio_returns))
    risk_error=portfolio_returns-target_vol
    rmse=float(np.sqrt(np.mean(risk_error**2)))
    mae=float(np.mean(np.abs(risk_error)))
    exceedances=np.abs(portfolio_returns)>PREREG['exceedance_threshold']*target_vol
    exc_rate=float(exceedances.mean())
    tail_95=float(np.percentile(portfolio_returns,5))
    tail_99=float(np.percentile(portfolio_returns,1))
    max_dd=0;peak=0;dd=0
    cum_ret=np.cumsum(portfolio_returns)
    for r in cum_ret:
        if r>peak:peak=r
        dd=peak-r
        if dd>max_dd:max_dd=dd
    avg_exposure=float(np.mean(np.abs(sizes)))
    return {'realized_vol':round(realized_vol,6),'rmse':round(rmse,6),'mae':round(mae,6),
            'exceedance_rate':round(exc_rate,4),'tail_95':round(tail_95,6),'tail_99':round(tail_99,6),
            'max_drawdown':round(max_dd,6),'avg_exposure':round(avg_exposure,4),
            'total_return':round(float(np.sum(portfolio_returns)),6)}

def run44_full():
    PREREG_PATH.write_text(json.dumps(PREREG,indent=1))
    rets,raw_data=load_ret()
    if 'IMOEX' not in rets:return {'error':'no IMOEX'}
    df=build_v1_features(rets,raw_data)
    if len(df)<200:return {'error':'insufficient data'}
    n=len(df);split=int(n*0.6)
    train=df.iloc[:split];test=df.iloc[split:]
    # target vol from train
    target_vol=float(train['target'].expanding(min_periods=120).median().iloc[-1])
    # V1 forecast on test
    feat_cols=['vol_24h','range_24h','vol_pctile','vol_zscore','amihud_zscore',
               'mag_het','participation','flow_het','hour_sin','hour_cos']
    v1_forecast=ols(train[feat_cols].values,train['target'].values,test[feat_cols].values)
    # naive vol (current realized)
    naive_vol=test['vol_24h'].values
    # signed returns (assumed neutral: use absolute return as proxy for risk-realization)
    returns=test['target'].values  # absolute return = realized magnitude
    signed_returns=test['signed_future'].values if 'signed_future' in test else test['signed_return'].values
    # R0: fixed size
    sizes_fixed=np.ones(len(test))
    # R1: naive vol targeting
    sizes_naive=np.clip(target_vol/np.maximum(naive_vol,1e-8),PREREG['min_multiplier'],PREREG['max_multiplier'])
    # R2: V1 forecast targeting
    sizes_v1=np.clip(target_vol/np.maximum(v1_forecast,1e-8),PREREG['min_multiplier'],PREREG['max_multiplier'])
    # R3: state sizing
    q25=np.percentile(v1_forecast,25);q50=np.percentile(v1_forecast,50);q75=np.percentile(v1_forecast,75)
    state_mult=np.ones(len(test))
    state_mult[v1_forecast>=q75]=0.3  # EXTREME: strongly reduced
    state_mult[(v1_forecast>=q50)&(v1_forecast<q75)]=0.6  # ACTIVE
    state_mult[(v1_forecast>=q25)&(v1_forecast<q50)]=0.9  # NORMAL
    state_mult[v1_forecast<q25]=1.3  # CALM: slightly increased
    sizes_state=state_mult
    # compute metrics
    m_fixed=risk_metrics(sizes_fixed,returns,target_vol)
    m_naive=risk_metrics(sizes_naive,returns,target_vol)
    m_v1=risk_metrics(sizes_v1,returns,target_vol)
    m_state=risk_metrics(sizes_state,returns,target_vol)
    # random null for V1
    rng=np.random.RandomState(42)
    null_rmse=[]
    for _ in range(50):
        shuf=v1_forecast.copy();rng.shuffle(shuf)
        s_null=np.clip(target_vol/np.maximum(shuf,1e-8),PREREG['min_multiplier'],PREREG['max_multiplier'])
        null_rmse.append(risk_metrics(s_null,returns,target_vol).get('rmse',1))
    real_rmse=m_v1.get('rmse',1)
    pct_in_null=float(np.mean(np.array(null_rmse)<=real_rmse))
    # oracle diagnostic
    oracle_sizes=np.clip(target_vol/np.maximum(returns,1e-8),PREREG['min_multiplier'],PREREG['max_multiplier'])
    m_oracle=risk_metrics(oracle_sizes,returns,target_vol)
    # EXTREME state comparison
    extreme_mask=v1_forecast>=q75
    ext_fixed=risk_metrics(sizes_fixed[extreme_mask],returns[extreme_mask],target_vol)
    ext_v1=risk_metrics(sizes_v1[extreme_mask],returns[extreme_mask],target_vol)
    # state-conditional
    state_cond={}
    for name,lo,hi in [('CALM',0,q25),('NORMAL',q25,q50),('ACTIVE',q50,q75),('EXTREME',q75,1e10)]:
        mask=(v1_forecast>=lo)&(v1_forecast<hi) if hi<1e10 else (v1_forecast>=lo)
        if mask.sum()>10:
            state_cond[name]={'n':int(mask.sum()),'avg_mult':round(float(sizes_v1[mask].mean()),4),
                             'realized_vol':round(float(returns[mask].mean()),6),
                             'tail_95':round(float(np.percentile(returns[mask],5)),6)}
    # temporal blocks
    blocks=[];bs=len(test)//3
    for i in range(3):
        s=i*bs;e=min((i+1)*bs,len(test))
        ret_b=returns[s:e]
        blocks.append({'block':i,'n':e-s,
                       'fixed_vol':round(float(np.std(ret_b)),6),
                       'v1_vol':round(float(np.std(sizes_v1[s:e]*ret_b)),6),
                       'v1_mae':round(float(np.mean(np.abs(sizes_v1[s:e]*ret_b-target_vol))),6)})
    # verdict
    v1_beats_fixed=m_v1['mae']<m_fixed['mae']
    v1_beats_naive=m_v1['mae']<m_naive['mae']
    if v1_beats_fixed and v1_beats_naive:sci='V1_RISK_SIZING_VALIDATED'
    elif v1_beats_fixed:sci='V1_BEATS_FIXED_NOT_NAIVE_VOL'
    else:sci='V1_RISK_ADVANTAGE_NOT_PROVEN'
    practical='RISK_ENGINE_PRODUCTION_CANDIDATE' if sci=='V1_RISK_SIZING_VALIDATED' else 'RISK_ENGINE_RESEARCH_ONLY'
    result={'schema':SCHEMA,'preregistration':PREREG,
            'methods':{'R0_fixed':m_fixed,'R1_naive_vol':m_naive,'R2_V1_forecast':m_v1,'R3_V1_state':m_state},
            'oracle':m_oracle,'extreme_comparison':{'fixed':ext_fixed,'V1':ext_v1},
            'state_conditional':state_cond,'temporal_blocks':blocks,
            'random_null':{'pct_beat_null':round(pct_in_null,4),'real_rmse':round(real_rmse,6)},
            'scientific_verdict':sci,'practical_verdict':practical,'ts':now()}
    (OUT/'run44_full.json').write_text(json.dumps(result,ensure_ascii=False,indent=1,default=str))
    return result

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--out',required=True);a=p.parse_args()
    r_=run44_full()
    Path(a.out).write_text(json.dumps(r_,ensure_ascii=False,indent=1,default=str))
    print('SCIENTIFIC:',r_['scientific_verdict'])
    print('PRACTICAL:',r_['practical_verdict'])
    for k,m in r_['methods'].items():
        print(f'  {k}: rmse={m.get("rmse")} mae={m.get("mae")} exc={m.get("exceedance_rate")} max_dd={m.get("max_drawdown")}')
    print('NULL:',r_['random_null'])
