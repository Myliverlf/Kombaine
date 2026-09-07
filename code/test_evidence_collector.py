"""Unit tests for core.evidence_collector — subprocess-backed evidence gathering."""
from __future__ import annotations

import sys
import pytest
from core.evidence_collector import (
    COLLECTOR_VERSION,
    ModuleEvidence,
    collect_batch,
    collect_module_evidence,
    summarize_verdicts,
)


# ---------------------------------------------------------------------------
# collect_module_evidence — basic command execution
# ---------------------------------------------------------------------------
class TestCollectModuleEvidence:
    def test_passing_command(self):
        ev = collect_module_evidence(
            "test_module",
            [sys.executable, "-c", "print('hello')"],
        )
        assert ev.verdict == "PASS"
        assert ev.returncode == 0
        assert "hello" in ev.stdout
        assert ev.module_path == "test_module"
        assert ev.check_command == [sys.executable, "-c", "print('hello')"]
        assert ev.timestamp  # non-empty
        assert ev.duration_seconds >= 0
        assert ev.collector_version == COLLECTOR_VERSION

    def test_failing_command(self):
        ev = collect_module_evidence(
            "fail_module",
            [sys.executable, "-c", "import sys; sys.exit(1)"],
        )
        assert ev.verdict == "FAIL"
        assert ev.returncode == 1

    def test_nonexistent_command_returns_error(self):
        ev = collect_module_evidence(
            "no_cmd",
            ["__nonexistent_command_xyz__"],
        )
        assert ev.verdict == "ERROR"
        assert ev.returncode == -2
        assert "not found" in ev.stderr.lower() or "Command not found" in ev.stderr

    def test_timeout_returns_timeout(self):
        ev = collect_module_evidence(
            "slow_module",
            [sys.executable, "-c", "import time; time.sleep(10)"],
            timeout_seconds=1,
        )
        assert ev.verdict == "TIMEOUT"
        assert ev.returncode == -1
        assert ev.duration_seconds < 5  # should not run 10s

    def test_to_dict(self):
        ev = collect_module_evidence(
            "m",
            [sys.executable, "-c", "pass"],
        )
        d = ev.to_dict()
        assert isinstance(d, dict)
        assert d["verdict"] == "PASS"
        assert d["module_path"] == "m"

    def test_stderr_captured(self):
        ev = collect_module_evidence(
            "stderr_module",
            [sys.executable, "-c", "import sys; sys.stderr.write('oops'); sys.exit(1)"],
        )
        assert ev.verdict == "FAIL"
        assert "oops" in ev.stderr

    def test_zero_exit_with_stderr_is_pass(self):
        """exit code 0 even with stderr → PASS (verdict based on returncode only)."""
        ev = collect_module_evidence(
            "stderr_pass",
            [sys.executable, "-c", "import sys; sys.stderr.write('warn'); sys.exit(0)"],
        )
        assert ev.verdict == "PASS"
        assert "warn" in ev.stderr


# ---------------------------------------------------------------------------
# collect_module_evidence — working_dir
# ---------------------------------------------------------------------------
class TestCollectModuleEvidenceWorkingDir:
    def test_working_dir(self):
        ev = collect_module_evidence(
            "wd_test",
            [sys.executable, "-c", "import os; print(os.getcwd())"],
            working_dir="/tmp",
        )
        assert ev.verdict == "PASS"
        assert "/tmp" in ev.stdout


# ---------------------------------------------------------------------------
# collect_batch
# ---------------------------------------------------------------------------
class TestCollectBatch:
    def test_batch_multiple_commands(self):
        checks = [
            {"module_path": "m1", "check_command": [sys.executable, "-c", "print(1)"]},
            {"module_path": "m2", "check_command": [sys.executable, "-c", "import sys; sys.exit(1)"]},
            {"module_path": "m3", "check_command": ["__no_such_cmd__"]},
        ]
        results = collect_batch(checks)
        assert len(results) == 3
        assert results[0].verdict == "PASS"
        assert results[1].verdict == "FAIL"
        assert results[2].verdict == "ERROR"

    def test_batch_empty_list(self):
        results = collect_batch([])
        assert results == []

    def test_batch_per_item_timeout(self):
        checks = [
            {"module_path": "fast", "check_command": [sys.executable, "-c", "print('ok')"], "timeout_seconds": 5},
        ]
        results = collect_batch(checks, timeout_seconds=1)
        assert results[0].verdict == "PASS"

    def test_batch_default_module_path(self):
        """Missing module_path defaults to 'unknown'."""
        checks = [{"check_command": [sys.executable, "-c", "print('x')"]}]
        results = collect_batch(checks)
        assert results[0].module_path == "unknown"

    def test_batch_default_empty_command_raises(self):
        """Missing check_command defaults to empty list → IndexError from subprocess.
        This is a known limitation: collect_batch does not guard against empty commands.
        """
        checks = [{"module_path": "no_cmd"}]
        with pytest.raises(IndexError):
            collect_batch(checks)


# ---------------------------------------------------------------------------
# summarize_verdicts
# ---------------------------------------------------------------------------
class TestSummarizeVerdicts:
    def test_all_pass(self):
        evs = [
            ModuleEvidence("m1", ["cmd"], "", "", 0, "PASS", "", 0.1, 30),
            ModuleEvidence("m2", ["cmd"], "", "", 0, "PASS", "", 0.1, 30),
        ]
        s = summarize_verdicts(evs)
        assert s["total"] == 2
        assert s["pass_count"] == 2
        assert s["fail_count"] == 0
        assert s["overall_verdict"] == "PASS"

    def test_one_fail_overall_fail(self):
        evs = [
            ModuleEvidence("m1", ["cmd"], "", "", 0, "PASS", "", 0.1, 30),
            ModuleEvidence("m2", ["cmd"], "", "", 1, "FAIL", "", 0.1, 30),
        ]
        s = summarize_verdicts(evs)
        assert s["overall_verdict"] == "FAIL"

    def test_one_error_overall_error(self):
        evs = [
            ModuleEvidence("m1", ["cmd"], "", "", 0, "PASS", "", 0.1, 30),
            ModuleEvidence("m2", ["cmd"], "", "", -2, "ERROR", "", 0.1, 30),
        ]
        s = summarize_verdicts(evs)
        assert s["overall_verdict"] == "ERROR"

    def test_one_timeout_overall_error(self):
        evs = [
            ModuleEvidence("m1", ["cmd"], "", "", -1, "TIMEOUT", "", 1.0, 1),
        ]
        s = summarize_verdicts(evs)
        assert s["overall_verdict"] == "ERROR"
        assert s["timeout_count"] == 1

    def test_empty_list(self):
        s = summarize_verdicts([])
        assert s["total"] == 0
        assert s["overall_verdict"] == "NO_DATA"

    def test_mixed_all_types(self):
        evs = [
            ModuleEvidence("m1", [], "", "", 0, "PASS", "", 0, 30),
            ModuleEvidence("m2", [], "", "", 1, "FAIL", "", 0, 30),
            ModuleEvidence("m3", [], "", "", -2, "ERROR", "", 0, 30),
            ModuleEvidence("m4", [], "", "", -1, "TIMEOUT", "", 0, 1),
        ]
        s = summarize_verdicts(evs)
        assert s["total"] == 4
        assert s["pass_count"] == 1
        assert s["fail_count"] == 1
        assert s["error_count"] == 1
        assert s["timeout_count"] == 1
        assert s["overall_verdict"] == "ERROR"  # error takes precedence
