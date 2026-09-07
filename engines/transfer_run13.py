#!/usr/bin/env python3
"""RUN-13: Transfer, regime-causal knowledge, saturation. Research only.

Priors are contextual, never absolute except mathematical/structural facts
(CLONE / LEAKAGE / DATA_INTEGRITY_FAIL). Budget is equal by construction:
both directors may spend the full allocation; memory shows value by choosing
better, and savings are reported separately as experiments_saved.
"""
from __future__ import annotations
import json, math
import numpy as np
from pathlib import Path
import sys
SC=Path('/root/prop-desk/strategy_combine'); FL=Path('/root/prop-desk/futures_lab')
sys.path[:0]=[str(SC),str(FL),str(SC/'engines')]
import research_intelligence_run11 as r
import adaptive_scientific_director_run12 as a
import research_intelligence_run11 as _r11
a.pick_budgeted=_r11.pick_budgeted

SCHEMA='transfer-run13-v1'
UNIVERSE=('IMOEX','SBER','LKOH','CNY')
SCORING=a.SCORING  # frozen, no re-tuning
HARD={'CLONE','LEAKAGE','DATA_INTEGRITY_FAIL'}
SOFT={'UNSTABLE','INSUFFICIENT_EVIDENCE','GLOBAL_FAIL','CONDITIONAL_FAIL'}

def np_log_ret(d):return (d.close/d.close.shift()).apply(math.log)

def causal_regimes(d):
    """Causal regime labels: expanding-window volatility quantile; no future bars."""
    rr=np_log_ret(d); v=rr.rolling(24).std()
    hi=[];lo=[];seen=[]
    for x in v:
        if not (x==x): hi.append(False);lo.append(False);seen.append(x);continue
        if len(seen)>=120:
            arr=np.array([s for s in seen if s==s]); q33,q66=np.quantile(arr,.33),np.quantile(arr,.66)
            hi.append(bool(x>=q66));lo.append(bool(x<=q33))
        else: hi.append(False);lo.append(False)
        seen.append(x)
    ema=d.close.ewm(span=24,adjust=False).mean(); sl=ema-ema.shift(12)
    trend=[];rng=[]
    for i in range(len(d)):
        w=sl.iloc[max(0,i-72):i+1].dropna()
        if len(w)>=24:
            frac=float((w>0).mean()); trend.append(frac>=.6); rng.append(.4<frac<.6)
        else: trend.append(False);rng.append(False)
    return {'high_vol':hi,'low_vol':lo,'trend':trend,'range':rng}

def context_regime(d,regimes,idx):
    out=[k for k in ('high_vol','low_vol','trend','range') if regimes[k][idx]]
    return '+'.join(out) if out else 'normal'

def prior_for(state,fk,target_asset,target_regime):
    """(block, confidence 0..1, scope, evidence). CLONE-like facts are GLOBAL hard
    blocks; statistical FAILs are scope-weighted soft priors with contradiction decay."""
    for x in state['failed_contexts']:
        if x['family_key']!=fk:continue
        if x['reason'] in HARD:return True,1.0,'GLOBAL',{'reason':x['reason'],'source':x.get('asset','?')}
    best=(False,0.0,'NONE',{})
    for x in state['failed_contexts']:
        if x['family_key']!=fk:continue
        same_asset=x.get('asset')==target_asset
        same_regime=x.get('regime')==target_regime and x.get('regime')!='global'
        if same_asset and same_regime: sc,scope=.95,'ASSET_REGIME'
        elif same_asset: sc,scope=.85,'ASSET'
        elif same_regime: sc,scope=.45,'REGIME'
        else: sc,scope=.25,'ASSET_CLASS'
        if x['reason'] in SOFT: sc*=.8  # statistical evidence is softer than structural
        conf=sc*(1.0-0.4*x.get('contradictions',0))
        if conf>best[1]:best=(False,round(conf,4),scope,{'source_experiment':x['experiment_id'],'source_reason':x['reason'],'source_asset':x.get('asset','?'),'source_regime':x.get('regime','?')})
    return best

class TransferAdaptiveDirector(a.AdaptiveScientificDirector):
    name='transfer-adaptive-run13'
    def __init__(self,knowledge=None):self.knowledge=knowledge or {'failed_contexts':[]}
    def propose_research_plan(self,state,round_no,budget):
        merged=dict(state)
        legacy=[{**x,'branch':x.get('branch','')} for x in self.knowledge.get('failed_contexts',[])]
        merged['failed_contexts']=list(state['failed_contexts'])+legacy
        ranked=[]
        for b in a.BRANCHES:
            for idx,e in enumerate(a.choices(b)):
                fk=r.canon(e)
                if fk in state['explored_families']:continue
                score,parts=a.candidate_score(merged,b,e,round_no)
                block,conf,scope,ev=prior_for(merged,fk,state.get('asset','?'),state.get('regime','normal'))
                if block:continue
                score=score-SCORING['negative_similarity_penalty']*conf
                parts={**parts,'transfer_confidence':conf,'transfer_scope':scope,'transfer_evidence':ev}
                discr=conf>0 or any(a.family_similarity(fk,x['family_key'])>=.65 for x in merged['failed_contexts'])
                ranked.append((score,b,e,{'policy':'transfer_adaptive','score':score,'score_parts':parts,'discriminating_experiment':discr,'choice_index':idx}))
        return a.pick_budgeted(ranked,budget)

class ColdAdaptiveDirector(a.AdaptiveScientificDirector):
    name='cold-adaptive-run13'
    def __init__(self):self.knowledge={'failed_contexts':[]}

def cold_choice(state,branch):
    """Counterfactual helper: what the no-memory policy would pick for a branch."""
    best=None
    for idx,e in enumerate(a.choices(branch)):
        fk=r.canon(e)
        if fk in state['explored_families']:continue
        score,_=a.candidate_score(state,branch,e,0)
        if best is None or score>best[0]:best=(score,fk)
    return best[1] if best else None

def saturation_check(state,window=6):
    rows=[state['experiments'][k] for k in state['experiments']][-window:]
    if len(rows)<window:return {'saturated':False,'reason':'insufficient_history'}
    ig=[x['information_gain'] for x in rows];uni=len({x['family_key'] for x in rows})
    novel=[x['observation']['novelty']['novel'] for x in rows if 'observation' in x]
    novel_rate=sum(novel)/max(1,len(novel))
    space=state.get('space_total',10**9);explored=len(set(state['explored_families']))
    exhausted=explored>=space
    # Cold exhausted at round 3 because it explored 21 unique families in a 21-space.
    # After exhaustion, no new round adds value: report true saturation instead of
    # silently re-running exhausted rounds.
    saturated=exhausted or (uni<=2 and (max(ig)-min(ig))<0.3 and novel_rate<.35)
    return {'saturated':bool(saturated),'space_exhausted':bool(exhausted),'window':window,'unique_families':uni,'ig_range':round(max(ig)-min(ig),4),'novel_rate':round(novel_rate,3),'explored':explored,'space_total':space,'verdict':'RESEARCH_SPACE_SATURATED' if saturated else 'OPEN'}

def run_context(ticker,director_cls,knowledge,rounds,budget,ckpt_dir=None):
    """Note: this research space has 21 families (7 branches × 3 preregistered
    choices). Cold policy explores all 21 by round 3 and then honestly saturates
    (RESEARCH_SPACE_SATURATED, space_exhausted=true) — longer horizons cannot add
    experiments without inventing new families, which would fake novelty."""
    base,p=r.load(ticker)
    ext_t='IMOEX' if ticker=='CNY' else 'CNY'
    ext,_=r.load(ext_t);d,cut=r.train_only(base);e,_=r.train_only(ext)
    dh=r.file_hash(p);reg=causal_regimes(d)
    st=a.initial_state(f'{ticker}-{director_cls.__name__}');st['asset']=ticker
    st['space_total']=len(a.BRANCHES)*3;rows=[];counterfactuals=[];done_rounds=[]
    ckpt=None
    if ckpt_dir:
        ckpt=Path(ckpt_dir)/f'{ticker}-{director_cls.__name__}.json'
        if ckpt.exists():
            c=json.loads(ckpt.read_text());rows=c['rows'];done_rounds=c['done_rounds']
            st['experiments']={x['experiment_id']:x for x in rows};st['explored_families']=[x['family_key'] for x in rows]
            st['failed_contexts']=c.get('failed_contexts',[]);st['surviving_families']=c.get('surviving_families',[])
    for rnd in range(1,rounds+1):
        if rnd in done_rounds:continue
        mid=(rnd-1)*len(d)//rounds+len(d)//(2*rounds)
        rg=context_regime(d,reg,min(mid,len(d)-1));st['regime']=rg
        if director_cls is TransferAdaptiveDirector:plan=TransferAdaptiveDirector(knowledge).propose_research_plan(st,rnd,budget)
        else:plan=director_cls().propose_research_plan(st,rnd,budget)
        if not plan:
            # No unexplored family passed hard priors: nothing left to ask honestly.
            # Record why and stop; fabricating experiments would fake novelty.
            st['events'].append({'event':'no_candidates','round':rnd,'reason':'all remaining families hard-blocked or already explored','space_total':st.get('space_total'),'explored':len(set(st['explored_families']))})
            break
        for b,expr,meta in plan:
            obs=r.observe(d,e,expr)
            item=r.PlanItem(b,expr,r.feature_id(expr),r.sid('hyp',{'fk':r.canon(expr),'br':b,'rnd':rnd,'tick':ticker}),r.canon(expr),rnd,{'evaluations':1,'complexity':r.complexity(expr),'cpu_units':r.complexity(expr)},{'min_abs_t':1.0,'half_stability':True})
            status,reasons,metrics=r.economic_test(d,ticker,item,obs,e)
            reason=a.contextual_reason(status,reasons,b,rg)
            rv=r.research_value(status,obs,item,reasons)
            info=rv+(0.5 if meta.get('discriminating_experiment') else 0)+(0.25 if reason in {'CLONE','UNSTABLE','INSUFFICIENT_EVIDENCE'} else 0)
            fk=r.canon(expr)
            cf=cold_choice(st,b)
            counterfactuals.append({'experiment_id':item.hypothesis_id,'branch':b,'decision_with_memory':fk,'counterfactual_decision_without_memory':cf,'same':cf==fk,'transfer_confidence':meta.get('score_parts',{}).get('transfer_confidence',0)})
            rec={'experiment_id':item.hypothesis_id,'ticker':ticker,'branch':b,'feature_expr':expr,'family_key':fk,'regime':rg,'round':rnd,'status':status,'reject_reasons':reasons,'negative_kind':reason,'metrics':metrics,'research_value':rv,'information_gain':round(info,4),'director':director_cls.__name__,'budget_spent':item.estimated_cost,'observation':obs,'director_meta':meta,'ts':r.now()}
            rows.append(rec);st['experiments'][item.hypothesis_id]=rec;st['explored_families'].append(fk)
            br=st['branches'][b];br['experiments_used']+=1;br['compute_cost']+=item.estimated_cost['cpu_units'];br['information_gain']+=info
            if status=='FAIL':
                br['hypotheses_failed']+=1
                contr=sum(1 for x in st['failed_contexts'] if x['family_key']==fk and x.get('contradictions'))
                st['failed_contexts'].append({'family_key':fk,'branch':b,'asset':ticker,'regime':rg,'reason':reason,'experiment_id':item.hypothesis_id,'contradictions':contr})
                if reason=='CLONE':br['novelty_rejections']+=1
            else:
                br['hypotheses_survived']+=1;st['surviving_families'].append(fk)
                for x in st['failed_contexts']:
                    if x['family_key']==fk:x['contradictions']=x.get('contradictions',0)+1
        done_rounds.append(rnd)
        if ckpt:
            ckpt.parent.mkdir(parents=True,exist_ok=True)
            ckpt.write_text(json.dumps({'rows':rows,'done_rounds':done_rounds,'failed_contexts':st['failed_contexts'],'surviving_families':st['surviving_families']},default=str))
        if saturation_check(st)['saturated']:
            st['events'].append({'event':'saturation_stop','round':rnd});break
    st['saturation']=saturation_check(st)
    return {'ticker':ticker,'director':st['label'],'rows':rows,'state':st,'counterfactuals':counterfactuals,'rounds_completed':len(done_rounds)}

def transfer_metrics(cold,transfer):
    """TRANSFER_PRECISION / NEGATIVE_TRANSFER_RATE via COLD ground truth on same ticker."""
    cold_by_fk={x['family_key']:x for x in cold['rows']}
    tr_fks={x['family_key'] for x in transfer['rows']}
    useful=[];harmful=[];conflicts=[]
    for x in transfer['rows']:
        conf=x['director_meta'].get('score_parts',{}).get('transfer_confidence',0)
        if conf>0 and x['status']!='FAIL':
            harmful.append({'family_key':x['family_key'],'transfer_status':x['status'],'prior_confidence':conf})
            conflicts.append({'family_key':x['family_key'],'prior':'FAIL_elsewhere','outcome_here':x['status'],'resolution':'kept_both','decision':'asset_or_regime_specific_or_noise'})
    for fk in set(cold_by_fk)-tr_fks:
        row=cold_by_fk[fk];conf_meta=None
        # how strongly memory penalized it: infer from transfer plan history if present, else from prior table
        for y in transfer['counterfactuals']:
            if y['branch']==row['branch'] and y['counterfactual_decision_without_memory']==fk:conf_meta=y.get('transfer_confidence')
        useful.append({'family_key':fk,'cold_status':row['status'],'cold_reasons':row['reject_reasons']})
    for x in transfer['rows']:
        if x['status']!='FAIL' and x['family_key'] not in tr_fks:pass
    n=len(useful)+len(harmful)
    return {'useful_priors':useful,'harmful_priors':harmful,'knowledge_conflicts':conflicts,'transfer_precision':round(len(useful)/n,4) if n else None,'negative_transfer_rate':round(len(harmful)/n,4) if n else None,'experiments_spent':{'cold':len(cold['rows']),'transfer':len(transfer['rows'])},'experiments_saved':len(cold['rows'])-len(transfer['rows'])}

def freeze_run12():
    src=Path('/root/audits/strategy_combine_research_intelligence/run12/isolated/tournament.json');d=json.loads(src.read_text())
    ad=d['directors']['adaptive-scientific-v1'];st=ad['state']
    return {'frozen_at':r.now(),'source':'run12/isolated/tournament.json','dataset_hash':d['dataset_hash'],'hidden_cutoff':d['hidden_cutoff'],'scoring_coefficients':a.SCORING,'dsl_ops':['raw','zscore','difference','slope','ratio','vol_ratio','cross_return'],'failed_contexts':st['failed_contexts'],'surviving_families':st['surviving_families'],'branches':st['branches'],'graph_nodes':len(st['graph']['nodes']),'graph_edges':len(st['graph']['edges']),'knowledge_snapshots':st['knowledge_snapshots']}

def knowledge_snapshot_v2(per_context):
    snap={'schema':'run13-knowledge-v2','KNOWN_GLOBAL':[],'KNOWN_CONTEXTUAL':[],'CONFLICTING_EVIDENCE':[],'PROMISING_GENERALIZABLE':[],'PROMISING_ASSET_SPECIFIC':[],'FAILED_GENERALIZABLY':[],'OPEN_TRANSFER_QUESTIONS':[]}
    fail_assets={};surv_assets={}
    for t_,x in per_context.items():
        for row in x['transfer']['rows']:
            fk=row['family_key']
            if row['status']=='FAIL':fail_assets.setdefault(fk,set()).add(t_)
            else:surv_assets.setdefault(fk,set()).add(t_)
    for fk,assets in fail_assets.items():
        if len(assets)>=3:snap['FAILED_GENERALIZABLY'].append({'family_key':fk,'assets':sorted(assets)})
        elif len(assets)>=2:snap['KNOWN_CONTEXTUAL'].append({'family_key':fk,'failed_on':sorted(assets),'scope':'MULTI_ASSET'})
        else:snap['KNOWN_CONTEXTUAL'].append({'family_key':fk,'failed_on':sorted(assets),'scope':'ASSET'})
    for fk,assets in surv_assets.items():
        (snap['PROMISING_GENERALIZABLE'] if len(assets)>=2 else snap['PROMISING_ASSET_SPECIFIC']).append({'family_key':fk,'survived_on':sorted(assets)})
    for fk in set(fail_assets)&set(surv_assets):
        snap['CONFLICTING_EVIDENCE'].append({'family_key':fk,'failed_on':sorted(fail_assets[fk]),'survived_on':sorted(surv_assets[fk])})
    return snap

def tournament(rounds=3,budget=7,out=None,ckpt_dir=None):
    fz=freeze_run12()
    result={'schema_version':SCHEMA,'rounds_planned':rounds,'budget_allocated_per_director':rounds*budget,'frozen':{k:fz[k] for k in ('frozen_at','source','dataset_hash','scoring_coefficients')},'per_context':{},'metrics':{}}
    for ticker in UNIVERSE:
        cold=run_context(ticker,ColdAdaptiveDirector,{},rounds,budget,ckpt_dir)
        transfer=run_context(ticker,TransferAdaptiveDirector,fz,rounds,budget,ckpt_dir)
        result['per_context'][ticker]={'cold':cold,'transfer':transfer}
        result['metrics'][ticker]=transfer_metrics(cold,transfer)
    result['knowledge_snapshot_v2']=knowledge_snapshot_v2(result['per_context'])
    if out:Path(out).write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str))
    return result

def _summ(z):
    rows=z['rows'];return dict(exp=len(rows),uni=len({y['family_key'] for y in rows}),rep=len(rows)-len({y['family_key'] for y in rows}),surv=sum(y['status']!='FAIL' for y in rows),ig=round(sum(y['information_gain'] for y in rows),3),sat=z['state']['saturation']['verdict'],rnds=z['rounds_completed'])

if __name__=='__main__':
    import argparse;p=argparse.ArgumentParser();p.add_argument('--out',required=True);p.add_argument('--rounds',type=int,default=3);p.add_argument('--ckpt-dir',default=None);args=p.parse_args()
    res=tournament(rounds=args.rounds,out=args.out,ckpt_dir=args.ckpt_dir)
    for t_,x in res['per_context'].items():print(t_,'COLD',_summ(x['cold']),'TRANSFER',_summ(x['transfer']))
    for t_,m in res['metrics'].items():print(t_,'precision',m['transfer_precision'],'neg_rate',m['negative_transfer_rate'],'saved',m['experiments_saved'])
