"""Monkeypatch-хелперы для Engine: post, _equity, fetch_candles, ensure_spec, tick.

Используется validate_engine.py для dry-run без реальных API-вызовов.
Все моки имеют ту же сигнатуру, что и оригинальные методы Engine.
"""
import json
import types
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


# ── Синтетические свечи ──────────────────────────────────────────────

def _make_synthetic_candles(n_bars: int = 120, base_price: float = 18000.0,
                            base_atr: float = 100.0, seed: int = 42) -> pd.DataFrame:
    """120 баров 15м: random-walk + синусоидальная осцилляция.

    Достаточно стабильные для RSI/BB стратегий (ft_bband_rsi, vwap_reversion).
    """
    rng = np.random.RandomState(seed)
    times = pd.date_range(end=pd.Timestamp.now(tz="UTC"), periods=n_bars, freq="15min")

    returns = rng.normal(0, base_atr * 0.002, n_bars)
    cycle = 30 * np.sin(np.linspace(0, 4 * np.pi, n_bars))
    cum = np.cumsum(returns) + cycle
    closes = base_price + cum
    highs = closes + np.abs(rng.normal(0, base_atr * 0.15, n_bars))
    lows = closes - np.abs(rng.normal(0, base_atr * 0.15, n_bars))
    opens = np.empty(n_bars)
    opens[0] = closes[0]
    opens[1:] = closes[:-1]

    return pd.DataFrame({
        "time": times,
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": np.abs(rng.normal(1000, 200, n_bars)).astype(int),
    })


# ── Мок-спека (минимальный аналог FuturesSpec) ───────────────────────

class MockFuturesSpec:
    """Достаточно атрибутов для process_slot: uid, point_value, active_margin."""

    def __init__(self, ticker: str = "GAZP", uid: str = "MOCK_UID",
                 point_value: float = 1.0, active_margin: float = 1433.32):
        self.ticker = ticker
        self.uid = uid
        self.name = f"{ticker}-MOCK"
        self.class_code = "fut"
        self.lot = 1
        self.min_price_increment = 1.0
        self.min_price_increment_amount = point_value
        self.initial_margin_on_buy = active_margin
        self.initial_margin_on_sell = active_margin
        # point_value и active_margin — property в оригинале, здесь — обычные атрибуты

    @property
    def point_value(self) -> float:
        if self.min_price_increment > 0 and self.min_price_increment_amount > 0:
            return self.min_price_increment_amount / self.min_price_increment
        return 1.0

    @property
    def active_margin(self) -> float:
        return self.initial_margin_on_buy or self.initial_margin_on_sell or 0.0


# ── Методы-заглушки (подпись = оригинальная) ─────────────────────────

def mock_post(self: Any, client: Any, uid: str, direction: str, qty: int) -> dict:
    """Возвращает DRY_RUN без реального ордера."""
    return {"order_id": "dry", "executed": qty, "status": "DRY_RUN"}


def mock_equity(self: Any, client: Any, broker_positions: Any = None) -> float:
    """Возвращает deposit_rub без API-вызова.

    broker_positions: не используется, но обязателен для совместимости
    с core/engine.py:297 _equity(self, client, broker_positions=None).
    """
    return self.cfg.deposit_rub


def mock_fetch_candles(self: Any, ticker: str) -> pd.DataFrame:
    """Синтетические свечи вместо Tinkoff API.
    Также заполняет self.specs[ticker] MockFuturesSpec (оригинал делает это в ensure_spec)."""
    if ticker not in self.specs:
        self.specs[ticker] = MockFuturesSpec(ticker=ticker)
    df = _make_synthetic_candles()
    self.candles[ticker] = df
    return df


def mock_ensure_spec(self: Any, ticker: str) -> MockFuturesSpec:
    """MockFuturesSpec вместо resolve_spec через API."""
    if ticker not in self.specs:
        self.specs[ticker] = MockFuturesSpec(ticker=ticker)
    return self.specs[ticker]


def mock_tick(self: Any):
    """Обходит Client(self.token) — вся логика registry инлайнена.

    Оригинальный tick() создаёт Client(self.token) в with-блоке.
    Этот мок читает/пишет portfolio.json напрямую и вызывает
    process_slot(None, ...) — client не используется (все методы заменены).
    """
    # Absolute import first (works when run directly), fallback to relative
    registry = None
    try:
        from core import registry as _reg
        registry = _reg
    except ImportError:
        try:
            from . import registry as _reg  # type: ignore[no-redef]
            registry = _reg
        except ImportError:
            registry = None

    portfolio = None
    if registry is not None:
        try:
            portfolio = registry.load_portfolio()
        except Exception:
            portfolio = None

    if portfolio is None:
        state_dir = Path("/root/prop-desk/strategy_combine/state")
        portfolio_file = state_dir / "portfolio.json"
        portfolio = json.loads(portfolio_file.read_text()) if portfolio_file.exists() else {
            "slots": {}, "peak_equity": 0.0, "halted": False, "halt_reason": None
        }

    positions = {
        sid: s["open_position"]
        for sid, s in portfolio["slots"].items()
        if s.get("open_position")
    }
    events: list[str] = []

    for slot_id, slot in portfolio["slots"].items():
        try:
            events += self.process_slot(None, slot_id, slot, portfolio, positions)
        except Exception as exc:
            events.append("%s ERROR: %s" % (slot_id, exc))

    saved = False
    if registry is not None:
        try:
            registry.save_portfolio(portfolio)
            saved = True
        except Exception:
            saved = False

    if not saved:
        state_dir = Path("/root/prop-desk/strategy_combine/state")
        portfolio_file = state_dir / "portfolio.json"
        tmp = portfolio_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(portfolio, indent=2, ensure_ascii=False))
        tmp.replace(portfolio_file)

    return events


# ── Apply / Remove ───────────────────────────────────────────────────

_PATCHES = {
    "post": mock_post,
    "_equity": mock_equity,
    "fetch_candles": mock_fetch_candles,
    "ensure_spec": mock_ensure_spec,
    "tick": mock_tick,
}

_origins: dict[int, dict[str, Any]] = {}


def apply_patches(eng) -> dict:
    """Bind-ит моки к инстансу Engine как instance-атрибуты.

    Возвращает словарь {имя: оригинальный_классовый_метод} для восстановления.
    """
    origs: dict[str, Any] = {}
    for name, fn in _PATCHES.items():
        class_method = getattr(type(eng), name, None)
        origs[name] = class_method
        setattr(eng, name, types.MethodType(fn, eng))
    _origins[id(eng)] = origs
    return origs


def remove_patches(eng) -> None:
    """Удаляет instance-атрибуты, восстанавливая доступ к классовым методам."""
    _origins.pop(id(eng), None)
    for name in _PATCHES:
        if name in eng.__dict__:
            del eng.__dict__[name]
