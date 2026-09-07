#!/usr/bin/env python3
"""RUN-41: Participation / Delta / CVD / OI × Magnitude Heterogeneity.
Deterministic. Paper only. No LLM. No strategy optimization.

Stage A: Data feasibility. Stage B: Scientific test.
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

SCHEMA='run41-flow-magnitude-heterogeneity-v1'
OUT=Path('/root/audits/strategy_combine_research_intelligence/run41')
OUT.mkdir(parents=True,exist_ok=True)
def now():return time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())

def r_squared(y,yhat):
    ss_res=((y-yhat)**2).sum();ss_tot=((y-y.mean())**2).sum()
    return 1-ss_res/max(ss_tot,1e-12)

# ================================================== STAGE A: DATA FEASIBILITY ===
def audit_data_feasibility():
    raw=q15.load_all()
    audit={}
    for t,ds in raw.items():
        d=ds['train']
        cols=list(d.columns)
        audit[t]={'shape':d.shape,'columns':cols,
                  'has_volume':'volume' in cols,
                  'has_buy_vol':'buy_volume' in cols or 'buy_vol' in cols,
                  'has_sell_vol':'sell_volume' in cols or 'sell_vol' in cols,
                  'has_trades':'trades' in cols or 'trade_count' in cols,
                  'has_oi':'open_interest' in cols or 'oi' in cols,
                  'has_delta':'delta' in cols,
                  'has_cvd':'cvd' in cols}
    return audit

# ================================================== STAGE B: SCIENTIFIC TEST ===
def load_ret():
    raw=q15.load_all()
    rets={};vols={}
    for t in ['IMOEX','SBER','LKOH','CNY']:
        if t not in raw:continue
        d=raw[t]['train'].copy()
        if 'time' in d.columns:
            d['time']=pd.to_datetime(d['time']);d=d.set_index('time')
        rets[t]=np.log(d.close/d.close.shift())
        vols[t]=d
    return rets,vols

def build_features(rets,raw_data):
    imoex=rets['IMOEX']
    target=imoex.abs().shift(-1)
    # baseline: full RUN-32
    vol_24h=imoex.rolling(24).std()
    range_24h=imoex.abs().rolling(24).mean()
    vol_pctile=vol_24h.expanding(min_periods=120).rank(pct=True)
    vol_zscore=(vol_24h-vol_24h.expanding(min_periods=120).mean())/vol_24h.expanding(min_periods=120).std().replace(0,np.nan)
    amihud_zscore=pd.Series(0,index=imoex.index)
    # magnitude heterogeneity (RUN-40 confirmed)
    comp_ret=pd.DataFrame({t:rets[t] for t in ['SBER','LKOH','CNY'] if t in rets}).reindex(imoex.index)
    mag_het=comp_ret.abs().std(axis=1)
    mag_het_z=(mag_het-mag_het.expanding(min_periods=120).mean())/mag_het.expanding(min_periods=120).std().replace(0,np.nan)
    # flow features (from actual data)
    imoex_raw=raw_data.get('IMOEX',{})
    flow_features={}
    # volume-based
    if 'volume' in imoex_raw:
        vol_raw=imoex_raw['volume'].reindex(imoex.index) if hasattr(imoex_raw['volume'],'reindex') else pd.Series(0,index=imoex.index)
        # P1: relative volume
        vol_mean=vol_raw.expanding(min_periods=120).mean()
        vol_std=vol_raw.expanding(min_periods=120).std().replace(0,np.nan)
        rel_vol=(vol_raw-vol_mean)/vol_std
        flow_features['rel_volume']=rel_vol
        # derived delta proxy from volume * sign(return)
        delta_proxy=vol_raw*np.sign(imoex)
        cvd=delta_proxy.cumsum()
        cvd_change=cvd.diff(24)
        cvd_z=(cvd_change-cvd_change.expanding(min_periods=120).mean())/cvd_change.expanding(min_periods=120).std().replace(0,np.nan)
        flow_features['cvd_change']=cvd_z
        # normalized delta
        norm_delta=(delta_proxy.abs()/(vol_raw+1e-10))
        flow_features['norm_delta']=norm_delta
    # cross-sectional flow heterogeneity
    if len(flow_features)>0 and len(comp_ret.columns)>=2:
        vol_cs=pd.DataFrame({t:raw_data[t]['volume'].reindex(imoex.index) if 'volume' in raw_data[t] else pd.Series(0,index=imoex.index) for t in ['SBER','LKOH','CNY'] if t in raw_data})
        flow_het=vol_cs.std(axis=1)
        flow_het_z=(flow_het-flow_het.expanding(min_periods=120).mean())/flow_het.expanding(min_periods=120).std().replace(0,np.nan)
        flow_features['flow_heterogeneity']=flow_het_z
    # build dataframe
    df=pd.DataFrame({'target':target,'vol_24h':vol_24h,'range_24h':range_24h,
                    'vol_pctile':vol_pctile,'vol_zscore':vol_zscore,'amihud_zscore':amihud_zscore,
                    'mag_het':mag_het_z})
    for k,v in flow_features.items():
        df[k]=v
    df=df.dropna()
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
            'mae':round(float(np.mean(np.abs(y_test-yhat))),6)}

def run41_full():
    # Stage A
    feasibility=audit_data_feasibility()
    imoex_audit=feasibility.get('IMOEX',{})
    available_flow=[k for k,v in imoex_audit.items() if v and k.startswith('has_')]
    # Stage B
    rets,raw_data=load_ret()
    if 'IMOEX' not in rets:return {'error':'no IMOEX'}
    df=build_features(rets,raw_data)
    if len(df)<200:return {'error':'insufficient data'}
    n=len(df);split=int(n*0.6)
    train=df.iloc[:split];test=df.iloc[split:]
    y_train=train['target'].values;y_test=test['target'].values
    base_cols=['vol_24h','range_24h','vol_pctile','vol_zscore','amihud_zscore','mag_het']
    flow_cols=[c for c in df.columns if c in ['rel_volume','cvd_change','norm_delta','flow_heterogeneity']]
    # Model A: full baseline + magnitude heterogeneity
    yhat_A=ols(train[base_cols].values,y_train,test[base_cols].values)
    mA=eval_model(y_test,yhat_A)
    # Model B: A + participation (rel_volume)
    if 'rel_volume' in flow_cols:
        yhat_B=ols(train[base_cols+['rel_volume']].values,y_train,test[base_cols+['rel_volume']].values)
        mB=eval_model(y_test,yhat_B)
    else:mB={'r2':0,'corr':0,'mae':0}
    # Model C: A + delta/CVD
    delta_cols=[c for c in ['cvd_change','norm_delta'] if c in flow_cols]
    if delta_cols:
        yhat_C=ols(train[base_cols+delta_cols].values,y_train,test[base_cols+delta_cols].values)
        mC=eval_model(y_test,yhat_C)
    else:mC={'r2':0,'corr':0,'mae':0}
    # Model D: A + flow heterogeneity
    if 'flow_heterogeneity' in flow_cols:
        yhat_D=ols(train[base_cols+['flow_heterogeneity']].values,y_train,test[base_cols+['flow_heterogeneity']].values)
        mD=eval_model(y_test,yhat_D)
    else:mD={'r2':0,'corr':0,'mae':0}
    # Model E: all flow
    all_flow=[c for c in flow_cols if c in df.columns]
    if all_flow:
        yhat_E=ols(train[base_cols+all_flow].values,y_train,test[base_cols+all_flow].values)
        mE=eval_model(y_test,yhat_E)
    else:mE=mA
    # mediation
    base_only_cols=['vol_24h','range_24h','vol_pctile','vol_zscore','amihud_zscore']
    if all_flow:
        yhat_base=ols(train[base_only_cols].values,y_train,test[base_only_cols].values)
        mBase=eval_model(y_test,yhat_base)
        yhat_flow_only=ols(train[all_flow].values,y_train,test[all_flow].values)
        mFlowOnly=eval_model(y_test,yhat_flow_only)
        # heterogeneity after flow
        yhat_flow_het=ols(train[all_flow+['mag_het']].values,y_train,test[all_flow+['mag_het']].values)
        mFlowHet=eval_model(y_test,yhat_flow_het)
        het_remaining=mFlowHet['r2']-mFlowOnly['r2']
    else:
        mBase=mA;mFlowOnly={'r2':0};het_remaining=0
    # temporal blocks
    blocks=[]
    block_size=len(test)//3
    for i in range(3):
        s=i*block_size;e=min((i+1)*block_size,len(test))
        yt=y_test[s:e]
        models_t={}
        for name,cols in [('A',base_cols),('E',base_cols+all_flow if all_flow else base_cols)]:
            yh=ols(train[cols].values,y_train,yt)
            models_t[name]=round(r_squared(yt,yh),6)
        blocks.append({'block':i,'n':e-s,'r2_A':models_t['A'],'r2_E':models_t['E'],
                       'delta':round(models_t['E']-models_t['A'],6)})
    # information novelty
    redundancy={}
    for fc in all_flow:
        try:
            yh_pred=ols(train[base_cols].values,train[fc].values,train[base_cols].values)
            r2_feat=r_squared(train[fc].values,yh_pred) if len(train)>50 else 0
        except:r2_feat=0
        redundancy[fc]=round(r2_feat,4)
    # directional
    signed=rets['IMOEX'].shift(-1).dropna()
    common=df.index.intersection(signed.index)
    dir_corrs={}
    for fc in all_flow:
        if fc in df.columns:
            fc_vals=df.loc[common,fc].values;sf=signed.reindex(common).values
            mask=~np.isnan(fc_vals)&~np.isnan(sf)
            dir_corrs[fc]=round(float(np.corrcoef(fc_vals[mask],sf[mask])[0,1]),4) if mask.sum()>50 else 0
    # verdict
    best_flow_delta=max((mB['r2'],mC['r2'],mD['r2']),default=mA['r2'])-mA['r2']
    if best_flow_delta>0.01:sci='FLOW_INFORMATION_INCREMENTAL_STRONG'
    elif best_flow_delta>0.001:sci='FLOW_INFORMATION_INCREMENTAL_CONTEXTUAL'
    elif best_flow_delta>-0.001:sci='FLOW_REDUNDANT'
    else:sci='NO_INCREMENTAL_FLOW_INFORMATION'
    result={'schema':SCHEMA,'stage_A_feasibility':feasibility,'available_flow_fields':available_flow,
            'flow_data_contract':{'rel_volume':'AVAILABLE' if 'rel_volume' in flow_cols else 'UNAVAILABLE',
                                 'cvd_change':'AVAILABLE' if 'cvd_change' in flow_cols else 'UNAVAILABLE',
                                 'norm_delta':'AVAILABLE' if 'norm_delta' in flow_cols else 'UNAVAILABLE',
                                 'flow_heterogeneity':'AVAILABLE' if 'flow_heterogeneity' in flow_cols else 'UNAVAILABLE'},
            'models':{'A_baseline+heterogeneity':mA,'B_participation':mB,'C_delta_CVD':mC,
                     'D_flow_heterogeneity':mD,'E_all_flow':mE},
            'delta_vs_baseline':{'participation':round(mB['r2']-mA['r2'],6),'delta_CVD':round(mC['r2']-mA['r2'],6),
                                'flow_het':round(mD['r2']-mA['r2'],6),'all_flow':round(mE['r2']-mA['r2'],6)},
            'mediation':{'base_r2':round(mBase['r2'],6),'flow_only_r2':round(mFlowOnly['r2'],6),
                        'heterogeneity_after_flow':round(het_remaining,6)},
            'redundancy':redundancy,'temporal_blocks':blocks,'dir_corrs':dir_corrs,
            'scientific_verdict':sci,'ts':now()}
    (OUT/'run41_full.json').write_text(json.dumps(result,ensure_ascii=False,indent=1,default=str))
    return result

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--out',required=True);a=p.parse_args()
    r_=run41_full()
    Path(a.out).write_text(json.dumps(r_,ensure_ascii=False,indent=1,default=str))
    print('VERDICT:',r_['scientific_verdict'])
    print('DELTA vs BASELINE:',r_['delta_vs_baseline'])
    print('MEDIATION:',r_['mediation'])
    print('CONTRACT:',r_['flow_data_contract'])
