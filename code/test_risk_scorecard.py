"""Тесты risk scorecard — ≥3 pytest-фиксур, dry-run, без live-ордеров.

Каждый тест маппится на критерий приёмки task.md.
Фикстуры inline-dict + tmp_path + JSON fixtures; сеть не используется.
"""
import json
import math
import os
import time

import pytest

from risk_scorecard import (
    STATUS_OK,
    STATUS_REDUCE,
    STATUS_VETO,
    STATUS_WARN,
    STATUS_NEUTRAL,
    build_scorecard,
    calc_caps,
    calc_correlation,
    calc_drawdown,
    calc_exposure,
    calc_signal_age,
    calc_slots,
    check_excluded_tickers,
)


# ─── helpers ────────────────────────────────────────────────────────────

def _base_config(**overrides) -> dict:
    """Базовый конфиг, совпадающий с config.json."""
    defaults = {
        "go_budget_rub": 21281 * 50 / 100,  # 10640.5
        "delta_band_pct": 30,
        "deposit_rub": 21281,
        "portfolio_stop_drawdown_pct": 25,
        "signal_max_age_minutes": 16,
        "max_slots": 3,
        "max_contracts_per_entry": 1,
        "excluded": ["RI"],
    }
    defaults.update(overrides)
    return defaults


def _slot(ticker, go_rub=5000, pnl=0.0, peak_pnl=0.0,
          contracts=1, direction=None, stop_streak=0,
          signal_age_sec=300, entry_atr=100.0) -> dict:
    """Создать синтетический слот."""
    open_pos = None
    if direction:
        open_pos = {
            "direction": direction,
            "qty": contracts,
            "entry_price": 100.0,
            "entry_atr": entry_atr,
            "entry_ts": time.time() - signal_age_sec,
        }
    return {
        "ticker": ticker,
        "strategy": "test",
        "contracts": contracts,
        "go_rub": go_rub,
        "open_position": open_pos,
        "pnl_rub": pnl,
        "peak_pnl_rub": peak_pnl,
        "stop_streak": stop_streak,
        "last_signal_ts": time.time() - signal_age_sec,
        "n_trades": 5,
    }


# ─── Fixture 1: clean portfolio ────────────────────────────────────────

@pytest.fixture
def clean_portfolio():
    """2 слота, без просадок, сигнал свежий."""
    return {
        "slot_A_1": _slot("A", go_rub=3000, pnl=100, peak_pnl=100,
                          direction="LONG", signal_age_sec=120),
        "slot_B_1": _slot("B", go_rub=2000, pnl=50, peak_pnl=50,
                          direction="SHORT", signal_age_sec=200),
    }


# ─── Fixture 2: stressed portfolio ─────────────────────────────────────

@pytest.fixture
def stressed_portfolio():
    """3 слота, все в просадке, сигналы протухли."""
    return {
        "slot_X_1": _slot("X", go_rub=4000, pnl=-500, peak_pnl=0,
                          direction="LONG", stop_streak=2, signal_age_sec=1200),
        "slot_Y_1": _slot("Y", go_rub=3500, pnl=-300, peak_pnl=0,
                          direction="SHORT", signal_age_sec=1500),
        "slot_Z_1": _slot("Z", go_rub=3000, pnl=-100, peak_pnl=0,
                          direction="LONG", signal_age_sec=2000),
    }


# ─── Fixture 3: RI in portfolio ────────────────────────────────────────

@pytest.fixture
def ri_portfolio():
    """Один слот с RI (исключённый тикер)."""
    return {
        "slot_RI_1": _slot("RI", go_rub=5000, pnl=0,
                           direction="LONG", signal_age_sec=60),
    }


# ─── Fixture 4: empty portfolio ────────────────────────────────────────

@pytest.fixture
def empty_portfolio():
    """Пустой портфель."""
    return {}


# ─── Тесты ──────────────────────────────────────────────────────────────

class TestMonotonicRiskScore:
    """Критерий: PnL↑/risk↓ — ухудшение любой компоненты не уменьшает score."""

    def test_worse_pnl_increases_risk(self, clean_portfolio):
        """Худший PnL → больший risk_score."""
        cfg = _base_config()

        good = build_scorecard(clean_portfolio, cfg,
                               equity=21281 + 150, peak_equity=22000)

        worse = dict(clean_portfolio)
        worse["slot_A_1"] = _slot("A", go_rub=3000, pnl=-800, peak_pnl=0,
                                  direction="LONG", signal_age_sec=120)
        bad = build_scorecard(worse, cfg,
                              equity=21281 - 750, peak_equity=22000)

        assert bad["risk_score"] >= good["risk_score"], (
            "risk_score должен расти при ухудшении: good=%.2f bad=%.2f"
            % (good["risk_score"], bad["risk_score"])
        )

    def test_perfect_portfolio_low_score(self):
        """Без слотов — минимальный score."""
        result = build_scorecard({}, _base_config(), equity=21281, peak_equity=21281)
        assert result["risk_score"] < 20, (
            "Пустой портфель должен иметь низкий score: %.2f" % result["risk_score"]
        )
        assert result["verdict"] == "ALLOW"


class TestRIExcludedVeto:
    """Критерий: RI excluded → VETO при любых метриках."""

    def test_ri_veto_in_active_position(self, ri_portfolio):
        """RI в активной позиции → VETO."""
        cfg = _base_config()
        result = build_scorecard(ri_portfolio, cfg)

        assert result["verdict"] == "VETO"
        assert "excluded_tickers" in result["components"]
        assert result["components"]["excluded_tickers"]["status"] == STATUS_VETO

    def test_ri_veto_even_with_good_metrics(self):
        """RI с отличными метриками всё равно VETO."""
        ri_slot = {
            "slot_RI_1": _slot("RI", go_rub=1000, pnl=5000, peak_pnl=5000,
                               direction="LONG", signal_age_sec=10),
        }
        cfg = _base_config()
        result = build_scorecard(ri_slot, cfg, equity=26000, peak_equity=26000)

        assert result["verdict"] == "VETO", (
            "RI не может получить ALLOW: %s" % result["verdict"]
        )

    def test_ri_not_in_pool_no_veto(self):
        """RI в пуле без позиции → нет VETO."""
        ri_pool_only = {
            "slot_RI_1": _slot("RI", go_rub=1000, pnl=0,
                               direction=None, signal_age_sec=60),
        }
        cfg = _base_config()
        result = build_scorecard(ri_pool_only, cfg)
        assert result["verdict"] != "VETO"


class TestMaxSlots3:
    """Критерий: n_active = 3 → slots = VETO."""

    def test_three_active_slots_veto(self):
        """3 активных слота → VETO."""
        slots = {
            "s1": _slot("A", direction="LONG", go_rub=2000),
            "s2": _slot("B", direction="SHORT", go_rub=2000),
            "s3": _slot("C", direction="LONG", go_rub=2000),
        }
        cfg = _base_config()
        result = build_scorecard(slots, cfg)
        assert result["components"]["slots"]["status"] == STATUS_VETO

    def test_two_active_slots_ok(self):
        """2 активных слота → OK или WARN (зависит от GO)."""
        slots = {
            "s1": _slot("A", direction="LONG", go_rub=2000),
            "s2": _slot("B", direction="SHORT", go_rub=2000),
        }
        cfg = _base_config()
        result = build_scorecard(slots, cfg)
        assert result["components"]["slots"]["status"] in (STATUS_OK, STATUS_WARN)


class TestCapsOneContract:
    """Критерий: contracts > 1 при капе 1 → caps = VETO."""

    def test_two_contracts_veto(self):
        """2 контракта при капе 1 → VETO."""
        slots = {
            "s1": _slot("A", contracts=2, direction="LONG", go_rub=5000),
        }
        cfg = _base_config()
        result = build_scorecard(slots, cfg)
        assert result["components"]["caps"]["status"] == STATUS_VETO
        assert result["verdict"] == "VETO"

    def test_one_contract_ok(self):
        """1 контракт при капе 1 → OK."""
        slots = {
            "s1": _slot("A", contracts=1, direction="LONG", go_rub=5000),
        }
        cfg = _base_config()
        result = build_scorecard(slots, cfg)
        assert result["components"]["caps"]["status"] == STATUS_OK


class TestSignalAgeExpiry:
    """Критерий: сигнал > 16 минут → WARN/VETO."""

    def test_fresh_signal_ok(self):
        """Свежий сигнал → OK."""
        slots = {
            "s1": _slot("A", direction="LONG", signal_age_sec=300),
        }
        result = build_scorecard(slots, _base_config())
        assert result["components"]["signal_age"]["status"] == STATUS_OK

    def test_expired_signal_warn(self):
        """Один из двух активных сигналов протух → WARN (не все)."""
        slots = {
            "s1": _slot("A", direction="LONG", signal_age_sec=1200),  # 20 мин
            "s2": _slot("B", direction="SHORT", signal_age_sec=120),   # 2 мин
        }
        result = build_scorecard(slots, _base_config())
        assert result["components"]["signal_age"]["status"] == STATUS_WARN

    def test_all_expired_veto(self):
        """Все активные сигналы протухли → VETO."""
        slots = {
            "s1": _slot("A", direction="LONG", signal_age_sec=1200),
            "s2": _slot("B", direction="SHORT", signal_age_sec=1500),
        }
        result = build_scorecard(slots, _base_config())
        assert result["components"]["signal_age"]["status"] == STATUS_VETO


class TestNoLiveOrdersImports:
    """Критерий C: модуль не импортирует orders.post_order / broker."""

    def test_no_broker_imports_in_scorecard(self):
        """risk_scorecard.py не содержит broker-импортов."""
        import inspect
        import risk_scorecard as mod
        source = inspect.getsource(mod)

        forbidden = ["post_order", "from tinkoff", "import tinkoff",
                      "Client(", "broker_client", "send_order"]
        for pattern in forbidden:
            assert pattern not in source, (
                "risk_scorecard.py содержит запрещённый паттерн: %s" % pattern
            )


class TestDrawdown:
    """Портфельная просадка vs стоп — градуированная шкала."""

    def test_portfolio_stop_veto(self):
        """Просадка >= 25% → VETO."""
        # equity=15900 → DD = (21281-15900)/21281 = 25.28% >= 25%
        slots = {"s1": _slot("A", pnl=-5000, peak_pnl=0, direction="LONG")}
        result = build_scorecard(slots, _base_config(),
                                 equity=15900, peak_equity=21281)
        assert result["components"]["drawdown"]["status"] == STATUS_VETO

    def test_reduce_tier(self):
        """Просадка 22% (80%-99% стопа 25%) → REDUCE."""
        # DD = (21281-16600)/21281 = 21.99% → >= 25*0.80=20% → REDUCE
        slots = {"s1": _slot("A", pnl=-4681, peak_pnl=0, direction="LONG")}
        result = build_scorecard(slots, _base_config(),
                                 equity=16600, peak_equity=21281)
        assert result["components"]["drawdown"]["status"] == STATUS_REDUCE

    def test_warn_tier(self):
        """Просадка 15% (40%-79% стопа 25%) → WARN."""
        # DD = (21281-18100)/21281 = 14.94% → >= 25*0.40=10% → WARN
        slots = {"s1": _slot("A", pnl=-3181, peak_pnl=0, direction="LONG")}
        result = build_scorecard(slots, _base_config(),
                                 equity=18100, peak_equity=21281)
        assert result["components"]["drawdown"]["status"] == STATUS_WARN

    def test_no_drawdown_ok(self):
        """Нет просадки → OK."""
        slots = {"s1": _slot("A", pnl=0, peak_pnl=0, direction="LONG")}
        result = build_scorecard(slots, _base_config(),
                                 equity=21281, peak_equity=21281)
        assert result["components"]["drawdown"]["status"] == STATUS_OK


class TestCorrelation:
    """Корреляция тикеров."""

    def test_high_correlation_veto(self):
        """Корреляция > 0.95 → VETO."""
        slots = {
            "s1": _slot("A", direction="LONG", go_rub=3000),
            "s2": _slot("B", direction="SHORT", go_rub=3000),
        }
        series = {
            "A": [100 + i for i in range(30)],
            "B": [200 + i for i in range(30)],  # perfectly correlated
        }
        result = build_scorecard(slots, _base_config(), price_series=series)
        assert result["components"]["correlation"]["status"] == STATUS_VETO

    def test_no_series_neutral(self):
        """Нет ценовых рядов → NEUTRAL."""
        slots = {
            "s1": _slot("A", direction="LONG"),
            "s2": _slot("B", direction="SHORT"),
        }
        result = build_scorecard(slots, _base_config())
        assert result["components"]["correlation"]["status"] == STATUS_NEUTRAL


class TestExposure:
    """Проверка компоненты exposure."""

    def test_go_budget_exceeded_veto(self):
        """GO > 100% бюджета → VETO."""
        slots = {
            "s1": _slot("A", go_rub=11000, direction="LONG"),
        }
        cfg = _base_config(go_budget_rub=10000)
        result = build_scorecard(slots, cfg)
        assert result["components"]["exposure"]["status"] == STATUS_VETO


# ─── Integration: full scorecard ────────────────────────────────────────

class TestFullScorecard:
    """Интеграционные проверки полного scorecard."""

    def test_verdict_allow_for_clean(self, clean_portfolio):
        """Чистый портфель → ALLOW или REDUCE (2/3 slots = WARN)."""
        cfg = _base_config()
        result = build_scorecard(clean_portfolio, cfg,
                                 equity=21281 + 150, peak_equity=22000)
        # 2/3 active slots → slots=WARN → verdict=REDUCE (ожидаемо)
        assert result["verdict"] in ("ALLOW", "REDUCE")
        assert result["risk_score"] < 30

    def test_verdict_veto_for_stressed(self, stressed_portfolio, ri_portfolio):
        """Стрессовый портфель → VETO или REDUCE."""
        cfg = _base_config()
        # stressedPortfolio: 3 слота → slots VETO
        result = build_scorecard(stressed_portfolio, cfg,
                                 equity=18000, peak_equity=22000)
        assert result["verdict"] in ("VETO", "REDUCE")

    def test_empty_portfolio_allowed(self):
        """Пустой → ALLOW."""
        result = build_scorecard({}, _base_config())
        assert result["verdict"] == "ALLOW"
        assert result["risk_score"] < 10


# ─── JSON-fixture integration ──────────────────────────────────────────

_FIXTURES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "tests", "fixtures")


@pytest.fixture
def json_portfolio():
    """Загружает tests/fixtures/portfolio_copy.json и адаптирует к формату scorecard."""
    path = os.path.join(_FIXTURES_DIR, "portfolio_copy.json")
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    # Адаптируем слоты: добавляем open_position, pnl_rub, last_signal_ts
    adapted = {}
    for key, s in raw.get("slots", {}).items():
        open_pos = None
        if s.get("status") == "active":
            open_pos = {
                "direction": s.get("direction", "LONG"),
                "qty": s.get("contracts", 1),
                "entry_price": s.get("entry_price", 0.0),
                "entry_atr": s.get("entry_atr", 50.0),
                "entry_ts": time.time() - 300,
            }
        adapted[key] = {
            "ticker": s.get("ticker", "?"),
            "strategy": s.get("strategy", "unknown"),
            "contracts": s.get("contracts", 1),
            "go_rub": s.get("go_rub", 0.0),
            "open_position": open_pos,
            "pnl_rub": s.get("pnl_rub", 0.0),
            "peak_pnl_rub": s.get("peak_pnl_rub", 0.0),
            "stop_streak": 0,
            "last_signal_ts": time.time() - 120,
            "n_trades": 5,
        }

    return {
        "slots": adapted,
        "config": {
            "go_budget_rub": raw.get("deposit_rub", 21281) * 50 / 100,
            "delta_band_pct": 30,
            "deposit_rub": raw.get("deposit_rub", 21281),
            "portfolio_stop_drawdown_pct": 25,
            "signal_max_age_minutes": 16,
            "max_slots": raw.get("max_slots", 3),
            "max_contracts_per_entry": raw.get("max_contracts_per_entry", 1),
            "excluded": raw.get("excluded", ["RI"]),
        },
    }


@pytest.fixture
def json_returns():
    """Загружает tests/fixtures/sample_returns.json."""
    path = os.path.join(_FIXTURES_DIR, "sample_returns.json")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


class TestJsonFixtures:
    """Smoke-тесты на JSON fixtures из tests/fixtures/."""

    def test_scorecard_from_json_portfolio(self, json_portfolio):
        """build_scorecard() не падает на данных из portfolio_copy.json."""
        result = build_scorecard(json_portfolio["slots"], json_portfolio["config"])
        assert "risk_score" in result
        assert "verdict" in result
        assert 0 <= result["risk_score"] <= 100
        assert result["verdict"] in ("ALLOW", "REDUCE", "VETO")

    def test_json_returns_readable(self, json_returns):
        """sample_returns.json содержит ожидаемые ключи."""
        assert "baseline" in json_returns
        assert isinstance(json_returns["baseline"], list)
        assert len(json_returns["baseline"]) >= 5

    def test_scorecard_weights_from_config(self, json_portfolio):
        """Веса берутся из config.risk_scorecard_weights, если заданы."""
        cfg = json_portfolio["config"]
        cfg["risk_scorecard_weights"] = {
            "exposure": 50, "drawdown": 50,
            "volatility": 0, "correlation": 0,
            "signal_age": 0, "slots": 0, "caps": 0,
        }
        result = build_scorecard(json_portfolio["slots"], cfg)
        assert 0 <= result["risk_score"] <= 100
