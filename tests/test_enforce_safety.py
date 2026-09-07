"""Enforce safety tests: backup→enforce→restore integration + live position protection.

Features covered (from plan.md):
  F3: Backup→enforce→restore integration + live position protection

Fixtures (3):
  - portfolio_with_live: 2 open positions + 5 noise slots
  - empty_tmp_state: tmp_path with state/ dir and sample files
  - safe_config: paper mode config

Tests:
  - test_backup_before_enforce: snapshot_state() creates .bak files
  - test_enforce_never_removes_open_position: after enforce all open_position slots survive
  - test_restore_from_backup_roundtrip: backup→modify→restore → identical to original

All tests use tmp_path. Real state/ is never modified.
No live broker/orders — only JSON read/write via state_backup.py.

Источники:
  - plan.md Фича 3
  - code/state_backup.py
  - code/portfolio_enforcer.py
  - code/live_order_guard.py
"""
import json
import sys
from pathlib import Path

import pytest

# Ensure project modules are importable
COMBINE_DIR = Path(__file__).resolve().parent.parent
CODE_DIR = COMBINE_DIR / "code"
CORE_DIR = COMBINE_DIR / "core"
sys.path.insert(0, str(CODE_DIR))
sys.path.insert(0, str(CORE_DIR))

from state_backup import (  # noqa: E402
    snapshot_state,
    atomic_write_json,
    restore_from_backup,
    list_backups,
)
from portfolio_enforcer import enforce_portfolio  # noqa: E402
from live_order_guard import assert_paper_mode  # noqa: E402


# ── Fixtures ───────────────────────────────────────────────────────


@pytest.fixture
def portfolio_with_live() -> dict:
    """8-slot portfolio: 2 open positions + 5 noise slots.

    The 2 open-position slots MUST survive enforce (live protection).
    """
    return {
        "slots": {
            "slot_LKOH_live": {
                "ticker": "LKOH",
                "strategy": "vwap_reversion",
                "contracts": 1,
                "pnl_rub": 538.0,
                "open_position": {
                    "direction": "SHORT",
                    "qty": 1,
                    "entry_price": 42598.0,
                },
                "promoted_ts": 1000.0,
                "n_trades": 10,
            },
            "slot_GAZP_live": {
                "ticker": "GAZP",
                "strategy": "ft_bband_rsi",
                "contracts": 1,
                "pnl_rub": -30.0,
                "open_position": {
                    "direction": "SHORT",
                    "qty": 1,
                    "entry_price": 83.94,
                },
                "promoted_ts": 2000.0,
                "n_trades": 5,
            },
            "slot_GAZP_noise1": {
                "ticker": "GAZP",
                "strategy": "bollinger_reversion",
                "contracts": 1,
                "pnl_rub": 102.0,
                "open_position": None,
                "promoted_ts": 3000.0,
                "n_trades": 4,
            },
            "slot_LKOH_noise1": {
                "ticker": "LKOH",
                "strategy": "nfi_trend",
                "contracts": 1,
                "pnl_rub": 0.0,
                "open_position": None,
                "promoted_ts": 4000.0,
                "n_trades": 0,
            },
            "slot_SBER_noise1": {
                "ticker": "SBER",
                "strategy": "mean_reversion",
                "contracts": 1,
                "pnl_rub": -15.0,
                "open_position": None,
                "promoted_ts": 5000.0,
                "n_trades": 3,
            },
            "slot_SBER_noise2": {
                "ticker": "SBER",
                "strategy": "momentum_breakout",
                "contracts": 1,
                "pnl_rub": 45.0,
                "open_position": None,
                "promoted_ts": 6000.0,
                "n_trades": 6,
            },
            "slot_LKOH_noise2": {
                "ticker": "LKOH",
                "strategy": "nateemma_basket_meanrev",
                "contracts": 1,
                "pnl_rub": 0.0,
                "open_position": None,
                "promoted_ts": 7000.0,
                "n_trades": 0,
            },
        },
        "peak_equity": 42789.0,
        "halted": False,
        "halt_reason": None,
    }


@pytest.fixture
def empty_tmp_state(tmp_path: Path) -> Path:
    """Create a temporary state directory with portfolio.json."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    portfolio = {
        "slots": {
            "slot_A": {
                "ticker": "LKOH",
                "strategy": "vwap_reversion",
                "contracts": 1,
                "pnl_rub": 100.0,
                "open_position": None,
                "promoted_ts": 1000.0,
                "n_trades": 5,
            },
            "slot_B": {
                "ticker": "GAZP",
                "strategy": "ft_bband_rsi",
                "contracts": 1,
                "pnl_rub": 50.0,
                "open_position": None,
                "promoted_ts": 2000.0,
                "n_trades": 3,
            },
        },
        "peak_equity": 20000.0,
        "halted": False,
        "halt_reason": None,
    }
    (state_dir / "portfolio.json").write_text(json.dumps(portfolio, indent=2))
    return state_dir


@pytest.fixture
def safe_config() -> dict:
    """Safe paper-mode config — passes assert_paper_mode."""
    return {"mode": "paper", "paper_first": True}


# ── Tests ──────────────────────────────────────────────────────────


class TestBackupBeforeEnforce:
    """snapshot_state creates .bak files before enforce writes."""

    def test_backup_before_enforce(self, empty_tmp_state: Path) -> None:
        """snapshot_state() creates .bak files for all JSON in state/."""
        result = snapshot_state(empty_tmp_state)
        assert len(result["errors"]) == 0
        assert len(result["created"]) >= 1  # at least portfolio.json.bak

        # Verify .bak files exist on disk
        for bak_path_str in result["created"]:
            assert Path(bak_path_str).exists()
            assert bak_path_str.endswith(".json.bak")

        # Verify backup content is valid JSON
        for bak_path_str in result["created"]:
            bak_content = Path(bak_path_str).read_text()
            parsed = json.loads(bak_content)  # raises on invalid JSON
            assert "slots" in parsed

    def test_backup_creates_listable_backups(self, empty_tmp_state: Path) -> None:
        """After snapshot, list_backups returns correct metadata."""
        snapshot_state(empty_tmp_state)
        backups = list_backups(empty_tmp_state)
        assert len(backups) >= 1
        for b in backups:
            assert b["size_bytes"] > 0
            assert b["original"].endswith(".json")


class TestEnforceNeverRemovesOpenPosition:
    """Enforce MUST NOT remove slots with active open_position.

    _slot_sort_key gives priority to (-has_position, ...).
    With enforce_portfolio writing to tmp_path portfolio, live slots survive.
    """

    def test_enforce_never_removes_open_position(
        self, portfolio_with_live: dict, tmp_path: Path
    ) -> None:
        """After enforce, all slots with open_position are still present."""
        portfolio_file = tmp_path / "portfolio.json"
        portfolio_file.write_text(json.dumps(portfolio_with_live, indent=2))

        before, after, removed = enforce_portfolio(portfolio_file, max_slots=3)

        assert before == 7
        assert after == 3
        assert len(removed) == 4

        # Read result
        result = json.loads(portfolio_file.read_text())
        result_slots = result["slots"]

        # Both live slots MUST survive
        live_ids = {"slot_LKOH_live", "slot_GAZP_live"}
        kept_ids = set(result_slots.keys())
        assert live_ids.issubset(kept_ids), (
            f"Live slots missing after enforce: {live_ids - kept_ids}"
        )

        # Verify they still have open_position
        for lid in live_ids:
            assert result_slots[lid]["open_position"] is not None
            assert result_slots[lid]["open_position"]["qty"] == 1

    def test_enforce_preserves_position_qty(
        self, portfolio_with_live: dict, tmp_path: Path
    ) -> None:
        """Live positions maintain original qty and direction after enforce."""
        portfolio_file = tmp_path / "portfolio.json"
        portfolio_file.write_text(json.dumps(portfolio_with_live, indent=2))

        enforce_portfolio(portfolio_file, max_slots=3)

        result = json.loads(portfolio_file.read_text())
        lkoh_live = result["slots"]["slot_LKOH_live"]
        gazp_live = result["slots"]["slot_GAZP_live"]

        assert lkoh_live["open_position"]["direction"] == "SHORT"
        assert lkoh_live["open_position"]["entry_price"] == 42598.0
        assert gazp_live["open_position"]["direction"] == "SHORT"
        assert gazp_live["open_position"]["entry_price"] == 83.94


class TestRestoreFromBackupRoundtrip:
    """Backup → modify → restore → identical to original."""

    def test_restore_from_backup_roundtrip(
        self, empty_tmp_state: Path
    ) -> None:
        """snapshot → modify → restore yields original content."""
        portfolio_path = empty_tmp_state / "portfolio.json"

        # Snapshot
        snapshot_result = snapshot_state(empty_tmp_state)
        assert len(snapshot_result["errors"]) == 0

        # Read original
        original = json.loads(portfolio_path.read_text())
        original_pnl = original["slots"]["slot_A"]["pnl_rub"]

        # Modify
        modified = json.loads(json.dumps(original))
        modified["slots"]["slot_A"]["pnl_rub"] = 99999.0
        atomic_write_json(portfolio_path, modified)

        # Verify mutation
        mutated = json.loads(portfolio_path.read_text())
        assert mutated["slots"]["slot_A"]["pnl_rub"] == 99999.0

        # Restore
        restore_result = restore_from_backup(portfolio_path)
        assert restore_result["restored"] is True
        assert restore_result["error"] is None

        # Verify restored matches original
        restored = json.loads(portfolio_path.read_text())
        assert restored["slots"]["slot_A"]["pnl_rub"] == original_pnl
        assert json.dumps(restored, sort_keys=True) == json.dumps(original, sort_keys=True)

    def test_full_enforce_safety_roundtrip(
        self, portfolio_with_live: dict, tmp_path: Path
    ) -> None:
        """Full roundtrip: snapshot → enforce → verify → restore.

        Demonstrates that enforce can be undone via backup.
        """
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        portfolio_path = state_dir / "portfolio.json"
        portfolio_path.write_text(json.dumps(portfolio_with_live, indent=2))

        # Step 1: Backup
        snapshot_result = snapshot_state(state_dir)
        assert len(snapshot_result["errors"]) == 0

        # Step 2: Enforce
        before, after, removed = enforce_portfolio(portfolio_path, max_slots=3)
        assert before == 7
        assert after == 3

        # Step 3: Verify enforce result
        enforced = json.loads(portfolio_path.read_text())
        assert len(enforced["slots"]) == 3

        # Step 4: Restore
        restore_result = restore_from_backup(portfolio_path)
        assert restore_result["restored"] is True

        # Step 5: Verify restored matches original
        restored = json.loads(portfolio_path.read_text())
        assert len(restored["slots"]) == 7  # original count
        assert json.dumps(restored, sort_keys=True) == json.dumps(
            portfolio_with_live, sort_keys=True
        )

    def test_backup_isolation_real_state_untouched(self, tmp_path: Path) -> None:
        """All operations happen in tmp_path — real state/ is never written."""
        real_state = COMBINE_DIR / "state" / "portfolio.json"
        if real_state.exists():
            real_checksum_before = _file_sha256(real_state)
        else:
            real_checksum_before = None

        # Run entire backup→enforce→restore in tmp_path
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        portfolio = {
            "slots": {
                "s1": {"ticker": "X", "strategy": "Y", "contracts": 1,
                        "pnl_rub": 10.0, "open_position": None,
                        "promoted_ts": 1.0, "n_trades": 1},
                "s2": {"ticker": "A", "strategy": "B", "contracts": 1,
                        "pnl_rub": 20.0, "open_position": None,
                        "promoted_ts": 2.0, "n_trades": 2},
            }
        }
        portfolio_path = state_dir / "portfolio.json"
        portfolio_path.write_text(json.dumps(portfolio, indent=2))

        snapshot_state(state_dir)
        enforce_portfolio(portfolio_path, max_slots=1)
        restore_from_backup(portfolio_path)

        # Verify real state is untouched
        if real_state.exists() and real_checksum_before is not None:
            real_checksum_after = _file_sha256(real_state)
            assert real_checksum_before == real_checksum_after, (
                "Real state/portfolio.json was modified by test!"
            )


# ── Helper ─────────────────────────────────────────────────────────


def _file_sha256(path: Path) -> str:
    """Compute SHA-256 of a file for integrity checks."""
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()
