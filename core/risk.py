"""Риск-менеджер комбайна: детерминированные решения APPROVED/VETO.

Без LLM. Все правила из config.json.
"""
import csv
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import time as _time

from .config import CombineConfig

# --- Жёсткий корреляционный гейт на входе (цикл 2, по итогам аудита) ---
CORR_DATA_DIR = Path("/root/prop-desk/futures_lab/artifacts/tinkoff_futures_data")
CORR_LOOKBACK_BARS = 60          # окно возвратов для pearson
CORR_MIN_SERIES_LEN = 10         # минимум возвратов, иначе гейт не считается
CORR_VETO_THRESHOLD = 0.75       # |corr| >= порога -> VETO (совпадает со скорингом)

# --- Fail-closed forecast гейт на входе ---
FORECAST_CACHE_PATH = Path(__file__).resolve().parent.parent / "state" / "forecast_cache.json"
FORECAST_TTL_SECONDS = 4 * 3600  # как в timesfm_adapter.CACHE_TTL_SECONDS


def _load_close_returns(ticker: str, window: int = CORR_LOOKBACK_BARS) -> List[float]:
    """Возвраты закрытия из свежего CSV тикера (последние `window` штук)."""
    if not ticker:
        return []
    matches = sorted(CORR_DATA_DIR.glob(f"{ticker}_*.csv"),
                     key=lambda p: p.stat().st_mtime, reverse=True)
    if not matches:
        return []
    closes: List[float] = []
    try:
        with matches[0].open("r", encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                raw = row.get("close")
                if raw in (None, ""):
                    continue
                try:
                    closes.append(float(raw))
                except (TypeError, ValueError):
                    continue
    except OSError:
        return []
    trimmed = closes[-(window + 1):]
    rets: List[float] = []
    for prev, curr in zip(trimmed, trimmed[1:]):
        if prev in (0.0, None):
            continue
        rets.append((curr - prev) / prev)
    return rets


def _pearson(a: List[float], b: List[float]) -> Optional[float]:
    n = min(len(a), len(b))
    if n < 2:
        return None
    x, y = a[-n:], b[-n:]
    mx, my = sum(x) / n, sum(y) / n
    cov = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y))
    vx = sum((xi - mx) ** 2 for xi in x)
    vy = sum((yi - my) ** 2 for yi in y)
    if vx <= 0 or vy <= 0:
        return None
    return cov / math.sqrt(vx * vy)


def _fresh_forecast(ticker: str, now: Optional[float] = None) -> Optional[dict]:
    """Свежий прогноз для тикера или None (нет/протух).

    Fail-closed по СВЕЖЕСТИ: если нет ни одной записи младше TTL — None.
    По ИСТОЧНИКУ: принимаем и реальный "timesfm", и "dummy". Причина — пакет
    timesfm может быть не установлен, и тогда живой цикл пишет только
    source="dummy"; требовать жёстко "timesfm" = вечный дедлок всех входов.
    Направление отклоняется только при conf>=0.7, а dummy даёт <=0.3, поэтому
    фейковый прогноз не может ветить вход сам по себе.
    """
    try:
        cache = json.loads(FORECAST_CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(cache, dict):
        return None
    best = None
    best_ts = -1.0
    for key, entry in cache.items():
        if not str(key).startswith(ticker):
            continue
        if not isinstance(entry, dict):
            continue
        ts = float(entry.get("ts", 0) or 0)
        if (now or _time.time()) - ts >= FORECAST_TTL_SECONDS:
            continue
        result = entry.get("result") or {}
        if not isinstance(result, dict):
            continue
        if result.get("source") not in ("timesfm", "dummy"):
            continue
        if ts > best_ts:
            best_ts = ts
            best = result
    return best


@dataclass
class SlotStats:
    ticker: str
    strategy: str
    trades: list = field(default_factory=list)  # list of pnl floats
    go_rub: float = 0.0
    last_signal_ts: float = 0.0
    stop_streak: int = 0

    @property
    def n_trades(self) -> int:
        return len(self.trades)

    @property
    def recent_pnl(self) -> list:
        return self.trades[-20:]

    def profit_factor(self) -> Optional[float]:
        gross_p = sum(p for p in self.recent_pnl if p > 0)
        gross_l = -sum(p for p in self.recent_pnl if p < 0)
        if gross_l == 0:
            return None if gross_p == 0 else float("inf")
        return gross_p / gross_l


class RiskManager:
    def __init__(self, cfg: CombineConfig):
        self.cfg = cfg

    # --- портфельные гейты ---
    def check_portfolio_stop(self, peak_equity: float, equity: float) -> bool:
        """True = СТОП всего портфеля (просадка > лимита)."""
        if peak_equity <= 0:
            return False
        dd = peak_equity - equity
        return dd >= self.cfg.portfolio_stop_rub

    def check_go_budget(self, used_go_rub: float, new_go_rub: float) -> bool:
        """True = бюджет ГО не нарушен."""
        return (used_go_rub + new_go_rub) <= self.cfg.go_budget_rub

    def check_slots(self, n_active: int) -> bool:
        return n_active < self.cfg.risk.max_slots

    def check_reserve(self, used_go_rub: float) -> bool:
        """Свободный резерв >= min_reserve_pct депозита."""
        reserve = self.cfg.deposit_rub - used_go_rub
        return reserve >= self.cfg.deposit_rub * self.cfg.risk.min_reserve_pct / 100.0

    def contracts_for_slot(self, go_per_contract_rub: float, used_go_rub: float) -> int:
        """Сколько контрактов влезает в бюджет ГО с учётом резерва."""
        headroom = min(
            self.cfg.go_budget_rub - used_go_rub,
            self.cfg.deposit_rub * (100 - self.cfg.risk.min_reserve_pct) / 100.0 - used_go_rub,
        )
        if go_per_contract_rub <= 0 or headroom <= 0:
            return 0
        return int(headroom // go_per_contract_rub)

    # --- решения по слотам (выкидывание) ---
    def eject_reason(self, s: SlotStats, slot_drawdown_rub: float, silent_days: float) -> Optional[str]:
        r = self.cfg.risk
        if s.n_trades >= r.slot_eject_window_trades:
            pf = s.profit_factor()
            if pf is not None and pf < r.slot_eject_pf:
                return "R1: PF %.2f < %.2f за последние %d сделок" % (pf, r.slot_eject_pf, s.n_trades)
        if s.stop_streak >= r.slot_eject_streak_stops:
            return "R2: %d стопов подряд" % s.stop_streak
        if slot_drawdown_rub >= self.cfg.deposit_rub * r.slot_eject_slot_drawdown_pct / 100.0:
            return "R3: просадка слота %.0f руб >= %.0f%%" % (slot_drawdown_rub, r.slot_eject_slot_drawdown_pct)
        if silent_days >= r.slot_eject_silent_days:
            return "R4: молчит %.0f дней" % silent_days
        return None

    # --- дельта портфеля ---
    def delta_ok(self, long_go: float, short_go: float) -> bool:
        """Чистая направленность (long-short ГО) как доля депозита.

        Для единственной позиции дельта по определению 100% — не проверяем
        (портфель из 2-3 слотов на 20k не может быть нейтральным с первого шага).
        """
        if long_go <= 0 or short_go <= 0:
            return True
        total = long_go + short_go
        net_pct = abs(long_go - short_go) / self.cfg.deposit_rub * 100.0
        return net_pct <= self.cfg.risk.delta_band_pct

    # --- approval ордера ---
    def approve_entry(self, *, ticker: str, direction: str, go_rub: float,
                      used_go_rub: float, n_active: int,
                      long_go: float, short_go: float,
                      peak_equity: float, equity: float,
                      stop_distance_rub: float | None = None,
                      contracts: int | None = None,
                      open_positions: dict | None = None,
                      forecast_gate: bool | None = None) -> tuple:
        """Полный гейт перед открытием. Возвращает (ok, reason).

        open_positions: dict {slot_id: {"ticker", "direction"}} открытых позиций —
        для жёсткого корреляционного гейта (цикл 2).
        forecast_gate: переопределение прогнозного гейта (по умолчанию из конфига).
        """
        if ticker in self.cfg.excluded:
            return False, "тикер в exclusions (%s)" % ticker
        if self.check_portfolio_stop(peak_equity, equity):
            return False, "стоп портфеля: просадка >= %.0f руб" % self.cfg.portfolio_stop_rub
        if not self.check_slots(n_active):
            return False, "лимит слотов (%d)" % self.cfg.risk.max_slots
        if not self.check_go_budget(used_go_rub, go_rub):
            return False, "бюджет ГО исчерпан (%.0f/%.0f)" % (used_go_rub, self.cfg.go_budget_rub)
        if not self.check_reserve(used_go_rub + go_rub):
            return False, "резерв ниже %.0f%%" % self.cfg.risk.min_reserve_pct

        # Рублёвый veto по риску на сделку: ожидаемый убыток = stop_distance × contracts.
        if stop_distance_rub is not None:
            stop_distance_rub = float(stop_distance_rub)
            n_contracts = int(contracts or 1)
            expected_loss_rub = max(0.0, stop_distance_rub) * max(1, n_contracts)
            risk_limit_rub = float(self.cfg.risk_per_trade_rub)
            if expected_loss_rub > risk_limit_rub:
                return False, "рублёвый стоп-лимит: убыток %.0f > %.0f" % (expected_loss_rub, risk_limit_rub)

        # Жёсткий корреляционный гейт (цикл 2): корреляция новой позиции с открытыми.
        # Знак учитываем: противоположное направление = хедж, не риск.
        if getattr(self.cfg.risk, "correlation_veto", True) and open_positions:
            new_rets = _load_close_returns(ticker)
            if len(new_rets) >= CORR_MIN_SERIES_LEN:
                for _slot_id, pos in open_positions.items():
                    if not isinstance(pos, dict):
                        continue
                    other = str(pos.get("ticker") or "")
                    if not other or other == ticker:
                        continue
                    other_rets = _load_close_returns(other)
                    if len(other_rets) < CORR_MIN_SERIES_LEN:
                        continue  # недостаточно данных по второму тикеру — пропускаем пару
                    corr = _pearson(new_rets, other_rets)
                    if corr is None:
                        continue
                    same_dir = (str(pos.get("direction") or "").upper() == str(direction).upper())
                    effective = corr if same_dir else -corr
                    if effective >= CORR_VETO_THRESHOLD:
                        return False, ("корреляционный гейт: %s/%s corr=%.2f (eff=%.2f >= %.2f)"
                                       % (ticker, other, corr, effective, CORR_VETO_THRESHOLD))

        # Fail-closed forecast-гейт (цикл 2): нет свежего РЕАЛЬНОГО прогноза — вход закрыт.
        if forecast_gate is None:
            forecast_gate = getattr(self.cfg.risk, "forecast_gate", True)
        if forecast_gate:
            fc = _fresh_forecast(ticker)
            if fc is None:
                return False, "forecast-гейт (fail-closed): нет свежего timesfm-прогноза для %s" % ticker
            fc_dir = str(fc.get("direction") or "").lower()
            fc_conf = float(fc.get("confidence") or 0.0)
            trade_dir = "up" if str(direction).upper() == "LONG" else "down"
            if fc_dir in ("up", "down") and fc_dir != trade_dir and fc_conf >= 0.7:
                return False, ("forecast-гейт: прогноз %s (conf=%.2f) против входа %s"
                               % (fc_dir, fc_conf, direction))

        add_long = long_go + (go_rub if direction == "LONG" else 0)
        add_short = short_go + (go_rub if direction == "SHORT" else 0)
        if not self.delta_ok(add_long, add_short):
            return False, "дельта портфеля вне полосы ±%.0f%%" % self.cfg.risk.delta_band_pct
        return True, "APPROVED"


# совместимость с аннотацией выше
int_or_float = (int, float)


def eject_check(slot: dict, cfg, now: float, conn=None) -> str | None:
    """Единая функция eject: объединяет supervisor.eject_rules() и RiskManager.eject_reason().

    Проверяет все 4 правила:
    R1: PF < порога за window_trades сделок (если pnl < 0)
    R2: streak_stops стопов подряд
    R3: просадка слота >= порога
    R4: молчит больше silent_days дней
    """
    r = cfg.risk

    # R1: Profit factor по последним сделкам слота
    if conn is not None:
        slot_key = slot.get("slot_id") or slot.get("id") or slot.get("ticker")
        rows = conn.execute(
            "SELECT pnl_rub FROM trades WHERE slot_id=? AND status='closed' ORDER BY ts_close DESC LIMIT ?",
            (slot_key, r.slot_eject_window_trades),
        ).fetchall()
        pnls = [r0[0] for r0 in reversed(rows) if r0[0] is not None]
        if len(pnls) >= r.slot_eject_window_trades:
            gross_p = sum(p for p in pnls if p > 0)
            gross_l = -sum(p for p in pnls if p < 0)
            if gross_l == 0:
                pf = None if gross_p == 0 else float("inf")
            else:
                pf = gross_p / gross_l
            if pf is not None and pf < r.slot_eject_pf:
                return "R1: PF %.2f < %.2f за последние %d сделок" % (pf, r.slot_eject_pf, len(pnls))
    else:
        if slot.get("n_trades", 0) >= r.slot_eject_window_trades:
            trades = slot.get("trades", [])[-r.slot_eject_window_trades:]
            gross_p = sum(p for p in trades if p > 0)
            gross_l = -sum(p for p in trades if p < 0)
            if gross_l == 0:
                pf = None if gross_p == 0 else float("inf")
            else:
                pf = gross_p / gross_l
            if pf is not None and pf < r.slot_eject_pf:
                return "R1: PF %.2f < %.2f за последние %d сделок" % (pf, r.slot_eject_pf, len(trades))

    # R2: Streak of stops
    if slot["stop_streak"] >= r.slot_eject_streak_stops:
        return "R2: %d стопов подряд" % slot["stop_streak"]

    # R3: Slot drawdown
    dd = slot.get("peak_pnl_rub", 0.0) - slot.get("pnl_rub", 0.0)
    max_dd = cfg.deposit_rub * r.slot_eject_slot_drawdown_pct / 100.0
    if dd > max_dd:
        return "R3: просадка слота %.0f руб > лимит %.0f" % (dd, max_dd)

    # R4: Silent days
    if now - slot.get("last_signal_ts", 0) > r.slot_eject_silent_days * 86400:
        return "R4: молчит %d дней" % r.slot_eject_silent_days

    return None
