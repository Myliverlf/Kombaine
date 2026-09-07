"""Persistent strategy registry for strategy_combine — CANONICAL SOURCE OF TRUTH.

This module keeps a durable registry of every generated strategy with:
- explicit status transitions,
- metrics/backtest snapshots,
- portfolio-aware context,
- append-only history trail.

It is intentionally self-contained and JSON-backed so it can be used in unit
tests without touching the live broker or the legacy core state files.

CANONICAL SOURCE OF TRUTH
--------------------------
strategy_registry.json is the single authoritative store for all strategy state.
Legacy state files (waitlist.json, signal_pool.json) are DERIVED VIEWS exported
from this registry for backward compatibility with consumers that have not yet
migrated to the registry API.

All writes MUST go through this module. Legacy files are written via
export_legacy_state_files() AFTER registry.save() as a compatibility shim.
Do NOT read legacy files to update the registry — the registry owns the state.

MIGRATION: To import legacy state into the registry, call
sync_from_legacy_files() once. After that, legacy files become read-only exports.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

STATE_DIR = Path(__file__).resolve().parent.parent / "state"
REGISTRY_FILE = STATE_DIR / "strategy_registry.json"
LEGACY_WAITLIST_FILE = STATE_DIR / "waitlist.json"
LEGACY_SIGNAL_POOL_FILE = STATE_DIR / "signal_pool.json"

# Canonical flag: all consumers should check this to confirm registry ownership
REGISTRY_CANONICAL = True

STATUS_REGISTRY_CANDIDATE = "registry/candidate"
STATUS_WAITLIST = "waitlist"
STATUS_ACTIVE_WATCHLIST = "active_watchlist"
STATUS_ACTIVE_SIGNAL_POOL = "active_signal_pool"
STATUS_REJECTED = "rejected"
STATUS_ROTATED_OUT = "rotated_out"
STATUS_CONFLICTED = "conflicted"
STATUS_EXPIRED = "expired"

ALL_STATUSES = {
    STATUS_REGISTRY_CANDIDATE,
    STATUS_WAITLIST,
    STATUS_ACTIVE_WATCHLIST,
    STATUS_ACTIVE_SIGNAL_POOL,
    STATUS_REJECTED,
    STATUS_ROTATED_OUT,
    STATUS_CONFLICTED,
    STATUS_EXPIRED,
}


@dataclass
class StrategyEvent:
    ts: float
    status: str
    reason: str = ""
    note: str = ""
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class StrategyRecord:
    strategy_id: str
    ticker: str
    strategy: str
    params: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    status: str = STATUS_REGISTRY_CANDIDATE
    created_ts: float = field(default_factory=time.time)
    updated_ts: float = field(default_factory=time.time)
    source: str = "generator"
    generation_batch_id: str = ""
    portfolio_context: dict[str, Any] = field(default_factory=dict)
    quality_gate: dict[str, Any] = field(default_factory=dict)
    history: list[StrategyEvent] = field(default_factory=list)
    candidate_stream: bool = True
    active_rank: float = 0.0
    watchlist_slot: Optional[int] = None
    signal_pool_slot: Optional[int] = None
    expires_ts: Optional[float] = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["history"] = [asdict(ev) for ev in self.history]
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StrategyRecord":
        history = [StrategyEvent(**event) for event in data.get("history", [])]
        field_names = {
            "strategy_id", "ticker", "strategy", "params", "metrics", "status",
            "created_ts", "updated_ts", "source", "generation_batch_id",
            "portfolio_context", "quality_gate", "candidate_stream", "active_rank",
            "watchlist_slot", "signal_pool_slot", "expires_ts",
        }
        kwargs = {key: data.get(key) for key in field_names}
        kwargs["params"] = kwargs.get("params") or {}
        kwargs["metrics"] = kwargs.get("metrics") or {}
        kwargs["portfolio_context"] = kwargs.get("portfolio_context") or {}
        kwargs["quality_gate"] = kwargs.get("quality_gate") or {}
        kwargs["history"] = history
        kwargs["candidate_stream"] = bool(data.get("candidate_stream", True))
        kwargs["active_rank"] = float(data.get("active_rank", 0.0) or 0.0)
        kwargs["watchlist_slot"] = data.get("watchlist_slot")
        kwargs["signal_pool_slot"] = data.get("signal_pool_slot")
        kwargs["expires_ts"] = data.get("expires_ts")
        return cls(**kwargs)

    def add_event(self, status: str, reason: str = "", note: str = "", payload: Optional[dict[str, Any]] = None) -> None:
        self.status = status
        self.updated_ts = time.time()
        self.history.append(
            StrategyEvent(
                ts=self.updated_ts,
                status=status,
                reason=reason,
                note=note,
                payload=dict(payload or {}),
            )
        )


class StrategyRegistry:
    """JSON-backed persistent registry of all generated strategies."""

    def __init__(self, path: Path = REGISTRY_FILE):
        self.path = Path(path)
        self._data = self._load()

    @classmethod
    def load(cls, path: Path = REGISTRY_FILE) -> "StrategyRegistry":
        return cls(path=path)

    def _default_data(self) -> dict[str, Any]:
        return {
            "version": 1,
            "created_ts": time.time(),
            "updated_ts": time.time(),
            "strategies": {},
            "events": [],
        }

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return self._default_data()
        try:
            data = json.loads(self.path.read_text())
        except json.JSONDecodeError:
            ts = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
            corrupt = self.path.with_name(self.path.name + f".corrupt-{ts}")
            self.path.replace(corrupt)
            return self._default_data()
        if "strategies" not in data:
            data["strategies"] = {}
        if "events" not in data:
            data["events"] = []
        data.setdefault("version", 1)
        data.setdefault("created_ts", time.time())
        data.setdefault("updated_ts", time.time())
        return data

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._data["updated_ts"] = time.time()
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(self._data, indent=2, ensure_ascii=False, sort_keys=True))
        tmp.replace(self.path)

    @property
    def data(self) -> dict[str, Any]:
        return self._data

    def as_dict(self) -> dict[str, Any]:
        return json.loads(json.dumps(self._data))

    def records(self) -> list[StrategyRecord]:
        return [StrategyRecord.from_dict(v) for v in self._data["strategies"].values()]

    def get(self, strategy_id: str) -> Optional[StrategyRecord]:
        payload = self._data["strategies"].get(strategy_id)
        return StrategyRecord.from_dict(payload) if payload else None

    def _store_record(self, record: StrategyRecord) -> StrategyRecord:
        self._data["strategies"][record.strategy_id] = record.to_dict()
        self._data["events"].append({
            "ts": time.time(),
            "strategy_id": record.strategy_id,
            "status": record.status,
            "ticker": record.ticker,
            "strategy": record.strategy,
        })
        return record

    def record_generation(
        self,
        strategy_id: str,
        ticker: str,
        strategy: str,
        params: Optional[dict[str, Any]] = None,
        metrics: Optional[dict[str, Any]] = None,
        portfolio_context: Optional[dict[str, Any]] = None,
        quality_gate: Optional[dict[str, Any]] = None,
        source: str = "generator",
        generation_batch_id: str = "",
        status: str = STATUS_REGISTRY_CANDIDATE,
        note: str = "generated",
        payload: Optional[dict[str, Any]] = None,
    ) -> StrategyRecord:
        record = self.get(strategy_id)
        if record is None:
            record = StrategyRecord(
                strategy_id=strategy_id,
                ticker=ticker,
                strategy=strategy,
                params=dict(params or {}),
                metrics=dict(metrics or {}),
                status=status,
                source=source,
                generation_batch_id=generation_batch_id,
                portfolio_context=dict(portfolio_context or {}),
                quality_gate=dict(quality_gate or {}),
            )
        else:
            record.ticker = ticker
            record.strategy = strategy
            record.params = dict(params or record.params)
            record.metrics = dict(metrics or record.metrics)
            record.source = source or record.source
            record.generation_batch_id = generation_batch_id or record.generation_batch_id
            if portfolio_context is not None:
                record.portfolio_context = dict(portfolio_context)
            if quality_gate is not None:
                record.quality_gate = dict(quality_gate)
        record.candidate_stream = True
        record.add_event(status=status, note=note, payload=payload or {})
        self._store_record(record)
        return record

    def transition(
        self,
        strategy_id: str,
        status: str,
        reason: str = "",
        note: str = "",
        payload: Optional[dict[str, Any]] = None,
    ) -> StrategyRecord:
        record = self.get(strategy_id)
        if record is None:
            raise KeyError(f"unknown strategy_id: {strategy_id}")
        record.add_event(status=status, reason=reason, note=note, payload=payload or {})
        if status in {STATUS_REJECTED, STATUS_ROTATED_OUT, STATUS_CONFLICTED, STATUS_EXPIRED}:
            record.candidate_stream = True
        elif status in {STATUS_ACTIVE_WATCHLIST, STATUS_ACTIVE_SIGNAL_POOL}:
            record.candidate_stream = False
        elif status == STATUS_WAITLIST:
            record.candidate_stream = True
        self._store_record(record)
        return record

    def update_quality_gate(self, strategy_id: str, **quality_gate: Any) -> StrategyRecord:
        record = self.get(strategy_id)
        if record is None:
            raise KeyError(f"unknown strategy_id: {strategy_id}")
        record.quality_gate.update(quality_gate)
        record.updated_ts = time.time()
        self._store_record(record)
        return record

    def set_portfolio_context(self, strategy_id: str, **context: Any) -> StrategyRecord:
        record = self.get(strategy_id)
        if record is None:
            raise KeyError(f"unknown strategy_id: {strategy_id}")
        record.portfolio_context.update(context)
        record.updated_ts = time.time()
        self._store_record(record)
        return record

    def mark_expired(self, strategy_id: str, reason: str = "ttl") -> StrategyRecord:
        return self.transition(strategy_id, STATUS_EXPIRED, reason=reason, note="expired")

    def mark_rejected(self, strategy_id: str, reason: str = "quality_gate") -> StrategyRecord:
        return self.transition(strategy_id, STATUS_REJECTED, reason=reason, note="rejected")

    def mark_waitlist(self, strategy_id: str, reason: str = "") -> StrategyRecord:
        return self.transition(strategy_id, STATUS_WAITLIST, reason=reason, note="waitlist")

    def mark_active_watchlist(self, strategy_id: str, slot: Optional[int] = None, score: float = 0.0) -> StrategyRecord:
        record = self.transition(
            strategy_id,
            STATUS_ACTIVE_WATCHLIST,
            reason="promoted_to_watchlist",
            note="active_watchlist",
            payload={"watchlist_slot": slot, "score": score},
        )
        record.watchlist_slot = slot
        record.active_rank = score
        self._store_record(record)
        return record

    def mark_active_signal_pool(self, strategy_id: str, slot: Optional[int] = None, score: float = 0.0) -> StrategyRecord:
        record = self.transition(
            strategy_id,
            STATUS_ACTIVE_SIGNAL_POOL,
            reason="promoted_to_signal_pool",
            note="active_signal_pool",
            payload={"signal_pool_slot": slot, "score": score},
        )
        record.signal_pool_slot = slot
        record.active_rank = score
        self._store_record(record)
        return record

    def mark_rotated_out(self, strategy_id: str, reason: str = "replaced") -> StrategyRecord:
        return self.transition(strategy_id, STATUS_ROTATED_OUT, reason=reason, note="rotated_out")

    def mark_conflicted(self, strategy_id: str, reason: str = "ticker_conflict") -> StrategyRecord:
        return self.transition(strategy_id, STATUS_CONFLICTED, reason=reason, note="conflicted")

    def active_by_status(self, statuses: Iterable[str]) -> list[StrategyRecord]:
        wanted = set(statuses)
        return [record for record in self.records() if record.status in wanted]

    def active_watchlist(self, limit: int = 10) -> list[StrategyRecord]:
        records = self.active_by_status({STATUS_ACTIVE_WATCHLIST})
        return sorted(records, key=lambda r: r.active_rank, reverse=True)[:limit]

    def active_signal_pool(self, limit: int = 10) -> list[StrategyRecord]:
        records = self.active_by_status({STATUS_ACTIVE_SIGNAL_POOL})
        return sorted(records, key=lambda r: r.active_rank, reverse=True)[:limit]

    def candidate_stream(self) -> list[StrategyRecord]:
        return [
            record
            for record in self.records()
            if record.status in {
                STATUS_REGISTRY_CANDIDATE,
                STATUS_WAITLIST,
                STATUS_REJECTED,
                STATUS_ROTATED_OUT,
                STATUS_CONFLICTED,
                STATUS_EXPIRED,
            }
        ]

    def prune_history(self, max_events: int = 1000, max_per_record: int = 50) -> None:
        """Prune global events list AND per-record history to control file size."""
        events = self._data.get("events", [])
        if len(events) > max_events:
            self._data["events"] = events[-max_events:]
        # Prune per-record history (each strategy keeps last N events)
        for record in self.records():
            if len(record.history) > max_per_record:
                record.history = record.history[-max_per_record:]

    def export_legacy_waitlist(self) -> dict[str, Any]:
        candidates = {}
        for record in self.records():
            if record.status not in {STATUS_REGISTRY_CANDIDATE, STATUS_WAITLIST}:
                continue
            candidates[record.strategy_id] = {
                "ticker": record.ticker,
                "strategy": record.strategy,
                "params": dict(record.params),
                "metrics": dict(record.metrics),
                "rank_score": float(record.active_rank or record.metrics.get("rank_score", 0.0) or 0.0),
                "added_ts": record.created_ts,
                "retested_ts": record.updated_ts,
                "ttl_days": int(record.quality_gate.get("ttl_days", 7)),
                "retests": int(record.quality_gate.get("retests", 0)),
                "status": record.status,
            }
        return {"candidates": candidates}

    def export_legacy_signal_pool(self) -> dict[str, Any]:
        strategies: dict[str, Any] = {}
        for record in self.records():
            if record.status not in {STATUS_ACTIVE_WATCHLIST, STATUS_ACTIVE_SIGNAL_POOL}:
                continue
            strategies[record.strategy_id] = {
                "ticker": record.ticker,
                "strategy": record.strategy,
                "params": dict(record.params),
                "metrics": dict(record.metrics),
                "rank_score": float(record.active_rank),
                "go_rub": float(record.portfolio_context.get("go_rub", record.metrics.get("go_rub", 0.0) or 0.0)),
                "added_ts": record.created_ts,
                "last_signal_ts": record.updated_ts,
                "signals_generated": int(record.quality_gate.get("signals_generated", 0)),
                "status": record.status,
            }
        return {"strategies": strategies, "last_rotation_ts": self._data.get("updated_ts", time.time())}

    # ------------------------------------------------------------------
    # Canonical helpers: formalizing registry as single source of truth
    # ------------------------------------------------------------------

    def canonical_status(self, strategy_id: str) -> Optional[str]:
        """Return the authoritative status of a strategy from the registry.

        This replaces any status read from legacy files.
        Returns None if the strategy is not tracked.
        """
        record = self.get(strategy_id)
        return record.status if record else None

    def export_legacy_state_files(
        self,
        waitlist_path: Path = LEGACY_WAITLIST_FILE,
        signal_pool_path: Path = LEGACY_SIGNAL_POOL_FILE,
    ) -> None:
        """Write derived legacy state files from the canonical registry.

        Call AFTER registry.save(). This is the ONLY way legacy files should
        be written — they are derived views, not authoritative state.
        """
        waitlist = self.export_legacy_waitlist()
        signal_pool = self.export_legacy_signal_pool()

        STATE_DIR.mkdir(parents=True, exist_ok=True)
        for path, data in [(waitlist_path, waitlist), (signal_pool_path, signal_pool)]:
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True))
            tmp.replace(path)

    def sync_from_legacy_files(
        self,
        waitlist_path: Path = LEGACY_WAITLIST_FILE,
        signal_pool_path: Path = LEGACY_SIGNAL_POOL_FILE,
    ) -> int:
        """One-time migration: import legacy waitlist/signal_pool into registry.

        Reads legacy files and ensures every entry exists in the registry.
        Returns the number of records imported. After calling this, legacy
        files become derived views — call export_legacy_state_files() after
        save() to keep them in sync.
        """
        import json as _json

        imported = 0
        # Import from legacy waitlist
        if waitlist_path.exists():
            try:
                waitlist = _json.loads(waitlist_path.read_text())
            except (_json.JSONDecodeError, OSError):
                waitlist = {}
            for cid, cand in waitlist.get("candidates", {}).items():
                if self.get(cid) is not None:
                    continue  # already in registry
                self.record_generation(
                    strategy_id=cid,
                    ticker=cand.get("ticker", ""),
                    strategy=cand.get("strategy", ""),
                    params=cand.get("params"),
                    metrics=cand.get("metrics"),
                    portfolio_context={
                        "regime_ok": True,
                        "stale_ok": True,
                        "contract_risk_ok": True,
                        "go_rub": cand.get("go_rub", 0.0),
                    },
                    quality_gate={"ttl_days": cand.get("ttl_days", 7), "retests": cand.get("retests", 0)},
                    source="legacy_waitlist",
                    status=cand.get("status", STATUS_WAITLIST),
                    note="synced_from_legacy_waitlist",
                )
                imported += 1

        # Import from legacy signal pool
        if signal_pool_path.exists():
            try:
                pool = _json.loads(signal_pool_path.read_text())
            except (_json.JSONDecodeError, OSError):
                pool = {}
            for pid, sig in pool.get("strategies", {}).items():
                if self.get(pid) is not None:
                    continue  # already in registry
                # Determine status from legacy pool entry
                legacy_status = sig.get("status", "active")
                if legacy_status == "active":
                    target_status = STATUS_ACTIVE_SIGNAL_POOL
                elif legacy_status == "promoted":
                    target_status = STATUS_ACTIVE_WATCHLIST
                else:
                    target_status = STATUS_WAITLIST
                self.record_generation(
                    strategy_id=pid,
                    ticker=sig.get("ticker", ""),
                    strategy=sig.get("strategy", ""),
                    params=sig.get("params"),
                    metrics=sig.get("metrics"),
                    portfolio_context={
                        "regime_ok": True,
                        "stale_ok": True,
                        "contract_risk_ok": True,
                        "go_rub": sig.get("go_rub", 0.0),
                    },
                    quality_gate={"signals_generated": sig.get("signals_generated", 0)},
                    source="legacy_signal_pool",
                    status=target_status,
                    note="synced_from_legacy_signal_pool",
                )
                # Apply ranking from legacy entry
                record = self.get(pid)
                if record:
                    record.active_rank = float(sig.get("rank_score", 0.0))
                    self._store_record(record)
                imported += 1

        return imported


def load_registry(path: Path = REGISTRY_FILE) -> StrategyRegistry:
    return StrategyRegistry.load(path=path)


def save_registry(registry: StrategyRegistry) -> None:
    registry.save()


def serialize_registry(registry: StrategyRegistry) -> str:
    return json.dumps(registry.as_dict(), ensure_ascii=False, indent=2, sort_keys=True)
