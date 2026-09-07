#!/usr/bin/env python3
"""RUN-49: RISK_ENGINE_V2_REACTIVE Independent Validation.
Deterministic. Paper only. No LLM. No strategy optimization.

Validates simplified current-vol + safety cap engine independently.
Parent checkpoint: SCIENTIFIC_CHECKPOINT_RUN14_44_V1
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

SCHEMA='run49-reactive-risk-engine-validation'
OUT=Path('/root/audits/strategy_combine_research_intelligence/run49')
OUT.mkdir(parents=True,exist_ok=True)
def now():return time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())

PREREG={
 'parent_checkpoint':'SCIENTIFIC_CHECKPOINT_RUN14_44_V1',
 'engine':'current_vol_multiplier clipped by safety_cap',
 'safety_cap':'derived from discovery period (40-60%), exposure-matched to hybrid V2',
 'min_multiplier':0.2,'max_multiplier':3.0,
 'target_vol':'expanding median from train',
 'exceedance_threshold':1.5,
 'null_simulations':100,
}
PREREG_PATH=OUT/'RUN49_PREREGISTRATION.json'

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

def build_features(rets,raw_data):
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
    df=pd.DataFrame({'target':target,'vol_24h':vol_24h,'range_24h':range_24h,'vol_pctile':vol_pctile,
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
    if len(sizes)==0 or len(returns)==0:return {}
    pr=sizes*returns
    rmse=float(np.sqrt(np.mean((pr-target_vol)**2)))
    mae=float(np.mean(np.abs(pr-target_vol)))
    exc=float((np.abs(pr)>PREREG['exceedance_threshold']*target_vol).mean())
    large_exc=float((np.abs(pr)>2*target_vol).mean())
    tail_95=float(np.percentile(pr,5))
    tail_99=float(np.percentile(pr,1))
    es_95=float(np.mean(pr[pr<=np.percentile(pr,5)])) if np.sum(pr<=np.percentile(pr,5))>0 else tail_95
    max_dd=0;peak=0
    for r in np.cumsum(pr):
        if r>peak:peak=r
        dd=peak-r
        if dd>max_dd:max_dd=dd
    avg_exp=float(np.mean(np.abs(sizes)))
    med_exp=float(np.median(np.abs(sizes)))
    return {'rmse':round(rmse,6),'mae':round(mae,6),'exceedance':round(exc,4),
            'large_exceedance':round(large_exc,4),'tail_95':round(tail_95,6),'tail_99':round(tail_99,6),
            'es_95':round(es_95,6),'max_drawdown':round(max_dd,6),
            'avg_exposure':round(avg_exp,4),'med_exposure':round(med_exp,4)}

def run49_full():
    PREREG_PATH.write_text(json.dumps(PREREG,indent=1))
    rets,raw_data=load_ret()
    if 'IMOEX' not in rets:return {'error':'no IMOEX'}
    df=build_features(rets,raw_data)
    if len(df)<200:return {'error':'insufficient data'}
    n=len(df)
    # 3-way: 40% train / 20% discovery / 40% validation
    train_end=int(n*0.4);discovery_end=int(n*0.6)
    train=df.iloc[:train_end];discovery=df.iloc[train_end:discovery_end];validation=df.iloc[discovery_end:]
    target_vol=float(train['target'].expanding(min_periods=120).median().iloc[-1])
    # derive safety cap from discovery period (exposure-matched to hybrid V2)
    feat_cols=['vol_24h','range_24h','vol_pctile','vol_zscore','amihud_zscore',
               'mag_het','participation','flow_het','hour_sin','hour_cos']
    v1_disc=ols(train[feat_cols].values,train['target'].values,discovery[feat_cols].values)
    naive_disc=discovery['vol_24h'].values
    mult_naive_disc=np.clip(target_vol/np.maximum(naive_disc,1e-8),PREREG['min_multiplier'],PREREG['max_multiplier'])
    mult_v1_disc=np.clip(target_vol/np.maximum(v1_disc,1e-8),PREREG['min_multiplier'],PREREG['max_multiplier'])
    hybrid_disc=np.minimum(mult_naive_disc,mult_v1_disc)
    avg_hybrid_exp=float(np.mean(hybrid_disc))
    # binary search for cap
    lo,hi=PREREG['min_multiplier'],PREREG['max_multiplier']
    for _ in range(50):
        mid=(lo+hi)/2
        if np.mean(np.minimum(mult_naive_disc,mid))>avg_hybrid_exp:hi=mid
        else:lo=mid
    safety_cap=round(mid,4)
    # validation
    naive_val=validation['vol_24h'].values
    returns=validation['target'].values
    mult_naive=np.clip(target_vol/np.maximum(naive_val,1e-8),PREREG['min_multiplier'],PREREG['max_multiplier'])
    sizes_R0=np.ones(len(validation))
    sizes_R1=mult_naive
    sizes_R2=np.minimum(mult_naive,safety_cap)
    # metrics
    m0=risk_metrics(sizes_R0,returns,target_vol)
    m1=risk_metrics(sizes_R1,returns,target_vol)
    m2=risk_metrics(sizes_R2,returns,target_vol)
    # cap activity
    cap_binding=mult_naive>safety_cap
    cap_bind_rate=float(cap_binding.mean())
    false_clip=cap_binding&(returns<np.median(returns))
    false_clip_rate=float(false_clip.mean()) if cap_binding.sum()>0 else 0
    missed_risk=(~cap_binding)&(returns>np.percentile(returns,90))
    missed_risk_rate=float(missed_risk.mean()) if (~cap_binding).sum()>0 else 0
    # exceedance analysis
    exc_r1=np.abs(sizes_R1*returns)>PREREG['exceedance_threshold']*target_vol
    exc_r2=np.abs(sizes_R2*returns)>PREREG['exceedance_threshold']*target_vol
    exc_prevented=int((exc_r1&~exc_r2).sum())
    exc_unchanged=int((exc_r1&exc_r2).sum())
    exc_new=int((~exc_r1&exc_r2).sum())
    # random cap null
    rng=np.random.RandomState(42)
    null_rmse_diff=[];null_exc_diff=[]
    for _ in range(PREREG['null_simulations']):
        # random cap-binding schedule preserving frequency
        random_bind=rng.random(len(cap_binding))<cap_bind_rate
        random_sizes=mult_naive.copy()
        random_sizes[random_bind]=np.minimum(random_sizes[random_bind],safety_cap)
        rm_null=risk_metrics(random_sizes,returns,target_vol)
        null_rmse_diff.append(m1['rmse']-rm_null.get('rmse',m1['rmse']))
        null_exc_diff.append(m1['exceedance']-rm_null.get('exceedance',m1['exceedance']))
    real_rmse_diff=m1['rmse']-m2['rmse']
    real_exc_diff=m1['exceedance']-m2['exceedance']
    rmse_pctl=float(np.mean(np.array(null_rmse_diff)<=real_rmse_diff))
    exc_pctl=float(np.mean(np.array(null_exc_diff)<=real_exc_diff))
    # temporal blocks
    blocks=[];bs=len(validation)//3
    for i in range(3):
        s=i*bs;e=min((i+1)*bs,len(validation))
        ret_b=returns[s:e];nb=mult_naive[s:e]
        sb=np.minimum(nb,safety_cap)
        blocks.append({'block':i,'n':e-s,
                       'r1_rmse':round(float(np.sqrt(np.mean((nb*ret_b-target_vol)**2))),6),
                       'r2_rmse':round(float(np.sqrt(np.mean((sb*ret_b-target_vol)**2))),6),
                       'r2_exc':round(float((np.abs(sb*ret_b)>PREREG['exceedance_threshold']*target_vol).mean()),4),
                       'cap_bind':round(float((nb>safety_cap).mean()),4)})
    # verdict
    rmse_ok=m2['rmse']<=m1['rmse']*1.05
    exc_ok=m2['exceedance']<=m1['exceedance']*1.15
    if rmse_ok and exc_ok:sci='REACTIVE_RISK_ENGINE_VALIDATED'
    elif rmse_ok:sci='REACTIVE_RISK_ENGINE_CONTEXTUAL'
    else:sci='SAFETY_CAP_ADDS_NO_ROBUST_VALUE'
    practical='RISK_ENGINE_V2_REACTIVE_PRODUCTION_CANDIDATE' if 'VALIDATED' in sci else 'KEEP_NAIVE_CURRENT_VOL'
    result={'schema':SCHEMA,'preregistration':PREREG,
            'safety_cap':safety_cap,'cap_lineage':'derived from discovery period 40-60%, exposure-matched to hybrid V2',
            'methods':{'R0_fixed':m0,'R1_current_vol':m1,'R2_reactive_cap':m2},
            'cap_activity':{'bind_rate':round(cap_bind_rate,4),'false_clip_rate':round(false_clip_rate,4),
                           'missed_risk_rate':round(missed_risk_rate,4)},
            'exceedance_analysis':{'prevented':exc_prevented,'unchanged':exc_unchanged,'new':exc_new},
            'null_test':{'rmse_pctl':round(rmse_pctl,4),'exc_pctl':round(exc_pctl,4)},
            'temporal_blocks':blocks,
            'scientific_verdict':sci,'practical_verdict':practical,
            'parent_checkpoint':'SCIENTIFIC_CHECKPOINT_RUN14_44_V1','ts':now()}
    (OUT/'run49_full.json').write_text(json.dumps(result,ensure_ascii=False,indent=1,default=str))
    return result

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--out',required=True);a=p.parse_args()
    r_=run49_full()
    Path(a.out).write_text(json.dumps(r_,ensure_ascii=False,indent=1,default=str))
    print('SCIENTIFIC:',r_['scientific_verdict'])
    print('PRACTICAL:',r_['practical_verdict'])
    print('CAP:',r_['safety_cap'])
    for k,m in r_['methods'].items():
        print(f'  {k}: rmse={m.get("rmse")} exc={m.get("exceedance")} exp={m.get("avg_exposure")}')
    print('CAP ACTIVITY:',r_['cap_activity'])
    print('EXCEEDANCE:',r_['exceedance_analysis'])
    print('NULL:',r_['null_test'])
