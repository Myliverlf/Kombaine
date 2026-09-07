import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "code"))

from multi_tf_data_gate import validate_multi_tf_data, gate_multi_tf_candidates


@pytest.fixture
def good_rows():
    return [{"time": f"2025-01-01 00:{i:02d}:00", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1} for i in range(25)]


@pytest.fixture
def data_by_ticker(good_rows):
    return {
        "BR": {"15m": {"rows": good_rows}, "1h": {"rows": good_rows[:6]}},
        "LKOH": {"15m": {"rows": good_rows}, "1h": {"rows": good_rows[:6]}},
        "RI": {"15m": {"rows": good_rows}, "1h": {"rows": good_rows[:6]}},
    }


class TestMultiTfDataGate:
    def test_passes_for_complete_data(self, data_by_ticker):
        result = validate_multi_tf_data({"ticker": "BR"}, data_by_ticker["BR"])
        assert result["passed"] is True
        assert result["timeframes"]["15m"]["bars"] >= 20
        assert result["timeframes"]["1h"]["bars"] >= 5

    def test_rejects_ri(self, data_by_ticker):
        result = validate_multi_tf_data({"ticker": "RI"}, data_by_ticker["RI"])
        assert result["passed"] is False
        assert result["reason"] == "excluded_ticker:RI"

    def test_gates_candidates(self, data_by_ticker):
        result = gate_multi_tf_candidates([{"ticker": "BR"}, {"ticker": "RI"}], data_by_ticker)
        assert result["summary"]["admitted"] == 1
        assert result["summary"]["rejected"] == 1
        assert result["admitted"][0]["ticker"] == "BR"
