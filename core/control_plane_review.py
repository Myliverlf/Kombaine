"""Independent review helpers for the autonomous Hermes control plane."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


@dataclass
class ReviewFinding:
    claim: str
    verdict: str
    evidence: List[str] = field(default_factory=list)
    note: str = ""


@dataclass
class ReviewReport:
    status: str
    findings: List[ReviewFinding] = field(default_factory=list)
    pass_requires_evidence: bool = True

    def has_pass(self) -> bool:
        return self.status.upper() == "PASS" and self.pass_requires_evidence and all(f.verdict.upper() == "PASS" for f in self.findings)


def build_review_report(status: str, findings: List[ReviewFinding], pass_requires_evidence: bool = True) -> ReviewReport:
    return ReviewReport(status=status, findings=findings, pass_requires_evidence=pass_requires_evidence)
