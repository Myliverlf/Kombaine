#!/usr/bin/env python3
"""genetic_engine.py — движок E2 v3: эволюционный поиск стратегий.

Что нового в v3 (2026-09-05, вторая итерация Апостола):
  ЭВОЛЮЦИЯ ВЫХОДОВ: стоп-лосс/тейк-профит — тоже ДНК. Геном несёт "exits":
  stop_mode: atr | trail | supertrend; take_mode: atr | bb | rsi | none;
  брейк-ивен, EMA-выход, параметры индикаторов — всё эволюционируется вместе
  с сигналами и рисковыми генами. Ядро: engines/exit_engine.py (регресс-тест
  tools/test_exit_engine.py: пустые exits = бит-в-бит futures_lab.run_backtest).

Что было в v2:
  1. РИСКОВЫЕ ГЕНЫ: стоп/тейк/удержание эволюционируют вместе с сигналами
     (раньше были зашиты 2.0/3.0/48 — сдерживали прибыльные геномы).
  2. ISLAND MODEL: несколько независимых популяций с миграцией — защита от
     вырождения в один и тот же геном (в v1 финалисты были клонами).
  3. ПРЕДКИ: лучшие геномы прошлых прогонов загружаются как затравка —
     эволюция кумулятивна между циклами, ДНК накапливается.
  4. ДEDUP ПО ПОВЕДЕНИЮ: клоны с одинаковым сигналом не проходят в финал.
  5. ЧЕСТНОСТЬ: fitness требует плюса на ОБЕИХ половинах года (walk-forward
     внутри фитнеса) + штраф, если одна сделка даёт >25% прибыли.

Fitness-бэктест — быстрый векторизованный симулятор с той же моделью исполнения,
что futures_lab.run_backtest. Финалисты честно перепроверяются РЕАЛЬНЫМ
run_backtest в воронке tools/select_stable_pool.py (те же гейты).

Выход: state/engine_candidates.json — общий приёмник всех движков.
Paper-only. Никаких брокерских вызовов.
"""
import argparse, json, sys, time, random
import numpy as np
import pandas as pd
from pathlib import Path

SC = Path("/root/prop-desk/strategy_combine")
FL = Path("/root/prop-desk/futures_lab")
sys.path.insert(0, str(SC)); sys.path.insert(0, str(FL)); sys.path.insert(0, str(SC / "tools"))
from state_io import atomic_write_json  # noqa: E402  # атомарная запись state-файлов
from engines.genome import (ATOM_PARAMS, LONG_ATOMS, SHORT_ATOMS, RISK_RANGES,
                            DEFAULT_RISK, genome_signal, genome_name, genome_desc)  # noqa: E402
from engines.exit_engine import (make_arr, run_loop, rand_exits, mutate_exits,
                                 crossover_exits, exits_key)  # noqa: E402

DATA = FL / "artifacts" / "tinkoff_futures_data"
CAP, COMM, SLIP = 20000.0, 5.0, 0.0001
OUT = SC / "state" / "engine_candidates.json"


def load(ticker, prefer_long=False):
    """Данные для эволюции. prefer_long=True → 1095d если есть (v4, audit):
    эволюция на 3 годах — случайные «чемпионы года» вымирают, остаются устойчивые.
    Воронка select_stable_pool по-прежнему фильтрует на 365d.
    Возвращает (df, dataset_id) — id ПО СОДЕРЖИМОМУ (hardening v2 §4): LKOH «1095d»
    побайтово равен «365d», и это теперь ВИДНО в кандидатах, а не скрыто именем."""
    from research_integrity import dataset_id as _dsid
    if prefer_long:
        pl = DATA / f"{ticker}_1095d_1h_continuous.csv"
        if pl.exists():
            df = pd.read_csv(pl).reset_index(drop=True)
            if len(df) > 3000:
                return df, _dsid(pl)
    p = DATA / f"{ticker}_365d_1h_continuous.csv"
    if not p.exists():
        return None, None
    return pd.read_csv(p).reset_index(drop=True), _dsid(p)


# ---------------- быстрый fitness-симулятор (сигналы + risk + exits гены) ----------------
_ARR_CACHE = {}


def get_arr(df, ticker):
    key = (ticker, len(df))
    if key not in _ARR_CACHE:
        _ARR_CACHE[key] = make_arr(df)
    return _ARR_CACHE[key]


def fast_eval(df, genome, arr=None):
    sig = genome_signal(df, genome).values
    risk = {**DEFAULT_RISK, **(genome.get("risk") or {})}
    exits = genome.get("exits") or {}
    if arr is None:
        arr = make_arr(df)
    # fitness-режим: 1 контракт, без урезания по марже, экономика та же
    metrics, trades, eq = run_loop(arr, sig, risk, exits, CAP, 1, 1.0, COMM, SLIP)
    pnls = [(t.exit_time, t.pnl) for t in trades]
    return metrics["final_equity"], pnls


def fitness(df, genome, arr=None):
    """Гладкий растущий equity + честность: обе половины года в плюсе,
    прибыль не из одной сделки."""
    equity, pnls = fast_eval(df, genome, arr=arr)
    if len(pnls) < 20:
        return -999.0, {}
    s = pd.Series({t: p for t, p in pnls}).sort_index()
    daily = s.resample("D").sum()
    eq = CAP + daily.cumsum()
    dd = float((eq.cummax() - eq).max())
    pnl = float(s.sum())
    monthly = s.resample("ME").sum()
    pm = int((monthly > 0).sum()); m = max(len(monthly), 1)
    calmar = pnl / dd if dd > 0 else (10.0 if pnl > 0 else 0.0)
    if pnl <= 0:
        return pnl / 1000.0, {"pnl": round(pnl), "dd": round(dd), "calmar": round(calmar, 2),
                              "trades": len(pnls), "profit_months": f"{pm}/{m}"}
    # половина года вперёд / назад — обе должны быть в плюсе
    half = len(s) // 2
    h1, h2 = float(s.iloc[:half].sum()), float(s.iloc[half:].sum())
    if h1 <= 0 or h2 <= 0:
        return calmar * 0.3, {"pnl": round(pnl), "dd": round(dd), "calmar": round(calmar, 2),
                              "trades": len(pnls), "profit_months": f"{pm}/{m}", "split": "1HALF"}
    # концентрация: одна сделка не должна давать >25% прибыли
    top_share = float(s.abs().max() / pnl)
    conc_pen = 0.5 if top_share > 0.25 else 1.0
    f = (calmar + 2.0 * (pm / m)) * conc_pen
    return f, {"pnl": round(pnl), "dd": round(dd), "calmar": round(calmar, 2),
               "trades": len(pnls), "profit_months": f"{pm}/{m}",
               "halves": f"{round(h1)}/{round(h2)}"}


# ---------------- генетика ----------------
def rand_atom(pool):
    name = random.choice(pool)
    args = []
    for lo, hi in ATOM_PARAMS[name]:
        args.append(round(random.uniform(lo, hi), 2) if isinstance(lo, float) else random.randint(int(lo), int(hi)))
    return [name] + args


def rand_risk():
    return {k: round(random.uniform(lo, hi), 2) if lo < 1 or hi > 10 else random.randint(int(lo), int(hi))
            for k, (lo, hi) in RISK_RANGES.items()}


def rand_genome(with_risk=True):
    g = {"long": [rand_atom(LONG_ATOMS) for _ in range(random.randint(1, 3))],
         "short": [rand_atom(SHORT_ATOMS) for _ in range(random.randint(1, 3))]}
    # v4: голосование k-из-N (иногда строгое И = N, чаще k<N — больше сделок, меньше переобучения)
    if random.random() < 0.7 and (len(g["long"]) > 1 or len(g["short"]) > 1):
        g["vote"] = {}
        if len(g["long"]) > 1:
            g["vote"]["long"] = random.randint(1, len(g["long"]))
        if len(g["short"]) > 1:
            g["vote"]["short"] = random.randint(1, len(g["short"]))
    if with_risk:
        g["risk"] = rand_risk()
        g["exits"] = rand_exits()   # v3: вид стопов/тейков — тоже ДНК
    return g


def mutate(g):
    g = json.loads(json.dumps(g))
    op = random.random()
    if op < 0.25:                                  # мутация exit-генов (v3)
        g["exits"] = mutate_exits(g.get("exits") or {})
        return g
    if op < 0.40:                                  # мутация рисковых генов
        k = random.choice(list(RISK_RANGES))
        lo, hi = RISK_RANGES[k]
        r = g.setdefault("risk", dict(DEFAULT_RISK))
        cur = r.get(k, DEFAULT_RISK[k])
        if k == "max_hold":
            r[k] = int(min(hi, max(lo, cur * random.uniform(0.6, 1.6))))
        else:
            r[k] = round(min(hi, max(lo, cur * random.uniform(0.7, 1.4))), 2)
        return g
    side = random.choice(["long", "short"])
    if op < 0.5 and g[side]:                       # джиттер параметра
        a = random.choice(g[side])
        ranges = ATOM_PARAMS[a[0]]
        if not ranges:
            pool = LONG_ATOMS if side == "long" else SHORT_ATOMS
            g[side][g[side].index(a)] = rand_atom(pool)
        else:
            k = random.randrange(len(ranges)) + 1
            lo, hi = ranges[k - 1]
            if isinstance(lo, float):
                a[k] = round(min(hi, max(lo, a[k] * random.uniform(0.7, 1.4))), 2)
            else:
                a[k] = int(min(hi, max(lo, a[k] * random.uniform(0.7, 1.4))))
    elif op < 0.75:                                # замена атома
        pool = LONG_ATOMS if side == "long" else SHORT_ATOMS
        if g[side]:
            g[side][random.randrange(len(g[side]))] = rand_atom(pool)
    elif op < 0.9 and len(g[side]) < 4:            # добавить атом
        pool = LONG_ATOMS if side == "long" else SHORT_ATOMS
        g[side].append(rand_atom(pool))
    elif len(g[side]) > 1:                         # удалить атом
        g[side].pop(random.randrange(len(g[side])))
    # v4: после изменения списков атомов зажать vote под новые длины
    v = g.get("vote")
    if v:
        if g.get("long"):
            v["long"] = max(1, min(int(v.get("long", len(g["long"]))), len(g["long"])))
        else:
            v.pop("long", None)
        if g.get("short"):
            v["short"] = max(1, min(int(v.get("short", len(g["short"]))), len(g["short"])))
        else:
            v.pop("short", None)
        if not v:
            g.pop("vote", None)
    return g


def crossover(a, b):
    child = {"long": list(random.choice([a, b])["long"]),
             "short": list(random.choice([a, b])["short"])}
    if random.random() < 0.5 and child["long"] and b["long"]:
        i = random.randrange(len(child["long"]))
        child["long"][i] = random.choice(b["long"])
    if random.random() < 0.5 and child["short"] and b["short"]:
        i = random.randrange(len(child["short"]))
        child["short"][i] = random.choice(b["short"])
    ra, rb = a.get("risk") or {}, b.get("risk") or {}
    child["risk"] = {k: (ra.get(k, DEFAULT_RISK[k]) if random.random() < 0.5
                         else rb.get(k, DEFAULT_RISK[k])) for k in DEFAULT_RISK}
    child["exits"] = crossover_exits(a.get("exits") or {}, b.get("exits") or {})  # v3
    # v4: vote-ген наследуется от случайного родителя, затем зажимается под длину списков
    va, vb = a.get("vote") or {}, b.get("vote") or {}
    vote = dict(va if random.random() < 0.5 else vb)
    if vote:
        if child["long"]:
            vote["long"] = max(1, min(int(vote.get("long", len(child["long"]))), len(child["long"])))
        else:
            vote.pop("long", None)
        if child["short"]:
            vote["short"] = max(1, min(int(vote.get("short", len(child["short"]))), len(child["short"])))
        else:
            vote.pop("short", None)
        if vote:
            child["vote"] = vote
    return child


def behavior_key(df, genome):
    """Дедуп по ПОВЕДЕНИЮ: одинаковый сигнал = клон, даже если геном syntactic разный.
    v3.1 (2026-09-05): СТРОГИЙ отпечаток — позиции сигнальных баров, а не их
    количество. Раньше считали только (n_long, n_short), и «братья» из одной
    генетической линии с совпадающими счётчиками пролезали как уникальные
    (45 кандидатов LKOH = по факту 2-3 разные стратегии)."""
    import hashlib
    sig = genome_signal(df, genome)
    idx = np.flatnonzero(sig != 0)
    fp = hashlib.sha256(np.ascontiguousarray(sig.values, dtype=np.int8)).hexdigest()[:16]
    return (fp, int((sig > 0).sum()), int((sig < 0).sum()))


def ancestors(ticker, n=4):
    """Предки из прошлых прогонов — эволюция кумулятивна между циклами."""
    if not OUT.exists():
        return []
    try:
        cands = json.loads(OUT.read_text()).get("candidates", [])
    except Exception:
        return []
    pool = [c for c in cands if c.get("engine") == "genetic" and c.get("ticker") == ticker and c.get("genome")]
    pool.sort(key=lambda c: -(c.get("fitness") or 0))
    return [c["genome"] for c in pool[:n]]


def evolve(ticker, df, gens, pop, seed, islands=3, dataset_id=None):
    rng = random.Random(seed)
    random.seed(seed)
    arr = get_arr(df, ticker)   # кэш массивов/индикаторов на весь прогон тикера
    anc = ancestors(ticker)
    trials = {"n": 0}           # hardening v2 §5: ЧЕСТНЫЙ счётчик всех оценок геномов
    # островная модель: каждая популяция со своим seed и долей предков
    pops = []
    for isl in range(islands):
        p = []
        for g in anc[:2]:
            gg = json.loads(json.dumps(g))
            gg.setdefault("risk", rand_risk())
            gg.setdefault("exits", rand_exits())   # v3: предки тоже получают exit-ДНК
            p.append(gg)
        while len(p) < pop:
            p.append(rand_genome())
        pops.append(p)
    best_overall = (-999.0, None, {})
    for gen in range(gens):
        island_best = []
        for isl, population in enumerate(pops):
            scored = []
            for g in population:
                f, st = fitness(df, g, arr=arr)
                trials["n"] += 1
                scored.append((f, g, st))
            scored.sort(key=lambda x: -x[0])
            if scored[0][0] > best_overall[0]:
                best_overall = scored[0]
            island_best.append(scored[0][1])
            elite = [s[1] for s in scored[:max(2, pop // 10)]]
            pool2 = [s[1] for s in scored[:pop // 2]]
            nxt = list(elite)
            while len(nxt) < pop:
                a, b = rng.choice(elite + pool2), rng.choice(pool2)
                child = crossover(a, b)
                if rng.random() < 0.7:
                    child = mutate(child)
                nxt.append(child)
            pops[isl] = nxt
        # миграция: каждый остров получает чемпиона соседа
        if gen % 3 == 2:
            for isl in range(islands):
                champ = island_best[(isl + 1) % islands]
                pops[isl][-1] = json.loads(json.dumps(champ))
        if gen % 5 == 0 or gen == gens - 1:
            print(f"  [{ticker}] gen {gen+1}/{gens} best_fit={best_overall[0]:.2f} top={best_overall[2]}", flush=True)
    # финал: все острова вместе, дедуп по имени и поведению
    allg = [g for p in pops for g in p]
    scored = []
    for g in allg:
        f, st = fitness(df, g, arr=arr)
        trials["n"] += 1
        scored.append((f, g, st))
    scored.sort(key=lambda x: -x[0])
    # распределение fitness ПО ВСЕМ попыткам (нужно для V[SR] в DSR — §5)
    all_fits = sorted(s[0] for s in scored)
    seen_name, seen_beh, out = set(), set(), []
    for f, g, st in scored:
        n = genome_name(g)
        bk = (behavior_key(df, g), exits_key(g.get("exits")))   # v3: разные выходы — не клон
        if n in seen_name or bk in seen_beh or f <= 0:
            continue
        seen_name.add(n); seen_beh.add(bk)
        out.append({"engine": "genetic", "ticker": ticker, "name": n, "genome": g,
                    "desc": genome_desc(g), "risk": g.get("risk") or {},
                    "exits": g.get("exits") or {},
                    "fitness": round(f, 3), "stats": st,
                    # hardening v2 §4/§5 — научная честность кандидата:
                    "dataset_id": dataset_id,          # content-derived id данных
                    "trials_count": trials["n"],       # из скольких оценок он выбран
                    "seed": seed, "gens": gens, "pop": pop, "islands": islands,
                    "fitness_p25": round(all_fits[len(all_fits)//4], 3) if all_fits else None,
                    "fitness_median": round(all_fits[len(all_fits)//2], 3) if all_fits else None,
                    "n_evaluated": len(all_fits)})
        if len(out) >= 5:
            break
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", default="CNY,IMOEX,GAZP,SBER,LKOH")
    ap.add_argument("--gens", type=int, default=12)
    ap.add_argument("--pop", type=int, default=40)
    ap.add_argument("--islands", type=int, default=3)
    ap.add_argument("--seed", type=int, default=int(time.time()) % 100000)
    ap.add_argument("--long-data", action="store_true",
                    help="v4: эволюция на 1095d если есть (устойчивость на 3 годах)")
    args = ap.parse_args()
    t0 = time.time()
    results = []
    registry_rows = []
    for t in args.tickers.split(","):
        df, dsid = load(t.strip(), prefer_long=args.long_data)
        if df is None or len(df) < 1000:
            print(f"  [{t}] no data, skip"); continue
        res = evolve(t.strip(), df, args.gens, args.pop, args.seed, args.islands,
                     dataset_id=dsid)
        results += res
        # §5: регистрируем ВЕСЬ прогон тикера (победитель знает trials_count)
        if res:
            registry_rows.append({
                "engine": "genetic", "ticker": t.strip(), "dataset_id": dsid,
                "seed": args.seed, "gens": args.gens, "pop": args.pop,
                "islands": args.islands, "long_data": bool(args.long_data),
                "trials_count": res[0].get("trials_count"),
                "n_finalists": len(res),
                "finalists": [r["name"] for r in res],
                "best_fitness": res[0].get("fitness"),
            })
    old = []
    if OUT.exists():
        try:
            old = json.loads(OUT.read_text()).get("candidates", [])
        except Exception:
            old = []
    merged = {c["name"]: c for c in old}
    for c in results:
        merged[c["name"]] = c
    OUT.parent.mkdir(exist_ok=True)
    atomic_write_json(OUT, {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "candidates": list(merged.values())})
    # §5: append-only реестр экспериментов — проигравшие не теряются
    if registry_rows:
        from research_integrity import append_experiment
        append_experiment(registry_rows)
    print(f"\nGENETIC ENGINE v3 done: {len(results)} new finalists, total pool {len(merged)}, {time.time()-t0:.0f}s")
    for c in results:
        print(f"  {c['ticker']:<6} {c['name']} fit={c['fitness']:.2f} {c['stats']} exits={exits_key(c['exits'])} risk={c['risk']} :: {c['desc'][:70]}")
    print(f"SAVED {OUT}")


if __name__ == "__main__":
    main()
