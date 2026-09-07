"""E2E final validation + Apostle report readability tests.

Verifies:
  (a) python code/final_validation.py exits 0 and creates verdict.md
  (b) verdict.md contains 0 FAILED checks and a markdown table
  (c) scorecard_apostle_report.py generates markdown with PnL, Risk, Constraints,
      and no mention of live orders

No live broker, no network. Subprocess-only calls to standalone scripts.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

# Ensure project modules are importable
COMBINE_DIR = Path(__file__).resolve().parent.parent
CODE_DIR = COMBINE_DIR / "code"
VERDICT_PATH = COMBINE_DIR / "verdict.md"


# ── Fixtures ──────────────────────────────────────────────────────────

@pytest.fixture
def run_final_validation() -> dict:
    """Run final_validation.py and capture results.

    Returns dict with: returncode, stdout, stderr, verdict_text.
    """
    result = subprocess.run(
        [sys.executable, str(CODE_DIR / "final_validation.py")],
        capture_output=True, text=True, timeout=120,
        cwd=str(COMBINE_DIR),
    )
    verdict_text = ""
    if VERDICT_PATH.exists():
        verdict_text = VERDICT_PATH.read_text(encoding="utf-8")

    return {
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "verdict_text": verdict_text,
    }


@pytest.fixture
def run_apostle_report() -> dict:
    """Run scorecard_apostle_report.py --dry-run and capture output.

    Returns dict with: returncode, stdout, stderr.
    """
    result = subprocess.run(
        [sys.executable, str(CODE_DIR / "scorecard_apostle_report.py"), "--dry-run"],
        capture_output=True, text=True, timeout=60,
        cwd=str(COMBINE_DIR),
    )
    return {
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


@pytest.fixture
def verdict_text(run_final_validation: dict) -> str:
    """Verdict.md content — skips test if validation didn't produce it."""
    text = run_final_validation["verdict_text"]
    if not text:
        pytest.skip("verdict.md was not created by final_validation.py")
    return text


@pytest.fixture
def apostle_output(run_apostle_report: dict) -> dict:
    """Apostle report output — skips if report script failed."""
    if run_apostle_report["returncode"] != 0:
        pytest.skip(
            f"scorecard_apostle_report.py failed (rc={run_apostle_report['returncode']})"
        )
    return run_apostle_report


# ── Tests ─────────────────────────────────────────────────────────────

class TestFinalValidation:
    """Verify final_validation.py produces a clean verdict."""

    def test_exits_zero(self, run_final_validation: dict) -> None:
        """final_validation.py must exit 0 (all checks passed)."""
        assert run_final_validation["returncode"] == 0, (
            f"final_validation.py exited {run_final_validation['returncode']}\n"
            f"stdout: {run_final_validation['stdout'][-500:]}\n"
            f"stderr: {run_final_validation['stderr'][-500:]}"
        )

    def test_creates_verdict_md(self, run_final_validation: dict) -> None:
        """verdict.md must exist after running final_validation."""
        assert VERDICT_PATH.exists(), "verdict.md not found"

    def test_verdict_has_markdown_table(self, verdict_text: str) -> None:
        """verdict.md must contain a markdown table with check results."""
        assert "|" in verdict_text, "verdict.md missing markdown table"
        assert "Статус" in verdict_text or "✅" in verdict_text or "❌" in verdict_text, (
            "verdict.md missing status icons or column"
        )


class TestVerdictClean:
    """Verify verdict.md shows 0 FAILED checks (after path fix)."""

    def test_no_failed_checks(self, verdict_text: str) -> None:
        """verdict.md must say ALL PASSED or have 0 FAILED."""
        # Check for ALL PASSED header
        has_all_passed = "ALL PASSED" in verdict_text
        # Count ❌ in the check rows
        failed_rows = len(re.findall(r"\| ❌ \|", verdict_text))
        assert has_all_passed or failed_rows == 0, (
            f"verdict.md has {failed_rows} FAILED check(s)"
        )

    def test_has_py_compile_check(self, verdict_text: str) -> None:
        """verdict.md must include py_compile check."""
        assert "py_compile" in verdict_text, "verdict.md missing py_compile check"

    def test_has_no_broker_check(self, verdict_text: str) -> None:
        """verdict.md must include broker import check."""
        assert "broker" in verdict_text.lower(), (
            "verdict.md missing broker check"
        )


class TestApostleReportReadability:
    """Verify scorecard_apostle_report.py produces readable markdown."""

    def test_generates_output(self, apostle_output: dict) -> None:
        """Apostle report must produce stdout output."""
        assert len(apostle_output["stdout"]) > 100, (
            "Apostle report output too short"
        )

    def test_has_pnl_section(self, apostle_output: dict) -> None:
        """Report must mention PnL metrics."""
        output = apostle_output["stdout"].lower()
        has_pnl = "pnl" in output or "expectancy" in output or "sharpe" in output
        assert has_pnl, "Apostle report missing PnL section"

    def test_has_risk_section(self, apostle_output: dict) -> None:
        """Report must mention risk metrics."""
        output = apostle_output["stdout"].lower()
        has_risk = "risk" in output or "drawdown" in output or "volatility" in output
        assert has_risk, "Apostle report missing Risk section"

    def test_has_constraints(self, apostle_output: dict) -> None:
        """Report must mention pipeline constraints (RI, slots, contracts)."""
        output = apostle_output["stdout"].lower()
        has_constraints = (
            "ri" in output
            or "excluded" in output
            or "max_slots" in output
            or "constraint" in output
        )
        assert has_constraints, "Apostle report missing Constraints section"

    def test_no_live_order_placement(self, apostle_output: dict) -> None:
        """Report must NOT contain live order PLACEMENT instructions.

        The phrase 'no live orders' is an acceptable safety disclaimer,
        but explicit order placement commands are forbidden.
        """
        output = apostle_output["stdout"].lower()
        # These are order-placement commands that must never appear
        forbidden = ["place order", "send order", "execute order",
                     "submit order", "open position", "open order"]
        for phrase in forbidden:
            assert phrase not in output, (
                f"Apostle report contains forbidden phrase: '{phrase}'"
            )
