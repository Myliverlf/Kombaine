"""RiskTimesFMConfig — пороги и мета-конфиг для forecast-aware risk.

Вынесен отдельно для тестируемости и backward-compat:
  - Существующие модули не трогаем
  - Config можно загрузить из dict/JSON
  - Все пороги с документированными дефолтами

Не импортирует broker/client, не ходит в сеть.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, Optional


# ─── Defaults ─────────────────────────────────────────────────────────

DEFAULT_RISK_ON_THRESHOLD = 0.6
DEFAULT_RISK_OFF_THRESHOLD = 0.3
DEFAULT_STALE_AGE_HOURS = 8
DEFAULT_CONFLICT_DIRECTION_THRESHOLD = 0.5

# Regime multipliers: regime → adjustment factor
# trend+trend → aggressive (higher values = more risk tolerance)
# range+calm → conservative (lower values = tighter control)
DEFAULT_REGIME_MULTIPLIERS: Dict[str, Dict[str, float]] = {
    "trend": {
        "eject_pf_mult": 0.85,       # lower PF threshold → harder to eject
        "max_slots_mult": 1.0,       # keep same
        "delta_band_mult": 1.2,      # wider delta band
        "silent_days_mult": 1.3,     # longer silent tolerance
    },
    "range": {
        "eject_pf_mult": 1.15,       # higher PF threshold → easier to eject
        "max_slots_mult": 0.67,      # reduce max slots (3→2)
        "delta_band_mult": 0.8,      # tighter delta band
        "silent_days_mult": 0.7,     # shorter silent tolerance
    },
}

# Vol regime adjustments
DEFAULT_VOL_MULTIPLIERS: Dict[str, Dict[str, float]] = {
    "volatile": {
        "eject_pf_mult": 1.1,
        "max_slots_mult": 0.67,
        "delta_band_mult": 0.75,
        "silent_days_mult": 0.8,
    },
    "calm": {
        "eject_pf_mult": 0.9,
        "max_slots_mult": 1.0,
        "delta_band_mult": 1.15,
        "silent_days_mult": 1.2,
    },
    "normal": {
        "eject_pf_mult": 1.0,
        "max_slots_mult": 1.0,
        "delta_band_mult": 1.0,
        "silent_days_mult": 1.0,
    },
}

# Risk-on/off mode thresholds for max_slots override
RISK_ON_MAX_SLOTS_OVERRIDE: Optional[int] = None   # None = use config max_slots
RISK_OFF_MAX_SLOTS_OVERRIDE: Optional[int] = 2     # risk-off caps at 2 slots


@dataclass(frozen=True)
class RiskTimesFMConfig:
    """Конфигурация для forecast-aware risk модуля.

    Все поля immutable (frozen=True). Для изменения — создать новый экземпляр
    через ``from_dict()`` или ``replace()``.

    Attributes:
        risk_on_threshold: confidence > threshold → risk-on
        risk_off_threshold: confidence < threshold → risk-off
        stale_age_hours: forecast старше этого → stale
        conflict_direction_threshold: CI-weighted direction mismatch threshold
        regime_multipliers: per-regime adjustment factors
        vol_multipliers: per-vol-regime adjustment factors
        risk_on_max_slots: override max_slots for risk-on (None = no override)
        risk_off_max_slots: override max_slots for risk-off (None = no override)
    """
    risk_on_threshold: float = DEFAULT_RISK_ON_THRESHOLD
    risk_off_threshold: float = DEFAULT_RISK_OFF_THRESHOLD
    stale_age_hours: float = DEFAULT_STALE_AGE_HOURS
    conflict_direction_threshold: float = DEFAULT_CONFLICT_DIRECTION_THRESHOLD
    regime_multipliers: Dict[str, Dict[str, float]] = field(
        default_factory=lambda: dict(DEFAULT_REGIME_MULTIPLIERS)
    )
    vol_multipliers: Dict[str, Dict[str, float]] = field(
        default_factory=lambda: dict(DEFAULT_VOL_MULTIPLIERS)
    )
    risk_on_max_slots: Optional[int] = RISK_ON_MAX_SLOTS_OVERRIDE
    risk_off_max_slots: Optional[int] = RISK_OFF_MAX_SLOTS_OVERRIDE

    def to_dict(self) -> Dict[str, Any]:
        """Сериализация в dict (для JSON-конфигов)."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RiskTimesFMConfig":
        """Загрузить из dict (из JSON-файла или inline dict).

        Неизвестные ключи игнорируются (backward-compat).
        Пустой dict → дефолты.
        """
        if not data:
            return cls()

        known_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in known_fields}
        return cls(**filtered)

    @classmethod
    def default(cls) -> "RiskTimesFMConfig":
        """Создать конфиг с дефолтными значениями."""
        return cls()

    def risk_mode_from_scores(
        self,
        confidence: float,
        portfolio_bias: str,
        vol_regime: str,
    ) -> str:
        """Определить risk_mode на основе scores и порогов конфига.

        Args:
            confidence: confidence_score 0..1
            portfolio_bias: "up" / "down" / "flat"
            vol_regime: "calm" / "normal" / "volatile"

        Returns:
            "risk-on" / "risk-off" / "neutral"
        """
        if confidence >= self.risk_on_threshold:
            if vol_regime != "volatile":
                return "risk-on"
        if confidence <= self.risk_off_threshold or vol_regime == "volatile":
            return "risk-off"
        return "neutral"


def load_config(
    source: Optional[Dict[str, Any]] = None,
    filepath: Optional[str] = None,
) -> RiskTimesFMConfig:
    """Загрузить RiskTimesFMConfig из dict или JSON-файла.

    Приоритет: source (dict) > filepath (JSON) > defaults.

    Args:
        source: dict с параметрами (приоритет)
        filepath: путь к JSON-файлу конфигурации

    Returns:
        RiskTimesFMConfig instance
    """
    if source is not None:
        return RiskTimesFMConfig.from_dict(source)

    if filepath is not None:
        import json as _json
        import os as _os

        resolved = _os.path.expanduser(filepath)
        if _os.path.isfile(resolved):
            with open(resolved, "r", encoding="utf-8") as f:
                data = _json.load(f)
            return RiskTimesFMConfig.from_dict(data)

    return RiskTimesFMConfig.default()
