#!/usr/bin/env python3
"""RUN-48: Hybrid Advantage — Information or Mechanical Clipping?
Deterministic. Paper only. No LLM. No strategy optimization.

Decomposes whether V1 forecast adds value or if min() is just mechanical clipping.
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

SCHEMA='run48-information-vs-mechanical-clipping'
OUT=Path('/root/audits/strategy_combine_research_intelligence/run48')
OUT.mkdir(parents=True,exist_ok=True)
def now():return time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())

PREREG={
 'parent_checkpoint':'SCIENTIFIC_CHECKPOINT_RUN14_44_V1',
 'parent_runs':['RUN-46','RUN-47'],
 'min_multiplier':0.2,'max_multiplier':3.0,
 'target_vol':'expanding median from train',
 'exceedance_threshold':1.5,
 'null_simulations':100,
 'mechanical_cap_derivation':'match average exposure of true hybrid from discovery period',
}
PREREG_PATH=OUT/'RUN48_PREREGISTRATION.json'

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
    return {'rmse':round(rmse,6),'mae':round(mae,6),'exceedance':round(exc,4),
            'large_exceedance':round(large_exc,4),'tail_95':round(tail_95,6),'tail_99':round(tail_99,6),
            'es_95':round(es_95,6),'max_drawdown':round(max_dd,6),'avg_exposure':round(avg_exp,4)}

def run48_full():
    PREREG_PATH.write_text(json.dumps(PREREG,indent=1))
    rets,raw_data=load_ret()
    if 'IMOEX' not in rets:return {'error':'no IMOEX'}
    df=build_features(rets,raw_data)
    if len(df)<200:return {'error':'insufficient data'}
    n=len(df)
    # 3-way: 40% train / 20% discovery / 40% validation
    train_end=int(n*0.4);discovery_end=int(n*0.6)
    train=df.iloc[:train_end];discovery=df.iloc[train_end:discovery_end];validation=df.iloc[discovery_end:]
    y_train=train['target'].values
    feat_cols=['vol_24h','range_24h','vol_pctile','vol_zscore','amihud_zscore',
               'mag_het','participation','flow_het','hour_sin','hour_cos']
    # V1 on discovery (for mechanical cap derivation)
    v1_disc=ols(train[feat_cols].values,y_train,discovery[feat_cols].values)
    naive_disc=discovery['vol_24h'].values
    target_vol=float(train['target'].expanding(min_periods=120).median().iloc[-1])
    mult_naive_disc=np.clip(target_vol/np.maximum(naive_disc,1e-8),PREREG['min_multiplier'],PREREG['max_multiplier'])
    mult_v1_disc=np.clip(target_vol/np.maximum(v1_disc,1e-8),PREREG['min_multiplier'],PREREG['max_multiplier'])
    hybrid_disc=np.minimum(mult_naive_disc,mult_v1_disc)
    avg_hybrid_exp=float(np.mean(hybrid_disc))
    # derive mechanical cap to match hybrid average exposure
    # cap = value such that mean(min(naive_mult, cap)) ≈ avg_hybrid_exp
    # binary search
    lo,hi=PREREG['min_multiplier'],PREREG['max_multiplier']
    for _ in range(50):
        mid=(lo+hi)/2
        test_mean=np.mean(np.minimum(mult_naive_disc,mid))
        if test_mean>avg_hybrid_exp:hi=mid
        else:lo=mid
    mech_cap=round(mid,4)
    # validation
    v1_val=ols(train[feat_cols].values,y_train,validation[feat_cols].values)
    naive_val=validation['vol_24h'].values
    returns=validation['target'].values
    mult_naive=np.clip(target_vol/np.maximum(naive_val,1e-8),PREREG['min_multiplier'],PREREG['max_multiplier'])
    mult_v1=np.clip(target_vol/np.maximum(v1_val,1e-8),PREREG['min_multiplier'],PREREG['max_multiplier'])
    sizes_R1=mult_naive
    sizes_R2=np.minimum(mult_naive,mult_v1)  # true hybrid
    # R3: placebo hybrid (blocked temporal shift of V1)
    block_size=48
    n_blocks=len(mult_v1)//block_size
    blocks=[mult_v1[i*block_size:(i+1)*block_size] for i in range(n_blocks)]
    shift=max(1,n_blocks//3)
    shifted=blocks[shift:]+blocks[:shift]
    placebo_v1=np.concatenate(shifted)[:len(mult_v1)]
    sizes_R3=np.minimum(mult_naive,placebo_v1)
    # R4: mechanical clip (no V1 info)
    sizes_R4=np.minimum(mult_naive,mech_cap)
    # metrics
    m1=risk_metrics(sizes_R1,returns,target_vol)
    m2=risk_metrics(sizes_R2,returns,target_vol)
    m3=risk_metrics(sizes_R3,returns,target_vol)
    m4=risk_metrics(sizes_R4,returns,target_vol)
    # exposure matching
    exposure_match={'R1':m1['avg_exposure'],'R2':m2['avg_exposure'],'R3':m3['avg_exposure'],'R4':m4['avg_exposure']}
    # effect decomposition
    total_effect_rmse=m1['rmse']-m2['rmse']
    mechanical_effect_rmse=m1['rmse']-m4['rmse']
    info_effect_rmse=m4['rmse']-m2['rmse']
    timing_effect_rmse=m3['rmse']-m2['rmse']
    total_effect_exc=m1['exceedance']-m2['exceedance']
    mechanical_effect_exc=m1['exceedance']-m4['exceedance']
    info_effect_exc=m4['exceedance']-m2['exceedance']
    timing_effect_exc=m3['exceedance']-m2['exceedance']
    # V1-only events
    v1_median=np.median(v1_val);naive_median=np.median(naive_val)
    v1_only=(naive_val<=naive_median)&(v1_val>v1_median)
    naive_only=(naive_val>naive_median)&(v1_val<=v1_median)
    def event_stats(mask,label):
        if mask.sum()<5:return {'label':label,'n':0}
        pr=sizes_R2[mask]*returns[mask]
        return {'label':label,'n':int(mask.sum()),
                'realized_risk':round(float(np.std(pr)),6),
                'exceedance':round(float((np.abs(pr)>PREREG['exceedance_threshold']*target_vol).mean()),4)}
    events={'V1_ONLY':event_stats(v1_only,'V1 only'),
            'CURRENT_VOL_ONLY':event_stats(naive_only,'Current vol only'),
            'BOTH':event_stats((naive_val>naive_median)&(v1_val>v1_median),'Both'),
            'NEITHER':event_stats((naive_val<=naive_median)&(v1_val<=v1_median),'Neither')}
    # false caution: V1 cuts but future risk is low
    false_caution_mask=v1_only&(returns<np.median(returns))
    false_caution_rate=float(false_caution_mask.mean()) if v1_only.sum()>0 else 0
    # matched clipping null
    rng=np.random.RandomState(42)
    null_rmse_diff=[];null_exc_diff=[]
    for _ in range(PREREG['null_simulations']):
        blks=list(range(n_blocks))
        rng.shuffle(blks)
        shuffled_v1=np.concatenate([blocks[i] for i in blks])[:len(mult_v1)]
        h_null=np.minimum(mult_naive,shuffled_v1)
        rm_null=risk_metrics(h_null,returns,target_vol)
        null_rmse_diff.append(m1['rmse']-rm_null.get('rmse',m1['rmse']))
        null_exc_diff.append(m1['exceedance']-rm_null.get('exceedance',m1['exceedance']))
    real_rmse_diff=m1['rmse']-m2['rmse']
    real_exc_diff=m1['exceedance']-m2['exceedance']
    rmse_pctl=float(np.mean(np.array(null_rmse_diff)<=real_rmse_diff))
    exc_pctl=float(np.mean(np.array(null_exc_diff)<=real_exc_diff))
    # attribution
    if total_effect_rmse>0:
        mech_fraction_rmse=round(mechanical_effect_rmse/total_effect_rmse,4)
        info_fraction_rmse=round(info_effect_rmse/total_effect_rmse,4)
    else:mech_fraction_rmse=0;info_fraction_rmse=0
    if total_effect_exc>0:
        mech_fraction_exc=round(mechanical_effect_exc/total_effect_exc,4)
        info_fraction_exc=round(info_effect_exc/total_effect_exc,4)
    else:mech_fraction_exc=0;info_fraction_exc=0
    # verdict
    info_beats_placebo=info_effect_rmse>0.0001
    info_beats_mech=info_effect_rmse>0.0001
    if info_beats_placebo and info_beats_mech:sci='HYBRID_ADVANTAGE_INFORMATIONAL'
    elif mechanical_effect_rmse>0.0001:sci='HYBRID_ADVANTAGE_MOSTLY_MECHANICAL'
    else:sci='HYBRID_ADVANTAGE_FULLY_MECHANICAL'
    if sci=='HYBRID_ADVANTAGE_INFORMATIONAL':arch='KEEP_RISK_ENGINE_V2_HYBRID'
    else:arch='SIMPLIFY_TO_REACTIVE_RISK_ENGINE'
    result={'schema':SCHEMA,'preregistration':PREREG,
            'mechanical_cap':mech_cap,'exposure_match':exposure_match,
            'methods':{'R1_naive_vol':m1,'R2_true_hybrid':m2,'R3_placebo_hybrid':m3,'R4_mechanical_clip':m4},
            'effect_decomposition':{'rmse':{'total':round(total_effect_rmse,6),'mechanical':round(mechanical_effect_rmse,6),
                                           'info':round(info_effect_rmse,6),'timing':round(timing_effect_rmse,6)},
                                   'exceedance':{'total':round(total_effect_exc,4),'mechanical':round(mechanical_effect_exc,4),
                                                'info':round(info_effect_exc,4),'timing':round(timing_effect_exc,4)}},
            'attribution':{'rmse_mechanical_fraction':mech_fraction_rmse,'rmse_info_fraction':info_fraction_rmse,
                          'exc_mechanical_fraction':mech_fraction_exc,'exc_info_fraction':info_fraction_exc},
            'events':events,'false_caution_rate':round(false_caution_rate,4),
            'null_test':{'rmse_pctl':round(rmse_pctl,4),'exc_pctl':round(exc_pctl,4)},
            'scientific_verdict':sci,'architecture_verdict':arch,
            'parent_checkpoint':'SCIENTIFIC_CHECKPOINT_RUN14_44_V1','ts':now()}
    (OUT/'run48_full.json').write_text(json.dumps(result,ensure_ascii=False,indent=1,default=str))
    return result

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--out',required=True);a=p.parse_args()
    r_=run48_full()
    Path(a.out).write_text(json.dumps(r_,ensure_ascii=False,indent=1,default=str))
    print('SCIENTIFIC:',r_['scientific_verdict'])
    print('ARCHITECTURE:',r_['architecture_verdict'])
    print('DECOMPOSITION RMSE:',r_['effect_decomposition']['rmse'])
    print('ATTRIBUTION:',r_['attribution'])
    print('NULL:',r_['null_test'])
    for k,m in r_['methods'].items():
        print(f'  {k}: rmse={m.get("rmse")} exc={m.get("exceedance")} exp={m.get("avg_exposure")}')
    print('EVENTS:')
    for k,v in r_['events'].items():
        print(f'  {k}: n={v.get("n")} exc={v.get("exceedance")}')
    print('FALSE CAUTION:',r_['false_caution_rate'])
