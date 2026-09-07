"""External agent process wrapper for layered orchestration.

Runs each layer as an independent subprocess and expects structured JSON
on stdout. This is the first real process boundary for the orchestration
stack.
"""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


PI_BIN = os.environ.get("PI_BIN", "/root/.local/bin/pi")
PI_PROVIDER = os.environ.get("PI_PROVIDER", "opencode-go")
PI_MODEL = os.environ.get("PI_MODEL", "mimo-v2.5")
PI_HOME = os.environ.get("PI_HOME", "/root")

ROLE_MODEL_DEFAULTS = {
    "L1": os.environ.get("PI_L1_MODEL", "gpt-5.4-mini"),
    "L2": os.environ.get("PI_L2_MODEL", PI_MODEL),
    "L3": os.environ.get("PI_L3_MODEL", PI_MODEL),
}
ROLE_PROVIDER_DEFAULTS = {
    "L1": os.environ.get("PI_L1_PROVIDER", PI_PROVIDER),
    "L2": os.environ.get("PI_L2_PROVIDER", PI_PROVIDER),
    "L3": os.environ.get("PI_L3_PROVIDER", PI_PROVIDER),
}


@dataclass
class AgentProcessResult:
    role: str
    ok: bool
    stdout: str
    stderr: str
    returncode: int
    payload: Dict[str, Any]


class AgentProcess:
    def __init__(
        self,
        role: str,
        profile_path: str,
        timeout_seconds: int = 600,
        provider: str | None = None,
        model: str | None = None,
    ):
        self.role = role
        self.profile_path = Path(profile_path)
        self.timeout_seconds = timeout_seconds
        self.provider = provider or ROLE_PROVIDER_DEFAULTS.get(role, PI_PROVIDER)
        self.model = model or ROLE_MODEL_DEFAULTS.get(role, PI_MODEL)

    def run(self, prompt: str) -> AgentProcessResult:
        cmd = [
            PI_BIN,
            "-p",
            "--provider", self.provider,
            "--model", self.model,
            "--append-system-prompt", str(self.profile_path),
            "--no-session",
            prompt,
        ]
        env = dict(os.environ)
        env["HOME"] = PI_HOME
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=self.timeout_seconds,
            env=env,
        )
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        payload: Dict[str, Any] = {}
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError:
            payload = {"text": stdout.strip()}
        ok = proc.returncode == 0
        return AgentProcessResult(
            role=self.role,
            ok=ok,
            stdout=stdout,
            stderr=stderr,
            returncode=proc.returncode,
            payload=payload,
        )


def build_role_prompt(role: str, context: Dict[str, Any]) -> str:
    """Build a JSON-first prompt for an external pi agent.

    We ask the agent to return strict JSON containing:
    - summary
    - decisions
    - task
    - result
    - evidence
    - blockers
    - next_action
    - next_tasks
    - convergence
    """
    return (
        "Ты внешний агент роли %s.\n"
        "Работай как отдельный процесс. Не симулируй.\n"
        "Пиши простым русским, без техжаргона. Это paper-only режим: без живых ордеров и без изменения брокера.\n"
        "Верни СТРОГО JSON без markdown и без лишнего текста.\n"
        "Обязательные поля: summary, decisions, task, result, evidence, blockers, next_action, next_tasks, convergence.\n"
        "summary — коротко и по делу, минимум одно содержательное предложение; значение не может быть пустым и не может быть 'no summary'.\n"
        "evidence — только факты и наблюдения из контекста, без общих слов.\n"
        "next_tasks — список конкретных следующих задач; если работа ещё не закончена, список должен быть непустым.\n"
        "convergence — объект с полями status и reason; status используй из набора continue, converged, stuck.\n"
        "Если не хватает фактов, всё равно верни JSON и заполни blockers и next_action.\n"
        "Контекст: %s\n"
        % (role, json.dumps(context, ensure_ascii=False))
    )


def _trim_text(value: Any) -> str:
    text = str(value or "").strip()
    return " ".join(text.split())


def _summary_fallback(role: str, payload: Dict[str, Any]) -> str:
    parts: List[str] = []
    result_text = _trim_text(payload.get("result"))
    next_action = _trim_text(payload.get("next_action"))
    blockers = payload.get("blockers") if isinstance(payload.get("blockers"), list) else []
    evidence = payload.get("evidence") if isinstance(payload.get("evidence"), list) else []

    if result_text:
        parts.append(result_text)
    if next_action:
        parts.append(f"следующий шаг: {next_action}")
    if not parts and evidence:
        first = evidence[0]
        if isinstance(first, dict):
            first_text = _trim_text(first.get("claim") or first.get("summary") or first.get("text"))
            if first_text:
                parts.append(f"доказательство: {first_text}")
        elif isinstance(first, str) and first.strip():
            parts.append(f"доказательство: {first.strip()}")
    if not parts and blockers:
        parts.append(f"есть блокеры: {', '.join(_trim_text(item) for item in blockers if _trim_text(item))}")
    if not parts:
        parts.append("нужен следующий шаг по цели")
    text = "; ".join(parts)
    if not text.lower().startswith(role.lower() + ":"):
        text = f"{text}"
    return text[:240]


def _normalize_next_tasks(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    normalized: List[Dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        goal = _trim_text(item.get("goal"))
        if not goal:
            continue
        normalized.append({
            "goal": goal,
            "why": _trim_text(item.get("why")),
            "role": _trim_text(item.get("role")) or "L2",
            "inputs": item.get("inputs") if isinstance(item.get("inputs"), list) else [],
            "expected_output": item.get("expected_output") if isinstance(item.get("expected_output"), list) else [],
        })
    return normalized


def _normalize_convergence(value: Any) -> Dict[str, str]:
    if not isinstance(value, dict):
        return {"status": "continue", "reason": ""}
    status = _trim_text(value.get("status")).lower() or "continue"
    if status not in {"continue", "converged", "stuck"}:
        status = "continue"
    return {
        "status": status,
        "reason": _trim_text(value.get("reason")),
    }


def normalize_external_payload(payload: Dict[str, Any], role: str) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        return {
            "summary": f"{role}: invalid payload",
            "decisions": [],
            "task": {},
            "result": {},
            "evidence": {},
            "blockers": ["invalid_payload"],
            "next_action": "inspect stdout format",
            "next_tasks": [],
            "convergence": {"status": "stuck", "reason": "invalid payload"},
        }

    normalized = dict(payload)
    summary = _trim_text(normalized.get("summary"))
    summary_fabricated = False
    if not summary or summary.lower() == "no summary" or summary.lower().endswith(": no summary"):
        summary = _summary_fallback(role, normalized)
        summary_fabricated = True
    normalized["summary"] = summary
    # FIX(empty-summary): a fabricated summary must never look like real agent output.
    normalized["summary_fabricated"] = summary_fabricated
    normalized["decisions"] = normalized.get("decisions", []) if isinstance(normalized.get("decisions", []), list) else []
    normalized["task"] = normalized.get("task", {}) if isinstance(normalized.get("task", {}), dict) else {}
    normalized["result"] = normalized.get("result", {}) if isinstance(normalized.get("result", {}), dict) else {}
    normalized["evidence"] = normalized.get("evidence", {}) if isinstance(normalized.get("evidence", {}), dict) else {}
    normalized["blockers"] = normalized.get("blockers", []) if isinstance(normalized.get("blockers", []), list) else []
    if summary_fabricated:
        normalized["blockers"].append("empty_agent_summary_fabricated")
    normalized["next_action"] = _trim_text(normalized.get("next_action"))
    normalized["next_tasks"] = _normalize_next_tasks(normalized.get("next_tasks", []))
    normalized["convergence"] = _normalize_convergence(normalized.get("convergence", {}))
    return normalized
