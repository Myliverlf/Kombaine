#!/usr/bin/env python3
"""RUN-58: Intraday Day-Type Transitions — Path → Next Day State.
Deterministic. Paper only. No LLM. No strategy optimization.

Tests if intraday trajectory type of day D predicts day D+1.
Parent checkpoint: SCIENTIFIC_CHECKPOINT_RUN14_49_V2
Parent RUN: RUN-57
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

SCHEMA='run58-intraday-day-type-transitions'
OUT=Path('/root/audits/strategy_combine_research_intelligence/run58')
OUT.mkdir(parents=True,exist_ok=True)
def now():return time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())

PREREG={
 'parent_checkpoint':'SCIENTIFIC_CHECKPOINT_RUN14_49_V2',
 'parent_run':57,
 'universe':['IMOEX','SBER','LKOH','CNY'],
 'reversal_scale':'rolling_median_range',
 'min_class_support':8,
 'null_simulations':100,
}
PREREG_PATH=OUT/'RUN58_PREREGISTRATION.json'

DAY_TYPES=['TREND_UP','TREND_DOWN','REV_UP','REV_DOWN','CHOP']
N_TYPES=len(DAY_TYPES)
TYPE_MAP={t:i for i,t in enumerate(DAY_TYPES)}

def brier_fn(y_true,y_prob):
    if y_prob.ndim==1:return float(np.mean((y_true-y_prob)**2))
    return float(np.mean(np.sum((pd.get_dummies(y_true).values-y_prob)**2,axis=1)))

def log_loss_fn(y_true,y_prob,n_classes=5):
    eps=1e-10;y_prob=np.clip(y_prob,eps,1-eps)
    y_onehot=pd.get_dummies(y_true).reindex(range(n_classes),axis=1,fill_value=0).values
    return float(-np.mean(np.sum(y_onehot*np.log(y_prob),axis=1)))

def load_15m():
    raw=q15.load_all()
    results={}
    for t in PREREG['universe']:
        if t not in raw:continue
        d=raw[t]['train'].copy()
        if 'time' in d.columns:
            d['time']=pd.to_datetime(d['time']);d=d.set_index('time')
        # Use 1H bars and resample to daily for path classification
        # Each 1H bar = one observation within the day
        results[t]=d
    return results

def classify_days(hourly_df):
    """Classify each trading day from hourly bars."""
    if 'time' in hourly_df.columns:
        hourly_df=hourly_df.set_index('time')
    hourly_df=hourly_df.copy()
    hourly_df['date']=hourly_df.index.date
    days=[]
    for day_date, group in hourly_df.groupby('date'):
        if len(group)<4:continue  # need minimum bars
        o=group['open'].iloc[0]
        c=group['close'].iloc[-1]
        h=group['high'].max()
        l=group['low'].min()
        oc_return=c/o-1
        rng=h-l
        if rng<1e-12:continue
        # Path efficiency: net move / total path
        prices=group['close'].values
        total_path=np.sum(np.abs(np.diff(prices)))
        net_move=abs(c-o)
        path_eff=net_move/max(total_path,1e-12)
        # Max excursion
        max_up=(group['high']-o).max()
        max_down=(o-group['low']).max()
        # Close location
        close_loc=(c-l)/rng
        # Open crosses
        crosses=int(((pd.Series(prices)>o).astype(int).diff().abs().sum()))
        # Time of high/low
        time_high=(group['high']==h).idxmax()
        time_low=(group['low']==l).idxmax()
        # Classification
        if abs(oc_return)<1e-10:
            day_type='CHOP'
        elif oc_return>0:
            if path_eff>0.5 and max_down/rng<0.3:
                day_type='TREND_UP'
            elif max_down/rng>0.4 and close_loc>0.5:
                day_type='REV_UP'
            else:
                day_type='CHOP' if path_eff<0.3 else 'TREND_UP'
        else:
            if path_eff>0.5 and max_up/rng<0.3:
                day_type='TREND_DOWN'
            elif max_up/rng>0.4 and close_loc<0.5:
                day_type='REV_DOWN'
            else:
                day_type='CHOP' if path_eff<0.3 else 'TREND_DOWN'
        days.append({'date':day_date,'open':o,'close':c,'high':h,'low':l,
                    'oc_return':oc_return,'range':rng,'path_eff':path_eff,
                    'max_up_exc':max_up,'max_down_exc':max_down,
                    'close_loc':close_loc,'crosses':crosses,
                    'day_type':day_type,'type_id':TYPE_MAP[day_type]})
    return pd.DataFrame(days)

def run58_full():
    PREREG_PATH.write_text(json.dumps(PREREG,indent=1))
    raw=load_15m()
    if not raw:return {'error':'no data'}
    all_results={}
    for t,hourly in raw.items():
        daily_df=classify_days(hourly)
        if len(daily_df)<60:continue
        daily_df=daily_df.reset_index(drop=True)
        # Shift to get next-day targets
        daily_df['next_type_id']=daily_df['type_id'].shift(-1)
        daily_df['next_oc_return']=daily_df['oc_return'].shift(-1)
        daily_df['next_direction']=(daily_df['next_oc_return']>0).astype(int)
        daily_df['prev_oc_return']=daily_df['oc_return'].shift(1)
        daily_df['prev_type_id']=daily_df['type_id'].shift(1)
        daily_df['prev_abs_return']=daily_df['oc_return'].abs().shift(1)
        df=daily_df.dropna(subset=['next_type_id','prev_type_id']).copy()
        if len(df)<40:continue
        n=len(df);split=int(n*0.6)
        train=df.iloc[:split];test=df.iloc[split:]
        # Class support
        class_support={TYPE_MAP[dn]:int((train['type_id']==TYPE_MAP[dn]).sum()) for dn in DAY_TYPES}
        # B0: unconditional next-type distribution
        base_dist=np.array([train['next_type_id'].value_counts().get(i,0) for i in range(N_TYPES)])
        base_dist=base_dist/base_dist.sum()
        y_test_types=test['next_type_id'].values.astype(int)
        yh_b0=np.tile(base_dist,(len(test),1))
        b0_ll=log_loss_fn(y_test_types,yh_b0)
        # B1: prev return only
        from sklearn.linear_model import LogisticRegression
        X1_train=train[['prev_oc_return','prev_abs_return']].values
        X1_test=test[['prev_oc_return','prev_abs_return']].values
        try:
            lr1=LogisticRegression(max_iter=500,multi_class='multinomial',solver='lbfgs',C=1.0)
            lr1.fit(X1_train,train['next_type_id'].values.astype(int))
            yh_b1=lr1.predict_proba(X1_test)
            b1_ll=log_loss_fn(y_test_types,yh_b1)
            b1_acc=float((lr1.predict(X1_test)==y_test_types).mean())
        except:
            yh_b1=yh_b0;b1_ll=b0_ll;b1_acc=0.2
        # B2: + day type
        # One-hot encode prev_type_id
        def one_hot(vals,n=N_TYPES):
            oh=np.zeros((len(vals),n))
            for i,v in enumerate(vals):
                oh[i,int(v)]=1
            return oh
        X2_train=np.column_stack([X1_train,one_hot(train['prev_type_id'].values)])
        X2_test=np.column_stack([X1_test,one_hot(test['prev_type_id'].values)])
        try:
            lr2=LogisticRegression(max_iter=500,multi_class='multinomial',solver='lbfgs',C=1.0)
            lr2.fit(X2_train,train['next_type_id'].values.astype(int))
            yh_b2=lr2.predict_proba(X2_test)
            b2_ll=log_loss_fn(y_test_types,yh_b2)
            b2_acc=float((lr2.predict(X1_test)==y_test_types).mean())  # bug: should use lr2
            b2_acc=float((lr2.predict(X2_test)==y_test_types).mean())
        except:
            yh_b2=yh_b1;b2_ll=b1_ll;b2_acc=b1_acc
        # Binary direction model
        y_dir_train=train['next_direction'].values;y_dir_test=test['next_direction'].values
        def logistic_binary(X_tr,y_tr,X_te):
            X_=np.column_stack([np.ones(len(X_tr)),X_tr])
            try:
                beta=np.linalg.lstsq(X_,y_tr,rcond=None)[0]
                X_t=np.column_stack([np.ones(len(X_te)),X_te])
                return np.clip(X_t@beta,0,1)
            except:return np.full(len(X_te),y_tr.mean())
        yh_d0=np.full(len(test),y_dir_train.mean())
        d0_brier=float(np.mean((y_dir_test-yh_d0)**2))
        yh_d1=logistic_binary(X1_train,y_dir_train,X1_test)
        d1_brier=float(np.mean((y_dir_test-yh_d1)**2))
        yh_d2=logistic_binary(X2_train,y_dir_train,X2_test)
        d2_brier=float(np.mean((y_dir_test-yh_d2)**2))
        # Transition matrix
        trans_counts=np.zeros((N_TYPES,N_TYPES),dtype=int)
        for _,row in train.iterrows():
            trans_counts[int(row['type_id']),int(row['next_type_id'])]+=1
        trans_probs=trans_counts/trans_counts.sum(axis=1,keepdims=True).clip(1)
        # Direction by prev type
        dir_by_type={}
        for dn in DAY_TYPES:
            tid=TYPE_MAP[dn]
            mask=test['prev_type_id']==tid
            if mask.sum()>3:
                dir_by_type[dn]={'n':int(mask.sum()),
                                'p_up':round(float(y_dir_test[mask].mean()),4),
                                'mean_return':round(float(test.loc[mask,'next_oc_return'].mean()),6)}
        # Null test
        rng=np.random.RandomState(42)
        null_deltas=[]
        for _ in range(PREREG['null_simulations']):
            shuf_types=train['prev_type_id'].values.copy();rng.shuffle(shuf_types)
            shuf_types_t=test['prev_type_id'].values.copy();rng.shuffle(shuf_types_t)
            X2_shuf_tr=np.column_stack([X1_train,one_hot(shuf_types)])
            X2_shuf_te=np.column_stack([X1_test,one_hot(shuf_types_t)])
            try:
                lr_shuf=LogisticRegression(max_iter=500,multi_class='multinomial',solver='lbfgs',C=1.0)
                lr_shuf.fit(X2_shuf_tr,train['next_type_id'].values.astype(int))
                yh_shuf=lr_shuf.predict_proba(X2_shuf_te)
                null_deltas.append(b1_ll-log_loss_fn(y_test_types,yh_shuf))
            except:null_deltas.append(0)
        real_delta=b1_ll-b2_ll
        null_pctl=float(np.mean(np.array(null_deltas)>=real_delta))
        # Temporal
        blocks=[];bs=len(test)//3
        for i in range(3):
            s=i*bs;e=min((i+1)*bs,len(test))
            yt=y_test_types[s:e]
            yh2_b=yh_b2[s:e] if hasattr(yh_b2,"__len__") else np.tile(base_dist,(e-s,1))
            blocks.append({'block':i,'n':e-s,
                          'b1_ll':round(float(log_loss_fn(yt,yh_b1[s:e])),6),
                          'b2_ll':round(float(log_loss_fn(yt,yh2_b)),6)})
        all_results[t]={'class_support':class_support,
                       'models':{'B0_logloss':round(b0_ll,6),'B1_logloss':round(b1_ll,6),
                                'B2_logloss':round(b2_ll,6),'B1_acc':round(b1_acc,4),'B2_acc':round(b2_acc,4)},
                       'binary':{'D0_brier':round(d0_brier,6),'D1_brier':round(d1_brier,6),'D2_brier':round(d2_brier,6)},
                       'transition_matrix':trans_probs.round(4).tolist(),
                       'dir_by_type':dir_by_type,
                       'null':{'real_delta':round(real_delta,6),'pctl':round(null_pctl,4)},
                       'temporal':blocks}
    # aggregate
    b_deltas=[v['models']['B1_logloss']-v['models']['B2_logloss'] for v in all_results.values()]
    d_deltas=[v['binary']['D1_brier']-v['binary']['D2_brier'] for v in all_results.values()]
    avg_b=np.mean(b_deltas) if b_deltas else 0
    avg_d=np.mean(d_deltas) if d_deltas else 0
    if avg_b>0.01 and avg_d>0.005:sci='DAY_TYPE_TRANSITIONS_CONFIRMED'
    elif avg_b>0.001 or avg_d>0.001:sci='DAY_TYPE_TRANSITIONS_CONTEXTUAL'
    else:sci='DAY_TYPE_REDUNDANT_WITH_DAILY_RETURN'
    result={'schema':SCHEMA,'preregistration':PREREG,
            'per_asset':all_results,
            'aggregate':{'avg_multiclass_delta':round(avg_b,6),'avg_binary_delta':round(avg_d,6),'n_assets':len(all_results)},
            'scientific_verdict':sci,'ts':now()}
    (OUT/'run58_full.json').write_text(json.dumps(result,ensure_ascii=False,indent=1,default=str))
    return result

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--out',required=True);a=p.parse_args()
    r_=run58_full()
    Path(a.out).write_text(json.dumps(r_,ensure_ascii=False,indent=1,default=str))
    print('VERDICT:',r_['scientific_verdict'])
    print('AGGREGATE:',r_['aggregate'])
    for t,v in r_['per_asset'].items():
        m=v['models']
        print(f'{t}: B1_ll={m["B1_logloss"]} B2_ll={m["B2_logloss"]} delta={m["B1_logloss"]-m["B2_logloss"]:.6f}')
        print(f'  support: {v["class_support"]}')
        print(f'  dir_by_type: {v["dir_by_type"]}')
