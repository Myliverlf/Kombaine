#!/usr/bin/env python3
"""RUN-47: RISK_ENGINE_V2_HYBRID Independent Temporal Validation.
Deterministic. Paper only. No LLM. No strategy optimization.

Independent validation of frozen Hybrid V2 on untouched data.
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

SCHEMA='run47-hybrid-v2-independent-validation'
OUT=Path('/root/audits/strategy_combine_research_intelligence/run47')
OUT.mkdir(parents=True,exist_ok=True)
def now():return time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())

PREREG={
 'parent_checkpoint':'SCIENTIFIC_CHECKPOINT_RUN14_44_V1',
 'parent_discovery':'RUN-46',
 'hybrid_rule':'min(current_vol_multiplier, V1_multiplier)',
 'min_multiplier':0.2,'max_multiplier':3.0,
 'target_vol':'expanding median from train',
 'exceedance_threshold':1.5,
 'non_inferiority_rmse_tolerance':1.05,
 'non_inferiority_exc_tolerance':1.15,
 'null_simulations':100,
}
PREREG_PATH=OUT/'RUN47_PREREGISTRATION.json'

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

def risk_metrics(sizes,returns,target_vol,exc_thresh=1.5):
    if len(sizes)==0 or len(returns)==0:return {}
    pr=sizes*returns
    realized_vol=float(np.std(pr))
    rmse=float(np.sqrt(np.mean((pr-target_vol)**2)))
    mae=float(np.mean(np.abs(pr-target_vol)))
    exceedances=np.abs(pr)>exc_thresh*target_vol
    exc_rate=float(exceedances.mean())
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
    return {'realized_vol':round(realized_vol,6),'rmse':round(rmse,6),'mae':round(mae,6),
            'exceedance_rate':round(exc_rate,4),'large_exceedance':round(large_exc,4),
            'tail_95':round(tail_95,6),'tail_99':round(tail_99,6),'es_95':round(es_95,6),
            'max_drawdown':round(max_dd,6),'avg_exposure':round(avg_exp,4),'med_exposure':round(med_exp,4)}

def run47_full():
    PREREG_PATH.write_text(json.dumps(PREREG,indent=1))
    rets,raw_data=load_ret()
    if 'IMOEX' not in rets:return {'error':'no IMOEX'}
    df=build_features(rets,raw_data)
    if len(df)<200:return {'error':'insufficient data'}
    n=len(df)
    # Three-way split: 40% train / 20% discovery(RUN-46) / 40% validation(RUN-47)
    train_end=int(n*0.4)
    discovery_end=int(n*0.6)
    train=df.iloc[:train_end]
    validation=df.iloc[discovery_end:]  # genuinely untouched
    # train on first 40%, validate on last 40%
    y_train=train['target'].values
    y_val=validation['target'].values
    target_vol=float(train['target'].expanding(min_periods=120).median().iloc[-1])
    feat_cols=['vol_24h','range_24h','vol_pctile','vol_zscore','amihud_zscore',
               'mag_het','participation','flow_het','hour_sin','hour_cos']
    v1_forecast=ols(train[feat_cols].values,y_train,validation[feat_cols].values)
    naive_vol=validation['vol_24h'].values
    returns=y_val
    # sizing
    sizes_fixed=np.ones(len(validation))
    sizes_naive=np.clip(target_vol/np.maximum(naive_vol,1e-8),PREREG['min_multiplier'],PREREG['max_multiplier'])
    sizes_v1=np.clip(target_vol/np.maximum(v1_forecast,1e-8),PREREG['min_multiplier'],PREREG['max_multiplier'])
    q25=np.percentile(v1_forecast,25);q50=np.percentile(v1_forecast,50);q75=np.percentile(v1_forecast,75)
    state_mult=np.ones(len(validation))
    state_mult[v1_forecast>=q75]=0.3
    state_mult[(v1_forecast>=q50)&(v1_forecast<q75)]=0.6
    state_mult[(v1_forecast>=q25)&(v1_forecast<q50)]=0.9
    state_mult[v1_forecast<q25]=1.3
    sizes_state=state_mult
    sizes_hybrid=np.minimum(sizes_naive,sizes_v1)
    # metrics
    m0=risk_metrics(sizes_fixed,returns,target_vol)
    m1=risk_metrics(sizes_naive,returns,target_vol)
    m2=risk_metrics(sizes_v1,returns,target_vol)
    m3=risk_metrics(sizes_state,returns,target_vol)
    m4=risk_metrics(sizes_hybrid,returns,target_vol)
    # V1-only leading value
    naive_vol_median=np.median(naive_vol)
    v1_median=np.median(v1_forecast)
    v1_only_mask=(naive_vol<=naive_vol_median)&(v1_forecast>v1_median)
    naive_only_mask=(naive_vol>naive_vol_median)&(v1_forecast<=v1_median)
    both_mask=(naive_vol>naive_vol_median)&(v1_forecast>v1_median)
    neither_mask=(naive_vol<=naive_vol_median)&(v1_forecast<=v1_median)
    def state_stats(mask,label):
        if mask.sum()<5:return {'label':label,'n':0}
        pr=sizes_hybrid[mask]*returns[mask]
        return {'label':label,'n':int(mask.sum()),
                'realized_risk':round(float(np.std(pr)),6),
                'exceedance':round(float((np.abs(pr)>PREREG['exceedance_threshold']*target_vol).mean()),4),
                'avg_mult':round(float(sizes_hybrid[mask].mean()),4)}
    state_events={
        'V1_ONLY_LEADING':state_stats(v1_only_mask,'V1 only'),
        'CURRENT_VOL_ONLY':state_stats(naive_only_mask,'Current vol only'),
        'BOTH_CHANNELS':state_stats(both_mask,'Both'),
        'NEITHER':state_stats(neither_mask,'Neither')}
    # error complementarity
    err_naive=sizes_naive*returns-target_vol
    err_v1=sizes_v1*returns-target_vol
    err_corr=float(np.corrcoef(err_naive,err_v1)[0,1])
    exc_naive=np.abs(err_naive)>PREREG['exceedance_threshold']*target_vol
    exc_v1=np.abs(err_v1)>PREREG['exceedance_threshold']*target_vol
    complementarity={'error_corr':round(err_corr,4),
                    'naive_caught':round(float((~exc_naive).mean()),4),
                    'v1_caught':round(float((~exc_v1).mean()),4),
                    'both_caught':round(float((~exc_naive&~exc_v1).mean()),4),
                    'both_missed':round(float((exc_naive&exc_v1).mean()),4)}
    # null test (proper)
    rng=np.random.RandomState(42)
    null_rmse_improvement=[]
    null_exc_improvement=[]
    r1_rmse=m1['rmse'];r1_exc=m1['exceedance_rate']
    for _ in range(PREREG['null_simulations']):
        # block shuffle to preserve some time structure
        block_size=24
        n_blocks=len(v1_forecast)//block_size
        blocks=[v1_forecast[i*block_size:(i+1)*block_size] for i in range(n_blocks)]
        rng.shuffle(blocks)
        shuf=np.concatenate(blocks)[:len(v1_forecast)]
        s_null=np.clip(target_vol/np.maximum(shuf,1e-8),PREREG['min_multiplier'],PREREG['max_multiplier'])
        h_null=np.minimum(sizes_naive,s_null)
        rm_null=risk_metrics(h_null,returns,target_vol)
        null_rmse_improvement.append(r1_rmse-rm_null.get('rmse',r1_rmse))
        null_exc_improvement.append(r1_exc-rm_null.get('exceedance_rate',r1_exc))
    real_rmse_imp=r1_rmse-m4['rmse']
    real_exc_imp=r1_exc-m4['exceedance_rate']
    rmse_percentile=float(np.mean(np.array(null_rmse_improvement)<=real_rmse_imp))
    exc_percentile=float(np.mean(np.array(null_exc_improvement)<=real_exc_imp))
    # temporal blocks
    blocks_t=[];bs=len(validation)//3
    for i in range(3):
        s=i*bs;e=min((i+1)*bs,len(validation))
        ret_b=returns[s:e]
        blocks_t.append({'block':i,'n':e-s,
                        'naive_rmse':round(float(np.sqrt(np.mean((sizes_naive[s:e]*ret_b-target_vol)**2))),6),
                        'v1_rmse':round(float(np.sqrt(np.mean((sizes_v1[s:e]*ret_b-target_vol)**2))),6),
                        'hybrid_rmse':round(float(np.sqrt(np.mean((sizes_hybrid[s:e]*ret_b-target_vol)**2))),6),
                        'hybrid_exc':round(float((np.abs(sizes_hybrid[s:e]*ret_b)>PREREG['exceedance_threshold']*target_vol).mean()),4)})
    # verdicts
    hybrid_rmse_ok=m4['rmse']<=m1['rmse']*PREREG['non_inferiority_rmse_tolerance']
    hybrid_exc_ok=m4['exceedance_rate']<=m1['exceedance_rate']*PREREG['non_inferiority_exc_tolerance']
    if hybrid_rmse_ok and hybrid_exc_ok:engine_verdict='RISK_ENGINE_V2_HYBRID_VALIDATED'
    elif hybrid_rmse_ok:engine_verdict='RISK_ENGINE_V2_HYBRID_CONTEXTUAL'
    else:engine_verdict='RISK_ENGINE_V2_NOT_REPLICATED'
    # V1 incremental
    v1_p_value=1-rmse_percentile
    if v1_p_value<0.05:v1_verdict='V1_INCREMENTAL_RISK_VALUE_CONFIRMED'
    elif v1_p_value<0.15:v1_verdict='V1_INCREMENTAL_RISK_VALUE_CONTEXTUAL'
    elif v1_p_value<0.30:v1_verdict='V1_INCREMENTAL_RISK_VALUE_MARGINAL'
    else:v1_verdict='V1_INCREMENTAL_RISK_VALUE_NOT_CONFIRMED'
    result={'schema':SCHEMA,'preregistration':PREREG,
            'data_independence':{'train_end':train_end,'discovery_end':discovery_end,
                               'validation_start':discovery_end,'validation_end':n,
                               'n_validation':len(validation)},
            'methods':{'R0_fixed':m0,'R1_naive_vol':m1,'R2_V1_forecast':m2,'R3_V1_state':m3,'R4_hybrid':m4},
            'complementarity':complementarity,'state_events':state_events,
            'null_test':{'n_simulations':PREREG['null_simulations'],
                        'real_rmse_improvement':round(real_rmse_imp,6),
                        'rmse_percentile':round(rmse_percentile,4),'rmse_p_value':round(1-rmse_percentile,4),
                        'real_exc_improvement':round(real_exc_imp,4),
                        'exc_percentile':round(exc_percentile,4),'exc_p_value':round(1-exc_percentile,4)},
            'temporal_blocks':blocks_t,
            'engine_verdict':engine_verdict,'v1_incremental_verdict':v1_verdict,
            'parent_checkpoint':'SCIENTIFIC_CHECKPOINT_RUN14_44_V1','ts':now()}
    (OUT/'run47_full.json').write_text(json.dumps(result,ensure_ascii=False,indent=1,default=str))
    return result

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--out',required=True);a=p.parse_args()
    r_=run47_full()
    Path(a.out).write_text(json.dumps(r_,ensure_ascii=False,indent=1,default=str))
    print('ENGINE:',r_['engine_verdict'])
    print('V1 INCREMENT:',r_['v1_incremental_verdict'])
    for k,m in r_['methods'].items():
        print(f'  {k}: rmse={m.get("rmse")} exc={m.get("exceedance_rate")} avg_exp={m.get("avg_exposure")}')
    print('NULL:',r_['null_test'])
    print('COMPLEMENTARITY:',r_['complementarity'])
    print('STATE EVENTS:')
    for k,v in r_['state_events'].items():
        print(f'  {k}: n={v.get("n")} risk={v.get("realized_risk")} exc={v.get("exceedance")}')
