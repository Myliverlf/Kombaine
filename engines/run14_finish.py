#!/usr/bin/env python3
"""RUN-14 finisher: freeze V1, cross-timeframe layer, V1-vs-V2 tournament,
fake-prior adversarial (PRIOR_CHALLENGE). Research only, paper only.

Nothing here touches broker, pool, or live config. Final 60d of every dataset
stays hidden (run11.train_only). 15m decisions use only fully closed 1h bars;
1h decisions use only 15m bars that close at/before the 1h decision instant.
"""
from __future__ import annotations
import json, hashlib
import numpy as np
import pandas as pd
from pathlib import Path
import sys
SC=Path('/root/prop-desk/strategy_combine'); FL=Path('/root/prop-desk/futures_lab')
sys.path[:0]=[str(SC),str(FL),str(SC/'engines')]
import research_intelligence_run11 as r
import adaptive_scientific_director_run12 as a
import transfer_run13 as t13
import research_space_v2 as v2

SCHEMA='run14-finish-v1'
OUT=Path('/root/audits/strategy_combine_research_intelligence/run14')
OUT.mkdir(parents=True,exist_ok=True)

# ------------------------------------------------------------ V1 FREEZE ----
def freeze_v1():
    run12=json.loads((Path('/root/audits/strategy_combine_research_intelligence/run12/isolated/tournament.json')).read_text())
    run13=json.loads((Path('/root/audits/strategy_combine_research_intelligence/run13/long/t13long.json')).read_text())
    ad=run12['directors']['adaptive-scientific-v1']; st=ad['state']
    snap=run13.get('knowledge_snapshot_v2',{})
    payload={
      'freeze_id':'RESEARCH_SPACE_V1_FREEZE','frozen_at':r.now(),
      'rule':'immutable; V2 extends as separate lineage; nothing may overwrite entries',
      'dsl':{'ops':['raw','zscore','difference','slope','ratio','vol_ratio','cross_return'],
             'window_bounds':[2,120],'lag_bounds':[1,72],'depth_max':3,'nodes_max':7},
      'scoring_coefficients':a.SCORING,
      'v1_space':v2.SPACE_V1,'v1_space_size':len(v2.SPACE_V1),
      'run12':{'graph_nodes':len(st['graph']['nodes']),'graph_edges':len(st['graph']['edges']),
               'failed_contexts':st['failed_contexts'],'surviving_families':st['surviving_families'],
               'negative_family_keys':st.get('explored_families',[])},
      'run13_knowledge_v2':snap,
      'transfer_hierarchy':{'GLOBAL':1.0,'ASSET_REGIME':.95,'ASSET':.85,'REGIME':.45,'ASSET_CLASS':.25},
      'hard_blocks':['CLONE','LEAKAGE','DATA_INTEGRITY_FAIL'],
      'datasets':{'hash':run12['dataset_hash'],'hidden_cutoff':run12['hidden_cutoff']},
    }
    blob=json.dumps(payload,ensure_ascii=False,indent=1,default=str)
    p=OUT/'RESEARCH_SPACE_V1_FREEZE.json'; p.write_text(blob)
    return {'path':str(p),'sha256':hashlib.sha256(blob.encode()).hexdigest()}

# ------------------------------------------------------ raw info audit ----
def info_audit():
    h1=sorted(p.name for p in (r.DATA).glob('*_365d_1h_continuous.csv'))
    m15=sorted(p.name for p in (r.DATA).glob('*15m*.csv'))
    return {'AVAILABLE_NOW':{'ohlcv_1h':h1,'ohlcv_15m':m15,
        'derived':'price structure, volume structure, volatility structure, cross-asset lagged, intraday hour, causal regimes, regime transitions'},
     'AVAILABLE_BUT_NOT_INGESTED':{'order_book':'not present in CSV artifacts','not_needed_for_RUN14':True},
     'REQUIRES_NEW_DATA':['options term structure (forts)','tick-level order flow','foreign futures (brent/USD rate intraday)'],
     'UNAVAILABLE':['true market depth history','per-tick trade tape']}

# ------------------------------------------------- cross-timeframe layer ---
XTF_GATE=('vol_hi','slope_pos')  # preregistered gate set

def load15(ticker):
    p=next(r.DATA.glob(f'{ticker}*15m*.csv'))
    d=pd.read_csv(p); d['time']=pd.to_datetime(d['time']); return d.sort_values('time').reset_index(drop=True),p

def closed_1h(d15):
    """Only hours with all 4 bars closed become 1h bars (alignment contract)."""
    d=d15.copy(); d['h']=d.time.dt.floor('1h')
    g=d.groupby('h')
    out=pd.DataFrame({'time':g.size().index,'open':g.open.first().values,'high':g.high.max().values,
        'low':g.low.min().values,'close':g.close.last().values,'volume':g.volume.sum().values,'n':g.size().values})
    out=out[out.n==4].drop(columns='n').reset_index(drop=True)
    # path efficiency inside the hour from 15m closes (new intrabar info)
    cc=d.close.values; hh=d.h.values
    pe=[]
    for h_ in out.time.values:
        seg=cc[hh==h_]
        denom=np.abs(np.diff(seg)).sum()
        pe.append(abs(seg[-1]-seg[0])/denom if denom>0 else np.nan)
    out['intra_path']=pd.Series(pe,index=out.index)
    return out

def h1_vol_hi(h1):
    """Causal 1h high-vol state: expanding quantile, shift(1) → value for hour H known at H+1h."""
    rr=np.log(h1.close/h1.close.shift()); vv=rr.rolling(24).std()
    thr=vv.expanding(min_periods=120).quantile(.66).shift(1)
    return (vv>=thr).astype(float)

def xtf_cond_15m(d15,h1,src,gate):
    """15m decision uses ONLY the previous fully-closed 1h bar (contract)."""
    ra=v2._ret(d15); s=v2.eval_v2(d15,src,None)
    if gate=='vol_hi': g=h1_vol_hi(h1)
    else:
        rr=np.log(h1.close/h1.close.shift()); g=(rr.rolling(24).sum()>0).astype(float)
    g=g.shift(1)  # decision in hour H uses hour H-1 state
    gm=pd.Series(g.values,index=h1.time.dt.floor('1h'))
    key=d15.time.dt.floor('1h')-pd.Timedelta('1h')
    gv=key.map(gm).astype(float)
    return s*gv

def xtf_path_1h(h1):
    """1h decision uses 15m bars of its own hour (all closed exactly at decision)."""
    return pd.Series(h1.intra_path.values,index=h1.index)

def xtf_families():
    return {
     'xtf.15m_mr_gated_1hvol':{'src':{'op':'zscore','x':{'op':'raw','name':'ret1'},'window':48},'gate':'vol_hi','tf':'15m',
       'mechanism':'15m mean reversion GIVEN previous 1h high-vol regime','info_source':'cross_timeframe.vol_gate',
       'why_not':'single-timeframe families cannot express other-TF gating','expected':'gated MR stronger than unconditional',
       'falsification':{'min_abs_t':1.0,'half_stability':True},'leakage':'prev closed 1h bar only'},
     'xtf.15m_mom_gated_1htrend':{'src':{'op':'slope','x':{'op':'raw','name':'ret1'},'window':24},'gate':'slope_pos','tf':'15m',
       'mechanism':'15m momentum GIVEN previous 1h trend direction','info_source':'cross_timeframe.trend_gate',
       'why_not':'no other-TF context in V1/V2','expected':'trend-gated mom differs from ungated',
       'falsification':{'min_abs_t':1.0,'half_stability':True},'leakage':'prev closed 1h bar only'},
     'xtf.1h_intra_path':{'tf':'1h→15m','mechanism':'1h bar internal path (from 15m closes) predicts hour-close drift',
       'info_source':'cross_timeframe.intrabar_path','why_not':'1h OHLC hides intrabar path','expected':'efficient hours drift / inefficient revert',
       'falsification':{'min_abs_t':1.0,'half_stability':True},'leakage':'uses own-hour 15m bars, all closed at decision'},
    }

def truncate_recompute(d15,h1=None,n_checks=6):
    """Strong causal test: truncate 15m at time T and recompute features decided at T.
    1h-from-15m: feature for hour H must be identical when 15m truncated at H+45m and
    ABSENT (NaN) when truncated at H+30m (bar not closed). 15m-from-1h: identical
    when truncated at t (uses only closed 1h bars)."""
    if h1 is None:h1=closed_1h(d15)
    res={'1h_from_15m_identical':True,'1h_from_15m_not_early':True,'15m_from_1h_identical':True,'checks':[]}
    fam=xtf_families(); src={'op':'zscore','x':{'op':'raw','name':'ret1'},'window':48}
    for t in np.linspace(int(len(d15)*0.5),int(len(d15)*0.98),n_checks).astype(int):
        t=int(t); cut=d15.time.iloc[t]; H=cut.floor('1h')
        sub15=d15[d15.time<=cut].reset_index(drop=True)
        sub1=closed_1h(sub15)
        if len(sub1)<2: continue
        full1=closed_1h(d15)
        f_full=v2.eval_v2(full1,src,None)
        f_sub=v2.eval_v2(sub1,src,None)
        # hours strictly before the current partial hour must match
        ok=True
        hh=sub1.time.iloc[-1]
        if hh in full1.time.values:
            a=float(f_sub.iloc[-1]) if f_sub.iloc[-1]==f_sub.iloc[-1] else None
            b=float(f_full[full1.time==hh].iloc[0]) if hh in full1.time.values else None
            if a is None and b is None: ok=True
            elif a is None or b is None: ok=False
            else: ok=abs(a-b)<1e-9
        # the CURRENT (possibly still open) hour must NOT be treated as closed when truncated mid-hour
        n_bars_hour=int((sub15.h if 'h' in sub15 else sub15.time.dt.floor('1h')).iloc[-1:].size)
        bars_in_last=int((sub15.time.dt.floor('1h')==H).sum())
        complete=(bars_in_last==4)
        res['checks'].append({'t_index':t,'hour':str(H),'complete_hour':complete,'last_feature_equal':ok})
        if not ok:res['1h_from_15m_identical']=False
        if (not complete) and hh in full1.time.values and len(sub1)>0:
            # truncated mid-hour: incomplete hour must be excluded by contract
            if sub1.time.iloc[-1]==H: res['1h_from_15m_not_early']=False
        # 15m feature uses previous closed 1h bar: recompute after truncation
        fa=xtf_cond_15m(sub15,sub1,src,'vol_hi'); fb=xtf_cond_15m(d15,full1,src,'vol_hi')
        va=float(fa.iloc[t]) if t<len(fa) and fa.iloc[t]==fa.iloc[t] else None
        vb=float(fb.iloc[t]) if t<len(fb) and fb.iloc[t]==fb.iloc[t] else None
        if va is not None and vb is not None and abs(va-vb)>1e-9:res['15m_from_1h_identical']=False
    return res

def mutation_test_xtf(d15):
    """Mutating FUTURE 15m bars must not change PAST xtf features (both directions)."""
    base15=d15.copy(); base1=closed_1h(base15)
    k=int(len(d15)*0.7)
    mut=d15.copy(); mut.loc[mut.index>=k,'close']=mut.loc[mut.index>=k,'close']*1.05+7.0
    mut.loc[mut.index>=k,'volume']=mut.loc[mut.index>=k,'volume']*3+11
    mut1=closed_1h(mut)
    src={'op':'zscore','x':{'op':'raw','name':'ret1'},'window':48}
    f_base=v2.eval_v2(base1,src,None); f_mut=v2.eval_v2(mut1,src,None)
    # Contract semantics: a 1h bar closes at its last 15m bar (H+45m). Only bars
    # fully closed BEFORE the mutation point are "past"; the hour containing the
    # mutation point closes after it and legitimately depends on mutated bars.
    idx=base1.index[(base1.time+pd.Timedelta(minutes=45))<base15.time.iloc[k]]
    eq15to1h=bool(np.allclose(f_base.loc[idx].fillna(-9),f_mut.loc[idx].fillna(-9)))
    g_base=xtf_cond_15m(base15,base1,src,'vol_hi'); g_mut=xtf_cond_15m(mut,mut1,src,'vol_hi')
    i15=d15.index[d15.time<base15.time.iloc[k]]
    eq1to15=bool(np.allclose(g_base.loc[i15].fillna(-9),g_mut.loc[i15].fillna(-9)))
    return {'future_15m_mutation_keeps_past_1h':eq15to1h,'future_15m_mutation_keeps_past_15m_feature':eq1to15}

# ------------------------------------------- V1 vs V2 equal-budget ----
BRANCH_OF={'price':'trend','vol':'volume_liquidity','cross':'cross_asset','intraday':'intraday_microstructure',
           'regime':'regimes','cond':'regimes','xtf':'regimes'}

def space_branch(key):
    if key.startswith('v1.'):return key.split('.')[1]
    return BRANCH_OF.get(key.split('.')[0],'regimes')

def info_source_of(key):
    if key.startswith('v1.'):return 'v1'
    fam=v2.FAMILIES_V2.get(key) or xtf_families().get(key)
    return fam['info_source'] if fam else '?'

def adaptive_over_space(space,state,round_no,budget):
    """run12-style scoring over an arbitrary family space (coefficients frozen)."""
    ranked=[]
    for key,fam in space.items():
        fk=key  # family key IS the canonical identity here (preregistered)
        if fk in state['explored_families']:continue
        b=space_branch(key); br=state['branches'][b]
        uncertainty=1/(1+br['experiments_used']); branch_iv=1-br['saturation']
        neg=max([a.family_similarity(fam['expr'] if 'expr' in fam else key,x['family_key']) for x in state['failed_contexts']] or [0])
        indep=1-neg
        comp=3/7  # xtf families are simple by design; V1/V2 standardize above threshold
        score=(0.30*uncertainty+0.20*indep+0.15*branch_iv+0.15*(1.0 if key not in state.get('tested_info_sources',[]) else .3)
               +0.20*indep-0.35*neg-0.03*comp-0.04*comp)
        ranked.append((score,b,key,fam,{'policy':'adaptive','score':round(score,4)}))
    ranked.sort(key=lambda x:(-x[0],x[2]))
    picked=[];seen=set()
    for sc,b,key,fam,m in ranked:
        if b not in seen:picked.append((b,key,fam,m));seen.add(b)
        if len(picked)>=budget:break
    return picked

def execute_space_round(state,d,e,ticker,space,rnd,budget,stat_only=False):
    """V2 families live in the V2 DSL — evaluation goes through eval_v2 with a
    V2-aware observe/economic_test (same falsification thresholds as V1 judge);
    V1 families keep the canonical run11 judge bit-for-bit."""
    plan=adaptive_over_space(space,state,rnd,budget);rows=[]
    for b,key,fam,meta in plan:
        expr=fam.get('expr')
        is_v1=key in v2.SPACE_V1
        if is_v1:
            obs=r.observe(d,e,expr)
            item=r.PlanItem(b,expr,r.feature_id(expr),r.sid('hyp',key),key,rnd,
                            {'evaluations':1,'complexity':r.complexity(expr),'cpu_units':r.complexity(expr)},fam['falsification'])
            status,reasons,metrics=r.economic_test(d,ticker,item,obs,e)
            obs['novelty']=dict(obs['novelty'],info_novel=obs['novelty']['novel'])  # common key
        else:
            nr=np.log(d.close.shift(-1)/d.close)
            f=v2.eval_v2(d,expr,e)
            z=pd.DataFrame({'f':f,'r':nr}).replace([np.inf,-np.inf],np.nan).dropna()
            eff=_quartile_effect(f,nr)
            stable=_half_stability(f,nr);n=len(z)
            onov=v2.orthogonality(d,e,expr,nr)
            obs={'observation_id':r.sid('obs',key),'feature_id':r.sid('feat',key),
                 'effect':eff,'sample_size':n,'stability':stable,
                 'novelty':{**onov,'novel':onov['info_novel']},'conditions':{'space':'V2'}}
            item=r.PlanItem(b,expr,v2.fid(expr),r.sid('hyp',key),key,rnd,
                            {'evaluations':1,'complexity':v2.complexity_v2(expr),'cpu_units':v2.complexity_v2(expr)},fam['falsification'])
            # V2 judge: same thresholds as V1 (min_n 80 / t 1.0 / half-stability), novelty via orthogonality gate
            reasons=[]
            if obs['sample_size']<80:reasons.append('insufficient_n')
            if not obs['stability']:reasons.append('observation_unstable')
            if abs(obs['effect']['tstat'])<1:reasons.append('tstat_floor')
            if not obs['novelty']['info_novel']:reasons.append('info_not_novel')
            status='FAIL' if reasons else 'PASS_RESEARCH_ONLY'
            metrics={}
        rv=r.research_value(status,obs,item,reasons) if obs is not None else 0.0
        info=rv+(0.25 if status!='FAIL' else 0.0)
        rows.append({'family_key':key,'branch':b,'ticker':ticker,'status':status,'reject_reasons':reasons,
                     'metrics':metrics,'research_value':rv,'information_gain':round(info,4),'round':rnd,
                     'info_source':info_source_of(key),'budget_spent':item.estimated_cost,'ts':r.now()})
        state['explored_families'].append(key);state.setdefault('tested_info_sources',set()).add(info_source_of(key))
        br=state['branches'][b];br['experiments_used']+=1;br['compute_cost']+=item.estimated_cost['cpu_units'];br['information_gain']+=info
        br['saturation']=round(min(1,br['experiments_used']/3),3)
        if status=='FAIL':br['hypotheses_failed']+=1;state['failed_contexts'].append({'family_key':key,'branch':b,'asset':ticker,'reason':'FAIL','experiment_id':key})
        else:br['hypotheses_survived']+=1;state['surviving_families'].append(key)
    return rows

def tournament_v1_v2(ticker='IMOEX',rounds=3,budget=7):
    base,p=r.load(ticker);ext,_=r.load('CNY');d,cut=r.train_only(base);e,_=r.train_only(ext);dh=r.file_hash(p)
    res={'schema':SCHEMA,'ticker':ticker,'dataset_hash':dh,'hidden_cutoff':str(cut),
         'budget_per_side':rounds*budget,'rounds':rounds,'sides':{}}
    for name,space in (('V1_SPACE_21',v2.SPACE_V1),('V2_SPACE_37',v2.SPACE_V2)):
        st={'label':name,'branches':{b:{'experiments_used':0,'saturation':0.0,'hypotheses_failed':0,'hypotheses_survived':0,'information_gain':0.0,'compute_cost':0} for b in a.BRANCHES},
            'explored_families':[],'failed_contexts':[],'surviving_families':[],'tested_info_sources':set(),'events':[]}
        rows=[]
        for rnd in range(1,rounds+1):rows+=execute_space_round(st,d,e,ticker,space,rnd,budget)
        res['sides'][name]={'experiments':len(rows),'unique_families':len({x['family_key'] for x in rows}),
            'info_sources_touched':len(st['tested_info_sources']),'survivors':sum(x['status']!='FAIL' for x in rows),
            'information_gain':round(sum(x['information_gain'] for x in rows),3),
            'info_gain_per_cpu':round(sum(x['information_gain'] for x in rows)/max(1,sum(x['budget_spent']['cpu_units'] for x in rows)),4),
            'rows':rows}
    return res

# ------------------------------------------------ fake prior challenge ----
def structural_comparison(d,e,expr_a,expr_b):
    fa=v2.eval_v2(d,expr_a,e);fb=v2.eval_v2(d,expr_b,e)
    z=pd.concat([fa,fb],axis=1).replace([np.inf,-np.inf],np.nan).dropna()
    if len(z)<100:return {'comparable':False}
    p=abs(float(z.iloc[:,0].corr(z.iloc[:,1])));s=abs(float(z.iloc[:,0].corr(z.iloc[:,1],method='spearman')))
    return {'comparable':True,'pearson':round(p,4),'spearman':round(s,4),
            'same_formula':r.canon(expr_a)==r.canon(expr_b)}

def prior_challenge(knowledge,family_key,claimed_clone_expr,candidate_expr,d,e):
    """PnL can NEVER overturn CLONE/LEAKAGE. Only structural evidence may."""
    cmp_=structural_comparison(d,e,candidate_expr,claimed_clone_expr)
    is_clone=bool(cmp_.get('same_formula')) or (cmp_.get('comparable') and max(cmp_['pearson'],cmp_['spearman'])>=.92)
    entry={'family_key':family_key,'challenge_ts':r.now(),'structural':cmp_,'verdict':'CONFIRMED_CLONE' if is_clone else 'NOT_A_CLONE'}
    if is_clone:return entry,'prior_stands'
    knowledge['failed_contexts']=[x for x in knowledge['failed_contexts'] if not (x['family_key']==family_key and x['reason']=='CLONE' and x.get('fake'))]
    knowledge.setdefault('INVALIDATED_KNOWLEDGE',[]).append({**entry,'note':'FAKE_CLONE removed by structural evidence; PnL not involved'})
    return entry,'prior_invalidated'

# ---------------------------------------------------- xtf cold/transfer ---
def xtf_space(ticker):
    sp=dict(v2.SPACE_V1)
    for k,f in xtf_families().items():sp[k]=f
    return sp

def xtf_run(ticker,director_kind,knowledge,rounds=2,budget=6):
    base15,p15=load15(ticker);d15=cut15(base15);e15,_=load15('CNY');e15=cut15(e15)
    h1=closed_1h(d15);dh=r.file_hash(p15)
    st={'label':f'{ticker}-{director_kind}-15m','branches':{b:{'experiments_used':0,'saturation':0.0,'hypotheses_failed':0,'hypotheses_survived':0,'information_gain':0.0,'compute_cost':0} for b in a.BRANCHES},
        'explored_families':[],'failed_contexts':[],'surviving_families':[],'tested_info_sources':set(),'events':[]}
    rows=[]
    for rnd in range(1,rounds+1):
        plan=adaptive_over_space(xtf_space(ticker),st,rnd,budget)
        for b,key,fam,meta in plan:
            expr=fam.get('expr')
            if key.startswith('xtf.'):
                f=xtf_eval(d15,h1,key);nr=np.log(d15.close.shift(-1)/d15.close)
                obs={'observation_id':r.sid('obs',key),'feature_id':r.sid('feat',key),
                     'effect':_quartile_effect(f,nr),'sample_size':int(f.notna().sum()),
                     'stability':_half_stability(f,nr),'novelty':{'novel':True},'conditions':{'xtf':key}}
            else:
                obs=r.observe(d15,e15,expr)
            item=r.PlanItem(b,expr or {'op':'raw','name':'ret1'},r.sid('feat',key),r.sid('hyp',key),key,rnd,{'evaluations':1,'complexity':4,'cpu_units':4},fam.get('falsification',{'min_abs_t':1.0}))
            status='FAIL' if (obs['sample_size']<200 or not obs['stability'] or abs(obs['effect']['tstat'])<1) else 'PASS_RESEARCH_ONLY'
            reasons=[] if status!='FAIL' else ['tstat_floor_or_unstable']
            rv=r.research_value(status,obs,item,reasons);info=rv+0.25*(status!='FAIL')
            rows.append({'family_key':key,'branch':b,'ticker':ticker,'tf':'15m','status':status,'reject_reasons':reasons,
                         'tstat':obs['effect']['tstat'],'information_gain':round(info,4),'info_source':info_source_of(key),
                         'budget_spent':item.estimated_cost,'ts':r.now()})
            st['explored_families'].append(key);st['tested_info_sources'].add(info_source_of(key))
            br=st['branches'][b];br['experiments_used']+=1;br['information_gain']+=info;br['saturation']=round(min(1,br['experiments_used']/3),3)
            if status=='FAIL':br['hypotheses_failed']+=1;st['failed_contexts'].append({'family_key':key,'branch':b,'asset':ticker,'timeframe':'15m','reason':'FAIL'})
            else:br['hypotheses_survived']+=1;st['surviving_families'].append(key)
    return {'ticker':ticker,'policy':director_kind,'rows':rows,'state':st}

def xtf_eval(d15,h1,key):
    fam=xtf_families()[key]
    if key=='xtf.1h_intra_path':
        # attach prev-hour closed intra-path to 15m bars (shift by one hour: contract)
        pe=pd.Series(h1.intra_path.values,index=h1.time.dt.floor('1h')).shift(1)
        key15=d15.time.dt.floor('1h')-pd.Timedelta('1h')
        return key15.map(pe)
    s=v2.eval_v2(d15,fam['src'],None)
    h1v=h1_vol_hi(h1) if fam['gate']=='vol_hi' else (np.log(h1.close/h1.close.shift()).rolling(24).sum()>0).astype(float)
    gm=pd.Series(h1v.shift(1).values,index=h1.time.dt.floor('1h'))
    g=d15.time.dt.floor('1h')-pd.Timedelta('1h')
    return s*g.map(gm).astype(float)

def _quartile_effect(f,nr):
    z=pd.DataFrame({'f':f,'r':nr}).replace([np.inf,-np.inf],np.nan).dropna()
    if len(z)<100:return {'mean_difference':0.0,'tstat':0.0,'threshold_q75':0.0}
    qh=z.f.quantile(.75);ql=z.f.quantile(.25);hi=z[z.f>=qh].r;lo=z[z.f<=ql].r
    eff=float(hi.mean()-lo.mean());import math as _m
    se=_m.sqrt(float(hi.var(ddof=1))/len(hi)+float(lo.var(ddof=1))/len(lo)) if len(hi)>1 and len(lo)>1 else float('inf')
    t=eff/se if se and np.isfinite(se) else 0.0
    return {'mean_difference':round(eff,10),'tstat':round(float(t),5),'threshold_q75':round(float(qh),10)}

def _half_stability(f,nr):
    z=pd.DataFrame({'f':f,'r':nr}).replace([np.inf,-np.inf],np.nan).dropna()
    if len(z)<100:return False
    mid=len(z)//2;hs=[]
    for x in (z.iloc[:mid],z.iloc[mid:]):
        qh=x.f.quantile(.75);ql=x.f.quantile(.25)
        hs.append(float(x[x.f>=qh].r.mean()-x[x.f<=ql].r.mean()))
    eff=float(z[z.f>=z.f.quantile(.75)].r.mean()-z[z.f<=z.f.quantile(.25)].r.mean())
    return bool(eff and np.sign(hs[0])==np.sign(hs[1])==np.sign(eff))

def cut15(d):
    cut=d.time.max()-pd.Timedelta(days=r.HOLDOUT_DAYS);return d[d.time<cut].reset_index(drop=True)

def xtf_transfer_compare(rounds=2,budget=6,ckpt=None):
    frozen=freeze_v1()['path']
    fz=json.loads(Path(frozen).read_text())
    legacy=[]
    for x in fz['run12']['failed_contexts']:
        legacy.append({**x,'family_key':x['family_key'],'timeframe':'1h'})
    out={'schema':SCHEMA,'timeframe_transfer':{},'priors_rule':'same asset+family across timeframe: conf=0.85*0.6=0.51 soft; CLONE stays GLOBAL hard'}
    per={}
    for ticker in ('SBER','LKOH','CNY'):
        cold=xtf_run(ticker,'cold',None,rounds,budget)
        tr=xtf_run(ticker,'transfer',legacy,rounds,budget)
        cold_fks={x['family_key']:x for x in cold['rows']};tr_fks={x['family_key'] for x in tr['rows']}
        harmful=[x for x in tr['rows'] if x['status']!='FAIL' and x['family_key'] in {y['family_key'] for y in tr['rows'][:0]}]
        # precision: transfer skipped families that COLD failed on (useful) vs skipped ones COLD survived (harmful)
        skipped=set(cold_fks)-tr_fks
        useful=[fk for fk in skipped if cold_fks[fk]['status']=='FAIL']
        hurt=[fk for fk in skipped if cold_fks[fk]['status']!='FAIL']
        n=len(useful)+len(hurt)
        per[ticker]={'cold':cold,'transfer':tr,
            'experiments':{'cold':len(cold['rows']),'transfer':len(tr['rows'])},
            'survivors':{'cold':sum(x['status']!='FAIL' for x in cold['rows']),'transfer':sum(x['status']!='FAIL' for x in tr['rows'])},
            'skipped_by_transfer':{'useful':useful,'harmful':hurt},
            'transfer_precision':round(len(useful)/n,3) if n else None,
            'negative_transfer_rate':round(len(hurt)/n,3) if n else None}
    out['timeframe_transfer']=per
    surv_equal=all(v['survivors']['transfer']>=v['survivors']['cold'] for v in per.values())
    prec=[v['transfer_precision'] for v in per.values() if v['transfer_precision'] is not None]
    neg=[v['negative_transfer_rate'] for v in per.values() if v['negative_transfer_rate'] is not None]
    saved=all(v['experiments']['transfer']<v['experiments']['cold'] for v in per.values())
    out['verdict']=('TIMEFRAME_TRANSFER_PROVEN' if surv_equal and prec and min(prec)>=.8 and saved
        else 'TIMEFRAME_TRANSFER_PARTIAL' if prec and min(prec)>=.5 else
        'TIMEFRAME_TRANSFER_NEGATIVE' if neg and max(neg)>.2 else 'TIMEFRAME_TRANSFER_NO')
    return out

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--out',required=True);a_=p.parse_args()
    result={}
    result['freeze']=freeze_v1()
    result['info_audit']=info_audit()
    d15,_=load15('IMOEX');d15=cut15(d15)
    result['xtf_contract']=mutation_test_xtf(d15)
    result['xtf_truncate']=truncate_recompute(d15)
    result['tournament_v1_v2']=tournament_v1_v2()
    # fake prior test on real data
    base,pth=r.load('IMOEX');ext,_=r.load('CNY');d,cut=r.train_only(base);e,_=r.train_only(ext)
    fzpath=result['freeze']['path'];fz=json.loads(Path(fzpath).read_text())
    relvol=v2.FAMILIES_V2['vol.rel_volume']['expr']
    fz['failed_contexts']=list(fz['run12']['failed_contexts'])+[{'family_key':r.canon(relvol),'branch':'volume_liquidity','asset':'IMOEX','regime':'global','reason':'CLONE','fake':True,'claimed_clone':r.canon(r.TEMPLATES['volume_liquidity'])}]
    entry,verdict=prior_challenge(fz,r.canon(relvol),r.TEMPLATES['volume_liquidity'],relvol,d,e)
    result['fake_prior']={'entry':entry,'verdict':verdict}
    entry2,verdict2=prior_challenge(fz,r.canon(r.TEMPLATES['intraday_microstructure']),r.TEMPLATES['intraday_microstructure'],r.TEMPLATES['intraday_microstructure'],d,e)
    result['real_clone_control']={'verdict':verdict2,'entry':entry2}
    result['xtf_transfer']=xtf_transfer_compare()
    Path(a_.out).write_text(json.dumps(result,ensure_ascii=False,indent=1,default=str))
    print('freeze sha',result['freeze']['sha256'][:16])
    print('xtf_contract',result['xtf_contract'])
    print('truncate',{k:v for k,v in result['xtf_truncate'].items() if k!='checks'})
    tv=result['tournament_v1_v2']['sides']
    for n,s in tv.items():print(n,'exp',s['experiments'],'srcs',s['info_sources_touched'],'surv',s['survivors'],'IG',s['information_gain'])
    print('fake_prior:',verdict,'real_clone_control:',verdict2)
    print('xtf verdict:',result['xtf_transfer']['verdict'])
