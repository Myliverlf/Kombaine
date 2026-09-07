#!/usr/bin/env python3
"""MVP Research Intelligence: deterministic tests, broker-free."""
import json, sys, tempfile, unittest
from pathlib import Path
import numpy as np
import pandas as pd
SC=Path('/root/prop-desk/strategy_combine'); FL=Path('/root/prop-desk/futures_lab')
sys.path.insert(0,str(SC)); sys.path.insert(0,str(FL)); sys.path.insert(0,str(SC/'engines'))
import research_intelligence as ri


def frame(n=400):
    rng=np.random.default_rng(7); r=rng.normal(0, .002,n); c=100*np.exp(np.cumsum(r))
    return pd.DataFrame({'time':pd.date_range('2025-01-01',periods=n,freq='h'),'open':c,'high':c*1.002,'low':c*.998,'close':c,'volume':rng.integers(10,100,n)})

class T(unittest.TestCase):
 def test_unsafe_rejected(self):
  with self.assertRaises(ValueError): ri.validate_expr({'op':'python','x':{'op':'raw','name':'close'}})
 def test_canonical_id(self):
  e={'window':24,'x':{'name':'close','op':'raw'},'op':'zscore'}
  self.assertEqual(ri.expression_id(e),ri.expression_id({'op':'zscore','x':{'op':'raw','name':'close'},'window':24}))
 def test_future_perturbation_no_past_change(self):
  d=frame(); e={'op':'zscore','x':{'op':'difference','x':{'op':'raw','name':'close'},'lag':5},'window':24}; a=ri.evaluate_feature(d,e)
  q=d.copy(); q.loc[300:,'close']*=3; q.loc[300:,'high']*=3; q.loc[300:,'low']*=3; b=ri.evaluate_feature(q,e)
  self.assertTrue(np.allclose(a.iloc[:300].fillna(0),b.iloc[:300].fillna(0)))
 def test_observation_has_preregistered_falsifier_evidence(self):
  d=frame(2000); e={'op':'zscore','x':{'op':'raw','name':'return_1'},'window':24}; o=ri.discover_observation(d,e,'IMOEX')
  self.assertIn('tstat',o.effect); self.assertIn('threshold_q75',o.effect)
  self.assertTrue(np.isfinite(o.effect['tstat']))
 def test_clone_detected(self):
  d=frame(); x=np.log(d.close/d.close.shift()); z=ri.feature_novelty(x,{'same':x})
  self.assertFalse(z['novel']); self.assertEqual(z['nearest_feature'],'same')
 def test_distinct_feature_can_be_novel(self):
  d=frame(); x=np.log(d.close/d.close.shift()); y=(d.volume-d.volume.rolling(24).mean())/d.volume.rolling(24).std(); z=ri.feature_novelty(y,{'return':x})
  self.assertTrue(z['novel'])
 def test_feature_bank_checks_existing_e4_representation(self):
  d=frame(); cov=ri.novelty_coverage(d)
  self.assertGreaterEqual(cov['bank_size'],7)
  # E4 optional dependency availability is recorded, never silently assumed.
  self.assertIn('e4_feature_bank_available',cov)
 def test_negative_knowledge_skips_next_plan(self):
  with tempfile.TemporaryDirectory() as td:
   st=ri.ResearchStore(Path(td)); s=st.load(); expr=ri.DeterministicDirector.templates[0]; s['negative_knowledge']=[{'family_key':ri.canonical(expr)}]; st.save(s)
   p=ri.DeterministicDirector().propose_research_plan(ri.DeterministicDirector().observe_state(st.load()),{'experiment_budget':3})
   self.assertNotIn(ri.canonical(expr),[x['family_key'] for x in p.hypotheses])
 def test_director_interface_replaceable(self):
  class Other(ri.DeterministicDirector): name='other'
  self.assertIsInstance(Other(),ri.ResearchDirector)
if __name__=='__main__': unittest.main(verbosity=2)
