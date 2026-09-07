from core.agent_process import AgentProcess, build_role_prompt, normalize_external_payload


def test_build_role_prompt_is_json_first():
    prompt = build_role_prompt("L1", {"goal": "improve autonomy"})
    assert "СТРОГО JSON" in prompt
    assert "next_tasks" in prompt
    assert "L1" in prompt


def test_normalize_external_payload_fills_defaults():
    payload = normalize_external_payload({"summary": "ok"}, "L2")
    assert payload["summary"] == "ok"
    assert payload["decisions"] == []
    assert payload["blockers"] == []
    assert payload["next_tasks"] == []


def test_agent_process_role_defaults_are_split():
    l1 = AgentProcess("L1", "/root/.pi/profiles/analyst.md")
    l2 = AgentProcess("L2", "/root/.pi/profiles/architect.md")
    assert l1.model == "gpt-5.4-mini"
    assert l2.model != ""


def test_agent_process_can_override_role_model():
    l1 = AgentProcess("L1", "/root/.pi/profiles/analyst.md", model="gpt-5.4-mini")
    assert l1.model == "gpt-5.4-mini"
