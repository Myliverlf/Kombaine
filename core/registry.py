"""Registry: активные слоты портфеля + Waitlist кандидатов + Signal Pool.

state/portfolio.json  — активные слоты
state/waitlist.json   — лист ожидания (TTL, fresh-retest) [DERIVED VIEW from registry]
state/signal_pool.json — пул сигнальных стратегий (до 10) [DERIVED VIEW from registry]

MIGRATION NOTE: waitlist.json and signal_pool.json are now derived views exported
from strategy_registry.json (the canonical source of truth).  Do NOT write to these
files directly — use StrategyRegistry instead.  These functions remain for backward
compatibility with legacy consumers that have not yet migrated.
"""
import json
import os
import time
from pathlib import Path
from typing import Optional

STATE_DIR = Path(__file__).resolve().parent.parent / "state"
PORTFOLIO = STATE_DIR / "portfolio.json"
WAITLIST = STATE_DIR / "waitlist.json"
SIGNAL_POOL = STATE_DIR / "signal_pool.json"


def _load(p: Path, default):
    if not p.exists():
        bak = p.with_suffix(p.suffix + ".bak")
        if bak.exists():
            try:
                return json.loads(bak.read_text())
            except json.JSONDecodeError:
                print("CRITICAL registry._load: backup JSON is broken for %s" % bak, flush=True)
                return default
        return default

    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError:
        ts = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
        corrupt = p.with_name(p.name + ".corrupt-%s" % ts)
        try:
            os.replace(p, corrupt)
        except OSError as exc:
            print("CRITICAL registry._load: failed to quarantine %s: %s" % (p, exc), flush=True)
        bak = p.with_suffix(p.suffix + ".bak")
        if bak.exists():
            try:
                print("CRITICAL registry._load: broken JSON in %s, fallback to %s" % (p, bak), flush=True)
                return json.loads(bak.read_text())
            except json.JSONDecodeError:
                print("CRITICAL registry._load: fallback backup is broken for %s" % bak, flush=True)
        else:
            print("CRITICAL registry._load: broken JSON in %s, fallback to default" % p, flush=True)
        return default


def _save(p: Path, data):
    STATE_DIR.mkdir(exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    os.replace(tmp, p)


# ---------- Portfolio ----------
def load_portfolio() -> dict:
    return _load(PORTFOLIO, {"slots": {}, "peak_equity": 0.0, "halted": False, "halt_reason": None})


def save_portfolio(p: dict):
    _save(PORTFOLIO, p)


def portfolio_mtime() -> float:
    """Время последней записи portfolio.json (0 если файла нет)."""
    if PORTFOLIO.exists():
        return PORTFOLIO.stat().st_mtime
    return 0.0



def reconcile_create_pending_trades(portfolio, broker_positions, db_conn):
    """После reconcile: создаём pending trade record для adopt-ённых позиций.

    Если adopt позиция имеет trade_id=None — создаём запись в analytics.
    Возвращает список созданных trade_id.
    """
    created = []
    for slot_id, slot in portfolio.get("slots", {}).items():
        pos = slot.get("open_position")
        if pos is None:
            continue
        if pos.get("trade_id") is not None:
            continue  # уже есть trade record
        if not pos.get("reconciled"):
            continue  # не adopt-ённая позиция

        ticker = slot["ticker"]
        bp = broker_positions.get(ticker)
        if not bp:
            continue

        # Создаём pending trade record
        direction = pos["direction"]
        qty = pos["qty"]
        entry_price = pos["entry_price"]
        ts = pos.get("entry_ts")
        import datetime as _dt
        ts_iso = _dt.datetime.fromtimestamp(ts, tz=_dt.timezone.utc).isoformat() if ts else None

        cur = db_conn.execute(
            "INSERT INTO trades (ts_open, slot_id, ticker, strategy, direction, "
            "contracts, entry_price, regime, hour, status) "
            "VALUES (?,?,?,?,?,?,?,?,?, 'open')",
            (ts_iso, slot_id, ticker, slot["strategy"], direction,
             qty, entry_price, "reconcile_adopt",
             _dt.datetime.fromtimestamp(ts).hour if ts else None))
        db_conn.commit()
        trade_id = cur.lastrowid
        pos["trade_id"] = trade_id
        created.append(trade_id)
    return created

def reconcile_broker_positions(portfolio: dict, broker_positions: dict) -> list:
    """Сверка состояния портфеля с реальными позициями брокера.

    broker_positions: {ticker: {"qty": int, "direction": str, "figi": str}}
      qty > 0 → long, qty < 0 → short, qty == 0 → нет позиции.

    Возвращает список событий reconciliation (для логирования).
    Если брокер имеет позицию, которой нет в portfolio → записываем open_position.
    Если portfolio имеет open_position, но брокер его не имеет → закрываем.
    """
    import time as _time
    events = []
    for slot_id, slot in portfolio.get("slots", {}).items():
        ticker = slot["ticker"]
        bp = broker_positions.get(ticker)
        broker_qty = bp["qty"] if bp else 0
        broker_dir = bp.get("direction", "") if bp else ""
        slot_pos = slot.get("open_position")

        if broker_qty != 0 and slot_pos is None:
            # adopt ТОЛЬКО в первый слот тикера: если другой слот того же тикера
            # уже трекает позицию — не дублируем (иначе одна позиция расходится
            # по нескольким слотам и qty задваивается)
            owned_by_other = any(
                other_id != slot_id and other["ticker"] == ticker and other.get("open_position")
                for other_id, other in portfolio["slots"].items())
            if owned_by_other:
                continue
            # Брокер имеет позицию, portfolio не знает — adopt
            direction = "LONG" if broker_qty > 0 else "SHORT"
            # entry_atr: пробуем вычислить из broker данных (avg_price / 100 как эвристика,
            # или 0 — движок обновит при первом тике)
            entry_atr = bp.get("entry_atr", 0.0)
            if entry_atr <= 0 and bp.get("avg_price", 0) > 0:
                # Эвристика: ATR ≈ 0.6% от цены (типичный ATR для фьючерсов)
                entry_atr = bp["avg_price"] * 0.006
            pos = {
                "direction": direction,
                "qty": abs(broker_qty),
                "entry_price": bp.get("avg_price", 0.0),
                "entry_atr": entry_atr,
                "entry_ts": _time.time(),
                "entry_bar": 0,
                "trade_id": None,  # будет заполнен ниже через analytics
                "reconciled": True,
            }
            slot["open_position"] = pos
            events.append(
                "RECONCILE %s/%s: adopt broker %s x%d (portfolio не знал, entry_atr=%.2f)" % (
                    ticker, slot["strategy"], direction, abs(broker_qty), entry_atr))

        elif broker_qty == 0 and slot_pos is not None:
            # Portfolio думает что позиция есть, но брокер не имеет — закрываем
            slot["open_position"] = None
            events.append(
                "RECONCILE %s/%s: позиция %s x%d не найдена на брокере — обнулено" % (
                    ticker, slot["strategy"],
                    slot_pos.get("direction", "?"), slot_pos.get("qty", 0)))

        elif broker_qty != 0 and slot_pos is not None:
            # Один лот не может принадлежать двум слотам: владельцем считается
            # первый слот тикера с open_position (порядок обхода), остальные —
            # дубли, обнуляем, чтобы qty не задваивался
            owner = next(
                (oid for oid, other in portfolio["slots"].items()
                 if other["ticker"] == ticker and other.get("open_position")),
                None)
            if owner is not None and owner != slot_id:
                slot["open_position"] = None
                events.append(
                    "RECONCILE %s/%s: дубль позиции — владелец %s, слот %s обнулён" % (
                        ticker, slot["strategy"], owner, slot_id))
                continue
            # Direction mismatch: portfolio LONG, broker SHORT (или наоборот)
            broker_direction = "LONG" if broker_qty > 0 else "SHORT"
            portfolio_direction = slot_pos.get("direction", "")
            if portfolio_direction and portfolio_direction != broker_direction:
                events.append(
                    "RECONCILE %s/%s: DIRECTION MISMATCH portfolio=%s broker=%s — "
                    "корректирую direction на %s" % (
                        ticker, slot["strategy"], portfolio_direction,
                        broker_direction, broker_direction))
                slot_pos["direction"] = broker_direction

            # Проверяем что количество совпадает
            old_qty = slot_pos.get("qty", 0)
            if abs(broker_qty) != old_qty:
                slot_pos["qty"] = abs(broker_qty)
                events.append(
                    "RECONCILE %s/%s: qty скорректирован %d → %d" % (
                        ticker, slot["strategy"], old_qty, abs(broker_qty)))

    return events


def add_slot(portfolio: dict, slot_id: str, ticker: str, strategy: str, params: dict,
             contracts: int, go_rub: float) -> dict:
    slot = {
        "ticker": ticker,
        "strategy": strategy,
        "params": params,
        "contracts": contracts,
        "go_rub": go_rub,
        "promoted_ts": time.time(),
        "open_position": None,
        "n_trades": 0,
        "pnl_rub": 0.0,
        "peak_pnl_rub": 0.0,
        "stop_streak": 0,
        "last_signal_ts": time.time(),
    }
    portfolio["slots"][slot_id] = slot
    return slot


def remove_slot(portfolio: dict, slot_id: str, reason: str) -> Optional[dict]:
    slot = portfolio["slots"].get(slot_id)
    if slot and slot.get("open_position"):
        slot["eject_pending"] = reason
        return None
    slot = portfolio["slots"].pop(slot_id, None)
    if slot:
        slot["ejected_ts"] = time.time()
        slot["eject_reason"] = reason
    return slot


def used_go(portfolio: dict) -> float:
    total = 0.0
    for s in portfolio["slots"].values():
        if s.get("open_position"):
            total += float(s.get("go_rub", 0.0))
    return total


def long_short_go(portfolio: dict, positions: dict) -> tuple:
    """(long_go, short_go) по текущим открытым позициям."""
    long_go = short_go = 0.0
    for sid, s in portfolio["slots"].items():
        pos = positions.get(sid)
        if pos:
            if pos["direction"] == "LONG":
                long_go += s["go_rub"]
            else:
                short_go += s["go_rub"]
    return long_go, short_go


# ---------- Waitlist (DEPRECATED — canonical source is strategy_registry.json) ----------
# These functions exist for backward compatibility with legacy consumers.
# New code should use StrategyRegistry from core/strategy_registry.py instead.
# Legacy files are DERIVED VIEWS exported from the registry via export_legacy_state_files().
def load_waitlist() -> dict:
    return _load(WAITLIST, {"candidates": {}})


def save_waitlist(w: dict):
    _save(WAITLIST, w)


def add_candidate(waitlist: dict, cand_id: str, ticker: str, strategy: str, params: dict,
                  metrics: dict, rank_score: float, ttl_days: int = 7) -> dict:
    cand = {
        "ticker": ticker,
        "strategy": strategy,
        "params": params,
        "metrics": metrics,          # {pnl, sharpe, win_rate, pf, dd, trades, trades_per_day}
        "rank_score": rank_score,
        "added_ts": time.time(),
        "retested_ts": time.time(),
        "ttl_days": ttl_days,
        "retests": 0,
    }
    waitlist["candidates"][cand_id] = cand
    return cand


def expired_candidates(waitlist: dict, now: float = None) -> list:
    now = now or time.time()
    out = []
    for cid, c in waitlist["candidates"].items():
        if now - c["retested_ts"] > c["ttl_days"] * 86400:
            out.append(cid)
    return out


def best_candidate(waitlist: dict, ticker: str = None) -> Optional[tuple]:
    """Лучший кандидат по rank_score (опционально фильтруя тикер)."""
    best = None
    for cid, c in waitlist["candidates"].items():
        if ticker and c["ticker"] != ticker:
            continue
        if best is None or c["rank_score"] > best[1]["rank_score"]:
            best = (cid, c)
    return best


def prune_waitlist(waitlist: dict, max_size: int):
    """Оставляем max_size лучших по rank_score."""
    cands = sorted(waitlist["candidates"].items(), key=lambda kv: kv[1]["rank_score"], reverse=True)
    waitlist["candidates"] = dict(cands[:max_size])


# ---------- Signal Pool (DEPRECATED — canonical source is strategy_registry.json) ----------
# These functions exist for backward compatibility with legacy consumers.
# New code should use StrategyRegistry from core/strategy_registry.py instead.
# Legacy files are DERIVED VIEWS exported from the registry via export_legacy_state_files().
def load_signal_pool() -> dict:
    """Загрузка пула сигнальных стратегий (до signal_pool_max)."""
    return _load(SIGNAL_POOL, {"strategies": {}, "last_rotation_ts": 0.0})


def save_signal_pool(pool: dict):
    """Сохранение пула сигнальных стратегий."""
    _save(SIGNAL_POOL, pool)


def add_to_signal_pool(pool: dict, pool_id: str, ticker: str, strategy: str,
                       params: dict, metrics: dict, rank_score: float,
                       go_rub: float = 2000.0, signal_ts: float = None) -> dict:
    """Добавление стратегии в сигнальный пул."""
    now = time.time()
    entry = {
        "ticker": ticker,
        "strategy": strategy,
        "params": params,
        "metrics": metrics,
        "rank_score": rank_score,
        "go_rub": go_rub,
        "added_ts": now,
        "last_signal_ts": signal_ts or now,
        "signals_generated": 0,
        "status": "active",  # active | stale | promoted | rotated_out
    }
    is_new = pool["strategies"].get(pool_id) is None
    pool["strategies"][pool_id] = entry
    entry["_is_new"] = is_new
    return entry


def remove_from_signal_pool(pool: dict, pool_id: str, reason: str = "removed"):
    """Удаление стратегии из сигнального пула."""
    entry = pool["strategies"].pop(pool_id, None)
    if entry:
        entry["removed_ts"] = time.time()
        entry["remove_reason"] = reason
    return entry


def best_signal(pool: dict, ticker: str = None, exclude_tickers: set = None) -> Optional[tuple]:
    """Лучший сигнал из пула по rank_score.

    Если ticker задан — фильтруем по тикеру.
    Если exclude_tickers задан — исключаем эти тикеры (для конфликтов с активными слотами).
    Возвращает (pool_id, entry) или None.
    """
    exclude_tickers = exclude_tickers or set()
    best = None
    for pid, p in pool["strategies"].items():
        if p.get("status") != "active":
            continue
        if ticker and p["ticker"] != ticker:
            continue
        if p["ticker"] in exclude_tickers:
            continue
        if best is None or p["rank_score"] > best[1]["rank_score"]:
            best = (pid, p)
    return best


def signal_pool_for_promotion(pool: dict, active_tickers: set = None,
                              max_go_rub: float = 0.0, max_age_minutes: int = 60) -> list:
    """Кандидаты на promotion из сигнального пула.

    Возвращает отсортированный по rank_score список (pool_id, entry).
    Фильтрует: status=active, нет конфликта с активными тикерами, ГО влезает,
    и сигнал не протух по возрасту.
    """
    active_tickers = active_tickers or set()
    now = time.time()
    max_age_s = max_age_minutes * 60
    candidates = []
    for pid, p in pool["strategies"].items():
        if p.get("status") not in {"active", "active_watchlist", "active_signal_pool"}:
            continue
        # сигнал протух — не используем
        sig_age = now - p.get("last_signal_ts", p.get("added_ts", now))
        if sig_age > max_age_s:
            continue
        # Жёсткий ticker-level guard: в live портфеле одна бумага = один слот.
        # Иначе несколько похожих стратегий по одному инструменту превращаются
        # в одну и ту же ставку с разными названиями.
        if p["ticker"] in active_tickers:
            continue
        if max_go_rub > 0 and p.get("go_rub", 0) > max_go_rub:
            continue
        candidates.append((pid, p))
    # сортировка по rank_score descending
    candidates.sort(key=lambda x: x[1]["rank_score"], reverse=True)
    return candidates


def stale_signals(pool: dict, now: float = None, max_age_days: int = 5) -> list:
    """Стратегии в пуле, которые не генерировали сигналы больше max_age_days."""
    now = now or time.time()
    out = []
    for pid, p in pool["strategies"].items():
        if p.get("status") != "active":
            continue
        if now - p.get("last_signal_ts", p["added_ts"]) > max_age_days * 86400:
            out.append(pid)
    return out


def rotate_signal_pool(pool: dict, max_size: int, now: float = None) -> int:
    """Авто-ротация: убираем stale, resolved_conflict, переполнение.

    Возвращает количество удалённых.
    """
    now = now or time.time()
    removed = 0

    # 1) Удаляем stale
    for pid in stale_signals(pool, now):
        remove_from_signal_pool(pool, pid, reason="stale_signal")
        removed += 1

    # 2) Удаляем resolved_conflict (они не ревайвятся)
    MAX_RESOLVED_IN_POOL = 3
    resolved = [(pid, p) for pid, p in pool["strategies"].items()
                if p.get("status") == "resolved_conflict"]
    if len(resolved) > MAX_RESOLVED_IN_POOL:
        resolved.sort(key=lambda x: x[1].get("added_ts", 0), reverse=True)
        for pid, _ in resolved[MAX_RESOLVED_IN_POOL:]:
            remove_from_signal_pool(pool, pid, reason="resolved_conflict_pruned")
            removed += 1

    # 3) Ревайвим только expired и только если тикер ещё не покрыт active-стратегией
    active_tickers = {p["ticker"] for p in pool["strategies"].values() if p.get("status") == "active"}
    for pid, p in list(pool["strategies"].items()):
        if p.get("status") != "expired":
            continue
        if p["ticker"] in active_tickers:
            continue
        age = now - p.get("last_signal_ts", p.get("added_ts", now))
        if age > max_size * 86400:
            continue
        p["status"] = "active"
        p["revived_ts"] = now
        active_tickers.add(p["ticker"])

    # 4) Если пул переполнен — убираем худших active
    active = [(pid, p) for pid, p in pool["strategies"].items()
              if p.get("status") == "active"]
    if len(active) > max_size:
        active.sort(key=lambda x: x[1]["rank_score"])
        to_remove = len(active) - max_size
        for pid, _ in active[:to_remove]:
            remove_from_signal_pool(pool, pid, reason="pool_overflow")
            removed += 1

    pool["last_rotation_ts"] = now
    return removed


def signal_conflicts(portfolio: dict, pool: dict) -> dict:
    """Проверка конфликтов: несколько стратегий в пуле на один тикер.

    Возвращает {ticker: [(pool_id, entry), ...]} — группы конфликтов.
    Для каждого тикера оставляем лучшую стратегию.
    """
    by_ticker = {}
    for pid, p in pool["strategies"].items():
        if p.get("status") != "active":
            continue
        t = p["ticker"]
        if t not in by_ticker:
            by_ticker[t] = []
        by_ticker[t].append((pid, p))

    conflicts = {}
    for t, entries in by_ticker.items():
        if len(entries) > 1:
            conflicts[t] = sorted(entries, key=lambda x: x[1]["rank_score"], reverse=True)
    return conflicts
