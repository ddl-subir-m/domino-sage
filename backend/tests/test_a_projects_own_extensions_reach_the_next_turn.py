"""A Project's own skills reach the next turn, as its Thread or App allows (#619).

ADR-0071. The files go in OpenCode's project slot, `.opencode/` at the Project root; OpenCode reads
them once per directory, so Sage disposes the instance after a change — never under a running turn.
The shim then decides per turn: a Thread or App can switch a skill off. Project tools and MCP
servers were taken out (#638); Sage's own tools are untouched.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sage import extensions
from sage.gateway.client import FakeGatewayClient
from sage.router.model_control import ModelControl
from sage.router.models import Mode, ModelCatalog, Phase
from sage.shim.enforcement import EnforcementShim

from .fake_opencode import Turn
from .test_chat_turn import IntentGateway, ObservedControlOpenCode
from .test_chat_turn import _orch as _chat_orch
from .test_no_edit_recovery_uses_the_active_stack import (
    _no_waiting,  # noqa: F401  (autouse)
)
from .test_turn_path import _build

CATALOG = ModelCatalog(
    sovereign_plan="sovereign-8b", sovereign_implement="sovereign-8b", sovereign_ask="sovereign-8b",
    plan="strong-vendor", implement="cheap-vendor", ask="ask-vendor",
)

SKILL = "---\nname: {name}\ndescription: A test skill.\n---\nDo the thing.\n"


def _skill(name: str) -> dict:
    return {"kind": "skill", "name": name, "files": {"SKILL.md": SKILL.format(name=name)}}


# ---- the store ---------------------------------------------------------------------------------

def test_a_skill_lands_in_the_project_slot_and_the_manifest(tmp_path):
    extensions.add(tmp_path, {**_skill("alpha"),
                              "files": {"SKILL.md": SKILL.format(name="alpha"), "ref/a.md": "A"}})

    slot = tmp_path / ".opencode"
    assert (slot / "skills" / "alpha" / "SKILL.md").read_text().startswith("---\nname: alpha")
    assert (slot / "skills" / "alpha" / "ref" / "a.md").read_text() == "A"
    [entry] = extensions.read_manifest(tmp_path)
    assert entry["id"] == "skill:alpha" and entry["defaultEnabled"] is True
    assert entry["files"] == [".opencode/skills/alpha/SKILL.md", ".opencode/skills/alpha/ref/a.md"]


@pytest.mark.parametrize(("body", "said"), [
    (_skill("sage-helper"), "sage-"),
    (_skill("Bad Name"), "lowercase"),
    ({"kind": "skill", "name": "esc",
      "files": {"SKILL.md": SKILL.format(name="esc"), "../../etc/x": "y"}}, "not a path"),
    (_skill("data-table"), "Sage ships"),
    ({"kind": "skill", "name": "alpha", "files": {"SKILL.md": SKILL.format(name="sage-x")}},
     "frontmatter"),
    ({"kind": "tool", "name": "lookup", "code": "export default {}\n"}, "kind"),
    ({"kind": "mcp", "name": "crm", "config": {"type": "remote", "url": "u"}}, "kind"),
    ({"kind": "agent", "name": "x"}, "kind"),
])
def test_a_name_or_path_sage_must_not_write_is_refused_at_the_door(tmp_path, body, said):
    with pytest.raises(extensions.ExtensionError, match=said):
        extensions.add(tmp_path, body)
    assert not (tmp_path / ".opencode").exists()


def test_a_folder_sage_did_not_write_is_never_overwritten(tmp_path):
    (tmp_path / ".opencode" / "skills" / "alpha").mkdir(parents=True)
    (tmp_path / ".opencode" / "skills" / "alpha" / "SKILL.md").write_text("theirs\n")
    with pytest.raises(extensions.ExtensionError, match="not Sage's"):
        extensions.add(tmp_path, _skill("alpha"))
    assert (tmp_path / ".opencode" / "skills" / "alpha" / "SKILL.md").read_text() == "theirs\n"


def test_removing_takes_away_exactly_what_the_skill_owned(tmp_path):
    extensions.add(tmp_path, _skill("alpha"))
    extensions.add(tmp_path, _skill("beta"))
    assert extensions.remove(tmp_path, "skill:alpha") is True
    assert extensions.remove(tmp_path, "skill:alpha") is False
    assert not (tmp_path / ".opencode" / "skills" / "alpha").exists()
    assert (tmp_path / ".opencode" / "skills" / "beta" / "SKILL.md").exists()
    assert [e["id"] for e in extensions.read_manifest(tmp_path)] == ["skill:beta"]


def test_a_tool_or_mcp_server_left_in_an_old_manifest_is_not_listed(tmp_path):
    """Written before #638 took them out: neither is listed, switched, or offered by name."""
    extensions.add(tmp_path, _skill("alpha"))
    path = tmp_path / extensions.MANIFEST
    body = json.loads(path.read_text())
    body["extensions"] += [{"id": "tool:lookup", "kind": "tool", "name": "lookup"},
                           {"id": "mcp:crm", "kind": "mcp", "name": "crm"}]
    path.write_text(json.dumps(body))
    assert [e["id"] for e in extensions.list_extensions(tmp_path)] == ["skill:alpha"]
    assert extensions.load_catalog(tmp_path).skills == {"alpha": "skill:alpha"}


# ---- the shim ----------------------------------------------------------------------------------

def _catalog(tmp_path: Path) -> extensions.ExtensionCatalog:
    extensions.add(tmp_path, _skill("alpha"))
    extensions.add(tmp_path, _skill("beta"))
    return extensions.load_catalog(tmp_path)


OFFERED = ("read", "edit", "lookup", "push", "push_many", "crm_search", "crm_update", "crm_other")
SYSTEM = ("Skills provide specialized instructions.\n<available_skills>\n"
          "  <skill>\n    <name>alpha</name>\n    <description>A.</description>\n  </skill>\n"
          "  <skill>\n    <name>beta</name>\n    <description>B.</description>\n  </skill>\n"
          "</available_skills>\n")


def _sent(control: ModelControl, system=SYSTEM, user="hi") -> tuple[list[str], dict]:
    gateway = FakeGatewayClient()
    shim = EnforcementShim(control, CATALOG, gateway)
    request = {"messages": [{"role": "system", "content": system},
                            {"role": "user", "content": user}],
               "tools": [{"type": "function", "function": {"name": n}} for n in OFFERED]}
    list(shim.handle(request, project="p"))
    sent = gateway.seen[-1][0]
    return [t["function"]["name"] for t in sent.get("tools", [])], sent


def test_project_skills_leave_the_tool_list_as_it_was(tmp_path):
    control = ModelControl(mode=Mode.IMPLEMENT, phase=Phase.IMPLEMENT)
    control.set_extensions(_catalog(tmp_path))
    names, sent = _sent(control)
    assert names == list(OFFERED)
    # Every skill still listed; the line after the block is #620's "Sage's instructions win".
    assert sent["messages"][0]["content"].startswith(SYSTEM.rstrip("\n"))
    assert "This Project added these skills: alpha, beta." in sent["messages"][0]["content"]


def test_a_switched_off_skill_is_hidden_from_available_skills(tmp_path):
    control = ModelControl(mode=Mode.IMPLEMENT, phase=Phase.IMPLEMENT)
    control.set_extensions(_catalog(tmp_path))
    control.arm_extensions_off(frozenset({"skill:beta"}))
    _, sent = _sent(control)
    system = sent["messages"][0]["content"]
    assert "<name>alpha</name>" in system and "<name>beta</name>" not in system
    assert system.count("<skill>") == 1 and "</available_skills>" in system


NAMED = "Load each with the skill tool before answering"


def test_a_skill_named_with_at_is_loaded_and_beats_its_switch_for_that_turn(tmp_path):
    """#628: the person named it, so it is offered and asked for even while switched off."""
    control = ModelControl(mode=Mode.IMPLEMENT, phase=Phase.IMPLEMENT)
    control.set_extensions(_catalog(tmp_path))
    control.arm_extensions_off(frozenset({"skill:beta"}))
    system = _sent(control, user="Summarize revenue using @beta.")[1]["messages"][0]["content"]
    assert "<name>alpha</name>" in system and "<name>beta</name>" in system
    assert f"The person named these skills with @: beta. {NAMED}" in system
    # The switch itself is untouched: the next turn, which names nothing, hides it again.
    assert "<name>beta</name>" not in _sent(control)[1]["messages"][0]["content"]


@pytest.mark.parametrize("user", ["mail bob@alpha.com", "use @alphabet", "use @alpha-x",
                                  [{"type": "text", "text": "no mention"}]])
def test_only_a_whole_at_token_names_a_skill(tmp_path, user):
    control = ModelControl(mode=Mode.IMPLEMENT, phase=Phase.IMPLEMENT)
    control.set_extensions(_catalog(tmp_path))
    assert NAMED not in _sent(control, user=user)[1]["messages"][0]["content"]


def test_a_mention_in_any_text_part_of_the_prompt_counts(tmp_path):
    control = ModelControl(mode=Mode.IMPLEMENT, phase=Phase.IMPLEMENT)
    control.set_extensions(_catalog(tmp_path))
    parts = [{"type": "text", "text": "context"}, {"type": "text", "text": "go, @alpha"}]
    assert "with @: alpha." in _sent(control, user=parts)[1]["messages"][0]["content"]


def test_the_switch_is_per_turn_and_drops_with_its_token(tmp_path):
    control = ModelControl(mode=Mode.IMPLEMENT, phase=Phase.IMPLEMENT)
    control.set_extensions(_catalog(tmp_path))
    token = control.arm_extensions_off(frozenset({"skill:beta"}))
    control.disarm_extensions_off(object())  # a stale disarm is a no-op
    assert control.snapshot().extensions_off == {"skill:beta"}
    control.disarm_extensions_off(token)
    assert "<name>beta</name>" in _sent(control)[1]["messages"][0]["content"]


# ---- the orchestrator: reload, never under a turn ----------------------------------------------

def test_an_added_extension_reloads_chat_and_every_app_and_reaches_the_shim(tmp_path):
    orch, oc, _ = _build(tmp_path, [])
    orch.create_app(stack="react-vite")
    project = orch.project(start_preview=False)
    root = Path(project.record.path)

    orch.add_extension(_skill("alpha"))

    apps = {str(orch._wm.app_workspace(orch._project_id, a).path) for a in orch._wm.app_ids()}
    assert len(apps) == 2
    assert set(oc.disposed) == {str(root / ".sage" / "chat-work")} | apps
    assert project.control.snapshot().extensions.skills == {"alpha": "skill:alpha"}

    orch.remove_extension("skill:alpha")
    assert len(oc.disposed) == 6
    assert not project.control.snapshot().extensions


def test_an_extension_added_mid_turn_does_not_dispose_under_that_turn(tmp_path):
    orch, oc, _ = _build(tmp_path, [])
    project = orch.project(start_preview=False)
    orch._turn_lock.acquire()
    try:
        orch.add_extension(_skill("alpha"))
        assert oc.disposed == []
        assert not project.control.snapshot().extensions
    finally:
        orch._turn_lock.release()
    waiter = orch._extensions_waiter
    if waiter is not None:
        waiter.join(timeout=5)
    assert oc.disposed and project.control.snapshot().extensions.skills == {"alpha": "skill:alpha"}


# ---- the orchestrator: the Thread and the App decide ------------------------------------------

def test_a_chat_turn_carries_what_its_thread_switched_off(tmp_path):
    orch, oc = _chat_orch(tmp_path, [Turn(text="ok"), Turn(text="ok")],
                          gateway=IntentGateway({"label": "other_chat", "confidence": 0.9}),
                          client=ObservedControlOpenCode)
    project = orch.project(start_preview=False)
    oc.control = project.control
    orch.add_extension(_skill("alpha"))
    off, on = orch.create_thread()["id"], orch.create_thread()["id"]
    orch.set_extension_enabled("skill:alpha", False, thread=off)

    list(orch.chat_stream(off, "hello there"))
    first = len(oc.snapshots)
    list(orch.chat_stream(on, "hello there"))

    assert first and {s.extensions_off for s in oc.snapshots[:first]} == {frozenset({"skill:alpha"})}
    assert {s.extensions_off for s in oc.snapshots[first:]} == {frozenset()}
    assert project.control.snapshot().extensions_off == frozenset()


def test_a_build_turn_carries_what_its_app_switched_off(tmp_path):
    orch, oc, _ = _build(tmp_path, [Turn(text="It shows sales.")])
    orch.create_app(stack="react-vite")
    project = orch.project(start_preview=False)
    seen = []
    send = oc.send_prompt
    oc.send_prompt = lambda *a, **k: (seen.append(project.control.snapshot().extensions_off),
                                      send(*a, **k))[1]
    orch.add_extension(_skill("push"))
    orch.set_extension_enabled("skill:push", False, app=project.app_for_turn().app_id)
    project.control.set_mode(Mode.ASK)

    list(orch.build_stream("What does this app show?"))

    assert seen == [{"skill:push"}]
    assert project.control.snapshot().extensions_off == frozenset()


def test_a_chat_and_a_build_prompt_reach_opencode_with_their_at_mention_intact(tmp_path):
    """#628: the shim reads `@<skill>` off the prompt OpenCode sends, so both doors must hand the
    token through as typed."""
    orch, oc = _chat_orch(tmp_path, [Turn(text="ok")],
                          gateway=IntentGateway({"label": "other_chat", "confidence": 0.9}),
                          client=ObservedControlOpenCode)
    oc.control = orch.project(start_preview=False).control
    list(orch.chat_stream(orch.create_thread()["id"], "Summarize revenue using @alpha please"))
    assert any("@alpha please" in p["text"] for p in oc.prompts)

    orch, oc, _ = _build(tmp_path / "b", [Turn(text="It shows sales.")])
    orch.create_app(stack="react-vite")
    orch.project(start_preview=False).control.set_mode(Mode.ASK)
    list(orch.build_stream("What does this app show? Use @alpha"))
    assert any("Use @alpha" in p["text"] for p in oc.prompts)


REACT_AGENTS = (Path(__file__).resolve().parents[2] / "template" / "react-vite" / "AGENTS.md")
HOUSE_STYLE = "---\nname: house-style\ndescription: Acme's design system.\n---\nUse Acme red.\n"


def _next_build_instructions(tmp_path, *, off_for_app: bool) -> str:
    """The system instructions the shim sends on an implement call, under what the orchestrator
    armed for the App's next Build turn."""
    orch, oc, _ = _build(tmp_path, [Turn(text="It shows sales.")])
    orch.create_app(stack="react-vite")
    project = orch.project(start_preview=False)
    orch.add_skills([{"SKILL.md": HOUSE_STYLE}], replaces="design", source={"type": "upload"})
    if off_for_app:
        orch.set_extension_enabled("skill:house-style", False, app=project.app_for_turn().app_id)
    armed = []
    send = oc.send_prompt
    oc.send_prompt = lambda *a, **k: (armed.append(project.control.snapshot()), send(*a, **k))[1]
    project.control.set_mode(Mode.ASK)
    list(orch.build_stream("What does this app show?"))

    control = ModelControl(mode=Mode.IMPLEMENT, phase=Phase.IMPLEMENT)
    control.set_extensions(armed[0].extensions)
    control.arm_extensions_off(armed[0].extensions_off)
    return _sent(control, REACT_AGENTS.read_text())[1]["messages"][0]["content"]


def test_a_skill_replacing_design_leaves_the_guardrail_and_drops_the_style_rules(tmp_path):
    system = _next_build_instructions(tmp_path, off_for_app=False)
    assert "Never hardcode hex values" in system and "falls back to a system font" in system
    assert "## Design system" not in system and "One clear primary action" not in system
    assert "### The {platformName} API" in system


def test_switching_it_off_for_the_app_brings_the_style_rules_back(tmp_path):
    system = _next_build_instructions(tmp_path, off_for_app=True)
    assert "Never hardcode hex values" in system and "falls back to a system font" in system
    assert "## Design system" in system and "One clear primary action" in system


def test_the_switch_refuses_an_unknown_extension_thread_or_app(tmp_path):
    orch, _, _ = _build(tmp_path, [])
    orch.add_extension(_skill("alpha"))
    with pytest.raises(KeyError):
        orch.set_extension_enabled("skill:nope", False, thread=orch.create_thread()["id"])
    with pytest.raises(KeyError):
        orch.set_extension_enabled("skill:alpha", False, thread="thr_missing")
    with pytest.raises(KeyError):
        orch.set_extension_enabled("skill:alpha", False, app="app_missing")
    with pytest.raises(ValueError):
        orch.set_extension_enabled("skill:alpha", False)


# ---- /api/project/extensions -----------------------------------------------------------------

def test_the_routes_add_list_switch_and_remove(tmp_path, monkeypatch):
    import sage.orchestrator.app as app_module

    orch, _, _ = _build(tmp_path, [])
    monkeypatch.setattr(app_module, "orchestrator", orch)
    client = TestClient(app_module.control_app)
    thread = orch.create_thread()["id"]

    assert client.post("/api/project/extensions", json=_skill("alpha")).json()["item"]["id"] == \
        "skill:alpha"
    assert client.post("/api/project/extensions", json=_skill("alpha")).status_code == 400
    assert [e["id"] for e in client.get("/api/project/extensions").json()["items"]] == ["skill:alpha"]
    assert client.put("/api/project/extensions/skill:alpha/enabled",
                      json={"thread": thread, "enabled": False}).json() == {"ok": True}
    assert client.put("/api/project/extensions/skill:nope/enabled",
                      json={"thread": thread, "enabled": False}).status_code == 404
    assert client.delete("/api/project/extensions/skill:alpha").json() == {"removed": True}
    assert client.delete("/api/project/extensions/skill:alpha").status_code == 404
