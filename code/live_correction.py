"""Live Correction Layer — secondary validation of backtest expectations.

Compares backtest-expected metrics (composite_score, win_rate, expectancy)
with actual/live trade results. Computes correction_delta and recommends
calibration actions for allocator weights.

Pure dict-in → dict-out, 0 broker imports, stdlib-only.

Usage:
    from live_correction import compute_corrections
    result = compute_corrections(backtest_expectations, live_results)
    # result["corrections"] — per-ticker calibration info
    # result["summary"]     — aggregate counts (improved/degraded/stable)
"""
from __future__ import annotations

from typing import Any, Dict

# ── Thresholds ──
# If delta_win_rate is above +threshold → increase allocation
# If delta_win_rate is below -threshold → decrease allocation
# Otherwise → maintain
DELTA_WIN_RATE_THRESHOLD = 0.05   # 5% absolute
DELTA_EXPECTANCY_THRESHOLD = 0.3  # in expectancy units
DELTA_PNL_SIGNIFICANCE = 100.0    # rub


def compute_corrections(
    backtest_expectations: Dict[str, Dict[str, Any]],
    live_results: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """Compare backtest expectations vs live results, produce corrections.

    Args:
        backtest_expectations: per-ticker backtest metrics:
            {ticker: {
                "composite_score": float,
                "win_rate": float,        # 0..1
                "expectancy": float,      # expected payoff per unit risk
                "sharpe": float (optional)
            }}
        live_results: per-ticker actual trade results:
            {ticker: {
                "actual_pnl": float,      # realized PnL in rub
                "actual_win_rate": float, # 0..1
                "actual_expectancy": float,
                "n_trades": int
            }}

    Returns:
        {
            "corrections": {
                ticker: {
                    "delta_pnl": float,          # actual vs expected trend (sign only)
                    "delta_win_rate": float,      # actual - expected
                    "delta_expectancy": float,    # actual - expected
                    "calibration_action": str,    # "increase" | "decrease" | "maintain"
                    "confidence": str,            # "high" | "medium" | "low"
                    "reason": str,
                }
            },
            "summary": {
                "n_improved": int,
                "n_degraded": int,
                "n_stable": int,
                "n_no_data": int,
            }
        }
    """
    corrections: Dict[str, Dict[str, Any]] = {}
    n_improved = 0
    n_degraded = 0
    n_stable = 0
    n_no_data = 0

    all_tickers = set(backtest_expectations.keys()) | set(live_results.keys())

    for ticker in sorted(all_tickers):
        bt = backtest_expectations.get(ticker)
        lr = live_results.get(ticker)

        if bt is None or lr is None:
            n_no_data += 1
            corrections[ticker] = {
                "delta_pnl": 0.0,
                "delta_win_rate": 0.0,
                "delta_expectancy": 0.0,
                "calibration_action": "maintain",
                "confidence": "low",
                "reason": "missing_data",
            }
            continue

        bt_win_rate = bt.get("win_rate", 0.0)
        bt_expectancy = bt.get("expectancy", 0.0)
        bt_composite = bt.get("composite_score", 0.0)

        actual_win_rate = lr.get("actual_win_rate", 0.0)
        actual_expectancy = lr.get("actual_expectancy", 0.0)
        actual_pnl = lr.get("actual_pnl", 0.0)
        n_trades = lr.get("n_trades", 0)

        delta_wr = actual_win_rate - bt_win_rate
        delta_exp = actual_expectancy - bt_expectancy

        # Determine confidence based on trade count
        if n_trades >= 20:
            confidence = "high"
        elif n_trades >= 10:
            confidence = "medium"
        else:
            confidence = "low"

        # Calibration action based on delta magnitudes
        score = 0.0
        reasons = []

        if delta_wr > DELTA_WIN_RATE_THRESHOLD:
            score += 1.0
            reasons.append(f"win_rate_above_expected(+{delta_wr:.3f})")
        elif delta_wr < -DELTA_WIN_RATE_THRESHOLD:
            score -= 1.0
            reasons.append(f"win_rate_below_expected({delta_wr:.3f})")

        if delta_exp > DELTA_EXPECTANCY_THRESHOLD:
            score += 1.0
            reasons.append(f"expectancy_above_expected(+{delta_exp:.2f})")
        elif delta_exp < -DELTA_EXPECTANCY_THRESHOLD:
            score -= 1.0
            reasons.append(f"expectancy_below_expected({delta_exp:.2f})")

        if actual_pnl > DELTA_PNL_SIGNIFICANCE:
            score += 0.5
            reasons.append(f"positive_pnl({actual_pnl:.0f}rub)")
        elif actual_pnl < -DELTA_PNL_SIGNIFICANCE:
            score -= 0.5
            reasons.append(f"negative_pnl({actual_pnl:.0f}rub)")

        if score >= 1.0:
            action = "increase"
            n_improved += 1
        elif score <= -1.0:
            action = "decrease"
            n_degraded += 1
        else:
            action = "maintain"
            n_stable += 1

        reason_str = "; ".join(reasons) if reasons else "within_expected_range"

        corrections[ticker] = {
            "delta_pnl": actual_pnl,
            "delta_win_rate": delta_wr,
            "delta_expectancy": delta_exp,
            "calibration_action": action,
            "confidence": confidence,
            "reason": reason_str,
        }

    return {
        "corrections": corrections,
        "summary": {
            "n_improved": n_improved,
            "n_degraded": n_degraded,
            "n_stable": n_stable,
            "n_no_data": n_no_data,
        },
    }
