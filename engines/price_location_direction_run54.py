#!/usr/bin/env python3
"""RUN-54: Price Location / Distance from Extrema — Incremental Directional Information.
Deterministic. Paper only. No LLM. No strategy optimization.

Tests if price location in recent range predicts next-bar direction beyond momentum.
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

SCHEMA='run54-price-location-direction'
OUT=Path('/root/audits/strategy_combine_research_intelligence/run54')
OUT.mkdir(parents=True,exist_ok=True)
def now():return time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())

PREREG={
 'parent_checkpoint':'SCIENTIFIC_CHECKPOINT_RUN14_49_V2',
 'lookback':24,
 'timeframe':'1h',
 'universe':['IMOEX','SBER','LKOH','CNY'],
 'target':'sign(next_1h_return)',
 'zero_return_rule':'exclude',
 'model':'ols_logistic',
 'oos_split':'chronological 60/40',
 'null_simulations':100,
}
PREREG_PATH=OUT/'RUN54_PREREGISTRATION.json'

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
    close=d['close'];high=d['high'];low=d['low']
    N=PREREG['lookback']
    # current return
    ret=close/close.shift()-1
    abs_ret=ret.abs()
    # recent cumulative return (momentum)
    recent_cum=close/close.shift(N)-1
    # recent extrema (causal: only past data)
    recent_high=high.rolling(N).max().shift(1)
    recent_low=low.rolling(N).min().shift(1)
    # price location
    rng=(recent_high-recent_low).replace(0,1e-10)
    price_location=(close-recent_low)/rng
    price_location=price_location.clip(0,1)
    # range width (diagnostic)
    range_width=(rng/close).rolling(24).mean()
    # target
    next_ret=close.shift(-1)/close-1
    target=(next_ret>0).astype(int)
    zero_mask=next_ret.abs()<1e-10
    df=pd.DataFrame({'current_return':ret,'abs_return':abs_ret,
                    'recent_cum':recent_cum,'price_location':price_location,
                    'range_width':range_width,'target':target}).dropna()
    df=df[~zero_mask.reindex(df.index,fill_value=False)]
    return df

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

def balanced_acc(y_true,y_pred):
    tp=((y_true==1)&(y_pred==1)).sum();fn=((y_true==1)&(y_pred==0)).sum()
    tn=((y_true==0)&(y_pred==0)).sum();fp=((y_true==0)&(y_pred==1)).sum()
    return (tp/max(tp+fn,1)+tn/max(tn+fp,1))/2

def ols_classify(X_train,y_train,X_test):
    X_=np.column_stack([np.ones(len(X_train)),X_train])
    try:
        beta=np.linalg.lstsq(X_,y_train,rcond=None)[0]
        X_t=np.column_stack([np.ones(len(X_test)),X_test])
        return np.clip(X_t@beta,0,1)
    except:return np.full(len(y_test),y_train.mean())

def evaluate(y_true,y_prob):
    pred=(y_prob>=0.5).astype(int)
    return {'log_loss':round(log_loss_fn(y_true,y_prob),6),
            'brier':round(brier_fn(y_true,y_prob),6),
            'auc':round(roc_auc_fn(y_true,y_prob),4),
            'bal_acc':round(balanced_acc(y_true,pred),4),
            'n':len(y_true),'base_rate':round(float(y_true.mean()),4)}

def run54_full():
    PREREG_PATH.write_text(json.dumps(PREREG,indent=1))
    raw=load_data()
    if not raw:return {'error':'no data'}
    all_results={}
    for t,d in raw.items():
        df=build_features(d)
        if len(df)<200:continue
        n=len(df);split=int(n*0.6)
        train=df.iloc[:split];test=df.iloc[split:]
        y_train=train['target'].values;y_test=test['target'].values
        # standardize using train
        for col in ['current_return','recent_cum','price_location']:
            mean=train[col].mean();std=max(train[col].std(),1e-10)
            train[col+'Z']=(train[col]-mean)/std
            test[col+'Z']=(test[col]-mean)/std
        # B0
        m0=evaluate(y_test,np.full(len(y_test),y_train.mean()))
        # B1: return + magnitude
        b1_Xtrain=np.column_stack([train['current_returnZ'].values,train['abs_return'].values])
        b1_Xtest=np.column_stack([test['current_returnZ'].values,test['abs_return'].values])
        yhat_B1=ols_classify(b1_Xtrain,y_train,b1_Xtest)
        m1=evaluate(y_test,yhat_B1)
        # B2: + recent momentum
        b2_Xtrain=np.column_stack([train['current_returnZ'].values,train['abs_return'].values,train['recent_cumZ'].values])
        b2_Xtest=np.column_stack([test['current_returnZ'].values,test['abs_return'].values,test['recent_cumZ'].values])
        yhat_B2=ols_classify(b2_Xtrain,y_train,b2_Xtest)
        m2=evaluate(y_test,yhat_B2)
        # B3: + price location
        b3_Xtrain=np.column_stack([train['current_returnZ'].values,train['abs_return'].values,train['recent_cumZ'].values,train['price_locationZ'].values])
        b3_Xtest=np.column_stack([test['current_returnZ'].values,test['abs_return'].values,test['recent_cumZ'].values,test['price_locationZ'].values])
        yhat_B3=ols_classify(b3_Xtrain,y_train,b3_Xtest)
        m3=evaluate(y_test,yhat_B3)
        # location states (quintiles)
        q20=train['price_location'].quantile(0.2);q40=train['price_location'].quantile(0.4)
        q60=train['price_location'].quantile(0.6);q80=train['price_location'].quantile(0.8)
        loc_states={}
        for name,lo,hi in [('LOW',0,q20),('LOW_MID',q20,q40),('MID',q40,q60),('HIGH_MID',q60,q80),('HIGH',q80,1.1)]:
            mask=(test['price_location']>=lo)&(test['price_location']<hi)
            if mask.sum()>10:
                loc_states[name]={'n':int(mask.sum()),
                                 'next_up_rate':round(float(y_test[mask].mean()),4)}
        # momentum x location matrix
        mom_up=test['recent_cum'].values>0
        mom_down=test['recent_cum'].values<0
        high_loc=test['price_location'].values>q80
        mid_loc=(test['price_location'].values>=q40)&(test['price_location'].values<=q60)
        low_loc=test['price_location'].values<q20
        matrix={}
        for sm,ml in [(mom_up,'MOM_UP'),(mom_down,'MOM_DOWN')]:
            for lm,ln in [(high_loc,'HIGH_LOC'),(mid_loc,'MID_LOC'),(low_loc,'LOW_LOC')]:
                mask=sm&lm
                if mask.sum()>10:
                    matrix[f'{ml}_{ln}']={'n':int(mask.sum()),
                                         'next_up_rate':round(float(y_test[mask].mean()),4)}
        # redundancy
        corr=train[['current_return','recent_cum','price_location']].corr()
        redundancy={'ret_vs_loc':round(float(corr.loc['current_return','price_location']),4),
                   'cum_vs_loc':round(float(corr.loc['recent_cum','price_location']),4)}
        # null
        rng=np.random.RandomState(42)
        null_deltas=[]
        for _ in range(PREREG['null_simulations']):
            shuf=train['price_locationZ'].values.copy();rng.shuffle(shuf)
            tmp=train.copy();tmp['pl_shuf']=shuf
            shuf_t=test['price_locationZ'].values.copy();rng.shuffle(shuf_t)
            tmp2=test.copy();tmp2['pl_shuf']=shuf_t
            b3_null_Xtrain=np.column_stack([train['current_returnZ'].values,train['abs_return'].values,train['recent_cumZ'].values,shuf])
            b3_null_Xtest=np.column_stack([test['current_returnZ'].values,test['abs_return'].values,test['recent_cumZ'].values,shuf_t])
            yh=ols_classify(b3_null_Xtrain,y_train,b3_null_Xtest)
            nm=evaluate(y_test,yh)
            null_deltas.append(m2['brier']-nm['brier'])
        real_delta=m2['brier']-m3['brier']
        null_pctl=float(np.mean(np.array(null_deltas)<=real_delta))
        # temporal
        blocks=[];bs=len(test)//3
        for i in range(3):
            s=i*bs;e=min((i+1)*bs,len(test))
            yt=y_test[s:e]
            yh2=ols_classify(b2_Xtrain,y_train,b2_Xtest[s:e])
            yh3=ols_classify(b3_Xtrain,y_train,b3_Xtest[s:e])
            blocks.append({'block':i,'n':e-s,
                          'b2_brier':round(brier_fn(yt,yh2),6),
                          'b3_brier':round(brier_fn(yt,yh3),6),
                          'delta':round(brier_fn(yt,yh2)-brier_fn(yt,yh3),6)})
        all_results[t]={'B0':m0,'B1':m1,'B2':m2,'B3':m3,
                       'loc_states':loc_states,'matrix':matrix,
                       'redundancy':redundancy,
                       'null':{'real_delta':round(real_delta,6),'pctl':round(null_pctl,4)},
                       'temporal':blocks}
    deltas=[r['B2']['brier']-r['B3']['brier'] for r in all_results.values()]
    auc_deltas=[r['B3']['auc']-r['B2']['auc'] for r in all_results.values()]
    avg_brier=np.mean(deltas) if deltas else 0
    avg_auc=np.mean(auc_deltas) if auc_deltas else 0
    if avg_brier>0.001 and avg_auc>0.005:sci='PRICE_LOCATION_INCREMENTAL_CONFIRMED'
    elif avg_brier>0:sci='PRICE_LOCATION_CONTEXTUAL'
    else:sci='PRICE_LOCATION_REDUNDANT_WITH_MOMENTUM'
    # program saturation assessment
    run51_delta=0.00018  # from RUN-51
    run52_delta=0.00023
    run53_delta=-0.00064
    run54_delta=avg_brier
    avg_directional_delta=np.mean([run51_delta,run52_delta,run53_delta,run54_delta])
    if avg_directional_delta<0.0005:saturation='OHLCV_DIRECTIONAL_FRONTIER_SATURATED'
    else:saturation='FRONTIER_NOT_SATURATED'
    result={'schema':SCHEMA,'preregistration':PREREG,
            'per_asset':all_results,
            'aggregate':{'avg_brier_delta':round(avg_brier,6),'avg_auc_delta':round(avg_auc,4),
                        'n_assets':len(all_results)},
            'program_saturation':saturation,
            'directional_program_summary':{
                'run51_candle_geometry':round(run51_delta,6),
                'run52_multi_tf':round(run52_delta,6),
                'run53_participation':round(run53_delta,6),
                'run54_price_location':round(run54_delta,6),
                'avg_delta':round(avg_directional_delta,6)},
            'scientific_verdict':sci,'ts':now()}
    (OUT/'run54_full.json').write_text(json.dumps(result,ensure_ascii=False,indent=1,default=str))
    return result

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--out',required=True);a=p.parse_args()
    r_=run54_full()
    Path(a.out).write_text(json.dumps(r_,ensure_ascii=False,indent=1,default=str))
    print('VERDICT:',r_['scientific_verdict'])
    print('SATURATION:',r_['program_saturation'])
    print('AGGREGATE:',r_['aggregate'])
    print('PROGRAM SUMMARY:',r_['directional_program_summary'])
    for t,v in r_['per_asset'].items():
        print(f'{t}: B2={v["B2"]["brier"]} B3={v["B3"]["brier"]} delta={v["B2"]["brier"]-v["B3"]["brier"]:.6f} auc_B3={v["B3"]["auc"]}')
        print(f'  loc_states: {v["loc_states"]}')
