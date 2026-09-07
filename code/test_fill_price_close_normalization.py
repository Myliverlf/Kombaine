from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CODE_DIR = ROOT / "code"
for path in (str(ROOT), str(CODE_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

from core.engine import Engine  # noqa: E402
from synthetic_close_fixtures import CloseEngineFixture, scenario_gazp_take_scaled, scenario_lkoh_stop  # noqa: E402


def test_normalize_fill_price_scales_gazp_close_quote() -> None:
    eng = Engine()
    assert eng.normalize_fill_price("GAZP", 8278.0, 82.78) == pytest.approx(82.78)
    assert eng.normalize_fill_price("GAZP", 82.78, 8278.0) == pytest.approx(8278.0)


def test_close_fill_price_is_normalized_before_pnl(tmp_path: Path) -> None:
    fixture = CloseEngineFixture(Engine, scenario_gazp_take_scaled(), tmp_path / "gazp_close.db")
    events, portfolio, slot = fixture.run(fill_price=8278.0)

    assert any("CLOSE(take)" in ev for ev in events)
    row = fixture.engine.db.execute("SELECT exit_price, pnl_rub FROM trades WHERE id = 1").fetchone()
    assert row is not None
    exit_price, pnl_rub = row
    assert exit_price == pytest.approx(82.78)
    assert pnl_rub == pytest.approx((82.78 - 82.78) * 1.0)
    assert slot["open_position"] is None
    assert portfolio["slots"]["slot1"]["open_position"] is None


def test_close_fill_price_keeps_lkoh_scale(tmp_path: Path) -> None:
    fixture = CloseEngineFixture(Engine, scenario_lkoh_stop(), tmp_path / "lkoh_close.db")
    events, _, slot = fixture.run(fill_price=42542.0)

    assert any("CLOSE(stop)" in ev for ev in events)
    row = fixture.engine.db.execute("SELECT exit_price FROM trades WHERE id = 1").fetchone()
    assert row is not None
    assert row[0] == pytest.approx(42542.0)
    assert slot["open_position"] is None
