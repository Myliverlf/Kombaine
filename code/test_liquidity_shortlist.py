import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "code"))

from liquidity_shortlist import build_liquidity_shortlist


@pytest.fixture
def candidates():
    return [
        {"ticker": "BR", "volume_15m": 180000, "volume_1h": 650000, "spread_bps": 3.5, "bars_15m": 64, "bars_1h": 24},
        {"ticker": "LKOH", "volume_15m": 250000, "volume_1h": 720000, "spread_bps": 2.2, "bars_15m": 64, "bars_1h": 24},
        {"ticker": "RI", "volume_15m": 999999, "volume_1h": 999999, "spread_bps": 1.0, "bars_15m": 64, "bars_1h": 24},
    ]


class TestLiquidityShortlist:
    def test_limits_and_ri_exclusion(self, candidates):
        result = build_liquidity_shortlist(candidates, limit=20)
        assert result["metrics"]["kept"] == 2
        assert result["metrics"]["ri_excluded"] is True
        assert all(row["ticker"] != "RI" for row in result["shortlist"])

    def test_sorts_by_liquidity(self, candidates):
        result = build_liquidity_shortlist(candidates, limit=2)
        tickers = [row["ticker"] for row in result["shortlist"]]
        assert tickers[0] == "LKOH"

    def test_missing_ticker_rejected(self):
        result = build_liquidity_shortlist([{"volume_15m": 1}], limit=20)
        assert result["metrics"]["excluded"] == 1
        assert result["excluded"][0]["reject_reason"] == "missing_ticker"
