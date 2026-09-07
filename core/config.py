"""Загрузка и валидация конфига комбайна."""
import json
import sys
from dataclasses import dataclass
from pathlib import Path

_CODE_DIR = Path(__file__).resolve().parent.parent / "code"
if str(_CODE_DIR) not in sys.path:
    sys.path.insert(0, str(_CODE_DIR))

from capital_context import DEFAULT_REPORT_CAPITAL_RUB, DEFAULT_REPORT_RISK_PCT  # noqa: E402

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"
STATE_DIR = Path(__file__).resolve().parent.parent / "state"


@dataclass
class RiskLimits:
    risk_per_trade_pct: float
    go_budget_pct: float
    max_slots: int
    portfolio_stop_drawdown_pct: float
    slot_eject_pf: float
    slot_eject_window_trades: int
    slot_eject_streak_stops: int
    slot_eject_slot_drawdown_pct: float
    slot_eject_silent_days: int
    promotion_margin_pct: float
    waitlist_ttl_days: int
    waitlist_max: int
    delta_band_pct: float
    min_reserve_pct: float
    # --- signal pool (новое) ---
    signal_pool_max: int = 10       # макс. стратегий в сигнальном пуле
    signal_rotation_days: int = 3   # авто-ротация каждые N дней
    signal_min_rank: float = 100.0  # мин. rank_score для попадания в пул
    signal_max_age_minutes: int = 16  # протухание сигнала в пуле
    max_contracts_per_entry: int = 1  # жёсткий кап на вход в одну стратегию
    # --- цикл 2: жёсткие гейты на входе ---
    correlation_veto: bool = True   # VETO при |corr| новой позиции с открытыми >= 0.75
    forecast_gate: bool = True      # fail-closed: вход только со свежим timesfm-прогнозом


@dataclass
class CombineConfig:
    mode: str
    paper_first: bool
    deposit_rub: float
    universe: list
    excluded: list
    account_id: str
    risk: RiskLimits
    sl_atr_mult: float
    tp_atr_mult: float
    atr_period: int
    force_exit_hours: int
    interval: str

    @property
    def go_budget_rub(self) -> float:
        return self.deposit_rub * self.risk.go_budget_pct / 100.0

    @property
    def risk_per_trade_rub(self) -> float:
        return self.deposit_rub * self.risk.risk_per_trade_pct / 100.0

    @property
    def portfolio_stop_configured_pct(self) -> float:
        return float(self.risk.portfolio_stop_drawdown_pct)

    @property
    def portfolio_stop_effective_pct(self) -> float:
        # Defensive default for paper-only safety: never allow a wider stop than 15%.
        configured = float(self.risk.portfolio_stop_drawdown_pct)
        return min(configured, 15.0)

    @property
    def portfolio_stop_clamped(self) -> bool:
        """True, если заявленный стоп выше защитного потолка и был ужат."""
        return float(self.risk.portfolio_stop_drawdown_pct) > 15.0

    @property
    def portfolio_stop_rub(self) -> float:
        return self.deposit_rub * self.portfolio_stop_effective_pct / 100.0


def load_config(path: Path = CONFIG_PATH) -> CombineConfig:
    d = json.loads(Path(path).read_text())
    r = d["risk"]
    # поддержка новых полей с дефолтами для обратной совместимости
    fields = RiskLimits.__dataclass_fields__
    risk_data = {k: r.get(k, fields[k].default) for k in fields}
    limits = RiskLimits(**risk_data)
    assert limits.risk_per_trade_pct > 0, "risk_per_trade_pct должен быть > 0"
    assert 0 < limits.go_budget_pct <= 100, "go_budget_pct вне диапазона"
    assert limits.max_slots >= 1, "max_slots >= 1"
    assert limits.signal_pool_max >= limits.max_slots, "signal_pool_max >= max_slots"
    safe_modes = {"paper", "dryrun", "backtest", "test"}
    assert d["mode"] in safe_modes, (
        "config mode must be one of %s, got %r" % (sorted(safe_modes), d["mode"])
    )
    # Явное предупреждение о тихом клампе портфельного стопа (цикл 2).
    configured_stop = float(limits.portfolio_stop_drawdown_pct)
    if configured_stop > 15.0:
        print("ВНИМАНИЕ: портфельный стоп заявлен %.0f%%, но защитный потолок ужимает его до 15%%"
              % configured_stop)
    return CombineConfig(
        mode=d["mode"],
        paper_first=bool(d.get("paper_first", True)),
        deposit_rub=d["deposit_rub"],
        universe=d["universe"],
        excluded=d["excluded"],
        account_id=d["account"]["id"],
        risk=limits,
        sl_atr_mult=d["engine"]["sl_atr_mult"],
        tp_atr_mult=d["engine"]["tp_atr_mult"],
        atr_period=d["engine"]["atr_period"],
        force_exit_hours=d["engine"]["force_exit_hours"],
        interval=d["engine"]["interval"],
    )


if __name__ == "__main__":
    c = load_config()
    print("mode:", c.mode, "| deposit:", c.deposit_rub)
    print("GO бюджет: %.0f руб (%.0f%%)" % (c.go_budget_rub, c.risk.go_budget_pct))
    print("риск на сделку: %.0f руб (%.1f%%)" % (c.risk_per_trade_rub, c.risk.risk_per_trade_pct))
    print("стоп портфеля: %.0f руб" % c.portfolio_stop_rub)
    print("юниверс:", c.universe, "| excluded:", c.excluded)
