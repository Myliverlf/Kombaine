#!/usr/bin/env python3
"""RUN-14 tests: V1 equivalence, anti-zoo, orthogonality, conditionals, leakage."""
import sys,unittest
from pathlib import Path
import numpy as np,pandas as pd
SC=Path('/root/prop-desk/strategy_combine');FL=Path('/root/prop-desk/futures_lab');sys.path[:0]=[str(SC),str(FL),str(SC/'engines')]
import research_space_v2 as v
import research_intelligence_run11 as r
import adaptive_scientific_director_run12 as a

def f(n=900,vol=.002):
 q=np.random.default_rng(11);c=100*np.exp(np.cumsum(q.normal(0,vol,n)))
 return pd.DataFrame({'time':pd.date_range('2025-01-01',periods=n,freq='h'),'open':c,'high':c*1.003,'low':c*.997,'close':c,'volume':q.integers(20,200,n)})
class T(unittest.TestCase):
 def test_v1_superset_bitwise(self):
  d,e=f(),f();ok,bad=v.v1_equivalence(d,e);self.assertTrue(ok,bad)
 def test_v1_unchanged_after_v2_ops(self):
  # old director spaces still validate under v1
  for b in a.BRANCHES:
   for ch in a.choices(b):r.validate(ch)
 def test_conditional_causal(self):
  d,e=f(),f();expr={'op':'cond','x':{'op':'slope','x':{'op':'raw','name':'ret1'},'window':24},'z':{'op':'regime_flip','x':{'op':'raw','name':'ret1'},'window':48}}
  x=v.eval_v2(d,expr,e);self.assertTrue(bool(np.isfinite(x.iloc[300:]).any()))
  # future perturbation of close doesn't change past conditional value
  q=d.copy();q.loc[len(q)-50:,'close']=q.loc[len(q)-50:,'close'].values*1.1
  y=v.eval_v2(q,expr,e)
  self.assertTrue(np.allclose(x.iloc[:len(d)-51].fillna(0),y.iloc[:len(d)-51].fillna(0)))
 def test_regime_flip_causal(self):
  d,e=f(),f();x=v.eval_v2(d,{'op':'regime_flip','x':{'op':'raw','name':'ret1'},'window':48},e)
  q=d.copy();q.loc[len(q)-100:,'close']*=1.2;y=v.eval_v2(q,{'op':'regime_flip','x':{'op':'raw','name':'ret1'},'window':48},e)
  self.assertTrue(np.allclose(x.iloc[:len(d)-100].fillna(0),y.iloc[:len(d)-100].fillna(0)))
 def test_cross_ops_causal(self):
  d,e=f(),f()
  for op in ('cross_corr','cross_beta','corr_breakdown','rel_strength'):
   expr={'op':op,'x':{'op':'raw','name':'ret1'},'window':48,'ticker':'CNY'}
   x=v.eval_v2(d,expr,e);q=e.copy();q.loc[len(q)-80:,'close']*=3
   y=v.eval_v2(d,expr,q)
   n=len(d)-90  # window 48 + lag buffer: beyond this, perturbation must not matter
   self.assertTrue(np.allclose(x.iloc[:n].fillna(0),y.iloc[:n].fillna(0)),op)
 def test_unsafe_ops_rejected(self):
  for bad in ({'op':'shell'},{'op':'raw','name':'open_interest'},{'op':'cross_return','ticker':'SBER','lag':2}):
   with self.assertRaises(ValueError):v.validate_v2(bad)
 def test_zoo_exprs_rejected_by_validate(self):
  # f/f = constant ratio is a degenerate clone; the *gate* must kill it via corr
  d,e=f(),f();nr=np.log(d.close.shift(-1)/d.close)
  clone={'op':'ratio','x':{'op':'zscore','x':{'op':'raw','name':'ret1'},'window':24},'y':{'op':'zscore','x':{'op':'raw','name':'ret1'},'window':24}}
  o=v.orthogonality(d,e,clone,nr)
  # f/f is constant (1) → correlation undefined/NaN → corr score 0 → would look "novel"
  # by raw corr. Orthogonality must catch degeneracy: f has no variance.
  fv=v.eval_v2(d,clone,e)
  degenerate=float(fv.dropna().std())==0.0 or float(fv.dropna().nunique())<=2
  self.assertTrue(degenerate,'f/f must be detected as degenerate constant')
  self.assertFalse(o['info_novel'])
 def test_anti_zoo_rejects_most(self):
  d,e=f(),f();az=v.anti_zoo_check(d,e);self.assertGreaterEqual(az['rejection_rate'],0.6)
 def test_information_novel_passes_gate(self):
  # genuinely new info source (dir_volume) must pass; formula-clone must fail
  d,e=f(),f();nr=np.log(d.close.shift(-1)/d.close)
  ok=v.FAMILIES_V2['vol.dir_volume']['expr']
  o=v.orthogonality(d,e,ok,nr);self.assertTrue(o['info_novel'])
 def test_space_map_complete(self):
  m=v.space_map();self.assertEqual(m['counts']['v1'],21);self.assertGreaterEqual(m['counts']['v2_new'],15)
  for k,fam in m['families'].items():
   if k.startswith(('price','vol.','cross','intraday','regime','cond')):self.assertIn('mechanism',fam,k)
if __name__=='__main__':unittest.main(verbosity=2)
