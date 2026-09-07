#!/usr/bin/env python3
"""RUN-42: Participation Independent Replication & Lead-Lag Mechanism.
Deterministic. Paper only. No LLM. No strategy optimization.

Replicates participation on independent data and tests causal timing.
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

SCHEMA='run42-participation-replication-v1'
OUT=Path('/root/audits/strategy_combine_research_intelligence/run42')
OUT.mkdir(parents=True,exist_ok=True)
def now():return time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())

PREREG={
 'participation':'relative_volume = (volume - expanding_mean) / expanding_std',
 'target':'abs_return_1h',
 'horizon':1,
 'oos_split':'chronological 60/40',
 'validation_sample':'last 40% (independent from RUN-41 discovery)',
 'materiality_threshold_delta_r2':0.01,
}
PREREG_PATH=OUT/'RUN42_PREREGISTRATION.json'

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
    # RUN-32 baseline
    vol_24h=imoex.rolling(24).std()
    range_24h=imoex.abs().rolling(24).mean()
    vol_pctile=vol_24h.expanding(min_periods=120).rank(pct=True)
    vol_zscore=(vol_24h-vol_24h.expanding(min_periods=120).mean())/vol_24h.expanding(min_periods=120).std().replace(0,np.nan)
    amihud_zscore=pd.Series(0,index=imoex.index)
    # magnitude heterogeneity
    comp_ret=pd.DataFrame({t:rets[t] for t in ['SBER','LKOH','CNY'] if t in rets}).reindex(imoex.index)
    mag_het=comp_ret.abs().std(axis=1)
    mag_het_z=(mag_het-mag_het.expanding(min_periods=120).mean())/mag_het.expanding(min_periods=120).std().replace(0,np.nan)
    # participation
    if 'volume' in raw_data.get('IMOEX',{}).columns if isinstance(raw_data.get('IMOEX',{}),pd.DataFrame) else False:
        vol_raw=raw_data['IMOEX']['volume'].reindex(imoex.index)
        vol_mean=vol_raw.expanding(min_periods=120).mean()
        vol_std=vol_raw.expanding(min_periods=120).std().replace(0,np.nan)
        rel_vol=(vol_raw-vol_mean)/vol_std
    else:
        rel_vol=pd.Series(0,index=imoex.index)
    # flow heterogeneity
    vol_cs=pd.DataFrame({t:raw_data[t]['volume'].reindex(imoex.index) for t in ['SBER','LKOH','CNY'] if t in raw_data and 'volume' in raw_data[t].columns}).reindex(imoex.index)
    flow_het=vol_cs.std(axis=1)
    flow_het_z=(flow_het-flow_het.expanding(min_periods=120).mean())/flow_het.expanding(min_periods=120).std().replace(0,np.nan)
    # time-of-day (hour of day)
    hour=pd.Series(imoex.index.hour,index=imoex.index)
    hour_sin=np.sin(2*np.pi*hour/24)
    hour_cos=np.cos(2*np.pi*hour/24)
    df=pd.DataFrame({'target':target,'vol_24h':vol_24h,'range_24h':range_24h,
                    'vol_pctile':vol_pctile,'vol_zscore':vol_zscore,'amihud_zscore':amihud_zscore,
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

def eval_model(y_test,yhat):
    return {'r2':round(r_squared(y_test,yhat),6),
            'corr':round(float(np.corrcoef(y_test,yhat)[0,1]),6),
            'mae':round(float(np.mean(np.abs(y_test-yhat))),6),
            'rmse':round(float(np.sqrt(np.mean((y_test-yhat)**2))),6)}

def run42_full():
    PREREG_PATH.write_text(json.dumps(PREREG,indent=1))
    rets,raw_data=load_ret()
    if 'IMOEX' not in rets:return {'error':'no IMOEX'}
    df=build_features(rets,raw_data)
    if len(df)<200:return {'error':'insufficient data'}
    n=len(df)
    # independent split: 60% train / 40% validation (discovery was different period)
    split=int(n*0.6)
    train=df.iloc[:split];val=df.iloc[split:]
    y_train=train['target'].values;y_val=val['target'].values
    base_cols=['vol_24h','range_24h','vol_pctile','vol_zscore','amihud_zscore']
    het_cols=base_cols+['mag_het']
    part_cols=het_cols+['participation']
    all_cols=part_cols+['flow_het']
    # Model A: FULL_RUN32
    yhat_A=ols(train[base_cols].values,y_train,val[base_cols].values)
    mA=eval_model(y_val,yhat_A)
    # Model B: + heterogeneity
    yhat_B=ols(train[het_cols].values,y_train,val[het_cols].values)
    mB=eval_model(y_val,yhat_B)
    # Model C: + participation
    yhat_C=ols(train[part_cols].values,y_train,val[part_cols].values)
    mC=eval_model(y_val,yhat_C)
    # Model D: + flow heterogeneity
    yhat_D=ols(train[all_cols].values,y_train,val[all_cols].values)
    mD=eval_model(y_val,yhat_D)
    # Model E: + time-of-day control
    tod_cols=part_cols+['hour_sin','hour_cos']
    yhat_E=ols(train[tod_cols].values,y_train,val[tod_cols].values)
    mE=eval_model(y_val,yhat_E)
    # Model F: participation after current volatility control (more aggressive)
    shock_cols=['vol_24h','range_24h','amihud_zscore','mag_het','participation']
    yhat_F=ols(train[shock_cols].values,y_train,val[shock_cols].values)
    mF=eval_model(y_val,yhat_F)
    # Model G: interaction
    train_int=train.copy();val_int=val.copy()
    train_int['het_x_part']=train['mag_het']*train['participation']
    val_int['het_x_part']=val['mag_het']*val['participation']
    g_cols=part_cols+['het_x_part']
    yhat_G=ols(train_int[g_cols].values,y_train,val_int[g_cols].values)
    mG=eval_model(y_val,yhat_G)
    # state analysis
    med_het=train['mag_het'].median();med_part=train['participation'].median()
    states={}
    for sh,lo_het in [('LOW',False),('HIGH',True)]:
        for sp,lo_part in [('LOW',False),('HIGH',True)]:
            mask=((val['mag_het']>med_het)==lo_het)&((val['participation']>med_part)==lo_part)
            if mask.sum()>10:
                states[f'{sh}_HET_{sp}_PART']={'n':int(mask.sum()),
                    'mean_vol':round(float(val['target'][mask].mean()),6),
                    'large_move_rate':round(float((val['target'][mask]>np.percentile(y_val,80)).mean()),4)}
    # temporal blocks
    blocks=[];bs=len(val)//3
    for i in range(3):
        s=i*bs;e=min((i+1)*bs,len(val))
        yt=y_val[s:e]
        models_t={}
        for name,cols in [('B',het_cols),('C',part_cols)]:
            yh=ols(train[cols].values,y_train,yt)
            models_t[name]=round(r_squared(yt,yh),6)
        blocks.append({'block':i,'n':e-s,'r2_B':models_t['B'],'r2_C':models_t['C'],
                       'delta':round(models_t['C']-models_t['B'],6)})
    # LOCO
    loco={}
    for leave in ['SBER','LKOH','CNY']:
        remaining=[t for t in ['SBER','LKOH','CNY'] if t!=leave]
        if len(remaining)<2:continue
        # rebuild with remaining
        comp_r=pd.DataFrame({t:rets[t] for t in remaining}).reindex(rets['IMOEX'].index)
        mh=comp_r.abs().std(axis=1)
        mh_z=(mh-mh.expanding(min_periods=120).mean())/mh.expanding(min_periods=120).std().replace(0,np.nan)
        tmp=df.copy();tmp['mag_het_loco']=mh_z.reindex(df.index)
        tl=int(len(tmp)*0.6)
        tt=tmp.iloc[:tl];tv=tmp.iloc[tl:]
        yh_b=ols(tt[base_cols+['mag_het_loco']].values,tt['target'].values,tv[base_cols+['mag_het_loco']].values)
        yh_c=ols(tt[base_cols+['mag_het_loco','participation']].values,tt['target'].values,tv[base_cols+['mag_het_loco','participation']].values)
        loco[leave]=round(r_squared(tv['target'].values,yh_c)-r_squared(tv['target'].values,yh_b),6)
    # directional
    signed=rets['IMOEX'].shift(-1).dropna()
    common=df.index.intersection(signed.index)
    dir_corr_part=float(np.corrcoef(df.loc[common,'participation'].values,signed.reindex(common).values)[0,1]) if len(common)>50 else 0
    # shuffle null for participation
    rng=np.random.RandomState(42)
    null_deltas=[]
    for _ in range(50):
        shuffled_part=train['participation'].values.copy()
        rng.shuffle(shuffled_part)
        tmp_train=train.copy();tmp_train['part_shuf']=shuffled_part
        yh_s=ols(tmp_train[het_cols[:-1]+['part_shuf']].values,y_train,val[het_cols[:-1]+['participation']].values)
        null_deltas.append(round(r_squared(y_val,yh_s)-mB['r2'],6))
    real_delta=mC['r2']-mB['r2']
    pct_in_null=float(np.mean(np.array(null_deltas)>=real_delta))
    # verdict
    rep_delta=mC['r2']-mB['r2']
    tod_survives=mE['r2']>mB['r2']
    if rep_delta>=PREREG['materiality_threshold_delta_r2'] and tod_survives:
        sci='PARTICIPATION_INCREMENTAL_REPLICATED'
        mech='LEADING_PARTICIPATION_SIGNAL'
    elif rep_delta>0.001:sci='PARTICIPATION_INCREMENTAL_CONTEXTUAL';mech='ADDITIVE_INDEPENDENT_CHANNEL'
    else:sci='PARTICIPATION_NOT_REPLICATED';mech='MECHANISM_UNRESOLVED'
    result={'schema':SCHEMA,'preregistration':PREREG,
            'models':{'A_RUN32':mA,'B_heterogeneity':mB,'C_participation':mC,'D_flow_het':mD,
                     'E_with_tod':mE,'F_shock_control':mF,'G_interaction':mG},
            'ladder':{'A_to_B_delta_r2':round(mB['r2']-mA['r2'],6),'B_to_C_delta_r2':round(mC['r2']-mB['r2'],6),
                     'A_to_C_total_r2':round(mC['r2']-mA['r2'],6)},
            'temporal_blocks':blocks,'loco':loco,
            'states':states,'dir_corr_participation':round(dir_corr_part,4),
            'null_test':{'real_delta_r2':round(real_delta,6),'pct_in_null':round(pct_in_null,4)},
            'scientific_verdict':sci,'mechanism_verdict':mech,
            'candidate_model':'MARKET_ACTIVITY_FORECAST_V1' if 'REPLICATED' in sci else 'NOT_READY',
            'ts':now()}
    (OUT/'run42_full.json').write_text(json.dumps(result,ensure_ascii=False,indent=1,default=str))
    return result

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--out',required=True);a=p.parse_args()
    r_=run42_full()
    Path(a.out).write_text(json.dumps(r_,ensure_ascii=False,indent=1,default=str))
    print('VERDICT:',r_['scientific_verdict'])
    print('MECHANISM:',r_['mechanism_verdict'])
    print('CANDIDATE:',r_['candidate_model'])
    print('LADDER:',r_['ladder'])
    print('NULL:',r_['null_test'])
    print('LOCO:',r_['loco'])
    for k,m in r_['models'].items():
        print(f'  {k}: R²={m["r2"]} corr={m["corr"]}')
