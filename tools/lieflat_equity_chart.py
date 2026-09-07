
#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from render_combined_equity_real import main as render_combined_equity_real_main

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--cycle', default=str(Path('/root/prop-desk/strategy_combine/reports/strategy_architect/cycle_20260902_033822.json')))
    ap.add_argument('--output', default=str(Path('/root/prop-desk/strategy_combine/reports/strategy_architect/lieflat_combined_equity.png')))
    ap.add_argument('--top-n', type=int, default=6)
    ap.add_argument('--cash', type=float, default=20000.0)
    ap.add_argument('--risk-pct', type=float, default=2.7)
    args = ap.parse_args()

    # Standardized entrypoint for Lieflat-compatible combined equity charts.
    # Reuses the validated render path while keeping a single chart-per-question contract.
    import sys
    sys.argv = [
        'render_combined_equity_real.py',
        '--cycle', args.cycle,
        '--output', args.output,
        '--top-n', str(args.top_n),
        '--cash', str(args.cash),
        '--risk-pct', str(args.risk_pct),
    ]
    return render_combined_equity_real_main()

if __name__ == '__main__':
    raise SystemExit(main())
