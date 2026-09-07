"""Тесты regime_gate — unit tests, fixtures ≥4, dry-run, без live-ордеров.

Каждый тест маппится на критерий приёмки task.md:
  - RI VETO при любом гейте
  - chop блокирует directional, пропускает mean-revert (None direction)
  - vol-high → cap_contracts=1
  - stale/нет snapshot → pass-through admit=True reason=no_regime_data
  - детерминизм классификации
  - нет broker-импортов
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

# Ensure code/ is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from regime_gate import (
    ADX_CHOP_MAX,
    ADX_TREND_MIN,
    SNAPSHOT_MAX_AGE_S,
    classify_ticker,
    admit,
)


# ─── Fixtures ──────────────────────────────────────────────────────────

@pytest.fixture
def fresh_snapshot():
    """Свежий regime snapshot (ts = now), с трендовыми/чоповыми тикерами."""
    now = datetime.now(timezone.utc).isoformat()
    return {
        "ts": now,
        "tickers": {
            "GAZP": {"adx": 35.3, "direction": "up", "regime": "trend", "atr_pct": 0.299},
            "SBER": {"adx": 18.0, "direction": "down", "regime": "range", "atr_pct": 0.183},
            "BR": {"adx": 23.1, "direction": "up", "regime": "trend", "atr_pct": 0.279},
            "LKOH": {"adx": 15.0, "direction": "down", "regime": "range", "atr_pct": 0.242},
            "Si": {"adx": 27.0, "direction": "up", "regime": "trend", "atr_pct": 0.097},
        },
        "bias": "neutral",
        "ups": 3,
        "downs": 2,
        "trend_cnt": 3,
    }


@pytest.fixture
def stale_snapshot():
    """Протухший snapshot (ts > 6h назад)."""
    old_ts = (datetime.now(timezone.utc) - timedelta(hours=7)).isoformat()
    return {
        "ts": old_ts,
        "tickers": {
            "GAZP": {"adx": 35.3, "direction": "up", "regime": "trend", "atr_pct": 0.299},
        },
        "bias": "long",
        "ups": 1,
        "downs": 0,
        "trend_cnt": 1,
    }


@pytest.fixture
def tmp_cfg():
    """Базовый конфиг для тестов."""
    return {
        "excluded": ["RI"],
        "max_contracts_per_entry": 1,
    }


@pytest.fixture
def candidate_directional():
    """Направленный кандидат (LONG)."""
    return {"ticker": "GAZP", "direction": "LONG", "contracts_requested": 1}


@pytest.fixture
def candidate_ri():
    """Кандидат RI (excluded)."""
    return {"ticker": "RI", "direction": "LONG", "contracts_requested": 1}


@pytest.fixture
def candidate_chop_ok():
    """Кандидат без направления (mean-revert), в чоповом тикере."""
    return {"ticker": "LKOH", "direction": None, "contracts_requested": 1}


@pytest.fixture
def candidate_multi_contracts():
    """Кандидат с несколькими контрактами."""
    return {"ticker": "GAZP", "direction": "LONG", "contracts_requested": 3}


# ─── Tests ─────────────────────────────────────────────────────────────

class TestRiVeto:
    """RI excluded VETO при любом гейте (F1, F4 analysis.md)."""

    def test_ri_veto_with_gate_on(self, candidate_ri, fresh_snapshot, tmp_cfg):
        result = admit(candidate_ri, fresh_snapshot, tmp_cfg)
        assert result["admit"] is False
        assert result["reason"] == "excluded"
        assert result["cap_contracts"] == 0

    def test_ri_veto_without_snapshot(self, candidate_ri, tmp_cfg):
        result = admit(candidate_ri, None, tmp_cfg)
        assert result["admit"] is False
        assert result["reason"] == "excluded"

    def test_ri_veto_in_high_vol(self, candidate_ri, fresh_snapshot, tmp_cfg):
        """RI remains excluded even in high-vol scenario."""
        result = admit(candidate_ri, fresh_snapshot, tmp_cfg, vol_pctl=95.0)
        assert result["admit"] is False
        assert result["reason"] == "excluded"


class TestChopRegime:
    """Chop regime blocks directional, passes mean-revert."""

    def test_chop_blocks_directional(self, fresh_snapshot, tmp_cfg):
        """SBER has ADX=18 (< 20 = chop), direction=LONG → denied."""
        cand = {"ticker": "SBER", "direction": "LONG", "contracts_requested": 1}
        result = admit(cand, fresh_snapshot, tmp_cfg)
        assert result["admit"] is False
        assert result["reason"] == "chop_regime_directional"

    def test_chop_passes_directionless(self, fresh_snapshot, tmp_cfg):
        """SBER chop, direction=None (mean-revert) → admitted."""
        cand = {"ticker": "SBER", "direction": None, "contracts_requested": 1}
        result = admit(cand, fresh_snapshot, tmp_cfg)
        assert result["admit"] is True
        assert result["regime_info"]["regime"] == "chop"

    def test_chop_deep_blocks_short(self, fresh_snapshot, tmp_cfg):
        """LKOH has ADX=15 (< 20 = chop), direction=SHORT → denied."""
        cand = {"ticker": "LKOH", "direction": "SHORT", "contracts_requested": 1}
        result = admit(cand, fresh_snapshot, tmp_cfg)
        assert result["admit"] is False
        assert result["reason"] == "chop_regime_directional"


class TestVolHigh:
    """High volatility caps contracts to 1."""

    def test_high_vol_caps_contracts(self, candidate_multi_contracts, fresh_snapshot, tmp_cfg):
        """GAZP has atr_pct=0.299; with vol_pctl=90 → high → cap_contracts=1."""
        result = admit(candidate_multi_contracts, fresh_snapshot, tmp_cfg, vol_pctl=90.0)
        assert result["admit"] is True
        assert result["reason"] == "high_vol_capped"
        assert result["cap_contracts"] == 1

    def test_high_vol_single_stays_one(self, fresh_snapshot, tmp_cfg):
        """Already 1 contract stays 1."""
        cand = {"ticker": "GAZP", "direction": "LONG", "contracts_requested": 1}
        result = admit(cand, fresh_snapshot, tmp_cfg, vol_pctl=90.0)
        assert result["admit"] is True
        assert result["cap_contracts"] == 1


class TestStaleSnapshot:
    """Stale/missing snapshot → pass-through."""

    def test_no_snapshot_pass_through(self, candidate_directional, tmp_cfg):
        result = admit(candidate_directional, None, tmp_cfg)
        assert result["admit"] is True
        assert result["reason"] == "no_regime_data"
        assert result["regime_info"] is None

    def test_stale_snapshot_pass_through(self, candidate_directional, stale_snapshot, tmp_cfg):
        result = admit(candidate_directional, stale_snapshot, tmp_cfg)
        assert result["admit"] is True
        assert result["reason"] == "stale_snapshot"
        assert result["regime_info"] is None

    def test_ticker_not_in_snapshot_pass_through(self, tmp_cfg):
        """Ticker not present in snapshot → no_regime_data."""
        snapshot = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "tickers": {"GAZP": {"adx": 30, "direction": "up", "regime": "trend", "atr_pct": 0.3}},
        }
        cand = {"ticker": "SBER", "direction": "LONG"}
        result = admit(cand, snapshot, tmp_cfg)
        assert result["admit"] is True
        assert result["reason"] == "no_regime_data"


class TestTrendAdmit:
    """Trend regime with matching direction → admitted."""

    def test_trend_admit(self, fresh_snapshot, tmp_cfg):
        """GAZP ADX=35.3 (trend), direction LONG, regime up → admitted."""
        cand = {"ticker": "GAZP", "direction": "LONG", "contracts_requested": 1}
        result = admit(cand, fresh_snapshot, tmp_cfg)
        assert result["admit"] is True
        assert result["reason"] == "admitted"
        assert result["regime_info"]["regime"] == "trend"

    def test_trend_vs_direction_admit(self, fresh_snapshot, tmp_cfg):
        """SBER is chop (ADX=18), but Si is trend (ADX=27) → Si LONG admitted."""
        cand = {"ticker": "Si", "direction": "LONG", "contracts_requested": 1}
        result = admit(cand, fresh_snapshot, tmp_cfg)
        assert result["admit"] is True
        assert result["regime_info"]["regime"] == "trend"


class TestClassifyDeterminism:
    """Determinism: same input → same output."""

    def test_classify_deterministic(self, fresh_snapshot):
        data = fresh_snapshot["tickers"]["GAZP"]
        c1 = classify_ticker(data, vol_pctl=50.0)
        c2 = classify_ticker(data, vol_pctl=50.0)
        assert c1 == c2

    def test_classify_thresholds(self):
        """ADX exactly at boundary."""
        at_trend = {"adx": ADX_TREND_MIN, "direction": "up", "atr_pct": 0.2}
        at_chop = {"adx": ADX_CHOP_MAX, "direction": "up", "atr_pct": 0.2}
        just_below = {"adx": ADX_CHOP_MAX - 0.1, "direction": "up", "atr_pct": 0.2}

        assert classify_ticker(at_trend)["regime"] == "trend"
        assert classify_ticker(at_chop)["regime"] == "transitional"
        assert classify_ticker(just_below)["regime"] == "chop"


class TestNoBrokerImports:
    """Модуль regime_gate не импортирует broker/client (patern F4 analysis.md)."""

    def test_no_broker_imports(self):
        gate_path = Path(__file__).resolve().parent / "regime_gate.py"
        content = gate_path.read_text()
        forbidden_imports = [
            "import tinkoff", "from tinkoff",
            "import broker", "from broker",
            "Client(", "post_order(", "place_order(",
        ]
        for pat in forbidden_imports:
            assert pat not in content, (
                f"regime_gate.py contains forbidden import/pattern: {pat}"
            )
