import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "code"))

from liquid_risk_scorecard import build_liquid_risk_scorecard


@pytest.fixture
def candidates():
    return [
        {"ticker": "LKOH", "volume_15m": 250000, "volume_1h": 720000, "spread_bps": 2.2, "bars_15m": 64, "bars_1h": 24, "win_rate": 0.56, "avg_win": 210, "avg_loss": 120},
        {"ticker": "BR", "volume_15m": 180000, "volume_1h": 650000, "spread_bps": 3.5, "bars_15m": 64, "bars_1h": 24, "win_rate": 0.51, "avg_win": 170, "avg_loss": 140},
        {"ticker": "RI", "volume_15m": 999999, "volume_1h": 999999, "spread_bps": 1.0, "bars_15m": 64, "bars_1h": 24, "win_rate": 0.90, "avg_win": 500, "avg_loss": 10},
    ]


@pytest.fixture
def data_by_ticker():
    rows = [{"time": f"2025-01-01 00:{i:02d}:00", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1} for i in range(25)]
    return {
        "LKOH": {"15m": {"rows": rows}, "1h": {"rows": rows[:6]}},
        "BR": {"15m": {"rows": rows}, "1h": {"rows": rows[:6]}},
        "RI": {"15m": {"rows": rows}, "1h": {"rows": rows[:6]}},
    }


@pytest.fixture
def smoke_report():
    return {"status": "PASS", "live_orders": 0, "broker_calls": False}


class TestLiquidRiskScorecard:
    def test_builds_scorecard_and_excludes_ri(self, candidates, data_by_ticker, smoke_report):
        result = build_liquid_risk_scorecard(candidates, data_by_ticker, smoke_report)
        assert result["metrics"]["ri_excluded"] is True
        assert result["metrics"]["live_orders"] == 0
        assert all(row["ticker"] != "RI" for row in result["shortlist"])

    def test_scorecard_has_metrics(self, candidates, data_by_ticker, smoke_report):
        result = build_liquid_risk_scorecard(candidates, data_by_ticker, smoke_report)
        assert result["metrics"]["allocator_score_avg"] >= 0.0
        assert result["metrics"]["risk_proxy_avg"] >= 0.0

    def test_max_live_slots_and_contracts(self, candidates, data_by_ticker, smoke_report):
        result = build_liquid_risk_scorecard(candidates + candidates, data_by_ticker, smoke_report, limit=20, max_live_slots=3, max_contracts_per_entry=1)
        promoted = result["smoke"]["promoted"]
        assert len(promoted) <= 3
        assert all(row["contracts"] == 1 for row in promoted)
