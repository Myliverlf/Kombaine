#!/usr/bin/env python3
"""RUN-56: Trend Term Structure × Ignition Dynamics — Directional Information.
Deterministic. Paper only. No LLM. No strategy optimization.

Tests if MA structure becomes directionally useful when ignition strengthens.
Parent checkpoint: SCIENTIFIC_CHECKPOINT_RUN14_49_V2
Parent RUN: RUN-55
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

SCHEMA='run56-trend-ignition-dynamics'
OUT=Path('/root/audits/strategy_combine_research_intelligence/run56')
OUT.mkdir(parents=True,exist_ok=True)
def now():return time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())

PREREG={
 'parent_checkpoint':'SCIENTIFIC_CHECKPOINT_RUN14_49_V2',
 'parent_run':55,
 'timeframe':'1h',
 'universe':['IMOEX','SBER','LKOH','CNY'],
 'fast_periods':(10,20),
 'med_periods':(60,70),
 'slow_periods':(100,150),
 'vol_lookback':24,
 'expansion_horizon':24,
 'oos_split':'chronological 60/40',
 'null_simulations':100,
}
PREREG_PATH=OUT/'RUN56_PREREGISTRATION.json'

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

def ema(s,n): return s.ewm(span=n,adjust=False).mean()

def build_features(d):
    close=d['close'];high=d['high'];low=d['low'];vol=d['volume']
    ret=close/close.shift()-1
    rng=high-low
    # MA term structure (FIXED periods)
    ma_f1=ema(close,PREREG['fast_periods'][0])
    ma_f2=ema(close,PREREG['fast_periods'][1])
    ma_m1=ema(close,PREREG['med_periods'][0])
    ma_m2=ema(close,PREREG['med_periods'][1])
    ma_s1=ema(close,PREREG['slow_periods'][0])
    ma_s2=ema(close,PREREG['slow_periods'][1])
    # normalized spreads
    price_safe=close.replace(0,1e-10)
    fast_spread=(ma_f1-ma_f2)/price_safe
    med_spread=(ma_m1-ma_m2)/price_safe
    slow_spread=(ma_s1-ma_s2)/price_safe
    fast_slope=fast_spread.diff(6)/6
    med_slope=med_spread.diff(6)/6
    slow_slope=slow_spread.diff(6)/6
    # trend alignment score (-3 to +3)
    trend_score=((fast_spread>0).astype(int)*2-1+(med_spread>0).astype(int)*2-1+(slow_spread>0).astype(int)*2-1)
    # returns
    current_ret=ret
    cum_ret_24=close/close.shift(24)-1
    abs_ret=ret.abs()
    # Ignition dynamics (from RUN-55 frozen)
    vol_level=ret.rolling(PREREG['vol_lookback']).std()
    vol_slope=vol_level.diff(6)/6
    range_accel=(rng-rng.rolling(PREREG['vol_lookback']).mean())/rng.rolling(PREREG['vol_lookback']).mean().replace(0,1e-10)
    # IGNITION SCORE: combined vol_slope + range_accel (frozen from RUN-55)
    # standardize before combining
    vs_z=(vol_slope-vol_slope.expanding(120).mean())/vol_slope.expanding(120).std().replace(0,1e-10)
    ra_z=(range_accel-range_accel.expanding(120).mean())/range_accel.expanding(120).std().replace(0,1e-10)
    ignition_score=(vs_z+ra_z)/2
    # TARGETS
    # Future signed return over expansion horizon
    future_signed=close.shift(-PREREG['expansion_horizon'])/close-1
    future_expansion_dir=(future_signed>0).astype(int)
    future_vol=ret.abs().rolling(PREREG['expansion_horizon']).mean().shift(-PREREG['expansion_horizon'])
    df=pd.DataFrame({
        'fast_spread':fast_spread,'med_spread':med_spread,'slow_spread':slow_spread,
        'fast_slope':fast_slope,'med_slope':med_slope,'slow_slope':slow_slope,
        'trend_score':trend_score,
        'current_ret':current_ret,'cum_ret_24':cum_ret_24,'abs_ret':abs_ret,
        'ignition_score':ignition_score,
        'future_signed':future_signed,'future_dir':future_expansion_dir,
        'future_vol':future_vol
    }).dropna()
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

def eval_reg(y,yhat):
    return {'r2':round(r_squared(y,yhat),6),'mae':round(float(np.mean(np.abs(y-yhat))),6),
            'corr':round(float(np.corrcoef(y,yhat)[0,1]),4)}

def eval_clf(y,y_prob):
    pred=(y_prob>=0.5).astype(int)
    tp=((y==1)&(pred==1)).sum();fn=((y==1)&(pred==0)).sum()
    tn=((y==0)&(pred==0)).sum();fp=((y==0)&(pred==1)).sum()
    bal=(tp/max(tp+fn,1)+tn/max(tn+fp,1))/2
    return {'brier':round(brier_fn(y,y_prob),6),'auc':round(roc_auc_fn(y,y_prob),4),
            'bal_acc':round(bal,4),'log_loss':round(log_loss_fn(y,y_prob),6)}

def run56_full():
    PREREG_PATH.write_text(json.dumps(PREREG,indent=1))
    raw=load_data()
    if not raw:return {'error':'no data'}
    all_results={}
    for t,d in raw.items():
        df=build_features(d)
        if len(df)<300:continue
        n=len(df);split=int(n*0.6)
        train=df.iloc[:split];test=df.iloc[split:]
        y_reg_train=train['future_signed'].values;y_reg_test=test['future_signed'].values
        y_clf_train=train['future_dir'].values;y_clf_test=test['future_dir'].values
        # standardize using train only
        zcols=['current_ret','cum_ret_24','abs_ret','fast_spread','med_spread','slow_spread',
               'fast_slope','med_slope','slow_slope','ignition_score']
        for col in zcols:
            mean=train[col].mean();std=max(train[col].std(),1e-10)
            train[col+'Z']=(train[col]-mean)/std
            test[col+'Z']=(test[col]-mean)/std
        # Trend interaction: trend_score × ignition_score
        trend_ign_train=(train['trend_score'].values*train['ignition_scoreZ'].values)
        trend_ign_test=(test['trend_score'].values*test['ignition_scoreZ'].values)
        # Continuous: future signed return
        # B0: mean
        yh_b0=np.full(len(y_reg_test),y_reg_train.mean())
        m0=eval_reg(y_reg_test,yh_b0)
        # B1: return baseline
        b1_Xtr=train[['current_retZ','abs_ret','cum_ret_24Z']].values
        b1_Xte=test[['current_retZ','abs_ret','cum_ret_24Z']].values
        yh_b1=ols(b1_Xtr,y_reg_train,b1_Xte)
        m1=eval_reg(y_reg_test,yh_b1)
        # B2: + MA term structure
        b2_Xtr=np.column_stack([b1_Xtr,train[['fast_spreadZ','med_spreadZ','slow_spreadZ',
                                                'fast_slopeZ','med_slopeZ','slow_slopeZ']].values])
        b2_Xte=np.column_stack([b1_Xte,test[['fast_spreadZ','med_spreadZ','slow_spreadZ',
                                               'fast_slopeZ','med_slopeZ','slow_slopeZ']].values])
        yh_b2=ols(b2_Xtr,y_reg_train,b2_Xte)
        m2=eval_reg(y_reg_test,yh_b2)
        # B3: + ignition
        b3_Xtr=np.column_stack([b2_Xtr,train[['ignition_scoreZ']].values])
        b3_Xte=np.column_stack([b2_Xte,test[['ignition_scoreZ']].values])
        yh_b3=ols(b3_Xtr,y_reg_train,b3_Xte)
        m3=eval_reg(y_reg_test,yh_b3)
        # B4: + interaction
        b4_Xtr=np.column_stack([b3_Xtr,trend_ign_train.reshape(-1,1)])
        b4_Xte=np.column_stack([b3_Xte,trend_ign_test.reshape(-1,1)])
        yh_b4=ols(b4_Xtr,y_reg_train,b4_Xte)
        m4=eval_reg(y_reg_test,yh_b4)
        # Binary: expansion direction
        def logistic(X_tr,y_tr,X_te):
            X_=np.column_stack([np.ones(len(X_tr)),X_tr])
            try:
                beta=np.linalg.lstsq(X_,y_tr,rcond=None)[0]
                X_t=np.column_stack([np.ones(len(X_te)),X_te])
                return np.clip(X_t@beta,0,1)
            except:return np.full(len(X_te),y_tr.mean())
        yh_clf_b3=logistic(b3_Xtr,y_clf_train,b3_Xte)
        yh_clf_b4=logistic(b4_Xtr,y_clf_train,b4_Xte)
        cm3=eval_clf(y_clf_test,yh_clf_b3)
        cm4=eval_clf(y_clf_test,yh_clf_b4)
        # High-ignition diagnostic
        q75=train['ignition_score'].quantile(0.75)
        high_ign=test['ignition_score']>q75
        low_ign=test['ignition_score']<=train['ignition_score'].quantile(0.25)
        diag={}
        for name,mask in [('HIGH_IGN',high_ign),('LOW_IGN',low_ign)]:
            if mask.sum()>10:
                yt=y_reg_test[mask]
                diag[name]={'n':int(mask.sum()),
                           'future_dir_up':round(float(y_clf_test[mask].mean()),4),
                           'mean_signed_return':round(float(yt.mean()),6),
                           'mean_abs_return':round(float(np.abs(yt).mean()),6)}
        # 2x3 matrix: ignition × trend
        med_ign=train['ignition_score'].quantile(0.25)
        high_ign_thr=train['ignition_score'].quantile(0.75)
        bull=test['trend_score']>=2
        bear=test['trend_score']<=-2
        mixed=~bull&~bear
        matrix={}
        for ign_name,ign_mask in [('LOW_IGN',test['ignition_score']<=med_ign),
                                   ('HIGH_IGN',test['ignition_score']>=high_ign_thr)]:
            for trend_name,tmask in [('BULL',bull),('MIXED',mixed),('BEAR',bear)]:
                m=ign_mask&tmask
                if m.sum()>10:
                    matrix[f'{ign_name}_{trend_name}']={'n':int(m.sum()),
                        'p_dir_up':round(float(y_clf_test[m].mean()),4),
                        'mean_return':round(float(y_reg_test[m].mean()),6)}
        # Null test
        rng=np.random.RandomState(42)
        null_deltas=[]
        for _ in range(PREREG['null_simulations']):
            shuf=train['ignition_scoreZ'].values.copy();rng.shuffle(shuf)
            shuf_t=test['ignition_scoreZ'].values.copy();rng.shuffle(shuf_t)
            yh_n=ols(np.column_stack([b2_Xtr,train[['ignition_scoreZ']].values,trend_ign_train.reshape(-1,1)]),
                     y_reg_train,np.column_stack([b2_Xte,test[['ignition_scoreZ']].values,trend_ign_test.reshape(-1,1)]))
            # Actually null should keep ignition but shuffle interaction
            shuf_ti=(train['trend_score'].values*rng.permutation(train['ignition_scoreZ'].values))
            shuf_ti_t=(test['trend_score'].values*rng.permutation(test['ignition_scoreZ'].values))
            yh_n=ols(np.column_stack([b3_Xtr,shuf_ti.reshape(-1,1)]),y_reg_train,
                     np.column_stack([b3_Xte,shuf_ti_t.reshape(-1,1)]))
            null_deltas.append(m3['r2']-r_squared(y_reg_test,yh_n))
        real_delta=m4['r2']-m3['r2']
        null_pctl=float(np.mean(np.array(null_deltas)<=real_delta))
        # Temporal
        blocks=[];bs=len(test)//3
        for i in range(3):
            s=i*bs;e=min((i+1)*bs,len(test))
            yt=y_reg_test[s:e]
            yh3=ols(b3_Xtr,y_reg_train,b3_Xte[s:e])
            yh4=ols(b4_Xtr,y_reg_train,b4_Xte[s:e])
            blocks.append({'block':i,'n':e-s,
                          'b3_r2':round(r_squared(yt,yh3),6),
                          'b4_r2':round(r_squared(yt,yh4),6),
                          'delta':round(r_squared(yt,yh4)-r_squared(yt,yh3),6)})
        all_results[t]={'continuous':{'B0':m0,'B1':m1,'B2':m2,'B3':m3,'B4':m4},
                       'binary':{'B3':cm3,'B4':cm4},
                       'diagnostic':diag,'matrix':matrix,
                       'null':{'real_delta':round(real_delta,6),'pctl':round(null_pctl,4)},
                       'temporal':blocks}
    # aggregate
    deltas_reg=[all_results[t]['continuous']['B4']['r2']-all_results[t]['continuous']['B3']['r2'] for t in all_results]
    deltas_clf=[all_results[t]['binary']['B4']['auc']-all_results[t]['binary']['B3']['auc'] for t in all_results]
    avg_reg=np.mean(deltas_reg) if deltas_reg else 0
    avg_clf=np.mean(deltas_clf) if deltas_clf else 0
    if avg_reg>0.005 and avg_clf>0.005:sci='TREND_STRUCTURE_IGNITION_CONFIRMED'
    elif avg_reg>0.001 or avg_clf>0.001:sci='TREND_STRUCTURE_IGNITION_CONTEXTUAL'
    else:sci='IGNITION_DOES_NOT_UNLOCK_DIRECTION'
    result={'schema':SCHEMA,'preregistration':PREREG,
            'per_asset':all_results,
            'aggregate':{'avg_reg_delta':round(avg_reg,6),'avg_clf_auc_delta':round(avg_clf,4),'n_assets':len(all_results)},
            'scientific_verdict':sci,'ts':now()}
    (OUT/'run56_full.json').write_text(json.dumps(result,ensure_ascii=False,indent=1,default=str))
    return result

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--out',required=True);a=p.parse_args()
    r_=run56_full()
    Path(a.out).write_text(json.dumps(r_,ensure_ascii=False,indent=1,default=str))
    print('VERDICT:',r_['scientific_verdict'])
    print('AGGREGATE:',r_['aggregate'])
    for t,v in r_['per_asset'].items():
        c=v['continuous']
        print(f'{t}: B3_r2={c["B3"]["r2"]} B4_r2={c["B4"]["r2"]} delta={c["B4"]["r2"]-c["B3"]["r2"]:.6f}')
        print(f'  binary B3_auc={v["binary"]["B3"]["auc"]} B4_auc={v["binary"]["B4"]["auc"]}')
        print(f'  matrix: {v["matrix"]}')
