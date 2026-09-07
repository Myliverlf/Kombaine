"""register.py — общий регистратор стратегий-геномов в futures_lab.STRATEGY_FUNCS.

Любой инструмент (select_stable_pool, forward_test, plot_stable_pool) вызывает
register_genomes() перед run_backtest — геномы из state/engine_candidates.json
восстанавливаются как функции под именами ge_<hash10>.
"""
import json
from pathlib import Path

SC = Path("/root/prop-desk/strategy_combine")
OUT = SC / "state" / "engine_candidates.json"


def register_genomes(names=None):
    import futures_lab
    from engines.genome import genome_signal
    if not OUT.exists():
        return 0
    try:
        cands = json.loads(OUT.read_text()).get("candidates", [])
    except Exception:
        return 0
    n = 0
    for c in cands:
        if names is not None and c["name"] not in names:
            continue
        if c.get("engine") == "neural":
            # E4: нейросеть — сигнал из СОХРАНЁННЫХ весов, без дообучения на лету
            from engines.neural_engine import neural_signal
            futures_lab.STRATEGY_FUNCS[c["name"]] = (lambda nm: lambda df, **kw: neural_signal(nm, df))(c["name"])
            n += 1
        elif c.get("engine") == "genetic" and c.get("genome"):
            g = c["genome"]
            futures_lab.STRATEGY_FUNCS[c["name"]] = (lambda gg: lambda df, **kw: genome_signal(df, gg))(g)
            n += 1
    return n


def genome_for(name):
    try:
        for c in json.loads(OUT.read_text()).get("candidates", []):
            if c.get("name") == name:
                return c.get("genome")
    except Exception:
        pass
    return None
