#!/usr/bin/env python3
"""RUN-40: Dispersion Mechanism Resolution.
Deterministic. Paper only. No LLM. No strategy optimization.

Decomposes dispersion into sign disagreement, magnitude heterogeneity, outlier concentration.
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

SCHEMA='run40-dispersion-mechanism-v1'
OUT=Path('/root/audits/strategy_combine_research_intelligence/run40')
OUT.mkdir(parents=True,exist_ok=True)
def now():return time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())

PREREG={
 'universe':['SBER','LKOH','CNY'],
 'M1_sign_disagreement':'1 - abs(mean(sign(component_returns)))',
 'M2_magnitude_heterogeneity':'std(abs(component_returns))',
 'M3_outlier_concentration':'top-1 share of total abs deviation',
 'target':'abs_return_1h',
 'baseline':'full RUN-32 (vol_24h,range_24h,vol_pctile,vol_zscore,amihud_zscore)',
 'oos_split':'chronological 60/40',
}
PREREG_PATH=OUT/'RUN40_PREREGISTRATION.json'

def r_squared(y,yhat):
    ss_res=((y-yhat)**2).sum();ss_tot=((y-y.mean())**2).sum()
    return 1-ss_res/max(ss_tot,1e-12)

def load_ret():
    raw=q15.load_all()
    rets={}
    for t in ['IMOEX','SBER','LKOH','CNY']:
        if t not in raw:continue
        d=raw[t]['train'].copy()
        if 'time' in d.columns:
            d['time']=pd.to_datetime(d['time']);d=d.set_index('time')
        rets[t]=np.log(d.close/d.close.shift())
    return rets

def build_all_features(rets):
    imoex=rets['IMOEX']
    target=imoex.abs().shift(-1)
    # baseline
    vol_24h=imoex.rolling(24).std()
    range_24h=imoex.abs().rolling(24).mean()
    vol_pctile=vol_24h.expanding(min_periods=120).rank(pct=True)
    vol_zscore=(vol_24h-vol_24h.expanding(min_periods=120).mean())/vol_24h.expanding(min_periods=120).std().replace(0,np.nan)
    amihud_zscore=pd.Series(0,index=imoex.index)
    # components
    comp_ret=pd.DataFrame({t:rets[t] for t in ['SBER','LKOH','CNY'] if t in rets})
    comp_ret=comp_ret.reindex(imoex.index)
    # total dispersion
    total_disp=comp_ret.std(axis=1)
    total_disp_z=(total_disp-total_disp.expanding(min_periods=120).mean())/total_disp.expanding(min_periods=120).std().replace(0,np.nan)
    # M1: sign disagreement
    signs=np.sign(comp_ret.values)
    m1=1-np.abs(np.nanmean(signs,axis=1))
    m1_series=pd.Series(m1,index=imoex.index)
    m1_z=(m1_series-m1_series.expanding(min_periods=120).mean())/m1_series.expanding(min_periods=120).std().replace(0,np.nan)
    # M2: magnitude heterogeneity
    m2_raw=comp_ret.abs().std(axis=1)
    m2_z=(m2_raw-m2_raw.expanding(min_periods=120).mean())/m2_raw.expanding(min_periods=120).std().replace(0,np.nan)
    # M3: outlier concentration (top-1 share of abs deviation from cross-sectional mean)
    cs_mean=comp_ret.mean(axis=1)
    abs_dev=(comp_ret.sub(cs_mean,axis=0)).abs()
    total_abs_dev=abs_dev.sum(axis=1)
    top1_dev=abs_dev.max(axis=1)
    m3_raw=top1_dev/total_abs_dev.replace(0,np.nan)
    m3_z=(m3_raw-m3_raw.expanding(min_periods=120).mean())/m3_raw.expanding(min_periods=120).std().replace(0,np.nan)
    df=pd.DataFrame({'target':target,
                     'vol_24h':vol_24h,'range_24h':range_24h,'vol_pctile':vol_pctile,
                     'vol_zscore':vol_zscore,'amihud_zscore':amihud_zscore,
                     'total_disp':total_disp_z,'m1_sign':m1_z,'m2_mag':m2_z,'m3_outlier':m3_z}).dropna()
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

def run40_full():
    PREREG_PATH.write_text(json.dumps(PREREG,indent=1))
    rets=load_ret()
    if 'IMOEX' not in rets:return {'error':'no IMOEX'}
    df=build_all_features(rets)
    if len(df)<200:return {'error':'insufficient data'}
    n=len(df);split=int(n*0.6)
    train=df.iloc[:split];test=df.iloc[split:]
    y_train=train['target'].values;y_test=test['target'].values
    base_cols=['vol_24h','range_24h','vol_pctile','vol_zscore','amihud_zscore']
    # orthogonality
    ortho={}
    for a,b in [('total_disp','m1_sign'),('total_disp','m2_mag'),('total_disp','m3_outlier'),
                ('m1_sign','m2_mag'),('m1_sign','m3_outlier'),('m2_mag','m3_outlier')]:
        mask=train[a].notna()&train[b].notna()
        if mask.sum()>50:
            ortho[f'{a}_vs_{b}']=round(float(np.corrcoef(train[a][mask],train[b][mask])[0,1]),4)
    # Model A: baseline
    yhat_A=ols(train[base_cols].values,y_train,test[base_cols].values)
    mA=eval_model(y_test,yhat_A)
    # Model B: baseline + M1
    yhat_B=ols(train[base_cols+['m1_sign']].values,y_train,test[base_cols+['m1_sign']].values)
    mB=eval_model(y_test,yhat_B)
    # Model C: baseline + M2
    yhat_C=ols(train[base_cols+['m2_mag']].values,y_train,test[base_cols+['m2_mag']].values)
    mC=eval_model(y_test,yhat_C)
    # Model D: baseline + M3
    yhat_D=ols(train[base_cols+['m3_outlier']].values,y_train,test[base_cols+['m3_outlier']].values)
    mD=eval_model(y_test,yhat_D)
    # Model E: baseline + total dispersion (reference)
    yhat_E=ols(train[base_cols+['total_disp']].values,y_train,test[base_cols+['total_disp']].values)
    mE=eval_model(y_test,yhat_E)
    # Model F: baseline + M1 + M2 + M3 (all mechanisms)
    yhat_F=ols(train[base_cols+['m1_sign','m2_mag','m3_outlier']].values,y_train,test[base_cols+['m1_sign','m2_mag','m3_outlier']].values)
    mF=eval_model(y_test,yhat_F)
    # Model G: baseline + total_disp + M1 (mediation)
    yhat_G=ols(train[base_cols+['total_disp','m1_sign']].values,y_train,test[base_cols+['total_disp','m1_sign']].values)
    mG=eval_model(y_test,yhat_G)
    # Model H: baseline + total_disp + M2
    yhat_H=ols(train[base_cols+['total_disp','m2_mag']].values,y_train,test[base_cols+['total_disp','m2_mag']].values)
    mH=eval_model(y_test,yhat_H)
    # Model I: baseline + total_disp + M3
    yhat_I=ols(train[base_cols+['total_disp','m3_outlier']].values,y_train,test[base_cols+['total_disp','m3_outlier']].values)
    mI=eval_model(y_test,yhat_I)
    # mediation
    orig_inc=mE['r2']-mA['r2']
    med_m1=mG['r2']-mB['r2']  # remaining dispersion after M1
    med_m2=mH['r2']-mC['r2']  # remaining dispersion after M2
    med_m3=mI['r2']-mD['r2']  # remaining dispersion after M3
    # residual volatility test
    fut_vol=rets['IMOEX'].abs().shift(-1)
    vol_resid=fut_vol-ols(train[base_cols].values,fut_vol.dropna().values.reindex(df.index).dropna().values,fut_vol.reindex(df.index).dropna().values) if False else None
    # directional independence
    signed=rets['IMOEX'].shift(-1).dropna()
    common=df.index.intersection(signed.index)
    dir_corrs={}
    for feat,col in [('m1','m1_sign'),('m2','m2_mag'),('m3','m3_outlier')]:
        fc=df.loc[common,col].values;sf=signed.reindex(common).values
        mask=~np.isnan(fc)&~np.isnan(sf)
        dir_corrs[feat]=round(float(np.corrcoef(fc[mask],sf[mask])[0,1]),4) if mask.sum()>50 else 0
    # temporal blocks
    blocks=[]
    block_size=len(test)//3
    for i in range(3):
        s=i*block_size;e=min((i+1)*block_size,len(test))
        yt=y_test[s:e]
        models_t={}
        for name,feat_cols in [('A',base_cols),('B',base_cols+['m1_sign']),
                               ('C',base_cols+['m2_mag']),('D',base_cols+['m3_outlier']),
                               ('E',base_cols+['total_disp'])]:
            yh=ols(train[feat_cols].values,y_train,y_test[s:e])
            models_t[name]=round(r_squared(yt,yh),6)
        blocks.append({'block':i,'n':e-s,'models':models_t,
                       'delta_m1':round(models_t['B']-models_t['A'],6),
                       'delta_m2':round(models_t['C']-models_t['A'],6),
                       'delta_m3':round(models_t['D']-models_t['A'],6),
                       'delta_disp':round(models_t['E']-models_t['A'],6)})
    # broad vs concentrated
    median_conc=train['m3_outlier'].median()
    test_broad=test[test['m3_outlier']<=median_conc]
    test_conc=test[test['m3_outlier']>median_conc]
    broad_r2=None;conc_r2=None
    if len(test_broad)>30:
        yh_b=ols(train[base_cols+['total_disp']].values,y_train,test_broad[base_cols+['total_disp']].values)
        broad_r2=round(r_squared(test_broad['target'].values,yh_b),6)
    if len(test_conc)>30:
        yh_c=ols(train[base_cols+['total_disp']].values,y_train,test_conc[base_cols+['total_disp']].values)
        conc_r2=round(r_squared(test_conc['target'].values,yh_c),6)
    # verdict
    best_mech=max([('M1',mB['r2']-mA['r2']),('M2',mC['r2']-mA['r2']),('M3',mD['r2']-mA['r2'])],key=lambda x:x[1])
    if best_mech[1]>0.02:sci=f'DISPERSION_{best_mech[0]}_EXPLAINED'
    elif mF['r2']-mA['r2']>0.02:sci='DISPERSION_MIXED_MECHANISM'
    elif mE['r2']-mA['r2']>0.01:sci='DISPERSION_MECHANISM_UNRESOLVED'
    else:sci='NO_DISPERSION_EFFECT'
    result={'schema':SCHEMA,'preregistration':PREREG,
            'orthogonality':ortho,
            'models':{'A_baseline':mA,'B_M1_sign':mB,'C_M2_mag':mC,'D_M3_outlier':mD,
                     'E_total_disp':mE,'F_all_mech':mF,
                     'G_disp+M1':mG,'H_disp+M2':mH,'I_disp+M3':mI},
            'delta_vs_baseline':{'M1':round(mB['r2']-mA['r2'],6),'M2':round(mC['r2']-mA['r2'],6),
                                'M3':round(mD['r2']-mA['r2'],6),'total_disp':round(mE['r2']-mA['r2'],6),
                                'all_mech':round(mF['r2']-mA['r2'],6)},
            'mediation':{'original_dispersion_increment':round(orig_inc,6),
                        'remaining_after_M1':round(med_m1,6),'remaining_after_M2':round(med_m2,6),
                        'remaining_after_M3':round(med_m3,6)},
            'broad_vs_concentrated':{'broad_r2':broad_r2,'concentrated_r2':conc_r2},
            'temporal_blocks':blocks,'dir_corrs':dir_corrs,
            'best_mechanism':best_mech[0],'scientific_verdict':sci,'ts':now()}
    (OUT/'run40_full.json').write_text(json.dumps(result,ensure_ascii=False,indent=1,default=str))
    return result

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--out',required=True);a=p.parse_args()
    r_=run40_full()
    Path(a.out).write_text(json.dumps(r_,ensure_ascii=False,indent=1,default=str))
    print('VERDICT:',r_['scientific_verdict'])
    print('BEST MECHANISM:',r_['best_mechanism'])
    print('DELTA vs BASELINE:',r_['delta_vs_baseline'])
    print('MEDIATION:',r_['mediation'])
    print('BROAD vs CONC:',r_['broad_vs_concentrated'])
