#!/usr/bin/env python3
"""RUN-38: Cross-Sectional Market Internals → Future IMOEX Volatility/Range.
Deterministic. Paper only. No LLM. No strategy optimization.

Tests if component dispersion/sync/breadth add incremental info over RUN-32 baseline.
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

SCHEMA='run38-market-internals-v1'
OUT=Path('/root/audits/strategy_combine_research_intelligence/run38')
OUT.mkdir(parents=True,exist_ok=True)
def now():return time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())

PREREG={
 'universe':['IMOEX','SBER','LKOH','CNY'],
 'target':'abs_return_1h = |log(close[t+1]/close[t])|',
 'baseline_features':['vol_24h'],
 'M1_dispersion':'cross-sectional std of component returns, expanding z-score',
 'M2_synchronization':'mean pairwise correlation of component returns (24-bar rolling)',
 'M3_breadth':'fraction of components with |return| > median, expanding z-score',
 'forecast_horizon':1,
 'oos_split':'chronological 60/40',
 'min_delta_r2':0.001,
}
PREREG_PATH=OUT/'RUN38_PREREGISTRATION.json'

def r_squared(y,yhat):
    ss_res=((y-yhat)**2).sum();ss_tot=((y-y.mean())**2).sum()
    return 1-ss_res/max(ss_tot,1e-12)

def load_all():
    raw=q15.load_all()
    rets={}
    for t in PREREG['universe']:
        if t not in raw:continue
        d=raw[t]['train'].copy()
        if 'time' in d.columns:
            d['time']=pd.to_datetime(d['time']);d=d.set_index('time')
        rets[t]=np.log(d.close/d.close.shift())
    # align all to IMOEX index
    if 'IMOEX' not in rets:return None
    idx=rets['IMOEX'].index
    aligned={}
    for t,r in rets.items():
        aligned[t]=r.reindex(idx)
    return aligned

def build_features(aligned):
    """Build baseline + internal features aligned to IMOEX."""
    imoex_ret=aligned['IMOEX']
    # target: future absolute return
    target=imoex_ret.abs().shift(-1)
    # baseline: vol_24h
    vol_24h=imoex_ret.rolling(24).std()
    # M1: cross-sectional dispersion
    ret_df=pd.DataFrame(aligned)
    m1_raw=ret_df.std(axis=1)
    m1_z=(m1_raw-m1_raw.expanding(min_periods=120).mean())/m1_raw.expanding(min_periods=120).std().replace(0,np.nan)
    # M2: synchronization (mean pairwise correlation, 24-bar rolling)
    m2_series=pd.Series(index=imoex_ret.index,dtype=float)
    for i in range(24,len(ret_df)):
        window=ret_df.iloc[i-24:i].dropna(axis=1)
        if window.shape[1]<2:m2_series.iloc[i]=np.nan;continue
        corr_mat=window.corr()
        n=corr_mat.shape[0]
        if n>1:
            mask=np.triu(np.ones((n,n),dtype=bool),k=1)
            m2_series.iloc[i]=corr_mat.values[mask].mean()
    m2_z=(m2_series-m2_series.expanding(min_periods=120).mean())/m2_series.expanding(min_periods=120).std().replace(0,np.nan)
    # M3: breadth (fraction of components with |return| > median)
    abs_ret_df=ret_df.abs()
    median_row=abs_ret_df.median(axis=1)
    breadth=(abs_ret_df.gt(median_row,axis=0)).sum(axis=1)/ret_df.shape[1]
    m3_z=(breadth-breadth.expanding(min_periods=120).mean())/breadth.expanding(min_periods=120).std().replace(0,np.nan)
    df=pd.DataFrame({'target':target,'vol_24h':vol_24h,'m1_dispersion':m1_z,'m2_sync':m2_z,'m3_breadth':m3_z}).dropna()
    return df

def ols_predict(y_train,X_train,y_test,X_test):
    X_=np.column_stack([np.ones(len(X_train)),X_train])
    try:
        beta=np.linalg.lstsq(X_,y_train,rcond=None)[0]
        X_t=np.column_stack([np.ones(len(X_test)),X_test])
        yhat=X_t@beta
        return yhat
    except:return np.full(len(y_test),y_train.mean())

def run38_full():
    PREREG_PATH.write_text(json.dumps(PREREG,indent=1))
    aligned=load_all()
    if not aligned:return {'error':'no data'}
    df=build_features(aligned)
    if df is None or len(df)<200:return {'error':'insufficient aligned data'}
    n=len(df);split=int(n*0.6)
    train=df.iloc[:split];test=df.iloc[split:]
    y_train=train['target'].values;y_test=test['target'].values
    # Model A: baseline (vol_24h only)
    X_baseline_train=train[['vol_24h']].values
    X_baseline_test=test[['vol_24h']].values
    yhat_A=ols_predict(y_train,X_baseline_train,y_test,X_baseline_test)
    r2_A=r_squared(y_test,yhat_A)
    corr_A=float(np.corrcoef(y_test,yhat_A)[0,1])
    mae_A=float(np.mean(np.abs(y_test-yhat_A)))
    # Model B: baseline + M1
    X_B_train=train[['vol_24h','m1_dispersion']].values
    X_B_test=test[['vol_24h','m1_dispersion']].values
    yhat_B=ols_predict(y_train,X_B_train,y_test,X_B_test)
    r2_B=r_squared(y_test,yhat_B)
    corr_B=float(np.corrcoef(y_test,yhat_B)[0,1])
    mae_B=float(np.mean(np.abs(y_test-yhat_B)))
    # Model C: baseline + M2
    X_C_train=train[['vol_24h','m2_sync']].values
    X_C_test=test[['vol_24h','m2_sync']].values
    yhat_C=ols_predict(y_train,X_C_train,y_test,X_C_test)
    r2_C=r_squared(y_test,yhat_C)
    corr_C=float(np.corrcoef(y_test,yhat_C)[0,1])
    mae_C=float(np.mean(np.abs(y_test-yhat_C)))
    # Model D: baseline + M3
    X_D_train=train[['vol_24h','m3_breadth']].values
    X_D_test=test[['vol_24h','m3_breadth']].values
    yhat_D=ols_predict(y_train,X_D_train,y_test,X_D_test)
    r2_D=r_squared(y_test,yhat_D)
    corr_D=float(np.corrcoef(y_test,yhat_D)[0,1])
    mae_D=float(np.mean(np.abs(y_test-yhat_D)))
    # Model E: all
    X_E_train=train[['vol_24h','m1_dispersion','m2_sync','m3_breadth']].values
    X_E_test=test[['vol_24h','m1_dispersion','m2_sync','m3_breadth']].values
    yhat_E=ols_predict(y_train,X_E_train,y_test,X_E_test)
    r2_E=r_squared(y_test,yhat_E)
    corr_E=float(np.corrcoef(y_test,yhat_E)[0,1])
    mae_E=float(np.mean(np.abs(y_test-yhat_E)))
    # redundancy check: how much does vol_24h explain internals?
    redundancy={}
    for feat in ['m1_dispersion','m2_sync','m3_breadth']:
        r2_feat=r_squared(train[feat].values,ols_predict(train[feat].values,train[['vol_24h']].values,train[feat].values,train[['vol_24h']].values))
        redundancy[feat]=round(r2_feat,4)
    # directional independence
    signed_future=aligned['IMOEX'].shift(-1).dropna()
    dir_corrs={}
    for feat_name,feat_col in [('m1','m1_dispersion'),('m2','m2_sync'),('m3','m3_breadth')]:
        common=df.index.intersection(signed_future.index)
        fc=df.loc[common,feat_col].values
        sf=signed_future.reindex(common).values
        mask=~np.isnan(fc)&~np.isnan(sf)
        if mask.sum()>50:dir_corrs[feat_name]=round(float(np.corrcoef(fc[mask],sf[mask])[0,1]),4)
    # results
    models={'A_baseline':{'r2':round(r2_A,6),'corr':round(corr_A,6),'mae':round(mae_A,6),'delta_r2':0,'delta_corr':0},
            'B_M1_dispersion':{'r2':round(r2_B,6),'corr':round(corr_B,6),'mae':round(mae_B,6),'delta_r2':round(r2_B-r2_A,6),'delta_corr':round(corr_B-corr_A,6)},
            'C_M2_sync':{'r2':round(r2_C,6),'corr':round(corr_C,6),'mae':round(mae_C,6),'delta_r2':round(r2_C-r2_A,6),'delta_corr':round(corr_C-corr_A,6)},
            'D_M3_breadth':{'r2':round(r2_D,6),'corr':round(corr_D,6),'mae':round(mae_D,6),'delta_r2':round(r2_D-r2_A,6),'delta_corr':round(corr_D-corr_A,6)},
            'E_all':{'r2':round(r2_E,6),'corr':round(corr_E,6),'mae':round(mae_E,6),'delta_r2':round(r2_E-r2_A,6),'delta_corr':round(corr_E-corr_A,6)}}
    best_delta=max(m['delta_r2'] for k,m in models.items() if k!='A_baseline')
    best_model=max([(k,m) for k,m in models.items() if k!='A_baseline'],key=lambda x:x[1]['delta_r2'])
    if best_delta>0.005:overall='MARKET_INTERNALS_INCREMENTAL_STRONG'
    elif best_delta>PREREG['min_delta_r2']:overall='MARKET_INTERNALS_INCREMENTAL_CONTEXTUAL'
    elif best_delta>-0.001:overall='MARKET_INTERNALS_REDUNDANT'
    else:overall='NO_INCREMENTAL_MARKET_INTERNALS'
    result={'schema':SCHEMA,'preregistration':PREREG,
            'n_assets':len([t for t in PREREG['universe'] if t in aligned]),
            'models':models,'redundancy':redundancy,'dir_corrs':dir_corrs,
            'best_incremental_model':best_model[0],'best_delta_r2':round(best_delta,6),
            'overall':overall,'ts':now()}
    (OUT/'run38_full.json').write_text(json.dumps(result,ensure_ascii=False,indent=1,default=str))
    return result

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--out',required=True);a=p.parse_args()
    r_=run38_full()
    Path(a.out).write_text(json.dumps(r_,ensure_ascii=False,indent=1,default=str))
    print('OVERALL:',r_['overall'])
    print('BEST INCREMENTAL:',r_['best_incremental_model'],'ΔR²=',r_['best_delta_r2'])
    for k,m in r_['models'].items():
        print(f'  {k}: R²={m["r2"]} corr={m["corr"]} ΔR²={m["delta_r2"]}')
    print('REDUNDANCY:',r_['redundancy'])
    print('DIR CORRS:',r_['dir_corrs'])
