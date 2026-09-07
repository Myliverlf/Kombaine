"""Durable evidence ledger for layered orchestration episodes.

Append-only JSON store. Keeps evidence from previous cycles so the next cycle
can see what was already proven.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List


class EvidenceLedger:
    def __init__(self, base_dir: Path | str):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def path_for(self, episode_id: str) -> Path:
        return self.base_dir / f"{episode_id}.evidence_ledger.json"

    def load(self, episode_id: str) -> List[Dict[str, Any]]:
        path = self.path_for(episode_id)
        if not path.exists():
            return []
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                return raw
        except Exception:
            pass
        return []

    def append(self, episode_id: str, evidence: Dict[str, Any]) -> Path:
        path = self.path_for(episode_id)
        ledger = self.load(episode_id)
        ledger.append(dict(evidence))
        path.write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def replace_all(self, episode_id: str, evidence_list: List[Dict[str, Any]]) -> Path:
        path = self.path_for(episode_id)
        path.write_text(json.dumps([dict(e) for e in evidence_list], ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def summary(self, episode_id: str) -> Dict[str, Any]:
        ledger = self.load(episode_id)
        return {
            "episode_id": episode_id,
            "count": len(ledger),
            "claims": [e.get("claim", "") for e in ledger[-20:]],
            "sources": sorted({e.get("source", "") for e in ledger if e.get("source")}),
        }
