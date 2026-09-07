import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "code"))

from smoke_promoter import smoke_pass, promote_after_smoke


@pytest.fixture
def smoke_report_pass():
    return {"status": "PASS", "live_orders": 0, "broker_calls": False}


@pytest.fixture
def smoke_report_fail():
    return {"status": "FAIL", "live_orders": 0, "broker_calls": False}


class TestSmokePromoter:
    def test_smoke_pass_detected(self, smoke_report_pass):
        assert smoke_pass(smoke_report_pass) is True

    def test_blocks_when_smoke_fails(self, smoke_report_fail):
        result = promote_after_smoke([{"ticker": "BR"}], smoke_report_fail)
        assert result["smoke_ok"] is False
        assert result["blocked"][0]["promotion_reason"] == "smoke_not_passed"

    def test_promotes_with_limits(self, smoke_report_pass):
        result = promote_after_smoke([{"ticker": "BR"}, {"ticker": "LKOH"}, {"ticker": "SBER"}, {"ticker": "GAZP"}], smoke_report_pass, max_live_slots=3)
        assert result["smoke_ok"] is True
        assert len(result["promoted"]) == 3
        assert all(row["contracts"] == 1 for row in result["promoted"])
