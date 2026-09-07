"""Супервизор комбайна — главный цикл (registry-first).

Каждый цикл:
  1. Регим-детектор по юниверсу (раз в 6 часов)
  2. Тик движка: сигналы/входы/выходы по слотам
  3. Риск-ревизия слотов (eject правила)
  4. Adaptive Registry: ingest → refresh → derive legacy views (single source of truth)
  5. Заполнение слотов: лучший кандидат из signal pool вместо худшего слота
  6. Rollover кандидатов с истёкшим TTL
  7. Фидбек генератору (агрегация из analytics)

Legacy signal pool manipulation (fill from waitlist, conflict resolution,
rotation, expired revival) was removed — the adaptive registry handles all
classification and status transitions.  The signal_pool/waitlist dicts are
derived from the registry via build_legacy_views() after each tick.

Запуск: каждые 15 минут по cron/systemd, синхронно.
"""
import json
import fcntl
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from . import config as cfg_mod
from . import registry
from . import analytics as an
from .risk import RiskManager
from .regime import scan_universe
from .engine import Engine
from .strategy_registry import StrategyRegistry as AdaptiveRegistry
from .strategy_supervisor_flow import (
    build_legacy_views,
    ingest_generated_strategies,
    refresh_active_watchlist,
)
from tinkoff.invest import Client

# Regime gate + scorecard for candidate filtering
_CODE_DIR = str(Path(__file__).resolve().parent.parent / "code")
if _CODE_DIR not in sys.path:
    sys.path.insert(0, _CODE_DIR)
from regime_gate import admit as regime_admit  # noqa: E402
from slot_scorecard import slot_scorecard  # noqa: E402
from strategy_replacement_policy import (  # noqa: E402
    PortfolioState, portfolio_aware_score, adaptive_threshold,
)

STATE_DIR = Path(__file__).resolve().parent.parent / "state"
DATA_DIR = Path("/root/prop-desk/futures_lab/artifacts/tinkoff_futures_data")
LOG = Path("/root/prop-desk/logs/combine_supervisor.log")


def live_go(ticker: str, fallback: float) -> float:
    """Живое ГО за контракт из спеки Tinkoff (read-only). Fallback если API недоступен."""
    try:
        from futures_lab import load_token, resolve_spec
        from core.engine import API_PREFIX
        from tinkoff.invest import Client
        with Client(load_token()) as c:
            return float(resolve_spec(c, API_PREFIX.get(ticker, ticker)).active_margin)
    except Exception as exc:
        log("WARNING: live_go fallback для %s (API недоступен: %s) — используем %.0f" % (ticker, exc, fallback))
        return fallback


def log(msg: str):
    line = "%s %s" % (datetime.now(timezone.utc).strftime("%F %T"), msg)
    print(line, flush=True)
    LOG.parent.mkdir(exist_ok=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")


def regime_fresh(snap_path: Path, max_age_s: float = 6 * 3600) -> bool:
    return snap_path.exists() and (time.time() - snap_path.stat().st_mtime) < max_age_s


def universe_admission(ticker, configured_universe) -> dict:
    """Fail-closed admission for the supervisor's auto-swap path.

    Runtime candidates use configured *root* tickers (for example ``LKOH``),
    not broker contract aliases (for example ``LKU6``).  Alias resolution is
    deliberately outside this boundary: an unknown root must never become an
    allowed swap candidate merely because an API-prefix lookup happens to work.
    """
    allowed = [str(item).strip() for item in (configured_universe or []) if isinstance(item, str) and item.strip()]
    raw = ticker if isinstance(ticker, str) else None
    normalized = raw.strip() if raw and raw.strip() else None
    allowed_by_fold = {item.casefold(): item for item in allowed}
    matched = allowed_by_fold.get(normalized.casefold()) if normalized else None
    return {
        "decision": "ALLOW" if matched else "VETO",
        "reason": "" if matched else "OUTSIDE_CONFIGURED_UNIVERSE",
        "ticker": raw,
        "normalized_ticker": matched if matched else normalized,
        "configured_universe": allowed,
        "config_source": "config.json:universe",
    }


def swap_candidate_admission(candidate: dict, configured_universe) -> dict:
    """Apply the canonical root-universe gate before swap scoring or mutation."""
    admission = universe_admission(candidate.get("ticker") if isinstance(candidate, dict) else None, configured_universe)
    return {**admission, "pipeline_stage": "swap_candidate", "strategy": candidate.get("strategy") if isinstance(candidate, dict) else None}


def veto_invalid_pending_swap(slot: dict, slot_id: str, configured_universe) -> dict | None:
    """Cancel only invalid replacement metadata; never touch broker position state."""
    pending = slot.get("swap_pending")
    if not isinstance(pending, dict):
        return None
    admission = universe_admission(pending.get("target_ticker"), configured_universe)
    if admission["decision"] == "ALLOW":
        return None
    original = dict(pending)
    slot.pop("swap_pending", None)
    event = {
        "pipeline_stage": "swap_pending_retry",
        "decision": "VETO",
        "reason": admission["reason"],
        "slot_id": slot_id,
        "instrument": admission["ticker"],
        "normalized_instrument": admission["normalized_ticker"],
        "configured_universe": admission["configured_universe"],
        "config_source": admission["config_source"],
        "original_pending": original,
    }
    log("UNIVERSE_GATE %s" % json.dumps(event, ensure_ascii=False, sort_keys=True))
    return event


def eject_rules(slot: dict, cfg, now: float, conn=None) -> str | None:
    """Детерминированные правила выкидывания. Единая логика через RiskManager."""
    from .risk import eject_check
    return eject_check(slot, cfg, now, conn=conn)


def main():
    lock_path = STATE_DIR / ".supervisor.lock"
    lock_path.parent.mkdir(exist_ok=True)
    lock_fh = lock_path.open("a+")
    try:
        fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("another tick in progress", file=sys.stderr)
        return

    try:
        cfg = cfg_mod.load_config()
        rm = RiskManager(cfg)
        portfolio = registry.load_portfolio()
        db = an.connect()
        now = time.time()

        # --- CANONICAL: load registry as the single source of truth ---
        adaptive_registry = AdaptiveRegistry.load()
        # Legacy views for consumers that haven't migrated yet
        waitlist, signal_pool = build_legacy_views(adaptive_registry)

        if portfolio.get("halted"):
            log("ПОРТФЕЛЬ ОСТАНОВЛЕН (%s) — новые входы запрещены, но тик продолжится для выхода позиций" % portfolio.get("halt_reason"))

        # 1) Регим
        snap_path = STATE_DIR / "regime_snapshot.json"
        # Load existing snapshot so `snap` is always defined (fallback to neutral)
        snap = json.loads(snap_path.read_text()) if snap_path.exists() else {"bias": "neutral", "trend_cnt": 0}
        if not regime_fresh(snap_path):
            snap = scan_universe(DATA_DIR, cfg.universe)
            STATE_DIR.mkdir(exist_ok=True)
            snap_path.write_text(json.dumps(snap, indent=2, ensure_ascii=False))
            with open(STATE_DIR / "regime_log.jsonl", "a") as f:
                f.write(json.dumps(snap, ensure_ascii=False) + "\n")
            log("REGIME: bias=%s, трендовых %d/%d" % (snap["bias"], snap["trend_cnt"], len(cfg.universe)))
            if snap["bias"] == "meanrev":
                log("  режим флэт по юниверсу — приоритет mean-reversion кандидатам")

            # --- Mid-position regime check: log warnings for mismatched positions ---
            regime_bias = snap.get("bias", "neutral")
            for sid, sl in portfolio.get("slots", {}).items():
                if not sl.get("open_position"):
                    continue
                strat_lower = sl.get("strategy", "").lower()
                is_trend_strategy = any(tag in strat_lower for tag in ("trend", "breakout", "momentum", "supertrend", "adx"))
                is_meanrev_strategy = any(tag in strat_lower for tag in ("meanrev", "reversion", "vwap", "bollinger"))
                mismatch = False
                if regime_bias == "meanrev" and is_trend_strategy:
                    mismatch = True
                elif regime_bias == "trend" and is_meanrev_strategy:
                    mismatch = True
                if mismatch:
                    log("REGIME WARN: %s/%s — позиция %s, но рынок %s (режим может смениться)" % (
                        sl["ticker"], sl["strategy"], sl["open_position"]["direction"], regime_bias))

        # 2) Тик движка
        mtime_before = registry.portfolio_mtime()
        eng = Engine()
        events = eng.tick()
        mtime_after = registry.portfolio_mtime()
        engine_didnt_save = mtime_after <= mtime_before and events
        if engine_didnt_save:
            log("WARNING: engine.tick() генерировал события, но portfolio.json не обновлён (mtime不变) — возможна потеря состояния")
        portfolio = registry.load_portfolio()
        broker_positions_after = eng.fetch_broker_positions()

        with Client(eng.token) as c:
            equity = eng._equity(c, broker_positions_after)
        if portfolio.get("peak_equity", 0) <= 0:
            portfolio["peak_equity"] = equity
        else:
            portfolio["peak_equity"] = max(portfolio.get("peak_equity", 0), equity)
        drawdown = portfolio["peak_equity"] - equity
        if drawdown >= cfg.portfolio_stop_rub:
            portfolio["halted"] = True
            portfolio["halt_reason"] = "portfolio_stop"
            log("PORTFOLIO STOP: dd=%.0f >= %.0f" % (drawdown, cfg.portfolio_stop_rub))
            for slot_id in list(portfolio["slots"].keys()):
                slot = portfolio["slots"][slot_id]
                pos = slot.get("open_position")
                if not pos:
                    continue
                slot["liquidation_pending"] = True
                slot["liquidation_reason"] = "portfolio_stop"
                slot["close_action"] = "force_close"
                slot["close_request_ts"] = now
                if cfg.mode == "live" and not cfg.paper_first:
                    try:
                        eng_liq = Engine()
                        closed = eng_liq.force_close_slot(slot_id, slot)
                    except Exception as exc:
                        closed = False
                        log("KILL-SWITCH live close error %s: %s" % (slot_id, exc))
                    if closed:
                        slot["open_position"] = None
                        registry.remove_slot(portfolio, slot_id, "portfolio_stop_liquidation")
                        db.execute("INSERT INTO slot_events (ts, slot_id, event, detail) VALUES (?,?,?,?)",
                                   (datetime.now(timezone.utc).isoformat(), slot_id, "liquidated", "portfolio_stop"))
                        db.commit()
                        log("KILL-SWITCH LIQUIDATED live %s %s/%s" % (slot_id, slot.get("ticker"), slot.get("strategy")))
                else:
                    slot["open_position"] = None
                    slot["liquidated_at"] = now
                    registry.remove_slot(portfolio, slot_id, "portfolio_stop_liquidation")
                    db.execute("INSERT INTO slot_events (ts, slot_id, event, detail) VALUES (?,?,?,?)",
                               (datetime.now(timezone.utc).isoformat(), slot_id, "liquidated", "portfolio_stop_paper"))
                    db.commit()
                    log("KILL-SWITCH LIQUIDATED paper %s %s/%s" % (slot_id, slot.get("ticker"), slot.get("strategy")))

        # 3) Риск-ревизия слотов
        ejected = []
        EJECT_DEFER_TIMEOUT_S = 48 * 3600  # 48 часов — если позиция застряла, выбрасываем принудительно
        for slot_id in list(portfolio["slots"].keys()):
            slot = portfolio["slots"][slot_id]
            slot["slot_id"] = slot_id
            # Universe gate runs before any pending-swap retry / Engine construction.
            # Invalid metadata is locally vetoed only; position state stays intact.
            pending_veto = veto_invalid_pending_swap(slot, slot_id, cfg.universe)
            if pending_veto is not None:
                continue
            # retry pending auto-swap close first
            if slot.get("swap_pending") and slot.get("open_position"):
                pending = slot["swap_pending"]
                if now - float(pending.get("requested_ts", now)) > 60:
                    log("AUTO-SWAP RETRY %s: retry close for %s/%s" % (
                        slot_id, pending.get("target_ticker"), pending.get("target_strategy")))
                    try:
                        eng = Engine()
                        closed = eng.force_close_slot(slot_id, slot)
                    except Exception as e:
                        closed = False
                        log("AUTO-SWAP RETRY ERROR %s: %s" % (slot_id, e))
                    if closed:
                        slot.pop("swap_pending", None)
                        slot["open_position"] = None
                        removed = registry.remove_slot(portfolio, slot_id, "auto_swap_out_retry")
                        if removed is not None:
                            ejected.append((slot_id, "auto_swap_out_retry"))
                            log("AUTO-SWAP RETRY OK %s" % slot_id)
                            continue
            reason = eject_rules(slot, cfg, now, conn=db)
            if reason:
                removed = registry.remove_slot(portfolio, slot_id, reason)
                if removed is None and slot.get("open_position"):
                    # --- Eject timeout: если.defer слишком долгий — принудительный eject ---
                    deferred_ts = slot.get("eject_deferred_ts", 0)
                    if deferred_ts == 0:
                        slot["eject_deferred_ts"] = now
                        log("EJECT DEFERRED %s: открытая позиция, ждём закрытия (таймер %dч)" % (
                            slot_id, EJECT_DEFER_TIMEOUT_S // 3600))
                    elif now - deferred_ts > EJECT_DEFER_TIMEOUT_S:
                        # Принудительный eject: закрываем позицию через брокера
                        log("EJECT TIMEOUT %s: позиция застряла >%dч, принудительное закрытие" % (
                            slot_id, EJECT_DEFER_TIMEOUT_S // 3600))
                        try:
                            eng = Engine()
                            closed = eng.force_close_slot(slot_id, slot)
                        except Exception as e:
                            closed = False
                            log("EJECT TIMEOUT ERROR %s: %s" % (slot_id, e))
                        if closed:
                            slot["open_position"] = None
                            removed = registry.remove_slot(portfolio, slot_id, reason + "_timeout")
                            if removed is not None:
                                ejected.append((slot_id, reason + "_timeout"))
                        else:
                            log("EJECT TIMEOUT: close not confirmed for %s — keep deferred state" % slot_id)
                    continue
                else:
                    # Успешный eject — убираем таймер
                    slot.pop("eject_deferred_ts", None)
                ejected.append((slot_id, reason))
                db.execute("INSERT INTO slot_events (ts, slot_id, event, detail) VALUES (?,?,?,?)",
                           (datetime.now(timezone.utc).isoformat(), slot_id, "ejected", reason))
                db.commit()
                log("EJECT %s: %s" % (slot_id, reason))

        # 4) Adaptive Registry: ingest → refresh → derive legacy views (single source of truth).
        #    Legacy signal pool manipulation (fill, conflict resolution, rotation,
        #    expired revival) was removed — the adaptive registry handles all
        #    classification and status transitions.
        pool_max = cfg.risk.signal_pool_max
        # Ingest any unregistered candidates from legacy views into the registry
        generated_payload = []
        for cid, cand in waitlist.get("candidates", {}).items():
            if adaptive_registry.get(cid) is None:
                generated_payload.append({
                    "strategy_id": cid,
                    "ticker": cand.get("ticker"),
                    "strategy": cand.get("strategy"),
                    "params": cand.get("params", {}),
                    "metrics": cand.get("metrics", {}),
                    "portfolio_context": {"regime_ok": True, "stale_ok": True, "contract_risk_ok": True, "go_rub": cand.get("go_rub", 0.0)},
                    "quality_gate": {"ttl_days": cand.get("ttl_days", 7)},
                    "status": cand.get("status", "waitlist"),
                })
        for pid, sig in signal_pool.get("strategies", {}).items():
            if adaptive_registry.get(pid) is None:
                generated_payload.append({
                    "strategy_id": pid,
                    "ticker": sig.get("ticker"),
                    "strategy": sig.get("strategy"),
                    "params": sig.get("params", {}),
                    "metrics": sig.get("metrics", {}),
                    "portfolio_context": {"regime_ok": True, "stale_ok": True, "contract_risk_ok": True, "go_rub": sig.get("go_rub", 0.0)},
                    "quality_gate": {"signals_generated": sig.get("signals_generated", 0)},
                    "status": sig.get("status", "active_signal_pool"),
                })
        if generated_payload:
            ingest_generated_strategies(adaptive_registry, generated_payload, portfolio, regime_bias=snap.get("bias", "neutral"), batch_id="live-sync")
        adaptive_result = refresh_active_watchlist(adaptive_registry, portfolio, limit=pool_max, regime_bias=snap.get("bias", "neutral"))
        # Refresh legacy views from the authoritative registry
        waitlist, signal_pool = build_legacy_views(adaptive_registry)
        if adaptive_result["snapshot"].promotions or adaptive_result["snapshot"].replacements:
            log("ADAPTIVE WATCHLIST: promotions=%d replacements=%d registry=%d" % (
                len(adaptive_result["snapshot"].promotions), len(adaptive_result["snapshot"].replacements), adaptive_result["snapshot"].registry_size))

        if not portfolio.get("halted"):
            free_slots = cfg.risk.max_slots - len(portfolio["slots"])
            active_tickers = {s["ticker"] for s in portfolio["slots"].values() if s.get("open_position")}
            promoted_keys = {(s['ticker'], s['strategy']) for s in portfolio['slots'].values()}

            # --- Live review: compare current live slots against best 60d candidates ---
            # verdicts: keep / review / swap_ready
            live_review_rows = []
            if portfolio["slots"]:
                ps = PortfolioState(
                    balance_rub=equity,
                    go_budget_rub=cfg.go_budget_rub,
                    used_go_rub=registry.used_go(portfolio),
                    max_slots=cfg.risk.max_slots,
                    open_tickers={s["ticker"] for s in portfolio["slots"].values() if s.get("open_position")},
                    regime_bias=snap.get("bias", "neutral"),
                    active_slots=len(portfolio["slots"]),
                )
                best_candidate = None
                best_candidate_score = None
                for pid, entry in signal_pool.get("strategies", {}).items():
                    if entry.get("status") not in {"active", "active_watchlist", "active_signal_pool"}:
                        continue
                    admission = swap_candidate_admission(entry, cfg.universe)
                    if admission["decision"] != "ALLOW":
                        log("UNIVERSE_GATE %s" % json.dumps({
                            **admission,
                            "decision": "VETO",
                            "reason": admission["reason"],
                            "candidate_id": pid,
                            "pipeline_stage": "swap_ready_selection",
                        }, ensure_ascii=False, sort_keys=True))
                        continue
                    if entry.get("ticker") in active_tickers:
                        continue
                    cand_score = portfolio_aware_score(entry, ps)
                    if best_candidate is None or cand_score > best_candidate_score:
                        best_candidate = (pid, entry)
                        best_candidate_score = cand_score

                for slot_id, slot in portfolio["slots"].items():
                    if not slot.get("open_position"):
                        continue
                    slot_score = portfolio_aware_score(slot, ps)
                    if best_candidate is None:
                        verdict = "keep"
                        delta = 0.0
                    else:
                        delta = float(best_candidate_score - slot_score)
                        if delta > 250.0:
                            verdict = "swap_ready"
                        elif delta > 80.0:
                            verdict = "review"
                        else:
                            verdict = "keep"
                    live_review_rows.append((slot_id, slot["ticker"], slot["strategy"], slot_score, verdict, delta))
                for slot_id, ticker, strategy, slot_score, verdict, delta in live_review_rows:
                    log("LIVE REVIEW %s/%s [%s]: score=%.0f delta=%.0f" % (ticker, strategy, verdict, slot_score, delta))

            # --- Regime gate pre-filter: reject candidates that don't match market regime ---
            raw_candidates = registry.signal_pool_for_promotion(
                signal_pool, active_tickers=active_tickers, max_age_minutes=cfg.risk.signal_max_age_minutes)
            raw_candidates = [(pid, entry) for pid, entry in raw_candidates if (entry['ticker'], entry['strategy']) not in promoted_keys]
            regime_filtered = []
            regime_rejected = 0
            for pid, entry in raw_candidates:
                cand_dict = {"ticker": entry["ticker"],
                             "direction": entry.get("metrics", {}).get("direction", "LONG"),
                             "contracts_requested": entry.get("contracts", 1)}
                gate_result = regime_admit(cand_dict, snap, {"excluded": cfg.excluded,
                                                              "max_contracts_per_entry": cfg.risk.max_contracts_per_entry})
                if gate_result["admit"]:
                    regime_filtered.append((pid, entry))
                else:
                    regime_rejected += 1
                    log("REGIME GATE: %s/%s → %s" % (entry["ticker"], entry["strategy"], gate_result["reason"]))
            if regime_rejected:
                log("REGIME GATE: %d/%d кандидатов отсечены по режиму" % (regime_rejected, len(raw_candidates)))
            promo_candidates = regime_filtered

            # --- Auto-swap: replace swap_ready live slot with best canonical candidate if safe ---
            swap_ready_rows = [row for row in live_review_rows if row[4] == "swap_ready"]
            canonical_candidates = []
            for rec in adaptive_registry.active_signal_pool(limit=pool_max):
                canonical_candidates.append((rec.strategy_id, rec.to_dict()))
            for rec in adaptive_registry.active_watchlist(limit=pool_max):
                canonical_candidates.append((rec.strategy_id, rec.to_dict()))
            if swap_ready_rows and canonical_candidates:
                log("AUTO-SWAP: canonical_candidates=%d" % len(canonical_candidates))
                swap_ps = PortfolioState(
                    balance_rub=equity,
                    go_budget_rub=cfg.go_budget_rub,
                    used_go_rub=registry.used_go(portfolio),
                    max_slots=cfg.risk.max_slots,
                    open_tickers={s["ticker"] for s in portfolio["slots"].values() if s.get("open_position")},
                    regime_bias=snap.get("bias", "neutral"),
                    active_slots=len(portfolio["slots"]),
                )
                # Universe gate precedes swap selection, score, state mutation and Engine calls.
                admitted_candidates = []
                for pid, entry in canonical_candidates:
                    admission = swap_candidate_admission(entry, cfg.universe)
                    if admission["decision"] == "ALLOW":
                        admitted_candidates.append((pid, entry))
                    else:
                        log("UNIVERSE_GATE %s" % json.dumps({
                            **admission,
                            "decision": "VETO",
                            "reason": admission["reason"],
                            "candidate_id": pid,
                        }, ensure_ascii=False, sort_keys=True))
                # best candidate globally, but never the same ticker as live slot
                for swap_row in swap_ready_rows:
                    swap_slot_id, swap_ticker, swap_strategy, swap_score, _, swap_delta = swap_row
                    if swap_slot_id not in portfolio["slots"]:
                        continue
                    candidates_same_time = [
                        (pid, entry) for pid, entry in admitted_candidates
                        if entry.get("ticker") != swap_ticker
                    ]
                    if not candidates_same_time:
                        log("AUTO-SWAP SKIP %s: no non-conflicting canonical candidates" % swap_slot_id)
                        continue
                    best_pid, best_entry = max(
                        candidates_same_time,
                        key=lambda item: portfolio_aware_score(item[1], swap_ps),
                    )
                    best_score = portfolio_aware_score(best_entry, swap_ps)
                    current_slot_score = portfolio_aware_score(portfolio["slots"][swap_slot_id], swap_ps)
                    if best_score <= current_slot_score + max(40.0, adaptive_threshold(swap_ps) * 0.08):
                        log("AUTO-SWAP SKIP %s: best candidate score %.0f not enough over current %.0f" % (
                            swap_slot_id, best_score, current_slot_score))
                        continue
                    old_slot = portfolio["slots"][swap_slot_id]
                    old_pos = old_slot.get("open_position")
                    if old_pos:
                        try:
                            eng = Engine()
                            closed = eng.force_close_slot(swap_slot_id, old_slot)
                        except Exception as e:
                            closed = False
                            log("AUTO-SWAP CLOSE ERROR %s: %s" % (swap_slot_id, e))
                        if not closed:
                            old_slot["swap_pending"] = {
                                "requested_ts": now,
                                "target_ticker": best_entry["ticker"],
                                "target_strategy": best_entry["strategy"],
                                "reason": "close_failed_pending_retry",
                            }
                            log("AUTO-SWAP PENDING %s: close failed, will retry next tick" % swap_slot_id)
                            continue
                    removed = registry.remove_slot(portfolio, swap_slot_id, "auto_swap_out")
                    if removed is None and old_pos:
                        log("AUTO-SWAP PENDING %s: waiting close confirmation" % swap_slot_id)
                        continue
                    used = registry.used_go(portfolio)
                    fallback_go = float(best_entry.get("go_rub") or best_entry.get("metrics", {}).get("go_rub") or 1500.0)
                    go = live_go(best_entry["ticker"], fallback_go)
                    if go <= 0:
                        go = fallback_go
                    existing_positions = {sid: s.get("open_position") for sid, s in portfolio["slots"].items() if s.get("open_position")}
                    real_long_go, real_short_go = registry.long_short_go(portfolio, existing_positions)
                    n_active = sum(1 for s in portfolio["slots"].values() if s.get("open_position"))
                    ok, reason = rm.approve_entry(
                        ticker=best_entry["ticker"], direction=best_entry.get("metrics", {}).get("direction", "LONG"),
                        go_rub=go, used_go_rub=used, n_active=n_active,
                        long_go=real_long_go, short_go=real_short_go, peak_equity=portfolio.get("peak_equity", 0),
                        equity=equity,
                        stop_distance_rub=best_entry.get("metrics", {}).get("stop_distance_rub"),
                        contracts=best_entry.get("contracts", 1))
                    if not ok:
                        log("AUTO-SWAP VETO %s/%s: %s" % (best_entry["ticker"], best_entry["strategy"], reason))
                        continue
                    new_slot_id = "slot_%s_%d" % (best_entry["ticker"], int(now) % 100000)
                    registry.add_slot(portfolio, new_slot_id, best_entry["ticker"], best_entry["strategy"],
                                      best_entry.get("params", {}), contracts=best_entry.get("contracts", 1),
                                      go_rub=go)
                    best_entry["status"] = "promoted"
                    best_entry["promoted_ts"] = now
                    db.execute("INSERT INTO slot_events (ts, slot_id, event, detail) VALUES (?,?,?,?)",
                               (datetime.now(timezone.utc).isoformat(), new_slot_id, "auto_swapped",
                                "replace %s/%s -> %s/%s" % (swap_ticker, swap_strategy, best_entry["ticker"], best_entry["strategy"])))
                    db.commit()
                    log("AUTO-SWAP %s -> %s/%s (ГО %.0f, delta=%.0f)" % (
                        swap_slot_id, best_entry["ticker"], best_entry["strategy"], go, swap_delta))
                    promoted_keys.add((best_entry["ticker"], best_entry["strategy"]))
                    active_tickers.add(best_entry["ticker"])
                    free_slots = cfg.risk.max_slots - len(portfolio["slots"])

            while free_slots > 0:
                if not promo_candidates:
                    if not portfolio["slots"]:
                        log("ПОРТФЕЛЬ ПУСТ: signal pool пуст — ждём кандидатов от скана/генератора")
                    break
                pool_id, pool_entry = promo_candidates[0]
                # --- Portfolio-aware scoring (replaces simple PnL comparison) ---
                if portfolio["slots"]:
                    ps = PortfolioState(
                        balance_rub=equity,
                        go_budget_rub=cfg.go_budget_rub,
                        used_go_rub=registry.used_go(portfolio),
                        max_slots=cfg.risk.max_slots,
                        open_tickers={s["ticker"] for s in portfolio["slots"].values() if s.get("open_position")},
                        regime_bias=snap.get("bias", "neutral"),
                        active_slots=len(portfolio["slots"]),
                    )
                    # Score candidate against worst active slot
                    cand_score = portfolio_aware_score(pool_entry, ps)
                    worst_slot = min(portfolio["slots"].values(),
                                     key=lambda s: portfolio_aware_score(s, ps))
                    worst_score = portfolio_aware_score(worst_slot, ps)
                    threshold = adaptive_threshold(ps)
                    improvement = max(40.0, threshold * 0.08)
                    if cand_score <= worst_score + improvement:
                        log("Замена отложена: score %.0f <= worst %.0f + %.0f (threshold %.0f)" % (
                            cand_score, worst_score, improvement, threshold))
                        break
                used = registry.used_go(portfolio)
                fallback_go = float(pool_entry.get("go_rub") or pool_entry.get("metrics", {}).get("go_rub") or 1500.0)
                go = live_go(pool_entry["ticker"], fallback_go)
                if go <= 0:
                    go = fallback_go
                existing_positions = {sid: s.get("open_position") for sid, s in portfolio["slots"].items() if s.get("open_position")}
                real_long_go, real_short_go = registry.long_short_go(portfolio, existing_positions)
                n_active = sum(1 for s in portfolio["slots"].values() if s.get("open_position"))
                ok, reason = rm.approve_entry(
                    ticker=pool_entry["ticker"], direction=pool_entry.get("metrics", {}).get("direction", "LONG"),
                    go_rub=go, used_go_rub=used, n_active=n_active,
                    long_go=real_long_go, short_go=real_short_go, peak_equity=portfolio.get("peak_equity", 0),
                    equity=equity,
                    stop_distance_rub=pool_entry.get("metrics", {}).get("stop_distance_rub"),
                    contracts=pool_entry.get("contracts", 1))
                if not ok:
                    log("PROMOTE VETO %s/%s: %s" % (pool_entry["ticker"], pool_entry["strategy"], reason))
                    pool_entry["status"] = "vetoed"
                    continue
                slot_id = "slot_%s_%d" % (pool_entry["ticker"], int(now) % 100000)
                registry.add_slot(portfolio, slot_id, pool_entry["ticker"], pool_entry["strategy"],
                                  pool_entry.get("params", {}), contracts=pool_entry.get("contracts", 1),
                                  go_rub=go)
                pool_entry["status"] = "promoted"
                pool_entry["promoted_ts"] = now
                db.execute("INSERT INTO slot_events (ts, slot_id, event, detail) VALUES (?,?,?,?)",
                           (datetime.now(timezone.utc).isoformat(), slot_id, "promoted",
                            "из signal pool: %s/%s" % (pool_entry["ticker"], pool_entry["strategy"])))
                db.commit()
                log("PROMOTE %s -> %s/%s (ГО %.0f, из signal pool)" % (
                    slot_id, pool_entry["ticker"], pool_entry["strategy"], go))
                active_tickers.add(pool_entry["ticker"])
                promoted_keys.add((pool_entry["ticker"], pool_entry["strategy"]))
                # ensure next promotion in the same tick cannot overwrite slot_id
                now += 1
                free_slots -= 1

        # --- Expired candidates: operate DIRECTLY on registry, not derived dict ---
        now_ts = now
        max_retests = getattr(cfg.risk, "waitlist_max_retests", 2)
        for record in list(adaptive_registry.records()):
            if record.status not in {"registry/candidate", "waitlist"}:
                continue
            ttl_days = int(record.quality_gate.get("ttl_days", 7))
            retested_ts = record.updated_ts
            if now_ts - retested_ts <= ttl_days * 86400:
                continue
            # Candidate expired
            retests = int(record.quality_gate.get("retests", 0))
            if retests >= max_retests:
                adaptive_registry.mark_rejected(record.strategy_id, reason="ttl_expired_%d_retests" % retests)
                log("WAITLIST: %s/%s выброшен (%d retest'ов)" % (record.ticker, record.strategy, retests))
            else:
                record.quality_gate["retests"] = retests + 1
                record.quality_gate["retested_ts"] = now_ts
                log("WAITLIST: %s/%s -> retest #%d" % (record.ticker, record.strategy, retests + 1))

        # --- Prune waitlist: reject excess candidates directly in registry ---
        waitlist_records = sorted(
            [r for r in adaptive_registry.records()
             if r.status in {"registry/candidate", "waitlist"}],
            key=lambda r: float(r.active_rank or r.metrics.get("rank_score", 0) or 0),
            reverse=True,
        )
        if len(waitlist_records) > cfg.risk.waitlist_max:
            for record in waitlist_records[cfg.risk.waitlist_max:]:
                adaptive_registry.mark_rejected(record.strategy_id, reason="pruned_over_limit")

        hints = an.generator_hints(db)
        if live_review_rows:
            hints["live_review"] = [
                {
                    "slot_id": slot_id,
                    "ticker": ticker,
                    "strategy": strategy,
                    "score": round(score, 2),
                    "verdict": verdict,
                    "delta_vs_best_candidate": round(delta, 2),
                }
                for slot_id, ticker, strategy, score, verdict, delta in live_review_rows
            ]
        if hints["by_direction_regime"] or hints.get("live_review"):
            STATE_DIR.mkdir(exist_ok=True)
            (STATE_DIR / "generator_feedback.json").write_text(json.dumps(hints, indent=2, ensure_ascii=False))
        if live_review_rows:
            log("LIVE REVIEW SUMMARY: %s" % json.dumps(hints["live_review"], ensure_ascii=False))

        for ev in events:
            log(ev)

        if not engine_didnt_save:
            registry.save_portfolio(portfolio)
        else:
            log("SKIP: portfolio save — engine не сохранил, избегаем перетирания")

        # --- CANONICAL SAVE: registry first, then derive legacy views ---
        adaptive_registry.save()
        adaptive_registry.export_legacy_state_files()  # derived legacy files
        adaptive_registry.prune_history()

        # Refresh in-memory legacy views after save
        waitlist, signal_pool = build_legacy_views(adaptive_registry)
        pool_active_final = sum(1 for p in signal_pool["strategies"].values() if p.get("status") in {"active", "active_signal_pool"})
        watchlist_final = sum(1 for p in waitlist["candidates"].values() if p.get("status") in {"waitlist", "registry/candidate"})
        log("ТИК ЗАВЕРШЁН: слотов=%d, signal_pool=%d, watchlist=%d, событий=%d, registry=%d" % (
            len(portfolio["slots"]), pool_active_final, watchlist_final,
            len(events), len(adaptive_registry.records())))
    finally:
        try:
            fcntl.flock(lock_fh, fcntl.LOCK_UN)
        finally:
            lock_fh.close()


if __name__ == "__main__":
    main()
