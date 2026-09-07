"""
Market Regime Detection & Strategy Regime Evidence — Iteration 15

CLASS 2: observational analytics / PAPER-first / non-authoritative.

REGIME ≠ PREDICTION ≠ TRADE SIGNAL

This module implements:
  - Deterministic, auditable, non-predictive market-regime observation
  - Instrument-local regime classification (BR ≠ SBER ≠ Si)
  - Separate dimensions: trend / volatility / stress / confidence
  - Strategy-to-regime evidence mapping (observational, not auto-gating)
  - No future leakage — prefix invariance mandatory
  - No broker/registry/risk/execution mutation

Regime classification uses ONLY data available at the classification timestamp.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd


# ── Regime Dimensions ──────────────────────────────────────────────────────

class TrendState(Enum):
    TREND_UP = "TREND_UP"
    TREND_DOWN = "TREND_DOWN"
    RANGE = "RANGE"
    TREND_UNCERTAIN = "TREND_UNCERTAIN"


class VolatilityState(Enum):
    VOL_LOW = "VOL_LOW"
    VOL_NORMAL = "VOL_NORMAL"
    VOL_HIGH = "VOL_HIGH"
    VOL_EXTREME = "VOL_EXTREME"


class StressState(Enum):
    STRESS_NORMAL = "STRESS_NORMAL"
    STRESS_ELEVATED = "STRESS_ELEVATED"
    STRESS_EXTREME = "STRESS_EXTREME"


class RegimeConfidence(Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    INSUFFICIENT = "INSUFFICIENT"


class EvidenceClass(Enum):
    BACKTEST = "BACKTEST"
    WALK_FORWARD = "WALK_FORWARD"
    PAPER = "PAPER"
    BROKER_REAL = "BROKER_REAL"


class EvidenceMaturity(Enum):
    INSUFFICIENT = "INSUFFICIENT"
    EARLY = "EARLY"
    USABLE = "USABLE"
    MATURE = "MATURE"


# ── Threshold Policy ───────────────────────────────────────────────────────

THRESHOLD_VERSION = "v1.0.0"
FEATURE_VERSION = "v1.0.0"

# Default thresholds — deterministic, versioned, documented.
# These are fixed interpretable values per directive §10.
DEFAULT_THRESHOLDS = {
    "trend": {
        "ma_spread_up": 0.005,       # MA20 > MA60 by > 0.5%
        "ma_spread_down": -0.005,    # MA20 < MA60 by > 0.5%
        "range_efficiency_threshold": 0.35,  # below this = range
        "trend_efficiency_threshold": 0.55,  # above this = trend
    },
    "volatility": {
        "vol_low_annualized": 0.15,   # < 15% annualized
        "vol_normal_annualized": 0.35, # < 35%
        "vol_high_annualized": 0.60,  # < 60%
        # > 60% = extreme
        "vol_window": 20,             # rolling window for vol
    },
    "stress": {
        "atr_normal_pct": 0.02,       # ATR < 2% of price
        "atr_elevated_pct": 0.04,     # ATR < 4%
        # > 4% = extreme
        "atr_window": 14,
        "shock_threshold": 0.03,      # single-bar move > 3%
    },
    "warmup": {
        "min_observations": 60,       # need at least 60 bars
    },
    "hysteresis": {
        "enabled": False,             # §18: not enabled by default
    },
}

# Freshness policy per timeframe (seconds)
FRESHNESS_POLICY = {
    "15m": 4 * 3600,   # 4 hours
    "1h": 24 * 3600,   # 24 hours
}


# ── Feature Contract ───────────────────────────────────────────────────────

@dataclass(frozen=True)
class FeatureSpec:
    """Deterministic feature specification per directive §7."""
    feature_id: str
    formula: str
    lookback: int
    min_observations: int
    normalization: str
    timeframe: str
    data_required: str
    missing_data_behavior: str
    version: str = FEATURE_VERSION


FEATURE_SPECS = {
    "returns": FeatureSpec(
        feature_id="returns",
        formula="log(close[t] / close[t-1])",
        lookback=1,
        min_observations=2,
        normalization="raw",
        timeframe="any",
        data_required="close",
        missing_data_behavior="NaN → confidence downgrade",
    ),
    "rolling_volatility": FeatureSpec(
        feature_id="rolling_volatility",
        formula="std(returns, window=20) * sqrt(252 * bars_per_day)",
        lookback=20,
        min_observations=20,
        normalization="annualized",
        timeframe="any",
        data_required="close",
        missing_data_behavior="NaN → confidence downgrade",
    ),
    "atr_normalized": FeatureSpec(
        feature_id="atr_normalized",
        formula="ATR(14) / close",
        lookback=14,
        min_observations=15,
        normalization="ratio",
        timeframe="any",
        data_required="high, low, close",
        missing_data_behavior="NaN → confidence downgrade",
    ),
    "ma_spread": FeatureSpec(
        feature_id="ma_spread",
        formula="(EMA20 - EMA60) / EMA60",
        lookback=60,
        min_observations=60,
        normalization="ratio",
        timeframe="any",
        data_required="close",
        missing_data_behavior="NaN → confidence downgrade",
    ),
    "range_efficiency": FeatureSpec(
        feature_id="range_efficiency",
        formula="(close - min(close, 20)) / (max(close, 20) - min(close, 20))",
        lookback=20,
        min_observations=20,
        normalization="[0,1]",
        timeframe="any",
        data_required="close",
        missing_data_behavior="0.5 (neutral) → confidence downgrade",
    ),
}


# ── Data Structures ────────────────────────────────────────────────────────

@dataclass
class RegimeObservation:
    """Single regime observation per instrument/timeframe/timestamp."""
    instrument: str
    timeframe: str
    timestamp: str
    trend_state: str
    volatility_state: str
    stress_state: str
    confidence: str
    features_json: str
    policy_version: str
    regime_build_id: str


@dataclass
class RegimeInterval:
    """Contiguous interval of same regime state."""
    interval_id: str
    instrument: str
    timeframe: str
    start_at: str
    end_at: str
    trend_state: str
    volatility_state: str
    stress_state: str
    bar_count: int
    confidence_summary: str
    regime_build_id: str = ""


@dataclass
class RegimeBuild:
    """Build metadata per directive §14."""
    regime_build_id: str
    policy_version: str
    feature_version: str
    code_identity: str
    instruments_json: str
    timeframes_json: str
    source_ranges_json: str
    started_at: str
    finished_at: str
    observation_count: int
    status: str
    errors_json: str = "[]"


@dataclass
class StrategyRegimeEvidence:
    """Strategy performance per regime bucket."""
    strategy_id: str
    regime_bucket: str
    evidence_class: str
    trade_count: int
    win_rate: Optional[float]
    gross_pnl: Optional[float]
    net_pnl: Optional[float]
    profit_factor: Optional[float]
    avg_trade: Optional[float]
    median_trade: Optional[float]
    max_win: Optional[float]
    max_loss: Optional[float]
    avg_holding_bars: Optional[float]
    evidence_maturity: str
    confidence_distribution_json: str
    regime_confidence_distribution_json: str
    regime_build_id: str
    policy_version: str


@dataclass
class CurrentRegimeSnapshot:
    """Current regime state with freshness per directive §33."""
    instrument: str
    timeframe: str
    trend_state: str
    volatility_state: str
    stress_state: str
    confidence: str
    as_of: str
    data_freshness: str  # FRESH / STALE / UNKNOWN
    regime_build_id: str


# ── Feature Computation ────────────────────────────────────────────────────

def compute_features(df: pd.DataFrame, thresholds: dict | None = None) -> pd.DataFrame:
    """
    Compute deterministic regime features from OHLCV data.
    Uses only past/current data — no future leakage.
    
    Returns DataFrame with feature columns appended.
    """
    if thresholds is None:
        thresholds = DEFAULT_THRESHOLDS
    
    c = df["close"].astype(float)
    h = df["high"].astype(float)
    l = df["low"].astype(float)
    
    # Returns (log)
    df = df.copy()
    df["_returns"] = np.log(c / c.shift(1))
    
    # Rolling volatility (annualized)
    vol_window = thresholds.get("volatility", {}).get("vol_window", 20)
    bars_per_day = _estimate_bars_per_day(df)
    df["_rolling_vol"] = df["_returns"].rolling(vol_window).std() * np.sqrt(252 * bars_per_day)
    
    # ATR normalized
    atr_window = thresholds.get("stress", {}).get("atr_window", 14)
    tr = pd.concat([
        h - l,
        (h - c.shift()).abs(),
        (l - c.shift()).abs(),
    ], axis=1).max(axis=1)
    atr = tr.rolling(atr_window).mean()
    df["_atr_normalized"] = atr / c
    
    # MA spread
    ema20 = c.ewm(span=20, adjust=False).mean()
    ema60 = c.ewm(span=60, adjust=False).mean()
    df["_ma_spread"] = (ema20 - ema60) / ema60
    
    # Range efficiency (rolling range position)
    window = 20
    rolling_min = c.rolling(window).min()
    rolling_max = c.rolling(window).max()
    denom = rolling_max - rolling_min
    df["_range_efficiency"] = np.where(denom > 0, (c - rolling_min) / denom, 0.5)
    
    # Shock magnitude (absolute daily return)
    df["_shock"] = df["_returns"].abs()
    
    return df


def _estimate_bars_per_day(df: pd.DataFrame) -> float:
    """Estimate bars per day from timestamps."""
    if len(df) < 2:
        return 26  # default for 15m
    times = pd.to_datetime(df["time"])
    diffs = times.diff().dropna()
    if len(diffs) == 0:
        return 26
    median_diff = diffs.median().total_seconds()
    if median_diff <= 0:
        return 26
    bars = 86400.0 / median_diff  # bars per day
    return max(1.0, min(bars, 100.0))


# ── Regime Classifier ──────────────────────────────────────────────────────

class RegimeClassifier:
    """
    Deterministic regime classifier per instrument/timeframe.
    
    Classifies trend, volatility, stress, and confidence dimensions
    from historical OHLCV data. No future leakage — uses only data
    available at the classification timestamp.
    """
    
    def __init__(self, thresholds: dict | None = None):
        self.thresholds = thresholds or DEFAULT_THRESHOLDS
        self.policy_version = THRESHOLD_VERSION
        self.feature_version = FEATURE_VERSION
    
    def classify_bar(
        self, df: pd.DataFrame, bar_index: int
    ) -> dict:
        """
        Classify regime at a specific bar index using only data up to that bar.
        
        Args:
            df: DataFrame with _features precomputed
            bar_index: index of bar to classify (inclusive)
        
        Returns:
            dict with trend_state, volatility_state, stress_state, confidence, features
        """
        min_obs = self.thresholds.get("warmup", {}).get("min_observations", 60)
        
        if bar_index < min_obs:
            return {
                "trend_state": TrendState.TREND_UNCERTAIN.value,
                "volatility_state": VolatilityState.VOL_NORMAL.value,
                "stress_state": StressState.STRESS_NORMAL.value,
                "confidence": RegimeConfidence.INSUFFICIENT.value,
                "features": {},
                "missing_features": ["all"],
            }
        
        features = {}
        missing = []
        
        # Extract features up to bar_index (prefix-only)
        subset = df.iloc[:bar_index + 1]
        
        # Returns
        ret = subset["_returns"].iloc[-1] if "_returns" in subset else None
        if ret is not None and not np.isnan(ret):
            features["returns"] = round(float(ret), 8)
        else:
            missing.append("returns")
        
        # Rolling volatility
        vol = subset["_rolling_vol"].iloc[-1] if "_rolling_vol" in subset else None
        if vol is not None and not np.isnan(vol):
            features["rolling_volatility"] = round(float(vol), 8)
        else:
            missing.append("rolling_volatility")
        
        # ATR normalized
        atr = subset["_atr_normalized"].iloc[-1] if "_atr_normalized" in subset else None
        if atr is not None and not np.isnan(atr):
            features["atr_normalized"] = round(float(atr), 8)
        else:
            missing.append("atr_normalized")
        
        # MA spread
        ma_spread = subset["_ma_spread"].iloc[-1] if "_ma_spread" in subset else None
        if ma_spread is not None and not np.isnan(ma_spread):
            features["ma_spread"] = round(float(ma_spread), 8)
        else:
            missing.append("ma_spread")
        
        # Range efficiency
        re = subset["_range_efficiency"].iloc[-1] if "_range_efficiency" in subset else None
        if re is not None and not np.isnan(re):
            features["range_efficiency"] = round(float(re), 8)
        else:
            missing.append("range_efficiency")
        
        # Shock
        shock = subset["_shock"].iloc[-1] if "_shock" in subset else None
        if shock is not None and not np.isnan(shock):
            features["shock"] = round(float(shock), 8)
        else:
            missing.append("shock")
        
        # Classify dimensions
        trend = self._classify_trend(features, missing)
        vol = self._classify_volatility(features, missing)
        stress = self._classify_stress(features, missing)
        confidence = self._classify_confidence(features, missing, len(subset))
        
        return {
            "trend_state": trend,
            "volatility_state": vol,
            "stress_state": stress,
            "confidence": confidence,
            "features": features,
            "missing_features": missing,
        }
    
    def _classify_trend(self, features: dict, missing: list) -> str:
        """Classify trend dimension from features."""
        if "ma_spread" in missing or "range_efficiency" in missing:
            return TrendState.TREND_UNCERTAIN.value
        
        ma_spread = features["ma_spread"]
        re = features["range_efficiency"]
        
        t = self.thresholds["trend"]
        
        if re < t["range_efficiency_threshold"]:
            return TrendState.RANGE.value
        elif re > t["trend_efficiency_threshold"]:
            if ma_spread > t["ma_spread_up"]:
                return TrendState.TREND_UP.value
            elif ma_spread < t["ma_spread_down"]:
                return TrendState.TREND_DOWN.value
            else:
                return TrendState.TREND_UNCERTAIN.value
        else:
            # Middle zone — use MA spread only
            if ma_spread > t["ma_spread_up"]:
                return TrendState.TREND_UP.value
            elif ma_spread < t["ma_spread_down"]:
                return TrendState.TREND_DOWN.value
            else:
                return TrendState.RANGE.value
    
    def _classify_volatility(self, features: dict, missing: list) -> str:
        """Classify volatility dimension from features."""
        if "rolling_volatility" in missing:
            return VolatilityState.VOL_NORMAL.value
        
        vol = features["rolling_volatility"]
        t = self.thresholds["volatility"]
        
        if vol < t["vol_low_annualized"]:
            return VolatilityState.VOL_LOW.value
        elif vol < t["vol_normal_annualized"]:
            return VolatilityState.VOL_NORMAL.value
        elif vol < t["vol_high_annualized"]:
            return VolatilityState.VOL_HIGH.value
        else:
            return VolatilityState.VOL_EXTREME.value
    
    def _classify_stress(self, features: dict, missing: list) -> str:
        """Classify stress dimension from features."""
        if "atr_normalized" in missing and "shock" in missing:
            return StressState.STRESS_NORMAL.value
        
        t = self.thresholds["stress"]
        stress_score = 0
        
        if "atr_normalized" in features:
            atr = features["atr_normalized"]
            if atr > t["atr_elevated_pct"]:
                stress_score += 2
            elif atr > t["atr_normal_pct"]:
                stress_score += 1
        
        if "shock" in features:
            shock = features["shock"]
            if shock > t["shock_threshold"]:
                stress_score += 1
        
        if stress_score >= 3:
            return StressState.STRESS_EXTREME.value
        elif stress_score >= 1:
            return StressState.STRESS_ELEVATED.value
        else:
            return StressState.STRESS_NORMAL.value
    
    def _classify_confidence(
        self, features: dict, missing: list, n_obs: int
    ) -> str:
        """Classify confidence from feature completeness and observation count."""
        min_obs = self.thresholds.get("warmup", {}).get("min_observations", 60)
        
        total_features = len(FEATURE_SPECS)
        available = total_features - len(missing)
        
        if n_obs < min_obs:
            return RegimeConfidence.INSUFFICIENT.value
        
        ratio = available / total_features
        if ratio >= 0.8 and n_obs >= min_obs * 2:
            return RegimeConfidence.HIGH.value
        elif ratio >= 0.6:
            return RegimeConfidence.MEDIUM.value
        elif ratio >= 0.3:
            return RegimeConfidence.LOW.value
        else:
            return RegimeConfidence.INSUFFICIENT.value


# ── Regime Store (SQLite) ──────────────────────────────────────────────────

class RegimeStore:
    """
    SQLite-backed regime state store at state/market_regimes.db.
    
    Schema: regime_builds, regime_features, regime_observations,
    regime_intervals, strategy_regime_evidence, regime_policy_versions.
    """
    
    SCHEMA_VERSION = "1.0"
    
    def __init__(self, db_path: str | Path | None = None):
        if db_path is None:
            db_path = Path(__file__).resolve().parent.parent / "state" / "market_regimes.db"
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
    
    def _init_db(self):
        import sqlite3
        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS schema_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT
                );
                
                CREATE TABLE IF NOT EXISTS regime_builds (
                    regime_build_id TEXT PRIMARY KEY,
                    policy_version TEXT,
                    feature_version TEXT,
                    code_identity TEXT,
                    instruments_json TEXT,
                    timeframes_json TEXT,
                    source_ranges_json TEXT,
                    started_at TEXT,
                    finished_at TEXT,
                    observation_count INTEGER,
                    status TEXT,
                    errors_json TEXT DEFAULT '[]'
                );
                
                CREATE TABLE IF NOT EXISTS regime_features (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    regime_build_id TEXT,
                    instrument TEXT,
                    timeframe TEXT,
                    timestamp TEXT,
                    feature_id TEXT,
                    feature_value REAL,
                    FOREIGN KEY (regime_build_id) REFERENCES regime_builds(regime_build_id)
                );
                
                CREATE TABLE IF NOT EXISTS regime_observations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    instrument TEXT,
                    timeframe TEXT,
                    timestamp TEXT,
                    trend_state TEXT,
                    volatility_state TEXT,
                    stress_state TEXT,
                    confidence TEXT,
                    features_json TEXT,
                    policy_version TEXT,
                    regime_build_id TEXT,
                    FOREIGN KEY (regime_build_id) REFERENCES regime_builds(regime_build_id)
                );
                
                CREATE TABLE IF NOT EXISTS regime_intervals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    interval_id TEXT,
                    instrument TEXT,
                    timeframe TEXT,
                    start_at TEXT,
                    end_at TEXT,
                    trend_state TEXT,
                    volatility_state TEXT,
                    stress_state TEXT,
                    bar_count INTEGER,
                    confidence_summary TEXT,
                    regime_build_id TEXT,
                    FOREIGN KEY (regime_build_id) REFERENCES regime_builds(regime_build_id)
                );
                
                CREATE TABLE IF NOT EXISTS regime_policy_versions (
                    policy_version TEXT PRIMARY KEY,
                    thresholds_json TEXT,
                    created_at TEXT,
                    description TEXT
                );
                
                CREATE TABLE IF NOT EXISTS strategy_regime_evidence (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    strategy_id TEXT,
                    regime_bucket TEXT,
                    evidence_class TEXT,
                    trade_count INTEGER,
                    win_rate REAL,
                    gross_pnl REAL,
                    net_pnl REAL,
                    profit_factor REAL,
                    avg_trade REAL,
                    median_trade REAL,
                    max_win REAL,
                    max_loss REAL,
                    avg_holding_bars REAL,
                    evidence_maturity TEXT,
                    confidence_distribution_json TEXT,
                    regime_confidence_distribution_json TEXT,
                    regime_build_id TEXT,
                    policy_version TEXT,
                    FOREIGN KEY (regime_build_id) REFERENCES regime_builds(regime_build_id)
                );
                
                CREATE INDEX IF NOT EXISTS idx_obs_inst_tf_ts 
                    ON regime_observations(instrument, timeframe, timestamp);
                CREATE INDEX IF NOT EXISTS idx_obs_build 
                    ON regime_observations(regime_build_id);
                CREATE INDEX IF NOT EXISTS idx_intervals_inst_tf 
                    ON regime_intervals(instrument, timeframe);
                CREATE INDEX IF NOT EXISTS idx_features_build 
                    ON regime_features(regime_build_id);
                CREATE INDEX IF NOT EXISTS idx_sre_strategy 
                    ON strategy_regime_evidence(strategy_id);
            """)
            # Set schema version
            conn.execute(
                "INSERT OR REPLACE INTO schema_meta (key, value) VALUES (?, ?)",
                ("schema_version", self.SCHEMA_VERSION),
            )
            conn.commit()
        finally:
            conn.close()
    
    def store_build(self, build: RegimeBuild):
        import sqlite3
        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.execute(
                """INSERT OR REPLACE INTO regime_builds 
                   (regime_build_id, policy_version, feature_version, code_identity,
                    instruments_json, timeframes_json, source_ranges_json,
                    started_at, finished_at, observation_count, status, errors_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (build.regime_build_id, build.policy_version, build.feature_version,
                 build.code_identity, build.instruments_json, build.timeframes_json,
                 build.source_ranges_json, build.started_at, build.finished_at,
                 build.observation_count, build.status, build.errors_json),
            )
            conn.commit()
        finally:
            conn.close()
    
    def store_observations(self, observations: list[RegimeObservation]):
        import sqlite3
        conn = sqlite3.connect(str(self.db_path))
        try:
            for obs in observations:
                conn.execute(
                    """INSERT INTO regime_observations
                       (instrument, timeframe, timestamp, trend_state, volatility_state,
                        stress_state, confidence, features_json, policy_version, regime_build_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (obs.instrument, obs.timeframe, obs.timestamp, obs.trend_state,
                     obs.volatility_state, obs.stress_state, obs.confidence,
                     obs.features_json, obs.policy_version, obs.regime_build_id),
                )
            conn.commit()
        finally:
            conn.close()
    
    def store_intervals(self, intervals: list[RegimeInterval]):
        import sqlite3
        conn = sqlite3.connect(str(self.db_path))
        try:
            for iv in intervals:
                conn.execute(
                    """INSERT INTO regime_intervals
                       (interval_id, instrument, timeframe, start_at, end_at,
                        trend_state, volatility_state, stress_state, bar_count,
                        confidence_summary, regime_build_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (iv.interval_id, iv.instrument, iv.timeframe, iv.start_at,
                     iv.end_at, iv.trend_state, iv.volatility_state, iv.stress_state,
                     iv.bar_count, iv.confidence_summary, iv.regime_build_id),
                )
            conn.commit()
        finally:
            conn.close()
    
    def store_strategy_evidence(self, evidence: list[StrategyRegimeEvidence]):
        import sqlite3
        conn = sqlite3.connect(str(self.db_path))
        try:
            for ev in evidence:
                conn.execute(
                    """INSERT INTO strategy_regime_evidence
                       (strategy_id, regime_bucket, evidence_class, trade_count,
                        win_rate, gross_pnl, net_pnl, profit_factor, avg_trade,
                        median_trade, max_win, max_loss, avg_holding_bars,
                        evidence_maturity, confidence_distribution_json,
                        regime_confidence_distribution_json, regime_build_id, policy_version)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (ev.strategy_id, ev.regime_bucket, ev.evidence_class, ev.trade_count,
                     ev.win_rate, ev.gross_pnl, ev.net_pnl, ev.profit_factor,
                     ev.avg_trade, ev.median_trade, ev.max_win, ev.max_loss,
                     ev.avg_holding_bars, ev.evidence_maturity,
                     ev.confidence_distribution_json,
                     ev.regime_confidence_distribution_json,
                     ev.regime_build_id, ev.policy_version),
                )
            conn.commit()
        finally:
            conn.close()
    
    def store_policy_version(self, policy_version: str, thresholds: dict,
                             description: str = ""):
        import sqlite3
        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.execute(
                """INSERT OR REPLACE INTO regime_policy_versions
                   (policy_version, thresholds_json, created_at, description)
                   VALUES (?, ?, ?, ?)""",
                (policy_version, json.dumps(thresholds, sort_keys=True),
                 datetime.now(timezone.utc).isoformat(), description),
            )
            conn.commit()
        finally:
            conn.close()
    
    def get_latest_build(self) -> Optional[RegimeBuild]:
        import sqlite3
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT * FROM regime_builds ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            return RegimeBuild(
                regime_build_id=row["regime_build_id"],
                policy_version=row["policy_version"],
                feature_version=row["feature_version"],
                code_identity=row["code_identity"],
                instruments_json=row["instruments_json"],
                timeframes_json=row["timeframes_json"],
                source_ranges_json=row["source_ranges_json"],
                started_at=row["started_at"],
                finished_at=row["finished_at"],
                observation_count=row["observation_count"],
                status=row["status"],
                errors_json=row["errors_json"],
            )
        finally:
            conn.close()
    
    def get_regime(self, instrument: str, timeframe: str,
                   timestamp: str) -> Optional[dict]:
        """Get regime observation for specific instrument/timeframe/timestamp."""
        import sqlite3
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                """SELECT * FROM regime_observations 
                   WHERE instrument=? AND timeframe=? AND timestamp=?
                   ORDER BY id DESC LIMIT 1""",
                (instrument, timeframe, timestamp),
            ).fetchone()
            if row is None:
                return None
            return dict(row)
        finally:
            conn.close()
    
    def get_current_regime(self, instrument: str,
                           timeframe: str) -> Optional[dict]:
        """Get most recent regime observation for instrument/timeframe."""
        import sqlite3
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                """SELECT * FROM regime_observations 
                   WHERE instrument=? AND timeframe=?
                   ORDER BY timestamp DESC LIMIT 1""",
                (instrument, timeframe),
            ).fetchone()
            if row is None:
                return None
            return dict(row)
        finally:
            conn.close()
    
    def get_regime_observations(self, instrument: str, timeframe: str,
                                start: str = None, end: str = None) -> list[dict]:
        """Get regime observations for instrument/timeframe in time range."""
        import sqlite3
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            query = """SELECT * FROM regime_observations 
                       WHERE instrument=? AND timeframe=?"""
            params = [instrument, timeframe]
            if start:
                query += " AND timestamp >= ?"
                params.append(start)
            if end:
                query += " AND timestamp <= ?"
                params.append(end)
            query += " ORDER BY timestamp ASC"
            rows = conn.execute(query, params).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
    
    def get_regime_intervals(self, instrument: str,
                             timeframe: str) -> list[dict]:
        """Get regime intervals for instrument/timeframe."""
        import sqlite3
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """SELECT * FROM regime_intervals 
                   WHERE instrument=? AND timeframe=?
                   ORDER BY start_at ASC""",
                (instrument, timeframe),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
    
    def get_strategy_evidence(self, strategy_id: str) -> list[dict]:
        """Get strategy regime evidence."""
        import sqlite3
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """SELECT * FROM strategy_regime_evidence 
                   WHERE strategy_id=? ORDER BY evidence_class""",
                (strategy_id,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
    
    def get_regime_distribution(self, instrument: str,
                                timeframe: str) -> dict:
        """Get regime distribution for instrument/timeframe."""
        import sqlite3
        conn = sqlite3.connect(str(self.db_path))
        try:
            trend = conn.execute(
                """SELECT trend_state, COUNT(*) as cnt 
                   FROM regime_observations 
                   WHERE instrument=? AND timeframe=?
                   GROUP BY trend_state""",
                (instrument, timeframe),
            ).fetchall()
            vol = conn.execute(
                """SELECT volatility_state, COUNT(*) as cnt 
                   FROM regime_observations 
                   WHERE instrument=? AND timeframe=?
                   GROUP BY volatility_state""",
                (instrument, timeframe),
            ).fetchall()
            stress = conn.execute(
                """SELECT stress_state, COUNT(*) as cnt 
                   FROM regime_observations 
                   WHERE instrument=? AND timeframe=?
                   GROUP BY stress_state""",
                (instrument, timeframe),
            ).fetchall()
            return {
                "trend": {r[0]: r[1] for r in trend},
                "volatility": {r[0]: r[1] for r in vol},
                "stress": {r[0]: r[1] for r in stress},
            }
        finally:
            conn.close()
    
    def get_transition_count(self, instrument: str,
                             timeframe: str) -> int:
        """Count regime transitions (state changes) for instrument/timeframe."""
        import sqlite3
        conn = sqlite3.connect(str(self.db_path))
        try:
            rows = conn.execute(
                """SELECT trend_state FROM regime_observations 
                   WHERE instrument=? AND timeframe=?
                   ORDER BY timestamp ASC""",
                (instrument, timeframe),
            ).fetchall()
            if len(rows) < 2:
                return 0
            transitions = 0
            prev = rows[0][0]
            for r in rows[1:]:
                if r[0] != prev:
                    transitions += 1
                    prev = r[0]
            return transitions
        finally:
            conn.close()
    
    def is_db_accessible(self) -> bool:
        """Health check: can the DB be read?"""
        try:
            import sqlite3
            conn = sqlite3.connect(str(self.db_path))
            conn.execute("SELECT 1")
            conn.close()
            return True
        except Exception:
            return False


# ── Builder Functions ──────────────────────────────────────────────────────

def load_csv(filepath: str | Path) -> pd.DataFrame:
    """Load OHLCV CSV with timezone-aware timestamps."""
    df = pd.read_csv(filepath, parse_dates=["time"])
    # Ensure timezone-aware
    if df["time"].dt.tz is None:
        df["time"] = df["time"].dt.tz_localize("UTC")
    return df


def build_regime_observations(
    instrument: str,
    timeframe: str,
    df: pd.DataFrame,
    regime_build_id: str,
    thresholds: dict | None = None,
) -> list[RegimeObservation]:
    """
    Build regime observations for all bars in a DataFrame.
    
    Uses only data available at each bar timestamp (prefix-only).
    """
    classifier = RegimeClassifier(thresholds)
    features_df = compute_features(df, thresholds)
    
    observations = []
    min_obs = (thresholds or DEFAULT_THRESHOLDS)["warmup"]["min_observations"]
    
    for i in range(len(features_df)):
        result = classifier.classify_bar(features_df, i)
        ts = str(features_df["time"].iloc[i])
        
        obs = RegimeObservation(
            instrument=instrument,
            timeframe=timeframe,
            timestamp=ts,
            trend_state=result["trend_state"],
            volatility_state=result["volatility_state"],
            stress_state=result["stress_state"],
            confidence=result["confidence"],
            features_json=json.dumps(result["features"], sort_keys=True),
            policy_version=THRESHOLD_VERSION,
            regime_build_id=regime_build_id,
        )
        observations.append(obs)
    
    return observations


def build_regime_intervals(observations: list[RegimeObservation], regime_build_id: str = "") -> list[RegimeInterval]:
    """
    Derive contiguous intervals from observations.
    
    An interval spans consecutive observations with the same
    trend_state + volatility_state + stress_state.
    """
    if not observations:
        return []
    
    intervals = []
    current = observations[0]
    bar_count = 1
    
    for obs in observations[1:]:
        same = (
            obs.trend_state == current.trend_state
            and obs.volatility_state == current.volatility_state
            and obs.stress_state == current.stress_state
        )
        if same:
            bar_count += 1
        else:
            # Close current interval
            interval = RegimeInterval(
                interval_id=str(uuid.uuid4()),
                instrument=current.instrument,
                timeframe=current.timeframe,
                start_at=current.timestamp,
                end_at=observations[observations.index(obs) - 1].timestamp,
                trend_state=current.trend_state,
                volatility_state=current.volatility_state,
                stress_state=current.stress_state,
                bar_count=bar_count,
                confidence_summary=_summarize_confidence(
                    observations, current.timestamp, obs.timestamp
                ),
                regime_build_id=regime_build_id,
            )
            intervals.append(interval)
            current = obs
            bar_count = 1
    
    # Close last interval
    interval = RegimeInterval(
        interval_id=str(uuid.uuid4()),
        instrument=current.instrument,
        timeframe=current.timeframe,
        start_at=current.timestamp,
        end_at=observations[-1].timestamp,
        trend_state=current.trend_state,
        volatility_state=current.volatility_state,
        stress_state=current.stress_state,
        bar_count=bar_count,
        confidence_summary=_summarize_confidence(
            observations, current.timestamp, observations[-1].timestamp
        ),
        regime_build_id=regime_build_id,
    )
    intervals.append(interval)
    
    return intervals


# build_regime_intervals is defined above with regime_build_id parameter


def _summarize_confidence(observations: list, start: str, end: str) -> str:
    """Summarize confidence distribution within an interval."""
    counts = {}
    for obs in observations:
        if start <= obs.timestamp <= end:
            c = obs.confidence
            counts[c] = counts.get(c, 0) + 1
    if not counts:
        return "UNKNOWN"
    dominant = max(counts, key=counts.get)
    total = sum(counts.values())
    pct = counts[dominant] / total * 100
    return f"{dominant}({pct:.0f}%)"


def get_current_regime(
    store: RegimeStore,
    instrument: str,
    timeframe: str,
    data_path: str | Path | None = None,
    freshness_policy: dict | None = None,
) -> CurrentRegimeSnapshot:
    """
    Get current regime snapshot with freshness check.
    
    Returns FRESH if data is recent enough per policy,
    STALE otherwise.
    """
    freshness_policy = freshness_policy or FRESHNESS_POLICY
    
    # Try store first
    stored = store.get_current_regime(instrument, timeframe)
    
    if stored is None:
        return CurrentRegimeSnapshot(
            instrument=instrument,
            timeframe=timeframe,
            trend_state=TrendState.TREND_UNCERTAIN.value,
            volatility_state=VolatilityState.VOL_NORMAL.value,
            stress_state=StressState.STRESS_NORMAL.value,
            confidence=RegimeConfidence.INSUFFICIENT.value,
            as_of="",
            data_freshness="UNKNOWN",
            regime_build_id="",
        )
    
    as_of = stored["timestamp"]
    
    # Check freshness
    try:
        ts = pd.Timestamp(as_of)
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        now = pd.Timestamp.now(tz="UTC")
        age_seconds = (now - ts).total_seconds()
        max_age = freshness_policy.get(timeframe, 24 * 3600)
        freshness = "FRESH" if age_seconds <= max_age else "STALE"
    except Exception:
        freshness = "UNKNOWN"
    
    return CurrentRegimeSnapshot(
        instrument=instrument,
        timeframe=timeframe,
        trend_state=stored["trend_state"],
        volatility_state=stored["volatility_state"],
        stress_state=stored["stress_state"],
        confidence=stored["confidence"],
        as_of=as_of,
        data_freshness=freshness,
        regime_build_id=stored.get("regime_build_id", ""),
    )


# ── Strategy Regime Evidence ──────────────────────────────────────────────

def map_strategy_to_regime(
    trades: list[dict],
    store: RegimeStore,
    instrument: str,
    timeframe: str,
) -> list[dict]:
    """
    Map attributed trade outcomes to the regime in which they occurred.
    
    For each trade, looks up regime at entry/exit timestamps.
    Returns enriched trade records with regime labels.
    """
    enriched = []
    for trade in trades:
        entry_ts = trade.get("ts_open", "")
        exit_ts = trade.get("ts_close", "")
        
        entry_regime = store.get_regime(instrument, timeframe, entry_ts)
        exit_regime = store.get_regime(instrument, timeframe, exit_ts)
        
        # Try exact match first, then nearest
        if entry_regime is None:
            entry_regime = _find_nearest_regime(store, instrument, timeframe, entry_ts)
        if exit_regime is None:
            exit_regime = _find_nearest_regime(store, instrument, timeframe, exit_ts)
        
        record = {
            **trade,
            "entry_regime_trend": entry_regime.get("trend_state", "UNKNOWN") if entry_regime else "UNKNOWN",
            "entry_regime_vol": entry_regime.get("volatility_state", "UNKNOWN") if entry_regime else "UNKNOWN",
            "entry_regime_stress": entry_regime.get("stress_state", "UNKNOWN") if entry_regime else "UNKNOWN",
            "exit_regime_trend": exit_regime.get("trend_state", "UNKNOWN") if exit_regime else "UNKNOWN",
            "exit_regime_vol": exit_regime.get("volatility_state", "UNKNOWN") if exit_regime else "UNKNOWN",
            "exit_regime_stress": exit_regime.get("stress_state", "UNKNOWN") if exit_regime else "UNKNOWN",
            "regime_evidence_class": _classify_regime_evidence(entry_regime, exit_regime),
        }
        enriched.append(record)
    
    return enriched


def _find_nearest_regime(store, instrument, timeframe, timestamp,
                       max_gap_hours: float = 48.0):
    """Find nearest regime observation to a timestamp.
    
    Returns None if the nearest observation is more than max_gap_hours away,
    to prevent assigning stale regime labels to trades far from any observation.
    """
    import sqlite3
    conn = sqlite3.connect(str(store.db_path))
    conn.row_factory = sqlite3.Row
    try:
        # Normalize timestamp for comparison (replace T with space for consistency)
        ts_norm = timestamp.replace("T", " ")
        row = conn.execute(
            """SELECT * FROM regime_observations 
               WHERE instrument=? AND timeframe=? AND timestamp <= ?
               ORDER BY timestamp DESC LIMIT 1""",
            (instrument, timeframe, ts_norm),
        ).fetchone()
        if row:
            # Check staleness
            try:
                obs_ts = pd.Timestamp(row["timestamp"])
                trade_ts = pd.Timestamp(ts_norm)
                if obs_ts.tzinfo is None:
                    obs_ts = obs_ts.tz_localize("UTC")
                if trade_ts.tzinfo is None:
                    trade_ts = trade_ts.tz_localize("UTC")
                gap_hours = abs((trade_ts - obs_ts).total_seconds()) / 3600
                if gap_hours > max_gap_hours:
                    return None
            except Exception:
                pass
            return dict(row)
        # Try after
        row = conn.execute(
            """SELECT * FROM regime_observations 
               WHERE instrument=? AND timeframe=? AND timestamp >= ?
               ORDER BY timestamp ASC LIMIT 1""",
            (instrument, timeframe, ts_norm),
        ).fetchone()
        if row:
            try:
                obs_ts = pd.Timestamp(row["timestamp"])
                trade_ts = pd.Timestamp(ts_norm)
                if obs_ts.tzinfo is None:
                    obs_ts = obs_ts.tz_localize("UTC")
                if trade_ts.tzinfo is None:
                    trade_ts = trade_ts.tz_localize("UTC")
                gap_hours = abs((trade_ts - obs_ts).total_seconds()) / 3600
                if gap_hours > max_gap_hours:
                    return None
            except Exception:
                pass
            return dict(row)
        return None
    finally:
        conn.close()


def _classify_regime_evidence(entry_regime, exit_regime):
    """Classify regime evidence quality."""
    if entry_regime is None and exit_regime is None:
        return "INSUFFICIENT_REGIME"
    if entry_regime and entry_regime.get("confidence") == "INSUFFICIENT":
        return "WEAK_REGIME"
    if exit_regime and exit_regime.get("confidence") == "INSUFFICIENT":
        return "WEAK_REGIME"
    return "USABLE_REGIME"


def get_strategy_regime_performance(
    enriched_trades: list[dict],
    evidence_class: str = "PAPER",
) -> list[StrategyRegimeEvidence]:
    """
    Derive regime-specific strategy performance aggregates.
    
    Groups by regime bucket (trend_state + vol_state) and computes
    per-bucket metrics. Sparse buckets labeled INSUFFICIENT.
    """
    MIN_TRADES_FOR_EVIDENCE = 5
    
    # Group by regime bucket
    buckets = {}
    for trade in enriched_trades:
        trend = trade.get("entry_regime_trend", "UNKNOWN")
        vol = trade.get("entry_regime_vol", "UNKNOWN")
        key = f"{trend}+{vol}"
        if key not in buckets:
            buckets[key] = []
        buckets[key].append(trade)
    
    results = []
    for bucket, trades in sorted(buckets.items()):
        count = len(trades)
        pnls = [t.get("pnl_rub", 0) or 0 for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        
        win_rate = len(wins) / count if count > 0 else None
        gross_pnl = sum(pnls) if pnls else None
        net_pnl = gross_pnl  # simplified — no commission model here
        
        gross_wins = sum(w for w in wins) if wins else 0
        gross_losses = abs(sum(l for l in losses)) if losses else 0
        profit_factor = gross_wins / gross_losses if gross_losses > 0 else None
        
        avg_trade = sum(pnls) / count if count > 0 else None
        sorted_pnls = sorted(pnls)
        median_trade = sorted_pnls[len(sorted_pnls) // 2] if sorted_pnls else None
        max_win = max(pnls) if pnls else None
        max_loss = min(pnls) if pnls else None
        
        # Confidence distribution
        conf_dist = {}
        for t in trades:
            c = t.get("regime_evidence_class", "UNKNOWN")
            conf_dist[c] = conf_dist.get(c, 0) + 1
        
        # Evidence maturity
        if count < MIN_TRADES_FOR_EVIDENCE:
            maturity = EvidenceMaturity.INSUFFICIENT.value
        elif count < 20:
            maturity = EvidenceMaturity.EARLY.value
        elif count < 50:
            maturity = EvidenceMaturity.USABLE.value
        else:
            maturity = EvidenceMaturity.MATURE.value
        
        results.append(StrategyRegimeEvidence(
            strategy_id=trades[0].get("strategy", "unknown") if trades else "unknown",
            regime_bucket=bucket,
            evidence_class=evidence_class,
            trade_count=count,
            win_rate=round(win_rate, 4) if win_rate is not None else None,
            gross_pnl=round(gross_pnl, 2) if gross_pnl is not None else None,
            net_pnl=round(net_pnl, 2) if net_pnl is not None else None,
            profit_factor=round(profit_factor, 4) if profit_factor is not None else None,
            avg_trade=round(avg_trade, 2) if avg_trade is not None else None,
            median_trade=round(median_trade, 2) if median_trade is not None else None,
            max_win=round(max_win, 2) if max_win is not None else None,
            max_loss=round(max_loss, 2) if max_loss is not None else None,
            avg_holding_bars=None,  # not computed here
            evidence_maturity=maturity,
            confidence_distribution_json=json.dumps(conf_dist),
            regime_confidence_distribution_json=json.dumps({}),
            regime_build_id="",
            policy_version=THRESHOLD_VERSION,
        ))
    
    return results


# ── Full Build Pipeline ────────────────────────────────────────────────────

def run_regime_build(
    data_dir: str | Path,
    instruments: list[str],
    timeframes: list[str] | None = None,
    store: RegimeStore | None = None,
    thresholds: dict | None = None,
) -> dict:
    """
    Full regime build pipeline.
    
    1. Load CSV data for each instrument/timeframe
    2. Compute features and classify regimes
    3. Build intervals
    4. Store to regime DB
    5. Return build summary
    
    Zero broker/registry/risk/execution mutation.
    """
    if timeframes is None:
        timeframes = ["15m"]
    
    if store is None:
        store = RegimeStore()
    
    build_id = f"build_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc).isoformat()
    
    all_observations = []
    all_intervals = []
    errors = []
    source_ranges = {}
    
    for inst in instruments:
        for tf in timeframes:
            try:
                # Find CSV file
                csv_path = _find_csv(data_dir, inst, tf)
                if csv_path is None:
                    errors.append(f"NO_DATA: {inst}/{tf}")
                    source_ranges[f"{inst}/{tf}"] = {"status": "missing"}
                    continue
                
                df = load_csv(csv_path)
                obs_count_before = len(df)
                
                # Build observations
                obs = build_regime_observations(inst, tf, df, build_id, thresholds)
                all_observations.extend(obs)
                
                # Build intervals
                ivs = build_regime_intervals(obs, build_id)
                all_intervals.extend(ivs)
                
                # Record source range
                source_ranges[f"{inst}/{tf}"] = {
                    "file": str(csv_path),
                    "bars": obs_count_before,
                    "first": str(df["time"].iloc[0]),
                    "last": str(df["time"].iloc[-1]),
                }
            except Exception as e:
                errors.append(f"ERROR: {inst}/{tf}: {str(e)}")
    
    # Store to DB
    build = RegimeBuild(
        regime_build_id=build_id,
        policy_version=THRESHOLD_VERSION,
        feature_version=FEATURE_VERSION,
        code_identity=f"market_regime.py:{FEATURE_VERSION}",
        instruments_json=json.dumps(instruments),
        timeframes_json=json.dumps(timeframes),
        source_ranges_json=json.dumps(source_ranges),
        started_at=now,
        finished_at=datetime.now(timezone.utc).isoformat(),
        observation_count=len(all_observations),
        status="COMPLETED" if not errors else "COMPLETED_WITH_ERRORS",
        errors_json=json.dumps(errors),
    )
    
    store.store_build(build)
    store.store_observations(all_observations)
    store.store_intervals(all_intervals)
    store.store_policy_version(THRESHOLD_VERSION, thresholds or DEFAULT_THRESHOLDS,
                               "Iteration 15 baseline")
    
    # Distribution summary
    distribution = {}
    for inst in instruments:
        for tf in timeframes:
            distribution[f"{inst}/{tf}"] = store.get_regime_distribution(inst, tf)
    
    return {
        "regime_build_id": build_id,
        "policy_version": THRESHOLD_VERSION,
        "feature_version": FEATURE_VERSION,
        "instruments": instruments,
        "timeframes": timeframes,
        "observation_count": len(all_observations),
        "interval_count": len(all_intervals),
        "errors": errors,
        "source_ranges": source_ranges,
        "distribution": distribution,
    }


def _find_csv(data_dir: str | Path, instrument: str, timeframe: str) -> Optional[Path]:
    """Find CSV file for instrument/timeframe."""
    data_dir = Path(data_dir)
    # Try 365d first, then 1095d, then 60d
    for days in ["365d", "1095d", "60d"]:
        csv_path = data_dir / f"{instrument}_{days}_{timeframe}_continuous.csv"
        if csv_path.exists():
            return csv_path
    return None
