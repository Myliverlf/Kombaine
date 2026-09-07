#!/usr/bin/env python3
"""RUN-21: Time-Scale Falsification.
Deterministic. Paper only. No LLM. No strategy.

Tests whether hourly autocorrelation survives on daily IMOEX data.
"""
from __future__ import annotations
import json,hashlib,math,time
import numpy as np
import pandas as pd
from pathlib import Path
import sys
SC=Path('/root/prop-desk/strategy_combine');FL=Path('/root/prop-desk/futures_lab')
sys.path[:0]=[str(SC),str(FL),str(SC/'engines')]
import question_loop_run15 as q15

SCHEMA='run21-time-scale-falsification-v1'
OUT=Path('/root/audits/strategy_combine_research_intelligence/run21')
OUT.mkdir(parents=True,exist_ok=True)
def now():return time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())

# ================================================== PREREGISTRATION ===
PREREG={
 'return_definition':'log(daily_close/daily_close_prev)',
 'missing_days':'forward-fill last available price',
 'lags_tested':[1,2,3],
 'vol_measure':'expanding std of daily returns, window>=20',
 'regime_method':'expanding terciles 33/66',
 'oos_split':'chronological 60/40',
 'temporal_split':'chronological thirds',
 'min_sample':50,
 'significance_alpha':0.05,
 'bootstrap_blocks':10,'bootstrap_n':500,
 'verdicts':['TIME_SCALE_ARTIFACT_CONFIRMED','DAILY_DEPENDENCE_DESCRIPTIVE_ONLY',
             'DAILY_DEPENDENCE_DECAYING','DAILY_DEPENDENCE_PREDICTIVE','INCONCLUSIVE'],
}
PREREG_PATH=OUT/'RUN21_PREREGISTRATION.json'

# ================================================== LOAD DAILY IMOEX ===
def load_daily():
    ds=q15.load_all().get('IMOEX')
    if not ds:return None
    d=ds['train'].copy()
    # handle time column
    if 'time' in d.columns:
        d['time']=pd.to_datetime(d['time'])
        d=d.set_index('time')
    elif not isinstance(d.index,pd.DatetimeIndex):
        d.index=pd.to_datetime(d.index)
    # resample to daily: take last close per calendar day
    daily=d['close'].resample('D').last().dropna()
    ret=np.log(daily/daily.shift(1)).dropna()
    vol=ret.rolling(20,min_periods=10).std()
    # regime: expanding terciles
    q33=vol.expanding(min_periods=20).quantile(0.33).shift(1)
    q66=vol.expanding(min_periods=20).quantile(0.66).shift(1)
    regime=pd.Series('MID',index=ret.index)
    regime[vol<=q33]='LOW';regime[vol>=q66]='HIGH'
    df=pd.DataFrame({'ret':ret,'vol':vol,'regime':regime,
                     'r_lag1':ret.shift(1),'r_lag2':ret.shift(2),'r_lag3':ret.shift(3),
                     'abs_ret':ret.abs()}).dropna()
    return df

# ================================================== ACF HELPER ===
def acf_lags(ret_series,lags):
    r=ret_series.dropna();n=len(r)
    out={}
    for lag in lags:
        if n-lag<PREREG['min_sample']:out[lag]={'ac':0,'t':0,'n':0,'ci_lo':0,'ci_hi':0,'sig':False};continue
        x=r.iloc[lag:].values;y=r.iloc[:-lag].values
        ac=float(np.corrcoef(x,y)[0,1])
        se=1/math.sqrt(len(x))
        t=ac*math.sqrt(len(x)-2)/math.sqrt(max(1-ac*ac,1e-12))
        ci_lo=ac-1.96*se;ci_hi=ac+1.96*se
        out[lag]={'ac':round(ac,6),'t':round(float(t),4),'n':len(x),
                  'ci_lo':round(ci_lo,6),'ci_hi':round(ci_hi,6),'sig':abs(float(t))>1.96}
    return out

# ================================================== OLS ===
def ols(y,X):
    X_=np.column_stack([np.ones(len(X)),X])
    try:
        beta=np.linalg.lstsq(X_,y,rcond=None)[0]
        return {'beta':beta,'resid':y-X_@beta}
    except:return None

def r_squared(y,yhat):
    ss_res=((y-yhat)**2).sum();ss_tot=((y-y.mean())**2).sum()
    return 1-ss_res/max(ss_tot,1e-12)

# ================================================== TEST A: DAILY ACF ===
def testA_daily_acf(df):
    return acf_lags(df['ret'],PREREG['lags_tested'])

# ================================================== TEST B: VOLATILITY CONTROL ===
def testB_vol_control(df):
    raw=acf_lags(df['ret'],[1,3])
    # control: regress ret on abs_ret lag, take residual
    X=df[['abs_ret']].values;y=df['ret'].values
    m=ols(y,X)
    if m is None:return {'raw':raw,'controlled':{},'error':'ols_failed'}
    resid=m['resid']
    controlled=acf_lags(pd.Series(resid,index=df.index),[1,3])
    return {'raw':raw,'controlled':controlled,
            'vol_coef':round(float(m['beta'][1]),6)}

# ================================================== TEST C: REGIME ===
def testC_regime(df):
    results={}
    for rg in ['LOW','MID','HIGH']:
        mask=df['regime']==rg;sub=df[mask]
        if len(sub)<PREREG['min_sample']:results[rg]={'insufficient':True,'n':len(sub)};continue
        results[rg]={'n':len(sub),**acf_lags(sub['ret'],[1,3])}
    # interaction
    acs={k:v.get(1,{}).get('ac',0) for k,v in results.items() if not v.get('insufficient')}
    results['interaction']={'ac_range':round(max(acs.values())-min(acs.values()),6) if acs else 0,
                            'regime_acs':{k:round(v,6) for k,v in acs.items()}}
    return results

# ================================================== TEST D: TEMPORAL STABILITY ===
def testD_temporal(df):
    n=len(df);third=n//3
    splits={'first':df.iloc[:third],'second':df.iloc[third:2*third],'third':df.iloc[2*third:]}
    results={}
    for name,sub in splits.items():
        if len(sub)<PREREG['min_sample']:results[name]={'insufficient':True,'n':len(sub)};continue
        results[name]={'n':len(sub),**acf_lags(sub['ret'],[1,3])}
    return results

# ================================================== TEST E: OOS PREDICTION ===
def testE_oos(df):
    n=len(df);split=int(n*0.6)
    train=df.iloc[:split];test=df.iloc[split:]
    y_train=train['ret'].values;y_test=test['ret'].values
    results={}
    for name,(ft,fe) in {
        'M0':(np.zeros((len(train),0)),np.zeros((len(test),0))),
        'M1':(train[['r_lag1']].values,test[['r_lag1']].values),
        'M3':(train[['r_lag3']].values,test[['r_lag3']].values),
        'M13':(train[['r_lag1','r_lag3']].values,test[['r_lag1','r_lag3']].values)}.items():
        if ft.shape[1]==0:
            yhat=np.full(len(y_test),y_train.mean())
        else:
            m=ols(y_train,ft)
            if m is None:continue
            yhat=m['beta'][0]+fe@m['beta'][1:]
        r2=r_squared(y_test,yhat)
        corr=float(np.corrcoef(y_test,yhat)[0,1]) if len(y_test)>1 else 0
        sign_acc=float((np.sign(yhat)==np.sign(y_test)).mean()) if len(y_test)>0 else 0
        results[name]={'r2_oos':round(r2,6),'corr_oos':round(corr,6),'sign_accuracy':round(sign_acc,4),
                       'n_train':len(y_train),'n_test':len(y_test)}
    return results

# ================================================== TEST F: HOURLY VS DAILY ===
def testF_hourly_vs_daily(daily_results,hourly_results):
    """Compare qualitative evidence."""
    daily_ac1=daily_results.get(1,{}).get('ac',0)
    hourly_ac1=hourly_results.get('lag_results',{}).get(1,{}).get('autocorr',0) if isinstance(hourly_results,dict) else 0
    daily_ac3=daily_results.get(3,{}).get('ac',0)
    hourly_ac3=hourly_results.get('lag_results',{}).get(3,{}).get('autocorr',0) if isinstance(hourly_results,dict) else 0
    return {'daily_lag1':daily_ac1,'hourly_lag1':hourly_ac1,
            'daily_lag3':daily_ac3,'hourly_lag3':hourly_ac3,
            'direction_preserved_lag1':bool(np.sign(daily_ac1)==np.sign(hourly_ac1)) if hourly_ac1!=0 else None,
            'direction_preserved_lag3':bool(np.sign(daily_ac3)==np.sign(hourly_ac3)) if hourly_ac3!=0 else None,
            'magnitude_ratio_lag1':round(abs(daily_ac1)/max(abs(hourly_ac1),1e-12),4) if hourly_ac1!=0 else None}

# ================================================== VERDICT ===
def verdict(daily_acf,vol_control,regime,temporal,oos,comparison):
    lag1=daily_acf.get(1,{});lag3=daily_acf.get(3,{})
    lag1_sig=lag1.get('sig',False);lag3_sig=lag3.get('sig',False)
    # check vol control
    vc_l1=vol_control.get('controlled',{}).get(1,{}).get('ac',0)
    vol_control_survives=vc_l1<0 and abs(vc_l1)>0.005
    # check temporal
    stable=all(not temporal.get(s,{}).get('insufficient',True) and
               temporal.get(s,{}).get(1,{}).get('ac',0)<0
               for s in ['first','second','third'])
    # check OOS
    oos_r2_l1=oos.get('M1',{}).get('r2_oos',0)
    oos_corr_l1=oos.get('M1',{}).get('corr_oos',0)
    oos_positive=oos_r2_l1>0 and oos_corr_l1>0
    # verdict
    if not lag1_sig and not lag3_sig:
        return 'TIME_SCALE_ARTIFACT_CONFIRMED'
    if lag1_sig and not oos_positive:
        return 'DAILY_DEPENDENCE_DESCRIPTIVE_ONLY'
    if lag1_sig and oos_positive and not stable:
        return 'DAILY_DEPENDENCE_DECAYING'
    if lag1_sig and oos_positive and stable:
        return 'DAILY_DEPENDENCE_PREDICTIVE'
    return 'INCONCLUSIVE'

# ================================================== MAIN ===
def run21_full():
    PREREG_PATH.write_text(json.dumps(PREREG,indent=1))
    df=load_daily()
    if df is None or len(df)<100:return {'error':'insufficient daily IMOEX data'}
    # load hourly results for comparison
    try:
        hourly=json.loads(Path('/root/audits/strategy_combine_research_intelligence/run18/run18_full.json').read_text())
    except:hourly={}
    # tests
    daily_acf=testA_daily_acf(df)
    vol_control=testB_vol_control(df)
    regime=testC_regime(df)
    temporal=testD_temporal(df)
    oos=testE_oos(df)
    comparison=testF_hourly_vs_daily(daily_acf,hourly)
    v=verdict(daily_acf,vol_control,regime,temporal,oos,comparison)
    # save individual outputs
    (OUT/'daily_autocorrelation.json').write_text(json.dumps(daily_acf,indent=1,default=str))
    (OUT/'daily_volatility_control.json').write_text(json.dumps(vol_control,indent=1,default=str))
    (OUT/'daily_regimes.json').write_text(json.dumps(regime,indent=1,default=str))
    (OUT/'daily_temporal_stability.json').write_text(json.dumps(temporal,indent=1,default=str))
    (OUT/'daily_oos_prediction.json').write_text(json.dumps(oos,indent=1,default=str))
    (OUT/'hourly_vs_daily.json').write_text(json.dumps(comparison,indent=1,default=str))
    result={'schema':SCHEMA,'preregistration':PREREG,
            'testA':daily_acf,'testB':vol_control,'testC':regime,'testD':temporal,
            'testE':oos,'testF':comparison,'verdict':v,
            'n_daily':len(df),'date_range':f"{df.index[0]} to {df.index[-1]}",'ts':now()}
    (OUT/'run21_full.json').write_text(json.dumps(result,ensure_ascii=False,indent=1,default=str))
    return result

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--out',required=True);a=p.parse_args()
    r_=run21_full()
    Path(a.out).write_text(json.dumps(r_,ensure_ascii=False,indent=1,default=str))
    print('VERDICT:',r_['verdict'])
    tA=r_['testA']
    print('DAILY ACF:',{f'lag{k}':v for k,v in tA.items()})
    tB=r_['testB']
    print('VOL CONTROL: raw_l1=',tB.get('raw',{}).get(1,{}).get('ac'),'controlled_l1=',tB.get('controlled',{}).get(1,{}).get('ac'))
    tE=r_['testE']
    for k in ['M0','M1','M3','M13']:
        r2=tE.get(k,{}).get('r2_oos');corr=tE.get(k,{}).get('corr_oos')
        print(f'  {k}: r2={r2}, corr={corr}')
    print('F:',r_['testF'])
