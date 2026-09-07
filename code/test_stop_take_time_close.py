from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CODE_DIR = ROOT / "code"
for path in (str(ROOT), str(CODE_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

from core import analytics as an  # noqa: E402
from core import engine as core_engine  # noqa: E402
from synthetic_close_fixtures import (  # noqa: E402
    CloseEngineFixture,
    scenario_gazp_take_scaled,
    scenario_lkoh_stop,
    scenario_sber_time,
)


@pytest.mark.parametrize(
    "scenario_factory,expected_reason,expected_close_px",
    [
        (scenario_lkoh_stop, "stop", 42548.0),
        (scenario_gazp_take_scaled, "take", 85.78),
        (scenario_sber_time, "time", 250.75),
    ],
)
def test_process_slot_closes_by_stop_take_time(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scenario_factory, expected_reason: str, expected_close_px: float) -> None:
    scenario = scenario_factory()
    fixture = CloseEngineFixture(core_engine.Engine, scenario, tmp_path / f"{scenario.ticker}_{scenario.reason}.db")

    events, portfolio, slot = fixture.run(fill_price=scenario.broker_fill_price)

    assert any(f"CLOSE({expected_reason})" in ev for ev in events), events
    assert slot["open_position"] is None
    assert slot["n_trades"] == 1
    assert slot["pnl_rub"] != 0

    trade_row = fixture.engine.db.execute(
        "SELECT exit_price, pnl_rub, exit_reason, status FROM trades WHERE id = ?",
        (1,),
    ).fetchone()
    assert trade_row is not None
    exit_price, pnl_rub, exit_reason, status = trade_row
    assert exit_reason == expected_reason
    assert status == "closed"
    assert exit_price == pytest.approx(expected_close_px)

    if expected_reason == "stop":
        assert pnl_rub == pytest.approx((expected_close_px - 42568.0) * 1.0)
    elif expected_reason == "take":
        assert pnl_rub == pytest.approx((85.78 - 82.78) * 1.0)
    else:
        assert pnl_rub == pytest.approx(250.75 - 250.0)

    order_row = fixture.engine.db.execute(
        "SELECT side, qty, price FROM orders ORDER BY rowid DESC LIMIT 1",
    ).fetchone()
    assert order_row is not None
    assert order_row[2] == pytest.approx(expected_close_px)

    assert portfolio["slots"]["slot1"]["open_position"] is None
    assert portfolio["slots"]["slot1"]["n_trades"] == 1
