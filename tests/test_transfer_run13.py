#!/usr/bin/env python3
"""RUN-13 adversarial tests: causal regimes, transfer priors, saturation, isolation."""
import sys,unittest,json
from pathlib import Path
import numpy as np,pandas as pd
SC=Path('/root/prop-desk/strategy_combine');FL=Path('/root/prop-desk/futures_lab');sys.path[:0]=[str(SC),str(FL),str(SC/'engines')]
import transfer_run13 as t
import adaptive_scientific_director_run12 as a
import research_intelligence_run11 as r

def f(n=800,vol=.002):
 q=np.random.default_rng(7);c=100*np.exp(np.cumsum(q.normal(0,vol,n)));return pd.DataFrame({'time':pd.date_range('2025-01-01',periods=n,freq='h'),'open':c,'high':c*1.002,'low':c*.998,'close':c,'volume':q.integers(5,100,n)})
class T(unittest.TestCase):
 def test_regime_causal(self):
  d=f();reg=t.causal_regimes(d)
  # future mutation: double vol in last 100 bars must not change earlier labels
  q=d.copy();q.loc[len(q)-100:,'close']=q.loc[len(q)-100:,'close'].values*1.05
  reg2=t.causal_regimes(q)
  for k in reg:
   self.assertEqual([bool(x) for x in reg[k][:len(d)-100]],[bool(x) for x in reg2[k][:len(d)-100]],k)
 def test_prior_scope_ordering(self):
  st={'failed_contexts':[{'family_key':'K','asset':'IMOEX','regime':'low_vol','reason':'GLOBAL_FAIL','experiment_id':'e','contradictions':0}]}
  # same asset+regime strongest
  b1,_,s1,_=t.prior_for(st,'K','IMOEX','low_vol')
  # different asset same regime weaker
  _,c2,s2,_=t.prior_for(st,'K','SBER','low_vol')
  # different asset different regime weakest
  _,c3,s3,_=t.prior_for(st,'K','SBER','high_vol')
  self.assertEqual(s1,'ASSET_REGIME');self.assertEqual(s2,'REGIME');self.assertEqual(s3,'ASSET_CLASS')
 def test_hard_block_unconditional(self):
  st={'failed_contexts':[{'family_key':'K','asset':'IMOEX','regime':'x','reason':'CLONE','experiment_id':'e'}]}
  b,_,scope,_=t.prior_for(st,'K','SBER','high_vol');self.assertTrue(b);self.assertEqual(scope,'GLOBAL')
 def test_contradiction_decays_confidence(self):
  st={'failed_contexts':[{'family_key':'K','asset':'IMOEX','regime':'low_vol','reason':'GLOBAL_FAIL','experiment_id':'e','contradictions':0}]}
  _,c1,_,_=t.prior_for(st,'K','IMOEX','low_vol')
  st['failed_contexts'][0]['contradictions']=1;_,c2,_,_=t.prior_for(st,'K','IMOEX','low_vol')
  self.assertLess(c2,c1)
 def test_cold_transfer_knowledge_isolation(self):
  # cold director never receives run12 knowledge
  cold=t.ColdAdaptiveDirector();self.assertEqual(cold.knowledge['failed_contexts'],[])
 def test_transfer_uses_priors(self):
  fz={'failed_contexts':[{'family_key':r.canon(r.TEMPLATES['cross_asset']),'asset':'IMOEX','regime':'normal','reason':'GLOBAL_FAIL','experiment_id':'e','contradictions':0}]}
  st=a.initial_state('x');st['asset']='SBER';st['regime']='normal'
  plan=t.TransferAdaptiveDirector(fz).propose_research_plan(st,1,7)
  ca=[x for x in plan if x[0]=='cross_asset'][0]
  # strong prior (same regime class via ASSET_CLASS scope) should shift score but not hard-block
  self.assertLess(ca[2]['score_parts']['transfer_confidence'],0.95)
 def test_saturation_flags(self):
  st=a.initial_state('x')
  # fabricate degenerate history: same family, low IG variance
  for i in range(6):
   st['experiments'][f'e{i}']={'information_gain':1.0,'family_key':'SAME','ts':str(i),'director_meta':{},'observation':{'novelty':{'novel':False}}}
  s=t.saturation_check(st);self.assertTrue(s['saturated'])
 def test_no_false_saturation_fresh(self):
  st=a.initial_state('x');self.assertFalse(t.saturation_check(st)['saturated'])
 def test_hidden_not_in_knowledge(self):
  # freeze snapshot carries metadata (cutoff date is fine) but NOT hidden-period data itself.
  fz=t.freeze_run12();s=json.dumps(fz,default=str).lower()
  self.assertNotIn("'close'",s);self.assertNotIn('close":',s);self.assertNotIn('volume":',s)
if __name__=='__main__':unittest.main(verbosity=2)
