#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lieflat_equity_chart import main as render_chart


def build_report(cycle_path: Path, out_html: Path, top_n: int, cash: float, risk_pct: float) -> None:
    data = json.loads(cycle_path.read_text(encoding='utf-8'))
    title = 'Lieflat combined equity report'
    chart_png = out_html.with_suffix('.png')
    sys.argv = [
        'lieflat_equity_chart.py',
        '--cycle', str(cycle_path),
        '--output', str(chart_png),
        '--top-n', str(top_n),
        '--cash', str(cash),
        '--risk-pct', str(risk_pct),
    ]
    render_chart()

    top_rows = data.get('top', [])[:top_n]
    rows_html = '\n'.join(
        f"<tr><td>{i+1}</td><td>{r.get('ticker','')}</td><td>{r.get('strategy','')}</td><td>{r.get('timeframe','')}</td><td>{r.get('trades',0)}</td><td>{float(r.get('rank_score',0) or 0):.2f}</td></tr>"
        for i, r in enumerate(top_rows)
    )
    html = f'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
body {{ font-family: Inter, system-ui, sans-serif; background: #f0efeb; color: #1c1c1a; margin: 0; padding: 32px; }}
.container {{ max-width: 1400px; margin: 0 auto; }}
h1 {{ font-size: 32px; margin: 0 0 8px; }}
.meta {{ color: #6a6963; margin-bottom: 20px; }}
.card {{ background: #fff; border-radius: 24px; padding: 24px; box-shadow: 0 1px 0 rgba(0,0,0,.06); }}
img {{ max-width: 100%; display: block; border-radius: 16px; }}
table {{ width: 100%; border-collapse: collapse; margin-top: 24px; }}
th, td {{ text-align: left; padding: 10px 8px; border-bottom: 1px solid #ddd; }}
th {{ color: #6a6963; font-weight: 600; }}
.kpi {{ display: flex; gap: 16px; flex-wrap: wrap; margin: 12px 0 24px; }}
.kpi div {{ background: #fff; border-radius: 18px; padding: 14px 16px; min-width: 180px; }}
.kpi b {{ display: block; font-size: 12px; color: #6a6963; margin-bottom: 4px; }}
.kpi span {{ font-size: 20px; font-weight: 700; }}
</style>
</head>
<body>
<div class="container">
  <h1>{title}</h1>
  <div class="meta">cycle: {cycle_path.name} · capital: {cash:.0f} RUB · risk: {risk_pct:.2f}%/trade</div>
  <div class="kpi">
    <div><b>Chart</b><span>{chart_png.name}</span></div>
    <div><b>Top N</b><span>{top_n}</span></div>
    <div><b>Rows total</b><span>{data.get('rows_total', 'n/a')}</span></div>
  </div>
  <div class="card"><img src="{chart_png.name}" alt="Lieflat combined equity chart"></div>
  <table>
    <thead><tr><th>#</th><th>Ticker</th><th>Strategy</th><th>TF</th><th>Trades</th><th>Rank</th></tr></thead>
    <tbody>{rows_html}</tbody>
  </table>
</div>
</body>
</html>'''
    out_html.write_text(html, encoding='utf-8')


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--cycle', required=True)
    ap.add_argument('--output', required=True)
    ap.add_argument('--top-n', type=int, default=6)
    ap.add_argument('--cash', type=float, default=20000.0)
    ap.add_argument('--risk-pct', type=float, default=2.7)
    args = ap.parse_args()
    out_html = Path(args.output)
    out_html.parent.mkdir(parents=True, exist_ok=True)
    build_report(Path(args.cycle), out_html, args.top_n, args.cash, args.risk_pct)
    print(str(out_html))
    print(str(out_html.with_suffix('.png')))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
