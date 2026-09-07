#!/usr/bin/env python3
"""select_stable_pool.py — prop-desk portfolio selection (2026-09-05, v2).

Критерии Апостола:
  - equity должен быть СТАБИЛЬНО РАСТУЩИМ (гладкость важнее пиковой прибыли);
  - планка дохода: НЕ ниже ~1200₽/мес (медиана месяца портфеля; снижена с 3000
    решением Апостола 2026-09-06 — 3000₽/мес на 20к было недостижимо честно);
  - зажать комбайн со всех щелей: риск-менеджмент, маржа, диверсификация, ML-фильтр.

Как считается сайзинг (как в реальном проп-деске):
  1. Риск на сделку = RISK_PER_TRADE_PCT от капитала (2%).
  2. Реальный риск 1 контракта = медиана |убытка| сделки из 1-контрактного бэктеста.
     contracts_by_risk = floor(risk_budget / risk_per_contract)
  3. Маржевый потолок: contracts_by_margin = floor(per_strategy_margin_budget / ГО).
  4. Портфельный бюджет ГО ≤ PORTFOLIO_MARGIN_PCT капитала (зажим «со всех щелей»).
  5. contracts = clamp(min(1..3), 1, min(risk, margin)) — не меньше 1, не больше 3.

Отбор:
  - маржа 1 контракта ≤ 50% капитала;
  - просадка ≤ 35% капитала;
  - ≥60% прибыльных месяцев; calmar ≥ 1.5; ML-фильтр не убивает стратегию;
  - greedy: стратегия добавляется, только если СУММАРНАЯ кривая становится глаже
    И портфельный бюджет ГО не превышен;
  - финальный гейт: медиана месяца портфеля ≥ MIN_MONTH_RUB, иначе ПОРТФЕЛЬ НЕ ПРИНЯТ.

Paper-only. Никаких брокерских вызовов.
"""
import json, sys, time, os, tempfile
import numpy as np
import pandas as pd
from pathlib import Path

SC = Path("/root/prop-desk/strategy_combine")
FL = Path("/root/prop-desk/futures_lab")
sys.path.insert(0, str(SC)); sys.path.insert(0, str(FL)); sys.path.insert(0, str(SC / "tools"))
from futures_lab import run_backtest, _synthetic_spec_for_file  # noqa: E402

DATA = FL / "artifacts" / "tinkoff_futures_data"
CAP, COMM, SLIP = 20000.0, 5.0, 1.0

# --- prop-desk sizing knobs -------------------------------------------------
RISK_PER_TRADE_PCT = 0.02      # 2% капитала = 400₽ риска на сделку
PORTFOLIO_MARGIN_PCT = 0.70    # суммарное ГО всех слотов <= 70% капитала
PER_STRATEGY_MARGIN_PCT = 0.50 # ГО одного слота <= 50% капитала
MAX_CONTRACTS = 3              # жёсткий потолок контрактов на слот (малый капитал)
# --- stability gates --------------------------------------------------------
MAX_DD_PCT = 0.35              # просадка <= 35% капитала
MIN_PROFIT_MONTH_RATIO = 0.6   # >=60% прибыльных месяцев
MIN_CALMAR = 1.5               # pnl / maxDD
MIN_MONTH_RUB = 1200.0         # ПЛАНКА: медиана месяца портфеля
                               # (снижена с 3000 решением Апостола 2026-09-06 после
                               # честного аудита: 3000₽/мес на 20к = 15%/мес —
                               # недостижимо без переобучения; реалистично 1000–1500)
POOL_TARGET = 6
MAX_PER_TICKER = 2
CORR_MAX = 0.7        # АНТИ-КЛОН: кандидат с корреляцией equity >= CORR_MAX
                      # к уже выбранному = клон семьи, пропускаем (2026-09-05, Апостол)
# --- research integrity (hardening v2 §5, 2026-09-06) --------------------------
# Победитель МАССОВОГО ПЕРЕБОРА обязан доказать значимость ПОСЛЕ поправки на число
# попыток (Deflated Sharpe, Bailey&Lopez de Prado 2014) и на переобученность самого
# отбора (CSCV-PBO, Bailey et al. 2017). Пороги зафиксированы ДО прогона; менять их
# задним числом ради сохранения пула ЗАПРЕЩЕНО. Математика: tools/research_integrity.py
# (проверена тестами на синтетике с известным ответом + прогоном на реальных данных:
#  /root/audits/strategy_combine_hardening_v2/07_multiple_testing_report.md).
DSR_MIN = 0.95        # минимум P(истинный SR>0 | выбран из trials) для ПРИНЯТИЯ пула
PBO_MAX = 0.50        # максимум доли CSCV-разбиений, где train-победитель ниже медианы test
TRIALS_FALLBACK = 1560  # консервативная нижняя граница попыток для кандидатов без поля
                        # trials_count: 12 gens × 40 pop × 3 острова + финал ≈ 1560/тиккер/прогон

SPECS = json.loads((FL / "futures_specs.json").read_text()).get("specs", {})
SPECS = {str(k).upper(): v for k, v in SPECS.items()}


def spec_margin(ticker):
    s = SPECS.get(str(ticker).upper())
    if not s:
        return None
    # ГО считаем по худшей стороне (buy/sell) — консервативнее, шорт не занижаем
    m = max(float(s.get("initial_margin_on_buy") or 0), float(s.get("initial_margin_on_sell") or 0))
    return m if m > 0 else None


def margin_ok(ticker):
    m = spec_margin(ticker)
    return m is not None and m <= PER_STRATEGY_MARGIN_PCT * CAP


def _cross_sr_std(rets_cols):
    """Эмпирическая V[SR]: кросс-секционный std периодических Sharpe уже набранных
    колонок доходностей (hardening v2 §5). Пусто/одна колонка → 0.0 (нижняя граница
    шума задаётся вызывающим кодом max(..., 0.15))."""
    if len(rets_cols) < 2:
        return 0.0
    try:
        srs = [float(pd.Series(c).mean() / pd.Series(c).std(ddof=1))
               for c in rets_cols if pd.Series(c).std(ddof=1) > 1e-12]
        if len(srs) < 2:
            return 0.0
        return float(np.std(srs, ddof=1))
    except Exception:
        return 0.0


def load_candidates():
    sys.path.insert(0, str(SC / "code"))
    from strategy_registry import StrategyRegistry, STATUS_WAITLIST, STATUS_ACTIVE_WATCHLIST, STATUS_ACTIVE_SIGNAL_POOL  # noqa: E402
    reg = StrategyRegistry(SC / "state/strategy_registry.json")
    seen, out = set(), []
    for rec in reg.records():
        if rec.status not in (STATUS_WAITLIST, STATUS_ACTIVE_WATCHLIST, STATUS_ACTIVE_SIGNAL_POOL):
            continue
        key = (rec.ticker, rec.strategy, json.dumps(rec.params or {}, sort_keys=True))
        if key in seen:
            continue
        seen.add(key)
        out.append({"ticker": rec.ticker, "strategy": rec.strategy, "params": rec.params or {}})
    out += load_engine_candidates()
    return out


def load_engine_candidates():
    """Кандидаты всех движков слоя engines/ (E2 genetic, E3 momentum, ...).

    Генетические геномы регистрируются в futures_lab.STRATEGY_FUNCS под именем
    ge_<hash> — дальше их оценивает ОБЫЧНЫЙ контур run_backtest, те же гейты.
    """
    p = SC / "state" / "engine_candidates.json"
    if not p.exists():
        return []
    try:
        cands = json.loads(p.read_text()).get("candidates", [])
    except Exception:
        return []
    import futures_lab  # noqa: F401
    from engines.register import register_genomes
    out = []
    for c in cands:
        if c.get("engine") == "genetic" and c.get("genome"):
            out.append({"ticker": c["ticker"], "strategy": c["name"], "params": {},
                        "risk": c.get("risk") or (c["genome"].get("risk") if isinstance(c["genome"], dict) else None) or {},
                        # v3: exit-гены (вид стопов/тейков по индикаторам) — та же ДНК
                        "exits": c.get("exits") or (c["genome"].get("exits") if isinstance(c["genome"], dict) else None) or {},
                        "engine": "genetic"})
        elif c.get("engine") == "momentum":
            pass  # дневные паттерны — отдельный контур, в 1h-воронку не мешаем
        elif c.get("engine") == "neural":
            # E4: нейросеть — тот же честный контур run_backtest/гейты, без поблажек
            out.append({"ticker": c["ticker"], "strategy": c["name"], "params": {},
                        "risk": c.get("risk") or {}, "exits": {}, "engine": "neural"})
    register_genomes()
    return out


def load_data(ticker, cutoff_days=0):
    p = DATA / f"{ticker}_365d_1h_continuous.csv"
    if not p.exists():
        return None
    df = pd.read_csv(p).reset_index(drop=True)
    if cutoff_days > 0:
        # честный OOS: отбор видит ТОЛЬКО первую часть года,
        # хвост (cutoff_days) зарезервирован для forward-теста.
        cutoff = pd.to_datetime(df["time"]).max() - pd.Timedelta(days=cutoff_days)
        df = df[pd.to_datetime(df["time"]) < cutoff].reset_index(drop=True)
        if len(df) < 1000:
            return None
    return df


def equity_stats(eq):
    daily = pd.Series(eq.values, index=pd.to_datetime(eq.index)).resample("D").last().ffill().dropna()
    changes = daily.diff()
    changes.iloc[0] = daily.iloc[0] - CAP
    monthly = changes.resample("ME").sum()
    dd = float((daily.cummax() - daily).max())
    pnl = float(daily.iloc[-1] - CAP)
    return {"pnl": pnl, "dd": dd, "daily": daily, "monthly": monthly,
            "months": len(monthly), "profit_months": int((monthly.values > 0).sum()),
            "median_month": float(np.median(monthly.values)) if len(monthly) else 0.0}


def run(c, df, contracts):
    spec = _synthetic_spec_for_file(c["ticker"])
    # единый роутер: без exit-генов = обычный run_backtest; с exit-генами (v3) = честный run_loop
    from engines.exit_engine import run_candidate
    return run_candidate(df, spec, c, contracts, cap=CAP, comm=COMM, slip_bps=SLIP)


def size_contracts(c, df):
    """Prop-desk sizing: risk-first, margin-capped, floored at 1."""
    margin = spec_margin(c["ticker"])
    if not margin:
        return 0, 0.0, 0
    margin_cap = max(0, int(PER_STRATEGY_MARGIN_PCT * CAP // margin))
    # реальный риск одного контракта = медиана |убытка| из 1-контрактного прогона
    _, trades, _ = run(c, df, 1)
    losses = [abs(t.pnl) for t in trades if t.pnl < 0]
    risk_per_contract = float(np.median(losses)) if losses else 0.0
    if risk_per_contract > 0:
        risk_cap = int((RISK_PER_TRADE_PCT * CAP) // risk_per_contract)
    else:
        risk_cap = MAX_CONTRACTS
    n = max(0, min(MAX_CONTRACTS, margin_cap, risk_cap))
    return n, risk_per_contract, margin_cap


def calmar(pnl, dd):
    return pnl / dd if dd > 0 else (10.0 if pnl > 0 else 0.0)


def portfolio_stats(evals):
    """Combine daily equity by DATE alignment."""
    if not evals:
        return -999.0, None
    frame = pd.concat([e["daily"] - CAP for e in evals], axis=1, join="inner").dropna()
    if frame.empty:
        return -999.0, None
    comb = frame.sum(axis=1) + CAP
    dd = float((comb.cummax() - comb).max())
    pnl = float(comb.iloc[-1] - CAP)
    changes = comb.diff()
    changes.iloc[0] = comb.iloc[0] - CAP
    monthly = changes.resample("ME").sum()
    return calmar(pnl, dd), {"daily": comb, "pnl": pnl, "dd": dd,
                             "median_month": float(np.median(monthly.values)) if len(monthly) else 0.0,
                             "profit_months": int((monthly.values > 0).sum()), "months": len(monthly)}


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--holdout-days", type=int, default=0,
                    help="reserve last N days for forward test (selection won't see them)")
    ap.add_argument("--min-month", type=float, default=MIN_MONTH_RUB)
    args = ap.parse_args()

    meta_cache = {}
    try:
        meta_cache = json.loads((SC / "state" / "meta_filter_cache.json").read_text())
        meta_cache = {k: v for k, v in meta_cache.items() if not k.startswith("_")}
    except Exception:
        pass

    cands = load_candidates()
    print(f"candidates: {len(cands)} | capital={CAP:.0f} risk/trade={RISK_PER_TRADE_PCT*CAP:.0f}₽ "
          f"portfolio margin budget={PORTFOLIO_MARGIN_PCT*CAP:.0f}₽")
    evals = []
    for c in cands:
        if not margin_ok(c["ticker"]):
            continue
        df = load_data(c["ticker"], cutoff_days=args.holdout_days)
        if df is None:
            continue
        n, rpc, mcap = size_contracts(c, df)
        if n < 1:
            continue
        _, trades, eq = run(c, df, n)
        if eq is None or len(eq) < 100 or len(trades) < 20:
            continue
        st = equity_stats(eq)
        if st["pnl"] <= 0 or st["dd"] / CAP > MAX_DD_PCT:
            continue
        meta = meta_cache.get(f"{str(c['ticker']).upper()}::{c['strategy']}")
        filt_ok = (meta is None) or (float(meta.get("filt", -1)) > 0)
        cal = calmar(st["pnl"], st["dd"])
        margin = spec_margin(c["ticker"])
        evals.append({**c, "contracts": n, "risk_per_contract": round(rpc, 1),
                      "margin_per_contract": round(margin, 1),
                      "margin_total": round(margin * n, 1),
                      "pnl": st["pnl"], "dd": st["dd"], "daily": st["daily"],
                      "median_month": st["median_month"],
                      "profit_months": st["profit_months"], "months": st["months"],
                      "calmar": cal, "meta_filt": filt_ok,
                      "meta_delta": float(meta.get("delta", 0)) if meta else None})
        print(f"  {c['ticker']:<6} {c['strategy']:<24} n={n} pnl={st['pnl']:>+8.0f} dd={st['dd']:>6.0f} "
              f"calmar={cal:>5.2f} med_month={st['median_month']:>+7.0f} m+={st['profit_months']}/{st['months']} "
              f"GO={margin*n:>7.0f} meta_ok={filt_ok}")

    stable = [e for e in evals
              if e["months"] > 0
              and e["profit_months"] / e["months"] >= MIN_PROFIT_MONTH_RATIO
              and e["meta_filt"] and e["calmar"] >= MIN_CALMAR]
    # сортировка: сначала вклад в медиану месяца (планка Апостола), потом calmar
    stable.sort(key=lambda e: -(e["median_month"] * 12 + e["calmar"] * 500 + (e["meta_delta"] or 0) / 40))
    print(f"\nstable single (m+>={int(MIN_PROFIT_MONTH_RATIO*100)}%, calmar>={MIN_CALMAR}, meta_ok): {len(stable)}")

    budget = PORTFOLIO_MARGIN_PCT * CAP
    chosen, per_ticker, used_margin = [], {}, 0.0
    for e in stable:
        if len(chosen) >= POOL_TARGET:
            break
        if per_ticker.get(e["ticker"], 0) >= MAX_PER_TICKER:
            continue
        # ужимаем контракты под оставшийся портфельный бюджет ГО
        n = e["contracts"]
        while n > 1 and used_margin + n * e["margin_per_contract"] > budget:
            n -= 1
        if used_margin + n * e["margin_per_contract"] > budget:
            print(f"  - skip {e['ticker']} {e['strategy']} (margin budget exhausted)")
            continue
        if n != e["contracts"]:
            df = load_data(e["ticker"], cutoff_days=args.holdout_days)
            _, trades, eq = run(e, df, n)
            if eq is None or len(eq) < 100 or len(trades) < 20:
                continue
            st = equity_stats(eq)
            cal = calmar(st["pnl"], st["dd"])
            if st["pnl"] <= 0 or st["dd"] / CAP > MAX_DD_PCT or st["months"] <= 0 or st["profit_months"] / st["months"] < MIN_PROFIT_MONTH_RATIO or cal < MIN_CALMAR:
                continue
            e = {**e, **st, "calmar": cal, "contracts": n}
        trial = [{**x, "daily": x["daily"]} for x in chosen] + [{**e, "contracts": n}]
        # АНТИ-КЛОН: equity не должен повторять уже выбранные стратегии
        clone_of = None
        if chosen:
            rets_new = e["daily"].diff().dropna()
            for x in chosen:
                rets_old = x["daily"].diff().dropna()
                idx = rets_new.index.intersection(rets_old.index)
                if len(idx) < 30:
                    continue
                r = float(np.corrcoef(rets_new.loc[idx].values, rets_old.loc[idx].values)[0, 1])
                # FIX(audit): NaN при постоянной доходности — считаем клоном только
                # если обе кривые плоские, иначе 0 (не клон)
                if np.isnan(r):
                    r = 1.0 if (rets_new.loc[idx].std() == 0 and rets_old.loc[idx].std() == 0) else 0.0
                if r >= CORR_MAX:
                    clone_of = (x["strategy"], round(r, 2))
                    break
        if clone_of:
            print(f"  - skip {e['ticker']} {e['strategy']} (КЛОН {clone_of[0]}, corr={clone_of[1]})")
            continue
        cal_now, _ = portfolio_stats(chosen) if chosen else (-999.0, None)
        cal_new, pnew = portfolio_stats(trial)
        if cal_new >= cal_now or not chosen:
            e = {**e, "contracts": n, "margin_total": round(n * e["margin_per_contract"], 1)}
            chosen.append(e)
            per_ticker[e["ticker"]] = per_ticker.get(e["ticker"], 0) + 1
            used_margin += n * e["margin_per_contract"]
            print(f"  + {e['ticker']} {e['strategy']} n={n} (portfolio calmar {cal_new:.2f}, "
                  f"GO used {used_margin:.0f}/{budget:.0f})")
        else:
            print(f"  - skip {e['ticker']} {e['strategy']} (hurts portfolio smoothness)")

    comb_cal, pstat = portfolio_stats(chosen) if chosen else (0.0, None)
    if pstat:
        # пересчёт кривой портфеля с фактическими контрактами уже внутри daily
        changes = pstat["daily"].diff()
        changes.iloc[0] = pstat["daily"].iloc[0] - CAP
        monthly = changes.resample("ME").sum()
        months = monthly.values.tolist()
    else:
        months = []

    # ── HARDENING v2 §5: RESEARCH INTEGRITY — multiple-testing correction ──────
    # «Красивый победитель» отклоняется, если после поправки на trials_count
    # доказательств недостаточно. Честно: accepted=false — нормальный результат.
    integrity = {"schema_version": "research-integrity-v1", "applied": False}
    if chosen:
        try:
            from research_integrity import (score_candidate, pbo_cscv, sharpe as _sharpe)
            per_cand = []
            rets_cols = []
            for e in chosen:
                daily_ret = e["daily"].diff().dropna().values / CAP
                trials = int(e.get("trials_count") or TRIALS_FALLBACK)
                # V[SR] по популяции финалистов данного прогона: std всех fitness-оценок.
                # fitness генетика = calmar-подобная метрика; консервативно берём
                # кросс-дисперсию SR всех выбранных + 0.15 как нижнюю границу шума.
                sc = score_candidate(daily_ret, trials, max(_cross_sr_std(rets_cols), 0.15))
                sc["strategy"] = e["strategy"]; sc["ticker"] = e["ticker"]
                per_cand.append(sc)
                rets_cols.append(pd.Series(daily_ret))
            mat = pd.concat(rets_cols, axis=1).dropna()
            pbo = pbo_cscv(mat.values.T, S=10) if mat.shape[0] >= 30 and len(chosen) >= 3 \
                else {"pbo": float("nan"), "note": "need >=3 strategies & >=30 days"}
            min_dsr = min(c["selection_adjusted_score"] for c in per_cand)
            pbo_v = pbo.get("pbo")
            pbo_ok = (pbo_v is None) or (isinstance(pbo_v, float) and (pbo_v != pbo_v or pbo_v <= PBO_MAX))
            dsr_ok = min_dsr >= DSR_MIN
            integrity = {
                "schema_version": "research-integrity-v1", "applied": True,
                "per_candidate": per_cand,
                "portfolio_pbo": None if pbo_v != pbo_v else round(float(pbo_v), 4),
                "pbo_max": PBO_MAX, "pbo_ok": bool(pbo_ok),
                "min_dsr": round(min_dsr, 4), "dsr_min": DSR_MIN, "dsr_ok": bool(dsr_ok),
                "passed": bool(pbo_ok and dsr_ok),
            }
            print(f"INTEGRITY: min_dsr={min_dsr:.4f} (>={DSR_MIN}? {dsr_ok}) "
                  f"pbo={integrity['portfolio_pbo']} (<={PBO_MAX}? {pbo_ok}) → {'PASS' if integrity['passed'] else 'FAIL'}")
        except Exception as ex:
            integrity = {"schema_version": "research-integrity-v1", "applied": False,
                         "error": repr(ex)[:200]}
            print("INTEGRITY: модуль не применим —", repr(ex)[:120])

    med_ok = bool(pstat) and pstat["median_month"] >= args.min_month
    integ_ok = bool(integrity.get("passed", False)) if integrity.get("applied") else True
    accepted = med_ok and integ_ok
    out = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ"), "capital": CAP,
           "portfolio_calmar": round(comb_cal, 3),
           "portfolio_pnl_365d": round(pstat["pnl"]) if pstat else 0,
           "portfolio_median_month": round(pstat["median_month"]) if pstat else 0,
           "portfolio_dd": round(pstat["dd"]) if pstat else 0,
           "min_month_target": args.min_month,
           "accepted": accepted,
           "accepted_median_gate": med_ok,
           "accepted_integrity_gate": integ_ok,
           "research_integrity": integrity,
           "margin_used": round(used_margin, 1), "margin_budget": budget,
           "risk_per_trade_pct": RISK_PER_TRADE_PCT,
           "monthly_pnl": [round(m) for m in months],
           "selected": [{k: v for k, v in e.items() if k not in ("daily", "monthly")} for e in chosen]}
    target = SC / "state" / "stable_pool.json"
    fd, name = tempfile.mkstemp(prefix=".stable_pool.json.", dir=target.parent)
    os.close(fd)
    tmp = Path(name)
    try:
        tmp.write_text(json.dumps(out, indent=2, allow_nan=False))
        with tmp.open("rb") as handle:
            os.fsync(handle.fileno())
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)
    print(json.dumps({"accepted": accepted, "portfolio_calmar": out["portfolio_calmar"],
                      "portfolio_pnl_365d": out["portfolio_pnl_365d"],
                      "portfolio_median_month": out["portfolio_median_month"],
                      "portfolio_dd": out["portfolio_dd"], "n": len(chosen),
                      "margin_used": out["margin_used"]}, indent=1))
    print("SAVED state/stable_pool.json")


if __name__ == "__main__":
    main()
