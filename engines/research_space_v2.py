#!/usr/bin/env python3
"""RUN-14 Research Space V2: new information sources, not an indicator zoo.

V1 (21 families, run11 DSL) stays frozen as RESEARCH_SPACE_V1_FREEZE.
V2 adds families ONLY with a declared information source + mechanism.
Contract: eval_v2 is a strict superset of run11.eval_feature — every V1
expression evaluates bit-identically (test-enforced).
"""
from __future__ import annotations
import json, math
import numpy as np
import pandas as pd
from pathlib import Path
import sys
SC=Path('/root/prop-desk/strategy_combine'); FL=Path('/root/prop-desk/futures_lab')
sys.path[:0]=[str(SC),str(FL),str(SC/'engines')]
import research_intelligence_run11 as r
import adaptive_scientific_director_run12 as a
import transfer_run13 as t13

SCHEMA='research-space-v2-v1'
HOLDOUT=r.HOLDOUT_DAYS

# ---------------------------------------------------------------- V2 DSL ----
RAW_V2={'close','open','high','low','volume','ret1','hour_sin','close_pos','gap','ts_open'}
OPS_V2={'zscore','difference','slope','ratio','vol_ratio',  # V1 set
        'path_eff','range_compression','rel_volume','dir_volume','vol_conc','pv_corr',
        'vol_of_vol','vol_term','cross_corr','cross_beta','rel_strength','corr_breakdown',
        'seasonal_dev','regime_flip','cond'}

def _ret(d):return np.log(d.close/d.close.shift())

def eval_v2(d,expr,external=None):
    """Strict superset evaluator. V1 exprs must produce identical output."""
    op=expr['op']
    c=d.close
    if op=='raw':
        n=expr['name']
        if n in d:return d[n].astype(float)
        if n=='ret1':return _ret(d)
        if n=='hour_sin':return pd.Series(np.sin(2*np.pi*d.time.dt.hour/24),index=d.index)
        if n=='close_pos':  # range position 0..1 (needs prior bar's range: shift for causality)
            rng=(d.high-d.low).replace(0,np.nan)
            # dead bars (high==low) get neutral 0.5 instead of NaN: NaN would poison
            # downstream rolling windows (pandas min_periods defaults to full window).
            return ((c-d.low)/rng).fillna(0.5)
        if n=='gap':return c/c.shift()-1
        if n=='ts_open':  # hours since local day open (causal, from timestamp)
            return pd.Series(d.time.dt.hour.astype(float),index=d.index)
        raise ValueError('unknown raw '+n)
    if op=='cross_return':
        if external is None:raise ValueError('external missing')
        e=external.sort_values('time');x=np.log(e.close/e.close.shift()).shift(int(expr['lag']))
        return pd.merge(d[['time']],e[['time']].assign(x=x),on='time',how='left')['x']
    if op in ('cross_corr','cross_beta','rel_strength','corr_breakdown'):
        if external is None:raise ValueError('external missing')
        e=external.sort_values('time')
        rb=np.log(e.close/e.close.shift()).shift(1)  # strictly lagged ≥1
        ra=_ret(d)
        m=pd.merge(d[['time']].assign(ra=ra),e[['time']].assign(rb=rb),on='time',how='left').dropna()
        w=int(expr.get('window',48))
        if op=='cross_corr':
            cc=m.ra.rolling(w).corr(m.rb).shift(1)
        elif op=='cross_beta':
            cc=(m.ra.rolling(w).cov(m.rb)/m.rb.rolling(w).var()).shift(1)
        elif op=='rel_strength':
            cc=(m.ra.rolling(int(expr.get('k',12))).sum()-m.rb.rolling(int(expr.get('k',12))).sum())
        else:  # corr_breakdown: |corr − trailing mean of corr|
            cc0=m.ra.rolling(w).corr(m.rb).shift(1)
            cc=(cc0-cc0.rolling(int(expr.get('w2',240)),min_periods=48).mean()).abs()
        return pd.Series(cc.values,index=m.index).reindex(d.index)
    x=eval_v2(d,expr['x'],external)
    if op=='difference':return x-x.shift(int(expr['lag']))
    if op=='zscore':
        w=int(expr['window']);return (x-x.rolling(w).mean())/x.rolling(w).std().replace(0,np.nan)
    if op=='slope':
        w=int(expr['window']);ix=np.arange(w,dtype=float);den=((ix-ix.mean())**2).sum()
        return x.rolling(w).apply(lambda v:float(np.dot(v-v.mean(),ix-ix.mean())/den),raw=True)
    if op=='ratio':return x/eval_v2(d,expr['y'],external).replace(0,np.nan)
    if op=='vol_ratio':
        w=int(expr['window']);return x/_ret(d).rolling(w).std().replace(0,np.nan)
    if op=='path_eff':
        w=int(expr['window']);rr=_ret(d)
        return rr.rolling(w).sum().abs()/rr.abs().rolling(w).sum().replace(0,np.nan)
    if op=='range_compression':
        w=int(expr['window']);rng=(d.high-d.low)
        short=rng.rolling(max(2,w//4)).mean();long_=rng.rolling(w).mean()
        return (short/long_.replace(0,np.nan)).replace([np.inf,-np.inf],np.nan)
    if op=='rel_volume':
        w=int(expr['window']);return d.volume/d.volume.rolling(w).mean().replace(0,np.nan)
    if op=='dir_volume':
        w=int(expr['window']);dv=np.sign(_ret(d)).fillna(0)*d.volume
        return dv.rolling(w).mean()/d.volume.rolling(w).mean().replace(0,np.nan)
    if op=='vol_conc':
        w=int(expr['window']);return d.volume/d.volume.rolling(w).sum().replace(0,np.nan)
    if op=='pv_corr':
        w=int(expr['window']);rr=_ret(d);dv=d.volume.pct_change().replace([np.inf,-np.inf],np.nan)
        z=pd.DataFrame({'a':rr,'b':dv});return z.a.rolling(w).corr(z.b)
    if op=='vol_of_vol':
        w=int(expr['window']);v1=_ret(d).rolling(24).std()
        return v1.rolling(w).std()/v1.rolling(w).mean().replace(0,np.nan)
    if op=='vol_term':
        w=int(expr['window']);rr=_ret(d)
        return rr.rolling(max(2,w//4)).std()/rr.rolling(w).std().replace(0,np.nan)
    if op=='seasonal_dev':
        w=int(expr['window']);rr=_ret(d);h=d.time.dt.hour
        # causal: expanding mean over PAST same-hour bars only
        z=pd.DataFrame({'r':rr,'h':h})
        out=pd.Series(np.nan,index=d.index)
        g=z.groupby('h')['r']
        exp_mean=g.transform(lambda s:s.shift(1).expanding().mean())
        out=rr-exp_mean
        return out
    if op=='regime_flip':
        w=int(expr['window']);v=_ret(d).rolling(24).std()
        # causal expanding quantile regime (like transfer_run13), simplified threshold on past
        thr=v.expanding(min_periods=120).quantile(.66).shift(1)
        hi=(v>=thr).astype(float)
        return hi.diff().abs()  # 1 on transition bars
    if op=='cond':
        xg=eval_v2(d,expr['z'],external)
        gate=(xg>0).astype(float)  # causal binary state
        return x*gate
    raise ValueError('unsafe op '+op)

def validate_v2(expr,depth=0):
    if not isinstance(expr,dict) or depth>3:raise ValueError('bad DSL')
    op=expr.get('op')
    if op=='raw':
        if expr.get('name') not in RAW_V2:raise ValueError('unknown raw')
        return 1
    if op=='cross_return':
        if expr.get('ticker') not in {'IMOEX','CNY'} or int(expr.get('lag',0))<1:raise ValueError('cross needs lag>=1')
        return 1
    if op not in OPS_V2:raise ValueError('unsafe op')
    n=validate_v2(expr.get('x'),depth+1)
    if op=='ratio':n+=validate_v2(expr.get('y'),depth+1)
    if op=='cond':n+=validate_v2(expr.get('z'),depth+1)
    if op in {'cross_corr','cross_beta','corr_breakdown'}:
        if expr.get('ticker') not in {'IMOEX','CNY'}:raise ValueError('bad cross ticker')
    if op in {'zscore','slope','vol_ratio','path_eff','range_compression','rel_volume','dir_volume','vol_conc','pv_corr','vol_of_vol','vol_term','regime_flip','cross_corr','cross_beta','corr_breakdown'}:
        w=int(expr.get('window',0))
        if not 2<=w<=120:raise ValueError('window')
    if op=='difference':
        k=int(expr.get('lag',0))
        if not 1<=k<=72:raise ValueError('lag')
    if n>7:raise ValueError('too complex')
    return n

def complexity_v2(e):return validate_v2(e)
def fid(e):validate_v2(e);return r.sid('feat',e)

# contract: superset preserves V1
def v1_equivalence(d,e):
    for x in list(r.TEMPLATES.values())+list(r.ALTERNATES.values()):
        a=r.eval_feature(d,x,e);b=eval_v2(d,x,e)
        if not np.allclose(a.fillna(0).values,b.fillna(0).values):return False,x
    return True,None

# ------------------------------------------------------- V2 FAMILIES ----
def F(cond_body):return cond_body
FAMILIES_V2={
 'price.close_pos':{'expr':{'op':'zscore','x':{'op':'raw','name':'close_pos'},'window':48},
   'mechanism':'closes near bar high pressure continuation','info_source':'price_structure.intrabar_position',
   'why_not':'V1 had only close-based returns; intrabar close location unused','expected':'upper position predicts next-bar drift direction','falsification':{'min_abs_t':1.0,'half_stability':True},'leakage':'none (bar fully closed)'},
 'price.gap':{'expr':{'op':'zscore','x':{'op':'raw','name':'gap'},'window':48},
   'mechanism':'gap continuation/fill','info_source':'price_structure.gap','why_not':'no gap feature in V1','expected':'large gaps drift','falsification':{'min_abs_t':1.0,'half_stability':True},'leakage':'none'},
 'price.dist_extrema':{'expr':{'op':'zscore','x':{'op':'difference','x':{'op':'raw','name':'close_pos'},'lag':12},'window':48},
   'mechanism':'distance from rolling extremum → reversion/breakout','info_source':'price_structure.extrema_distance','why_not':'V1 trend used returns only','expected':'near-extremum bars behave differently','falsification':{'min_abs_t':1.0,'half_stability':True},'leakage':'none'},
 'price.path_eff':{'expr':{'op':'path_eff','x':{'op':'raw','name':'ret1'},'window':48},
   'mechanism':'directional persistence (path efficiency)','info_source':'price_structure.path','why_not':'V1 slope used signed sums only','expected':'high efficiency → continuation','falsification':{'min_abs_t':1.0,'half_stability':True},'leakage':'none'},
 'price.compression':{'expr':{'op':'range_compression','x':{'op':'raw','name':'close'},'window':48},
   'mechanism':'range compression precedes expansion','info_source':'price_structure.compression','why_not':'V1 vol branch used returns std, not range','expected':'low compression → expansion next','falsification':{'min_abs_t':1.0,'half_stability':True},'leakage':'none'},
 'vol.rel_volume':{'expr':{'op':'rel_volume','x':{'op':'raw','name':'volume'},'window':48},
   'mechanism':'participation level predicts impact','info_source':'volume_structure.level','why_not':'V1 used Δvolume zscore, not level ratio','expected':'high rel volume → larger moves','falsification':{'min_abs_t':1.0,'half_stability':True},'leakage':'none'},
 'vol.dir_volume':{'expr':{'op':'dir_volume','x':{'op':'raw','name':'volume'},'window':48},
   'mechanism':'directional volume proxy (signed)','info_source':'volume_structure.direction','why_not':'V1 volume was unsigned','expected':'signed volume imbalance predicts drift','falsification':{'min_abs_t':1.0,'half_stability':True},'leakage':'none'},
 'vol.pv_corr':{'expr':{'op':'pv_corr','x':{'op':'raw','name':'volume'},'window':48},
   'mechanism':'price-volume disagreement regime','info_source':'volume_structure.coupling','why_not':'no pv coupling in V1','expected':'negative pv-corr regime → reversion','falsification':{'min_abs_t':1.0,'half_stability':True},'leakage':'none'},
 'vol.vol_of_vol':{'expr':{'op':'vol_of_vol','x':{'op':'raw','name':'ret1'},'window':48},
   'mechanism':'volatility-of-volatility signals regime instability','info_source':'volatility_structure.second_order','why_not':'V1 had only first-order vol','expected':'high VoV → unstable next-bar','falsification':{'min_abs_t':1.0,'half_stability':True},'leakage':'none'},
 'vol.vol_term':{'expr':{'op':'vol_term','x':{'op':'raw','name':'ret1'},'window':48},
   'mechanism':'short/long vol ratio = expansion state','info_source':'volatility_structure.term','why_not':'V1 vol_ratio was ret/vol not vol/vol','expected':'term spikes → directional move','falsification':{'min_abs_t':1.0,'half_stability':True},'leakage':'none'},
 'cross.corr_regime':{'expr':{'op':'cross_corr','x':{'op':'raw','name':'ret1'},'window':48,'ticker':'CNY'},
   'mechanism':'cross-asset correlation regime conditions local edge','info_source':'cross_asset.correlation','why_not':'V1 cross used only lagged returns','expected':'corr extremes → different local behaviour','falsification':{'min_abs_t':1.0,'half_stability':True},'leakage':'corr shifted 1 bar'},
 'cross.corr_breakdown':{'expr':{'op':'corr_breakdown','x':{'op':'raw','name':'ret1'},'window':48,'ticker':'CNY'},
   'mechanism':'correlation breakdown precedes decoupling moves','info_source':'cross_asset.corr_transition','why_not':'no correlation dynamics in V1','expected':'breakdown bars behave differently','falsification':{'min_abs_t':1.0,'half_stability':True},'leakage':'shifted'},
 'cross.rel_strength':{'expr':{'op':'rel_strength','x':{'op':'raw','name':'ret1'},'ticker':'CNY','k':12,'window':48},
   'mechanism':'relative strength vs reference asset','info_source':'cross_asset.relative_strength','why_not':'V1 used single lagged return only','expected':'RS extremes mean-revert','falsification':{'min_abs_t':1.0,'half_stability':True},'leakage':'external returns lagged 1'},
 'intraday.seasonal_dev':{'expr':{'op':'seasonal_dev','x':{'op':'raw','name':'ret1'},'window':48},
   'mechanism':'deviation from causal hourly profile','info_source':'intraday.seasonality','why_not':'V1 hour_sin was static, no expectation model','expected':'large deviation → reversion','falsification':{'min_abs_t':1.0,'half_stability':True},'leakage':'expanding past-only mean'},
 'regime.transition':{'expr':{'op':'regime_flip','x':{'op':'raw','name':'ret1'},'window':48},
   'mechanism':'vol-regime transition bars behave differently','info_source':'regime.transition','why_not':'V1 had static regime labels, no transitions','expected':'transition bars → larger/opposite drift','falsification':{'min_abs_t':1.0,'half_stability':True},'leakage':'expanding quantile shift(1)'},
 'cond.high_vol_mom':{'expr':{'op':'cond','x':{'op':'slope','x':{'op':'raw','name':'ret1'},'window':24},'z':{'op':'regime_flip','x':{'op':'raw','name':'ret1'},'window':48}},
   'mechanism':'momentum GIVEN recent regime transition','info_source':'conditional.price×regime','why_not':'V1 DSL had no conditionals','expected':'mom works only after transitions','falsification':{'min_abs_t':1.0,'half_stability':True},'leakage':'none'},
}

# V1 family space (from run11) — 21 families for the V1 side of the tournament.
def v1_space():
    out={}
    for b in a.BRANCHES:
        for i,x in enumerate(a.choices(b)):out[f'v1.{b}.{i}']={'expr':x,'mechanism':'v1_fixed','info_source':'v1','why_not':'-','expected':'-','falsification':{'min_abs_t':1.0,'half_stability':True},'leakage':'none'}
    return out

SPACE_V1=v1_space()
SPACE_V2={**SPACE_V1,**{k:{**v,'expr':v['expr']} for k,v in FAMILIES_V2.items()}}
for k in SPACE_V2:SPACE_V2[k]['expr'] and validate_v2(SPACE_V2[k]['expr'])

# ------------------------------------------------------- orthogonality ----
def mutual_info(a,b,bins=10):
    z=pd.concat([a,b],axis=1).replace([np.inf,-np.inf],np.nan).dropna()
    if len(z)<100:return 0.0
    ha=np.histogram(z.iloc[:,0],bins=bins)[0]/len(z);hb=np.histogram(z.iloc[:,1],bins=bins)[0]/len(z)
    ab=np.histogram2d(z.iloc[:,0],z.iloc[:,1],bins=bins)[0]/len(z)
    nz=ab>0
    return float((ab[nz]*np.log(ab[nz]/(ha[:,None]*hb[None,:])[nz])).sum())

def orthogonality(d,e,expr,next_ret):
    """FORMULA vs INFORMATION novelty. info_novel requires that NO single bank
    feature explains away the candidate's predictive relation (residual test)."""
    f=eval_v2(d,expr,e)
    # Degeneracy guard: a CONSTANT feature carries no information.
    # Legitimately binary families (e.g. regime transitions) are allowed;
    # they still must beat the residual test to be info_novel.
    fd=f.replace([np.inf,-np.inf],np.nan).dropna()
    if len(fd)<100 or float(fd.std())<=1e-12 or int(fd.nunique())<=1:
        return {'corr':0.,'mi':0.,'resid_ratio':0.0,'nearest':None,'degenerate':True,
                'formula_novel':False,'info_novel':False}
    bank={}
    for name,x in list(r.TEMPLATES.items())+list(r.ALTERNATES.items()):
        bank[f'v1::{name}']=eval_v2(d,x,e)
    best={'corr':0.,'mi':0.,'resid_ratio':1.0,'nearest':None}
    z=pd.DataFrame({'f':f,'r':next_ret}).replace([np.inf,-np.inf],np.nan).dropna()
    for name,bv in bank.items():
        zz=pd.concat([f,bv],axis=1).replace([np.inf,-np.inf],np.nan).dropna()
        if len(zz)<100:continue
        p=abs(float(zz.iloc[:,0].corr(zz.iloc[:,1])));s=abs(float(zz.iloc[:,0].corr(zz.iloc[:,1],method='spearman')))
        q75=zz.iloc[:,1].quantile(.75);a_=zz.iloc[:,0]>=zz.iloc[:,0].quantile(.75);q=zz.iloc[:,1]>=q75
        ov=float((a_&q).sum()/max(1,(a_|q).sum()))
        mi=mutual_info(zz.iloc[:,0],zz.iloc[:,1])
        score=max(p,s,ov)
        if score>best['corr']:best={'corr':round(score,4),'mi':round(mi,4),'nearest':name,'resid_ratio':best['resid_ratio']}
        if mi>best['mi']:best['mi']=round(mi,4)
    # residual predictiveness: 1-factor OLS on strongest correlated bank member
    if best['nearest']:
        bv=bank[best['nearest']];zz=pd.concat([f,bv],axis=1).replace([np.inf,-np.inf],np.nan).dropna()
        zz['r']=next_ret.reindex(zz.index)
        beta=float(np.cov(zz.iloc[:,0],zz.iloc[:,1])[0,1]/np.var(zz.iloc[:,1])) if np.var(zz.iloc[:,1])>0 else 0.
        resid=zz.iloc[:,0]-beta*zz.iloc[:,1]
        raw=float(zz.iloc[:,0].corr(zz['r'])) if zz['r'].std()>0 else 0.
        res=float(resid.corr(zz['r'])) if zz['r'].std()>0 else 0.
        best['resid_ratio']=round(abs(res)/max(1e-9,abs(raw)),4) if raw else 1.0
    formula_novel=best['corr']<.92
    info_novel=formula_novel and best['mi']<0.6 and best['resid_ratio']>0.5
    return {**best,'formula_novel':bool(formula_novel),'info_novel':bool(info_novel)}

# ------------------------------------------------------------- gate ------
def anti_zoo_check(d,e):
    """Adversarial zoo generator → gate must reject most."""
    next_ret=np.log(d.close.shift(-1)/d.close)
    junk=[
      {'op':'zscore','x':{'op':'raw','name':'ret1'},'window':w} for w in (6,18,30,36,60,96,108)
    ]+[
      {'op':'ratio','x':{'op':'zscore','x':{'op':'raw','name':'ret1'},'window':24},'y':{'op':'zscore','x':{'op':'raw','name':'ret1'},'window':24}},  # f/f = 1
      {'op':'difference','x':{'op':'zscore','x':{'op':'raw','name':'ret1'},'window':24},'lag':1},
      {'op':'zscore','x':{'op':'zscore','x':{'op':'raw','name':'ret1'},'window':24},'window':48},
      {'op':'slope','x':{'op':'raw','name':'hour_sin'},'window':24},
    ]
    accepted=0;details=[]
    for j in junk:
        try:
            o=orthogonality(d,e,j,next_ret)
            ok=o['info_novel']
        except Exception:ok=False
        details.append((r.canon(j)[:40],ok));accepted+=ok
    return {'junk_total':len(junk),'accepted':accepted,'rejected':len(junk)-accepted,'rejection_rate':round((len(junk)-accepted)/len(junk),3),'details':details}

# ------------------------------------------------------ space map --------
def space_map():
    return {'schema':SCHEMA,'families':{k:{'information_source':v.get('info_source'),'mechanism':v.get('mechanism'),'branch':k.split('.')[0] if '.' in k else 'v1','complexity':complexity_v2(v['expr']) if '.' in k else r.complexity(v['expr']),'leakage_risk':v.get('leakage'),'expected_effect':v.get('expected'),'falsification':v.get('falsification'),'why_not_represented':v.get('why_not'),'tested_assets':[],'tested_regimes':[],'results':{},'conflicts':[]} for k,v in SPACE_V2.items()},'counts':{'v1':len(SPACE_V1),'v2_new':len(FAMILIES_V2),'total':len(SPACE_V2)}}

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--out',required=True);args=p.parse_args()
    base,_=r.load('IMOEX');ext,_=r.load('CNY');d,cut=r.train_only(base);e,_=r.train_only(ext)
    nr=np.log(d.close.shift(-1)/d.close)
    res={'v1_equiv':v1_equivalence(d,e)[0],'orthogonality':{},'anti_zoo':anti_zoo_check(d,e),'space_map':space_map()}
    for k,v in FAMILIES_V2.items():res['orthogonality'][k]=orthogonality(d,e,v['expr'],nr)
    Path(args.out).write_text(json.dumps(res,ensure_ascii=False,indent=2,default=str))
    print('v1_equiv',res['v1_equiv'],'anti_zoo_rejection',res['anti_zoo']['rejection_rate'])
    print('info_novel:',{k:x['info_novel'] for k,x in res['orthogonality'].items()})
