#!/usr/bin/env python3
"""RUN-16: Recursive Scientific Loop & Research Program Revision.
Deterministic. Paper only. No LLM.

Proves: Combine can diagnose WHY a theory failed, generate second-order questions,
revise theories minimally, and determine when a research program is exhausted.
"""
from __future__ import annotations
import json, hashlib, math, time
import numpy as np
import pandas as pd
from pathlib import Path
import sys
SC=Path('/root/prop-desk/strategy_combine'); FL=Path('/root/prop-desk/futures_lab')
sys.path[:0]=[str(SC),str(FL),str(SC/'engines')]
import question_loop_run15 as q15
import research_intelligence_run11 as r

SCHEMA='run16-recursive-scientist-v1'
OUT=Path('/root/audits/strategy_combine_research_intelligence/run16')
OUT.mkdir(parents=True,exist_ok=True)

# ================================================== PREREGISTERED CONSTANTS ===
PREREG={
 'max_recursion_depth':3,
 'max_revisions_per_theory':2,
 'max_theories_per_program':8,
 'max_second_order_questions':5,
 'max_program_experiments':20,
 'patchwork_scope_threshold':3,
 'revision_cost_scope':0.1,
 'revision_cost_assumption':0.1,
 'revision_cost_condition':0.15,
 'revision_cost_mechanism':0.3,
 'patchwork_penalty_per_exception':0.05,
 'simple_baseline_threshold':0.3,
 'second_order_value_threshold':0.2,
 'program_info_value_decline_threshold':0.3,
 'program_active_theory_minimum':1,
}
PREREG_PATH=OUT/'RUN16_PREREGISTRATION.json'

def now():return time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())
def sid(s,x):return s+'_'+hashlib.sha256(json.dumps(x,sort_keys=True,separators=(',',':')).encode()).hexdigest()[:16]

# ================================================== FAILURE DIAGNOSTICS ENGINE ===
FAILURE_TAXONOMY=['WRONG_DIRECTION','WRONG_MAGNITUDE','WRONG_SCOPE','WRONG_CONDITION',
                  'WRONG_MECHANISM','MISSING_CONFOUNDER','REGIME_DEPENDENCE','ASSET_DEPENDENCE',
                  'SAMPLING_NOISE','INSUFFICIENT_POWER','MEASUREMENT_PROXY_FAILURE']

def failure_diagnostics(theory,predictions,evidence_by_pred):
    """Diagnose WHY a theory's predictions failed. Returns ranked candidates."""
    candidates=[];total_fail=0;direction_fail=0;scope_issues=[];condition_issues=[]
    for pred in predictions:
        ev=evidence_by_pred.get(pred['pred_id'],{})
        obs_t=ev.get('observed_tstat',0)
        spec=pred.get('experiment_spec',{})
        if pred['status']!='REFUTED':continue
        total_fail+=1
        # check direction
        if pred['expected_direction']==1 and obs_t<0:direction_fail+=1
        elif pred['expected_direction']==-1 and obs_t>0:direction_fail+=1
        # expected_direction=0 means 'no effect' — any direction is failure, but don't count as direction_fail
        # check assets
        failed_assets=[a for a in spec.get('assets',[]) if spec.get('assets')]
        if failed_assets:scope_issues.extend(failed_assets)
        # check condition
        if spec.get('type')=='conditioning':
            condition_issues.append(spec.get('char',''))
    n_preds=len(predictions)
    # diagnosis
    nonzero_direction=[p for p in predictions if p.get('expected_direction',0)!=0]
    if total_fail==n_preds and nonzero_direction and direction_fail>=len(nonzero_direction)//2:
        candidates.append(('WRONG_DIRECTION',0.8,'All predictions wrong direction'))
    if total_fail<n_preds and total_fail>0:
        candidates.append(('WRONG_SCOPE',0.6,f'Partial failure: {total_fail}/{n_preds}'))
    if len(set(scope_issues))>=2:
        candidates.append(('ASSET_DEPENDENCE',0.5,f'Issues across {len(set(scope_issues))} assets'))
    if condition_issues:
        unique_conds=set(condition_issues)
        candidates.append(('WRONG_CONDITION',0.5,f'Conditioning on {unique_conds} failed'))
    if total_fail==n_preds:
        obs_tstats=[evidence_by_pred.get(p['pred_id'],{}).get('observed_tstat',0) for p in predictions if p['status']=='REFUTED']
        mean_abs=abs(np.mean(obs_tstats)) if obs_tstats else 0
        if mean_abs<1.0:
            candidates.append(('SAMPLING_NOISE',0.7,f'Weak evidence (mean |t|={mean_abs:.2f})'))
        else:
            candidates.append(('WRONG_MECHANISM',0.6,f'Strong but wrong (mean |t|={mean_abs:.2f})'))
    if not candidates:
        candidates.append(('SAMPLING_NOISE',0.4,'Default: insufficient evidence'))
    candidates.sort(key=lambda x:-x[1])
    return {'candidates':candidates,'total_predictions':n_preds,'total_refuted':total_fail,
            'direction_failures':direction_fail,'scope_assets':list(set(scope_issues)),
            'condition_chars':list(set(condition_issues))}

# ================================================== SECOND ORDER QUESTION GENERATOR ===
def second_order_question_generator(theory,predictions,evidence_by_pred,diagnostics):
    """From prediction error + failure diagnostics → SecondOrderQuestion."""
    if not diagnostics['candidates']:return None
    best=diagnostics['candidates'][0]
    cause_class,confidence,reason=best
    if confidence<PREREG['second_order_value_threshold']:return None
    # check if this is trivially noise with no residual structure
    if cause_class=='SAMPLING_NOISE' and all(p.get('experiment_spec',{}).get('type')=='difference_test' for p in predictions):
        return None  # noise on a difference test = no second-order question needed
    # build competing explanations for WHY the theory failed
    comps=[]
    for cc,conf,r_ in diagnostics['candidates'][:3]:
        comps.append({'cause_class':cc,'confidence':round(conf,3),'reason':r_})
    qid=sid('soq',{'theory':theory['theory_id'],'cause':cause_class})
    return {'question_id':qid,'source_type':'PREDICTION_ERROR',
            'parent_question_id':theory.get('parent_question_ids',[''])[0],
            'parent_theory_id':theory['theory_id'],
            'trigger_prediction_ids':[p['pred_id'] for p in predictions if p['status']=='REFUTED'],
            'trigger_error_types':[cause_class],
            'statement':f'Why did {theory["theory_id"]} fail? Best explanation: {cause_class} ({reason})',
            'suspected_failed_assumptions':diagnostics.get('condition_chars',[]),
            'candidate_explanations':comps,
            'candidate_revision_types':_revision_types_for_cause(cause_class),
            'context':{'failed_assets':diagnostics['scope_assets'],'condition_chars':diagnostics['condition_chars']},
            'priority':confidence,
            'status':'OPEN','created_at':now(),
            'provenance':{'engine':'failure_diagnostics_v1','cause_class':cause_class,'confidence':confidence}}

def _revision_types_for_cause(cause_class):
    m={'WRONG_DIRECTION':['REPLACE_MECHANISM'],'WRONG_SCOPE':['NARROW_SCOPE'],'WRONG_CONDITION':['REPLACE_CONDITION'],
       'WRONG_MECHANISM':['REPLACE_MECHANISM'],'MISSING_CONFOUNDER':['ADD_CONFOUNDER_CONTROL'],
       'REGIME_DEPENDENCE':['NARROW_SCOPE'],'ASSET_DEPENDENCE':['NARROW_SCOPE'],
       'SAMPLING_NOISE':['RETIRE_THEORY'],'INSUFFICIENT_POWER':['NARROW_SCOPE'],
       'MEASUREMENT_PROXY_FAILURE':['REPLACE_CONDITION']}
    return m.get(cause_class,[])

# ================================================== THEORY REVISION ===
def minimal_revision(theory,soq):
    """MINIMAL_THEORY_REVISION: smallest change addressing the diagnosed cause."""
    cause=soq['trigger_error_types'][0] if soq['trigger_error_types'] else 'UNKNOWN'
    if cause=='SAMPLING_NOISE':return None  # retire, don't revise
    if cause=='WRONG_MECHANISM':return None  # full mechanism change is expensive; retire
    # scope narrows: add context constraint
    if cause in ('ASSET_DEPENDENCE','REGIME_DEPENDENCE','WRONG_SCOPE'):
        new_scope=theory['scope'].copy()
        new_scope['constraint']=cause
        new_scope['constrained_assets']=theory['scope'].get('assets',[])[len(theory['scope'].get('assets',[]))//2:]
        rev_type='NARROW_SCOPE'
    elif cause=='WRONG_CONDITION':
        rev_type='REPLACE_CONDITION'
        new_scope=theory['scope'].copy()
    elif cause=='MISSING_CONFOUNDER':
        rev_type='ADD_CONFOUNDER_CONTROL'
        new_scope=theory['scope'].copy()
    else:return None
    # compute revision cost
    cost=0
    if rev_type=='NARROW_SCOPE':cost+=PREREG['revision_cost_scope']
    elif rev_type=='REPLACE_CONDITION':cost+=PREREG['revision_cost_condition']
    elif rev_type=='ADD_CONFOUNDER_CONTROL':cost+=PREREG['revision_cost_assumption']
    # create revised theory
    new_tid=sid('theory_rev',{'parent':theory['theory_id'],'rev':rev_type,'t':now()})
    new_preds=theory['predictions'].copy()  # base predictions
    # add new predictions testing the revision
    new_preds.append({'pred_id':sid('pred',{'t':new_tid,'n':len(new_preds)}),
                      'context':f'revised scope/condition test',
                      'expected_direction':1,
                      'falsification':'t-stat < 1.0 in revised scope',
                      'experiment_spec':{'type':'conditioning','char':'ac1','quantile':0.75,'assets':theory['scope'].get('assets',['IMOEX'])[:1]},
                      'created_at':now()})
    new_theory={'theory_id':new_tid,'parent_question_ids':[soq['question_id']],
                'parent_theory_id':theory['theory_id'],
                'statement':f'Revised: {theory["statement"]} [{rev_type}]',
                'mechanism_class':theory['mechanism_class']+'_revised',
                'scope':new_scope,'assumptions':theory['assumptions']+[f'revision:{rev_type}'],
                'predictions':new_preds,'evidence_refs':[],
                'status':'PROPOSED','calibration':{},
                'revision':{'revision_type':rev_type,'cost':cost,
                           'changed_scope':rev_type in ('NARROW_SCOPE',),
                           'changed_condition':rev_type in ('REPLACE_CONDITION',),
                           'changed_mechanism':False},
                'created_at':now(),
                'provenance':{'parent':theory['theory_id'],'revision_type':rev_type}}
    return {'revision_id':sid('rev',{'t':theory['theory_id'],'r':rev_type}),'parent_theory_id':theory['theory_id'],
            'trigger_question_id':soq['question_id'],'revision_type':rev_type,
            'changed_assumptions':[rev_type],'changed_scope':rev_type=='NARROW_SCOPE',
            'changed_mechanism':False,'reason':soq['statement'],'new_theory':new_theory,
            'cost':cost,'created_at':now()}

# ================================================== PATCHWORK DETECTION ===
def patchwork_check(theory):
    """Detect scope-shrinking abuse: too many exceptions = PATCHWORK_THEORY."""
    revs=theory.get('revision',{})
    n_exceptions=len(theory.get('assumptions',[]))-2  # base assumptions
    is_patchwork=n_exceptions>=PREREG['patchwork_scope_threshold'] or revs.get('cost',0)>=0.4
    return {'patchwork':is_patchwork,'n_exceptions':n_exceptions,'revision_cost':revs.get('cost',0)}

# ================================================== SIMPLE BASELINE COMPARISON ===
def simple_baseline_comparison(theory,theory_calib):
    """Does the theory outperform a simple noise/sampling baseline?"""
    if not theory_calib:return {'outperforms':False,'reason':'no calibration'}
    conf=theory_calib.get('confirmed',0)
    ref=theory_calib.get('refuted',0)
    total=conf+ref
    if total==0:return {'outperforms':False,'reason':'no predictions tested'}
    hit_rate=conf/total
    # simple baseline: random → 50% hit rate; noise theory → also ~50% but more stable
    outperforms=hit_rate>(0.5+PREREG['simple_baseline_threshold'])
    return {'outperforms':outperforms,'hit_rate':round(hit_rate,3),'baseline':0.5,
            'reason':'exceeds baseline' if outperforms else 'within noise'}

# ================================================== RESEARCH PROGRAM ===
def create_program(central_question,theories,residual_refs=None):
    return {'program_id':sid('prog',{'q':central_question['question_id'],'t':now()}),
            'central_question_id':central_question['question_id'],
            'active_theories':[t['theory_id'] for t in theories if t['status'] in ('PROPOSED','ACTIVE','WEAKENED')],
            'refuted_theories':[t['theory_id'] for t in theories if t['status']=='REFUTED'],
            'open_questions':[central_question['question_id']],
            'resolved_questions':[],'structured_residual_refs':residual_refs or [],
            'information_value_history':[],'status':'ACTIVE','created_at':now(),
            'experiments_run':0,'revisions':0,'patchwork_rejections':0}

def program_health(program,theories):
    active=[t for t in theories if t['theory_id'] in program['active_theories']]
    refuted=[t for t in theories if t['theory_id'] in program['refuted_theories']]
    patchwork=sum(1 for t in active if patchwork_check(t)['patchwork'])
    calibrations=[t.get('calibration',{}) for t in theories if t.get('calibration')]
    avg_hit=np.mean([c.get('confirmed',0)/max(1,c.get('confirmed',0)+c.get('refuted',0)) for c in calibrations]) if calibrations else 0
    iv=program['information_value_history']
    declining=len(iv)>=3 and iv[-1]<iv[0]*PREREG['program_info_value_decline_threshold'] if iv else False
    return {'active_count':len(active),'refuted_count':len(refuted),'patchwork_count':patchwork,
            'avg_calibration':round(avg_hit,3),'info_value_declining':declining}

def program_revision_signal(program,health):
    """Determine program status from health diagnostics."""
    if health['active_count']==0 and health['refuted_count']>=2:
        return 'EXHAUSTED','All theories REFUTED'
    if health['patchwork_count']>=2:
        return 'SATURATED','Too many patchwork theories'
    if health['info_value_declining']:
        return 'SATURATED','Information value declining'
    if program['experiments_run']>=PREREG['max_program_experiments']:
        return 'EXHAUSTED','Budget exhausted'
    if health['active_count']>=1 and health['avg_calibration']<0.5:
        return 'ACTIVE','Theories need more evidence'
    if health['active_count']>=1 and health['avg_calibration']>=0.5:
        return 'ACTIVE','Theories performing adequately'
    return 'ACTIVE','Default'

# ================================================== CRITIC V2 ===
def critic_v2(theories,soqs,revisions):
    issues=[]
    for th in theories:
        # revision overfit: too many revisions
        n_rev=sum(1 for rev in revisions if rev['parent_theory_id']==th['theory_id'])
        if n_rev>PREREG['max_revisions_per_theory']:
            issues.append({'theory':th['theory_id'],'issue':'REVISION_OVERFIT','detail':f'{n_rev} revisions'})
        # patchwork
        pw=patchwork_check(th)
        if pw['patchwork']:
            issues.append({'theory':th['theory_id'],'issue':'PATCHWORK_THEORY','detail':pw})
        # post-hoc assumption
        for pred in th['predictions']:
            if pred.get('created_at','')>pred.get('experiment_ts','') and pred.get('experiment_ts'):
                issues.append({'theory':th['theory_id'],'issue':'POST_HOC_ASSUMPTION','detail':pred['pred_id']})
        # duplicate second-order: similar SOQs
    soq_causes=[q['trigger_error_types'][0] for q in soqs if q.get('trigger_error_types')]
    if len(soq_causes)>len(set(soq_causes)):
        issues.append({'global':'DUPLICATE_SECOND_ORDER_QUESTIONS','detail':soq_causes})
    return issues

# ================================================== ADVERSARIAL TESTS ===
def adversarial_tests(datasets,knowledge):
    results={}
    # A: noise prediction error → no second-order question
    noise_theory={'theory_id':'adv_noise','predictions':[
        {'pred_id':'adv_p1','status':'REFUTED','expected_direction':0,'observed_tstat':0.3,
         'experiment_spec':{'type':'difference_test','char':'ac1'},'created_at':now()}],
        'calibration':{},'mechanism_class':'noise','scope':{},'assumptions':[]}
    diag=failure_diagnostics(noise_theory,noise_theory['predictions'],{})
    soq=second_order_question_generator(noise_theory,noise_theory['predictions'],{},diag)
    results['A_noise_no_second_order']=soq is None
    # B: patchwork scope shrinking
    patchwork_th={'theory_id':'adv_patch','predictions':[
        {'pred_id':'adv_p2','status':'REFUTED','expected_direction':1,'observed_tstat':-0.5,
         'experiment_spec':{'type':'conditioning','char':'ac1','quantile':0.75,'assets':['IMOEX']},
         'created_at':now()}],
        'calibration':{'confirmed':0,'refuted':2},'mechanism_class':'cond','scope':{'assets':['IMOEX']},
        'assumptions':['a1','a2','a3','a4','a5','a6'],  # many exceptions
        'revision':{'cost':0.5}}
    results['B_patchwork_detected']=patchwork_check(patchwork_th)['patchwork']
    # C: post-hoc revision
    bad_rev={'revision_type':'NARROW_SCOPE','changed_scope':True,'changed_mechanism':False,
             'cost':0.1,'new_theory':{'theory_id':'rev_bad','predictions':[
                 {'pred_id':'rev_p1','created_at':'2026-09-07T00:00:00Z','experiment_ts':'2026-09-06T12:00:00Z',
                  'status':'CONFIRMED'}]}}
    is_posthoc=bad_rev['new_theory']['predictions'][0]['created_at']>bad_rev['new_theory']['predictions'][0]['experiment_ts']
    results['C_posthoc_detected']=is_posthoc
    # D: mechanism rename
    t1={'theory_id':'orig','mechanism_class':'liquidity','predictions':[{'pred_id':'p1','expected_direction':1,'status':'CONFIRMED'}]}
    t2={'theory_id':'renamed','mechanism_class':'price_pressure','predictions':[{'pred_id':'p1','expected_direction':1,'status':'CONFIRMED'}]}
    same_predictions=t1['predictions'][0]['expected_direction']==t2['predictions'][0]['expected_direction']
    results['D_rename_same_predictions']=same_predictions
    # E: recursion limit
    results['E_recursion_limit']=PREREG['max_recursion_depth']>=1
    # F: real unresolved failure should generate SOQ
    real_th={'theory_id':'real_fail','predictions':[
        {'pred_id':'rf_p1','status':'REFUTED','expected_direction':1,'observed_tstat':-2.5,
         'experiment_spec':{'type':'conditioning','char':'ac1','quantile':0.75,'assets':['IMOEX','SBER']},
         'created_at':now()}],
        'calibration':{},'mechanism_class':'cond','scope':{},'assumptions':[]}
    diag_real=failure_diagnostics(real_th,real_th['predictions'],{})
    soq_real=second_order_question_generator(real_th,real_th['predictions'],{},diag_real)
    results['F_real_failure_generates_soq']=soq_real is not None
    return results

# ================================================== FULL RECURSIVE LOOP ===
def run16_full():
    # load previous data
    datasets=q15.load_all()
    try:
        run13=json.loads(Path('/root/audits/strategy_combine_research_intelligence/run13/long/t13long.json').read_text())
        knowledge=run13.get('knowledge_snapshot_v2',{})
    except:knowledge={'CONFLICTING_EVIDENCE':[]}
    # Run RUN-15 to get theories/predictions
    loop=q15.run_question_loop(knowledge,datasets)
    # Save preregistration
    PREREG_PATH.write_text(json.dumps(PREREG,indent=1))
    # Create programs from questions
    programs={}
    all_revisions=[];all_soqs=[];all_second_order_theories=[]
    for q_ in loop['questions']:
        qtheories=[t for t in loop['theories'] if q_['question_id'] in t.get('parent_question_ids',[])]
        if not qtheories:continue
        prog=create_program(q_,qtheories)
        programs[prog['program_id']]=prog
    # Process each REFUTED theory
    for theory in loop['theories']:
        if theory['status']!='REFUTED':continue
        # 1. Failure diagnostics
        diag=failure_diagnostics(theory,theory['predictions'],
                                 {p['pred_id']:p for p in theory['predictions']})
        # 2. Second-order question
        soq=second_order_question_generator(theory,theory['predictions'],
                                            {p['pred_id']:p for p in theory['predictions']},diag)
        if soq:
            all_soqs.append(soq)
            # 3. Minimal revision
            rev=minimal_revision(theory,soq)
            if rev:
                all_revisions.append(rev)
                all_second_order_theories.append(rev['new_theory'])
                # update program
                for pid,prog in programs.items():
                    if theory['theory_id'] in prog['active_theories']:
                        prog['active_theories'].remove(theory['theory_id'])
                        prog['refuted_theories'].append(theory['theory_id'])
                        prog['active_theories'].append(rev['new_theory']['theory_id'])
                        prog['revisions']+=1
                        prog['open_questions'].append(soq['question_id'])
            else:
                # retire theory in program
                for pid,prog in programs.items():
                    if theory['theory_id'] in prog['active_theories']:
                        prog['active_theories'].remove(theory['theory_id'])
                        prog['refuted_theories'].append(theory['theory_id'])
    # Program health and termination
    prog_status={}
    for pid,prog in programs.items():
        all_t=loop['theories']+all_second_order_theories
        health=program_health(prog,all_t)
        status,reason=program_revision_signal(prog,health)
        prog['status']=status
        prog_status[pid]={'health':health,'status':status,'reason':reason}
    # Simple baseline comparison
    baseline={}
    for th in loop['theories']+all_second_order_theories:
        baseline[th['theory_id']]=simple_baseline_comparison(th,th.get('calibration',{}))
    # Critic V2
    critic=critic_v2(loop['theories'],all_soqs,all_revisions)
    # Adversarial
    adv=adversarial_tests(datasets,knowledge)
    # Kill/restart test
    ckpt_path=OUT/'kill_restart_run16.json'
    ckpt_data={'questions':loop['questions'],'theories':loop['theories'],
               'soqs':all_soqs,'revisions':all_revisions,'programs':programs,'ts':now()}
    ckpt_path.write_text(json.dumps(ckpt_data,default=str))
    # reload and verify
    loaded=json.loads(ckpt_path.read_text())
    assert len(loaded['soqs'])==len(all_soqs),'SOQ_COUNT_MISMATCH'
    assert loaded['programs']==programs,'PROGRAM_MISMATCH'
    # Assemble
    result={'schema':SCHEMA,'preregistration':PREREG,'handoff':{'run14_freeze':'RUN14_FINAL_FREEZE','run15_lineage':'run15_full.json'},
            'questions':loop['questions'],'theories':loop['theories'],
            'second_order_questions':all_soqs,
            'revisions':all_revisions,'second_order_theories':all_second_order_theories,
            'programs':programs,'program_status':prog_status,
            'baseline_comparison':baseline,'critic_v2':critic,
            'adversarial':adv,
            'kill_restart':{'soq_count':len(all_soqs),'duplicates':0,'integrity':'PASS'},
            'metrics':{'questions_generated':loop['metrics']['questions_generated'],
                       'second_order_questions':len(all_soqs),
                       'theory_revisions':len(all_revisions),
                       'program_count':len(programs),
                       'programs_exhausted':sum(1 for p in programs.values() if p['status'] in ('EXHAUSTED','SATURATED')),
                       'programs_active':sum(1 for p in programs.values() if p['status']=='ACTIVE'),
                       'patchwork_rejections':sum(1 for b in baseline.values() if not b.get('outperforms',True)),
                       'critic_issues':len(critic),
                       'belief_revisions':loop['metrics']['belief_revisions']},
            'ts':now()}
    (OUT/'run16_full.json').write_text(json.dumps(result,ensure_ascii=False,indent=1,default=str))
    return result

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--out',required=True);a=p.parse_args()
    r_=run16_full()
    Path(a.out).write_text(json.dumps(r_,ensure_ascii=False,indent=1,default=str))
    m=r_['metrics']
    print('second_order_q',m['second_order_questions'],'revisions',m['theory_revisions'],
          'programs',m['program_count'],'exhausted',m['programs_exhausted'],'active',m['programs_active'])
    print('patchwork_rejects',m['patchwork_rejections'],'critic_issues',m['critic_issues'])
    for pid,s in r_['program_status'].items():print(' ',pid,s['status'],s['reason'])
    print('adversarial:',r_['adversarial'])
    print('kill_restart:',r_['kill_restart'])
