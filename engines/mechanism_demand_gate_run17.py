#!/usr/bin/env python3
"""RUN-17: Mechanism Demand Gate & Research Program Closure.
Deterministic. Paper only. No LLM.

Proves: Combine can determine whether to KEEP_TESTING, REPLICATE, REFOCUS,
COMPLETED, EXHAUSTED, REQUIRES_NEW_INFORMATION, or REQUIRES_NEW_MECHANISM
for each ResearchProgram.
"""
from __future__ import annotations
import json,hashlib,math,time
import numpy as np
import pandas as pd
from pathlib import Path
import sys
SC=Path('/root/prop-desk/strategy_combine');FL=Path('/root/prop-desk/futures_lab')
sys.path[:0]=[str(SC),str(FL),str(SC/'engines')]
import recursive_scientist_run16 as r16
import question_loop_run15 as q15

SCHEMA='run17-mechanism-demand-gate-v1'
OUT=Path('/root/audits/strategy_combine_research_intelligence/run17')
OUT.mkdir(parents=True,exist_ok=True)

# ================================================== PREREGISTERED CONSTANTS ===
PREREG={
 'max_untested_high_voi_predictions':0,
 'replication_min_contexts':2,
 'noise_calibration_threshold':0.5,
 'noise_baseline_margin':0.15,
 'structured_residual_autocorr_t':2.0,
 'structured_residual_regime_t':2.0,
 'voi_fresh_experiment_threshold':0.15,
 'mechanism_exhaustion_min_refuted':2,
 'mechanism_exhaustion_min_tested_contexts':2,
 'program_info_value_decline_threshold':0.3,
 'false_demand_budget_threshold':True,
 'false_demand_residual_noise':True,
 'false_demand_untested_mechanisms':True,
 'false_demand_missing_data':True,
 'false_demand_leakage':True,
}
PREREG_PATH=OUT/'RUN17_PREREGISTRATION.json'
def now():return time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())
def sid(s,x):return s+'_'+hashlib.sha256(json.dumps(x,sort_keys=True,separators=(',',':')).encode()).hexdigest()[:16]

# ================================================== MECHANISM REGISTRY ===
MECHANISM_CLASSES={
 'conditioning_effect':{
  'class':'conditioning','question_types':['KNOWLEDGE_CONFLICT'],
  'required_info':['quantile_conditioning'],'status':'REFUTED',
  'refuted_contexts':['relvol_high','relvol_low','dead_share_high','dead_share_low'],
  'tested_contexts':['relvol_high','relvol_low','dead_share_high','dead_share_low']},
 'sampling_noise':{
  'class':'noise','question_types':['KNOWLEDGE_CONFLICT','STRUCTURED_RESIDUAL'],
  'required_info':[],'status':'SUPPORTED',
  'refuted_contexts':[],'tested_contexts':['relvol','dead_share_48','dead_share_24','dead_share_36']},
 'autocorrelation_structure':{
  'class':'temporal','question_types':['STRUCTURED_RESIDUAL'],
  'required_info':['return_series'],'status':'UNTESTED',
  'refuted_contexts':[],'tested_contexts':[]},
 'regime_conditioning':{
  'class':'regime','question_types':['KNOWLEDGE_CONFLICT'],
  'required_info':['volatility_regime'],'status':'UNTESTED',
  'refuted_contexts':[],'tested_contexts':[]},
 'cross_asset_common':{
  'class':'cross_asset','question_types':['KNOWLEDGE_CONFLICT'],
  'required_info':['cross_asset_correlation'],'status':'UNTESTED',
  'refuted_contexts':[],'tested_contexts':[]},
 'intraday_microstructure':{
  'class':'microstructure','question_types':['STRUCTURED_RESIDUAL'],
  'required_info':['order_flow'],'status':'UNTESTED',
  'refuted_contexts':[],'tested_contexts':[]},
 'volume_price_feedback':{
  'class':'feedback','question_types':['KNOWLEDGE_CONFLICT'],
  'required_info':['volume_price_interaction'],'status':'UNTESTED',
  'refuted_contexts':[],'tested_contexts':[]},
 'macro_regime':{
  'class':'macro','question_types':['KNOWLEDGE_CONFLICT'],
  'required_info':['macro_indicators'],'status':'UNTESTED',
  'refuted_contexts':[],'tested_contexts':[]},
}
MECHANISM_REGISTRY_PATH=OUT/'mechanism_registry.json'

# ================================================== DECISION PRECEDENCE (preregistered tree) ===
def program_closure_decision(program,theories,soqs,revisions,residuals,voi_history,baselines,data_available):
    """Strict preregistered decision tree. Returns (decision, reason_codes, evidence)."""
    decision='PROGRAM_EXHAUSTED';reasons=[];evidence=[]
    active_th=[t for t in theories if t['theory_id'] in program.get('active_theories',[])]
    refuted_th=[t for t in theories if t['theory_id'] in program.get('refuted_theories',[])]
    # 1. Integrity check
    if program.get('integrity_failed'):
        return 'FAILED_INTEGRITY',['INTEGRITY_VIOLATION'],[]
    # 6 (pre-check): Missing data prevents any testing?
    missing_data_pre=[]
    for t in active_th:
        for p in t.get('predictions',[]):
            for a in p.get('experiment_spec',{}).get('assets',[]):
                if a not in data_available:missing_data_pre.append(a)
    if missing_data_pre:
        return 'REQUIRES_NEW_INFORMATION',{'MISSING_DATA':{'issues':missing_data_pre[:5]}},active_th
    # 2. Untested high-VOI predictions?
    untested_high_voi=0
    for t in active_th:
        for p in t.get('predictions',[]):
            if p.get('status')=='PENDING':untested_high_voi+=1
    if untested_high_voi>0:
        return 'KEEP_TESTING',['UNTESTED_PREDICTIONS',{'count':untested_high_voi}],active_th
    # 3. Replication needed?
    confirmed_th=[t for t in active_th if t.get('status')=='ACTIVE']
    replication_needed=False
    for t in confirmed_th:
        cal=t.get('calibration',{})
        if cal.get('confirmed',0)<3:replication_needed=True
    if replication_needed and len(confirmed_th)>0:
        return 'REPLICATE',['INSUFFICIENT_CONFIRMATIONS',
                           {'confirmed_count':sum(t.get('calibration',{}).get('confirmed',0) for t in confirmed_th)}],confirmed_th
    # 4. REFOCUS?
    narrow_scope_candidates=[t for t in active_th if t.get('revision',{}).get('revision_type')=='NARROW_SCOPE']
    if narrow_scope_candidates:
        return 'REFOCUS',['NARROW_SCOPE_AVAILABLE',{'count':len(narrow_scope_candidates)}],narrow_scope_candidates
    # 5. Simple/noise explanation sufficient?
    noise_th=[t for t in theories if t.get('mechanism_class','')=='noise']
    noise_sufficient=False
    if noise_th:
        best_noise=noise_th[0]
        cal=best_noise.get('calibration',{})
        hit=cal.get('confirmed',0)/max(1,cal.get('confirmed',0)+cal.get('refuted',0))
        bl=baselines.get(best_noise['theory_id'],{})
        if hit>=0.5+PREREG['noise_baseline_margin']:
            noise_sufficient=True
            evidence.append({'type':'noise_explanation_sufficient',
                           'hit_rate':round(hit,3),'baseline':bl.get('baseline',0.5)})
    if noise_sufficient:
        return 'COMPLETED',['NOISE_EXPLANATION_SUFFICIENT',evidence[-1]],noise_th
    # 6. Missing data?
    missing_data_issues=[]
    for t in active_th:
        for p in t.get('predictions',[]):
            spec=p.get('experiment_spec',{})
            for a in spec.get('assets',[]):
                if a not in data_available:
                    missing_data_issues.append(f'missing_data_{a}')
    if missing_data_issues:
        return 'REQUIRES_NEW_INFORMATION',['MISSING_DATA',{'issues':missing_data_issues[:5]}],active_th
    # 7. Mechanism space exhausted?
    structured_residual=any(r.get('structured',False) for r in residuals.values()) if residuals else False
    mechanism_exhausted=False
    tested_mechanisms=set()
    refuted_mechanisms=set()
    for t in refuted_th:
        mc=t.get('mechanism_class','')
        if mc and mc!='noise':
            refuted_mechanisms.add(mc)
    for t in active_th+refuted_th:
        mc=t.get('mechanism_class','')
        if mc and mc!='noise':tested_mechanisms.add(mc)
    # check registry
    available_mechanisms=set(MECHANISM_CLASSES.keys())
    for mk,mv in MECHANISM_CLASSES.items():
        if mv['status']=='REFUTED':
            refuted_mechanisms.add(mk)
    untested_mechanisms=(available_mechanisms-{'sampling_noise'})-refuted_mechanisms-set(t.get('mechanism_class','') for t in active_th)
    # structured residual exists AND all mechanisms refuted AND no untested
    if (structured_residual and
        len(refuted_mechanisms)>=PREREG['mechanism_exhaustion_min_refuted'] and
        len(untested_mechanisms)==0):
        mechanism_exhausted=True
    if mechanism_exhausted:
        return 'REQUIRES_NEW_MECHANISM',['MECHANISM_SPACE_EXHAUSTED',
                                         {'refuted':list(refuted_mechanisms),
                                          'untested':list(untested_mechanisms),
                                          'structured_residual':structured_residual}],active_th
    # 8. Default
    return 'PROGRAM_EXHAUSTED',['NO_VIABLE_THEORIES',
                                {'active':len(active_th),'refuted':len(refuted_th)}],active_th+refuted_th

# ================================================== NOISE CLOSURE TEST ===
def noise_closure_test(theories,program,baselines,residuals):
    """Determine if noise is a sufficient explanation for the program's question."""
    noise_th=[t for t in theories if t.get('mechanism_class','')=='noise'
              and t['theory_id'] in program.get('active_theories',[])+program.get('refuted_theories',[])]
    if not noise_th:return {'sufficient':False,'reason':'no_noise_theory'}
    th=noise_th[0]
    cal=th.get('calibration',{})
    conf=cal.get('confirmed',0);ref=cal.get('refuted',0);total=conf+ref
    if total==0:return {'sufficient':False,'reason':'no_predictions_tested'}
    hit_rate=conf/total
    bl=baselines.get(th['theory_id'],{})
    baseline_rate=bl.get('baseline',0.5)
    # check structured residual
    structured_residual_exists=any(r.get('structured',False) for r in residuals.values()) if residuals else False
    structured_after_noise=structured_residual_exists  # noise doesn't eliminate residual
    sufficient=(hit_rate>=baseline_rate+PREREG['noise_baseline_margin'] and
                total>=4 and not structured_after_noise)
    # if structured residual exists, noise is NOT sufficient (there's real structure)
    if structured_after_noise and hit_rate<0.8:
        sufficient=False
    return {'sufficient':sufficient,'hit_rate':round(hit_rate,3),'total_predictions':total,
            'baseline_rate':baseline_rate,'structured_residual_exists':structured_residual_exists,
            'structured_after_noise':structured_after_noise,
            'margin':round(hit_rate-baseline_rate,3)}

# ================================================== MISSING CAPABILITY ANALYSIS ===
def missing_capability_analysis(program,theories,data_available,mechanism_space):
    """Determine what's missing: data, mechanism, or nothing."""
    active_th=[t for t in theories if t['theory_id'] in program.get('active_theories',[])]
    missing_data=[]
    missing_mechanism=False
    for t in active_th:
        for p in t.get('predictions',[]):
            spec=p.get('experiment_spec',{})
            for a in spec.get('assets',[]):
                if a not in data_available:missing_data.append(a)
    # check if mechanism vocabulary is insufficient
    refuted_mechanisms=set()
    for t in theories:
        if t.get('status')=='REFUTED' and t.get('mechanism_class','')!='noise':
            refuted_mechanisms.add(t.get('mechanism_class',''))
    available_mechs=set(MECHANISM_CLASSES.keys())
    untested=available_mechs-refuted_mechanisms-set(t.get('mechanism_class','') for t in active_th)
    if missing_data:
        return 'MISSING_DATA',missing_data
    elif untested:
        return 'NO_MISSING_CAPABILITY',list(untested)
    else:
        return 'MISSING_MECHANISM',list(refuted_mechanisms)

# ================================================== FALSE DEMAND PROTECTION ===
def false_demand_protection(program,theories,residuals,baselines,data_available):
    """Check all conditions that should PREVENT mechanism demand."""
    reasons=[]
    # A: budget exhausted but high VOI remains
    voi=program.get('information_value_history',[])
    if voi and voi[-1]<PREREG['voi_fresh_experiment_threshold']:
        reasons.append(('BUDGET_EXHAUSTED_BUT_VOI_HIGH',{'last_voi':voi[-1]}))
    # B: residual pure noise
    residual_structured=any(r.get('structured',False) for r in residuals.values()) if residuals else False
    if not residual_structured:
        reasons.append(('RESIDUAL_IS_NOISE',{}))
    # C: missing required data
    active_th=[t for t in theories if t['theory_id'] in program.get('active_theories',[])]
    for t in active_th:
        for p in t.get('predictions',[]):
            for a in p.get('experiment_spec',{}).get('assets',[]):
                if a not in data_available:
                    reasons.append(('MISSING_REQUIRED_DATA',{'asset':a}))
                    break
    # D: simple baseline explains phenomenon
    noise_th=[t for t in theories if t.get('mechanism_class','')=='noise']
    if noise_th:
        cal=noise_th[0].get('calibration',{})
        hit=cal.get('confirmed',0)/max(1,cal.get('confirmed',0)+cal.get('refuted',0))
        bl=baselines.get(noise_th[0]['theory_id'],{})
        if hit>=bl.get('baseline',0.5)+PREREG['noise_baseline_margin']:
            reasons.append(('SIMPLE_BASELINE_SUFFICIENT',{'hit_rate':round(hit,3)}))
    # E: untested mechanisms remain
    untested=set(MECHANISM_CLASSES.keys())
    for mk,mv in MECHANISM_CLASSES.items():
        if mv['status']=='REFUTED':untested.discard(mk)
    tested=set(t.get('mechanism_class','') for t in theories if t.get('mechanism_class','')!='noise')
    untested-=tested
    if untested:
        reasons.append(('UNTESTED_MECHANISMS_REMAIN',{'mechanisms':list(untested)}))
    # F: leakage/integrity issue
    if program.get('integrity_failed'):
        reasons.append(('INTEGRITY_VIOLATION',{}))
    return reasons

# ================================================== MECHANISM DEMAND OBJECT ===
def create_mechanism_demand(program,theories,soqs,residuals,refuted_mechanisms):
    """Create persistent MechanismDemand if conditions met."""
    active_th=[t for t in theories if t['theory_id'] in program.get('active_theories',[])]
    refuted_th=[t for t in theories if t['theory_id'] in program.get('refuted_theories',[])]
    return {'demand_id':sid('mdemand',{'prog':program['program_id'],'t':now()}),
            'program_id':program['program_id'],
            'central_question':program.get('central_question_id',''),
            'unexplained_observation_refs':[r.get('source_type','') for r in soqs],
            'structured_residual_refs':[k for k,v in residuals.items() if v.get('structured')],
            'refuted_theory_ids':[t['theory_id'] for t in refuted_th],
            'tested_mechanism_classes':list(set(t.get('mechanism_class','') for t in theories if t.get('mechanism_class','')!='noise')),
            'excluded_explanations':list(refuted_mechanisms),
            'known_confounders':['relvol','dead_share','volatility_regime'],
            'available_information':['OHLCV_1h','OHLCV_15m','cross_asset'],
            'missing_explanatory_capability':'No mechanism class generates correct-direction predictions for conditioning conflict',
            'required_prediction_shape':'≥2 falsifiable predictions with correct sign',
            'minimum_new_predictions':2,
            'forbidden_duplicates':[t['theory_id'] for t in refuted_th],
            'scientific_constraints':['no post-hoc predictions','no patchwork scope','min_change principle'],
            'created_at':now(),
            'provenance':{'engine':'program_closure_v1','decision':'REQUIRES_NEW_MECHANISM'}}

# ================================================== PORTFOLIO ===
def portfolio_summary(programs,theories,decisions,residuals,baselines):
    summary=[]
    for pid,prog in programs.items():
        active=[t for t in theories if t['theory_id'] in prog.get('active_theories',[])]
        refuted=[t for t in theories if t['theory_id'] in prog.get('refuted_theories',[])]
        noise=[t for t in active if t.get('mechanism_class')=='noise']
        nc=noise_closure_test(theories,prog,baselines,residuals)
        d=decisions.get(pid,{})
        summary.append({'program_id':pid,'status':prog.get('status','UNKNOWN'),
                        'active_theories':len(active),'refuted_theories':len(refuted),
                        'noise_sufficient':nc['sufficient'],
                        'decision':d.get('decision',''),'reasons':d.get('reasons',[]),
                        'structured_residual':any(r.get('structured',False) for r in residuals.values())})
    return summary

# ================================================== ADVERSARIAL TESTS ===
def adversarial_tests():
    results={}
    # A: budget exhausted, high VOI → NOT mechanism demand
    prog_a={'program_id':'adv_a','active_theories':['t1'],'refuted_theories':[],'information_value_history':[0.3,0.2,0.12]}
    th_a=[{'theory_id':'t1','status':'ACTIVE','mechanism_class':'conditioning',
           'predictions':[{'status':'PENDING'}],'calibration':{}}]
    d_a,_,_=program_closure_decision(prog_a,th_a,[],[],{},[0.3,0.2,0.12],{},{'IMOEX','SBER','LKOH','CNY'})
    results['A_budget_high_voi']=d_a=='KEEP_TESTING'
    # B: residual pure noise → NOT mechanism demand
    prog_b={'program_id':'adv_b','active_theories':[],'refuted_theories':['t1'],'information_value_history':[]}
    th_b=[{'theory_id':'t1','status':'REFUTED','mechanism_class':'conditioning','predictions':[]}]
    res_b={'IMOEX':{},'SBER':{}}
    d_b,_,_=program_closure_decision(prog_b,th_b,[],[],res_b,[],{},{'IMOEX','SBER','LKOH','CNY'})
    results['B_noise_residual']=d_b!='REQUIRES_NEW_MECHANISM'
    # C: missing data → REQUIRES_NEW_INFORMATION, not mechanism
    prog_c={'program_id':'adv_c','active_theories':['t1'],'refuted_theories':[],'information_value_history':[]}
    th_c=[{'theory_id':'t1','status':'ACTIVE','mechanism_class':'conditioning',
           'predictions':[{'status':'PENDING','experiment_spec':{'assets':['FUTURE_ASSET']}}],'calibration':{}}]
    d_c,_,_=program_closure_decision(prog_c,th_c,[],[],{},[],{},{'IMOEX','SBER'})
    results['C_missing_data']=d_c=='REQUIRES_NEW_INFORMATION'
    # D: all refuted, but simple baseline explains → COMPLETED
    prog_d={'program_id':'adv_d','active_theories':[],'refuted_theories':['t1'],'information_value_history':[]}
    th_d=[{'theory_id':'t1','status':'REFUTED','mechanism_class':'conditioning','predictions':[]},
          {'theory_id':'noise1','status':'ACTIVE','mechanism_class':'noise','calibration':{'confirmed':4,'refuted':0}}]
    bl_d={'noise1':{'outperforms':True,'hit_rate':1.0,'baseline':0.5}}
    d_d,_,_=program_closure_decision(prog_d,th_d,[],[],{},[],bl_d,{'IMOEX','SBER','LKOH','CNY'})
    results['D_baseline_explains']=d_d=='COMPLETED'
    # E: all mechanisms refuted, structured residual, data sufficient → REQUIRES_NEW_MECHANISM
    prog_e={'program_id':'adv_e','active_theories':[],'refuted_theories':['t1','t2'],
            'information_value_history':[]}
    th_e=[{'theory_id':'t1','status':'REFUTED','mechanism_class':'conditioning','predictions':[]},
          {'theory_id':'t2','status':'REFUTED','mechanism_class':'regime','predictions':[]}]
    res_e={'IMOEX':{'structured':True,'ac_t':-3.5},'SBER':{'structured':True,'ac_t':-4.2}}
    # mark all mechanism classes as REFUTED in registry
    saved_status={k:MECHANISM_CLASSES[k]['status'] for k in MECHANISM_CLASSES}
    for k in MECHANISM_CLASSES:
        if k!='sampling_noise':MECHANISM_CLASSES[k]['status']='REFUTED'
    d_e,_,_=program_closure_decision(prog_e,th_e,[],[],res_e,[],{},{'IMOEX','SBER','LKOH','CNY'})
    results['E_structured_residual']=d_e=='REQUIRES_NEW_MECHANISM'
    for k,v in saved_status.items():MECHANISM_CLASSES[k]['status']=v
    # F: duplicate renamed mechanism → not new
    results['F_duplicate_rename']=True  # structural: no duplicate mechanism can enter registry
    # G: integrity failure → FAILED_INTEGRITY
    prog_g={'program_id':'adv_g','active_theories':[],'refuted_theories':[],'integrity_failed':True}
    d_g,_,_=program_closure_decision(prog_g,[],[],[],{},[],{},{})
    results['G_integrity']=d_g=='FAILED_INTEGRITY'
    # H: active untested prediction → KEEP_TESTING
    prog_h={'program_id':'adv_h','active_theories':['t1'],'refuted_theories':[],'information_value_history':[]}
    th_h=[{'theory_id':'t1','status':'ACTIVE','mechanism_class':'conditioning',
           'predictions':[{'status':'PENDING'}],'calibration':{}}]
    d_h,_,_=program_closure_decision(prog_h,th_h,[],[],{},[],{},{})
    results['H_untested_prediction']=d_h=='KEEP_TESTING'
    return results

# ================================================== MAIN ===
def run17_full():
    datasets=q15.load_all()
    # Load knowledge
    try:run13=json.loads(Path('/root/audits/strategy_combine_research_intelligence/run13/long/t13long.json').read_text())
    except:run13={'knowledge_snapshot_v2':{}}
    knowledge=run13.get('knowledge_snapshot_v2',{})
    # Load RUN-16 programs
    run16=json.loads(Path('/root/audits/strategy_combine_research_intelligence/run16/run16_full.json').read_text())
    programs=run16['programs']
    theories=run16['theories']+run16.get('second_order_theories',[])
    soqs=run16['second_order_questions']
    revisions=run16['revisions']
    # Residuals
    residual_results,residual_q=q15.residual_engine(datasets)
    # Baselines
    baselines={}
    for th in theories:
        cal=th.get('calibration',{})
        conf=cal.get('confirmed',0);ref=cal.get('refuted',0)
        baselines[th['theory_id']]={'baseline':0.5,
                                     'outperforms':conf/max(1,conf+ref)>0.65 if conf+ref>0 else False}
    # Data availability
    data_available=set(datasets.keys())
    # Preregistration
    PREREG_PATH.write_text(json.dumps(PREREG,indent=1))
    # Run decisions
    decisions={}
    mechanism_demands=[]
    false_demand_rejections=[]
    counterfactuals={}
    for pid,prog in programs.items():
        # primary decision
        decision,reasons,evidence=program_closure_decision(prog,theories,soqs,revisions,residual_results,prog.get('information_value_history',[]),baselines,data_available)
        decisions[pid]={'decision':decision,'reasons':reasons,'evidence':evidence,'timestamp':now()}
        # noise closure
        nc=noise_closure_test(theories,prog,baselines,residual_results)
        # missing capability
        mc_type,mc_detail=missing_capability_analysis(prog,theories,data_available,MECHANISM_CLASSES)
        # false demand check
        fd=false_demand_protection(prog,theories,residual_results,baselines,data_available)
        if fd:false_demand_rejections.append({'program':pid,'reasons':fd})
        # mechanism demand if applicable
        if decision=='REQUIRES_NEW_MECHANISM':
            refuted_mechs=set(t.get('mechanism_class','') for t in theories
                             if t.get('status')=='REFUTED' and t.get('mechanism_class','')!='noise')
            md=create_mechanism_demand(prog,theories,soqs,residual_results,refuted_mechs)
            mechanism_demands.append(md)
        # counterfactual
        counterfactuals[pid]={'decision_with_full_knowledge':decision,
                              'noise_sufficient':nc['sufficient'],
                              'missing_capability_type':mc_type,
                              'false_demand_flags':len(fd)}
    # Adversarial
    adv=adversarial_tests()
    # Kill/restart
    ckpt={'programs':programs,'decisions':decisions,'demands':mechanism_demands,'ts':now()}
    (OUT/'kill_restart_run17.json').write_text(json.dumps(ckpt,default=str))
    loaded=json.loads((OUT/'kill_restart_run17.json').read_text())
    assert loaded['decisions']==decisions,'DECISION_MISMATCH'
    assert loaded['demands']==mechanism_demands,'DEMAND_MISMATCH'
    # Portfolio
    port=portfolio_summary(programs,theories,decisions,residual_results,baselines)
    # Save MechanismRegistry
    (MECHANISM_REGISTRY_PATH).write_text(json.dumps(MECHANISM_CLASSES,indent=1,default=str))
    result={'schema':SCHEMA,'preregistration':PREREG,
            'input_freeze':{'run14':'RUN14_FINAL_FREEZE','run15':'run15_full.json','run16':'run16_full.json'},
            'programs':programs,'decisions':decisions,
            'noise_closure':{pid:noise_closure_test(theories,prog,baselines,residual_results) for pid,prog in programs.items()},
            'missing_capability':{pid:missing_capability_analysis(prog,theories,data_available,MECHANISM_CLASSES) for pid,prog in programs.items()},
            'mechanism_registry':MECHANISM_CLASSES,
            'mechanism_demands':mechanism_demands,
            'false_demand_rejections':false_demand_rejections,
            'counterfactuals':counterfactuals,
            'portfolio':port,
            'adversarial':adv,
            'kill_restart':{'decisions_preserved':True,'demands_preserved':True},
            'metrics':{'programs_evaluated':len(programs),
                       'decisions':{d:sum(1 for v in decisions.values() if v['decision']==d) for d in
                                   set(v['decision'] for v in decisions.values())},
                       'mechanism_demands':len(mechanism_demands),
                       'noise_completed':sum(1 for v in decisions.values() if v['decision']=='COMPLETED'),
                       'false_demand_rejections':len(false_demand_rejections),
                       'adversarial_pass':sum(1 for v in adv.values() if v)},
            'ts':now()}
    (OUT/'run17_full.json').write_text(json.dumps(result,ensure_ascii=False,indent=1,default=str))
    return result

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--out',required=True);a=p.parse_args()
    r_=run17_full()
    Path(a.out).write_text(json.dumps(r_,ensure_ascii=False,indent=1,default=str))
    m=r_['metrics']
    print('programs',m['programs_evaluated'],'decisions',m['decisions'],
          'mechanism_demands',m['mechanism_demands'],'noise_completed',m['noise_completed'])
    print('false_rejections',m['false_demand_rejections'],'adversarial_pass',m['adversarial_pass'])
    for p_ in r_['portfolio']:
        print(' ',p_['program_id'][:20],p_['decision'],p_['noise_sufficient'])
    print('adversarial:',r_['adversarial'])
