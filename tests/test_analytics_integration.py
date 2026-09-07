"""Tests: Analytics Integration — fixture-based тесты для core/analytics.py API.

Закрывает desync #2 (analytics пустой) и #3 (statistics модули изолированы).
Все тесты используют tmp_path для изолированной БД, без side-effects.

Покрытие:
  - test_record_open_close_cycle: record_open + record_close → status='closed'
  - test_slot_pnls_computation: slot_pnls() возвращает корректные PnL
  - test_allocator_metrics_on_analytics_data: expectancy_r + risk_penalty
    на данных, извлечённых из analytics
  - test_strategy_stats_aggregation: strategy_stats агрегирует по стратегиям

Источник: plan.md Фича 4
"""
import sys
from pathlib import Path

import pytest

# Ensure imports
COMBINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(COMBINE_DIR / "core"))
sys.path.insert(0, str(COMBINE_DIR / "code"))

import analytics
import allocator_metrics


# ─── Fixtures ────────────────────────────────────────────────────────

@pytest.fixture
def analytics_db(tmp_path):
    """Создаёт изолированную analytics SQLite БД в tmp_path."""
    db_path = tmp_path / "test_analytics.db"
    conn = analytics.connect(db_path)
    yield conn
    conn.close()


@pytest.fixture
def sample_trade_data():
    """Образец данных для трейда."""
    return {
        "slot_id": "LKOH__vwap_reversion",
        "ticker": "LKOH",
        "strategy": "vwap_reversion",
        "direction": "LONG",
        "contracts": 1,
        "entry_price": 18000.0,
        "regime": "trend",
        "ts": "2025-08-20T10:00:00+00:00",
    }


@pytest.fixture
def sample_trades_batch():
    """Батч из 5 трейдов для агрегации."""
    return [
        {"slot_id": "LKOH__vwap", "ticker": "LKOH", "strategy": "vwap",
         "direction": "LONG", "contracts": 1, "entry_price": 18000.0, "regime": "trend"},
        {"slot_id": "LKOH__vwap", "ticker": "LKOH", "strategy": "vwap",
         "direction": "LONG", "contracts": 1, "entry_price": 18100.0, "regime": "trend"},
        {"slot_id": "GAZP__momentum", "ticker": "GAZP", "strategy": "momentum",
         "direction": "SHORT", "contracts": 1, "entry_price": 180.0, "regime": "range"},
        {"slot_id": "GAZP__momentum", "ticker": "GAZP", "strategy": "momentum",
         "direction": "SHORT", "contracts": 1, "entry_price": 178.0, "regime": "range"},
        {"slot_id": "SBER__rsi", "ticker": "SBER", "strategy": "rsi",
         "direction": "LONG", "contracts": 1, "entry_price": 270.0, "regime": "trend"},
    ]


@pytest.fixture
def sample_regime_snapshot():
    """Regime snapshot для allocator_metrics."""
    return {
        "tickers": {
            "LKOH": {"adx": 35, "direction": "up", "regime": "trend"},
            "GAZP": {"adx": 10, "direction": "neutral", "regime": "range"},
            "SBER": {"adx": 28, "direction": "up", "regime": "trend"},
        },
        "bias": "neutral",
    }


# ─── Tests: record_open/close cycle ──────────────────────────────────

def test_record_open_close_cycle(analytics_db, sample_trade_data):
    """record_open + record_close: трейд переходит из open в closed."""
    # Открываем трейд
    trade_id = analytics.record_open(
        analytics_db,
        slot_id=sample_trade_data["slot_id"],
        ticker=sample_trade_data["ticker"],
        strategy=sample_trade_data["strategy"],
        direction=sample_trade_data["direction"],
        contracts=sample_trade_data["contracts"],
        entry_price=sample_trade_data["entry_price"],
        regime=sample_trade_data["regime"],
        ts=sample_trade_data["ts"],
    )
    assert trade_id is not None
    assert trade_id > 0

    # Проверяем что статус = open
    row = analytics_db.execute(
        "SELECT status FROM trades WHERE id=?", (trade_id,)
    ).fetchone()
    assert row[0] == "open"

    # Закрываем трейд
    analytics.record_close(
        analytics_db, trade_id,
        exit_price=18500.0,
        pnl_rub=500.0,
        exit_reason="tp",
    )

    # Проверяем статус = closed
    row = analytics_db.execute(
        "SELECT status, exit_price, pnl_rub, exit_reason FROM trades WHERE id=?",
        (trade_id,),
    ).fetchone()
    assert row[0] == "closed"
    assert row[1] == 18500.0
    assert row[2] == 500.0
    assert row[3] == "tp"


# ─── Tests: slot_pnls ────────────────────────────────────────────────

def test_slot_pnls_computation(analytics_db):
    """slot_pnls() возвращает корректные PnL для закрытых трейдов."""
    slot_id = "LKOH__vwap_reversion"

    # Открываем и закрываем 3 трейда с разными PnL
    pnls_expected = [500.0, -200.0, 300.0]
    for i, pnl in enumerate(pnls_expected):
        trade_id = analytics.record_open(
            analytics_db, slot_id=slot_id, ticker="LKOH", strategy="vwap",
            direction="LONG", contracts=1, entry_price=18000.0 + i * 100,
            regime="trend", ts=f"2025-08-2{i + 1}T10:00:00+00:00",
        )
        analytics.record_close(
            analytics_db, trade_id,
            exit_price=18000.0 + i * 100 + pnl,
            pnl_rub=pnl,
            exit_reason="tp" if pnl > 0 else "sl",
        )

    # Получаем PnL
    pnls = analytics.slot_pnls(analytics_db, slot_id, last_n=20)

    # slot_pnls возвращает в порядке DESC (последний первый), reversed → ASC
    assert len(pnls) == 3
    # Проверяем что все PnL на месте (в порядке обратном добавлению из-за DESC)
    assert set(pnls) == set(pnls_expected)


# ─── Tests: allocator_metrics on analytics data ──────────────────────

def test_allocator_metrics_on_analytics_data(analytics_db, sample_regime_snapshot):
    """expectancy_r и risk_penalty корректно работают на данных из analytics."""
    # Создаём набор трейдов и закрываем их
    trades = [
        {"pnl_rub": 500.0, "exit_reason": "tp"},   # win
        {"pnl_rub": 300.0, "exit_reason": "tp"},   # win
        {"pnl_rub": -100.0, "exit_reason": "sl"},  # loss
        {"pnl_rub": 200.0, "exit_reason": "tp"},   # win
        {"pnl_rub": -150.0, "exit_reason": "sl"},  # loss
    ]

    for i, t in enumerate(trades):
        tid = analytics.record_open(
            analytics_db, slot_id="LKOH__vwap", ticker="LKOH", strategy="vwap",
            direction="LONG", contracts=1, entry_price=18000.0,
            regime="trend",
        )
        analytics.record_close(analytics_db, tid, exit_price=18000.0 + t["pnl_rub"],
                               pnl_rub=t["pnl_rub"], exit_reason=t["exit_reason"])

    # Считаем stats через strategy_stats
    stats_rows = analytics.strategy_stats(analytics_db)
    assert len(stats_rows) >= 1, "Должна быть хотя бы одна стратегия в stats"

    # Извлекаем win_rate
    row = stats_rows[0]
    strategy_name, ticker_name, n, total_pnl, win_rate = row
    assert n == 5
    assert total_pnl == 500 + 300 - 100 + 200 - 150  # = 750

    # Allocator metrics на извлечённых данных
    avg_win = 500.0 + 300.0 + 200.0  # = 1000 / 3 = 333.33
    avg_loss = 100.0 + 150.0  # = 250 / 2 = 125.0
    stats = {"win_rate": win_rate, "avg_win": avg_win / 3, "avg_loss": avg_loss / 2}

    e_r = allocator_metrics.expectancy_r(stats, risk_per_trade=125.0)
    assert e_r > 0, "Expectancy должна быть положительной (3 wins > 2 losses)"

    r_pen = allocator_metrics.risk_penalty({"drawdown_pct": 5.0})
    assert 0.0 <= r_pen <= 1.0, "Risk penalty должна быть в [0, 1]"

    reg = allocator_metrics.regime_bonus("LKOH", "LONG", sample_regime_snapshot)
    assert -1.0 <= reg <= 1.0, "Regime bonus должна быть в [-1, +1]"


# ─── Tests: strategy_stats ───────────────────────────────────────────

def test_strategy_stats_aggregation(analytics_db):
    """strategy_stats агрегирует PnL и win_rate по стратегиям."""
    # Добавляем трейды для 2 стратегий
    for pnl, slot, ticker, strat in [
        (500.0, "LKOH__vwap", "LKOH", "vwap"),
        (300.0, "LKOH__vwap", "LKOH", "vwap"),
        (-100.0, "LKOH__vwap", "LKOH", "vwap"),
        (200.0, "GAZP__mo", "GAZP", "momentum"),
        (-50.0, "GAZP__mo", "GAZP", "momentum"),
    ]:
        tid = analytics.record_open(
            analytics_db, slot_id=slot, ticker=ticker, strategy=strat,
            direction="LONG", contracts=1, entry_price=100.0, regime="trend",
        )
        analytics.record_close(analytics_db, tid, exit_price=100.0 + pnl,
                               pnl_rub=pnl, exit_reason="tp" if pnl > 0 else "sl")

    stats = analytics.strategy_stats(analytics_db)
    assert len(stats) == 2, "Должно быть 2 стратегии"

    # LKOH: pnl = 500+300-100 = 700
    lkoh = [s for s in stats if s[1] == "LKOH"][0]
    assert lkoh[3] == 700.0
    assert lkoh[2] == 3  # 3 trades

    # GAZP: pnl = 200-50 = 150
    gazp = [s for s in stats if s[1] == "GAZP"][0]
    assert gazp[3] == 150.0
    assert gazp[2] == 2  # 2 trades
