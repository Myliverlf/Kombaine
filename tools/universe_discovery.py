#!/usr/bin/env python3
"""Read-only Tinkoff futures universe discovery for Strategy Architect.

No orders, no broker mutations. Lists liquid-looking active futures roots and
writes a data acquisition plan for 20-asset research universe.
"""
from __future__ import annotations

import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROP_ROOT = PROJECT_ROOT.parent
FUTURES_LAB = PROP_ROOT / "futures_lab"
DATA_ROOT = FUTURES_LAB / "artifacts" / "tinkoff_futures_data"
REPORT_DIR = PROJECT_ROOT / "reports" / "strategy_architect"
CONFIG_PATH = PROJECT_ROOT / "config/strategy_architect_24x7.json"

sys.path.insert(0, str(FUTURES_LAB))

from tinkoff.invest import Client  # type: ignore  # noqa:E402
from tinkoff.invest.constants import INVEST_GRPC_API  # type: ignore  # noqa:E402
from futures_lab import load_token  # type: ignore  # noqa:E402

BASELINE = ["BR", "GAZP", "LKOH", "SBER", "Si"]
EXCLUDED = {"RI"}
PREFERRED = [
    "BR", "Si", "GAZP", "LKOH", "SBER", "GOLD", "SILV", "CNY", "EURRUB", "USDRUB",
    "MIX", "IMOEX", "NG", "PLD", "PLT", "ALRS", "NVTK", "ROSN", "GMKN", "VTBR",
    "MGNT", "AFLT", "TATN", "TRNF", "CHMF", "NLMK", "MOEX", "IRAO", "SNGS", "YDEX",
]


def root_from_ticker(ticker: str) -> str:
    # Tinkoff futures tickers vary by expiry; keep alphabetic root plus common RUB names.
    t = ticker.upper()
    for known in PREFERRED:
        if t == known or t.startswith(known):
            return known
    # fallback: strip trailing digits/month codes conservatively
    root = "".join(ch for ch in t if ch.isalpha())
    return root or t


def csv_status(root: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for tf in ["15m", "1h"]:
        p = DATA_ROOT / f"{root}_60d_{tf}_continuous.csv"  # keep 60d as readiness baseline; long-history files live alongside it
        if p.exists():
            try:
                rows = max(0, sum(1 for _ in p.open("r", encoding="utf-8")) - 1)
            except OSError:
                rows = 0
            out[tf] = {"exists": True, "rows": rows, "path": str(p)}
        else:
            out[tf] = {"exists": False, "rows": 0, "path": str(p)}
    out["ready"] = bool(out["15m"]["exists"] and out["1h"]["exists"] and out["15m"]["rows"] >= 300 and out["1h"]["rows"] >= 300)
    return out


def discover(limit: int = 20) -> Dict[str, Any]:
    token = load_token()
    now = datetime.now(timezone.utc)
    roots: Dict[str, Dict[str, Any]] = {}
    with Client(token, target=INVEST_GRPC_API) as client:
        resp = client.instruments.futures(instrument_status=1)
        grouped: Dict[str, List[Any]] = defaultdict(list)
        for inst in resp.instruments:
            exp = getattr(inst, "expiration_date", None) or getattr(inst, "last_trade_date", None)
            if exp is not None and exp < now:
                continue
            root = root_from_ticker(getattr(inst, "ticker", ""))
            if root in EXCLUDED or not root:
                continue
            grouped[root].append(inst)
        for root, items in grouped.items():
            items.sort(key=lambda x: getattr(x, "expiration_date", None) or datetime.max.replace(tzinfo=timezone.utc))
            front = items[0]
            roots[root] = {
                "root": root,
                "front_ticker": getattr(front, "ticker", root),
                "name": getattr(front, "name", root),
                "class_code": getattr(front, "class_code", ""),
                "expiration_date": str(getattr(front, "expiration_date", "")),
                "chain_len": len(items),
                "csv": csv_status(root),
            }
    ordered = []
    for root in PREFERRED:
        if root in roots:
            ordered.append(roots[root])
    for root in sorted(roots):
        if root not in {r["root"] for r in ordered}:
            ordered.append(roots[root])
    selected = ordered[:limit]
    ready = [r for r in selected if r["csv"]["ready"]]
    missing = [r for r in selected if not r["csv"]["ready"]]
    return {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "target_size": limit,
        "selected_roots": [r["root"] for r in selected],
        "ready_roots": [r["root"] for r in ready],
        "missing_roots": [r["root"] for r in missing],
        "excluded": sorted(EXCLUDED),
        "selected": selected,
        "download_plan": [
            {
                "root": r["root"],
                "front_ticker": r["front_ticker"],
                "needed": [tf for tf in ["15m", "1h"] if not r["csv"][tf]["exists"] or r["csv"][tf]["rows"] < 300],
                "command_template": f"cd {FUTURES_LAB} && python3 futures_lab.py download --ticker {r['root']} --days 60 --interval <15m|1h> --continuous --out {DATA_ROOT}/{r['root']}_60d_<tf>_continuous.csv",
            }
            for r in missing
        ],
        "live_orders": 0,
    }


def render_md(payload: Dict[str, Any]) -> str:
    lines = [
        "# Universe discovery — Strategy Architect",
        "",
        f"- target: {payload['target_size']} assets",
        f"- ready: {len(payload['ready_roots'])}/{payload['target_size']} ({', '.join(payload['ready_roots'])})",
        f"- missing data: {len(payload['missing_roots'])} ({', '.join(payload['missing_roots'])})",
        f"- excluded: {', '.join(payload['excluded'])}",
        f"- live_orders: {payload['live_orders']}",
        "",
        "| root | front | 15m rows | 1h rows | ready |",
        "|---|---|---:|---:|---|",
    ]
    for r in payload["selected"]:
        lines.append(
            f"| {r['root']} | {r['front_ticker']} | {r['csv']['15m']['rows']} | {r['csv']['1h']['rows']} | {r['csv']['ready']} |"
        )
    lines.append("")
    lines.append("## Download plan")
    for item in payload["download_plan"]:
        lines.append(f"- {item['root']}: need {', '.join(item['needed'])}")
    return "\n".join(lines)


def main() -> int:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    payload = discover(limit=20)
    (REPORT_DIR / "universe_discovery.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (REPORT_DIR / "universe_discovery.md").write_text(render_md(payload), encoding="utf-8")
    print(json.dumps({"ready": len(payload["ready_roots"]), "target": payload["target_size"], "missing": payload["missing_roots"], "live_orders": 0}, ensure_ascii=False))
    print(REPORT_DIR / "universe_discovery.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
