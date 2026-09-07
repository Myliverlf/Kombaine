"""Live Execution Engine комбайна.

Гоняет активные слоты по живым 15м-свечам Tinkoff (read для данных),
шлёт реальные ордера через полный гейт риск-менеджера.

Логика идентична бэктест-движку futures_lab.run_backtest:
  сигнал с ПРЕДЫДУЩЕГО закрытого бара, вход по open текущего,
  SL = 2*ATR, TP = 3*ATR, force-exit через 48 часов (192 бара 15м).
"""
import json
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, "/root/prop-desk/futures_lab")
_CODE_DIR = str(Path(__file__).resolve().parent.parent / "code")
if _CODE_DIR not in sys.path:
    sys.path.insert(0, _CODE_DIR)
from futures_lab import (          # noqa: E402
    load_token, load_account, resolve_spec, build_signal, atr as atr_func,
)
try:
    from data_loader import load_ohlcv  # noqa: E402
except ImportError:  # pragma: no cover
    from code.data_loader import load_ohlcv  # type: ignore  # noqa: E402
from tinkoff.invest import Client, OrderDirection, OrderType   # noqa: E402
from tinkoff.invest.schemas import CandleInterval              # noqa: E402

from .config import load_config
from .risk import RiskManager
from . import registry
from . import analytics as an
from . import execution_journal as ej

LOG = Path("/root/prop-desk/logs/combine_engine.log")

# тикеры наших данных -> префикс для поиска фьючерса на MOEX (LKOH = LK*)
API_PREFIX = {"LKOH": "LK"}


def log(msg: str):
    line = "%s %s" % (datetime.now(timezone.utc).strftime("%F %T"), msg)
    print(line, flush=True)
    LOG.parent.mkdir(exist_ok=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")


class Engine:
    def __init__(self):
        self.cfg = load_config()
        self.risk = RiskManager(self.cfg)
        self.token = load_token()
        try:
            self.account = load_account()
        except Exception:
            self.account = self.cfg.account_id
        self.db = an.connect()
        ej.ensure_schema(self.db)
        self.specs = {}          # ticker -> FuturesSpec
        self.candles = {}        # ticker -> DataFrame

    # ---------- данные ----------
    def ensure_spec(self, ticker: str):
        if ticker not in self.specs:
            with Client(self.token) as c:
                self.specs[ticker] = resolve_spec(c, API_PREFIX.get(ticker, ticker))
        return self.specs[ticker]

    def fetch_broker_positions(self) -> dict | None:
        """Запрос реальных фьючерсных позиций у брокера.

        Возвращает {ticker: {"qty": int, "direction": str, "figi": str, "avg_price": float}}
        qty > 0 → long, qty < 0 → short.
        """
        result = {}
        # гарантируем спеки всех тикеров юниверса — иначе uid→ticker маппинг пустой
        for t in self.cfg.universe:
            try:
                self.ensure_spec(t)
            except Exception:
                continue
        try:
            with Client(self.token) as c:
                resp = c.operations.get_portfolio(account_id=self.account)
                for pos in resp.positions:
                    if pos.instrument_type != "futures":
                        continue
                    qty = int(pos.quantity.units)  # nano для целых лотов = 0
                    if qty == 0:
                        continue
                    # Определяем тикер по uid через specs кеш
                    ticker = None
                    pos_uid = getattr(pos, "instrument_uid", None) or getattr(pos, "uid", None)
                    pos_figi = getattr(pos, "figi", None)
                    for t, spec in self.specs.items():
                        if spec.uid == pos_uid or spec.uid == pos_figi:
                            ticker = t
                            break
                    if not ticker:
                        # Попробуем через прямой запрос спецификации
                        continue
                    direction = "LONG" if qty > 0 else "SHORT"
                    avg = getattr(pos, "average_position_price", None)
                    avg_price = (float(avg.units) + avg.nano / 1e9) if avg else 0.0
                    last_price = 0.0
                    for attr in ("current_price", "last_price", "market_price"):
                        price = getattr(pos, attr, None)
                        if price is None:
                            continue
                        units = getattr(price, "units", None)
                        nano = getattr(price, "nano", None)
                        if units is not None and nano is not None:
                            last_price = float(units) + float(nano) / 1e9
                        else:
                            try:
                                last_price = float(price)
                            except (TypeError, ValueError):
                                last_price = 0.0
                        if last_price > 0:
                            break
                    result[ticker] = {
                        "qty": qty,
                        "direction": direction,
                        "uid": pos_uid or pos_figi,
                        "avg_price": avg_price,
                        "last_price": last_price or avg_price,
                    }
        except Exception as e:
            # Unknown broker state must not be interpreted as a confirmed empty
            # portfolio.  Callers use None to skip destructive reconciliation.
            log("BROKER POSITIONS ERROR: %s" % e)
            return None
        return result

    def fetch_candles(self, ticker: str) -> pd.DataFrame:
        """Последние 7 дней 15м-свечей с fallback на локальный 60d CSV.

        Если Tinkoff endpoint недоступен / отдает stale candles, engine должен
        продолжать жить на локальных данных, а не стопорить весь тик.
        """
        spec = self.ensure_spec(ticker)
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=7)
        rows = []
        api_ok = False
        try:
            with Client(self.token) as c:
                resp = c.market_data.get_candles(
                    from_=start, to=end,
                    interval=CandleInterval.CANDLE_INTERVAL_15_MIN,
                    instrument_id=spec.uid)
            for k in resp.candles:
                rows.append({
                    "time": k.time,
                    "open": float(k.open.units) + k.open.nano / 1e9,
                    "high": float(k.high.units) + k.high.nano / 1e9,
                    "low": float(k.low.units) + k.low.nano / 1e9,
                    "close": float(k.close.units) + k.close.nano / 1e9,
                    "volume": int(k.volume),
                })
            api_ok = len(rows) > 0
        except Exception as e:
            log("WARNING: candle API failed for %s: %s — fallback to local CSV" % (ticker, e))

        df = pd.DataFrame(rows) if rows else pd.DataFrame()
        if (not api_ok) or df.empty or not set(["time", "open", "high", "low", "close"]).issubset(df.columns):
            try:
                df = load_ohlcv(ticker, interval="15m", fallback_rows=400)
                df = df.tail(7 * 24 * 4).copy()
                log("INFO: using local 60d CSV fallback for %s (rows=%d, source=%s)" % (ticker, len(df), df.attrs.get('source', 'unknown')))
            except Exception as e:
                log("ERROR: local CSV fallback failed for %s: %s" % (ticker, e))
                return pd.DataFrame()
        self.candles[ticker] = df
        return df

    def fetch_candle_signal(self, ticker: str):
        df = self.fetch_candles(ticker)
        if df is None or df.empty or not set(["time", "open", "high", "low", "close"]).issubset(df.columns):
            return None, "stale_candles"
        last_time = pd.to_datetime(df.iloc[-1]["time"], utc=True)
        age = datetime.now(timezone.utc) - last_time
        # Если свечи чуть устарели — не стопорим тик: это off-hours / delayed feed.
        # Если совсем мёртвые — всё-таки возвращаем stale_candles.
        if age > timedelta(minutes=180):
            log("WARNING: stale_candles for %s last=%s" % (ticker, last_time.isoformat()))
            return None, "stale_candles"
        if age > timedelta(minutes=30):
            log("INFO: delayed_candles for %s last=%s age=%s — using fallback data" % (ticker, last_time.isoformat(), age))
        return df, None

    # ---------- ордера ----------
    @staticmethod
    def _intent_id(*parts) -> str:
        """Deterministic broker id for one logical action across retry/restart."""
        material = "|".join(str(part) for part in parts)
        return str(uuid.uuid5(uuid.NAMESPACE_URL, "strategy-combine:" + material))

    def _execution_authorized(self) -> bool:
        """Final fail-closed boundary for every broker order path.

        Config parsing currently admits only safe modes.  Keeping this check at
        the final `post_order` boundary protects direct helpers, retries and
        future callers even if an earlier gate is bypassed.
        """
        return self.cfg.mode == "live" and self.cfg.paper_first is False

    def post(self, client: Client, uid: str, direction: str, qty: int, *, intent_id: str | None = None,
             intent_context: dict | None = None) -> dict:
        """Submit only after write-ahead journal claim; ambiguity is never retried here."""
        if not self._execution_authorized():
            log("ORDER_VETO mode=%r paper_first=%r reason=EXECUTION_NOT_AUTHORIZED" % (
                self.cfg.mode, self.cfg.paper_first))
            return {"order_id": None, "executed": 0,
                    "status": "VETO:EXECUTION_NOT_AUTHORIZED", "fill_price": None}
        oid = intent_id or str(uuid.uuid4())
        if intent_context is None:
            # Internal callers must provide context before real execution. This is
            # fail-closed instead of creating an unauditable broker request.
            return {"order_id": oid, "executed": 0,
                    "status": "VETO:MISSING_INTENT_CONTEXT", "fill_price": None}
        intent = ej.create_intent(self.db, intent_id=oid, action=intent_context["action"],
                                  ticker=intent_context["ticker"], instrument_id=uid, side=direction,
                                  quantity=qty, slot_id=intent_context.get("slot_id"),
                                  strategy=intent_context.get("strategy"),
                                  provenance=intent_context.get("provenance"), mode=self.cfg.mode,
                                  paper_first=self.cfg.paper_first)
        if intent["status"] == "CREATED":
            intent = ej.transition(self.db, oid, "APPROVED")
        if intent["status"] not in {"APPROVED"} or not ej.claim_submission(self.db, oid):
            return {"order_id": oid, "executed": 0,
                    "status": "VETO:INTENT_NOT_SUBMITTABLE", "fill_price": None}
        side = OrderDirection.ORDER_DIRECTION_BUY if direction == "LONG" else OrderDirection.ORDER_DIRECTION_SELL
        try:
            order = client.orders.post_order(
                instrument_id=uid, quantity=qty, direction=side,
                account_id=self.account, order_type=OrderType.ORDER_TYPE_MARKET, order_id=oid)
        except Exception as exc:
            ej.ambiguous(self.db, oid, exc)
            return {"order_id": oid, "executed": 0, "status": "UNKNOWN:BROKER_EXCEPTION", "fill_price": None}
        avg_price = None
        if getattr(order, "executed_order_price", None) is not None:
            avg = order.executed_order_price
            avg_price = float(getattr(avg, "units", 0)) + float(getattr(avg, "nano", 0)) / 1e9
        elif getattr(order, "initial_order_price", None) is not None:
            avg = order.initial_order_price
            avg_price = float(getattr(avg, "units", 0)) + float(getattr(avg, "nano", 0)) / 1e9
        api_order_id = getattr(order, "order_id", None) or getattr(order, "request_id", None) or oid
        executed = int(getattr(order, "lots_executed", 0) or 0)
        status = str(getattr(order, "execution_report_status", "UNKNOWN"))
        ej.broker_result(self.db, oid, broker_order_id=api_order_id, executed=executed,
                         fill_price=avg_price, status=status)
        return {"order_id": api_order_id, "executed": executed, "status": status,
                "fill_price": avg_price, "intent_id": oid}

    def normalize_fill_price(self, ticker: str, price: float | None, reference_price: float) -> float | None:
        """Normalize broker fill price to candle scale.

        Some FORTS quotes (observed: GAZP futures) can come back from order
        responses in cents/kopeks-like scale (8278) while candles/portfolio use
        ruble scale (82.78). Stop/TP math must use the same scale as candles.
        """
        if price is None:
            return None
        try:
            p = float(price)
            ref = float(reference_price)
        except (TypeError, ValueError):
            return price
        if p <= 0 or ref <= 0:
            return p
        while p > ref * 20.0:
            p /= 100.0
        while p < ref / 20.0:
            p *= 100.0
        return p

    def futures_unrealized_pnl(self, broker_positions: dict) -> float:
        """Unrealized futures PnL; does not add notional price to equity."""
        total = 0.0
        for ticker, pos in (broker_positions or {}).items():
            try:
                spec = self.ensure_spec(ticker)
                point_value = float(getattr(spec, "point_value", 1.0) or 1.0)
            except Exception:
                point_value = 1.0
            last_price = float(pos.get("last_price") or pos.get("avg_price") or 0.0)
            avg_price = float(pos.get("avg_price") or last_price or 0.0)
            signed_qty = int(pos.get("qty", 0) or 0)
            if last_price > 0 and avg_price > 0 and signed_qty != 0:
                total += signed_qty * (last_price - avg_price) * point_value
        return total

    # ---------- основной цикл слота ----------
    def process_slot(self, client: Client, slot_id: str, slot: dict, portfolio: dict, positions: dict,
                     *, broker_state_known: bool = True) -> list:
        """Return slot events; unknown broker state fails closed for new exposure."""
        events = []
        ticker, strategy, params = slot["ticker"], slot["strategy"], slot["params"]
        df, stale_reason = self.fetch_candle_signal(ticker)
        if stale_reason:
            return ["%s/%s: %s" % (ticker, strategy, stale_reason)]
        if df is None or df.empty:
            return ["%s/%s: stale_candles" % (ticker, strategy)]
        if len(df) < 80:
            return ["%s: мало свечей (%d)" % (ticker, len(df))]

        spec = self.specs[ticker]
        # актуализируем ГО слота по живой спеке (бюджет должен сходиться)
        if slot.get("go_rub") != spec.active_margin:
            slot["go_rub"] = float(spec.active_margin)
        df = df.copy()
        df["atr"] = atr_func(df, self.cfg.atr_period)
        sig = build_signal(df, strategy, params)

        pos = slot.get("open_position")
        last = df.iloc[-1]
        prev_signal = int(sig.iloc[-2])
        atr_now = float(last["atr"]) if pd.notna(last["atr"]) else 0.0

        if pos is None:
            if not broker_state_known:
                return ["%s/%s VETO: broker_state_unknown" % (ticker, strategy)]
            if portfolio.get("halted"):
                return ["%s/%s: portfolio_stop" % (ticker, strategy)]
            # --- вход ---
            # Duplicate order guard: если slot уже имеет позицию (в positions dict),
            # НЕ отправляем повторный ордер
            if slot_id in positions and positions[slot_id] is not None:
                events.append("%s/%s: duplicate entry blocked (position exists in positions dict)" % (
                    ticker, strategy))
                return events
            # Ticker-level guard: второй вход по тому же тикеру запрещён
            # (защита от дубль-слотов одной стратегии)
            for other_id, other in portfolio["slots"].items():
                if other_id != slot_id and other["ticker"] == ticker and other.get("open_position"):
                    events.append("%s/%s: entry blocked — %s уже открыт в %s" % (
                        ticker, strategy, ticker, other_id))
                    return events
            if prev_signal == 0 or atr_now <= 0:
                return events
            direction = "LONG" if prev_signal > 0 else "SHORT"
            stop_risk = max(atr_now * self.cfg.sl_atr_mult * spec.point_value, 1e-9)
            qty = int(self.cfg.risk_per_trade_rub // stop_risk)
            qty = min(qty, self.risk.contracts_for_slot(spec.active_margin, registry.used_go(portfolio)))
            qty = min(qty, self.cfg.risk.max_contracts_per_entry)
            if qty <= 0:
                events.append("%s/%s: сигнал %s, но qty=0 (риск/ГО)" % (ticker, strategy, direction))
                return events
            go = spec.active_margin * qty
            used = registry.used_go(portfolio)
            long_go, short_go = registry.long_short_go(portfolio, positions)
            n_active = sum(1 for s in portfolio["slots"].values() if s.get("open_position"))
            ok, reason = self.risk.approve_entry(
                ticker=ticker, direction=direction, go_rub=go,
                used_go_rub=used, n_active=n_active,
                long_go=long_go, short_go=short_go,
                peak_equity=portfolio.get("peak_equity", 0), equity=self._equity(client, positions),
                stop_distance_rub=stop_risk, contracts=qty,
                open_positions=positions)
            if not ok:
                events.append("%s/%s VETO: %s" % (ticker, strategy, reason))
                return events
            intent_id = self._intent_id(slot_id, "entry", ticker, direction, int(qty), str(last["time"]))
            res = self.post(client, spec.uid, direction, qty, intent_id=intent_id,
                            intent_context={"action": "OPEN", "ticker": ticker, "slot_id": slot_id,
                                            "strategy": strategy,
                                            "provenance": {"bar_time": str(last["time"]), "risk": reason}})
            if res["executed"] > 0:
                entry_px = self.normalize_fill_price(ticker, res.get("fill_price"), float(last["open"])) or float(last["open"])
                pos = {
                    "ticker": ticker,  # цикл 2: нужен корреляционному гейту в approve_entry
                    "direction": direction, "qty": int(res["executed"]),
                    "entry_price": entry_px, "entry_atr": atr_now,
                    "entry_ts": time.time(), "entry_bar": len(df) - 1,
                    "trade_id": an.record_open(
                        self.db, slot_id, ticker, strategy, direction,
                        int(res["executed"]), entry_px, "live"),
                }
                slot["open_position"] = pos
                slot["last_signal_ts"] = time.time()
                positions[slot_id] = pos
                an.record_order(self.db, res.get("order_id"), pos["trade_id"], direction, int(res["executed"]), entry_px)
                slot["sl_px"] = entry_px - (self.cfg.sl_atr_mult * atr_now if direction == "LONG" else -self.cfg.sl_atr_mult * atr_now)
                slot["tp_px"] = entry_px + (self.cfg.tp_atr_mult * atr_now if direction == "LONG" else -self.cfg.tp_atr_mult * atr_now)
                events.append("%s/%s OPEN %s x%d @ %.2f (ГО %.0f)" % (
                    ticker, strategy, direction, pos["qty"], entry_px, go))
            else:
                events.append("%s/%s ордер отклонён: %s" % (ticker, strategy, res["status"]))
        else:
            # --- управление позицией ---
            d = 1 if pos["direction"] == "LONG" else -1
            stop_px = pos["entry_price"] - d * self.cfg.sl_atr_mult * pos["entry_atr"]
            take_px = pos["entry_price"] + d * self.cfg.tp_atr_mult * pos["entry_atr"]
            bars_held = int(time.time() - pos["entry_ts"]) // 900
            stop_hit = last["low"] <= stop_px if d > 0 else last["high"] >= stop_px
            take_hit = last["high"] >= take_px if d > 0 else last["low"] <= take_px
            force_exit = bars_held >= self.cfg.force_exit_hours * 4
            if stop_hit or take_hit or force_exit:
                close_dir = "SHORT" if d > 0 else "LONG"
                theory_exit_px = stop_px if stop_hit else (take_px if take_hit else float(last["open"]))
                intent_id = self._intent_id(slot_id, "close", ticker, close_dir, int(pos["qty"]), str(last["time"]))
                res = self.post(client, spec.uid, close_dir, pos["qty"], intent_id=intent_id,
                                intent_context={"action": "CLOSE", "ticker": ticker, "slot_id": slot_id,
                                                "strategy": strategy,
                                                "provenance": {"exit_reason": "stop" if stop_hit else ("take" if take_hit else "time")}})
                if res["executed"] > 0:
                    exit_px = self.normalize_fill_price(
                        ticker,
                        res.get("fill_price"),
                        theory_exit_px,
                    ) or theory_exit_px
                    pnl = d * (exit_px - pos["entry_price"]) * spec.point_value * pos["qty"]
                    reason = "stop" if stop_hit else ("take" if take_hit else "time")
                    an.record_close(self.db, pos["trade_id"], exit_px, pnl, reason)
                    an.record_order(self.db, res.get("order_id"), pos["trade_id"], close_dir, pos["qty"], exit_px)
                    slot["n_trades"] += 1
                    slot["pnl_rub"] += pnl
                    slot["peak_pnl_rub"] = max(slot["peak_pnl_rub"], slot["pnl_rub"])
                    slot["stop_streak"] = slot["stop_streak"] + 1 if reason == "stop" else 0
                    slot["open_position"] = None
                    positions.pop(slot_id, None)
                    events.append("%s/%s CLOSE(%s) pnl=%.0f руб (%d сделок всего)" % (
                        ticker, strategy, reason, pnl, slot["n_trades"]))
                else:
                    events.append("%s/%s close REJECTED: %s" % (ticker, strategy, res["status"]))
        return events

    def force_close_slot(self, slot_id: str, slot: dict):
        """Принудительное закрытие позиции (eject timeout).

        Returns True when a close order was successfully executed and the slot
        state was cleared, otherwise False.
        """
        pos = slot.get("open_position")
        if not pos:
            return False
        ticker = slot["ticker"]
        try:
            spec = self.ensure_spec(ticker)
        except Exception as e:
            log("FORCE_CLOSE: cannot get spec for %s: %s" % (ticker, e))
            return
        close_dir = "SHORT" if pos["direction"] == "LONG" else "LONG"
        intent_id = self._intent_id(slot_id, "force_close", ticker, close_dir, int(pos["qty"]), pos.get("entry_ts", 0))
        try:
            with Client(self.token) as c:
                res = self.post(c, spec.uid, close_dir, pos["qty"], intent_id=intent_id,
                                intent_context={"action": "FORCE_CLOSE", "ticker": ticker, "slot_id": slot_id,
                                                "strategy": slot.get("strategy"),
                                                "provenance": {"reason": "eject_timeout"}})
            if res["executed"] > 0:
                exit_px = self.normalize_fill_price(ticker, res.get("fill_price"), pos["entry_price"]) or pos["entry_price"]
                d = 1 if pos["direction"] == "LONG" else -1
                pnl = d * (exit_px - pos["entry_price"]) * spec.point_value * pos["qty"]
                an.record_close(self.db, pos.get("trade_id"), exit_px, pnl, "eject_timeout")
                slot["open_position"] = None
                log("FORCE_CLOSE %s: %s x%d @ %.2f PnL=%.0f" % (slot_id, close_dir, pos["qty"], exit_px, pnl))
                return True
            else:
                log("FORCE_CLOSE REJECTED %s: %s" % (slot_id, res["status"]))
                return False
        except Exception as e:
            log("FORCE_CLOSE ERROR %s: %s" % (slot_id, e))
            return False

    def _equity(self, client: Client, broker_positions: dict | None = None) -> float:
        try:
            resp = client.operations.get_portfolio(account_id=self.account)
            cash = 0.0
            for a in getattr(resp, "assets", []):
                if a.asset_type and str(a.asset_type).endswith("MONEY"):
                    cash += float(a.quantity.units) + a.quantity.nano / 1e9
            # For FORTS futures, portfolio positions are margin instruments.
            # Do NOT add notional instrument price to equity; that creates fake
            # 40k+ equity jumps and triggers false portfolio_stop. Use cash if
            # broker returns it, otherwise configured deposit, plus unrealized
            # futures PnL on current open positions.
            total = cash if cash > 0 else float(self.cfg.deposit_rub)
            broker_positions = broker_positions or self.fetch_broker_positions()
            total += self.futures_unrealized_pnl(broker_positions)
            return total or self.cfg.deposit_rub
        except Exception:
            return self.cfg.deposit_rub

    # ---------- один тик всего портфеля ----------
    def tick(self):
        portfolio = registry.load_portfolio()
        positions = {sid: s["open_position"] for sid, s in portfolio["slots"].items() if s.get("open_position")}
        events = []

        # Шаг 0: Reconciliation — сверяем portfolio с реальными позициями брокера
        # до обработки слотов, чтобы не открывать дубли
        broker_positions = self.fetch_broker_positions()
        if broker_positions is not None:
            reconcile_events = registry.reconcile_broker_positions(portfolio, broker_positions)
            events += reconcile_events
            # Создаём pending trade records для adopt-ённых позиций
            from .registry import reconcile_create_pending_trades
            created_ids = reconcile_create_pending_trades(portfolio, broker_positions, self.db)
            for tid in created_ids:
                events.append("RECONCILE: created pending trade record id=%d" % tid)
            # Обновляем positions dict после reconciliation
            positions = {sid: s["open_position"] for sid, s in portfolio["slots"].items() if s.get("open_position")}

        with Client(self.token) as c:
            for slot_id, slot in portfolio["slots"].items():
                try:
                    events += self.process_slot(
                        c, slot_id, slot, portfolio, positions,
                        broker_state_known=broker_positions is not None,
                    )
                except Exception as e:
                    events.append("%s ERROR: %s" % (slot_id, e))

        # Финальная reconciliation после обработки: проверяем что broker и portfolio согласованы
        broker_positions_after = self.fetch_broker_positions()
        if broker_positions_after is not None:
            reconcile_events = registry.reconcile_broker_positions(portfolio, broker_positions_after)
            events += reconcile_events

        registry.save_portfolio(portfolio)
        return events


if __name__ == "__main__":
    eng = Engine()
    evs = eng.tick()
    print("tick events:", len(evs))
