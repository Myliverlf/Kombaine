"""Synthetic close fixtures for stop/take/time dry-run tests.

Local-only helpers for strategy_combine close logic. No broker mutations, no
network calls.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd
import time

from core import analytics as an


@dataclass(frozen=True)
class CloseScenario:
    ticker: str
    reason: str
    entry_price: float
    reference_close_price: float
    broker_fill_price: float
    direction: str = "LONG"
    point_value: float = 1.0
    active_margin: float = 1000.0
    atr: float = 10.0
    sl_mult: float = 2.0
    tp_mult: float = 3.0
    force_exit_hours: int = 48
    qty: int = 1
    bars_held: int = 0


class MockSpec:
    def __init__(self, ticker: str, point_value: float, active_margin: float) -> None:
        self.ticker = ticker
        self.uid = f"UID_{ticker}"
        self.name = f"{ticker}-MOCK"
        self.class_code = "fut"
        self.lot = 1
        self.min_price_increment = 1.0
        self.min_price_increment_amount = point_value
        self.initial_margin_on_buy = active_margin
        self.initial_margin_on_sell = active_margin

    @property
    def point_value(self) -> float:
        return float(self.min_price_increment_amount) / float(self.min_price_increment or 1.0)

    @property
    def active_margin(self) -> float:
        return float(self.initial_margin_on_buy or self.initial_margin_on_sell or 0.0)


class FakePrice:
    def __init__(self, value: float) -> None:
        self.units = int(value)
        self.nano = int(round((float(value) - int(value)) * 1e9))


class FakeCandle:
    def __init__(self, ts: datetime, open_: float, high: float, low: float, close: float) -> None:
        self.time = ts
        self.open = FakePrice(open_)
        self.high = FakePrice(high)
        self.low = FakePrice(low)
        self.close = FakePrice(close)
        self.volume = 1


class FakeOrders:
    def __init__(self, fill_price: float) -> None:
        self.fill_price = fill_price
        self.calls: list[dict[str, Any]] = []

    def post_order(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return SimpleNamespace(
            lots_executed=kwargs.get("quantity", 0),
            execution_report_status="EXECUTED",
            executed_order_price=FakePrice(self.fill_price),
            order_id="DRY_ORDER",
        )


class FakeMarketData:
    def __init__(self, candles: list[FakeCandle]) -> None:
        self._candles = candles

    def get_candles(self, **kwargs: Any) -> Any:
        return SimpleNamespace(candles=self._candles)


class FakeOperations:
    def __init__(self, money: float = 100_000.0) -> None:
        self.money = money

    def get_portfolio(self, **kwargs: Any) -> Any:
        money_asset = SimpleNamespace(asset_type="money", quantity=FakePrice(self.money))
        return SimpleNamespace(assets=[money_asset], positions=[])


class FakeClient:
    def __init__(self, fill_price: float, candles: list[FakeCandle], money: float = 100_000.0) -> None:
        self.orders = FakeOrders(fill_price)
        self.market_data = FakeMarketData(candles)
        self.operations = FakeOperations(money)

    def __enter__(self) -> "FakeClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


class CloseEngineFixture:
    def __init__(self, engine_cls: type[Any], scenario: CloseScenario, tmp_db: Path) -> None:
        self.scenario = scenario
        if tmp_db.exists():
            tmp_db.unlink()
        wal_path = tmp_db.with_suffix(tmp_db.suffix + "-wal")
        shm_path = tmp_db.with_suffix(tmp_db.suffix + "-shm")
        for sidecar in (wal_path, shm_path):
            if sidecar.exists():
                sidecar.unlink()
        self.engine = object.__new__(engine_cls)
        self.engine.cfg = SimpleNamespace(
            sl_atr_mult=scenario.sl_mult,
            tp_atr_mult=scenario.tp_mult,
            force_exit_hours=scenario.force_exit_hours,
            atr_period=14,
            risk_per_trade_rub=1000.0,
        )
        self.engine.risk = SimpleNamespace(max_contracts_per_entry=1)
        self.engine.account = "ACC_DRY"
        self.engine.token = "TOKEN_DRY"
        self.engine.specs = {
            scenario.ticker: MockSpec(
                scenario.ticker,
                point_value=scenario.point_value,
                active_margin=scenario.active_margin,
            )
        }
        self.engine.candles = {}
        self.engine.db = an.connect(tmp_db)
        self.engine._current_df = None
        self.engine.fetch_candles = lambda ticker: self.engine._current_df.copy()

    def make_candles(self) -> pd.DataFrame:
        scenario = self.scenario
        now = datetime.now(timezone.utc)
        rows: list[dict[str, Any]] = []
        for idx in range(100):
            ts = now - timedelta(minutes=15 * (99 - idx))
            open_px = scenario.reference_close_price
            high_px = scenario.reference_close_price + 1.0
            low_px = scenario.reference_close_price - 1.0
            close_px = scenario.reference_close_price
            rows.append({
                "time": ts,
                "open": open_px,
                "high": high_px,
                "low": low_px,
                "close": close_px,
                "volume": 1,
            })
        df = pd.DataFrame(rows)
        if scenario.reason == "stop":
            stop_px = scenario.entry_price - scenario.sl_mult * scenario.atr
            df.loc[df.index[-1], "low"] = stop_px - 0.25
            df.loc[df.index[-1], "high"] = scenario.reference_close_price + 1.0
        elif scenario.reason == "take":
            take_px = scenario.entry_price + scenario.tp_mult * scenario.atr
            df.loc[df.index[-1], "high"] = take_px + 0.25
            df.loc[df.index[-1], "low"] = scenario.reference_close_price - 1.0
        elif scenario.reason == "time":
            df.loc[df.index[-1], "high"] = scenario.reference_close_price + 0.5
            df.loc[df.index[-1], "low"] = scenario.reference_close_price - 0.5
        else:
            raise ValueError(f"unsupported scenario reason: {scenario.reason}")
        return df

    def make_slot(self) -> dict[str, Any]:
        scenario = self.scenario
        entry_ts = time_now_minus_bars(scenario.bars_held + scenario.force_exit_hours * 4)
        trade_id = an.record_open(
            self.engine.db,
            "slot1",
            scenario.ticker,
            f"{scenario.ticker.lower()}_{scenario.reason}",
            scenario.direction,
            scenario.qty,
            scenario.entry_price,
            "dry-run",
            ts=datetime.now(timezone.utc).isoformat(),
        )
        return {
            "ticker": scenario.ticker,
            "strategy": f"{scenario.ticker.lower()}_{scenario.reason}",
            "params": {},
            "contracts": scenario.qty,
            "go_rub": scenario.active_margin,
            "promoted_ts": time.time(),
            "open_position": {
                "direction": scenario.direction,
                "qty": scenario.qty,
                "entry_price": scenario.entry_price,
                "entry_atr": scenario.atr,
                "entry_ts": entry_ts,
                "entry_bar": 0,
                "trade_id": trade_id,
            },
            "n_trades": 0,
            "pnl_rub": 0.0,
            "peak_pnl_rub": 0.0,
            "stop_streak": 0,
            "last_signal_ts": time.time(),
        }

    def make_portfolio(self, slot: dict[str, Any]) -> dict[str, Any]:
        return {"slots": {"slot1": slot}, "peak_equity": 0.0, "halted": False, "halt_reason": None}

    def run(self, fill_price: float | None = None) -> tuple[list[str], dict[str, Any], dict[str, Any]]:
        self.engine._current_df = self.make_candles()
        slot = self.make_slot()
        portfolio = self.make_portfolio(slot)
        positions = {"slot1": slot["open_position"]}
        client = FakeClient(fill_price or self.scenario.broker_fill_price, self._candles_for_client())
        from core import engine as core_engine
        original_build_signal = core_engine.build_signal
        core_engine.build_signal = lambda df, strategy, params: pd.Series([0] * (len(df) - 2) + [1, 1], index=df.index)
        try:
            events = self.engine.process_slot(client, "slot1", slot, portfolio, positions)
        finally:
            core_engine.build_signal = original_build_signal
        return events, portfolio, slot

    def _candles_for_client(self) -> list[FakeCandle]:
        df = self.make_candles()
        return [
            FakeCandle(row.time, row.open, row.high, row.low, row.close)
            for row in df.itertuples(index=False)
        ]


def time_now_minus_bars(bars: int) -> float:
    return time.time() - float(bars * 900)


def scenario_lkoh_stop() -> CloseScenario:
    return CloseScenario(
        ticker="LKOH",
        reason="stop",
        entry_price=42568.0,
        reference_close_price=42568.0,
        broker_fill_price=42548.0,
        direction="LONG",
        point_value=1.0,
        active_margin=2000.0,
        atr=10.0,
        sl_mult=2.0,
        tp_mult=3.0,
        force_exit_hours=48,
        qty=1,
        bars_held=0,
    )


def scenario_gazp_take_scaled() -> CloseScenario:
    return CloseScenario(
        ticker="GAZP",
        reason="take",
        entry_price=82.78,
        reference_close_price=82.78,
        broker_fill_price=85.78,
        direction="LONG",
        point_value=1.0,
        active_margin=1000.0,
        atr=1.0,
        sl_mult=2.0,
        tp_mult=3.0,
        force_exit_hours=48,
        qty=1,
        bars_held=0,
    )


def scenario_sber_time() -> CloseScenario:
    return CloseScenario(
        ticker="SBER",
        reason="time",
        entry_price=250.0,
        reference_close_price=250.0,
        broker_fill_price=250.75,
        direction="LONG",
        point_value=1.0,
        active_margin=1500.0,
        atr=2.0,
        sl_mult=2.0,
        tp_mult=3.0,
        force_exit_hours=48,
        qty=1,
        bars_held=192,
    )
