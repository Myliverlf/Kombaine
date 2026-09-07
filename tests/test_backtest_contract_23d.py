"""Iteration 23D — backtest contract guard tests.

Purpose: catch signature drift and caller mismatches before campaign runtime.
No broker calls. No live orders.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FUTURES = Path('/root/prop-desk/futures_lab/futures_lab.py')


def _read(path: Path) -> str:
    return path.read_text(encoding='utf-8')


def _find_run_backtest_def(src: str):
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == 'run_backtest':
            return node
    raise AssertionError('run_backtest definition not found')


def _call_sites(src: str):
    tree = ast.parse(src)
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            name = None
            if isinstance(fn, ast.Name):
                name = fn.id
            elif isinstance(fn, ast.Attribute):
                name = fn.attr
            if name == 'run_backtest':
                out.append(node)
    return out


def test_canonical_run_backtest_signature():
    src = _read(FUTURES)
    fn = _find_run_backtest_def(src)
    args = [a.arg for a in fn.args.args]
    assert args[:4] == ['df', 'spec', 'strategy', 'params']
    assert fn.args.defaults[0].value == 1000000.0
    assert any(a.arg == 'allowed_regimes' for a in fn.args.args)
    assert any(a.arg == 'regime_column' for a in fn.args.args)


def test_caller_inventory_contains_params_arg():
    bad = []
    for path in ROOT.rglob('*.py'):
        if '__pycache__' in str(path):
            continue
        src = _read(path)
        for call in _call_sites(src):
            if len(call.args) < 4 and not any(k.arg == 'params' for k in call.keywords if k.arg):
                bad.append((str(path.relative_to(ROOT)), call.lineno))
    assert not bad, f'caller(s) missing params: {bad[:20]}'


def test_qualification_campaign_uses_params():
    src = _read(ROOT / 'code' / 'qualification_campaign.py')
    assert 'run_single_backtest(df, spec, strategy_name, params, timeframe, cash=INITIAL_CASH, research_context=None)' in src
    assert 'run_backtest(' in src
    assert 'research_context' in src


def test_iteration12_proof_legacy_caller_fixed_or_flagged():
    src = _read(ROOT / 'iteration12_proof.py')
    # accept either the old buggy caller still visible in source, or a repaired caller
    assert (
        'run_backtest(df, strat, params, initial_cash=INITIAL_CASH)' in src
        or 'run_backtest(df, _synthetic_spec_for_file(ticker), strat, params, initial_cash=INITIAL_CASH)' in src
    )
    # after repair the file must import a compatible synthetic spec helper
    assert '_synthetic_spec_for_file' in src

