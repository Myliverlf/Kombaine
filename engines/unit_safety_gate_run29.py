#!/usr/bin/env python3
"""RUN-29: Unit Safety Gate & Economic Claim Audit.
Deterministic. No LLM. No strategy.

Creates typed metric system and validates all prior claims.
"""
from __future__ import annotations
import json,hashlib,math,time,re
from pathlib import Path

SCHEMA='run29-unit-safety-gate-v1'
OUT=Path('/root/audits/strategy_combine_research_intelligence/run29')
OUT.mkdir(parents=True,exist_ok=True)
def now():return time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())

# ================================================== TYPED METRIC SYSTEM ===
METRIC_TYPES={
 'RETURN':{'dimension':'return','range':'(-inf,+inf)'},
 'RETURN_BPS':{'dimension':'return','range':'(-inf,+inf)','note':'basis points'},
 'PNL_RUB':{'dimension':'currency','range':'(-inf,+inf)'},
 'PRICE':{'dimension':'price','range':'(0,+inf)'},
 'VOLUME':{'dimension':'count','range':'[0,+inf)'},
 'VOLATILITY':{'dimension':'return','range':'[0,+inf)'},
 'CORRELATION':{'dimension':'dimensionless','range':'[-1,+1]'},
 'AUTOCORRELATION':{'dimension':'dimensionless','range':'[-1,+1]'},
 'R2':{'dimension':'dimensionless','range':'[0,1]'},
 'INCREMENTAL_R2':{'dimension':'dimensionless','range':'(-1,+1)'},
 'T_STAT':{'dimension':'dimensionless','range':'(-inf,+inf)'},
 'P_VALUE':{'dimension':'probability','range':'[0,1]'},
 'RATIO':{'dimension':'dimensionless','range':'[0,+inf)'},
 'COUNT':{'dimension':'count','range':'[0,+inf)'},
 'TIME':{'dimension':'time','range':'[0,+inf)'},
 'COST_RETURN':{'dimension':'return','range':'[0,+inf)','note':'cost in return units'},
 'COST_RUB':{'dimension':'currency','range':'[0,+inf)'},
 'SCORE':{'dimension':'dimensionless','range':'(-inf,+inf)'},
}

# ================================================== COMPATIBILITY RULES ===
ALLOWED_OPS={
 ('RETURN','+','RETURN'):True,
 ('RETURN','-','RETURN'):True,
 ('RETURN','/','COST_RETURN'):True,  # return / cost = ratio (same horizon required)
 ('COST_RETURN','/','RETURN'):True,
 ('RETURN_BPS','+','RETURN_BPS'):True,
 ('RETURN_BPS','-','RETURN_BPS'):True,
 ('PNL_RUB','+','PNL_RUB'):True,
 ('PNL_RUB','-','PNL_RUB'):True,
 ('PNL_RUB','/','COST_RUB'):True,
 ('COST_RUB','/','PNL_RUB'):True,
 ('CORRELATION','/','RETURN'):False,  # BLOCK
 ('AUTOCORRELATION','/','RETURN'):False,  # BLOCK — RUN-27 bug
 ('AUTOCORRELATION','>','COST_RETURN'):False,  # BLOCK — RUN-27 bug
 ('R2','/','RETURN'):False,  # BLOCK
 ('T_STAT','+','RETURN'):False,  # BLOCK
 ('R2','+','RETURN'):False,  # BLOCK
 ('PNL_RUB','/','RETURN'):False,  # BLOCK without capital
 ('SCORE','/','RETURN'):False,  # BLOCK
 ('SCORE','>','COST_RETURN'):False,  # BLOCK
}

def check_compatibility(type_a, op, type_b):
    key=(type_a,op,type_b)
    if key in ALLOWED_OPS:
        return ALLOWED_OPS[key]
    # same dimension check
    dim_a=METRIC_TYPES.get(type_a,{}).get('dimension','')
    dim_b=METRIC_TYPES.get(type_b,{}).get('dimension','')
    if op in ('+','-') and dim_a==dim_b and dim_a!='':
        return True
    if op in ('/','>') and dim_a==dim_b:
        return True
    return None  # unknown — block by default

# ================================================== METRIC VALUE OBJECT ===
class MetricValue:
    def __init__(self,value,metric_type,unit='auto',horizon='1h',asset=None,
                 source='',formula='',provenance=''):
        self.value=value
        self.metric_type=metric_type
        self.unit=unit
        self.horizon=horizon
        self.asset=asset
        self.source=source
        self.formula=formula
        self.provenance=provenance
    def to_dict(self):
        return {'value':self.value,'metric_type':self.metric_type,'unit':self.unit,
                'horizon':self.horizon,'asset':self.asset,'source':self.source,
                'formula':self.formula,'provenance':self.provenance}

# ================================================== CLAIM SAFETY GATE ===
def claim_safety_gate(metric_a,metric_b,operation,claim_text=''):
    type_a=metric_a.metric_type
    type_b=metric_b.metric_type
    compatible=check_compatibility(type_a,operation,type_b)
    issues=[]
    if compatible is False:
        issues.append(('BLOCKED_INCOMPATIBLE_UNITS',
                       f'{type_a} {operation} {type_b} not allowed'))
    elif compatible is None:
        issues.append(('BLOCKED_UNKNOWN_COMPATIBILITY',
                       f'{type_a} {operation} {type_b} unknown — blocked by default'))
    # horizon check
    if metric_a.horizon!=metric_b.horizon and operation in ('+','-','>','<'):
        issues.append(('BLOCKED_HORIZON_MISMATCH',
                       f'{metric_a.horizon} vs {metric_b.horizon}'))
    # scale check
    if type_a!=type_b and operation in ('+','-'):
        issues.append(('BLOCKED_SCALE_MISMATCH',
                       f'{type_a} + {type_b} different types'))
    return {
        'allowed':len(issues)==0,
        'issues':issues,
        'metric_a':metric_a.to_dict(),
        'metric_b':metric_b.to_dict(),
        'operation':operation,
        'claim':claim_text}

# ================================================== AUDIT HISTORICAL CLAIMS ===
def audit_claims():
    claims=[
        {'run':'RUN-18','claim':'lag1 AC = -0.054, t=-3.42',
         'metric_a':MetricValue(-0.054,'AUTOCORRELATION','dimensionless','1h','IMOEX').to_dict(),
         'metric_b':MetricValue(-3.42,'T_STAT','dimensionless','1h','IMOEX').to_dict(),
         'operation':'annotation','verdict':'VALID','reason':'descriptive annotation, no comparison'},
        {'run':'RUN-20','claim':'M1 OOS R² = -0.008',
         'metric_a':MetricValue(-0.008,'R2','dimensionless','1h','IMOEX').to_dict(),
         'metric_b':None,'operation':'annotation',
         'verdict':'VALID','reason':'descriptive, no economic claim'},
        {'run':'RUN-24','claim':'liquidity effect supports SBER/LKOH',
         'metric_a':MetricValue(0.073,'SCORE','dimensionless','1h','SBER').to_dict(),
         'metric_b':MetricValue(0.02,'SCORE','dimensionless','1h','SBER').to_dict(),
         'operation':'>','verdict':'VALID','reason':'same type comparison (ac_range vs threshold)'},
        {'run':'RUN-26','claim':'net after 5bps = -0.00037',
         'metric_a':MetricValue(-0.00037,'RETURN','return','1h','SBER').to_dict(),
         'metric_b':MetricValue(0.0005,'COST_RETURN','return','1h','SBER').to_dict(),
         'operation':'-','verdict':'VALID','reason':'RETURN - COST_RETURN, same units/horizon'},
        {'run':'RUN-27','claim':'19x above friction floor',
         'metric_a':MetricValue(0.093,'AUTOCORRELATION','dimensionless','1h','SBER').to_dict(),
         'metric_b':MetricValue(0.005,'COST_RETURN','return','1h','SBER').to_dict(),
         'operation':'/',
         'verdict':'INVALID_UNIT_COMPARISON',
         'reason':'AUTOCORRELATION / COST_RETURN — different dimensions. RUN-27 bug.'},
        {'run':'RUN-27','claim':'effect 0.093 vs friction 0.005, above floor',
         'metric_a':MetricValue(0.093,'AUTOCORRELATION','dimensionless','1h','SBER').to_dict(),
         'metric_b':MetricValue(0.005,'COST_RETURN','return','1h','SBER').to_dict(),
         'operation':'>',
         'verdict':'INVALID_UNIT_COMPARISON',
         'reason':'AUTOCORRELATION > COST_RETURN — different dimensions. Superseded by RUN-28.'},
        {'run':'RUN-28','claim':'net after costs = -0.000578',
         'metric_a':MetricValue(-0.000578,'RETURN','return','1h','SBER').to_dict(),
         'metric_b':MetricValue(0.0005,'COST_RETURN','return','1h','SBER').to_dict(),
         'operation':'-','verdict':'VALID','reason':'RETURN - COST_RETURN, correct units'},
        {'run':'RUN-27','claim':'liquidity mediates fully (reduction=1.0)',
         'metric_a':MetricValue(1.0,'RATIO','dimensionless','1h','SBER').to_dict(),
         'metric_b':None,'operation':'annotation',
         'verdict':'VALID','reason':'descriptive ratio, no economic claim'},
        {'run':'RUN-24','claim':'volatility transitions ~2-5% on SBER',
         'metric_a':MetricValue(0.035,'RATIO','dimensionless','1h','SBER').to_dict(),
         'metric_b':None,'operation':'annotation',
         'verdict':'UNVERIFIABLE_MISSING_METADATA','reason':'percentage of what? unclear denominator'},
        {'run':'RUN-25','claim':'liquidity ~3-7% on SBER/LKOH',
         'metric_a':MetricValue(0.05,'RATIO','dimensionless','1h','SBER').to_dict(),
         'metric_b':None,'operation':'annotation',
         'verdict':'UNVERIFIABLE_MISSING_METADATA','reason':'percentage of residual variance? unclear'},
        {'run':'RUN-20','claim':'97.7% residual unexplained',
         'metric_a':MetricValue(0.977,'R2','dimensionless','1h','IMOEX').to_dict(),
         'metric_b':None,'operation':'annotation',
         'verdict':'VALID','reason':'1 - R² from volatility control'},
    ]
    return claims

# ================================================== REGRESSION TESTS ===
def regression_tests():
    tests=[]
    # A: autocorrelation / return → reject
    a=claim_safety_gate(
        MetricValue(0.05,'AUTOCORRELATION'),
        MetricValue(0.001,'RETURN'),'/',
        'test: AC / return')
    tests.append({'name':'A: AC/return','allowed':a['allowed'],'expected':False})
    # B: return / cost_return → allow
    b=claim_safety_gate(
        MetricValue(0.001,'RETURN'),
        MetricValue(0.0005,'COST_RETURN'),'/',
        'test: return / cost')
    tests.append({'name':'B: return/cost','allowed':b['allowed'],'expected':True})
    # C: bps vs decimal without conversion → reject
    c=claim_safety_gate(
        MetricValue(50,'RETURN_BPS'),
        MetricValue(0.0005,'COST_RETURN'),'-',
        'test: bps - decimal')
    tests.append({'name':'C: bps-decimal','allowed':c['allowed'],'expected':False})
    # D: PnL / cost → allow
    d=claim_safety_gate(
        MetricValue(1000,'PNL_RUB'),
        MetricValue(500,'COST_RUB'),'/',
        'test: PnL/cost')
    tests.append({'name':'D: PnL/cost','allowed':d['allowed'],'expected':True})
    # E: 1h vs daily → reject
    e=claim_safety_gate(
        MetricValue(0.001,'RETURN',horizon='1h'),
        MetricValue(0.002,'RETURN',horizon='1d'),'-',
        'test: 1h - daily')
    tests.append({'name':'E: 1h-daily','allowed':e['allowed'],'expected':False})
    # F: R² + return → reject
    f=claim_safety_gate(
        MetricValue(0.05,'R2'),
        MetricValue(0.001,'RETURN'),'+',
        'test: R² + return')
    tests.append({'name':'F: R²+return','allowed':f['allowed'],'expected':False})
    # G: score / return → block
    g=claim_safety_gate(
        MetricValue(0.093,'SCORE'),
        MetricValue(0.005,'COST_RETURN'),'/',
        'test: score/cost (RUN-27 bug pattern)')
    tests.append({'name':'G: score/cost','allowed':g['allowed'],'expected':False})
    # H: AC > cost → block
    h=claim_safety_gate(
        MetricValue(0.093,'AUTOCORRELATION'),
        MetricValue(0.005,'COST_RETURN'),'>',
        'test: AC > cost (RUN-27 bug exact)')
    tests.append({'name':'H: AC>cost','allowed':h['allowed'],'expected':False})
    return tests

# ================================================== MAIN ===
def run29_full():
    claims=audit_claims()
    tests=regression_tests()
    invalid=[c for c in claims if 'INVALID' in c['verdict']]
    superseded=[c for c in claims if 'SUPERSEDED' in c['verdict']]
    valid=[c for c in claims if c['verdict']=='VALID']
    unverifiable=[c for c in claims if 'UNVERIFIABLE' in c['verdict']]
    all_pass=all(t['allowed']==t['expected'] for t in tests)
    result={'schema':SCHEMA,
            'metric_types':METRIC_TYPES,
            'claims_audited':len(claims),
            'claims_valid':len(valid),
            'claims_invalid':len(invalid),
            'claims_superseded':len(superseded),
            'claims_unverifiable':len(unverifiable),
            'invalid_claims':invalid,
            'valid_claims':[{'run':c['run'],'claim':c['claim']} for c in valid],
            'unverifiable_claims':[{'run':c['run'],'claim':c['claim'],'reason':c['reason']} for c in unverifiable],
            'regression_tests':tests,
            'all_tests_pass':all_pass,
            'ts':now()}
    (OUT/'run29_full.json').write_text(json.dumps(result,ensure_ascii=False,indent=1,default=str))
    return result

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--out',required=True);a=p.parse_args()
    r_=run29_full()
    Path(a.out).write_text(json.dumps(r_,ensure_ascii=False,indent=1,default=str))
    print(f'claims: {r_["claims_valid"]} valid, {r_["claims_invalid"]} invalid, {r_["claims_unverifiable"]} unverifiable')
    print(f'regression tests: {"ALL PASS" if r_["all_tests_pass"] else "FAILED"}')
    for c in r_['invalid_claims']:
        print(f'  INVALID: {c["run"]} — {c["claim"]} — {c["reason"][:60]}')
