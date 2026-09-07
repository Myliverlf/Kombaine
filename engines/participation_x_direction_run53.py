#!/usr/bin/env python3
"""RUN-53: PARTICIPATION × DIRECTION — Incremental Directional Information.
Deterministic. Paper only. No LLM. No strategy optimization.

Tests if participation modifies directional meaning of price moves.
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

SCHEMA='run53-participation-x-direction'
OUT=Path('/root/audits/strategy_combine_research_intelligence/run53')
OUT.mkdir(parents=True,exist_ok=True)
def now():return time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())

PREREG={
 'parent_checkpoint':'SCIENTIFIC_CHECKPOINT_RUN14_49_V2',
 'parent_runs':['RUN-51','RUN-52'],
 'participation':'frozen relative volume from RUN-41/42',
 'timeframe':'1h',
 'universe':['IMOEX','SBER','LKOH','CNY'],
 'target':'sign(next_1h_return)',
 'zero_return_rule':'exclude',
 'model':'ols_logistic',
 'oos_split':'chronological 60/40',
 'null_simulations':100,
 'high_participation_threshold':'top quartile in train',
}
PREREG_PATH=OUT/'RUN53_PREREGISTRATION.json'

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
    close=d['close'];vol=d['volume']
    # current return
    ret=close/close.shift()-1
    abs_ret=ret.abs()
    # participation (frozen: expanding z-score of volume)
    vol_mean=vol.expanding(min_periods=120).mean()
    vol_std=vol.expanding(min_periods=120).std().replace(0,np.nan)
    participation=(vol-vol_mean)/vol_std
    # interaction (standardized then multiplied)
    # will be computed after train/test split using train stats
    # target
    next_ret=close.shift(-1)/close-1
    target=(next_ret>0).astype(int)
    # future magnitude (diagnostic)
    future_abs=next_ret.abs()
    zero_mask=next_ret.abs()<1e-10
    df=pd.DataFrame({'current_return':ret,'abs_return':abs_ret,
                    'participation':participation,'target':target,
                    'future_abs_return':future_abs}).dropna()
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

def run53_full():
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
        # standardize using train stats
        cr_mean=train['current_return'].mean();cr_std=train['current_return'].std()
        part_mean=train['participation'].mean();part_std=train['participation'].std()
        cr_std=max(cr_std,1e-10);part_std=max(part_std,1e-10)
        # train features
        cr_train=(train['current_return'].values-cr_mean)/cr_std
        ar_train=train['abs_return'].values
        part_train=(train['participation'].values-part_mean)/part_std
        inter_train=cr_train*part_train
        # test features
        cr_test=(test['current_return'].values-cr_mean)/cr_std
        ar_test=test['abs_return'].values
        part_test=(test['participation'].values-part_mean)/part_std
        inter_test=cr_test*part_test
        # high participation threshold
        thr_high=np.percentile(part_train,75)
        high_mask_test=part_test>thr_high
        # B0: base rate
        m0=evaluate(y_test,np.full(len(y_test),y_train.mean()))
        # B1: return + magnitude
        b1_Xtrain=np.column_stack([cr_train,ar_train])
        b1_Xtest=np.column_stack([cr_test,ar_test])
        yhat_B1=ols_classify(b1_Xtrain,y_train,b1_Xtest)
        m1=evaluate(y_test,yhat_B1)
        # B2: + participation
        b2_Xtrain=np.column_stack([cr_train,ar_train,part_train])
        b2_Xtest=np.column_stack([cr_test,ar_test,part_test])
        yhat_B2=ols_classify(b2_Xtrain,y_train,b2_Xtest)
        m2=evaluate(y_test,yhat_B2)
        # B3: + interaction
        b3_Xtrain=np.column_stack([cr_train,ar_train,part_train,inter_train])
        b3_Xtest=np.column_stack([cr_test,ar_test,part_test,inter_test])
        yhat_B3=ols_classify(b3_Xtrain,y_train,b3_Xtest)
        m3=evaluate(y_test,yhat_B3)
        # 2x2 state map
        up_mask=cr_test>0;down_mask=cr_test<0
        states={}
        for sd,dir_name in [(up_mask,'UP'),(down_mask,'DOWN')]:
            for sp,label in [(high_mask_test,'HIGH_PART'),(~high_mask_test,'NORMAL_PART')]:
                mask=sd&sp
                if mask.sum()>10:
                    states[f'{dir_name}_{label}']={
                        'n':int(mask.sum()),
                        'next_up_rate':round(float(y_test[mask].mean()),4),
                        'mean_next_return':round(float(test['future_abs_return'].values[mask].mean()),6) if 'future_abs_return' in test else None,
                        'mean_current_return':round(float(np.abs(cr_test[mask]).mean()),6)}
        # continuation vs reversal
        cont_rev={}
        for sd,dir_name in [(up_mask,'UP'),(down_mask,'DOWN')]:
            mask=sd
            if mask.sum()>10:
                # continuation = same direction next bar
                if dir_name=='UP':cont_rate=float(y_test[mask].mean())
                else:cont_rate=float((1-y_test[mask]).mean())
                cont_rev[dir_name]={'continuation_rate':round(cont_rate,4),
                                   'reversal_rate':round(1-cont_rate,4)}
        # null test
        rng=np.random.RandomState(42)
        null_deltas=[]
        for _ in range(PREREG['null_simulations']):
            shuf=part_train.copy();rng.shuffle(shuf)
            inter_null=cr_train*shuf
            b3_null_Xtrain=np.column_stack([cr_train,ar_train,shuf,inter_null])
            # also shuffle test participation
            shuf_t=part_test.copy();rng.shuffle(shuf_t)
            inter_null_t=cr_test*shuf_t
            b3_null_Xtest=np.column_stack([cr_test,ar_test,shuf_t,inter_null_t])
            yh_null=ols_classify(b3_null_Xtrain,y_train,b3_null_Xtest)
            nm=evaluate(y_test,yh_null)
            null_deltas.append(m2['brier']-nm['brier'])
        real_delta=m2['brier']-m3['brier']
        null_pctl=float(np.mean(np.array(null_deltas)<=real_delta))
        # redundancy
        corr_mat=train[['current_return','abs_return','participation']].corr()
        redundancy={'ret_vs_part':round(float(corr_mat.loc['current_return','participation']),4),
                   'absret_vs_part':round(float(corr_mat.loc['abs_return','participation']),4)}
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
                       'states':states,'cont_rev':cont_rev,
                       'redundancy':redundancy,
                       'null':{'real_delta':round(real_delta,6),'pctl':round(null_pctl,4)},
                       'temporal':blocks}
    # aggregate
    deltas_b2b3=[r['B2']['brier']-r['B3']['brier'] for r in all_results.values()]
    deltas_auc=[r['B3']['auc']-r['B2']['auc'] for r in all_results.values()]
    avg_brier=np.mean(deltas_b2b3) if deltas_b2b3 else 0
    avg_auc=np.mean(deltas_auc) if deltas_auc else 0
    if avg_brier>0.001 and avg_auc>0.005:sci='PARTICIPATION_DIRECTION_INTERACTION_CONFIRMED'
    elif avg_brier>0:sci='PARTICIPATION_DIRECTION_INTERACTION_CONTEXTUAL'
    else:sci='PARTICIPATION_PREDICTS_ACTIVITY_NOT_DIRECTION'
    result={'schema':SCHEMA,'preregistration':PREREG,
            'per_asset':all_results,
            'aggregate':{'avg_brier_delta':round(avg_brier,6),'avg_auc_delta':round(avg_auc,4),
                        'n_assets':len(all_results)},
            'scientific_verdict':sci,'ts':now()}
    (OUT/'run53_full.json').write_text(json.dumps(result,ensure_ascii=False,indent=1,default=str))
    return result

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--out',required=True);a=p.parse_args()
    r_=run53_full()
    Path(a.out).write_text(json.dumps(r_,ensure_ascii=False,indent=1,default=str))
    print('VERDICT:',r_['scientific_verdict'])
    print('AGGREGATE:',r_['aggregate'])
    for t,v in r_['per_asset'].items():
        print(f'{t}: B2={v["B2"]["brier"]} B3={v["B3"]["brier"]} delta={v["B2"]["brier"]-v["B3"]["brier"]:.6f} auc_B3={v["B3"]["auc"]}')
        print(f'  states: {v["states"]}')
        print(f'  cont_rev: {v["cont_rev"]}')
