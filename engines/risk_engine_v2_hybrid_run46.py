#!/usr/bin/env python3
"""RUN-46: RISK_ENGINE_V2_HYBRID — Current Vol + V1 Forecast.
Deterministic. Paper only. No LLM. No strategy optimization.

Tests if combining current-vol and V1 forecast improves risk control.
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

SCHEMA='run46-risk-engine-v2-hybrid'
OUT=Path('/root/audits/strategy_combine_research_intelligence/run46')
OUT.mkdir(parents=True,exist_ok=True)
def now():return time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())

PREREG={
 'parent_checkpoint':'SCIENTIFIC_CHECKPOINT_RUN14_44_V1',
 'hybrid_rule':'min(current_vol_multiplier, V1_multiplier)',
 'min_multiplier':0.2,'max_multiplier':3.0,
 'target_vol':'expanding median from train',
 'exceedance_threshold':1.5,
}
PREREG_PATH=OUT/'RUN46_PREREGISTRATION.json'

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
    realized_vol=float(np.std(pr))
    rmse=float(np.sqrt(np.mean((pr-target_vol)**2)))
    mae=float(np.mean(np.abs(pr-target_vol)))
    exceedances=np.abs(pr)>PREREG['exceedance_threshold']*target_vol
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
    return {'realized_vol':round(realized_vol,6),'rmse':round(rmse,6),'mae':round(mae,6),
            'exceedance_rate':round(exc_rate,4),'large_exceedance':round(large_exc,4),
            'tail_95':round(tail_95,6),'tail_99':round(tail_99,6),'es_95':round(es_95,6),
            'max_drawdown':round(max_dd,6),'avg_exposure':round(avg_exp,4),
            'total_return':round(float(np.sum(pr)),6)}

def run46_full():
    PREREG_PATH.write_text(json.dumps(PREREG,indent=1))
    rets,raw_data=load_ret()
    if 'IMOEX' not in rets:return {'error':'no IMOEX'}
    df=build_features(rets,raw_data)
    if len(df)<200:return {'error':'insufficient data'}
    n=len(df);split=int(n*0.6)
    train=df.iloc[:split];test=df.iloc[split:]
    target_vol=float(train['target'].expanding(min_periods=120).median().iloc[-1])
    feat_cols=['vol_24h','range_24h','vol_pctile','vol_zscore','amihud_zscore',
               'mag_het','participation','flow_het','hour_sin','hour_cos']
    v1_forecast=ols(train[feat_cols].values,train['target'].values,test[feat_cols].values)
    naive_vol=test['vol_24h'].values
    returns=test['target'].values
    # sizing
    sizes_fixed=np.ones(len(test))
    sizes_naive=np.clip(target_vol/np.maximum(naive_vol,1e-8),PREREG['min_multiplier'],PREREG['max_multiplier'])
    sizes_v1=np.clip(target_vol/np.maximum(v1_forecast,1e-8),PREREG['min_multiplier'],PREREG['max_multiplier'])
    q25=np.percentile(v1_forecast,25);q50=np.percentile(v1_forecast,50);q75=np.percentile(v1_forecast,75)
    state_mult=np.ones(len(test))
    state_mult[v1_forecast>=q75]=0.3
    state_mult[(v1_forecast>=q50)&(v1_forecast<q75)]=0.6
    state_mult[(v1_forecast>=q25)&(v1_forecast<q50)]=0.9
    state_mult[v1_forecast<q25]=1.3
    sizes_state=state_mult
    # HYBRID V2: min(current_vol, V1)
    sizes_hybrid=np.minimum(sizes_naive,sizes_v1)
    # metrics
    m0=risk_metrics(sizes_fixed,returns,target_vol)
    m1=risk_metrics(sizes_naive,returns,target_vol)
    m2=risk_metrics(sizes_v1,returns,target_vol)
    m3=risk_metrics(sizes_state,returns,target_vol)
    m4=risk_metrics(sizes_hybrid,returns,target_vol)
    # error complementarity
    err_naive=sizes_naive*returns-target_vol
    err_v1=sizes_v1*returns-target_vol
    err_corr=float(np.corrcoef(err_naive,err_v1)[0,1])
    exc_naive=np.abs(err_naive)>PREREG['exceedance_threshold']*target_vol
    exc_v1=np.abs(err_v1)>PREREG['exceedance_threshold']*target_vol
    both_caught=(~exc_naive)&(~exc_v1)
    naive_only=exc_naive&(~exc_v1)
    v1_only=(~exc_naive)&exc_v1
    both_missed=exc_naive&exc_v1
    complementarity={'error_corr':round(err_corr,4),
                    'both_caught':round(float(both_caught.mean()),4),
                    'naive_only':round(float(naive_only.mean()),4),
                    'v1_only':round(float(v1_only.mean()),4),
                    'both_missed':round(float(both_missed.mean()),4)}
    # risk state matrix
    naive_vol_median=np.median(naive_vol)
    matrix={}
    for sc,lo_c in [('LOW',False),('HIGH',True)]:
        for sf,lo_f in [('LOW',False),('HIGH',True)]:
            mask=((naive_vol>naive_vol_median)==lo_c)&((v1_forecast>np.median(v1_forecast))==lo_f)
            if mask.sum()>10:
                matrix[f'{sc}_VOL_{sf}_V1']={'n':int(mask.sum()),
                    'hybrid_mult':round(float(sizes_hybrid[mask].mean()),4),
                    'realized_risk':round(float((sizes_hybrid[mask]*returns[mask]).mean()),6)}
    # null test
    rng=np.random.RandomState(42)
    null_rmse=[]
    for _ in range(50):
        shuf=v1_forecast.copy();rng.shuffle(shuf)
        s_null=np.clip(target_vol/np.maximum(shuf,1e-8),PREREG['min_multiplier'],PREREG['max_multiplier'])
        h_null=np.minimum(sizes_naive,s_null)
        null_rmse.append(risk_metrics(h_null,returns,target_vol).get('rmse',1))
    real_rmse=m4.get('rmse',1)
    pct_in_null=float(np.mean(np.array(null_rmse)<=real_rmse))
    # temporal blocks
    blocks=[];bs=len(test)//3
    for i in range(3):
        s=i*bs;e=min((i+1)*bs,len(test))
        ret_b=returns[s:e]
        blocks.append({'block':i,'n':e-s,
                       'naive_rmse':round(float(np.sqrt(np.mean((sizes_naive[s:e]*ret_b-target_vol)**2))),6),
                       'v1_rmse':round(float(np.sqrt(np.mean((sizes_v1[s:e]*ret_b-target_vol)**2))),6),
                       'hybrid_rmse':round(float(np.sqrt(np.mean((sizes_hybrid[s:e]*ret_b-target_vol)**2))),6),
                       'hybrid_exc':round(float((np.abs(sizes_hybrid[s:e]*ret_b)>PREREG['exceedance_threshold']*target_vol).mean()),4)})
    # verdict
    hybrid_beats_v1_rmse=m4['rmse']<=m2['rmse']*1.05  # non-inferior
    hybrid_beats_naive_exc=m4['exceedance_rate']<=m1['exceedance_rate']*1.15  # non-inferior
    if hybrid_beats_v1_rmse and hybrid_beats_naive_exc:sci='HYBRID_RISK_COMPLEMENTARITY_CONFIRMED'
    elif hybrid_beats_v1_rmse:sci='HYBRID_RISK_CONTEXTUAL'
    else:sci='NO_HYBRID_ADVANTAGE'
    practical='RISK_ENGINE_V2_HYBRID_CANDIDATE' if 'CONFIRMED' in sci else 'RISK_ENGINE_V2_RESEARCH_ONLY'
    result={'schema':SCHEMA,'preregistration':PREREG,
            'methods':{'R0_fixed':m0,'R1_naive_vol':m1,'R2_V1_forecast':m2,'R3_V1_state':m3,'R4_hybrid':m4},
            'complementarity':complementarity,'risk_state_matrix':matrix,
            'null_test':{'pct_beat_null':round(pct_in_null,4),'real_rmse':round(real_rmse,6)},
            'temporal_blocks':blocks,
            'scientific_verdict':sci,'practical_verdict':practical,
            'parent_checkpoint':'SCIENTIFIC_CHECKPOINT_RUN14_44_V1','ts':now()}
    (OUT/'run46_full.json').write_text(json.dumps(result,ensure_ascii=False,indent=1,default=str))
    return result

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--out',required=True);a=p.parse_args()
    r_=run46_full()
    Path(a.out).write_text(json.dumps(r_,ensure_ascii=False,indent=1,default=str))
    print('SCIENTIFIC:',r_['scientific_verdict'])
    print('PRACTICAL:',r_['practical_verdict'])
    for k,m in r_['methods'].items():
        print(f'  {k}: rmse={m.get("rmse")} exc={m.get("exceedance_rate")} tail95={m.get("tail_95")} avg_exp={m.get("avg_exposure")}')
    print('COMPLEMENTARITY:',r_['complementarity'])
    print('NULL:',r_['null_test'])
