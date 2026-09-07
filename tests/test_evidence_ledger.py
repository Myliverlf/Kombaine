from pathlib import Path

from core.evidence_ledger import EvidenceLedger


def test_evidence_ledger_append_and_summary(tmp_path: Path):
    ledger = EvidenceLedger(tmp_path)
    ledger.append("ep-1", {"claim": "a", "source": "x"})
    ledger.append("ep-1", {"claim": "b", "source": "y"})
    summary = ledger.summary("ep-1")
    assert summary["count"] == 2
    assert summary["claims"] == ["a", "b"]
