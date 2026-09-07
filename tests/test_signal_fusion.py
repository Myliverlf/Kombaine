"""Tests for Signal Fusion module.

≥3 fixtures, covers: majority_vote, unanimous, weighted_average,
RI excluded → VETO, max_contracts=1, compare_compositions, guard.
"""
import json
import os
import sys

import numpy as np
import pandas as pd
import pytest

# Ensure code/ dir on sys.path
_CODE_DIR = os.path.join(os.path.dirname(__file__), "..", "code")
if _CODE_DIR not in sys.path:
    sys.path.insert(0, _CODE_DIR)

from signal_fusion import (
    FusionRule,
    fuse_signals,
    check_composition_excluded,
    check_no_broker_imports,
    assert_no_broker_imports,
    MAX_STRATEGIES_PER_FUSION,
    EXCLUDED_TICKERS,
)
from fusion_scorecard import (
    FusionScorecard,
    score_fusion,
    score_fusion_with_agreement,
    compare_compositions,
    compute_agreement_ratio,
    select_top_compositions,
)
from fusion_pipeline import (
    build_fusion_candidate,
    run_fusion_pipeline,
    validate_fusion_constraints,
)
from fusion_guard import (
    check_all_fusion_files,
    compute_baseline_hashes,
    snapshot_baseline,
    assert_baseline_intact,
)


# ─── Fixtures ──────────────────────────────────────────────────────────

FIXTURE_PATH = os.path.join(
    os.path.dirname(__file__), "fixtures", "fusion_fixture.json"
)


@pytest.fixture(scope="module")
def fusion_data():
    """Load synthetic fusion fixture data."""
    with open(FIXTURE_PATH, "r") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def signals_dict(fusion_data):
    """Convert fixture signals to dict of pd.Series."""
    idx = fusion_data["ohlcv"]["index"]
    return {
        name: pd.Series(vals, index=idx, dtype=int)
        for name, vals in fusion_data["signals"].items()
    }


@pytest.fixture(scope="module")
def returns_series(fusion_data):
    """Convert fixture returns to pd.Series."""
    idx = fusion_data["ohlcv"]["index"]
    return pd.Series(fusion_data["ohlcv"]["returns"], index=idx, dtype=float)


@pytest.fixture
def sample_fused_signal():
    """A simple fused signal for scoring tests."""
    return pd.Series([0, 1, 1, 1, -1, -1, -1, 0, 1, 1], dtype=int)


@pytest.fixture
def sample_returns():
    """Simple returns for scoring tests."""
    return pd.Series([0, 0.01, 0.01, -0.01, -0.01, 0.01, 0.01, -0.01, 0.01, -0.01], dtype=float)


@pytest.fixture
def baseline_snapshot():
    """Snapshot of baseline hashes (for baseline-intact tests)."""
    return snapshot_baseline()


# ─── Test: FusionRule validation ──────────────────────────────────────

class TestFusionRule:
    """Tests for FusionRule dataclass validation."""

    def test_min_strategies(self):
        """FusionRule requires >=2 strategies."""
        with pytest.raises(ValueError, match=">=2"):
            FusionRule(strategies=["sma_cross"])

    def test_max_strategies(self):
        """FusionRule max 3 strategies."""
        with pytest.raises(ValueError, match="max 3"):
            FusionRule(strategies=["sma_cross", "rsi", "macd", "donchian"])

    def test_invalid_method(self):
        """FusionRule rejects unknown methods."""
        with pytest.raises(ValueError, match="Unknown fusion method"):
            FusionRule(strategies=["sma_cross", "rsi"], method="bad_method")

    def test_valid_majority(self):
        """Valid FusionRule for majority_vote."""
        rule = FusionRule(strategies=["sma_cross", "rsi_reversal"])
        assert rule.method == "majority_vote"
        assert len(rule.strategies) == 2


# ─── Test: majority_vote ──────────────────────────────────────────────

class TestMajorityVote:
    """Tests for majority_vote fusion method."""

    def test_2_of_3_agree(self, signals_dict, fusion_data):
        """majority_vote: 2/3 agree → direction."""
        rule = FusionRule(strategies=["sma_cross", "rsi_reversal", "macd_trend"],
                          method="majority_vote")
        result = fuse_signals(signals_dict, [rule])
        label = "+".join(rule.strategies) + "_majority_vote"
        fused = result[label]

        # At index=1: sma_cross=1, rsi_reversal=1, macd_trend=0 → majority=1
        assert fused.iloc[1] == 1
        # At index=5: sma_cross=-1, rsi_reversal=-1, macd_trend=-1 → majority=-1
        assert fused.iloc[5] == -1
        # At index=0: all=0 → majority=0
        assert fused.iloc[0] == 0

    def test_split_vote(self):
        """majority_vote: 1 positive, 1 negative → 0."""
        idx = range(5)
        signals = {
            "a": pd.Series([1, 0, -1, 1, 0], index=idx, dtype=int),
            "b": pd.Series([-1, 0, 1, 1, 0], index=idx, dtype=int),
        }
        rule = FusionRule(strategies=["a", "b"], method="majority_vote")
        result = fuse_signals(signals, [rule])
        fused = result["a+b_majority_vote"]

        # index=0: a=1, b=-1 → sum=0 → fused=0
        assert fused.iloc[0] == 0
        # index=1: a=0, b=0 → sum=0 → fused=0
        assert fused.iloc[1] == 0
        # index=3: a=1, b=1 → sum=2 → fused=1
        assert fused.iloc[3] == 1


# ─── Test: unanimous ──────────────────────────────────────────────────

class TestUnanimous:
    """Tests for unanimous fusion method."""

    def test_all_agree(self):
        """unanimous: all agree → signal."""
        idx = range(5)
        signals = {
            "a": pd.Series([1, 1, -1, -1, 0], index=idx, dtype=int),
            "b": pd.Series([1, 1, -1, -1, 0], index=idx, dtype=int),
            "c": pd.Series([1, 1, -1, -1, 0], index=idx, dtype=int),
        }
        rule = FusionRule(strategies=["a", "b", "c"], method="unanimous")
        result = fuse_signals(signals, [rule])
        fused = result["a+b+c_unanimous"]

        assert fused.iloc[0] == 1
        assert fused.iloc[2] == -1
        assert fused.iloc[4] == 0

    def test_one_dissent(self):
        """unanimous: one dissent → 0."""
        idx = range(4)
        signals = {
            "a": pd.Series([1, 1, -1, -1], index=idx, dtype=int),
            "b": pd.Series([1, 0, -1, -1], index=idx, dtype=int),
            "c": pd.Series([1, 1, -1, 1], index=idx, dtype=int),
        }
        rule = FusionRule(strategies=["a", "b", "c"], method="unanimous")
        result = fuse_signals(signals, [rule])
        fused = result["a+b+c_unanimous"]

        assert fused.iloc[0] == 1   # all agree: +1
        assert fused.iloc[1] == 0   # b=0 → not unanimous
        assert fused.iloc[2] == -1  # all agree: -1
        assert fused.iloc[3] == 0   # c=1 → not unanimous


# ─── Test: weighted_average ──────────────────────────────────────────

class TestWeightedAverage:
    """Tests for weighted_average fusion method."""

    def test_weights_influence(self):
        """weighted_average: heavier weight dominates."""
        idx = range(5)
        signals = {
            "heavy": pd.Series([1, -1, 1, 0, 0], index=idx, dtype=int),
            "light": pd.Series([-1, -1, -1, 1, 0], index=idx, dtype=int),
        }
        rule = FusionRule(
            strategies=["heavy", "light"],
            method="weighted_average",
            weights={"heavy": 10.0, "light": 1.0},
        )
        result = fuse_signals(signals, [rule])
        fused = result["heavy+light_weighted_average"]

        # index=0: heavy=1*10 + light=-1*1 = 9 → +1
        assert fused.iloc[0] == 1
        # index=1: heavy=-1*10 + light=-1*1 = -11 → -1
        assert fused.iloc[1] == -1
        # index=3: heavy=0*10 + light=1*1 = 1 → +1
        assert fused.iloc[3] == 1


# ─── Test: threshold ──────────────────────────────────────────────────

class TestThreshold:
    """Tests for threshold fusion method."""

    def test_above_threshold(self):
        """threshold: fraction >= threshold → direction."""
        idx = range(6)
        signals = {
            "a": pd.Series([1, 1, 1, -1, -1, 0], index=idx, dtype=int),
            "b": pd.Series([1, 1, 0, -1, 0, 0], index=idx, dtype=int),
            "c": pd.Series([1, 0, 0, -1, 0, 0], index=idx, dtype=int),
        }
        rule = FusionRule(
            strategies=["a", "b", "c"],
            method="threshold",
            threshold=0.66,
        )
        result = fuse_signals(signals, [rule])
        fused = result["a+b+c_threshold"]

        # index=0: 3/3=1.0 >= 0.66 → +1
        assert fused.iloc[0] == 1
        # index=1: 2/3=0.67 >= 0.66 → +1
        assert fused.iloc[1] == 1
        # index=2: 1/3=0.33 < 0.66 → 0
        assert fused.iloc[2] == 0
        # index=3: 3/3=1.0 >= 0.66 → -1
        assert fused.iloc[3] == -1


# ─── Test: RI excluded ───────────────────────────────────────────────

class TestRIExcluded:
    """Tests for RI ticker exclusion (VETO)."""

    def test_ri_in_composition_veto(self, fusion_data):
        """Composition with RI → VETO."""
        ri_test = fusion_data["ri_excluded_test"]
        assert check_composition_excluded(ri_test["strategies"])

    def test_no_ri_ok(self):
        """Composition without RI → no VETO."""
        assert not check_composition_excluded(["sma_cross", "rsi_reversal"])

    def test_ri_veto_in_pipeline(self, signals_dict, returns_series):
        """Pipeline: RI in composition → composite_score = -inf."""
        ri_rule = FusionRule(
            strategies=["sma_cross", "rsi_reversal"],
            method="majority_vote",
        )
        # Create signals with RI
        signals_with_ri = dict(signals_dict)
        signals_with_ri["RI"] = signals_dict["sma_cross"].copy()

        config = {"excluded": ["RI"], "risk": {"max_slots": 3}}
        # Manually build fusion candidate with RI
        fused = signals_dict["sma_cross"] + signals_dict["rsi_reversal"]
        fused = fused.apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
        candidate = build_fusion_candidate(
            composition_label="sma+rsi",
            fused_signal=fused,
            strategies=["sma_cross", "RI", "rsi_reversal"],
            method="majority_vote",
            config=config,
        )
        assert candidate["veto"] is True
        assert "RI" in candidate["veto_reason"]


# ─── Test: max_contracts = 1 ──────────────────────────────────────────

class TestMaxContracts:
    """Tests for 1 contract per entry constraint."""

    def test_contracts_always_1(self):
        """build_fusion_candidate always returns contracts=1."""
        dummy_signal = pd.Series([0, 1, 1], dtype=int)
        candidate = build_fusion_candidate(
            composition_label="test",
            fused_signal=dummy_signal,
            strategies=["a", "b"],
            method="majority_vote",
            config={"max_contracts_per_entry": 1},
        )
        assert candidate["contracts"] == 1

    def test_contracts_veto_is_0(self):
        """Vetoed candidate has contracts=0."""
        dummy_signal = pd.Series([0, 1, 1], dtype=int)
        candidate = build_fusion_candidate(
            composition_label="test",
            fused_signal=dummy_signal,
            strategies=["a", "b"],
            method="majority_vote",
            ticker="RI",
            config={"excluded": ["RI"]},
        )
        assert candidate["contracts"] == 0
        assert candidate["veto"] is True


# ─── Test: compare_compositions ──────────────────────────────────────

class TestCompareCompositions:
    """Tests for composition ranking."""

    def test_ranking_order(self):
        """compare_compositions returns desc order by composite_score."""
        sc1 = FusionScorecard(composition_label="a", composite_score=0.5)
        sc2 = FusionScorecard(composition_label="b", composite_score=0.8)
        sc3 = FusionScorecard(composition_label="c", composite_score=0.3)
        ranked = compare_compositions([sc1, sc2, sc3])
        assert ranked[0].composition_label == "b"
        assert ranked[1].composition_label == "a"
        assert ranked[2].composition_label == "c"

    def test_veto_excluded(self):
        """select_top_compositions excludes RI compositions."""
        sc1 = FusionScorecard(
            composition_label="sma+rsi", composite_score=0.5,
            strategies=["sma_cross", "rsi_reversal"],
        )
        sc2 = FusionScorecard(
            composition_label="sma+RI", composite_score=0.9,
            strategies=["sma_cross", "RI"],
        )
        selected = select_top_compositions(
            [sc1, sc2], max_slots=3, excluded={"RI"}
        )
        labels = [s.composition_label for s in selected]
        assert "sma+RI" not in labels
        assert sc2.composite_score == -float("inf")

    def test_max_slots_limit(self):
        """select_top_compositions respects max_slots."""
        scores = [
            FusionScorecard(composition_label=f"c{i}", composite_score=float(i))
            for i in range(6)
        ]
        selected = select_top_compositions(scores, max_slots=3)
        assert len(selected) == 3


# ─── Test: FusionScorecard ──────────────────────────────────────────

class TestFusionScorecard:
    """Tests for fusion scoring."""

    def test_score_basic(self, sample_fused_signal, sample_returns):
        """score_fusion produces valid scorecard."""
        sc = score_fusion(
            composition_label="test",
            fused_signal=sample_fused_signal,
            returns=sample_returns,
            strategies=["a", "b"],
            method="majority_vote",
        )
        assert isinstance(sc, FusionScorecard)
        assert sc.composition_label == "test"
        assert sc.contracts == 1
        assert sc.n_bars == 10
        assert sc.composite_score != -999.0

    def test_to_dict(self, sample_fused_signal, sample_returns):
        """FusionScorecard.to_dict returns valid dict."""
        sc = score_fusion(
            composition_label="test_dict",
            fused_signal=sample_fused_signal,
            returns=sample_returns,
            strategies=["a", "b"],
            method="unanimous",
        )
        d = sc.to_dict()
        assert d["composition_label"] == "test_dict"
        assert d["contracts"] == 1
        assert "expectancy_r" in d
        assert "risk_penalty_raw" in d

    def test_agreement_ratio(self):
        """compute_agreement_ratio calculates correctly."""
        idx = range(5)
        df = pd.DataFrame({
            "a": [1, 1, -1, 0, 1],
            "b": [1, -1, -1, 0, 0],
            "c": [1, 1, -1, 1, -1],
        }, index=idx)
        ratio = compute_agreement_ratio(df)
        # index 0: all=1 → agree; index 2: all=-1 → agree; total=2/5=0.4
        assert abs(ratio - 0.4) < 0.001

    def test_agreement_ratio_single_column(self):
        """Single column DataFrame → agreement = 1.0."""
        df = pd.DataFrame({"a": [1, -1, 0]})
        ratio = compute_agreement_ratio(df)
        assert ratio == 1.0


# ─── Test: AST guard ─────────────────────────────────────────────────

class TestASTGuard:
    """Tests for broker-import guard."""

    def test_fusion_files_no_broker(self):
        """All fusion files have no broker imports."""
        errors = check_all_fusion_files()
        assert errors == [], f"Broker imports detected: {errors}"

    def test_guard_on_fusion_files(self):
        """check_no_broker_imports returns True for each fusion file."""
        fusion_files = [
            os.path.join(_CODE_DIR, "signal_fusion.py"),
            os.path.join(_CODE_DIR, "fusion_scorecard.py"),
            os.path.join(_CODE_DIR, "fusion_pipeline.py"),
            os.path.join(_CODE_DIR, "fusion_guard.py"),
        ]
        for fpath in fusion_files:
            assert check_no_broker_imports(fpath), f"Broker import in {fpath}"

    def test_baseline_intact(self, baseline_snapshot):
        """Baseline files not modified."""
        errors = assert_baseline_intact(baseline_snapshot)
        assert errors == [], f"Baseline modified: {errors}"


# ─── Test: pipeline integration ──────────────────────────────────────

class TestPipelineIntegration:
    """Tests for full fusion pipeline."""

    def test_run_fusion_pipeline(self, signals_dict, returns_series, fusion_data):
        """Full pipeline: fuse → score → select."""
        rules = [FusionRule(**r) for r in fusion_data["rules"]]
        config = fusion_data["config"]

        result = run_fusion_pipeline(
            signals=signals_dict,
            returns=returns_series,
            rules=rules,
            config=config,
        )

        assert "fused_signals" in result
        assert "scorecards" in result
        assert "selected" in result
        assert "meta" in result

        # Generated at least some fusions
        assert len(result["fused_signals"]) >= 1
        # Selected ≤ max_slots
        assert len(result["selected"]) <= config["risk"]["max_slots"]

    def test_validate_constraints(self):
        """validate_fusion_constraints catches violations."""
        bad = [
            {"contracts": 2, "ticker": "BR", "fused_strategies": ["a", "b"], "veto": False},
        ]
        errors = validate_fusion_constraints(bad)
        assert any("contracts=2" in e for e in errors)

        bad_excluded = [
            {"contracts": 1, "ticker": "RI", "fused_strategies": ["a", "b"], "veto": False},
        ]
        errors = validate_fusion_constraints(bad_excluded)
        assert any("excluded ticker" in e for e in errors)

    def test_max_slots_enforced(self, signals_dict, returns_series):
        """Pipeline respects max_slots=2."""
        rules = [
            FusionRule(strategies=["sma_cross", "rsi_reversal"], method="majority_vote"),
            FusionRule(strategies=["sma_cross", "macd_trend"], method="unanimous"),
            FusionRule(strategies=["rsi_reversal", "donchian_breakout"], method="weighted_average"),
        ]
        config = {"risk": {"max_slots": 2}, "excluded": ["RI"]}
        result = run_fusion_pipeline(
            signals=signals_dict,
            returns=returns_series,
            rules=rules,
            config=config,
        )
        assert len(result["selected"]) <= 2
