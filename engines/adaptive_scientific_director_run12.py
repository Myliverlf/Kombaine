#!/usr/bin/env python3
"""RUN-12 Adaptive Scientific Director — research only, paper only.

Uses RUN-11 bounded DSL/economic test. Director is proposal-only; it cannot write
pool/candidates or change validation. Final 60d is removed by run11.train_only().
"""
from __future__ import annotations
import json, math
from dataclasses import asdict
from pathlib import Path
import sys
SC=Path('/root/prop-desk/strategy_combine'); FL=Path('/root/prop-desk/futures_lab')
sys.path[:0]=[str(SC),str(FL),str(SC/'engines')]
import research_intelligence_run11 as r

SCHEMA='adaptive-scientific-run12-v1'
BRANCHES=r.BRANCHES
# Frozen before tournament: third distinct expression per branch. No result tuning.
THIRD={
 'trend':{'op':'slope','x':{'op':'raw','name':'ret1'},'window':72},
 'mean_reversion':{'op':'zscore','x':{'op':'raw','name':'ret1'},'window':72},
 'volume_liquidity':{'op':'zscore','x':{'op':'difference','x':{'op':'raw','name':'volume'},'lag':36},'window':48},
 'volatility':{'op':'vol_ratio','x':{'op':'raw','name':'ret1'},'window':72},
 'cross_asset':{'op':'cross_return','ticker':'CNY','lag':12},
 'regimes':{'op':'ratio','x':{'op':'zscore','x':{'op':'raw','name':'ret1'},'window':72},'y':{'op':'zscore','x':{'op':'raw','name':'volume'},'window':24}},
 'intraday_microstructure':{'op':'zscore','x':{'op':'raw','name':'hour_sin'},'window':48},}

# Preregistered scoring formula. All terms 0..1 except penalties. No PnL term.
SCORING={'uncertainty_reduction':0.30,'novelty':0.20,'branch_information_value':0.15,
         'regime_information_value':0.15,'hypothesis_independence':0.20,
         'negative_similarity_penalty':0.35,'complexity_penalty':0.03,'compute_cost_penalty':0.04}


def regimes(d):
    rr=(d.close/d.close.shift()).apply(math.log); v=rr.rolling(24).std(); med=v.median()
    return {'high_vol':(v>=med).fillna(False),'low_vol':(v<med).fillna(False)}

def regime_label(d, expr, external):
    # Context at decision time only, final classification is stored with result.
    x=r.eval_feature(d,expr,external); return 'high_vol' if float(x.abs().median(skipna=True) or 0)>=float(x.abs().quantile(.5) or 0) else 'normal'

def family_similarity(a,b):
    # Semantic proximity: same branch primitive / canonical closeness, not name only.
    if a==b:return 1.0
    try:
        aa=json.loads(a);bb=json.loads(b)
        if aa.get('op')==bb.get('op'): return .65
        if aa.get('op')=='cross_return' and bb.get('op')=='cross_return':return .8
    except Exception:pass
    return 0.0

def graph_node(kind,payload): return {'id':r.sid(kind,payload),'kind':kind,'payload':payload}
def add_edge(g,src,dst,relation):g['edges'].append({'source':src,'target':dst,'relation':relation})

def initial_state(label):
    return {'schema_version':SCHEMA,'label':label,'explored_families':[],'failed_contexts':[],
      'surviving_families':[],'observations':{},'hypotheses':{},'experiments':{},
      'branches':{b:{'budget':0,'experiments_used':0,'hypotheses_generated':0,'hypotheses_failed':0,
        'hypotheses_survived':0,'novelty_rejections':0,'compute_cost':0,'information_gain':0.0,
        'saturation':0.0} for b in BRANCHES},
      'graph':{'nodes':[],'edges':[]},'knowledge_snapshots':[],'events':[]}

def choices(branch):return (r.TEMPLATES[branch],r.ALTERNATES[branch],THIRD[branch])

def candidate_score(state,branch,expr,round_no):
    fk=r.canon(expr); b=state['branches'][branch]
    # uncertainty high if branch had little evidence; branch info rewards underexplored branches.
    uncertainty=1/(1+b['experiments_used'])
    branch_iv=1-b['saturation']
    regime_iv=1.0 if not any(x['branch']==branch for x in state['failed_contexts']) else .5
    neg=max([family_similarity(fk,x['family_key']) for x in state['failed_contexts']] or [0])
    independence=1-neg
    # expression novelty assessed statically against prior family keys; full signal novelty remains judge's job.
    novelty=independence
    comp=r.complexity(expr)/7; cost=comp
    score=(SCORING['uncertainty_reduction']*uncertainty+SCORING['novelty']*novelty+
      SCORING['branch_information_value']*branch_iv+SCORING['regime_information_value']*regime_iv+
      SCORING['hypothesis_independence']*independence-SCORING['negative_similarity_penalty']*neg-
      SCORING['complexity_penalty']*comp-SCORING['compute_cost_penalty']*cost)
    return round(score,6),{'uncertainty':round(uncertainty,4),'novelty':round(novelty,4),'branch_iv':round(branch_iv,4),'regime_iv':round(regime_iv,4),'independence':round(independence,4),'negative_similarity':neg,'complexity':comp,'cost':cost}

class ResearchDirector:
    name='abstract'
    def observe_state(self,state):return state
    def propose_research_plan(self,state,round,budget):raise NotImplementedError

class RandomBaselineDirector(ResearchDirector):
    name='random-baseline-run12'
    def propose_research_plan(self,state,round,budget):
        return [(b,r.TEMPLATES[b],{'policy':'blind_primary','score':0.0}) for b in BRANCHES][:budget]

class DeterministicDirector(ResearchDirector):
    name='deterministic-run12'
    def propose_research_plan(self,state,round,budget):
        failed={x['family_key'] for x in state['failed_contexts']}; out=[]
        for b in BRANCHES:
            e=r.ALTERNATES[b] if r.canon(r.TEMPLATES[b]) in failed else r.TEMPLATES[b]
            out.append((b,e,{'policy':'primary_then_alternate','score':0.0}))
        return out[:budget]

class AdaptiveScientificDirector(ResearchDirector):
    name='adaptive-scientific-v1'
    def propose_research_plan(self,state,round,budget):
        ranked=[]
        for b in BRANCHES:
            for idx,e in enumerate(choices(b)):
                fk=r.canon(e)
                if fk in state['explored_families']:continue
                score,parts=candidate_score(state,b,e,round)
                # A discriminating experiment is an untested alternate when a related family failed.
                discr=any(family_similarity(fk,x['family_key'])>=.65 for x in state['failed_contexts'])
                ranked.append((score,b,e,{'policy':'adaptive_preregistered','score':score,'score_parts':parts,'discriminating_experiment':discr,'choice_index':idx}))
        ranked.sort(key=lambda x:(-x[0],x[1],r.canon(x[2])))
        # branch starvation guard: exactly one best available candidate per branch per round.
        picked=[];seen=set()
        for _,b,e,m in ranked:
            if b not in seen: picked.append((b,e,m));seen.add(b)
            if len(picked)>=budget:break
        return picked

def competing_hypotheses(obs,branch,expr):
    # Same observation deliberately yields mechanisms with opposite/conditional predictions.
    base={'observation_id':obs['observation_id'],'branch':branch,'feature_expr':expr}
    variants=[('continuation','upper feature predicts continuation'),('reversal','upper feature predicts reversal'),('high_vol_only','effect occurs only during high volatility'),('seasonality_artifact','effect is explained by intraday seasonality')]
    return [{'hypothesis_id':r.sid('hyp',dict(base,mechanism=k)),'mechanism':k,'statement':v,
             'falsification':{'min_abs_t':1.0,'half_stability':True,'holdout_days':r.HOLDOUT_DAYS}} for k,v in variants]

def contextual_reason(status,reasons,branch,regime):
    if 'feature_clone' in reasons:return 'CLONE'
    if 'observation_unstable' in reasons:return 'UNSTABLE'
    if 'tstat_floor' in reasons:return 'INSUFFICIENT_EVIDENCE'
    if status=='FAIL':return 'CONDITIONAL_FAIL' if regime!='global' else 'GLOBAL_FAIL'
    return 'SURVIVED_RESEARCH_ONLY'

def execute(state,d,e,ticker,dh,director,round_no,budget):
    plan=director.propose_research_plan(state,round_no,budget)
    state['events'].append({'event':'plan_frozen','director':director.name,'round':round_no,'scoring':SCORING,'items':[{'branch':b,'expr':x,'meta':m} for b,x,m in plan]})
    rows=[]
    for branch,expr,meta in plan:
        obs=r.observe(d,e,expr); regime='high_vol' if branch in {'volatility','regimes'} else 'global'
        hyps=competing_hypotheses(obs,branch,expr); selected=hyps[2] if meta.get('discriminating_experiment') else hyps[0]
        item=r.PlanItem(branch,expr,r.feature_id(expr),selected['hypothesis_id'],r.canon(expr),round_no,{'evaluations':1,'complexity':r.complexity(expr),'cpu_units':r.complexity(expr)},selected['falsification'])
        status,reasons,metrics=r.economic_test(d,ticker,item,obs,e); exp=r.sid('exp',{'hyp':item.hypothesis_id,'director':director.name,'round':round_no,'dataset':dh})
        reason=contextual_reason(status,reasons,branch,regime)
        rv=r.research_value(status,obs,item,reasons)
        # Information gain: reject close family or discriminate competing explanations is valuable.
        info=rv+(0.5 if meta.get('discriminating_experiment') else 0)+(0.25 if reason in {'CLONE','UNSTABLE','INSUFFICIENT_EVIDENCE'} else 0)
        on=graph_node('Observation',obs); fn=graph_node('FeatureFamily',{'family_key':item.family_key}); hn=graph_node('Hypothesis',selected); xn=graph_node('Experiment',{'experiment_id':exp}); rn=graph_node('Result',{'status':status,'reason':reason})
        state['graph']['nodes'] += [on,fn,hn,xn,rn]
        add_edge(state['graph'],on['id'],hn['id'],'SUPPORTS');add_edge(state['graph'],hn['id'],fn['id'],'DERIVED_FROM');add_edge(state['graph'],hn['id'],xn['id'],'TESTED_BY');add_edge(state['graph'],xn['id'],rn['id'],'SURVIVED_UNDER' if status!='FAIL' else 'FAILED_UNDER')
        for old in state['failed_contexts']:
            if family_similarity(item.family_key,old['family_key'])>=.65:add_edge(state['graph'],fn['id'],old['feature_node'],'SIMILAR_TO')
        rec={'experiment_id':exp,'observation_id':obs['observation_id'],'hypothesis_id':selected['hypothesis_id'],'competing_hypotheses':hyps,'selected_mechanism':selected['mechanism'],'branch':branch,'feature_expr':expr,'family_key':item.family_key,'regime_context':regime,'director_meta':meta,'status':status,'reject_reasons':reasons,'negative_knowledge_kind':reason,'metrics':metrics,'research_value':rv,'information_gain':round(info,4),'hidden_cutoff':str(d.time.max()),'budget_spent':item.estimated_cost}
        state['observations'][obs['observation_id']]=obs;state['hypotheses'][selected['hypothesis_id']]=selected;state['experiments'][exp]=rec;state['explored_families'].append(item.family_key);rows.append(rec)
        b=state['branches'][branch];b['budget']+=1;b['experiments_used']+=1;b['hypotheses_generated']+=len(hyps);b['compute_cost']+=item.estimated_cost['cpu_units'];b['information_gain']+=info;b['saturation']=round(min(1,b['experiments_used']/3),3)
        if status=='FAIL':
            b['hypotheses_failed']+=1
            if reason=='CLONE':b['novelty_rejections']+=1
            state['failed_contexts'].append({'family_key':item.family_key,'branch':branch,'regime':regime,'reason':reason,'feature_node':fn['id'],'experiment_id':exp})
        else: b['hypotheses_survived']+=1;state['surviving_families'].append(item.family_key)
    return rows

def knowledge_snapshot(state):
    snap={'snapshot_id':r.sid('snapshot',{'n':len(state['experiments']),'label':state['label']}),'known_negative':[],'conditional':[],'promising':[],'open_questions':[],'unknown_branches':[]}
    for x in state['failed_contexts']:
        dest='conditional' if x['reason']=='CONDITIONAL_FAIL' else 'known_negative';snap[dest].append(x)
    for x in state['experiments'].values():
        if x['status']!='FAIL':snap['promising'].append({'experiment_id':x['experiment_id'],'note':'research-only survivor; needs independent validation'})
    for b,v in state['branches'].items():
        if v['experiments_used']==0:snap['unknown_branches'].append(b)
        elif v['hypotheses_failed'] and v['hypotheses_survived']==0:snap['open_questions'].append({'branch':b,'question':'test different regime or information source, not same failed family'})
    state['knowledge_snapshots'].append(snap);return snap

def tournament(ticker='IMOEX',rounds=3,budget=7,out=None):
    base,p=r.load(ticker); ext,_=r.load('CNY');d,cut=r.train_only(base);e,_=r.train_only(ext);dh=r.file_hash(p)
    result={'schema_version':SCHEMA,'ticker':ticker,'dataset_hash':dh,'hidden_cutoff':str(cut),'budget_per_director':rounds*budget,'rounds':rounds,'scoring_preregistered':SCORING,'directors':{}}
    for director in (RandomBaselineDirector(),DeterministicDirector(),AdaptiveScientificDirector()):
        s=initial_state(director.name);rows=[]
        for rnd in range(1,rounds+1):rows+=execute(s,d,e,ticker,dh,director,rnd,budget)
        k=knowledge_snapshot(s);fam=[x['family_key'] for x in rows];sem=sum(1 for x in rows if any(y['family_key']!=x['family_key'] and family_similarity(x['family_key'],y['family_key'])>=.65 for y in rows))
        result['directors'][director.name]={'experiments':len(rows),'unique_families':len(set(fam)),'repeated_families':len(fam)-len(set(fam)),'semantic_repetitions':sem,'survivors_research_only':sum(x['status']!='FAIL' for x in rows),'falsified':sum(x['status']=='FAIL' for x in rows),'discriminating_experiments':sum(bool(x['director_meta'].get('discriminating_experiment')) for x in rows),'information_gain':round(sum(x['information_gain'] for x in rows),4),'compute_cost':sum(x['budget_spent']['cpu_units'] for x in rows),'information_gain_per_cpu':round(sum(x['information_gain'] for x in rows)/max(1,sum(x['budget_spent']['cpu_units'] for x in rows)),4),'branches':s['branches'],'knowledge_snapshot':k,'state':s,'lineage':rows}
    if out:Path(out).write_text(json.dumps(result,ensure_ascii=False,indent=2))
    return result
if __name__=='__main__':
 import argparse;p=argparse.ArgumentParser();p.add_argument('--out',required=True);a=p.parse_args();print(json.dumps(tournament(out=a.out),ensure_ascii=False,indent=2))
