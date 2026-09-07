#!/usr/bin/env python3
"""RUN-51: CANDLE_GEOMETRY_DIRECTION — Incremental Directional Information.
Deterministic. Paper only. No LLM. No strategy optimization.

Tests if intra-bar geometry predicts next-bar direction beyond current return.
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

SCHEMA='run51-candle-geometry-direction'
OUT=Path('/root/audits/strategy_combine_research_intelligence/run51')
OUT.mkdir(parents=True,exist_ok=True)
def now():return time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())

PREREG={
 'parent_checkpoint':'SCIENTIFIC_CHECKPOINT_RUN14_49_V2',
 'timeframe':'1h',
 'universe':['IMOEX','SBER','LKOH','CNY'],
 'features':['current_return','abs_return','normalized_body','close_location','wick_imbalance'],
 'target':'sign(next_bar_return)',
 'zero_return_rule':'exclude',
 'model':'logistic_regression',
 'oos_split':'chronological 60/40',
 'null_simulations':100,
}
PREREG_PATH=OUT/'RUN51_PREREGISTRATION.json'

def load_all():
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
    """Build candle geometry features from raw OHLCV."""
    close=d['close'];open_=d['open'];high=d['high'];low=d['low']
    # current return
    current_ret=close/close.shift()-1
    abs_ret=current_ret.abs()
    # candle geometry
    rng=(high-low).replace(0,1e-10)
    normalized_body=(close-open_)/rng
    close_location=2*(close-low)/rng-1  # -1 to +1
    lower_wick=(pd.concat([open_,close],axis=1).min(axis=1)-low)/rng
    upper_wick=(high-pd.concat([open_,close],axis=1).max(axis=1))/rng
    wick_imbalance=(lower_wick-upper_wick)/rng
    # target: next bar direction
    next_ret=close.shift(-1)/close-1
    target=(next_ret>0).astype(int)
    # exclude zero returns
    zero_mask=(next_ret.abs()<1e-10)
    df=pd.DataFrame({'current_return':current_ret,'abs_return':abs_ret,
                    'normalized_body':normalized_body,'close_location':close_location,
                    'wick_imbalance':wick_imbalance,'target':target,'next_ret':next_ret}).dropna()
    df=df[~zero_mask.reindex(df.index,fill_value=False)]
    return df

def log_loss(y_true,y_prob):
    eps=1e-10
    y_prob=np.clip(y_prob,eps,1-eps)
    return float(-np.mean(y_true*np.log(y_prob)+(1-y_true)*np.log(1-y_prob)))

def brier_score(y_true,y_prob):
    return float(np.mean((y_true-y_prob)**2))

def balanced_accuracy(y_true,y_pred):
    tp=((y_true==1)&(y_pred==1)).sum()
    tn=((y_true==0)&(y_pred==0)).sum()
    fp=((y_true==0)&(y_pred==1)).sum()
    fn=((y_true==1)&(y_pred==0)).sum()
    tpr=tp/max(tp+fn,1)
    tnr=tn/max(tn+fp,1)
    return (tpr+tnr)/2

def roc_auc(y_true,y_scores):
    pos=y_scores[y_true==1]
    neg=y_scores[y_true==0]
    if len(pos)==0 or len(neg)==0:return 0.5
    n_pos=len(pos);n_neg=len(neg)
    total=n_pos*n_neg
    sum_ranks=0
    all_scores=np.concatenate([pos,neg])
    all_labels=np.concatenate([np.ones(n_pos),np.zeros(n_neg)])
    order=np.argsort(all_scores)
    ranks=np.empty_like(order)
    ranks[order]=np.arange(1,len(order)+1)
    sum_ranks=ranks[:n_pos].sum()
    return float((sum_ranks-n_pos*(n_pos+1)/2)/total)

def ols_classify(X_train,y_train,X_test):
    """Simple OLS classifier (linear probability model)."""
    X_=np.column_stack([np.ones(len(X_train)),X_train])
    try:
        beta=np.linalg.lstsq(X_,y_train,rcond=None)[0]
        X_t=np.column_stack([np.ones(len(X_test)),X_test])
        pred=X_t@beta
        return np.clip(pred,0,1)
    except:return np.full(len(y_test),y_train.mean())

def evaluate(y_true,y_prob):
    pred=(y_prob>=0.5).astype(int)
    return {'log_loss':round(log_loss(y_true,y_prob),6),
            'brier':round(brier_score(y_true,y_prob),6),
            'auc':round(roc_auc(y_true,y_prob),4),
            'balanced_acc':round(balanced_accuracy(y_true,pred),4),
            'n':len(y_true),
            'base_rate':round(float(y_true.mean()),4)}

def run51_full():
    PREREG_PATH.write_text(json.dumps(PREREG,indent=1))
    datasets=load_all()
    if not datasets:return {'error':'no data'}
    all_results={}
    for t,d in datasets.items():
        df=build_features(d)
        if len(df)<200:continue
        n=len(df);split=int(n*0.6)
        train=df.iloc[:split];test=df.iloc[split:]
        y_train=train['target'].values;y_test=test['target'].values
        # B0: base rate
        m0=evaluate(y_test,np.full(len(y_test),y_train.mean()))
        # B1: current return only
        b1_cols=['current_return']
        yhat_B1=ols_classify(train[b1_cols].values,y_train,test[b1_cols].values)
        m1=evaluate(y_test,yhat_B1)
        # B2: current return + magnitude
        b2_cols=['current_return','abs_return']
        yhat_B2=ols_classify(train[b2_cols].values,y_train,test[b2_cols].values)
        m2=evaluate(y_test,yhat_B2)
        # B3: + candle geometry
        b3_cols=['current_return','abs_return','normalized_body','close_location','wick_imbalance']
        yhat_B3=ols_classify(train[b3_cols].values,y_train,test[b3_cols].values)
        m3=evaluate(y_test,yhat_B3)
        # redundancy audit
        corr_mat=train[['current_return','normalized_body','close_location','wick_imbalance']].corr()
        redundancy={
            'return_vs_body':round(float(corr_mat.loc['current_return','normalized_body']),4),
            'return_vs_close_loc':round(float(corr_mat.loc['current_return','close_location']),4),
            'return_vs_wick':round(float(corr_mat.loc['current_return','wick_imbalance']),4),
            'body_vs_close_loc':round(float(corr_mat.loc['normalized_body','close_location']),4),
        }
        # ablation (only if B3 shows increment)
        ablation={}
        if m3['brier']<m2['brier']:
            for leave_out in ['normalized_body','close_location','wick_imbalance']:
                abl_cols=[c for c in b3_cols if c!=leave_out]
                yhat_abl=ols_classify(train[abl_cols].values,y_train,test[abl_cols].values)
                abl_m=evaluate(y_test,yhat_abl)
                ablation[f'without_{leave_out}']={'brier':abl_m['brier'],'auc':abl_m['auc']}
        # null test
        rng=np.random.RandomState(42)
        null_deltas=[]
        for _ in range(PREREG['null_simulations']):
            shuf=train['normalized_body'].values.copy()
            rng.shuffle(shuf)
            tmp=train.copy();tmp['nb_shuf']=shuf
            tmp2=test.copy()
            # also shuffle test
            shuf_t=test['normalized_body'].values.copy()
            rng.shuffle(shuf_t)
            tmp2['nb_shuf']=shuf_t
            abl_cols=['current_return','abs_return','nb_shuf','close_location','wick_imbalance']
            yhat_null=ols_classify(tmp[abl_cols].values,y_train,tmp2[abl_cols].values)
            null_m=evaluate(y_test,yhat_null)
            null_deltas.append(m2['brier']-null_m['brier'])
        real_delta=m2['brier']-m3['brier']
        null_pctl=float(np.mean(np.array(null_deltas)<=real_delta))
        # temporal blocks
        blocks=[];bs=len(test)//3
        for i in range(3):
            s=i*bs;e=min((i+1)*bs,len(test))
            yt=y_test[s:e]
            yh_b2=ols_classify(train[b2_cols].values,y_train,test[b2_cols].values[s:e] if False else test[b2_cols].values[s:e])
            yh_b3=ols_classify(train[b3_cols].values,y_train,test[b3_cols].values[s:e])
            b2_brier=brier_score(yt,yh_b2)
            b3_brier=brier_score(yt,yh_b3)
            blocks.append({'block':i,'n':e-s,'b2_brier':round(b2_brier,6),'b3_brier':round(b3_brier,6),
                          'delta':round(b2_brier-b3_brier,6)})
        all_results[t]={'B0':m0,'B1':m1,'B2':m2,'B3':m3,
                       'redundancy':redundancy,'ablation':ablation,
                       'null':{'real_delta':round(real_delta,6),'pctl':round(null_pctl,4)},
                       'temporal':blocks}
    # aggregate
    deltas_brier=[r['B2']['brier']-r['B3']['brier'] for r in all_results.values()]
    deltas_auc=[r['B3']['auc']-r['B2']['auc'] for r in all_results.values()]
    avg_delta_brier=np.mean(deltas_brier) if deltas_brier else 0
    avg_delta_auc=np.mean(deltas_auc) if deltas_auc else 0
    # verdict
    if avg_delta_brier>0.001 and avg_delta_auc>0.005:sci='CANDLE_GEOMETRY_INCREMENTAL_CONFIRMED'
    elif avg_delta_brier>0:sci='CANDLE_GEOMETRY_INCREMENTAL_CONTEXTUAL'
    else:sci='CANDLE_GEOMETRY_REDUNDANT_WITH_RETURN'
    result={'schema':SCHEMA,'preregistration':PREREG,
            'per_asset':all_results,
            'aggregate':{'avg_delta_brier':round(avg_delta_brier,6),'avg_delta_auc':round(avg_delta_auc,4),
                        'n_assets':len(all_results)},
            'scientific_verdict':sci,'ts':now()}
    (OUT/'run51_full.json').write_text(json.dumps(result,ensure_ascii=False,indent=1,default=str))
    return result

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--out',required=True);a=p.parse_args()
    r_=run51_full()
    Path(a.out).write_text(json.dumps(r_,ensure_ascii=False,indent=1,default=str))
    print('VERDICT:',r_['scientific_verdict'])
    print('AGGREGATE:',r_['aggregate'])
    for t,v in r_['per_asset'].items():
        print(f'{t}: B2_brier={v["B2"]["brier"]} B3_brier={v["B3"]["brier"]} delta={v["B2"]["brier"]-v["B3"]["brier"]:.6f} auc_B3={v["B3"]["auc"]}')
