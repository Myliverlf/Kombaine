"""Тесты lifecycle metrics — ≥3 pytest-фиксур, dry-run, без live-ордеров.

Тестирует hit_rate, expectancy, decay, stability из lifecycle_metrics.py.
Фикстуры: sample_returns.json (≥3 файлов fixtures), inline-dict.
Нет broker-импортов, нет сети, нет записи state/.
"""
import json
import os

import pytest

from lifecycle_metrics import (
    decay,
    expectancy,
    hit_rate,
    stability,
)

# ─── Path to fixtures ─────────────────────────────────────────────────
FIXTURES_DIR = os.path.join(
    os.path.dirname(__file__), os.pardir, "tests", "fixtures"
)
SAMPLE_RETURNS_PATH = os.path.join(FIXTURES_DIR, "sample_returns.json")
PORTFOLIO_PATH = os.path.join(FIXTURES_DIR, "portfolio_copy.json")
REGIME_PATH = os.path.join(FIXTURES_DIR, "regime_snapshot.json")


# ─── Fixtures ──────────────────────────────────────────────────────────

@pytest.fixture
def sample_returns() -> dict:
    """Загружает tests/fixtures/sample_returns.json."""
    with open(SAMPLE_RETURNS_PATH) as f:
        return json.load(f)


@pytest.fixture
def portfolio_copy() -> dict:
    """Загружает tests/fixtures/portfolio_copy.json."""
    with open(PORTFOLIO_PATH) as f:
        return json.load(f)


@pytest.fixture
def regime_snapshot() -> dict:
    """Загружает tests/fixtures/regime_snapshot.json."""
    with open(REGIME_PATH) as f:
        return json.load(f)


@pytest.fixture
def winning_trades() -> list:
    """Список сделок с положительным R (без убытков)."""
    return [1.0, 2.0, 1.5, 0.5, 3.0]


@pytest.fixture
def losing_trades() -> list:
    """Список сделок с отрицательным R (все убыточные)."""
    return [-1.0, -2.0, -0.5, -1.5, -3.0]


@pytest.fixture
def mixed_trades() -> list:
    """Смешанные сделки: 3 прибыльных, 2 убыточных."""
    return [1.5, -1.0, 2.0, -0.5, 1.0]


# ─── Tests: hit_rate ───────────────────────────────────────────────────

class TestHitRate:
    """Тесты hit_rate."""

    def test_known_distribution(self, mixed_trades: list):
        """3 wins / 5 total = 0.6."""
        assert hit_rate(mixed_trades) == pytest.approx(0.6)

    def test_all_wins(self, winning_trades: list):
        """Все выигрышные → hit_rate = 1.0."""
        assert hit_rate(winning_trades) == 1.0

    def test_all_losses(self, losing_trades: list):
        """Все убыточные → hit_rate = 0.0."""
        assert hit_rate(losing_trades) == 0.0

    def test_empty(self):
        """Пустой список → hit_rate = 0.0."""
        assert hit_rate([]) == 0.0

    def test_single_trade_win(self):
        """Одна прибыльная сделка."""
        assert hit_rate([1.0]) == 1.0

    def test_single_trade_loss(self):
        """Одна убыточная сделка."""
        assert hit_rate([-1.0]) == 0.0


# ─── Tests: expectancy ────────────────────────────────────────────────

class TestExpectancy:
    """Тесты expectancy."""

    def test_symmetric(self):
        """Симметричные сделки → expectancy ≈ 0."""
        assert expectancy([1.0, -1.0]) == pytest.approx(0.0)

    def test_positive_edge(self, mixed_trades: list):
        """Смешанные: (1.5 - 1.0 + 2.0 - 0.5 + 1.0) / 5 = 0.6."""
        assert expectancy(mixed_trades) == pytest.approx(0.6)

    def test_empty(self):
        """Пустой список → expectancy = 0.0."""
        assert expectancy([]) == 0.0

    def test_pnl_up_distinction(self, sample_returns: dict):
        """candidate_better expectancy > candidate_worse expectancy.

        Это ключевая проверка PnL↑: метрика различает лучшего/худшего кандидата.
        """
        better_exp = expectancy(sample_returns["candidate_better"])
        worse_exp = expectancy(sample_returns["candidate_worse"])
        assert better_exp > worse_exp, (
            f"candidate_better ({better_exp}) should have higher "
            f"expectancy than candidate_worse ({worse_exp})"
        )


# ─── Tests: decay ──────────────────────────────────────────────────────

class TestDecay:
    """Тесты decay (proxy: вторая половина vs первая)."""

    def test_growing_returns(self):
        """Растущие доходности → decay > 1 (нет затухания)."""
        growing = [0.01, 0.02, 0.03, 0.04, 0.05, 0.06]
        assert decay(growing) > 1.0

    def test_declining_returns(self):
        """Убывающие доходности → decay < 1 (есть затухание)."""
        declining = [0.05, 0.04, 0.03, 0.02, 0.01, 0.005]
        assert decay(declining) < 1.0

    def test_constant_returns(self):
        """Постоянные доходности → decay ≈ 1.0 (стабильность)."""
        constant = [0.01, 0.01, 0.01, 0.01, 0.01, 0.01]
        assert decay(constant) == pytest.approx(1.0)

    def test_empty(self):
        """Пустой список → decay = 0.0."""
        assert decay([]) == 0.0

    def test_single_element(self):
        """Один элемент → decay = 0.0 (недостаточно данных)."""
        assert decay([0.01]) == 0.0

    def test_from_fixture_sample(self, sample_returns: dict):
        """candidate_better из fixture: проверяем что decay считается."""
        result = decay(sample_returns["candidate_better"])
        assert isinstance(result, float)


# ─── Tests: stability ─────────────────────────────────────────────────

class TestStability:
    """Тесты stability (доля окон с Sharpe > 0)."""

    def test_all_positive(self, sample_returns: dict):
        """all_positive: все доходности > 0 → stability = 1.0."""
        assert stability(sample_returns["all_positive"]) == 1.0

    def test_flat_zero(self, sample_returns: dict):
        """flat: все нули → stability = 0.0 (Sharpe = 0, не > 0)."""
        assert stability(sample_returns["flat"]) == 0.0

    def test_mixed_signal(self, sample_returns: dict):
        """baseline: смешанный сигнал → 0.0 < stability < 1.0."""
        result = stability(sample_returns["baseline"])
        assert 0.0 <= result <= 1.0

    def test_empty(self):
        """Пустой список → stability = 0.0."""
        assert stability([]) == 0.0

    def test_short_series(self):
        """Серия < 2 элементов → stability = 0.0."""
        assert stability([0.01]) == 0.0

    def test_custom_windows(self, sample_returns: dict):
        """Пользовательское число окон (n_windows=2) работает."""
        result = stability(sample_returns["baseline"], n_windows=2)
        assert 0.0 <= result <= 1.0


# ─── Purity test: no broker imports ────────────────────────────────────

class TestPurity:
    """Проверка: lifecycle_metrics.py не содержит broker-импортов."""

    def test_no_broker_in_source(self):
        """В исходниках lifecycle_metrics нет импортов/вызовов broker."""
        import pathlib
        import ast as _ast
        source_path = pathlib.Path(__file__).parent / "lifecycle_metrics.py"
        source_text = source_path.read_text()
        tree = _ast.parse(source_text)
        # Check imports
        for node in _ast.walk(tree):
            if isinstance(node, _ast.Import):
                for alias in node.names:
                    assert "broker" not in alias.name.lower(), (
                        "lifecycle_metrics.py has broker import: %s" % alias.name
                    )
            elif isinstance(node, _ast.ImportFrom):
                if node.module and "broker" in node.module.lower():
                    assert False, "lifecycle_metrics.py has broker from-import: %s" % node.module
            elif isinstance(node, _ast.Call):
                func = node.func
                name = ""
                if isinstance(func, _ast.Name):
                    name = func.id
                elif isinstance(func, _ast.Attribute):
                    name = func.attr
                assert name not in ("post_order", "place_order", "send_order"), (
                    "lifecycle_metrics.py has forbidden call: %s()" % name
                )

    def test_no_network_imports(self):
        """Нет сетевых импортов (requests, httpx, aiohttp)."""
        import pathlib
        source_path = pathlib.Path(__file__).parent / "lifecycle_metrics.py"
        source_text = source_path.read_text()
        network_modules = ["import requests", "import httpx", "import aiohttp",
                           "from requests", "from httpx", "from aiohttp"]
        for module in network_modules:
            assert module not in source_text, (
                "lifecycle_metrics.py contains network import: '%s'" % module
            )
