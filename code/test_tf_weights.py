#!/usr/bin/env python3
import sys, json
sys.path.insert(0, 'code')
from strategy_architect_autopilot import compute_tf_weights, tf_efficiency_bonus
import strategy_architect_autopilot as sa
from pathlib import Path

base = Path('reports/strategy_architect')
d = json.loads((base / 'cycle_20260824_164348.json').read_text())
rows = d.get('top', [])
weights = compute_tf_weights(rows)
print('TF weights:', json.dumps(weights, indent=2))

# Set global so tf_efficiency_bonus can read it
sa.TF_GLOBAL_WEIGHTS = weights

for r in rows:
    tf = r.get('timeframe')
    bonus = tf_efficiency_bonus(r)
    print(f'  {r["ticker"]} {tf} {r["strategy"]}: tf_bonus={bonus:+.0f}')
