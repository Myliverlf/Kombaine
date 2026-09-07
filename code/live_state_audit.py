#!/usr/bin/env python3
import json, sqlite3, pathlib, sys
root=pathlib.Path('/root/prop-desk/strategy_combine')
sys.path.insert(0, str(root))
from core.engine import Engine
eng=Engine(); broker=eng.fetch_broker_positions()
p=json.load(open(root/'state/portfolio.json'))
issues=[]
for sid,s in p.get('slots',{}).items():
    pos=s.get('open_position')
    if float(s.get('go_rub') or 0)<=0: issues.append((sid,'GO<=0'))
    if pos:
        ep=float(pos.get('entry_price') or 0); sl=float(s.get('sl_px') or 0); tp=float(s.get('tp_px') or 0)
        if s.get('ticker')=='GAZP' and ep>1000: issues.append((sid,'GAZP_SCALE_BAD'))
        if s.get('ticker')=='LKOH' and ep<1000: issues.append((sid,'LKOH_SCALE_BAD'))
        if pos.get('direction')=='LONG' and not (sl<ep<tp): issues.append((sid,'LONG_SLTP_BAD',sl,ep,tp))
        if pos.get('direction')=='SHORT' and not (sl>ep>tp): issues.append((sid,'SHORT_SLTP_BAD',sl,ep,tp))
con=sqlite3.connect(root/'analytics.db')
open_trades=list(con.execute("select id,slot_id,ticker,entry_price,status from trades where status='open'"))
orphans=[r for r in open_trades if r[1] not in set(p.get('slots',{}))]
print(json.dumps({'broker':broker,'portfolio':{'halted':p.get('halted'),'reason':p.get('halt_reason'),'slots':len(p.get('slots',{})),'open':sum(1 for s in p.get('slots',{}).values() if s.get('open_position'))},'issues':issues,'open_trades':open_trades,'orphans':orphans},ensure_ascii=False,default=str,indent=2))
raise SystemExit(3 if issues or orphans else 0)
