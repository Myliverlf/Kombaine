"""Pytest-тесты для broken links pipeline combine.

Проверяет:
1. mock_equity принимает 3 аргумента без ошибки (links: risk→live)
2. portfolio ≤ max_slots после enforcement (links: list→pool)
3. signal_pool non-empty после export (links: pool→legacy)
4. composite score из risk_allocator_scorecard
5. RI excluded из portfolio

Fixtures ≥3 (как требует task.md). Все через dry-run, zero broker imports.

Источник: plan.md Feature 4, analysis.md broken links.
"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

COMBINE_DIR = Path("/root/prop-desk/strategy_combine")
CODE_DIR = COMBINE_DIR / "code"
STATE_DIR = COMBINE_DIR / "state"
# Also use workspace code/ for self-referencing tests
WORKSPACE_DIR = Path(__file__).resolve().parent

sys.path.insert(0, str(CODE_DIR))
sys.path.insert(0, str(WORKSPACE_DIR))
sys.path.insert(0, str(COMBINE_DIR))


# ── Fixtures ─────────────────────────────────────────────────────────

@pytest.fixture
def mock_engine():
    """Создаёт mock Engine-подобный объект с _equity методом."""
    class FakeCfg:
        deposit_rub = 21281
        sl_atr_mult = 2.0
        tp_atr_mult = 3.0

    class FakeEngine:
        cfg = FakeCfg()
        specs = {}
        candles = {}

    return FakeEngine()


@pytest.fixture
def portfolio_fixture(tmp_path):
    """Создаёт temp portfolio.json с 5 слотами (3 уникальных тикера)."""
    slots = {
        "slot_LKOH_1": {
            "ticker": "LKOH", "strategy": "vwap_reversion", "params": {},
            "contracts": 1, "go_rub": 7000.0, "promoted_ts": 100.0,
            "open_position": {"direction": "LONG", "qty": 1},
            "n_trades": 3, "pnl_rub": -189.0, "peak_pnl_rub": 0.0,
            "stop_streak": 0, "last_signal_ts": 200.0,
        },
        "slot_LKOH_2": {
            "ticker": "LKOH", "strategy": "nfi_trend", "params": {},
            "contracts": 1, "go_rub": 7000.0, "promoted_ts": 150.0,
            "open_position": None,
            "n_trades": 0, "pnl_rub": 0.0, "peak_pnl_rub": 0.0,
            "stop_streak": 0, "last_signal_ts": 150.0,
        },
        "slot_GAZP_1": {
            "ticker": "GAZP", "strategy": "ft_bband_rsi", "params": {},
            "contracts": 1, "go_rub": 1400.0, "promoted_ts": 50.0,
            "open_position": {"direction": "SHORT", "qty": 1},
            "n_trades": 1, "pnl_rub": -29.0, "peak_pnl_rub": 0.0,
            "stop_streak": 1, "last_signal_ts": 300.0,
        },
        "slot_GAZP_2": {
            "ticker": "GAZP", "strategy": "bollinger_reversion", "params": {},
            "contracts": 1, "go_rub": 1400.0, "promoted_ts": 200.0,
            "open_position": None,
            "n_trades": 1, "pnl_rub": 102.6, "peak_pnl_rub": 102.6,
            "stop_streak": 0, "last_signal_ts": 400.0,
        },
        "slot_SI_1": {
            "ticker": "SI", "strategy": "atr_breakout", "params": {},
            "contracts": 1, "go_rub": 5000.0, "promoted_ts": 80.0,
            "open_position": None,
            "n_trades": 2, "pnl_rub": 50.0, "peak_pnl_rub": 60.0,
            "stop_streak": 0, "last_signal_ts": 350.0,
        },
    }
    portfolio = {
        "slots": slots,
        "peak_equity": 42789.0,
        "halted": False,
        "halt_reason": None,
    }
    pf = tmp_path / "portfolio.json"
    pf.write_text(json.dumps(portfolio, indent=2))
    return pf


@pytest.fixture
def signal_pool_fixture(tmp_path):
    """Создаёт temp signal_pool.json с 2 стратегиями."""
    pool = {
        "strategies": {
            "s1": {
                "ticker": "LKOH", "strategy": "vwap_reversion",
                "params": {}, "metrics": {"pnl": 100.0},
                "rank_score": 500.0, "status": "active_signal_pool",
            },
            "s2": {
                "ticker": "GAZP", "strategy": "ft_bband_rsi",
                "params": {}, "metrics": {"pnl": 200.0},
                "rank_score": 800.0, "status": "active_signal_pool",
            },
        },
        "last_rotation_ts": 1000.0,
    }
    sp = tmp_path / "signal_pool.json"
    sp.write_text(json.dumps(pool, indent=2))
    return sp


@pytest.fixture
def config_fixture(tmp_path):
    """Создаёт temp config.json с risk params."""
    config = {
        "mode": "paper",
        "deposit_rub": 21281,
        "risk": {
            "max_slots": 3,
            "max_contracts_per_entry": 1,
            "risk_per_trade_pct": 2.7,
        },
        "excluded": ["RI"],
    }
    cf = tmp_path / "config.json"
    cf.write_text(json.dumps(config, indent=2))
    return cf


# ── Test 1: mock_equity accepts 3 args ───────────────────────────────

class TestMockEquityFix:
    """T1: mock_equity принимает (self, client, broker_positions=None)."""

    def test_mock_equity_accepts_three_args(self, mock_engine):
        from mock_equity_fix import mock_equity
        result = mock_equity(mock_engine, None, None)
        assert result == 21281, "Expected deposit_rub=21281, got %s" % result

    def test_mock_equity_accepts_positions_dict(self, mock_engine):
        from mock_equity_fix import mock_equity
        positions = {"slot_1": {"qty": 1, "direction": "LONG"}}
        result = mock_equity(mock_engine, None, positions)
        assert result == 21281, "Expected deposit_rub=21281, got %s" % result

    def test_mock_equity_backward_compat_two_args(self, mock_engine):
        from mock_equity_fix import mock_equity
        result = mock_equity(mock_engine, None)
        assert result == 21281, "Expected deposit_rub=21281, got %s" % result

    def test_apply_mock_equity_patch(self, mock_engine):
        from mock_equity_fix import apply_mock_equity
        # Создаём некорректный мок (как в оригинальном engine_patches.py)
        import types
        def old_mock_equity(self, client):
            return self.cfg.deposit_rub
        mock_engine._equity = types.MethodType(old_mock_equity, mock_engine)

        # Перезаписываем корректным моком
        apply_mock_equity(mock_engine)
        # Bound method: _equity(client, broker_positions=None)
        result = mock_engine._equity(None, None)
        assert result == 21281


# ── Test 2: portfolio max_slots enforcement ───────────────────────────

class TestPortfolioEnforcer:
    """T2: portfolio дедуплицируется и обрезается до max_slots."""

    def test_enforce_reduces_slots(self, portfolio_fixture):
        from portfolio_enforcer import enforce_portfolio
        before, after, removed = enforce_portfolio(portfolio_fixture, max_slots=3)
        assert before == 5, "Expected 5 slots before, got %d" % before
        assert after <= 3, "Expected ≤3 slots after, got %d" % after
        assert len(removed) >= 2, "Expected ≥2 removed, got %d" % len(removed)

    def test_enforce_keeps_active_positions(self, portfolio_fixture):
        """Приоритет: слоты с open_position остаются."""
        from portfolio_enforcer import enforce_portfolio
        enforce_portfolio(portfolio_fixture, max_slots=3)
        portfolio = json.loads(portfolio_fixture.read_text())
        # Должны остаться слоты с open_position (LKOH_1 + GAZP_1)
        active = [sid for sid, s in portfolio["slots"].items() if s.get("open_position")]
        assert len(active) >= 1, "Expected ≥1 active position slot"

    def test_enforce_no_duplicates(self, portfolio_fixture):
        """После enforcement нет дублей (ticker, strategy)."""
        from portfolio_enforcer import enforce_portfolio
        enforce_portfolio(portfolio_fixture, max_slots=3)
        portfolio = json.loads(portfolio_fixture.read_text())
        seen = set()
        for sid, s in portfolio["slots"].items():
            key = (s["ticker"], s["strategy"])
            assert key not in seen, "Duplicate found: %s" % (key,)
            seen.add(key)


# ── Test 3: signal_pool export ───────────────────────────────────────

class TestSignalPoolExport:
    """T3: signal_pool непустой после export или при наличии данных."""

    def test_read_existing_pool(self, signal_pool_fixture):
        from signal_pool_exporter import read_signal_pool
        pool = read_signal_pool(signal_pool_fixture)
        assert len(pool) == 2, "Expected 2 strategies, got %d" % len(pool)

    def test_pool_has_required_fields(self, signal_pool_fixture):
        from signal_pool_exporter import read_signal_pool
        pool = read_signal_pool(signal_pool_fixture)
        for pid, info in pool.items():
            assert "ticker" in info, "Missing 'ticker' in %s" % pid
            assert "strategy" in info, "Missing 'strategy' in %s" % pid
            assert "rank_score" in info, "Missing 'rank_score' in %s" % pid

    def test_empty_pool_file(self, tmp_path):
        """Пустой pool → пустой dict."""
        from signal_pool_exporter import read_signal_pool
        empty = tmp_path / "empty_pool.json"
        empty.write_text('{"strategies": {}}')
        pool = read_signal_pool(empty)
        assert len(pool) == 0


# ── Test 4: risk_allocator_scorecard ─────────────────────────────────

class TestRiskAllocatorScorecard:
    """T4: composite score и acceptance criteria из scorecard."""

    def test_scorecard_passes_with_valid_portfolio(self, portfolio_fixture, signal_pool_fixture, config_fixture):
        from risk_allocator_scorecard import run_scorecard
        # Обрезаем до 3 слотов через enforcer
        from portfolio_enforcer import enforce_portfolio
        enforce_portfolio(portfolio_fixture, max_slots=3)

        result = run_scorecard(
            portfolio_path=portfolio_fixture,
            config_path=config_fixture,
            pool_path=signal_pool_fixture,
        )
        assert "composite_score" in result
        assert isinstance(result["composite_score"], float)
        # После enforcement всё должно быть ≤3 slots, contracts=1, no RI
        assert result["pass"], "Scorecard failed: %s" % json.dumps(result["metrics"], indent=2)

    def test_scorecard_detects_max_slots_violation(self, tmp_path):
        """Портфель с >3 слотами → FAIL."""
        from risk_allocator_scorecard import run_scorecard
        # Портфель с 4 слотами
        slots = {}
        for i in range(4):
            slots["slot_%d" % i] = {
                "ticker": "TST", "strategy": "strat_%d" % i, "params": {},
                "contracts": 1, "go_rub": 1000.0, "promoted_ts": float(i),
                "open_position": None,
                "n_trades": 0, "pnl_rub": 0.0, "peak_pnl_rub": 0.0,
                "stop_streak": 0, "last_signal_ts": float(i),
            }
        portfolio = {"slots": slots, "peak_equity": 0.0, "halted": False, "halt_reason": None}
        pf = tmp_path / "portfolio.json"
        pf.write_text(json.dumps(portfolio, indent=2))

        config = {"risk": {"max_slots": 3, "max_contracts_per_entry": 1}, "excluded": ["RI"]}
        cf = tmp_path / "config.json"
        cf.write_text(json.dumps(config))

        sp = tmp_path / "signal_pool.json"
        sp.write_text('{"strategies": {"s1": {"ticker": "TST"}}}')

        result = run_scorecard(portfolio_path=pf, config_path=cf, pool_path=sp)
        slots_check = [m for m in result["metrics"] if m["name"] == "max_live_slots"][0]
        assert not slots_check["passed"], "Expected max_live_slots FAIL, got PASS"
        assert not result["pass"], "Expected overall FAIL"

    def test_scorecard_detects_ri_inclusion(self, tmp_path):
        """RI в portfolio → FAIL."""
        from risk_allocator_scorecard import run_scorecard
        slots = {
            "slot_RI_1": {
                "ticker": "RI", "strategy": "test", "params": {},
                "contracts": 1, "go_rub": 5000.0, "promoted_ts": 0.0,
                "open_position": None,
                "n_trades": 0, "pnl_rub": 0.0, "peak_pnl_rub": 0.0,
                "stop_streak": 0, "last_signal_ts": 0.0,
            },
        }
        portfolio = {"slots": slots, "peak_equity": 0.0, "halted": False, "halt_reason": None}
        pf = tmp_path / "portfolio.json"
        pf.write_text(json.dumps(portfolio, indent=2))

        config = {"risk": {"max_slots": 3, "max_contracts_per_entry": 1}, "excluded": ["RI"]}
        cf = tmp_path / "config.json"
        cf.write_text(json.dumps(config))

        sp = tmp_path / "signal_pool.json"
        sp.write_text('{"strategies": {"s1": {"ticker": "RI"}}}')

        result = run_scorecard(portfolio_path=pf, config_path=cf, pool_path=sp)
        ri_check = [m for m in result["metrics"] if m["name"] == "ri_excluded"][0]
        assert not ri_check["passed"], "Expected ri_excluded FAIL, got PASS"


# ── Test 5: py_compile all code files ────────────────────────────────

class TestPyCompile:
    """T5: все .py файлы в code/ компилируются."""

    @pytest.mark.parametrize("filename", [
        "mock_equity_fix.py",
        "portfolio_enforcer.py",
        "signal_pool_exporter.py",
        "risk_allocator_scorecard.py",
        "test_pipeline_links.py",
    ])
    def test_py_compile(self, filename):
        import py_compile
        # Try project code/ first, fallback to workspace code/
        filepath = CODE_DIR / filename
        if not filepath.exists():
            filepath = WORKSPACE_DIR / filename
        assert filepath.exists(), "File not found in code/ dirs: %s" % filename
        py_compile.compile(str(filepath), doraise=True)
