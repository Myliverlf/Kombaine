#!/usr/bin/env python3
"""Unified manual launcher for Strategy Architect smoke runs.

Rules:
- Prefer .venv-timesfm/bin/python when available.
- Never hides missing TimesFM venv: reports whether real TimesFM or dummy is used.
- Passes only local paper/backtest-safe arguments to strategy_architect_autopilot.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import List, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PYTHON = PROJECT_ROOT / ".venv-timesfm" / "bin" / "python"
AUTOPILOT = PROJECT_ROOT / "code" / "strategy_architect_autopilot.py"


def pick_interpreter() -> tuple[Path, bool]:
    if DEFAULT_PYTHON.is_file() and os.access(DEFAULT_PYTHON, os.X_OK):
        return DEFAULT_PYTHON, True
    return Path(sys.executable), False


def probe_timesfm(interpreter: Path) -> bool:
    code = "import importlib.util; raise SystemExit(0 if importlib.util.find_spec('timesfm') else 1)"
    return subprocess.run([str(interpreter), "-c", code], cwd=str(PROJECT_ROOT), check=False).returncode == 0


def build_autopilot_args(args: argparse.Namespace) -> List[str]:
    cli = [str(AUTOPILOT)]
    cli.extend(["--timeframes", args.timeframes])
    cli.extend(["--strategies", args.strategies])
    cli.extend(["--max-params", str(args.max_params)])
    cli.extend(["--min-bars", str(args.min_bars)])
    cli.extend(["--min-trades", str(args.min_trades)])
    cli.extend(["--min-pf", str(args.min_pf)])
    cli.extend(["--top-n", str(args.top_n)])
    cli.extend(["--max-per-ticker", str(args.max_per_ticker)])
    cli.extend(["--max-per-strategy", str(args.max_per_strategy)])
    cli.extend(["--min-tickers", str(args.min_tickers)])
    cli.extend(["--initial-cash", str(args.initial_cash)])
    if args.no_write_registry:
        cli.append("--no-write-registry")
    else:
        cli.append("--no-write-registry")
    return cli


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Manual Strategy Architect launcher via .venv-timesfm")
    ap.add_argument("--timeframes", default="15m,1h")
    ap.add_argument("--strategies", default="policy")
    ap.add_argument("--max-params", type=int, default=4)
    ap.add_argument("--min-bars", type=int, default=300)
    ap.add_argument("--min-trades", type=int, default=2)
    ap.add_argument("--min-pf", type=float, default=1.05)
    ap.add_argument("--top-n", type=int, default=20)
    ap.add_argument("--max-per-ticker", type=int, default=3)
    ap.add_argument("--max-per-strategy", type=int, default=3)
    ap.add_argument("--min-tickers", type=int, default=5)
    ap.add_argument("--initial-cash", type=float, default=1_000_000.0)
    ap.add_argument("--no-write-registry", action="store_true", default=True, help="keep the run dry-run and skip registry writes")
    ap.add_argument("--python", default=None, help="override interpreter path; default prefers .venv-timesfm/bin/python")
    return ap


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    interpreter, venv_found = pick_interpreter() if args.python is None else (Path(args.python), Path(args.python).is_file())
    if args.python is not None and not interpreter.exists():
        raise FileNotFoundError(f"Python interpreter not found: {interpreter}")

    autopilot_args = build_autopilot_args(args)
    env = os.environ.copy()
    env.setdefault("OMP_NUM_THREADS", "1")
    env.setdefault("MKL_NUM_THREADS", "1")
    env.setdefault("TOKENIZERS_PARALLELISM", "false")

    has_timesfm = probe_timesfm(interpreter)
    print(f"manual_strategy_architect_launcher: interpreter={interpreter}")
    print(f"manual_strategy_architect_launcher: venv_timesfm={'present' if venv_found else 'fallback'}")
    print(f"manual_strategy_architect_launcher: TimesFM source={'real TimesFM' if has_timesfm else 'dummy'}")
    print(f"manual_strategy_architect_launcher: autopilot={' '.join(autopilot_args)}")
    completed = subprocess.run([str(interpreter), *autopilot_args], cwd=str(PROJECT_ROOT), env=env, check=False)
    return int(completed.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
