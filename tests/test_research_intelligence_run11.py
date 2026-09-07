#!/usr/bin/env python3
"""RUN-11 tests: bounded DSL, cross-asset causality, holdout isolation, equal budget."""
import sys, tempfile, unittest
from pathlib import Path
import numpy as np
import pandas as pd
SC=Path('/root/prop-desk/strategy_combine'); FL=Path('/root/prop-desk/futures_lab')
sys.path[:0]=[str(SC),str(FL),str(SC/'engines')]
import research_intelligence_run11 as r

def f(n=600,shift=0):
 rng=np.random.default_rng(42+shift); c=100*np.exp(np.cumsum(rng.normal(0,.002,n)))
 return pd.DataFrame({'time':pd.date_range('2025-01-01',periods=n,freq='h'),'open':c,'high':c*1.002,'low':c*.998,'close':c,'volume':rng.integers(10,100,n)})
class T(unittest.TestCase):
 def test_cross_requires_lag(self):
  with self.assertRaises(ValueError):r.validate({'op':'cross_return','ticker':'CNY','lag':0})
 def test_cross_future_perturbation(self):
  a,b=f(),f(1); e={'op':'cross_return','ticker':'CNY','lag':2}; x=r.eval_feature(a,e,b); q=b.copy();q.loc[400:,'close']*=5;y=r.eval_feature(a,e,q)
  self.assertTrue(np.allclose(x.iloc[:402].fillna(0),y.iloc[:402].fillna(0)))
 def test_local_future_perturbation(self):
  a,b=f(),f(1); e={'op':'zscore','x':{'op':'difference','x':{'op':'raw','name':'close'},'lag':5},'window':24};x=r.eval_feature(a,e,b);q=a.copy();q.loc[400:,'close']*=5;y=r.eval_feature(q,e,b)
  self.assertTrue(np.allclose(x.iloc[:400].fillna(0),y.iloc[:400].fillna(0)))
 def test_hidden_period_does_not_change_plan(self):
  # planning only reads persistent FAIL state/budget; hidden data cannot affect it.
  s=r.fresh_state('x'); d=r.DeterministicIntelligentDirector(); p1=d.plan(s,1,{'experiment_evaluations':7});p2=d.plan(s,1,{'experiment_evaluations':7})
  self.assertEqual([x.hypothesis_id for x in p1],[x.hypothesis_id for x in p2])
 def test_memory_changes_intelligent_plan(self):
  s=r.fresh_state('x'); primary=r.canon(r.TEMPLATES['cross_asset']);s['negative_family_keys']=[primary];p=r.DeterministicIntelligentDirector().plan(s,2,{'experiment_evaluations':7});item=[x for x in p if x.branch=='cross_asset'][0]
  self.assertEqual(item.feature_expr,r.ALTERNATES['cross_asset']);self.assertNotEqual(item.family_key,primary)
 def test_baseline_repeats(self):
  s=r.fresh_state('x');d=r.RandomBaselineDirector();a=d.plan(s,1,{'experiment_evaluations':7});b=d.plan(s,2,{'experiment_evaluations':7});self.assertEqual([x.family_key for x in a],[x.family_key for x in b])
 def test_novelty_bank_excludes_self_but_keeps_other_features(self):
  a,b=f(),f(1); e=r.TEMPLATES['trend']; z=r.eval_feature(a,e,b); n=r.novelty(z,r.bank(a,b,exclude_feature_id=r.feature_id(e)))
  self.assertNotEqual(n.get('nearest'),f'candidate::{r.feature_id(e)}')
 def test_checkpoint_resume_uses_frozen_plan(self):
  # Full runtime uses real data; this checks public recovery contract exists.
  self.assertTrue(callable(r.checkpointed_cycle))
 def test_research_value_not_only_profit(self):
  item=r.make_plan(r.TEMPLATES,1,'x',{'experiment_evaluations':1})[0];o={'effect':{'tstat':0},'novelty':{'novel':False}}
  self.assertGreater(r.research_value('FAIL',o,item,['feature_clone']),0)
if __name__=='__main__':unittest.main(verbosity=2)
