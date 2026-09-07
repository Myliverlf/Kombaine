#!/usr/bin/env python3
"""RUN-11: deterministic Research Intelligence integration + equal-budget tournament.

Research proposes; existing run_candidate produces economic evidence; canonical
ResearchRun + ExperimentMemory store durable evidence. This module never imports
broker clients, never mutates pool/config, and never promotes candidates.

All feature work excludes final HOLDOUT_DAYS before observations, thresholds,
branch allocation, planning and backtest. Cross asset is strictly lagged >=1.
"""
from __future__ import annotations
import hashlib, json, math, os, time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
import numpy as np
import pandas as pd

SC=Path('/root/prop-desk/strategy_combine'); FL=Path('/root/prop-desk/futures_lab')
DATA=FL/'artifacts'/'tinkoff_futures_data'; HOLDOUT_DAYS=60
STATE=SC/'state'/'research_intelligence'; RUNS=STATE/'run11_runs'
BRANCHES=('trend','mean_reversion','volume_liquidity','volatility','cross_asset','regimes','intraday_microstructure')
SCHEMA='research-intelligence-run11-v1'; COST={'commission_per_contract':5.0,'slippage_bps':1.0,'capital':20000.0}

def canon(x): return json.dumps(x,sort_keys=True,separators=(',',':'),ensure_ascii=False)
def sid(prefix,x): return prefix+'_'+hashlib.sha256(canon(x).encode()).hexdigest()[:16]
def now(): return time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())
def file_hash(p):
 h=hashlib.sha256();
 with open(p,'rb') as f:
  for b in iter(lambda:f.read(65536),b''): h.update(b)
 return h.hexdigest()[:16]
def code_hash(): return file_hash(Path(__file__))

def load(ticker):
 p=DATA/f'{ticker}_365d_1h_continuous.csv'
 if not p.exists(): raise FileNotFoundError(p)
 d=pd.read_csv(p).reset_index(drop=True); d['time']=pd.to_datetime(d['time']); return d,p

def train_only(d):
 cut=d.time.max()-pd.Timedelta(days=HOLDOUT_DAYS)
 return d[d.time<cut].reset_index(drop=True),cut

def raw(d,name):
 c=d.close.astype(float)
 if name in d: return d[name].astype(float)
 if name=='ret1': return np.log(c/c.shift())
 if name=='hour_sin': return pd.Series(np.sin(2*np.pi*d.time.dt.hour/24),index=d.index)
 raise ValueError(name)

# expressions are bounded declarative JSON, no eval/callback code.
# cross_return is permitted only with lag>=1; it is the sole cross-asset primitive.
def validate(expr,depth=0):
 if not isinstance(expr,dict) or depth>3: raise ValueError('invalid DSL depth')
 op=expr.get('op')
 if op=='raw':
  if expr.get('name') not in {'close','volume','ret1','hour_sin'}: raise ValueError('unknown raw')
  return 1
 if op=='cross_return':
  if expr.get('ticker') not in {'IMOEX','CNY'} or int(expr.get('lag',0))<1: raise ValueError('cross asset requires known lag>=1')
  return 1
 if op not in {'zscore','difference','slope','ratio','vol_ratio'}: raise ValueError('unsafe op')
 n=validate(expr.get('x'),depth+1)
 if op=='ratio': n+=validate(expr.get('y'),depth+1)
 if op in {'zscore','slope','vol_ratio'}:
  w=int(expr.get('window',0));
  if not 2<=w<=120: raise ValueError('window out of bound')
 if op=='difference':
  k=int(expr.get('lag',0));
  if not 1<=k<=72: raise ValueError('lag out of bound')
 if n>7: raise ValueError('too complex')
 return n

def eval_feature(d,expr, external=None):
 validate(expr); op=expr['op']
 if op=='raw': return raw(d,expr['name'])
 if op=='cross_return':
  if external is None: raise ValueError('external dataset missing')
  e=external[['time','close']].copy().sort_values('time'); e['x']=np.log(e.close/e.close.shift()).shift(int(expr['lag']))
  # exact timestamps only; shift prevents same-bar contemporary information.
  return pd.merge(d[['time']],e[['time','x']],on='time',how='left')['x']
 x=eval_feature(d,expr['x'],external)
 if op=='difference': return x-x.shift(int(expr['lag']))
 if op=='zscore':
  w=int(expr['window']); return (x-x.rolling(w).mean())/x.rolling(w).std().replace(0,np.nan)
 if op=='slope':
  w=int(expr['window']); ix=np.arange(w,dtype=float); den=((ix-ix.mean())**2).sum()
  return x.rolling(w).apply(lambda a:float(np.dot(a-a.mean(),ix-ix.mean())/den),raw=True)
 if op=='ratio': return x/eval_feature(d,expr['y'],external).replace(0,np.nan)
 if op=='vol_ratio':
  w=int(expr['window']); r=np.log(d.close/d.close.shift()); return x/r.rolling(w).std().replace(0,np.nan)
 raise AssertionError(op)

def complexity(e): return validate(e)
def feature_id(e): validate(e); return sid('feat',e)

def novelty(f,bank):
 best={'score':0.0,'nearest':None}
 for n,b in bank.items():
  z=pd.concat([f,b],axis=1).replace([np.inf,-np.inf],np.nan).dropna()
  if len(z)<80: continue
  p=abs(float(z.iloc[:,0].corr(z.iloc[:,1]))); s=abs(float(z.iloc[:,0].corr(z.iloc[:,1],method='spearman')))
  a=z.iloc[:,0]>=z.iloc[:,0].quantile(.75); q=z.iloc[:,1]>=z.iloc[:,1].quantile(.75); ov=float((a&q).sum()/max(1,(a|q).sum()))
  score=max(0 if not np.isfinite(p) else p,0 if not np.isfinite(s) else s,ov)
  if score>best['score']: best={'score':round(score,4),'nearest':n,'pearson':round(p,4) if np.isfinite(p) else 0,'spearman':round(s,4) if np.isfinite(s) else 0,'overlap':round(ov,4)}
 return {**best,'novel':best['score']<.92}

def bank(d,e,exclude_feature_id=None):
 # Existing representation only. Candidate itself is excluded: otherwise every
 # expression would tautologically be marked its own clone.
 c=d.close; r=np.log(c/c.shift()); out={'ret1':r,'ret12':np.log(c/c.shift(12)),'vol24':r.rolling(24).std(),'ema50':c/c.ewm(span=50,adjust=False).mean()-1,'volume_z':(d.volume-d.volume.rolling(48).mean())/d.volume.rolling(48).std()}
 for x in list(TEMPLATES.values())+list(ALTERNATES.values()):
  fid=feature_id(x)
  if fid!=exclude_feature_id: out[f'candidate::{fid}']=eval_feature(d,x,e)
 return out

# One fixed expression per branch, plus a different pre-registered alternate.
TEMPLATES={
 'trend':{'op':'slope','x':{'op':'raw','name':'ret1'},'window':24},
 'mean_reversion':{'op':'zscore','x':{'op':'raw','name':'ret1'},'window':24},
 'volume_liquidity':{'op':'zscore','x':{'op':'difference','x':{'op':'raw','name':'volume'},'lag':12},'window':48},
 'volatility':{'op':'vol_ratio','x':{'op':'raw','name':'ret1'},'window':48},
 'cross_asset':{'op':'cross_return','ticker':'CNY','lag':2},
 'regimes':{'op':'ratio','x':{'op':'zscore','x':{'op':'raw','name':'ret1'},'window':24},'y':{'op':'zscore','x':{'op':'raw','name':'volume'},'window':24}},
 'intraday_microstructure':{'op':'raw','name':'hour_sin'},}
ALTERNATES={
 'trend':{'op':'slope','x':{'op':'raw','name':'ret1'},'window':48},
 'mean_reversion':{'op':'zscore','x':{'op':'raw','name':'ret1'},'window':48},
 'volume_liquidity':{'op':'zscore','x':{'op':'difference','x':{'op':'raw','name':'volume'},'lag':24},'window':48},
 'volatility':{'op':'vol_ratio','x':{'op':'raw','name':'ret1'},'window':24},
 'cross_asset':{'op':'cross_return','ticker':'CNY','lag':6},
 'regimes':{'op':'ratio','x':{'op':'zscore','x':{'op':'raw','name':'ret1'},'window':48},'y':{'op':'zscore','x':{'op':'raw','name':'volume'},'window':48}},
 'intraday_microstructure':{'op':'zscore','x':{'op':'raw','name':'hour_sin'},'window':24}}

@dataclass(frozen=True)
class PlanItem:
 branch:str; feature_expr:dict; feature_id:str; hypothesis_id:str; family_key:str; round:int; estimated_cost:dict; falsification:dict

class BaseDirector:
 name='base'
 def plan(self,state,round,budget): raise NotImplementedError
class RandomBaselineDirector(BaseDirector):
 name='random-baseline-v1'
 def plan(self,state,round,budget):
  # intentionally blind: repeats primary expression on every round, as fixed-space baseline.
  return make_plan(TEMPLATES,round,self.name,budget)
class DeterministicIntelligentDirector(BaseDirector):
 name='deterministic-intelligent-v1'
 def plan(self,state,round,budget):
  neg=set(state.get('negative_family_keys',[])); pool={}
  for b in BRANCHES:
   primary=TEMPLATES[b]; alternate=ALTERNATES[b]
   # memory rule: exact/semantic family failed -> choose pre-registered alternate; no tuning after outcome.
   pool[b]=alternate if canon(primary) in neg else primary
  return make_plan(pool,round,self.name,budget)

def make_plan(pool,round,director,budget):
 out=[]
 for b in BRANCHES:
  e=pool[b]; fk=canon(e); h={'branch':b,'family_key':fk,'round':round,'director':director,'falsification':{'min_n':80,'min_abs_t':1.0,'half_stability':True}}
  out.append(PlanItem(b,e,feature_id(e),sid('hyp',h),fk,round,{'evaluations':1,'complexity':complexity(e),'cpu_units':complexity(e)},h['falsification']))
 return out[:int(budget['experiment_evaluations'])]

def pick_budgeted(ranked,budget):
 """Branch starvation guard shared by RUN-12/13 directors: at most one candidate
 per branch, best-first, up to budget."""
 picked=[];seen=set()
 for item in ranked:
  score,b,e,meta=(item[0],item[1],item[2],item[3]) if len(item)==4 else (None,item[0],item[1],item[2])
  if b not in seen: picked.append((b,e,meta));seen.add(b)
  if len(picked)>=int(budget):break
 return picked

def observe(d,e,expr):
 f=eval_feature(d,expr,e); nr=np.log(d.close.shift(-1)/d.close); z=pd.DataFrame({'f':f,'r':nr}).replace([np.inf,-np.inf],np.nan).dropna()
 qh=z.f.quantile(.75); ql=z.f.quantile(.25); hi=z[z.f>=qh].r; lo=z[z.f<=ql].r; eff=float(hi.mean()-lo.mean())
 se=math.sqrt(float(hi.var(ddof=1))/len(hi)+float(lo.var(ddof=1))/len(lo)) if len(hi)>1 and len(lo)>1 else math.inf; t=eff/se if se and math.isfinite(se) else 0.
 mid=len(z)//2; halves=[]
 for x in (z.iloc[:mid],z.iloc[mid:]):
  halves.append(float(x[x.f>=x.f.quantile(.75)].r.mean()-x[x.f<=x.f.quantile(.25)].r.mean()))
 stable=bool(eff and np.sign(halves[0])==np.sign(halves[1])==np.sign(eff))
 n=novelty(f,bank(d,e,exclude_feature_id=feature_id(expr))); oid=sid('obs',{'feature':feature_id(expr),'effect':round(eff,10),'n':len(z)})
 return {'observation_id':oid,'feature_id':feature_id(expr),'conditions':{'upper_vs_lower_quartile':True},'effect':{'mean_difference':round(eff,10),'tstat':round(float(t),5),'threshold_q75':round(float(qh),10),'halves':halves},'sample_size':len(z),'stability':stable,'possible_leakage':False,'novelty':n,'holdout_excluded_days':HOLDOUT_DAYS}

def economic_test(d,ticker,item,obs,e):
 # Research-level only. Existing backtest; never candidate/pool/promotion.
 if not obs['novelty']['novel']: return 'FAIL',['feature_clone'],{}
 if obs['sample_size']<80 or not obs['stability'] or abs(obs['effect']['tstat'])<1: 
  rs=[]
  if not obs['stability']:rs.append('observation_unstable')
  if abs(obs['effect']['tstat'])<1:rs.append('tstat_floor')
  return 'FAIL',rs,{}
 from futures_lab import STRATEGY_FUNCS,_synthetic_spec_for_file
 from engines.exit_engine import run_candidate
 f=eval_feature(d,item.feature_expr,e); sig=pd.Series(0,index=d.index,dtype=int); direction=1 if obs['effect']['mean_difference']>=0 else -1; sig[f>=obs['effect']['threshold_q75']]=direction
 name='ri11_'+item.hypothesis_id[-10:]; STRATEGY_FUNCS[name]=(lambda x:lambda df,**kw:pd.Series(x[:len(df)],index=df.index))(sig.values)
 try:
  _,tr,eq=run_candidate(d,_synthetic_spec_for_file(ticker),{'strategy':name,'ticker':ticker,'risk':{'stop_atr':2,'take_atr':3,'max_hold':48},'exits':{}},1,cap=COST['capital'],comm=COST['commission_per_contract'],slip_bps=COST['slippage_bps'])
 finally: STRATEGY_FUNCS.pop(name,None)
 pnl=float(eq.iloc[-1]-COST['capital']); dd=float((eq.cummax()-eq).max()); reasons=[]
 if len(tr)<8:reasons.append('too_few_trades')
 if pnl<=0:reasons.append('nonpositive_pnl')
 if dd>7000:reasons.append('drawdown_35pct')
 return ('PASS_RESEARCH_ONLY' if not reasons else 'FAIL'),reasons,{'pnl':round(pnl,2),'dd':round(dd,2),'trades':len(tr)}

def research_value(status,obs,item,reasons):
 # Frozen formula: evidence quality + novelty + uncertainty reduction + negative knowledge - cost - complexity.
 economic=min(abs(obs['effect']['tstat'])/3,1.0) if status=='PASS_RESEARCH_ONLY' else 0.
 nov=1.0 if obs['novelty']['novel'] else 0.
 uncertainty=0.8 if status=='FAIL' else 0.2
 negative=0.7 if status=='FAIL' else 0.
 cost=0.10; penalty=0.03*item.estimated_cost['complexity']
 return round(economic+nov+uncertainty+negative-cost-penalty,4)

def fresh_state(label):
 return {'schema_version':SCHEMA,'label':label,'negative_family_keys':[],'lineage':{},'branches':{b:{'budget':0,'experiments_used':0,'hypotheses_generated':0,'hypotheses_failed':0,'hypotheses_survived':0,'novelty_rejections':0,'compute_cost':0,'information_gain':0.} for b in BRANCHES},'events':[]}

def execute_round(state,d,e,ticker,dataset_hash,director,round,budget):
 plan=director.plan(state,round,budget); state['events'].append({'event':'plan_frozen','round':round,'director':director.name,'items':[asdict(x) for x in plan],'budget':budget})
 rows=[]
 for item in plan:
  obs=observe(d,e,item.feature_expr); status,reasons,metrics=economic_test(d,ticker,item,obs,e); exp=sid('exp',{'hypothesis':item.hypothesis_id,'dataset':dataset_hash,'round':round,'director':director.name})
  rv=research_value(status,obs,item,reasons)
  rec={'experiment_id':exp,'observation_id':obs['observation_id'],'hypothesis_id':item.hypothesis_id,'plan_round':round,'branch':item.branch,'feature_id':item.feature_id,'feature_expr':item.feature_expr,'family_key':item.family_key,'dataset':{'ticker':ticker,'hash':dataset_hash,'holdout_days':HOLDOUT_DAYS},'budget_spent':item.estimated_cost,'observation':obs,'hypothesis':{'expected_effect':'conditional next-bar return differs by feature quartile','falsification':item.falsification},'status':status,'verdict':status,'reject_reasons':reasons,'metrics':metrics,'research_value':rv,'candidate_registry_status':'NOT_ELIGIBLE_RESEARCH_ONLY','evidence_write_gate':'NOT_ATTEMPTED_LOCAL_FILE_RESEARCH','created_at':now()}
  state['lineage'][exp]=rec; rows.append(rec); b=state['branches'][item.branch]; b['budget']+=1;b['experiments_used']+=1;b['hypotheses_generated']+=1;b['compute_cost']+=item.estimated_cost['cpu_units'];b['information_gain']+=rv
  if status=='FAIL':
   b['hypotheses_failed']+=1
   if 'feature_clone' in reasons:b['novelty_rejections']+=1
   state['negative_family_keys'].append(item.family_key)
  else:b['hypotheses_survived']+=1
 return rows

def checkpointed_cycle(ticker, director, state_path, max_new=None, pause_s=0.0):
 """Persistent one-item-at-a-time cycle. Each terminal record is fsync+rename
 before next item. Safe to kill: resume reads the frozen plan/position, never
 repeats terminal experiment IDs. Used only for recovery proof / future scheduler."""
 from tools.state_io import atomic_write_json
 state_path=Path(state_path); p=DATA/f'{ticker}_365d_1h_continuous.csv'; base=pd.read_csv(p);base['time']=pd.to_datetime(base['time']);d,cut=train_only(base); ext,_=load('CNY');e,_=train_only(ext); dh=file_hash(p)
 s=json.loads(state_path.read_text()) if state_path.exists() else {'schema_version':SCHEMA,'ticker':ticker,'dataset_hash':dh,'hidden_cutoff':str(cut),'director':director.name,'negative_family_keys':[],'terminal':{},'position':0,'events':[]}
 if 'frozen_plan' not in s:
  s['frozen_plan']=[asdict(x) for x in director.plan({'negative_family_keys':s['negative_family_keys']},1,{'experiment_evaluations':7})];s['events'].append({'event':'plan_frozen','count':len(s['frozen_plan']),'ts':now()});atomic_write_json(state_path,s)
 done=0
 while s['position']<len(s['frozen_plan']) and (max_new is None or done<max_new):
  item=PlanItem(**s['frozen_plan'][s['position']]); exp=sid('exp',{'hypothesis':item.hypothesis_id,'dataset':dh,'checkpoint':True})
  if exp not in s['terminal']:
   obs=observe(d,e,item.feature_expr);status,reasons,metrics=economic_test(d,ticker,item,obs,e)
   s['terminal'][exp]={'experiment_id':exp,'observation_id':obs['observation_id'],'hypothesis_id':item.hypothesis_id,'branch':item.branch,'family_key':item.family_key,'status':status,'reject_reasons':reasons,'metrics':metrics,'ts':now()}
   if status=='FAIL': s['negative_family_keys'].append(item.family_key)
  s['position']+=1;s['events'].append({'event':'terminal_checkpoint','experiment_id':exp,'position':s['position'],'ts':now()});atomic_write_json(state_path,s);done+=1
  if pause_s: time.sleep(pause_s)
 return s


def run_tournament(ticker='IMOEX',rounds=2,budget=7,out=None):
 """Equal budget: each director gets branches×rounds economic evaluations/plans.
 Intelligent uses only prior persisted FAIL to select pre-registered alternate; baseline repeats.
 This tests memory/repetition, not trading alpha."""
 base,path=load(ticker); ext,_=load('CNY'); d,cut=train_only(base); e,_=train_only(ext); dh=file_hash(path)
 result={'schema_version':SCHEMA,'ticker':ticker,'dataset_hash':dh,'hidden_cutoff':str(cut),'budget_per_director':rounds*budget,'rounds':rounds,'directors':{}}
 for director in (RandomBaselineDirector(),DeterministicIntelligentDirector()):
  st=fresh_state(director.name); allrows=[]
  for rnd in range(1,rounds+1): allrows+=execute_round(st,d,e,ticker,dh,director,rnd,{'experiment_evaluations':budget})
  fam=[r['family_key'] for r in allrows]; repeat=len(fam)-len(set(fam)); survives=sum(r['status']=='PASS_RESEARCH_ONLY' for r in allrows); clones=sum('feature_clone' in r['reject_reasons'] for r in allrows)
  result['directors'][director.name]={'experiments':len(allrows),'unique_hypotheses':len({r['hypothesis_id'] for r in allrows}),'unique_families':len(set(fam)),'failed_family_repetitions':repeat,'novelty_rejections':clones,'falsified':sum(r['status']=='FAIL' for r in allrows),'survivors_research_only':survives,'information_gain':round(sum(r['research_value'] for r in allrows),4),'compute_cost':sum(r['budget_spent']['cpu_units'] for r in allrows),'branches':st['branches'],'lineage':st['lineage'],'negative_family_keys':st['negative_family_keys']}
 if out: Path(out).write_text(json.dumps(result,ensure_ascii=False,indent=2))
 return result

if __name__=='__main__':
 import argparse
 p=argparse.ArgumentParser();p.add_argument('--out',required=True);p.add_argument('--ticker',default='IMOEX');a=p.parse_args();print(json.dumps(run_tournament(a.ticker,out=a.out),ensure_ascii=False,indent=2))
