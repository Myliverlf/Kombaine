from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CODE_DIR = ROOT / "code"
for path in (str(ROOT), str(CODE_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

from core.engine import Engine  # noqa: E402
from synthetic_close_fixtures import (  # noqa: E402
    CloseEngineFixture,
    scenario_gazp_take_scaled,
    scenario_lkoh_stop,
    scenario_sber_time,
)


def run() -> dict:
    scenarios = [scenario_lkoh_stop(), scenario_gazp_take_scaled(), scenario_sber_time()]
    report = {"ok": True, "results": []}
    for scenario in scenarios:
        fixture = CloseEngineFixture(Engine, scenario, ROOT / f".tmp_{scenario.ticker.lower()}_{scenario.reason}.db")
        events, portfolio, slot = fixture.run(fill_price=scenario.broker_fill_price)
        trade_row = fixture.engine.db.execute(
            "SELECT exit_price, pnl_rub, exit_reason FROM trades WHERE id = 1",
        ).fetchone()
        result = {
            "ticker": scenario.ticker,
            "reason": scenario.reason,
            "events": events,
            "closed": slot["open_position"] is None and portfolio["slots"]["slot1"]["open_position"] is None,
            "trade_row": list(trade_row) if trade_row else None,
        }
        report["results"].append(result)
        if not result["closed"] or not trade_row or trade_row[2] != scenario.reason:
            report["ok"] = False
    return report


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))
