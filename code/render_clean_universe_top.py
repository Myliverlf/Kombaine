#!/usr/bin/env python3
from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path('/root/prop-desk/strategy_combine')
FUT = pathlib.Path('/root/prop-desk/futures_lab')
REPORT = ROOT / 'reports' / 'strategy_architect'
LATEST = REPORT / 'latest.md'

UNIVERSE = {'GAZP', 'SBER', 'LKOH'}
text = LATEST.read_text(encoding='utf-8')
rows = []
collect = False
for line in text.splitlines():
    if line.startswith('| ticker | tf | strategy | bucket |'):
        collect = True
        continue
    if collect:
        if not line.startswith('|'):
            break
        if line.startswith('|---'):
            continue
        parts = [p.strip() for p in line.strip('|').split('|')]
        if len(parts) >= 16 and parts[0] in UNIVERSE:
            rows.append({
                'ticker': parts[0], 'tf': parts[1], 'strategy': parts[2],
                'trades': parts[4], 'wr': parts[6], 'pnl': parts[7], 'pf': parts[8], 'dd': parts[9], 'decision': parts[15]
            })

out_json = REPORT / 'clean_universe_top.json'
out_md = REPORT / 'clean_universe_top.md'
out_json.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')
lines = ['# Clean universe top', '', '| ticker | tf | strategy | trades | WR% | PnL | PF | DD | decision |', '|---|---:|---|---:|---:|---:|---:|---:|---|']
for r in rows[:10]:
    lines.append(f"| {r['ticker']} | {r['tf']} | {r['strategy']} | {r['trades']} | {r['wr']} | {r['pnl']} | {r['pf']} | {r['dd']} | {r['decision']} |")
out_md.write_text('\n'.join(lines), encoding='utf-8')
print(out_md)
