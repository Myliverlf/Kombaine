#!/usr/bin/env python3
"""Research Intelligence MVP — discovery слой НАД существующим Combine.

Граница ролей:
  ResearchDirector (предлагает) -> ExperimentPlanner -> run_candidate (судит)
Никаких broker imports, изменения mode, gate thresholds или holdout.

MVP доказывает 7 вещей из master task:
- causal DSL создаёт derived feature;
- feature novelty отделяет клон от новой информации;
- observation -> falsifiable hypothesis;
- deterministic registered experiment -> existing run_candidate;
- FAIL становится NegativeKnowledge;
- следующий план не повторяет failed family;
- состояние достаточно для другого Director implementation.

Persistent truth: state/research_intelligence/research_state.json.
Existing core ExperimentMemory/KnowledgeStore остаются canonical evidence layer;
этот модуль создаёт research-native structured artifacts, которые adapter может
передавать в core run bundles. Он не заменяет validation pipeline.
"""
from __future__ import annotations
import abc
import hashlib
import json
import math
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

SC = Path('/root/prop-desk/strategy_combine')
FL = Path('/root/prop-desk/futures_lab')
STATE_DIR = SC / 'state' / 'research_intelligence'
DATA_DIR = FL / 'artifacts' / 'tinkoff_futures_data'
SCHEMA_VERSION = 'research-intelligence-v1'
HOLDOUT_DAYS = 60

# Explicit safe DSL. No eval(), no Python callbacks in persisted expression.
RAW = frozenset({'close', 'open', 'high', 'low', 'volume', 'return_1', 'range_pct'})
OPS = frozenset({'rolling_mean', 'rolling_std', 'difference', 'ratio', 'zscore',
                 'percentile', 'slope', 'acceleration', 'rank', 'vol_adjust'})
MAX_DEPTH, MAX_NODES, MAX_WINDOW = 3, 9, 240


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def stable_id(prefix: str, payload: Any) -> str:
    return prefix + '_' + hashlib.sha256(canonical(payload).encode()).hexdigest()[:16]


def utc_now() -> str:
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def _num(v: Any, lo: int = 2, hi: int = MAX_WINDOW) -> int:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ValueError('DSL numeric parameter required')
    x = int(v)
    if not lo <= x <= hi:
        raise ValueError(f'DSL window outside [{lo},{hi}]: {x}')
    return x


def validate_expr(expr: dict, depth: int = 0) -> int:
    """Validate finite causal expression. Returns number of nodes."""
    if not isinstance(expr, dict):
        raise ValueError('DSL expression must be object')
    if depth > MAX_DEPTH:
        raise ValueError(f'DSL depth>{MAX_DEPTH}')
    kind = expr.get('op')
    if kind == 'raw':
        if expr.get('name') not in RAW:
            raise ValueError(f'unknown raw variable: {expr.get("name")}')
        return 1
    if kind not in OPS:
        raise ValueError(f'unknown/unsafe DSL op: {kind}')
    child = expr.get('x')
    nodes = validate_expr(child, depth + 1)
    if kind == 'ratio':
        nodes += validate_expr(expr.get('y'), depth + 1)
    if kind in {'rolling_mean', 'rolling_std', 'zscore', 'percentile', 'slope', 'rank', 'vol_adjust'}:
        _num(expr.get('window'))
    if kind == 'difference':
        _num(expr.get('lag'), 1, MAX_WINDOW)
    if kind == 'acceleration':
        _num(expr.get('lag'), 1, MAX_WINDOW // 2)
    if nodes > MAX_NODES:
        raise ValueError(f'DSL nodes>{MAX_NODES}')
    return nodes


def expression_id(expr: dict) -> str:
    validate_expr(expr)
    return stable_id('feat', expr)


def expression_complexity(expr: dict) -> int:
    return validate_expr(expr)


def _raw(df: pd.DataFrame, name: str) -> pd.Series:
    c = df['close'].astype(float)
    if name in df.columns:
        return df[name].astype(float)
    if name == 'return_1':
        return np.log(c / c.shift(1))
    if name == 'range_pct':
        return (df['high'].astype(float) - df['low'].astype(float)) / c.replace(0, np.nan)
    raise ValueError(name)


def evaluate_feature(df: pd.DataFrame, expr: dict) -> pd.Series:
    """Causal vector evaluator. Every transform only accesses t and earlier bars."""
    validate_expr(expr)
    op = expr['op']
    if op == 'raw':
        return _raw(df, expr['name']).replace([np.inf, -np.inf], np.nan)
    x = evaluate_feature(df, expr['x'])
    if op == 'rolling_mean': return x.rolling(_num(expr['window'])).mean()
    if op == 'rolling_std': return x.rolling(_num(expr['window'])).std()
    if op == 'difference': return x - x.shift(_num(expr['lag'], 1))
    if op == 'acceleration':
        k = _num(expr['lag'], 1, MAX_WINDOW // 2)
        return x - 2*x.shift(k) + x.shift(2*k)
    if op == 'ratio':
        y = evaluate_feature(df, expr['y'])
        return x / y.replace(0, np.nan)
    if op == 'zscore':
        w = _num(expr['window']); m=x.rolling(w).mean(); sd=x.rolling(w).std()
        return (x-m)/sd.replace(0,np.nan)
    if op == 'percentile':
        w = _num(expr['window'])
        return x.rolling(w).rank(pct=True)
    if op == 'rank':
        return x.rolling(_num(expr['window'])).rank(pct=True)
    if op == 'slope':
        w = _num(expr['window'])
        # analytical OLS slope over trailing window; all trailing, no future.
        ix=np.arange(w,dtype=float); den=((ix-ix.mean())**2).sum()
        return x.rolling(w).apply(lambda a: float(np.dot(a-a.mean(), ix-ix.mean())/den), raw=True)
    if op == 'vol_adjust':
        w=_num(expr['window']); vol=np.log(df['close']/df['close'].shift(1)).rolling(w).std()
        return x/vol.replace(0,np.nan)
    raise AssertionError(op)


def signal_from_feature(s: pd.Series, direction: str, threshold: float) -> pd.Series:
    """Single feature test signal. threshold comes from preregistered hypothesis."""
    out=pd.Series(0,index=s.index,dtype=int)
    if direction == 'long_high': out[s >= threshold]=1
    elif direction == 'long_low': out[s <= threshold]=1
    elif direction == 'short_high': out[s >= threshold]=-1
    elif direction == 'short_low': out[s <= threshold]=-1
    else: raise ValueError(f'unknown direction: {direction}')
    return out


def _mutual_information_hist(a: pd.Series, b: pd.Series, bins: int=8) -> float:
    """Dependency-free binned MI, normalized 0..1; diagnostic, not significance proof."""
    z=pd.concat([a,b],axis=1).replace([np.inf,-np.inf],np.nan).dropna()
    if len(z)<50 or z.iloc[:,0].nunique()<2 or z.iloc[:,1].nunique()<2: return 0.0
    h,_,_=np.histogram2d(z.iloc[:,0],z.iloc[:,1],bins=bins)
    p=h/h.sum(); px=p.sum(1,keepdims=True); py=p.sum(0,keepdims=True)
    nz=p>0; mi=float((p[nz]*np.log(p[nz]/(px@py)[nz])).sum())
    hx=float(-(px[px>0]*np.log(px[px>0])).sum()); hy=float(-(py[py>0]*np.log(py[py>0])).sum())
    return round(mi/max(1e-12,min(hx,hy)),4)


def feature_novelty(candidate: pd.Series, existing: dict[str,pd.Series], threshold: float=.92) -> dict:
    """Novelty = not a near duplicate by Pearson, rank correlation, MI, signal overlap."""
    best={'novel':True,'nearest_feature':None,'pearson':0.0,'spearman':0.0,'mi':0.0,'signal_overlap':0.0}
    x=candidate.replace([np.inf,-np.inf],np.nan)
    for name,y in existing.items():
        z=pd.concat([x,y.replace([np.inf,-np.inf],np.nan)],axis=1).dropna()
        if len(z)<50: continue
        p=float(z.iloc[:,0].corr(z.iloc[:,1])); sp=float(z.iloc[:,0].corr(z.iloc[:,1],method='spearman'))
        # compare binary upper-quartile event — robust to units/sign scale
        sx=z.iloc[:,0]>=z.iloc[:,0].quantile(.75); sy=z.iloc[:,1]>=z.iloc[:,1].quantile(.75)
        ov=float((sx & sy).sum()/max(1,(sx | sy).sum()))
        mi=_mutual_information_hist(z.iloc[:,0],z.iloc[:,1])
        score=max(abs(p) if np.isfinite(p) else 0, abs(sp) if np.isfinite(sp) else 0, mi, ov)
        prev=max(abs(best['pearson']),abs(best['spearman']),best['mi'],best['signal_overlap'])
        if score>prev:
            best.update({'nearest_feature':name,'pearson':round(p,4) if np.isfinite(p) else 0.0,
                         'spearman':round(sp,4) if np.isfinite(sp) else 0.0,'mi':mi,'signal_overlap':round(ov,4)})
    best['novel']=max(abs(best['pearson']),abs(best['spearman']),best['mi'],best['signal_overlap']) < threshold
    return best


def base_feature_bank(df: pd.DataFrame) -> dict[str,pd.Series]:
    """Банк ВСЕХ уже доступных E4 features + базовые raw transforms для novelty.

    Не считаем derived feature «новым» только потому, что у него другое имя.
    Если E4 feature generator недоступен, fallback остаётся детерминированным, а
    caller обязан записать это как coverage limitation (не как PASS novelty).
    """
    c=df['close']; r=np.log(c/c.shift(1))
    bank={'return_1':r,'ret12':np.log(c/c.shift(12)),
          'rsi_proxy':r.rolling(14).mean()/r.rolling(14).std(),
          'vol24':r.rolling(24).std(),'ema_dist':c/c.ewm(span=50,adjust=False).mean()-1,
          'range_pct':(df['high']-df['low'])/c,
          'volume_z':(df['volume']-df['volume'].rolling(48).mean())/df['volume'].rolling(48).std()}
    try:
        from engines.neural_engine import features as e4_features
        x=e4_features(df, 3)
        bank.update({f'e4::{col}': x[col] for col in x.columns})
    except Exception:
        # No false claim: fallback coverage is encoded below by the caller.
        pass
    return bank


def novelty_coverage(df: pd.DataFrame) -> dict:
    """Audit whether full existing E4 representation was available for comparison."""
    bank=base_feature_bank(df)
    return {'bank_size':len(bank), 'e4_feature_bank_available':any(k.startswith('e4::') for k in bank)}


@dataclass(frozen=True)
class MarketObservation:
    observation_id: str; feature_id: str; description: str; instruments: list[str]
    conditions: dict; effect: dict; sample_size: int; stability: float; possible_leakage: bool; novelty: dict

@dataclass(frozen=True)
class Hypothesis:
    hypothesis_id: str; origin: str; reasoning: str; required_data: list[str]; expected_effect: dict
    falsification: dict; feature_expr: dict; direction: str; threshold: float; novelty: dict; estimated_cost: dict

@dataclass(frozen=True)
class ResearchPlan:
    plan_id: str; director: str; hypotheses: list[dict]; budget: dict; created_at: str


class ResearchDirector(abc.ABC):
    """Provider-independent protocol. Director proposes; cannot judge/promote/trade."""
    @abc.abstractmethod
    def observe_state(self, state: dict) -> dict: ...
    @abc.abstractmethod
    def propose_research_plan(self, observed: dict, budget: dict) -> ResearchPlan: ...
    @abc.abstractmethod
    def evaluate_results(self, state: dict, results: list[dict]) -> dict: ...


class DeterministicDirector(ResearchDirector):
    """Failover Director: works without AI and chooses only fixed safe DSL templates."""
    name='deterministic-v1'
    # These are seed constructions, not blessed alpha. Complexity <= 3.
    templates=(
      {'op':'zscore','x':{'op':'ratio','x':{'op':'raw','name':'range_pct'},'y':{'op':'raw','name':'return_1'}},'window':48},
      {'op':'zscore','x':{'op':'difference','x':{'op':'raw','name':'volume'},'lag':12},'window':48},
      {'op':'vol_adjust','x':{'op':'difference','x':{'op':'raw','name':'close'},'lag':12},'window':48},
    )
    def observe_state(self,state:dict)->dict:
        return {'negative_families':set(x['family_key'] for x in state.get('negative_knowledge',[])),
                'known_features':set(state.get('features',{})), 'branches':state.get('branches',{})}
    def propose_research_plan(self,observed:dict,budget:dict)->ResearchPlan:
        hs=[]
        for expr in self.templates:
            fid=expression_id(expr); family_key=canonical(expr)
            if family_key in observed['negative_families']: continue
            # preregister hypothesis before examining market result
            h={'feature_expr':expr,'feature_id':fid,'family_key':family_key,'origin':'FeatureDiscoveryEngine',
               'reasoning':'Test whether a bounded causal transformation contains conditional next-bar information.',
               'expected_effect':{'metric':'conditional_next_return_mean','direction':'nonzero'},
               'falsification':{'min_sample_size':80,'min_abs_tstat':1.0,'require_half_sign_stability':True},
               'direction':'long_high','threshold':1.0,'estimated_cost':{'experiments':1,'trials':1,'cpu':'low'}}
            h['hypothesis_id']=stable_id('hyp',h); hs.append(h)
            if len(hs)>=int(budget.get('experiment_budget',1)): break
        payload={'director':self.name,'hypotheses':[x['hypothesis_id'] for x in hs],'budget':budget}
        return ResearchPlan(stable_id('plan',payload),self.name,hs,budget,utc_now())
    def evaluate_results(self,state:dict,results:list[dict])->dict:
        return {'director':self.name,'result_count':len(results),'next_action':'persist_and_avoid_failed_families'}


class ResearchStore:
    """Append-only semantic state. Does NOT mutate core validation truth."""
    def __init__(self, root:Path=STATE_DIR): self.root=Path(root); self.path=self.root/'research_state.json'
    def load(self)->dict:
        if not self.path.exists():
            return {'schema_version':SCHEMA_VERSION,'features':{},'observations':{},'hypotheses':{},'experiments':{},'negative_knowledge':[], 'plans':[], 'branches':{'derived_features':'OPEN'},'events':[]}
        return json.loads(self.path.read_text())
    def save(self,state:dict)->None:
        from tools.state_io import atomic_write_json
        state['schema_version']=SCHEMA_VERSION; state['updated_at']=utc_now(); atomic_write_json(self.path,state)
    def event(self,state:dict,kind:str,payload:dict)->None:
        state['events'].append({'ts':utc_now(),'kind':kind,'payload':payload})


def discover_observation(df:pd.DataFrame, expr:dict, ticker:str)->MarketObservation:
    f=evaluate_feature(df,expr); next_r=np.log(df['close'].shift(-1)/df['close'])
    # final 60d excluded from discovery regardless of caller
    ts=pd.to_datetime(df['time']); cut=ts.max()-pd.Timedelta(days=HOLDOUT_DAYS)
    z=pd.DataFrame({'f':f,'r':next_r,'t':ts}); z=z[z.t<cut].dropna()
    q=z.f.quantile(.75); hi=z[z.f>=q]; lo=z[z.f<=z.f.quantile(.25)]
    effect=float(hi.r.mean()-lo.r.mean()) if len(hi) and len(lo) else 0.0
    # Welch-like independent-sample t statistic: diagnostic/falsifier only.
    # It does NOT establish alpha and does not replace DSR/PBO later.
    se=math.sqrt(float(hi.r.var(ddof=1))/max(1,len(hi)) + float(lo.r.var(ddof=1))/max(1,len(lo))) if len(hi)>1 and len(lo)>1 else math.inf
    tstat=float(effect/se) if se>0 and math.isfinite(se) else 0.0
    # stability: same sign across time halves (no strategy created yet)
    h=len(z)//2; q1=z.iloc[:h].f.quantile(.75); q1lo=z.iloc[:h].f.quantile(.25); q2=z.iloc[h:].f.quantile(.75); q2lo=z.iloc[h:].f.quantile(.25)
    a=float(z.iloc[:h][z.iloc[:h].f>=q1].r.mean()-z.iloc[:h][z.iloc[:h].f<=q1lo].r.mean()) if h>20 else 0.0
    b=float(z.iloc[h:][z.iloc[h:].f>=q2].r.mean()-z.iloc[h:][z.iloc[h:].f<=q2lo].r.mean()) if len(z)-h>20 else 0.0
    stability=1.0 if effect and np.sign(a)==np.sign(b)==np.sign(effect) else 0.0
    bank=base_feature_bank(df)
    nov=feature_novelty(f,bank)
    nov['comparison_coverage']=novelty_coverage(df)
    payload={'feature_id':expression_id(expr),'ticker':ticker,'effect':round(effect,8),'n':len(z)}
    return MarketObservation(stable_id('obs',payload),expression_id(expr),
      'Conditional next-bar return difference: upper vs lower feature quartile.',[ticker],
      {'upper_quartile':True,'holdout_excluded_days':HOLDOUT_DAYS}, {'mean_difference':round(effect,8),'tstat':round(tstat,4),'threshold_q75':round(float(q),10),'half1':round(a,8),'half2':round(b,8)},len(z),stability,False,nov)


def hypothesis_from_observation(obs:MarketObservation, expr:dict)->Hypothesis:
    direction='long_high' if obs.effect['mean_difference']>=0 else 'short_high'
    payload={'obs':obs.observation_id,'expr':expr,'direction':direction}
    return Hypothesis(stable_id('hyp',payload),'MarketObservation',
      'Feature upper quartile has a conditional next-bar distribution different from lower quartile.',
      ['OHLCV 1h completed bars'],{'metric':'conditional_next_return_mean','sign':int(np.sign(obs.effect['mean_difference']))},
      {'min_sample_size':80,'min_abs_tstat':1.0,'require_half_sign_stability':True},expr,direction,1.0,obs.novelty,
      {'experiments':1,'trials':1,'cpu':'low'})


def run_registered_experiment(df:pd.DataFrame,ticker:str,hyp:Hypothesis, threshold:float)->dict:
    """Only execution entry: feature signal -> existing run_candidate. Holdout excluded.
    threshold is frozen by MarketObservation BEFORE strategy construction.
    This is RESEARCH evidence, never a pool promotion. It registers no broker adapter."""
    from futures_lab import STRATEGY_FUNCS, _synthetic_spec_for_file
    from engines.exit_engine import run_candidate
    ts=pd.to_datetime(df['time']); cut=ts.max()-pd.Timedelta(days=HOLDOUT_DAYS)
    train=df[ts<cut].reset_index(drop=True)
    f=evaluate_feature(train,hyp.feature_expr)
    threshold=float(threshold)
    sig=signal_from_feature(f,hyp.direction,threshold)
    name='ri_'+hyp.hypothesis_id.split('_')[-1][:10]
    STRATEGY_FUNCS[name]=(lambda s: lambda d,**kw: pd.Series(s[:len(d)],index=d.index))(sig.values)
    cand={'strategy':name,'ticker':ticker,'params':{},'risk':{'stop_atr':2.0,'take_atr':3.0,'max_hold':48},'exits':{}}
    try:
        _,trades,eq=run_candidate(train,_synthetic_spec_for_file(ticker),cand,1,cap=20000,comm=5,slip_bps=1)
    finally:
        STRATEGY_FUNCS.pop(name,None)
    pnl=float(eq.iloc[-1]-20000) if eq is not None and len(eq) else 0.0
    dd=float((eq.cummax()-eq).max()) if eq is not None and len(eq) else math.inf
    # Falsification precommitted: neither enough trades nor positive robust basic economics -> FAIL.
    verdict='PASS_RESEARCH_ONLY' if len(trades)>=8 and pnl>0 and dd<=7000 else 'FAIL'
    reasons=[]
    if len(trades)<8: reasons.append('too_few_trades')
    if pnl<=0: reasons.append('nonpositive_pnl')
    if dd>7000: reasons.append('drawdown_over_35pct_capital')
    return {'experiment_id':stable_id('exp',{'hypothesis':hyp.hypothesis_id,'ticker':ticker,'cut':str(cut),'threshold':round(threshold,10)}),
            'hypothesis_id':hyp.hypothesis_id,'ticker':ticker,'dataset_cutoff':str(cut),'strategy_name':name,
            'status':verdict,'reject_reasons':reasons,'metrics':{'pnl':round(pnl,2),'dd':round(dd,2),'trades':len(trades),'threshold':round(threshold,10)},
            'validation_boundary':'research pre-holdout only; cannot promote; must enter existing candidate pipeline separately',
            'executed_at':utc_now()}


def run_mvp_cycle(ticker:str='IMOEX', budget:dict|None=None, store:ResearchStore|None=None, director:ResearchDirector|None=None)->dict:
    """One autonomous deterministic research cycle. No state outside ResearchStore."""
    store=store or ResearchStore(); director=director or DeterministicDirector(); budget=budget or {'experiment_budget':1,'trials_budget':1,'cpu':'low','wall_clock_s':120}
    p=DATA_DIR/f'{ticker}_365d_1h_continuous.csv'
    if not p.exists(): raise FileNotFoundError(p)
    df=pd.read_csv(p)
    state=store.load(); observed=director.observe_state(state); plan=director.propose_research_plan(observed,budget)
    state['plans'].append(asdict(plan)); results=[]
    for h0 in plan.hypotheses:
        expr=h0['feature_expr']; fid=expression_id(expr)
        # prevent repeat after a stored NegativeKnowledge family
        if any(x['family_key']==h0['family_key'] for x in state['negative_knowledge']):
            results.append({'hypothesis_id':h0['hypothesis_id'],'status':'SKIP_NEGATIVE_KNOWLEDGE'}); continue
        obs=discover_observation(df,expr,ticker); hyp=hypothesis_from_observation(obs,expr)
        # Preserve plan id identity and preregistered fields; observation adds evidence only.
        state['features'][fid]={'feature_id':fid,'expression':expr,'complexity':expression_complexity(expr),'novelty':obs.novelty,'created_at':utc_now()}
        state['observations'][obs.observation_id]=asdict(obs); state['hypotheses'][hyp.hypothesis_id]=asdict(hyp)
        # Novelty clone is a research failure without spending an economic test.
        if not obs.novelty['novel']:
            result={'experiment_id':stable_id('exp',{'hypothesis':hyp.hypothesis_id,'reason':'feature_clone'}),'hypothesis_id':hyp.hypothesis_id,'ticker':ticker,'status':'FAIL','reject_reasons':['feature_clone'],'metrics':{},'executed_at':utc_now()}
        elif (obs.sample_size < hyp.falsification['min_sample_size'] or obs.stability < 1 or
              abs(float(obs.effect.get('tstat', 0.0))) < hyp.falsification['min_abs_tstat']):
            reasons=[]
            if obs.sample_size < hyp.falsification['min_sample_size']: reasons.append('observation_too_small')
            if obs.stability < 1: reasons.append('observation_unstable')
            if abs(float(obs.effect.get('tstat', 0.0))) < hyp.falsification['min_abs_tstat']: reasons.append('observation_tstat_below_preregistered_floor')
            result={'experiment_id':stable_id('exp',{'hypothesis':hyp.hypothesis_id,'reason':reasons}),'hypothesis_id':hyp.hypothesis_id,'ticker':ticker,'status':'FAIL','reject_reasons':reasons,'metrics':{'sample_size':obs.sample_size,'stability':obs.stability,'tstat':obs.effect.get('tstat')},'executed_at':utc_now()}
        else:
            result=run_registered_experiment(df,ticker,hyp,float(obs.effect['threshold_q75']))
        state['experiments'][result['experiment_id']]=result; results.append(result)
        if result['status']=='FAIL':
            state['negative_knowledge'].append({'negative_id':stable_id('neg',{'family':h0['family_key'],'reason':result['reject_reasons']}),'family_key':h0['family_key'],'feature_id':fid,'scope':{'ticker':ticker,'timeframe':'1h'},'reason_codes':result['reject_reasons'],'evidence_experiment_ids':[result['experiment_id']],'created_at':utc_now(),'revisit_only_if':['new data','new regime','new feature','new hypothesis','new methodology']})
    state['director_last_evaluation']=director.evaluate_results(state,results); store.event(state,'cycle_completed',{'plan_id':plan.plan_id,'results':[r['experiment_id'] for r in results]}); store.save(state)
    return {'plan':asdict(plan),'results':results,'state_path':str(store.path)}


if __name__=='__main__':
    import argparse
    ap=argparse.ArgumentParser(); ap.add_argument('--ticker',default='IMOEX'); ap.add_argument('--budget',type=int,default=1); ap.add_argument('--state-dir',default=None)
    a=ap.parse_args(); st=ResearchStore(Path(a.state_dir)) if a.state_dir else None
    print(json.dumps(run_mvp_cycle(a.ticker,{'experiment_budget':a.budget,'trials_budget':a.budget,'cpu':'low','wall_clock_s':120},st),ensure_ascii=False,indent=2))
