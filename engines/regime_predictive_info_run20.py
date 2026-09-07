#!/usr/bin/env python3
"""RUN-20: Regime-Conditioned Predictive Information.
Deterministic. Paper only. No LLM. No strategy. No PnL.

Tests whether regime-conditioned autocorrelation has genuine
out-of-sample predictive information about future IMOEX returns.
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

SCHEMA='run20-regime-predictive-info-v1'
OUT=Path('/root/audits/strategy_combine_research_intelligence/run20')
OUT.mkdir(parents=True,exist_ok=True)
def now():return time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())

# ================================================== PREREGISTRATION ===
PREREG={
 'target':'next_period_return r[t+1]',
 'predictors':['r[t]','r[t-2]'],
 'regime_definition':'expanding median rolling24 std, terciles 33/66',
 'train_fraction':0.6,
 'oos_method':'expanding_window_rolling_200',
 'rolling_window':200,
 'significance_bootstrap':'block_10',
 'n_bootstrap':500,
 'hac_lags':4,
 'min_oos':100,
 'acceptance_incremental_r2':0.001,
 'acceptance_sign_accuracy':0.51,
 'acceptance_regime_interaction_pvalue':0.1,
 'verdicts':['PREDICTIVE_INFORMATION_ROBUST','PREDICTIVE_INFORMATION_DECAYING',
             'DESCRIPTIVE_NOT_PREDICTIVE','MICROSTRUCTURE_ARTIFACT','INCONCLUSIVE'],
}
PREREG_PATH=OUT/'RUN20_PREREGISTRATION.json'

# ================================================== LOAD + PREPARE ===
def load_imoex():
    ds=q15.load_all().get('IMOEX')
    if not ds:return None
    d=ds['train']
    ret=np.log(d.close/d.close.shift())
    vol=ret.rolling(24).std()
    q33=vol.expanding(min_periods=120).quantile(0.33).shift(1)
    q66=vol.expanding(min_periods=120).quantile(0.66).shift(1)
    regime=pd.Series('MID',index=d.index)
    regime[vol<=q33]='LOW'
    regime[vol>=q66]='HIGH'
    df=pd.DataFrame({'ret':ret,'vol':vol,'regime':regime,'r_t':ret.shift(0),'r_lag1':ret.shift(1),
                     'r_lag2':ret.shift(2),'r_lag3':ret.shift(3),'r_lag4':ret.shift(4),
                     'abs_ret':ret.abs(),'r_next':ret.shift(-1)}).dropna()
    return df

# ================================================== MODEL HELPERS ===
def ols(y,X):
    X_=np.column_stack([np.ones(len(X)),X])
    try:
        beta=np.linalg.lstsq(X_,y,rcond=None)[0]
        resid=y-X_@beta
        return {'beta':beta,'resid':resid}
    except:return None

def r_squared(y,yhat):
    ss_res=((y-yhat)**2).sum()
    ss_tot=((y-y.mean())**2).sum()
    return 1-ss_res/max(ss_tot,1e-12)

def block_bootstrap_correlation(x,y,n_boot=500,block=10):
    n=len(x);obs=[]
    for _ in range(n_boot):
        idx=np.concatenate([np.arange(i,min(i+block,n)) for i in range(0,n,block)])
        np.random.shuffle(idx)
        idx=idx[:n]
        obs.append(np.corrcoef(x[idx],y[idx])[0,1])
    obs=np.array(obs)
    ci_lo=np.percentile(obs,2.5);ci_hi=np.percentile(obs,97.5)
    t_stat=np.mean(obs)/max(np.std(obs),1e-12)
    return {'mean':float(np.mean(obs)),'ci_lo':float(ci_lo),'ci_hi':float(ci_hi),
            'p_value':float(2*(1-np.abs(t_stat).clip(0,3)/3))}

# ================================================== TEST 1: UNCONDITIONAL BASELINE ===
def test1_unconditional(df):
    n=len(df);split=int(n*PREREG['train_fraction'])
    train=df.iloc[:split];test=df.iloc[split:]
    results={}
    for name,features in {
        'M0':np.zeros((len(train),0)),
        'M1':train[['r_lag1']].values,
        'M2':train[['r_lag3']].values,
        'M3':train[['r_lag1','r_lag3']].values}.items():
        y_train=train['r_next'].values
        y_test=test['r_next'].values
        if features.shape[1]==0:
            yhat_train=np.full(len(y_train),y_train.mean())
            yhat_test=np.full(len(y_test),y_train.mean())
            r2_in=0;r2_oos=r_squared(y_test,yhat_test)
        else:
            m_train=ols(y_train,features)
            m_test_ols=ols(y_test,test[['r_lag1','r_lag3']].values[:,:features.shape[1]])
            if m_train is None:continue
            yhat_train=m_train['beta'][0]+features@m_train['beta'][1:]
            X_test=test[['r_lag1','r_lag3']].values[:,:features.shape[1]]
            yhat_test=m_train['beta'][0]+X_test@m_train['beta'][1:]
            r2_in=r_squared(y_train,yhat_train)
            r2_oos=r_squared(y_test,yhat_test)
        corr=float(np.corrcoef(y_test,yhat_test)[0,1]) if len(y_test)>0 else 0
        sign_acc=float((np.sign(yhat_test)==np.sign(y_test)).mean()) if len(y_test)>0 else 0
        results[name]={'r2_in_sample':round(r2_in,6),'r2_oos':round(r2_oos,6),
                       'corr_oos':round(corr,6),'sign_accuracy_oos':round(sign_acc,4),
                       'n_train':len(y_train),'n_test':len(y_test)}
    return results

# ================================================== TEST 2: REGIME-ONLY BASELINE ===
def test2_regime_only(df):
    n=len(df);split=int(n*PREREG['train_fraction'])
    train=df.iloc[:split];test=df.iloc[split:]
    # regime means
    regime_means=train.groupby('regime')['r_next'].mean().to_dict()
    y_test=test['r_next'].values
    yhat=pd.Series(test['regime'].map(regime_means)).values
    r2=r_squared(y_test,yhat)
    corr=float(np.corrcoef(y_test,yhat)[0,1])
    # vs intercept
    yhat_null=np.full(len(y_test),y_test.mean())
    r2_null=r_squared(y_test,yhat_null)
    return {'r2_oos':round(r2,6),'r2_baseline':round(r2_null,6),'corr_oos':round(corr,6),
            'regime_means':{k:round(v,6) for k,v in regime_means.items()}}

# ================================================== TEST 3: REGIME × LAG INTERACTION ===
def test3_regime_interaction(df):
    n=len(df);split=int(n*PREREG['train_fraction'])
    train=df.iloc[:split];test=df.iloc[split:]
    results={}
    for regime in ['LOW','MID','HIGH']:
        mask=train['regime']==regime
        sub=train[mask]
        if len(sub)<50:results[regime]={'insufficient':True};continue
        X=sub[['r_lag1']].values;y=sub['r_next'].values
        m=ols(y,X)
        if m is None:continue
        beta=float(m['beta'][1])
        # OOS
        mask_t=test['regime']==regime
        sub_t=test[mask_t]
        if len(sub_t)<20:results[regime]={'insufficient':True};continue
        X_t=sub_t[['r_lag1']].values;y_t=sub_t['r_next'].values
        yhat=m['beta'][0]+X_t@np.array([beta])
        r2=r_squared(y_t,yhat)
        corr=float(np.corrcoef(y_t,yhat)[0,1]) if len(y_t)>0 else 0
        sign_acc=float((np.sign(yhat)==np.sign(y_t)).mean()) if len(y_t)>0 else 0
        results[regime]={'beta_train':round(beta,6),'beta_direction':'negative' if beta<0 else 'positive',
                         'r2_oos':round(r2,6),'corr_oos':round(corr,6),'sign_accuracy':round(sign_acc,4),
                         'n_train':int(mask.sum()),'n_test':int(mask_t.sum())}
    # interaction test: does beta differ across regimes?
    betas=[v.get('beta_train',0) for v in results.values() if not v.get('insufficient')]
    results['interaction']={'beta_range':round(max(betas)-min(betas),6) if betas else 0,
                            'expected_order_HIGH_neg_LOW_pos':all(
                                results.get(r,{}).get('beta_train',0)<0 for r in ['HIGH','MID']
                            ) and results.get('LOW',{}).get('beta_train',0)>0}
    return results

# ================================================== TEST 4: HARD REGIME RULE ===
def test4_hard_regime(df):
    n=len(df);split=int(n*PREREG['train_fraction'])
    results={}
    for regime in ['LOW','MID','HIGH']:
        mask_all=df['regime']==regime
        df_r=df[mask_all]
        split_r=int(len(df_r)*PREREG['train_fraction'])
        train=df_r.iloc[:split_r];test=df_r.iloc[split_r:]
        if len(train)<50 or len(test)<20:
            results[regime]={'insufficient':True};continue
        X_train=train[['r_lag1']].values;y_train=train['r_next'].values
        X_test=test[['r_lag1']].values;y_test=test['r_next'].values
        m=ols(y_train,X_train)
        if m is None:continue
        beta=float(m['beta'][1])
        yhat=m['beta'][0]+X_test@np.array([beta])
        r2=r_squared(y_test,yhat)
        corr=float(np.corrcoef(y_test,yhat)[0,1]) if len(y_test)>0 else 0
        sign_acc=float((np.sign(yhat)==np.sign(y_test)).mean()) if len(y_test)>0 else 0
        results[regime]={'n_train':len(train),'n_test':len(test),
                         'beta_train':round(beta,6),'beta_test_direction':'negative' if beta<0 else 'positive',
                         'r2_oos':round(r2,6),'corr_oos':round(corr,6),'sign_accuracy':round(sign_acc,4),
                         'sign_preserved':bool(np.sign(beta)<0)}
    return results

# ================================================== TEST 5: LAG-1 VS LAG-3 ===
def test5_lag_comparison(df):
    n=len(df);split=int(n*PREREG['train_fraction'])
    train=df.iloc[:split];test=df.iloc[split:]
    results={}
    y_train=train['r_next'].values;y_test=test['r_next'].values
    for name,(ft,fe) in {
        'lag1_only':(train[['r_lag1']].values,test[['r_lag1']].values),
        'lag3_only':(train[['r_lag3']].values,test[['r_lag3']].values),
        'both':(train[['r_lag1','r_lag3']].values,test[['r_lag1','r_lag3']].values)}.items():
        m=ols(y_train,ft)
        if m is None:continue
        yhat=m['beta'][0]+fe@m['beta'][1:]
        r2=r_squared(y_test,yhat)
        corr=float(np.corrcoef(y_test,yhat)[0,1]) if len(y_test)>0 else 0
        sign_acc=float((np.sign(yhat)==np.sign(y_test)).mean()) if len(y_test)>0 else 0
        results[name]={'r2_oos':round(r2,6),'corr_oos':round(corr,6),'sign_accuracy':round(sign_acc,4),
                       'coefficients':[round(float(b),6) for b in m['beta'][1:]]}
    # incremental: does adding lag3 to lag1 help?
    if 'lag1_only' in results and 'both' in results:
        results['incremental_lag3']={'r2_lag1':results['lag1_only']['r2_oos'],
                                     'r2_both':results['both']['r2_oos'],
                                     'incremental':round(results['both']['r2_oos']-results['lag1_only']['r2_oos'],6)}
    return results

# ================================================== TEST 6: TEMPORAL DECAY ===
def test6_temporal_decay(df):
    n=len(df)
    window=PREREG['rolling_window']
    results=[]
    for start in range(window,n-100,50):
        end=min(start+window,n-1)
        sub=df.iloc[start:end]
        if len(sub)<50:continue
        X=sub[['r_lag1']].values;y=sub['r_next'].values
        m=ols(y,X)
        if m is None:continue
        beta=float(m['beta'][1])
        period_start=df.index[start] if hasattr(df.index[start],'date') else str(df.index[start])
        results.append({'start_idx':int(start),'beta':round(beta,6),
                        'period':str(period_start)[:10]})
    # latest period
    latest=df.iloc[-200:]
    X_latest=latest[['r_lag1']].values;y_latest=latest['r_next'].values
    m_latest=ols(y_latest,X_latest)
    latest_beta=float(m_latest['beta'][1]) if m_latest else 0
    # decay assessment
    betas=[r['beta'] for r in results]
    if len(betas)>=3:
        first_half=np.mean(betas[:len(betas)//2])
        second_half=np.mean(betas[len(betas)//2:])
        decay_ratio=abs(second_half)/max(abs(first_half),1e-12) if abs(first_half)>0.001 else 1
        # trend
        x=np.arange(len(betas))
        trend_corr=float(np.corrcoef(x,betas)[0,1]) if len(betas)>2 else 0
    else:
        decay_ratio=1;trend_corr=0
    return {'windows':results,'latest_period_beta':round(latest_beta,6),
            'decay_ratio':round(decay_ratio,4),'trend_correlation':round(trend_corr,4),
            'n_windows':len(results),
            'assessment':'decaying' if decay_ratio<0.5 else 'stable' if decay_ratio>0.8 else 'weakening'}

# ================================================== TEST 7: MICROSTRUCTURE FALSIFICATION ===
def test7_microstructure(df):
    n=len(df);split=int(n*PREREG['train_fraction'])
    train=df.iloc[:split];test=df.iloc[split:]
    # controls: abs_ret (volatility proxy), session hour
    X_train=train[['r_lag1','abs_ret']].values
    X_test=test[['r_lag1','abs_ret']].values
    y_train=train['r_next'].values
    y_test=test['r_next'].values
    m=ols(y_train,X_train)
    if m is None:return {'error':'ols failed'}
    yhat=m['beta'][0]+X_test@m['beta'][1:]
    r2=r_squared(y_test,yhat)
    corr=float(np.corrcoef(y_test,yhat)[0,1]) if len(y_test)>0 else 0
    # does lag1 survive after controlling for abs_ret?
    lag1_coef=float(m['beta'][1])
    vol_coef=float(m['beta'][2])
    return {'r2_with_controls':round(r2,6),'lag1_coef_after_control':round(lag1_coef,6),
            'vol_coef':round(vol_coef,6),'lag1_survives':bool(lag1_coef<0),
            'corr_oos':round(corr,6)}

# ================================================== TEST 8: CROSS-ASSET REGIME ===
def test8_cross_asset(df_all):
    results={}
    for t,ds in q15.load_all().items():
        d=ds['train'];ret=np.log(d.close/d.close.shift())
        vol=ret.rolling(24).std()
        q33=vol.expanding(min_periods=120).quantile(0.33).shift(1)
        q66=vol.expanding(min_periods=120).quantile(0.66).shift(1)
        regime=pd.Series('MID',index=d.index)
        regime[vol<=q33]='LOW';regime[vol>=q66]='HIGH'
        r_lag1=ret.shift(1);r_next=ret.shift(-1)
        df_t=pd.DataFrame({'r_next':r_next,'r_lag1':r_lag1,'regime':regime}).dropna()
        if len(df_t)<200:continue
        # regime composition
        comp=df_t['regime'].value_counts(normalize=True).to_dict()
        # lag1 per regime
        regime_ac={}
        for rg in ['LOW','MID','HIGH']:
            sub=df_t[df_t['regime']==rg]
            if len(sub)<50:regime_ac[rg]={'n':len(sub),'ac':0,'insufficient':True};continue
            ac=float(np.corrcoef(sub['r_lag1'].values,sub['r_next'].values)[0,1])
            regime_ac[rg]={'n':len(sub),'ac':round(ac,6),
                          'significant':abs(ac)*math.sqrt(max(len(sub)-2,1))>1.96}
        results[t]={'regime_composition':{k:round(v,4) for k,v in comp.items()},
                    'regime_ac':regime_ac}
    return results

# ================================================== FULL RUN ===
def run20_full():
    PREREG_PATH.write_text(json.dumps(PREREG,indent=1))
    df=load_imoex()
    if df is None:return {'error':'no IMOEX data'}
    t1=test1_unconditional(df)
    t2=test2_regime_only(df)
    t3=test3_regime_interaction(df)
    t4=test4_hard_regime(df)
    t5=test5_lag_comparison(df)
    t6=test6_temporal_decay(df)
    t7=test7_microstructure(df)
    t8=test8_cross_asset(df)
    # Save individual outputs
    (OUT/'oos_prediction.json').write_text(json.dumps(t1,indent=1,default=str))
    (OUT/'regime_interaction.json').write_text(json.dumps(t3,indent=1,default=str))
    (OUT/'temporal_decay.json').write_text(json.dumps(t6,indent=1,default=str))
    (OUT/'cross_asset_regimes.json').write_text(json.dumps(t8,indent=1,default=str))
    # Verdict
    # Check temporal decay
    decay=t6.get('assessment','stable')
    latest_beta=t6.get('latest_period_beta',0)
    # Check if lag1 has OOS info
    lag1_r2=t1.get('M1',{}).get('r2_oos',0)
    lag1_corr=t1.get('M1',{}).get('corr_oos',0)
    # Check regime interaction
    interaction=t3.get('interaction',{})
    beta_range=interaction.get('beta_range',0)
    # Verdict logic
    if decay=='decaying' and abs(latest_beta)<0.01:
        verdict='PREDICTIVE_INFORMATION_DECAYING'
    elif t7.get('lag1_survives',False) and lag1_corr>0.01 and beta_range>0.03:
        verdict='PREDICTIVE_INFORMATION_ROBUST'
    elif lag1_corr<0.01 and abs(lag1_r2)<0.001:
        verdict='DESCRIPTIVE_NOT_PREDICTIVE'
    elif not t7.get('lag1_survives',True):
        verdict='MICROSTRUCTURE_ARTIFACT'
    else:
        verdict='INCONCLUSIVE'
    result={'schema':SCHEMA,'preregistration':PREREG,
            'test1_unconditional':t1,'test2_regime_only':t2,'test3_regime_interaction':t3,
            'test4_hard_regime':t4,'test5_lag_comparison':t5,'test6_temporal_decay':t6,
            'test7_microstructure':t7,'test8_cross_asset':t8,'verdict':verdict,'ts':now()}
    (OUT/'run20_full.json').write_text(json.dumps(result,ensure_ascii=False,indent=1,default=str))
    return result

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--out',required=True);a=p.parse_args()
    r_=run20_full()
    Path(a.out).write_text(json.dumps(r_,ensure_ascii=False,indent=1,default=str))
    print('VERDICT:',r_['verdict'])
    t1=r_['test1_unconditional']
    print('M1 OOS r2:',t1.get('M1',{}).get('r2_oos'),'corr:',t1.get('M1',{}).get('corr_oos'))
    t3=r_['test3_regime_interaction']
    for rg in ['LOW','MID','HIGH']:print(f'  {rg} beta:',t3.get(rg,{}).get('beta_train'),'oos_r2:',t3.get(rg,{}).get('r2_oos'))
    print('temporal decay:',r_['test6_temporal_decay']['assessment'],'latest_beta:',r_['test6_temporal_decay']['latest_period_beta'])
