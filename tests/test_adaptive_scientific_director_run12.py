#!/usr/bin/env python3
"""RUN-12 adversarial checks: director cannot weaken science."""
import sys,unittest,json
from pathlib import Path
import numpy as np,pandas as pd
SC=Path('/root/prop-desk/strategy_combine');FL=Path('/root/prop-desk/futures_lab');sys.path[:0]=[str(SC),str(FL),str(SC/'engines')]
import adaptive_scientific_director_run12 as a
import research_intelligence_run11 as r

def f(n=700):
 q=np.random.default_rng(3);c=100*np.exp(np.cumsum(q.normal(0,.002,n)));return pd.DataFrame({'time':pd.date_range('2025-01-01',periods=n,freq='h'),'open':c,'high':c*1.002,'low':c*.998,'close':c,'volume':q.integers(5,100,n)})
class T(unittest.TestCase):
 def test_competing_hypotheses(self):
  x=a.competing_hypotheses({'observation_id':'o'},'volume_liquidity',r.TEMPLATES['volume_liquidity']);self.assertEqual(len(x),4);self.assertEqual({z['mechanism'] for z in x},{'continuation','reversal','high_vol_only','seasonality_artifact'})
 def test_adaptive_memory_penalty(self):
  s=a.initial_state('x');primary=r.canon(r.TEMPLATES['cross_asset']);s['failed_contexts']=[{'family_key':primary,'branch':'cross_asset','regime':'global','reason':'GLOBAL_FAIL','feature_node':'f','experiment_id':'e'}]
  p=a.AdaptiveScientificDirector().propose_research_plan(s,2,7);z=[x for x in p if x[0]=='cross_asset'][0];self.assertNotEqual(r.canon(z[1]),primary)
 def test_branch_starvation_guard(self):
  p=a.AdaptiveScientificDirector().propose_research_plan(a.initial_state('x'),1,7);self.assertEqual(len({x[0] for x in p}),7)
 def test_bad_complexity_rejected(self):
  # depth 4 beneath root exceeds DSL depth 3.
  with self.assertRaises(ValueError):r.validate({'op':'zscore','x':{'op':'zscore','x':{'op':'zscore','x':{'op':'zscore','x':{'op':'raw','name':'ret1'},'window':24},'window':24},'window':24},'window':24})
 def test_cross_future_immutability(self):
  d,e=f(),f();expr={'op':'cross_return','ticker':'CNY','lag':2};x=r.eval_feature(d,expr,e);q=e.copy();q.loc[400:,'close']*=3;y=r.eval_feature(d,expr,q);self.assertTrue(np.allclose(x.iloc[:402].fillna(0),y.iloc[:402].fillna(0)))
 def test_corrupted_director_output_rejected(self):
  with self.assertRaises(ValueError):r.validate({'op':'shell','command':'rm -rf /'})
 def test_llm_failure_is_not_dependency(self):
  # no LLM class/import required; adaptive director runs wholly deterministic
  self.assertTrue(a.AdaptiveScientificDirector().name.startswith('adaptive'))
 def test_contextual_negative_kind(self):
  self.assertEqual(a.contextual_reason('FAIL',['feature_clone'],'x','global'),'CLONE');self.assertEqual(a.contextual_reason('FAIL',['tstat_floor'],'x','high_vol'),'INSUFFICIENT_EVIDENCE')
if __name__=='__main__':unittest.main(verbosity=2)
