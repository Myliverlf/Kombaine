#!/usr/bin/env python3
"""RUN-57: Weekday Directional Seasonality — Static + Adaptive Calendar Bias.
Deterministic. Paper only. No LLM. No strategy optimization.

Tests if daily direction depends on weekday and if adaptive estimation helps.
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

SCHEMA='run57-weekday-directional-seasonality'
OUT=Path('/root/audits/strategy_combine_research_intelligence/run57')
OUT.mkdir(parents=True,exist_ok=True)
def now():return time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())

PREREG={
 'parent_checkpoint':'SCIENTIFIC_CHECKPOINT_RUN14_49_V2',
 'universe':['IMOEX','SBER','LKOH','CNY'],
 'horizons':{'long':'all','quarter':63,'month':21,'two_weeks':10},
 'min_support':5,
 'null_simulations':100,
}
PREREG_PATH=OUT/'RUN57_PREREGISTRATION.json'

WEEKDAY_NAMES=['MON','TUE','WED','THU','FRI']

def brier_fn(y_true,y_prob):
    return float(np.mean((y_true-y_prob)**2))

def log_loss_fn(y_true,y_prob):
    eps=1e-10;y_prob=np.clip(y_prob,eps,1-eps)
    return float(-np.mean(y_true*np.log(y_prob)+(1-y_true)*np.log(1-y_prob)))

def roc_auc_fn(y_true,y_scores):
    pos=y_scores[y_true==1];neg=y_scores[y_true==0]
    if len(pos)==0 or len(neg)==0: return 0.5
    n_pos=len(pos);n_neg=len(neg)
    all_scores=np.concatenate([pos,neg])
    order=np.argsort(all_scores)
    ranks=np.empty_like(order);ranks[order]=np.arange(1,len(order)+1)
    return float((ranks[:n_pos].sum()-n_pos*(n_pos+1)/2)/(n_pos*n_neg))

def load_daily():
    raw=q15.load_all()
    results={}
    for t in PREREG['universe']:
        if t not in raw:continue
        d=raw[t]['train'].copy()
        if 'time' in d.columns:
            d['time']=pd.to_datetime(d['time']);d=d.set_index('time')
        # resample to daily
        daily=d.resample('1D').agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum'}).dropna()
        if len(daily)<100:continue
        daily['weekday']=daily.index.dayofweek  # 0=Mon,4=Fri
        daily['oc_return']=daily['close']/daily['open']-1
        daily['direction']=(daily['oc_return']>0).astype(int)
        daily['abs_return']=daily['oc_return'].abs()
        daily['range']=daily['high']-daily['low']
        results[t]=daily
    return results

def expanding_weekday_bias(daily_col,n_min=5):
    """For each day, compute past-only weekday bias."""
    bias=[];series=daily_col.values;wd_idx=daily_col.index
    for i in range(len(series)):
        wd=wd_idx[i].dayofweek
        past=series[:i]
        past_wd=past[[idx.dayofweek==wd for idx in daily_col.index[:i]]]
        if len(past_wd)>=n_min:
            bias.append(past_wd.mean())
        else:
            bias.append(np.nan)
    return pd.Series(bias,index=daily_col.index)

def adaptive_weekday_bias(daily_series,daily_idx,horizon_days):
    """Rolling window weekday bias."""
    bias=[]
    vals=daily_series.values
    idx=daily_idx
    for i in range(len(vals)):
        wd=idx[i].dayofweek
        start=max(0,i-horizon_days)
        window=vals[start:i]
        window_wd=[window[j] for j in range(len(window)) if idx[start+j].dayofweek==wd]
        if len(window_wd)>=PREREG['min_support']:
            bias.append(np.mean(window_wd))
        else:
            bias.append(np.nan)
    return pd.Series(bias,index=idx)

def run57_full():
    PREREG_PATH.write_text(json.dumps(PREREG,indent=1))
    raw=load_daily()
    if not raw:return {'error':'no data'}
    all_results={}
    for t,daily in raw.items():
        n=len(daily);split=int(n*0.6)
        train=daily.iloc[:split];test=daily.iloc[split:]
        y_train=train['direction'].values;y_test=test['direction'].values
        base_rate=float(y_train.mean())
        # Static weekday model
        weekday_static={};weekday_stats={}
        for wd in range(5):
            mask_tr=train['weekday']==wd
            mask_te=test['weekday']==wd
            if mask_tr.sum()>PREREG['min_support']:
                p_up=float(train.loc[mask_tr,'direction'].mean())
                weekday_static[wd]=p_up
                weekday_stats[WEEKDAY_NAMES[wd]]={
                    'n_train':int(mask_tr.sum()),'n_test':int(mask_te.sum()),
                    'p_up_train':round(p_up,4),
                    'mean_oc_return':round(float(train.loc[mask_tr,'oc_return'].mean()),6),
                    'median_oc_return':round(float(train.loc[mask_tr,'oc_return'].median()),6),
                    'std_oc_return':round(float(train.loc[mask_tr,'oc_return'].std()),6),
                    'mean_abs_return':round(float(train.loc[mask_tr,'abs_return'].mean()),6)}
        # B0: base rate
        yh_b0=np.full(len(y_test),base_rate)
        b0={'brier':round(brier_fn(y_test,yh_b0),6),'auc':round(roc_auc_fn(y_test,yh_b0),4)}
        # B1: static weekday
        yh_b1=np.array([weekday_static.get(wd,base_rate) for wd in test['weekday'].values])
        b1={'brier':round(brier_fn(y_test,yh_b1),6),'auc':round(roc_auc_fn(y_test,yh_b1),4)}
        # Long-history causal weekday
        long_bias_full=expanding_weekday_bias(daily['direction'],PREREG['min_support'])
        yh_b2=np.array([long_bias_full.iloc[i] if not pd.isna(long_bias_full.iloc[i]) else base_rate 
                        for i in range(split,len(daily))])
        b2={'brier':round(brier_fn(y_test,yh_b2),6),'auc':round(roc_auc_fn(y_test,yh_b2),4)}
        # Adaptive horizons
        adaptive_results={}
        for h_name,h_days in PREREG['horizons'].items():
            if h_name=='long':continue
            adapt=adaptive_weekday_bias(train['direction'],train.index,h_days)
            # For test, recompute with expanding window including test
            full_dir=daily['direction']
            adapt_full=adaptive_weekday_bias(full_dir,daily.index,h_days)
            yh_adapt=adapt_full.iloc[split:].values
            yh_adapt_clean=np.array([v if not pd.isna(v) else base_rate for v in yh_adapt])
            adaptive_results[h_name]={
                'brier':round(brier_fn(y_test,yh_adapt_clean),6),
                'auc':round(roc_auc_fn(y_test,yh_adapt_clean),4),
                'mean_support':round(float(adapt_full.iloc[split:].notna().sum()/len(test)),4)}
        # Adaptive shrinkage: reliability-weighted long+quarter+month
        weights=np.array([0.5,0.3,0.2])  # long, quarter, month — frozen
        combined=np.full(len(test),base_rate)
        for i,idx_te in enumerate(test.index):
            vals=[];ws=[]
            for h_name,h_days,w in zip(['quarter','month','two_weeks'],
                                        [63,21,10],weights[1:]):
                # compute bias up to this point
                full_dir_local=daily['direction']
                if h_name in adaptive_results:
                    full_adapt=adaptive_weekday_bias(full_dir_local,daily.index,h_days)
                    v=full_adapt.iloc[split+i]
                    if not pd.isna(v):
                        vals.append(v);ws.append(w)
            # add long history
            long_v=long_bias_full.iloc[split+i] if (split+i)<len(long_bias_full) and not pd.isna(long_bias_full.iloc[split+i]) else base_rate
            vals.append(long_v);ws.append(weights[0])
            if vals:
                ws=np.array(ws);ws=ws/ws.sum()
                combined[i]=np.average(vals,weights=ws)
        b3={'brier':round(brier_fn(y_test,combined),6),'auc':round(roc_auc_fn(y_test,combined),4)}
        # Temporal stability
        blocks=[];bs=len(test)//3
        for i in range(3):
            s=i*bs;e=min((i+1)*bs,len(test))
            yt=y_test[s:e]
            yh1=np.array([weekday_static.get(wd,base_rate) for wd in test['weekday'].values[s:e]])
            blocks.append({'block':i,'n':e-s,
                          'b0_brier':round(brier_fn(yt,np.full(e-s,base_rate)),6),
                          'b1_brier':round(brier_fn(yt,yh1),6),
                          'b1_auc':round(roc_auc_fn(yt,yh1),4)})
        # Null test
        rng=np.random.RandomState(42)
        null_aucs=[]
        for _ in range(PREREG['null_simulations']):
            shuf_wd=test['weekday'].values.copy()
            for i in range(len(shuf_wd)-1,0,-1):
                j=rng.randint(0,i+1)
                shuf_wd[i],shuf_wd[j]=shuf_wd[j],shuf_wd[i]
            yh_n=np.array([weekday_static.get(wd,base_rate) for wd in shuf_wd])
            null_aucs.append(roc_auc_fn(y_test,yh_n))
        real_auc=b1['auc']
        null_pctl=float(np.mean(np.array(null_aucs)<=real_auc))
        # Monday special
        mon_mask=test['weekday']==0
        fri_mask=test['weekday']==4
        mon_stats={'n':int(mon_mask.sum()),
                  'p_up':round(float(y_test[mon_mask].mean()),4) if mon_mask.sum()>0 else 0,
                  'mean_return':round(float(test.loc[mon_mask,'oc_return'].mean()),6) if mon_mask.sum()>0 else 0}
        fri_stats={'n':int(fri_mask.sum()),
                  'p_up':round(float(y_test[fri_mask].mean()),4) if fri_mask.sum()>0 else 0,
                  'mean_return':round(float(test.loc[fri_mask,'oc_return'].mean()),6) if fri_mask.sum()>0 else 0}
        all_results[t]={'weekday_stats':weekday_stats,
                       'models':{'B0':b0,'B1_static':b1,'B2_long':b2,'B3_adaptive':b3},
                       'adaptive':adaptive_results,
                       'monday':mon_stats,'friday':fri_stats,
                       'temporal':blocks,
                       'null':{'real_auc':round(real_auc,4),'pctl':round(null_pctl,4)}}
    # aggregate
    b1_aucs=[v['models']['B1_static']['auc'] for v in all_results.values()]
    b3_aucs=[v['models']['B3_adaptive']['auc'] for v in all_results.values()]
    avg_b1=np.mean(b1_aucs) if b1_aucs else 0.5
    avg_b3=np.mean(b3_aucs) if b3_aucs else 0.5
    # check if any weekday shows strong effect across assets
    all_weekday_p_up={w:[] for w in WEEKDAY_NAMES}
    for v in all_results.values():
        for wn,ws in v['weekday_stats'].items():
            all_weekday_p_up[wn].append(ws['p_up_train'])
    weekday_breadth={w:(np.mean(ps),np.std(ps)) for w,ps in all_weekday_p_up.items() if ps}
    max_spread=max([v[0] for v in weekday_breadth.values()])-min([v[0] for v in weekday_breadth.values()]) if weekday_breadth else 0
    if avg_b1>0.51 and avg_b3>=avg_b1:sci='WEEKDAY_DIRECTIONAL_EFFECT_CONFIRMED'
    elif avg_b1>0.505:sci='WEEKDAY_DIRECTIONAL_EFFECT_CONTEXTUAL'
    else:sci='WEEKDAY_DIRECTION_NO_INFORMATION'
    result={'schema':SCHEMA,'preregistration':PREREG,
            'per_asset':all_results,
            'aggregate':{'avg_static_auc':round(avg_b1,4),'avg_adaptive_auc':round(avg_b3,4),
                        'weekday_spread':round(max_spread,4),'n_assets':len(all_results)},
            'weekday_breadth':{w:{"mean_p_up":round(v[0],4),"std":round(v[1],4)} for w,v in weekday_breadth.items()},
            'scientific_verdict':sci,'ts':now()}
    (OUT/'run57_full.json').write_text(json.dumps(result,ensure_ascii=False,indent=1,default=str))
    return result

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--out',required=True);a=p.parse_args()
    r_=run57_full()
    Path(a.out).write_text(json.dumps(r_,ensure_ascii=False,indent=1,default=str))
    print('VERDICT:',r_['scientific_verdict'])
    print('AGGREGATE:',r_['aggregate'])
    print('WEEKDAY BREADTH:',r_['weekday_breadth'])
    for t,v in r_['per_asset'].items():
        m=v['models']
        print(f'{t}: B0={m["B0"]["auc"]} B1={m["B1_static"]["auc"]} B2={m["B2_long"]["auc"]} B3={m["B3_adaptive"]["auc"]}')
        print(f'  weekday: {v["weekday_stats"]}')
