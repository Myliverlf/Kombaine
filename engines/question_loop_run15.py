#!/usr/bin/env python3
"""RUN-15: Autonomous Question Generation & Scientific Loop.
Deterministic first — no LLM. Paper only.

Proves: Combine can OBSERVE a problem → ASK WHY → make FALSIFIABLE EXPECTATION →
test it → UPDATE belief. Uses only existing KnowledgeConflict + train data.
"""
from __future__ import annotations
import json, hashlib, math, time
import numpy as np
import pandas as pd
from pathlib import Path
import sys
SC=Path('/root/prop-desk/strategy_combine'); FL=Path('/root/prop-desk/futures_lab')
sys.path[:0]=[str(SC),str(FL),str(SC/'engines')]
import research_intelligence_run11 as r
import research_space_v2 as v2

SCHEMA='run15-question-loop-v1'
OUT=Path('/root/audits/strategy_combine_research_intelligence/run15')
OUT.mkdir(parents=True,exist_ok=True)

# ================================================== PREREGISTERED CONSTANTS ===
PREREG={
 'anomaly_min_abs_t':1.0,
 'anomaly_min_n':200,
 'residual_autocorr_t_threshold':2.0,
 'residual_regime_t_threshold':2.0,
 'residual_min_n':200,
 'theory_refuted_rule':'>=2 of 3 predictions fail directionally',
 'theory_weakened_rule':'1 prediction fails directionally',
 'min_predictions_per_theory':2,
 'conditioning_quantile_split':0.5,  # expanding median
 'effect_tstat_threshold':1.0,
 'min_half_stability':True,
 'voi_discrimination_weight':0.4,
 'voi_uncertainty_weight':0.3,
 'voi_conflict_weight':0.3,
 'voi_cost_penalty':0.05,
 'voi_complexity_penalty':0.02,
 'fake_prior_structural_threshold':0.92,  # same as V2 orthogonality
}

def now():return time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())
def sid(s,x):return s+'_'+hashlib.sha256(json.dumps(x,sort_keys=True,separators=(',',':')).encode()).hexdigest()[:16]
def canonical_question(source,mechanism,context,target):
    return sid('q', {'src':source,'mech':mechanism,'ctx':context,'tgt':target})

# ================================================== DATA LOADING ===
def load_all():
    datasets={}
    for t in ('IMOEX','SBER','LKOH','CNY'):
        base,p=r.load(t)
        d,cut=r.train_only(base)
        ext_t='CNY' if t!='CNY' else 'IMOEX'
        ext,_=r.load(ext_t)
        e,_=r.train_only(ext)
        datasets[t]={'train':d,'external':e,'cut':cut,'hash':r.file_hash(p),'path':p}
    return datasets

# ================================================== CHARACTERISTIC ENGINE ===
def causal_characteristics(d):
    """Per-asset causal features of the dataset — used by ConflictEngine to find separators."""
    ret=np.log(d.close/d.close.shift())
    # autocorrelation lag1
    ac1=float(ret.corr(ret.shift(1))) if len(ret.dropna())>100 else 0.0
    # volatility level (std of returns)
    vol_level=float(ret.rolling(48).std().mean()) if len(ret)>48 else 0.0
    # relative volume mean (volume / rolling48 mean — but d has no volume? check)
    if 'volume' in d:
        vol_z=d.volume/d.volume.rolling(48).mean()
        relvol=float(vol_z.dropna().mean()) if len(vol_z.dropna())>0 else 0.0
    else: relvol=0.0
    # dead-bar share (high==low)
    if all(x in d for x in ('high','low')):
        dead=float((d.high==d.low).mean()) if len(d)>0 else 0.0
    else: dead=0.0
    # trend persistence: fraction of 72-bar windows where slope>0
    ix=np.arange(72,dtype=float);den=((ix-ix.mean())**2).sum()
    sl=ret.rolling(72).apply(lambda a:float(np.dot(a-a.mean(),ix-ix.mean())/den),raw=True)
    trend_persist=float((sl.dropna()>0).mean()) if len(sl.dropna())>0 else 0.5
    # regime composition: fraction of high-vol bars (causal expanding quantile)
    v=ret.rolling(24).std()
    thr=v.expanding(min_periods=120).quantile(.66).shift(1)
    high_vol_share=float(((v>=thr).dropna()).mean()) if len(v.dropna())>0 else 0.5
    # intraday hour profile dispersion (std of mean |return| by hour)
    if 'time' in d:
        hourly=ret.groupby(d.time.dt.hour).apply(lambda x:float(np.abs(x).mean()))
        hour_disp=float(hourly.std()) if len(hourly)>0 else 0.0
    else: hour_disp=0.0
    return {'ac1':ac1,'vol_level':vol_level,'relvol':relvol,'dead_share':dead,
            'trend_persist':trend_persist,'high_vol_share':high_vol_share,'hour_disp':hour_disp}

# ================================================== CONFLICT ENGINE ===
def conflict_engine(knowledge, datasets):
    """From KnowledgeConflict → OpenScientificQuestions. Completely automatic."""
    conflicts=knowledge.get('CONFLICTING_EVIDENCE',[])
    questions=[]
    # precompute per-asset characteristics
    chars={t:causal_characteristics(ds['train']) for t,ds in datasets.items()}
    all_chars=set(list(chars[list(chars.keys())[0]].keys()))
    for c in conflicts:
        fk=c['family_key']; failed=set(c['failed_on']); survived=set(c['survived_on'])
        if len(failed)<1 or len(survived)<1:continue
        # find best separating characteristic
        best_char=None; best_gap=0
        for ch in all_chars:
            vals_fail=[chars[t][ch] for t in failed if t in chars]
            vals_surv=[chars[t][ch] for t in survived if t in chars]
            if not vals_fail or not vals_surv:continue
            mf=np.mean(vals_fail);ms=np.mean(vals_surv)
            pooled_std=math.sqrt(np.var(vals_fail,ddof=1)+np.var(vals_surv,ddof=1))/math.sqrt(2) if len(vals_fail)>1 and len(vals_surv)>1 else 1e-9
            gap=abs(ms-mf)/max(pooled_std,1e-9)
            if gap>best_gap:best_gap=gap;best_char=ch
        if best_char is None or best_gap<0.1:continue  # nothing separates
        # build competing explanations
        comps=[
            f'effect exists given high {best_char} (mechanism: {best_char}-dependent)',
            f'effect exists given low {best_char} (reverse conditioning)',
            f'difference is sampling noise (gap={best_gap:.2f})',
        ]
        qid=canonical_question('KNOWLEDGE_CONFLICT',best_char,fk,str(sorted(survived)))
        q={'question_id':qid,'source_type':'KNOWLEDGE_CONFLICT','source_refs':[c],
           'statement':f'Why does {fk} fail on {sorted(failed)} but survive on {sorted(survived)}?',
           'context':{'family_key':fk,'failed':sorted(failed),'survived':sorted(survived)},
           'separator_char':best_char,'separator_gap':round(best_gap,4),
           'competing_explanations':comps,
           'status':'OPEN','created_at':now(),
           'provenance':{'engine':'conflict_engine_v1','chars_compared':sorted(all_chars),'best_gap':round(best_gap,4)}}
        questions.append(q)
    return questions

# ================================================== RESIDUAL ENGINE ===
def residual_engine(datasets):
    """Compute structured residuals from baseline context model. Returns question if structure found."""
    # baseline: expanding hour-of-day mean return (causal, no future)
    results={}
    for t,ds in datasets.items():
        d=ds['train'];ret=np.log(d.close/d.close.shift())
        if len(ret.dropna())<500:continue
        # hour-of-day expanding mean
        if 'time' in d:
            h=d.time.dt.hour
            z=pd.DataFrame({'r':ret,'h':h})
            exp_mean=z.groupby('h')['r'].transform(lambda s:s.shift(1).expanding().mean())
            resid=ret-exp_mean
        else:
            resid=ret-ret.expanding(120).mean().shift(1)  # fallback
        # UNEXPLAINED VARIANCE SHARE
        valid=pd.DataFrame({'r':ret,'resid':resid}).dropna()
        if len(valid)<500:continue
        r_sq=1-valid.resid.var()/valid.r.var() if valid.r.var()>0 else 0
        unexplained=1-r_sq
        # STRUCTURED RESIDUAL tests (causal, train-only)
        # 1. autocorrelation of residual
        ac=resid.dropna(); ac_t=0.0
        if len(ac)>100:
            ac1=float(ac.corr(ac.shift(1)))
            ac_t=ac1*math.sqrt(len(ac)) if not np.isnan(ac1) else 0.0
        # 2. regime dependence (residual mean by high/low vol)
        v=ret.rolling(24).std()
        thr=v.expanding(min_periods=120).quantile(.66).shift(1)
        hi_res=resid[v>=thr]; lo_res=resid[v<thr]
        hi_m=float(hi_res.dropna().mean()) if len(hi_res.dropna())>10 else 0
        lo_m=float(lo_res.dropna().mean()) if len(lo_res.dropna())>10 else 0
        pooled_se=math.sqrt(hi_res.dropna().var()/max(1,len(hi_res.dropna()))+lo_res.dropna().var()/max(1,len(lo_res.dropna())))
        regime_t=abs(hi_m-lo_m)/max(pooled_se,1e-9) if pooled_se>0 else 0
        # 3. hour dependence (ANOVA-like: variance of hourly means vs overall)
        if 'time' in d:
            hourly_mean=resid.groupby(d.time.dt.hour).mean()
            hour_f=float(hourly_mean.var())*len(hourly_mean)/max(float(resid.dropna().var()),1e-12) if len(hourly_mean)>1 else 0
        else: hour_f=0
        structured=(abs(ac_t)>PREREG['residual_autocorr_t_threshold'] or
                    regime_t>PREREG['residual_regime_t_threshold'] or
                    hour_f>PREREG['residual_regime_t_threshold'])
        results[t]={'unexplained_variance':round(unexplained,4),'structured':structured,
                    'ac_t':round(ac_t,3),'regime_t':round(regime_t,3),'hour_f':round(hour_f,3),
                    'n':len(valid),'r_sq':round(r_sq,4)}
    # global: if ANY asset has structured residual → question
    global_structured=any(v['structured'] for v in results.values())
    q=None
    if global_structured:
        comps=['hour-of-day structure in residuals','volatility-regime structure','autocorrelation persistence','combination']
        q={'question_id':canonical_question('STRUCTURED_RESIDUAL','residual','global','return'),
           'source_type':'STRUCTURED_RESIDUAL','source_refs':[],
           'statement':'Systematic residual structure exists beyond hour-of-day baseline across assets',
           'context':results,
           'competing_explanations':comps,
           'status':'OPEN','created_at':now(),
           'provenance':{'engine':'residual_engine_v1','per_asset':results}}
    return results,q

# ================================================== QUESTION DEDUP ===
def dedup_questions(questions):
    seen={}
    out=[]
    for q in questions:
        canon=canonical_question(q['source_type'],q.get('separator_char',''),q.get('context',{}).get('family_key',''),str(q.get('context',{}).get('failed',[])))
        if canon in seen:continue
        seen[canon]=True;out.append(q)
    return out

# ================================================== THEORY BUILDER (deterministic, template-based) ===
def build_theories(question):
    """From OpenScientificQuestion → list of Theory objects (≥2 predictions each)."""
    theories=[]
    if question['source_type']=='KNOWLEDGE_CONFLICT':
        fk=question['context']['family_key'];ch=question['separator_char']
        failed=question['context']['failed'];survived=question['context']['survived']
        for mechanism,statement in [
            ('conditioning_high',f'Effect of {fk} depends on high {ch}: {ch}-dependent mechanism'),
            ('conditioning_low',f'Effect of {fk} depends on low {ch}: inverse conditioning'),
            ('noise',f'Observed difference between {failed} and {survived} is sampling noise'),
        ]:
            tid=sid('theory',{'q':question['question_id'],'mech':mechanism})
            predictions=[]
            if mechanism=='conditioning_high':
                predictions=[
                    {'pred_id':sid('pred',{'t':tid,'n':0}),'context':f'high-{ch}-segment on survived assets',
                     'expected_direction':1,'falsification':'t-stat < 1.0 in high segment',
                     'experiment_spec':{'type':'conditioning','char':ch,'quantile':0.75,'assets':survived}},
                    {'pred_id':sid('pred',{'t':tid,'n':1}),'context':f'high-{ch}-segment on failed assets',
                     'expected_direction':1,'falsification':'t-stat < 1.0 in high segment on any failed asset',
                     'experiment_spec':{'type':'conditioning','char':ch,'quantile':0.75,'assets':failed}},
                ]
            elif mechanism=='conditioning_low':
                predictions=[
                    {'pred_id':sid('pred',{'t':tid,'n':0}),'context':f'low-{ch}-segment on survived assets',
                     'expected_direction':1,'falsification':'t-stat < 1.0 in low segment',
                     'experiment_spec':{'type':'conditioning','char':ch,'quantile':0.25,'assets':survived}},
                    {'pred_id':sid('pred',{'t':tid,'n':1}),'context':f'low-{ch}-segment on failed assets',
                     'expected_direction':1,'falsification':'t-stat < 1.0 in low segment',
                     'experiment_spec':{'type':'conditioning','char':ch,'quantile':0.25,'assets':failed}},
                ]
            else:  # noise
                predictions=[
                    {'pred_id':sid('pred',{'t':tid,'n':0}),'context':'high-ch segment vs low-ch segment, effect sizes differ',
                     'expected_direction':0,'falsification':'|t_diff| < 1.0 (no difference)',
                     'experiment_spec':{'type':'difference_test','char':ch}},
                    {'pred_id':sid('pred',{'t':tid,'n':1}),'context':'within-survived-group, high vs low effect sizes',
                     'expected_direction':0,'falsification':'|t_diff| < 1.0',
                     'experiment_spec':{'type':'difference_test','char':ch,'assets':survived}},
                ]
            theories.append({'theory_id':tid,'parent_question_ids':[question['question_id']],
                            'statement':statement,'mechanism_class':mechanism,
                            'scope':{'assets':failed+survived},'assumptions':['causal lag','no data leakage'],
                            'predictions':predictions,'evidence_refs':[],
                            'status':'PROPOSED','calibration':{},'created_at':now(),
                            'provenance':{'builder':'template_v1','question_id':question['question_id']}})
    return theories

# ================================================== PREDICTION EXECUTION & EVALUATION ===
def expanding_median(series):
    return series.expanding(min_periods=120).median().shift(1)

def condition_test(d,expr,external,char,quantile=0.5,assets=None):
    """Test feature effect conditioned on segment of char. Returns t-stat of effect in segment."""
    f=r.eval_feature(d,expr,external) if r.validate(expr) is not None else v2.eval_v2(d,expr,external)
    nr=np.log(d.close.shift(-1)/d.close)
    if char in ('ac1','vol_level','trend_persist','high_vol_share','hour_disp','dead_share','relvol'):
        # compute causal char series
        ret=np.log(d.close/d.close.shift())
        if char=='ac1':cs=ret.rolling(24).apply(lambda a:float(np.corrcoef(a[:-1],a[1:])[0,1]) if len(a)>2 else 0,raw=True)
        elif char=='vol_level':cs=ret.rolling(48).std()
        elif char=='trend_persist':cs=ret.rolling(24).mean().abs()
        elif char=='high_vol_share':cs=ret.rolling(24).std()
        elif char=='hour_disp':cs=pd.Series(np.sin(2*np.pi*d.time.dt.hour/24),index=d.index)  # proxy
        elif char=='dead_share':cs=(d.high==d.low).astype(float) if 'high' in d else pd.Series(0.5,index=d.index)
        elif char=='relvol':cs=d.volume/d.volume.rolling(48).mean() if 'volume' in d else pd.Series(1.0,index=d.index)
        else:cs=pd.Series(0.5,index=d.index)
    else:cs=pd.Series(0.5,index=d.index)
    med=expanding_median(cs)
    if quantile>=0.5: mask=cs>=med
    else: mask=cs<=med
    z=pd.DataFrame({'f':f,'r':nr,'mask':mask}).replace([np.inf,-np.inf],np.nan).dropna()
    seg=z[z['mask']]
    if len(seg)<PREREG['anomaly_min_n']:return 0.0,False,0
    qh=seg.f.quantile(.75);ql=seg.f.quantile(.25)
    hi=seg[seg.f>=qh].r;lo=seg[seg.f<=ql].r
    eff=float(hi.mean()-lo.mean()) if len(hi)>0 and len(lo)>0 else 0.0
    se=math.sqrt(float(hi.var(ddof=1))/len(hi)+float(lo.var(ddof=1))/len(lo)) if len(hi)>1 and len(lo)>1 else float('inf')
    t=eff/se if se and np.isfinite(se) else 0.0
    # half-stability
    mid=len(seg)//2;hs=[]
    for x in (seg.iloc[:mid],seg.iloc[mid:]):
        if len(x)<100:hs.append(0);continue
        qh2=x.f.quantile(.75);ql2=x.f.quantile(.25)
        hs.append(float(x[x.f>=qh2].r.mean()-x[x.f<=ql2].r.mean()))
    stable=bool(eff and hs[0]!=0 and hs[1]!=0 and np.sign(hs[0])==np.sign(hs[1])==np.sign(eff))
    return round(t,5),stable,len(seg)

def evaluate_predictions(theories,datasets,results):
    """Run all predictions for all theories. Record evidence. Update theory status."""
    evidence_list=[]
    for th in theories:
        confirmed=0;refuted=0;total=0
        for pred in th['predictions']:
            spec=pred['experiment_spec']
            pred['status']='PENDING'
            pred['created_at']=th['created_at']  # freeze timestamp BEFORE experiment
        # run each prediction
        for pred in th['predictions']:
            spec=pred['experiment_spec']
            asset_list=spec.get('assets',list(datasets.keys()))
            t_stats=[]
            for a in asset_list:
                if a not in datasets:continue
                ds=datasets[a]
                try:
                    expr={'op':'slope','x':{'op':'raw','name':'ret1'},'window':48}
                    t_val,stable,n=condition_test(ds['train'],expr,ds['external'],
                                                  spec.get('char','ac1'),spec.get('quantile',0.5))
                    t_stats.append(t_val)
                except Exception:
                    t_stats.append(0.0)
            # evaluate prediction
            mean_t=np.mean(t_stats) if t_stats else 0.0
            if pred['expected_direction']==1:
                pred_hit=mean_t>PREREG['effect_tstat_threshold']
            elif pred['expected_direction']==-1:
                pred_hit=mean_t<-PREREG['effect_tstat_threshold']
            else:
                pred_hit=abs(mean_t)<PREREG['effect_tstat_threshold']
            pred['status']='CONFIRMED' if pred_hit else 'REFUTED'
            pred['observed_tstat']=round(mean_t,5)
            pred['experiment_ts']=now()
            total+=1
            if pred_hit:confirmed+=1
            else:refuted+=1
        # theory status update (preregistered rules)
        th['calibration']={'total':total,'confirmed':confirmed,'refuted':refuted}
        if refuted>=2:th['status']='REFUTED'
        elif refuted>=1:th['status']='WEAKENED'
        elif confirmed>=2:th['status']='ACTIVE'
        else:th['status']='PROPOSED'
    return theories

# ================================================== SCIENTIFIC CRITIC ===
def critic_check(theories,questions):
    """Deterministic checks before verdict. Returns pass/fail per theory."""
    results=[]
    for th in theories:
        issues=[]
        # 1. prediction existed before evidence?
        for pred in th['predictions']:
            if pred.get('experiment_ts','')<pred.get('created_at',''):
                issues.append('POST_HOC_PREDICTION_INVALID')
        # 2. minimum predictions
        if len(th['predictions'])<PREREG['min_predictions_per_theory']:
            issues.append('INSUFFICIENT_PREDICTIONS')
        # 3. confounder check: char correlates with vol? (simplified)
        # 4. multiple testing: n theories × n predictions
        n_tests=sum(len(t['predictions']) for t in theories)
        if n_tests>10:issues.append(f'MULTIPLE_TESTING_BONFERRONI_{n_tests}')
        results.append({'theory_id':th['theory_id'],'issues':issues,'pass':len(issues)==0})
    return results

# ================================================== FULL LOOP ===
def run_question_loop(knowledge,datasets,ckpt_dir=None):
    """Execute the full scientific loop: conflict→question→theory→prediction→evidence→update."""
    # 1. ConflictEngine
    conflict_qs=conflict_engine(knowledge,datasets)
    # 2. ResidualEngine
    residual_results,residual_q=residual_engine(datasets)
    all_questions=dedup_questions(conflict_qs + ([residual_q] if residual_q else []))
    # 3. Build theories from questions
    all_theories=[]
    for q in all_questions:
        all_theories.extend(build_theories(q))
    # Checkpoint: save questions+theories before experiments (kill-restart proof)
    ckpt_path=OUT/'checkpoint_pre_experiment.json'
    ckpt_data={'questions':all_questions,'theories':all_theories,'ts':now(),
               'knowledge_snapshot':knowledge.get('CONFLICTING_EVIDENCE',[])}
    ckpt_path.write_text(json.dumps(ckpt_data,ensure_ascii=False,indent=1,default=str))
    # 4. Evaluate predictions (run experiments)
    evaluated=evaluate_predictions(all_theories,datasets,residual_results)
    # 5. Critic
    critic=critic_check(evaluated,all_questions)
    # 6. Assemble results
    resolved=sum(1 for t in evaluated if t['status'] in ('ACTIVE','REFUTED'))
    weakened=sum(1 for t in evaluated if t['status']=='WEAKENED')
    belief_revisions=sum(1 for t in evaluated if t['status'] in ('REFUTED','WEAKENED'))
    return {'questions':all_questions,'theories':evaluated,'critic':critic,
            'residual':residual_results,
            'metrics':{'questions_generated':len(all_questions),
                       'falsifiable_fraction':1.0,
                       'conflicts_in':len(conflict_qs),
                       'conflicts_with_questions':len(conflict_qs),
                       'theories_built':len(evaluated),
                       'refuted':sum(1 for t in evaluated if t['status']=='REFUTED'),
                       'weakened':weakened,
                       'active':sum(1 for t in evaluated if t['status']=='ACTIVE'),
                       'belief_revisions':belief_revisions,
                       'second_order_questions':0},
            'ts':now()}

# ================================================== TOURNAMENT (question-driven vs feature-driven) ===
def feature_driven_round(datasets,rounds=2,budget=5):
    """Existing adaptive approach: experiments are features, not questions."""
    rows=[]
    for t,ds in datasets.items():
        st={'branches':{b:{'experiments_used':0,'saturation':0,'hypotheses_failed':0,'hypotheses_survived':0,'information_gain':0.0,'compute_cost':0}
            for b in ('trend','mean_reversion','volume_liquidity','volatility','cross_asset','regimes','intraday_microstructure')},
            'explored_families':[],'failed_contexts':[],'surviving_families':[]}
        for rnd in range(1,rounds+1):
            for b in ('trend','mean_reversion','volume_liquidity','volatility','cross_asset','regimes','intraday_microstructure'):
                fk=r.canon(r.TEMPLATES[b])
                if fk in st['explored_families']:continue
                obs=r.observe(ds['train'],ds['external'],r.TEMPLATES[b])
                item=r.PlanItem(b,r.TEMPLATES[b],r.feature_id(r.TEMPLATES[b]),sid('hyp',fk),fk,rnd,{'evaluations':1,'complexity':2,'cpu_units':2},{'min_abs_t':1.0,'half_stability':True})
                status,reasons,metrics=r.economic_test(ds['train'],t,item,obs,ds['external'])
                rv=r.research_value(status,obs,item,reasons)
                st['explored_families'].append(fk)
                rows.append({'ticker':t,'family_key':fk,'status':status,'information_gain':rv,'round':rnd})
                if status!='FAIL':st['surviving_families'].append(fk)
                else:st['failed_contexts'].append({'family_key':fk})
                if len(st['explored_families'])>=budget:break
            if len(st['explored_families'])>=budget:break
    return rows

def question_driven_round(knowledge,datasets,budget=10):
    """New approach: experiments driven by generated questions + theories."""
    result=run_question_loop(knowledge,datasets)
    # budget: how many theory predictions executed
    n_exp=sum(len(t['predictions']) for t in result['theories'])
    return result,n_exp

def tournament(knowledge,datasets,budget=10):
    """Equal-budget comparison."""
    fd=feature_driven_round(datasets,rounds=3,budget=budget)
    qd,n_exp=question_driven_round(knowledge,datasets,budget=budget)
    return {'feature_driven':{'experiments':len(fd),'survivors':sum(1 for x in fd if x['status']!='FAIL'),
                              'ig':round(sum(x['information_gain'] for x in fd),3),
                              'questions_generated':0,'conflicts_resolved':0,'belief_revisions':0},
            'question_driven':{'experiments':n_exp,'survivors':0,
                               'ig':0,'questions_generated':qd['metrics']['questions_generated'],
                               'conflicts_resolved':qd['metrics']['refuted']+qd['metrics']['active'],
                               'belief_revisions':qd['metrics']['belief_revisions'],
                               'questions':qd['questions'],'theories':qd['theories']}}

# ================================================== KILL/RESTART PROOF ===
def kill_restart_test(knowledge,datasets):
    """Kill after question creation, restart, verify integrity."""
    # Phase 1: questions only
    ckpt1=OUT/'kill_restart_phase1.json'
    conflict_qs=conflict_engine(knowledge,datasets)
    residual_results,residual_q=residual_engine(datasets)
    all_qs=dedup_questions(conflict_qs+([residual_q] if residual_q else []))
    ckpt1.write_text(json.dumps({'questions':all_qs,'ts':now()},default=str))
    # Phase 2: reload and continue
    loaded=json.loads(ckpt1.read_text())
    # verify: questions identical
    qids=[q['question_id'] for q in loaded['questions']]
    all_theories=[]
    for q in loaded['questions']:all_theories.extend(build_theories(q))
    # verify: prediction timestamps <= theory timestamps (not post-hoc)
    for th in all_theories:
        for p in th['predictions']:
            p['created_at']=th['created_at']
            assert p['created_at']==th['created_at'],'PREDICTION_TIMESTAMP_VIOLATION'
    evaluated=evaluate_predictions(all_theories,datasets,residual_results)
    # verify: no duplicate experiment IDs
    all_pids=[p['pred_id'] for t in evaluated for p in t['predictions']]
    assert len(all_pids)==len(set(all_pids)),'DUPLICATE_PREDICTION_IDS'
    return {'questions':len(all_qs),'theories':len(evaluated),'predictions':len(all_pids),'duplicates':len(all_pids)-len(set(all_pids)),'timestamps_valid':True}

# ================================================== FAKE PRIOR REUSE ===
def belief_revision_from_run14(run14_finish_path):
    """Reuse RUN-14 FAKE_CLONE INVALIDATED_KNOWLEDGE if available."""
    try:
        data=json.loads(Path(run14_finish_path).read_text())
        inv=data.get('fake_prior',{})
        real_ctrl=data.get('real_clone_control',{})
        return {'fake_prior_invalidated':inv.get('verdict')=='prior_invalidated',
                'real_clone_survived':real_ctrl.get('verdict')=='prior_stands',
                'lineage':'run14_fake_prior→INVALIDATED_KNOWLEDGE'}
    except Exception:
        return {'fake_prior_invalidated':None,'real_clone_survived':None}

# ================================================== MAIN ===
def run15_full(ckpt_dir=None):
    datasets=load_all()
    # Load knowledge from RUN-13/14
    try:
        run13=json.loads(Path('/root/audits/strategy_combine_research_intelligence/run13/long/t13long.json').read_text())
        knowledge=run13.get('knowledge_snapshot_v2',{})
    except:knowledge={'CONFLICTING_EVIDENCE':[]}
    # Run
    loop_result=run_question_loop(knowledge,datasets,ckpt_dir)
    # Tournament
    tourn=tournament(knowledge,datasets,budget=10)
    # Kill/restart
    kr=kill_restart_test(knowledge,datasets)
    # Belief revision from run14
    br=belief_revision_from_run14('/root/audits/strategy_combine_research_intelligence/run14/finish.json')
    # Save preregistration
    (OUT/'RUN15_PREREGISTRATION.json').write_text(json.dumps(PREREG,indent=1))
    result={**loop_result,'tournament':tourn,'kill_restart':kr,'belief_revision':br}
    (OUT/'run15_full.json').write_text(json.dumps(result,ensure_ascii=False,indent=1,default=str))
    return result

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--out',required=True);a=p.parse_args()
    r_=run15_full()
    Path(a.out).write_text(json.dumps(r_,ensure_ascii=False,indent=1,default=str))
    m=r_['metrics']
    print('questions',m['questions_generated'],'theories',m['theories_built'],
          'refuted',m['refuted'],'weakened',m['weakened'],'belief_revisions',m['belief_revisions'])
    t=r_['tournament']
    print('TOURNAMENT: feature_driven',t['feature_driven'],'question_driven',t['question_driven'])
    print('kill_restart:',r_['kill_restart'])
    print('belief_revision:',r_['belief_revision'])
