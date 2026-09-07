#!/usr/bin/env python3
"""RUN-55: IGNITION_STATE_VALIDATION.
Deterministic. Paper only. No LLM. No strategy optimization.

Tests if volatility ignition is distinct from high vol / simple vol rise.
Parent checkpoint: SCIENTIFIC_CHECKPOINT_RUN14_49_V2
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

SCHEMA='run55-ignition-state-validation'
OUT=Path('/root/audits/strategy_combine_research_intelligence/run55')
OUT.mkdir(parents=True,exist_ok=True)
def now():return time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())

PREREG={
 'parent_checkpoint':'SCIENTIFIC_CHECKPOINT_RUN14_49_V2',
 'timeframe':'1h',
 'universe':['IMOEX','SBER','LKOH','CNY'],
 'vol_lookback':24,
 'compression_percentile':30,
 'expansion_percentile':70,
 'future_horizon':24,
 'oos_split':'chronological 60/40',
 'null_simulations':100,
}
PREREG_PATH=OUT/'RUN55_PREREGISTRATION.json'

def r_squared(y,yhat):
    ss_res=((y-yhat)**2).sum();ss_tot=((y-y.mean())**2).sum()
    return 1-ss_res/max(ss_tot,1e-12)

def load_data():
    raw=q15.load_all()
    results={}
    for t in PREREG['universe']:
        if t not in raw:continue
        d=raw[t]['train'].copy()
        if 'time' in d.columns:
            d['time']=pd.to_datetime(d['time']);d=d.set_index('time')
        results[t]=d
    return results

def build_features(d):
    close=d['close'];high=d['high'];low=d['low'];vol=d['volume']
    ret=close/close.shift()-1
    rng=high-low
    # VOL_LEVEL: expanding std of returns
    vol_level=ret.rolling(PREREG['vol_lookback']).std()
    # VOL_SLOPE: rate of change of vol_level (causal)
    vol_slope=vol_level.diff(6)/6  # 6-bar slope
    # RANGE_ACCELERATION: change in range relative to recent norm
    range_ma=rng.rolling(PREREG['vol_lookback']).mean()
    range_accel=(rng-range_ma)/range_ma.replace(0,1e-10)
    # COMPRESSION: vol_level below rolling percentile
    vol_percentile=vol_level.expanding(min_periods=120).rank(pct=True)
    compression=(vol_percentile<PREREG['compression_percentile']/100).astype(int)
    # Participation (for B4)
    vol_mean=vol.expanding(min_periods=120).mean()
    vol_std=vol.expanding(min_periods=120).std().replace(0,np.nan)
    participation=(vol-vol_mean)/vol_std
    part_slope=participation.diff(6)/6
    # TARGETS
    # Future realized volatility (24h forward)
    future_vol=ret.abs().rolling(PREREG['future_horizon']).mean().shift(-PREREG['future_horizon'])
    # Future expansion event (binary)
    future_expansion=(future_vol>future_vol.expanding(min_periods=120).quantile(PREREG['expansion_percentile']/100)).astype(int)
    df=pd.DataFrame({'vol_level':vol_level,'vol_slope':vol_slope,'range_accel':range_accel,
                    'compression':compression,'part_slope':part_slope,
                    'future_vol':future_vol,'future_expansion':future_expansion}).dropna()
    return df

def ols(X_train,y_train,X_test):
    X_=np.column_stack([np.ones(len(X_train)),X_train])
    try:
        beta=np.linalg.lstsq(X_,y_train,rcond=None)[0]
        X_t=np.column_stack([np.ones(len(X_test)),X_test])
        return X_t@beta
    except:return np.full(len(X_test),y_train.mean())

def log_loss_fn(y_true,y_prob):
    eps=1e-10;y_prob=np.clip(y_prob,eps,1-eps)
    return float(-np.mean(y_true*np.log(y_prob)+(1-y_true)*np.log(1-y_prob)))

def brier_fn(y_true,y_prob):
    return float(np.mean((y_true-y_prob)**2))

def roc_auc_fn(y_true,y_scores):
    pos=y_scores[y_true==1];neg=y_scores[y_true==0]
    if len(pos)==0 or len(neg)==0: return 0.5
    n_pos=len(pos);n_neg=len(neg)
    all_scores=np.concatenate([pos,neg])
    order=np.argsort(all_scores)
    ranks=np.empty_like(order);ranks[order]=np.arange(1,len(order)+1)
    return float((ranks[:n_pos].sum()-n_pos*(n_pos+1)/2)/(n_pos*n_neg))

def run55_full():
    PREREG_PATH.write_text(json.dumps(PREREG,indent=1))
    raw=load_data()
    if not raw:return {'error':'no data'}
    all_results={}
    for t,d in raw.items():
        df=build_features(d)
        if len(df)<200:continue
        n=len(df);split=int(n*0.6)
        train=df.iloc[:split];test=df.iloc[split:]
        # standardize using train
        for col in ['vol_level','vol_slope','range_accel','part_slope']:
            mean=train[col].mean();std=max(train[col].std(),1e-10)
            train[col+'Z']=(train[col]-mean)/std
            test[col+'Z']=(test[col]-mean)/std
        y_vol_train=train['future_vol'].values;y_vol_test=test['future_vol'].values
        y_exp_train=train['future_expansion'].values;y_exp_test=test['future_expansion'].values
        # CONTINUOUS: future volatility
        # B0: mean
        yhat_b0=np.full(len(y_vol_test),y_vol_train.mean())
        r2_b0=r_squared(y_vol_test,yhat_b0)
        # B1: vol_level only
        yhat_b1=ols(train[['vol_levelZ']].values,y_vol_train,test[['vol_levelZ']].values)
        r2_b1=r_squared(y_vol_test,yhat_b1)
        corr_b1=float(np.corrcoef(y_vol_test,yhat_b1)[0,1])
        # B2: + compression
        yhat_b2=ols(train[['vol_levelZ','compression']].values,y_vol_train,test[['vol_levelZ','compression']].values)
        r2_b2=r_squared(y_vol_test,yhat_b2)
        # B3: + ignition dynamics
        yhat_b3=ols(train[['vol_levelZ','compression','vol_slopeZ','range_accelZ']].values,y_vol_train,
                    test[['vol_levelZ','compression','vol_slopeZ','range_accelZ']].values)
        r2_b3=r_squared(y_vol_test,yhat_b3)
        # B4: + participation acceleration
        yhat_b4=ols(train[['vol_levelZ','compression','vol_slopeZ','range_accelZ','part_slopeZ']].values,y_vol_train,
                    test[['vol_levelZ','compression','vol_slopeZ','range_accelZ','part_slopeZ']].values)
        r2_b4=r_squared(y_vol_test,yhat_b4)
        # BINARY: expansion event
        def logistic(X_train,y_train,X_test):
            X_=np.column_stack([np.ones(len(X_train)),X_train])
            try:
                beta=np.linalg.lstsq(X_,y_train,rcond=None)[0]
                X_t=np.column_stack([np.ones(len(X_test)),X_test])
                return np.clip(X_t@beta,0,1)
            except:return np.full(len(y_test),y_train.mean())
        yh_exp_b2=logistic(train[['vol_levelZ','compression']].values,y_exp_train,
                           test[['vol_levelZ','compression']].values)
        yh_exp_b3=logistic(train[['vol_levelZ','compression','vol_slopeZ','range_accelZ']].values,y_exp_train,
                           test[['vol_levelZ','compression','vol_slopeZ','range_accelZ']].values)
        m_exp_b2={'brier':round(brier_fn(y_exp_test,yh_exp_b2),6),'auc':round(roc_auc_fn(y_exp_test,yh_exp_b2),4)}
        m_exp_b3={'brier':round(brier_fn(y_exp_test,yh_exp_b3),6),'auc':round(roc_auc_fn(y_exp_test,yh_exp_b3),4)}
        # State comparison
        thr_compression=train['vol_level'].quantile(PREREG['compression_percentile']/100)
        thr_high=train['vol_level'].quantile(0.75)
        is_compression=test['vol_level']<=thr_compression
        is_high=test['vol_level']>=thr_high
        is_ignition=is_compression&(test['vol_slope']>0)
        is_vol_rise=(~is_compression)&(test['vol_slope']>0)
        states={}
        for name,mask in [('COMPRESSION',is_compression),('IGNITION',is_ignition),
                          ('HIGH_VOL',is_high),('VOL_RISE',is_vol_rise)]:
            if mask.sum()>10:
                states[name]={'n':int(mask.sum()),
                             'current_vol':round(float(test['vol_level'][mask].mean()),6),
                             'future_vol':round(float(y_vol_test[mask].mean()),6),
                             'expansion_rate':round(float(y_exp_test[mask].mean()),4)}
        # Lead time
        ignition_mask=is_ignition.values
        lead_times=[]
        for i in range(len(ignition_mask)-1):
            if ignition_mask[i]:
                # find how far ahead future expansion peak is
                for h in range(1,min(PREREG['future_horizon'],len(y_vol_test)-i)):
                    if i+h<len(y_vol_test) and y_vol_test[i+h]>np.percentile(y_vol_test,75):
                        lead_times.append(h)
                        break
        avg_lead=np.mean(lead_times) if lead_times else 0
        # False ignition
        false_ignition=is_ignition.values&(y_exp_test==0)
        false_ignition_rate=float(false_ignition.mean()) if is_ignition.sum()>0 else 0
        # Missed expansion
        missed=(y_exp_test==1)&(~ignition_mask[:len(y_exp_test)])
        missed_rate=float(missed.mean()) if (y_exp_test==1).sum()>0 else 0
        # Null test
        rng=np.random.RandomState(42)
        null_deltas=[]
        for _ in range(PREREG['null_simulations']):
            shuf=train['vol_slopeZ'].values.copy();rng.shuffle(shuf)
            shuf_t=test['vol_slopeZ'].values.copy();rng.shuffle(shuf_t)
            yh_null=ols(np.column_stack([train[['vol_levelZ','compression']].values,shuf]),
                       y_vol_train,np.column_stack([test[['vol_levelZ','compression']].values,shuf_t]))
            r2_null=r_squared(y_vol_test,yh_null)
            null_deltas.append(r2_b2-r2_null)
        real_delta=r2_b3-r2_b2
        null_pctl=float(np.mean(np.array(null_deltas)<=real_delta))
        # temporal
        blocks=[];bs=len(test)//3
        for i in range(3):
            s=i*bs;e=min((i+1)*bs,len(test))
            yt=y_vol_test[s:e]
            yh2=ols(train[['vol_levelZ','compression']].values,y_vol_train,test[['vol_levelZ','compression']].values[s:e])
            yh3=ols(train[['vol_levelZ','compression','vol_slopeZ','range_accelZ']].values,y_vol_train,
                    test[['vol_levelZ','compression','vol_slopeZ','range_accelZ']].values[s:e])
            blocks.append({'block':i,'n':e-s,
                          'r2_b2':round(r_squared(yt,yh2),6),
                          'r2_b3':round(r_squared(yt,yh3),6),
                          'delta':round(r_squared(yt,yh3)-r_squared(yt,yh2),6)})
        all_results[t]={'continuous':{'B0':round(r2_b0,6),'B1':round(r2_b1,6),'corr_B1':round(corr_b1,4),
                                     'B2':round(r2_b2,6),'B3':round(r2_b3,6),'B4':round(r2_b4,6)},
                       'expansion_event':{'B2':m_exp_b2,'B3':m_exp_b3},
                       'states':states,'lead_time':round(avg_lead,2),
                       'false_ignition_rate':round(false_ignition_rate,4),
                       'missed_expansion_rate':round(missed_rate,4),
                       'null':{'real_delta':round(real_delta,6),'pctl':round(null_pctl,4)},
                       'temporal':blocks}
    # aggregate
    deltas=[all_results[t]['continuous']['B3']-all_results[t]['continuous']['B2'] for t in all_results]
    avg_delta=np.mean(deltas) if deltas else 0
    if avg_delta>0.005:sci='IGNITION_STATE_VALIDATED'
    elif avg_delta>0.001:sci='IGNITION_STATE_CONTEXTUAL'
    else:sci='IGNITION_NOT_REPLICATED'
    result={'schema':SCHEMA,'preregistration':PREREG,
            'per_asset':all_results,
            'aggregate':{'avg_b2_b3_delta':round(avg_delta,6),'n_assets':len(all_results)},
            'scientific_verdict':sci,'ts':now()}
    (OUT/'run55_full.json').write_text(json.dumps(result,ensure_ascii=False,indent=1,default=str))
    return result

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--out',required=True);a=p.parse_args()
    r_=run55_full()
    Path(a.out).write_text(json.dumps(r_,ensure_ascii=False,indent=1,default=str))
    print('VERDICT:',r_['scientific_verdict'])
    print('AGGREGATE:',r_['aggregate'])
    for t,v in r_['per_asset'].items():
        c=v['continuous']
        print(f'{t}: B0={c["B0"]} B1={c["B1"]} B2={c["B2"]} B3={c["B3"]} B4={c["B4"]}')
        print(f'  states: {v["states"]}')
        print(f'  lead={v["lead_time"]} false_ign={v["false_ignition_rate"]} missed={v["missed_expansion_rate"]}')
