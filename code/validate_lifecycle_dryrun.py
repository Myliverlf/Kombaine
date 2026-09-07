"""Dry-run валидатор полного аспекта: 6 метрик жизненного цикла одной таблицей.

CLI по образцу validate_scorecard_dryrun.py:
  - Read-only читает tests/fixtures/*.json + config.json
  - Печатает таблицу по всем 6 метрикам аспекта:
    hit_rate, expectancy, drawdown, decay, signal_freshness, stability
  - Баннер «DRY_RUN: no real orders»
  - exit 0 при успехе

Ничего не пишет в state/ и code/.
"""
import json
import os
import sys
import time
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────
COMBINE_DIR = Path(__file__).resolve().parent.parent
FIXTURES_DIR = COMBINE_DIR / "tests" / "fixtures"
SAMPLE_RETURNS = FIXTURES_DIR / "sample_returns.json"
PORTFOLIO = FIXTURES_DIR / "portfolio_copy.json"
REGIME = FIXTURES_DIR / "regime_snapshot.json"
CONFIG_PATH = COMBINE_DIR / "config.json"

# ── Import lifecycle metrics ───────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).resolve().parent))
from lifecycle_metrics import decay, expectancy, hit_rate, stability
from scorecard_metrics import max_drawdown


def _load_json(path: Path) -> dict:
    """Load JSON file, return empty dict on error."""
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _calc_signal_freshness(portfolio: dict, config: dict, now_ts: float) -> dict:
    """Calculate signal freshness metrics from portfolio data.

    Returns dict with fresh_count, stale_count, worst_age_min, max_age_min.
    """
    signal_max_age_min = config.get("risk", {}).get(
        "signal_max_age_minutes",
        config.get("signal_max_age_minutes", 16),
    )
    max_age_sec = signal_max_age_min * 60.0

    slots = portfolio.get("slots", {})
    fresh_count = 0
    stale_count = 0
    worst_age_min = 0.0

    for _name, slot in slots.items():
        # Estimate age from entry_ts or last known signal
        entry_ts = slot.get("entry_ts", now_ts)
        age_sec = now_ts - entry_ts if entry_ts else 0.0
        age_min = age_sec / 60.0
        if age_min > worst_age_min:
            worst_age_min = age_min
        if age_sec > max_age_sec:
            stale_count += 1
        else:
            fresh_count += 1

    return {
        "fresh_count": fresh_count,
        "stale_count": stale_count,
        "worst_age_min": round(worst_age_min, 1),
        "max_age_min": signal_max_age_min,
    }


def main() -> int:
    """Run dry-run validation and print 6-metric table."""
    print("=" * 60)
    print("  DRY_RUN: no real orders")
    print("  Lifecycle Metrics — Full Aspect Validator")
    print("=" * 60)
    print()

    # Load data
    sample = _load_json(SAMPLE_RETURNS)
    portfolio = _load_json(PORTFOLIO)
    config = _load_json(CONFIG_PATH)
    now_ts = time.time()

    # Get returns from fixtures
    baseline = sample.get("baseline", [])
    better = sample.get("candidate_better", [])
    worse = sample.get("candidate_worse", [])

    # ── Table header ───────────────────────────────────────────────────
    header = "%-22s | %-12s | %-12s | %-12s" % (
        "Metric", "Baseline", "Better", "Worse"
    )
    print(header)
    print("-" * len(header))

    # ── 1. Hit Rate ────────────────────────────────────────────────────
    hr_b = hit_rate(baseline)
    hr_be = hit_rate(better)
    hr_bw = hit_rate(worse)
    print("%-22s | %12.4f | %12.4f | %12.4f" % ("hit_rate", hr_b, hr_be, hr_bw))

    # ── 2. Expectancy (R) ─────────────────────────────────────────────
    exp_b = expectancy(baseline)
    exp_be = expectancy(better)
    exp_bw = expectancy(worse)
    print("%-22s | %12.4f | %12.4f | %12.4f" % ("expectancy_r", exp_b, exp_be, exp_bw))

    # ── 3. Max Drawdown ────────────────────────────────────────────────
    dd_b = max_drawdown(baseline)
    dd_be = max_drawdown(better)
    dd_bw = max_drawdown(worse)
    print("%-22s | %12.4f | %12.4f | %12.4f" % ("max_drawdown", dd_b, dd_be, dd_bw))

    # ── 4. Decay (proxy) ───────────────────────────────────────────────
    dec_b = decay(baseline)
    dec_be = decay(better)
    dec_bw = decay(worse)
    print("%-22s | %12.4f | %12.4f | %12.4f" % ("decay", dec_b, dec_be, dec_bw))

    # ── 5. Signal Freshness ────────────────────────────────────────────
    freshness = _calc_signal_freshness(portfolio, config, now_ts)
    print()
    print("Signal Freshness:")
    print("  fresh slots: %d, stale: %d, worst: %.1f min (limit: %d min)" % (
        freshness["fresh_count"],
        freshness["stale_count"],
        freshness["worst_age_min"],
        freshness["max_age_min"],
    ))

    # ── 6. Stability ───────────────────────────────────────────────────
    stab_b = stability(baseline)
    stab_be = stability(better)
    stab_bw = stability(worse)
    print()
    header2 = "%-22s | %-12s | %-12s | %-12s" % (
        "Stability", "Baseline", "Better", "Worse"
    )
    print(header2)
    print("-" * len(header2))
    print("%-22s | %12.4f | %12.4f | %12.4f" % ("stability", stab_b, stab_be, stab_bw))

    # ── Summary ────────────────────────────────────────────────────────
    print()
    print("=" * 60)
    print("  DRY_RUN COMPLETE — exit 0")
    print("  No state/ or code/ files were modified.")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
