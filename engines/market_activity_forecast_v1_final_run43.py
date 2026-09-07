#!/usr/bin/env python3
"""RUN-43: MARKET_ACTIVITY_FORECAST_V1 Final Calibration & State Map.
Deterministic. Paper only. No LLM. No strategy optimization.

Calibrates V1 model and produces market state map.
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

SCHEMA='run43-market-activity-forecast-v1-final'
OUT=Path('/root/audits/strategy_combine_research_intelligence/run43')
OUT.mkdir(parents=True,exist_ok=True)
def now():return time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())

V1_SPEC={
 'features':['vol_24h','range_24h','vol_pctile','vol_zscore','amihud_zscore',
             'mag_het','participation','flow_het','hour_sin','hour_cos'],
 'target':'abs_return_1h',
 'horizon':1,
 'fitting':'OLS',
 'oos':'chronological 60/40',
 'large_move_threshold':'80th percentile of abs_return from train',
}
SPEC_PATH=OUT/'MARKET_ACTIVITY_FORECAST_V1_SPEC.json'

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
    target_range=((raw_data['IMOEX']['high'].shift(-1)-raw_data['IMOEX']['low'].shift(-1))/raw_data['IMOEX']['close'].shift(-1)) if 'high' in raw_data['IMOEX'].columns else target
    # baseline
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
    vol_raw=raw_data['IMOEX']['volume'].reindex(imoex.index)
    vol_mean=vol_raw.expanding(min_periods=120).mean()
    vol_std=vol_raw.expanding(min_periods=120).std().replace(0,np.nan)
    rel_vol=(vol_raw-vol_mean)/vol_std
    # flow heterogeneity
    vol_cs=pd.DataFrame({t:raw_data[t]['volume'].reindex(imoex.index) for t in ['SBER','LKOH','CNY'] if t in raw_data and 'volume' in raw_data[t].columns}).reindex(imoex.index)
    flow_het=vol_cs.std(axis=1)
    flow_het_z=(flow_het-flow_het.expanding(min_periods=120).mean())/flow_het.expanding(min_periods=120).std().replace(0,np.nan)
    # time-of-day
    hour=pd.Series(imoex.index.hour,index=imoex.index)
    hour_sin=np.sin(2*np.pi*hour/24)
    hour_cos=np.cos(2*np.pi*hour/24)
    df=pd.DataFrame({'target':target,'target_range':target_range,
                    'vol_24h':vol_24h,'range_24h':range_24h,'vol_pctile':vol_pctile,
                    'vol_zscore':vol_zscore,'amihud_zscore':amihud_zscore,
                    'mag_het':mag_het_z,'participation':rel_vol,'flow_het':flow_het_z,
                    'hour_sin':hour_sin,'hour_cos':hour_cos}).dropna()
    # signed return for directional check
    df['signed_future']=imoex.shift(-1).reindex(df.index)
    return df

def ols(X_train,y_train,X_test):
    X_=np.column_stack([np.ones(len(X_train)),X_train])
    try:
        beta=np.linalg.lstsq(X_,y_train,rcond=None)[0]
        X_t=np.column_stack([np.ones(len(X_test)),X_test])
        return X_t@beta, beta
    except:return np.full(len(X_test),y_train.mean()),None

def eval_model(y_test,yhat):
    return {'r2':round(r_squared(y_test,yhat),6),
            'corr':round(float(np.corrcoef(y_test,yhat)[0,1]),6),
            'mae':round(float(np.mean(np.abs(y_test-yhat))),6),
            'rmse':round(float(np.sqrt(np.mean((y_test-yhat)**2))),6)}

def run43_full():
    SPEC_PATH.write_text(json.dumps(V1_SPEC,indent=1))
    rets,raw_data=load_ret()
    if 'IMOEX' not in rets:return {'error':'no IMOEX'}
    df=build_features(rets,raw_data)
    if len(df)<200:return {'error':'insufficient data'}
    n=len(df);split=int(n*0.6)
    train=df.iloc[:split];test=df.iloc[split:]
    y_train=train['target'].values;y_test=test['target'].values
    y_range_test=test['target_range'].values
    signed_test=test['signed_future'].values
    feat_cols=V1_SPEC['features']
    # model
    yhat,beta=ols(train[feat_cols].values,y_train,test[feat_cols].values)
    metrics=eval_model(y_test,yhat)
    # large move
    thr_large=np.percentile(y_train,80)
    large_test=(y_test>=thr_large).astype(int)
    # probability calibration
    vol_norm=(yhat-yhat.min())/(yhat.max()-yhat.min()+1e-10)
    brier=float(np.mean((large_test-vol_norm)**2))
    # state map (4 states)
    q25=np.percentile(yhat,25);q50=np.percentile(yhat,50);q75=np.percentile(yhat,75)
    states={}
    for name,lo,hi in [('CALM',0,q25),('NORMAL',q25,q50),('ACTIVE',q50,q75),('EXTREME',q75,1e10)]:
        mask=(yhat>=lo)&(yhat<hi) if hi<1e10 else (yhat>=lo)
        if mask.sum()<10:continue
        states[name]={'n':int(mask.sum()),'coverage':round(mask.mean()*100,1),
                     'mean_predicted_vol':round(float(yhat[mask].mean()),6),
                     'mean_realized_vol':round(float(y_test[mask].mean()),6),
                     'mean_realized_range':round(float(y_range_test[mask].mean()),6) if len(y_range_test)==len(mask) else None,
                     'p_large_move':round(float(large_test[mask].mean()),4),
                     'forecast_error':round(float(np.mean(np.abs(y_test[mask]-yhat[mask]))),6)}
    # two-channel matrix
    med_het=train['mag_het'].median();med_part=train['participation'].median()
    matrix={}
    for sh,lo_h in [('LOW',False),('HIGH',True)]:
        for sp,lo_p in [('LOW',False),('HIGH',True)]:
            mask=((test['mag_het']>med_het)==lo_h)&((test['participation']>med_part)==lo_p)
            if mask.sum()>10:
                matrix[f'{sh}_HET_{sp}_PART']={'n':int(mask.sum()),
                    'mean_realized_vol':round(float(y_test[mask].mean()),6),
                    'p_large_move':round(float(large_test[mask].mean()),4)}
    # state ordering
    state_vol=[states[s]['mean_realized_vol'] for s in ['CALM','NORMAL','ACTIVE','EXTREME'] if s in states]
    monotonic=all(state_vol[i]<=state_vol[i+1] for i in range(len(state_vol)-1)) if len(state_vol)>=3 else False
    # directional independence
    dir_corr=float(np.corrcoef(yhat[:len(signed_test)],signed_test)[0,1]) if len(signed_test)==len(yhat) else 0
    # temporal stability
    blocks=[];bs=len(test)//3
    for i in range(3):
        s=i*bs;e=min((i+1)*bs,len(test))
        yt=y_test[s:e];yh=yhat[s:e]
        blocks.append({'block':i,'n':e-s,'r2':round(r_squared(yt,yh),6),
                       'corr':round(float(np.corrcoef(yt,yh)[0,1]),6)})
    # baseline ladder
    base_cols=['vol_24h','range_24h','vol_pctile','vol_zscore','amihud_zscore']
    ladder={}
    for name,cols in [('B0_mean',[]),('B1_RUN32',base_cols),
                      ('B2_het',base_cols+['mag_het']),
                      ('B3_part',base_cols+['mag_het','participation']),
                      ('B4_V1',feat_cols)]:
        if not cols:ladder[name]={'r2':round(r_squared(y_test,np.full(len(y_test),y_train.mean())),6)}
        else:
            yh,_=ols(train[cols].values,y_train,test[cols].values)
            ladder[name]={'r2':round(r_squared(y_test,yh),6),'corr':round(float(np.corrcoef(y_test,yh)[0,1]),6)}
    # confidence model
    support_counts=pd.Series(yhat).groupby(pd.cut(yhat,bins=20)).count()
    high_conf_threshold=support_counts.quantile(0.75)
    confidence=pd.Series('MEDIUM',index=test.index)
    confidence[pd.Series(yhat,index=test.index).groupby(pd.cut(yhat,bins=20)).transform('count')>high_conf_threshold]='HIGH'
    confidence[pd.Series(yhat,index=test.index).groupby(pd.cut(yhat,bins=20)).transform('count')<support_counts.quantile(0.25)]='LOW'
    # verdict
    if monotonic and metrics['r2']>0.1:sci='MARKET_ACTIVITY_FORECAST_V1_VALIDATED'
    elif metrics['r2']>0.05:sci='MARKET_ACTIVITY_FORECAST_V1_CONTEXTUAL'
    else:sci='V1_NOT_VALIDATED'
    # hash
    spec_hash=hashlib.md5(json.dumps(V1_SPEC,sort_keys=True).encode()).hexdigest()[:12]
    result={'schema':SCHEMA,'spec':V1_SPEC,'spec_hash':spec_hash,
            'final_metrics':metrics,'large_move_threshold':round(thr_large,6),
            'brier':round(brier,6),
            'state_map':states,'state_ordering_monotonic':monotonic,
            'two_channel_matrix':matrix,'dir_corr':round(dir_corr,4),
            'temporal_blocks':blocks,'baseline_ladder':ladder,
            'metric_lineage':{'RUN32_full':{'reported_r2':0.12,'reported_corr':0.41,'note':'different split/sample'},
                             'RUN39_full':{'reported_r2':0.144,'reported_corr':0.382},
                             'RUN42_independent':{'reported_r2':0.066,'reported_corr':0.263,'note':'independent validation'},
                             'RUN43_final':metrics},
            'scientific_verdict':sci,'ts':now()}
    (OUT/'run43_full.json').write_text(json.dumps(result,ensure_ascii=False,indent=1,default=str))
    # output contract sample
    output={'timestamp':now(),'horizon':'1h',
            'expected_volatility':round(float(yhat[-1]),6) if len(yhat)>0 else None,
            'large_move_probability':round(float(vol_norm[-1]),4) if len(vol_norm)>0 else None,
            'market_state':'EXTREME' if len(yhat)>0 and yhat[-1]>=q75 else ('ACTIVE' if len(yhat)>0 and yhat[-1]>=q50 else 'NORMAL'),
            'model_version':'V1','spec_hash':spec_hash}
    (OUT/'MARKET_ACTIVITY_FORECAST_V1_OUTPUT.json').write_text(json.dumps(output,indent=1))
    return result

if __name__=='__main__':
    import argparse,hashlib
    p=argparse.ArgumentParser();p.add_argument('--out',required=True);a=p.parse_args()
    r_=run43_full()
    Path(a.out).write_text(json.dumps(r_,ensure_ascii=False,indent=1,default=str))
    print('VERDICT:',r_['scientific_verdict'])
    print('METRICS:',r_['final_metrics'])
    print('MONOTONIC:',r_['state_ordering_monotonic'])
    print('LADDER:',r_['baseline_ladder'])
    for s,v in r_['state_map'].items():
        print(f'  {s}: n={v["n"]} vol={v["mean_realized_vol"]} p_large={v["p_large_move"]}')
