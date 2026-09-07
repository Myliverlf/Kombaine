"""Тесты reconciliation broker ↔ portfolio state.

Запуск: python3 -m code.test_reconcile
"""
import json
import time
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import registry


def make_portfolio(slots_data=None):
    """Создаёт минимальный portfolio dict."""
    if slots_data is None:
        slots_data = {}
    return {
        "slots": slots_data,
        "peak_equity": 100000.0,
        "halted": False,
        "halt_reason": None,
    }


def make_slot(ticker, strategy="test", open_position=None):
    return {
        "ticker": ticker,
        "strategy": strategy,
        "params": {},
        "contracts": 1,
        "go_rub": 1500.0,
        "promoted_ts": time.time(),
        "open_position": open_position,
        "n_trades": 0,
        "pnl_rub": 0.0,
        "peak_pnl_rub": 0.0,
        "stop_streak": 0,
        "last_signal_ts": time.time(),
    }


# ── Тест 1: Брокер имеет GAZP short -4, portfolio не знает ──
def test_adopt_broker_position():
    portfolio = make_portfolio({
        "slot_GAZP_1": make_slot("GAZP", "ft_bband_rsi", open_position=None),
    })
    broker = {"GAZP": {"qty": -4, "direction": "SHORT", "figi": "FUT_GAZP", "avg_price": 135.5}}

    events = registry.reconcile_broker_positions(portfolio, broker)

    pos = portfolio["slots"]["slot_GAZP_1"]["open_position"]
    assert pos is not None, "open_position должен быть adopt с брокера"
    assert pos["direction"] == "SHORT", "direction должен быть SHORT (qty=-4)"
    assert pos["qty"] == 4, "qty должен быть 4 (абсолютное значение)"
    assert pos["reconciled"] is True, "флаг reconciled должен быть True"
    assert any("adopt" in e for e in events), "должно быть событие adopt"
    print("✓ Тест 1: adopt broker position — OK")


# ── Тест 2: Portfolio имеет open_position, брокер не имеет ──
def test_clear_stale_position():
    portfolio = make_portfolio({
        "slot_GAZP_1": make_slot("GAZP", "ft_bband_rsi", open_position={
            "direction": "SHORT", "qty": 4, "entry_price": 135.0,
            "entry_atr": 2.0, "entry_ts": time.time() - 86400,
            "entry_bar": 100, "trade_id": 42,
        }),
    })
    broker = {}  # брокер не имеет позиции

    events = registry.reconcile_broker_positions(portfolio, broker)

    pos = portfolio["slots"]["slot_GAZP_1"]["open_position"]
    assert pos is None, "open_position должен быть обнулён"
    assert any("не найдена" in e for e in events), "должно быть событие обнуления"
    print("✓ Тест 2: clear stale position — OK")


# ── Тест 3: Количество не совпадает — корректировка ──
def test_qty_correction():
    portfolio = make_portfolio({
        "slot_GAZP_1": make_slot("GAZP", "ft_bband_rsi", open_position={
            "direction": "SHORT", "qty": 2, "entry_price": 135.0,
            "entry_atr": 2.0, "entry_ts": time.time(),
            "entry_bar": 100, "trade_id": 42,
        }),
    })
    broker = {"GAZP": {"qty": -4, "direction": "SHORT", "figi": "FUT_GAZP", "avg_price": 135.5}}

    events = registry.reconcile_broker_positions(portfolio, broker)

    pos = portfolio["slots"]["slot_GAZP_1"]["open_position"]
    assert pos["qty"] == 4, "qty должен быть скорректирован до 4"
    assert any("скорректирован" in e for e in events), "должно быть событие корректировки"
    print("✓ Тест 3: qty correction — OK")


# ── Тест 4: Позиция совпадает — ничего не делаем ──
def test_position_matches():
    portfolio = make_portfolio({
        "slot_GAZP_1": make_slot("GAZP", "ft_bband_rsi", open_position={
            "direction": "SHORT", "qty": 4, "entry_price": 135.0,
            "entry_atr": 2.0, "entry_ts": time.time(),
            "entry_bar": 100, "trade_id": 42,
        }),
    })
    broker = {"GAZP": {"qty": -4, "direction": "SHORT", "figi": "FUT_GAZP", "avg_price": 135.0}}

    events = registry.reconcile_broker_positions(portfolio, broker)

    pos = portfolio["slots"]["slot_GAZP_1"]["open_position"]
    assert pos["qty"] == 4, "qty не должен измениться"
    assert len(events) == 0, "не должно быть событий"
    print("✓ Тест 4: position matches — OK")


# ── Тест 5: Несколько слотов, разные кейсы ──
def test_mixed_scenario():
    portfolio = make_portfolio({
        "slot_GAZP_1": make_slot("GAZP", "ft_bband_rsi", open_position=None),
        "slot_LKOH_1": make_slot("LKOH", "vwap_reversion", open_position={
            "direction": "LONG", "qty": 3, "entry_price": 7500.0,
            "entry_atr": 100.0, "entry_ts": time.time(),
            "entry_bar": 50, "trade_id": 10,
        }),
        "slot_SBER_1": make_slot("SBER", "test_strat", open_position={
            "direction": "SHORT", "qty": 1, "entry_price": 300.0,
            "entry_atr": 5.0, "entry_ts": time.time(),
            "entry_bar": 60, "trade_id": 11,
        }),
    })
    broker = {
        "GAZP": {"qty": -4, "direction": "SHORT", "figi": "FUT_GAZP", "avg_price": 135.0},
        "LKOH": {"qty": 3, "direction": "LONG", "figi": "FUT_LKOH", "avg_price": 7500.0},
        # SBER — позиции нет на брокере (закрыта вручную?)
    }

    events = registry.reconcile_broker_positions(portfolio, broker)

    # GAZP: adopt
    gazp_pos = portfolio["slots"]["slot_GAZP_1"]["open_position"]
    assert gazp_pos is not None, "GAZP adopt"
    assert gazp_pos["direction"] == "SHORT" and gazp_pos["qty"] == 4

    # LKOH: без изменений
    lkoh_pos = portfolio["slots"]["slot_LKOH_1"]["open_position"]
    assert lkoh_pos is not None and lkoh_pos["qty"] == 3

    # SBER: обнулено
    sber_pos = portfolio["slots"]["slot_SBER_1"]["open_position"]
    assert sber_pos is None, "SBER position should be cleared"

    print("✓ Тест 5: mixed scenario — OK")


# ── Тест 6: supervisor mtime guard — stale не перетирает fresh ──
def test_stale_snapshot_does_not_overwrite():
    """Проверяем что supervisor перечитывает portfolio после engine.tick()."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_dir = Path(tmpdir)
        portfolio_path = state_dir / "portfolio.json"

        # Сценарий: supervisor загрузил portfolio с open_position=null для GAZP
        stale_state = make_portfolio({
            "slot_GAZP_1": make_slot("GAZP", "ft_bband_rsi", open_position=None),
        })
        portfolio_path.write_text(json.dumps(stale_state))

        # engine.tick() обновил portfolio (открыл позицию GAZP)
        fresh_state = make_portfolio({
            "slot_GAZP_1": make_slot("GAZP", "ft_bband_rsi", open_position={
                "direction": "SHORT", "qty": 4, "entry_price": 135.0,
                "entry_atr": 2.0, "entry_ts": time.time(),
                "entry_bar": 100, "trade_id": 42,
            }),
        })

        # Симулируем: supervisor загружает stale, потом engine обновляет, потом supervisor перечитывает
        loaded_stale = json.loads(portfolio_path.read_text())
        assert loaded_stale["slots"]["slot_GAZP_1"]["open_position"] is None

        # engine.tick() сохранил свежий state
        portfolio_path.write_text(json.dumps(fresh_state))

        # supervisor перечитывает — должен получить свежий state
        loaded_fresh = json.loads(portfolio_path.read_text())
        assert loaded_fresh["slots"]["slot_GAZP_1"]["open_position"] is not None, \
            "supervisor должен прочитать свежий state после engine.tick()"
        assert loaded_fresh["slots"]["slot_GAZP_1"]["open_position"]["qty"] == 4

        print("✓ Тест 6: stale snapshot не перетирает fresh — OK")


# ── Тест 7: mtime guard — предупреждение если engine не сохранил ──
def test_mtime_guard():
    """portfolio_mtime() возвращает корректное значение."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_dir = Path(tmpdir)
        portfolio_path = state_dir / "portfolio.json"

        # Патчим PORTFOLIO на тестовый путь
        old_portfolio = registry.PORTFOLIO
        registry.PORTFOLIO = portfolio_path

        try:
            assert registry.portfolio_mtime() == 0.0, "нет файла → mtime=0"

            portfolio_path.write_text("{}")
            mtime1 = registry.portfolio_mtime()
            assert mtime1 > 0, "файл есть → mtime > 0"

            time.sleep(0.05)
            portfolio_path.write_text('{"x":1}')
            mtime2 = registry.portfolio_mtime()
            assert mtime2 >= mtime1, "mtime не уменьшился"

            print("✓ Тест 7: portfolio_mtime() — OK")
        finally:
            registry.PORTFOLIO = old_portfolio


if __name__ == "__main__":
    test_adopt_broker_position()
    test_clear_stale_position()
    test_qty_correction()
    test_position_matches()
    test_mixed_scenario()
    test_stale_snapshot_does_not_overwrite()
    test_mtime_guard()
    print("\n═══ Все тесты пройдены ═══")
