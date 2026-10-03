"""A Project's own skills, tools and MCP servers reach the next turn, as its Thread or App allows (#619).

ADR-0071. The files go in OpenCode's project slot, `.opencode/` at the Project root; OpenCode reads
them once per directory, so Sage disposes the instance after a change — never under a running turn.
The shim then decides per turn: a Thread or App can switch an extension off, and a read-only turn
gets only the tools marked read-only.
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
from sage.workspace.stack import STACKS

from .fake_opencode import Turn
from .test_chat_turn import IntentGateway, ObservedControlOpenCode
from .test_chat_turn import _orch as _chat_orch
from .test_no_edit_recovery_uses_the_active_stack import (
    ScriptedFeedback,
    _no_waiting,  # noqa: F401  (autouse)
)
from .test_turn_path import _build

CATALOG = ModelCatalog(
    sovereign_plan="sovereign-8b", sovereign_implement="sovereign-8b", sovereign_ask="sovereign-8b",
    plan="strong-vendor", implement="cheap-vendor", ask="ask-vendor",
)

SKILL = "---\nname: {name}\ndescription: A test skill.\n---\nDo the thing.\n"
TOOL_TS = "export default { description: 'x', args: {}, async execute() { return 'ok' } }\n"


def _skill(name: str) -> dict:
    return {"kind": "skill", "name": name, "files": {"SKILL.md": SKILL.format(name=name)}}


# ---- the store ---------------------------------------------------------------------------------

def test_each_kind_lands_in_the_project_slot_and_the_manifest(tmp_path):
    extensions.add(tmp_path, {**_skill("alpha"),
                              "files": {"SKILL.md": SKILL.format(name="alpha"), "ref/a.md": "A"}})
    extensions.add(tmp_path, {"kind": "tool", "name": "lookup", "code": TOOL_TS, "readOnly": True})
    extensions.add(tmp_path, {"kind": "mcp", "name": "crm",
                              "config": {"type": "remote", "url": "https://crm.example/mcp",
                                         "headers": {"Authorization": "Bearer {env:CRM_TOKEN}"}},
                              "tools": {"search": True, "update": False}})

    slot = tmp_path / ".opencode"
    assert (slot / "skills" / "alpha" / "SKILL.md").read_text().startswith("---\nname: alpha")
    assert (slot / "skills" / "alpha" / "ref" / "a.md").read_text() == "A"
    assert (slot / "tools" / "lookup.ts").read_text() == TOOL_TS
    mcp = json.loads((slot / "opencode.json").read_text())["mcp"]
    assert mcp == {"crm": {"type": "remote", "url": "https://crm.example/mcp",
                           "headers": {"Authorization": "Bearer {env:CRM_TOKEN}"}}}
    entries = {e["id"]: e for e in extensions.read_manifest(tmp_path)}
    assert set(entries) == {"skill:alpha", "tool:lookup", "mcp:crm"}
    assert entries["skill:alpha"]["files"] == [".opencode/skills/alpha/SKILL.md",
                                               ".opencode/skills/alpha/ref/a.md"]
    assert entries["tool:lookup"]["readOnly"] is True
    assert entries["mcp:crm"]["tools"] == {"search": True, "update": False}
    assert all(e["defaultEnabled"] is True for e in entries.values())


@pytest.mark.parametrize(("body", "said"), [
    (_skill("sage-helper"), "sage-"),
    ({"kind": "mcp", "name": "sage_live", "config": {"type": "remote", "url": "u"}}, "sage-"),
    ({"kind": "tool", "name": "bash", "code": TOOL_TS}, "built-in"),
    ({"kind": "tool", "name": "live_read", "code": TOOL_TS}, "built-in"),
    (_skill("Bad Name"), "lowercase"),
    ({"kind": "skill", "name": "esc",
      "files": {"SKILL.md": SKILL.format(name="esc"), "../../etc/x": "y"}}, "not a path"),
    (_skill("data-table"), "Sage ships"),
    ({"kind": "skill", "name": "alpha", "files": {"SKILL.md": SKILL.format(name="sage-x")}},
     "frontmatter"),
    ({"kind": "mcp", "name": "crm", "config": {"type": "remote", "url": "u"}, "tools": ["x"]},
     "read-only"),
    ({"kind": "agent", "name": "x"}, "kind"),
])
def test_a_name_or_path_sage_must_not_write_is_refused_at_the_door(tmp_path, body, said):
    with pytest.raises(extensions.ExtensionError, match=said):
        extensions.add(tmp_path, body)
    assert not (tmp_path / ".opencode").exists()


def test_tools_and_mcp_servers_share_one_namespace(tmp_path):
    """Both are matched by `<name>` and `<name>_`, so one must not sit under the other's prefix."""
    extensions.add(tmp_path, {"kind": "mcp", "name": "crm", "config": {"type": "remote", "url": "u"}})
    with pytest.raises(extensions.ExtensionError, match="clashes"):
        extensions.add(tmp_path, {"kind": "tool", "name": "crm_search", "code": TOOL_TS})
    with pytest.raises(extensions.ExtensionError, match="clashes"):
        extensions.add(tmp_path, {"kind": "tool", "name": "crm", "code": TOOL_TS})
    extensions.add(tmp_path, _skill("crm"))  # skills are a separate namespace


def test_a_file_sage_did_not_write_is_never_overwritten(tmp_path):
    (tmp_path / ".opencode" / "tools").mkdir(parents=True)
    (tmp_path / ".opencode" / "tools" / "mine.ts").write_text("// theirs\n")
    with pytest.raises(extensions.ExtensionError, match="not Sage's"):
        extensions.add(tmp_path, {"kind": "tool", "name": "mine", "code": TOOL_TS})
    assert (tmp_path / ".opencode" / "tools" / "mine.ts").read_text() == "// theirs\n"


def test_removing_takes_away_exactly_what_the_extension_owned(tmp_path):
    (tmp_path / ".opencode").mkdir()
    (tmp_path / ".opencode" / "opencode.json").write_text(json.dumps(
        {"mcp": {"theirs": {"type": "local", "command": ["x"]}}}))
    extensions.add(tmp_path, _skill("alpha"))
    extensions.add(tmp_path, {"kind": "tool", "name": "lookup", "code": TOOL_TS})
    extensions.add(tmp_path, {"kind": "mcp", "name": "crm", "config": {"type": "remote", "url": "u"}})

    for ext_id in ("skill:alpha", "tool:lookup", "mcp:crm"):
        assert extensions.remove(tmp_path, ext_id) is True
    assert extensions.remove(tmp_path, "skill:alpha") is False

    assert not (tmp_path / ".opencode" / "skills" / "alpha").exists()
    assert not (tmp_path / ".opencode" / "tools" / "lookup.ts").exists()
    assert json.loads((tmp_path / ".opencode" / "opencode.json").read_text())["mcp"] == {
        "theirs": {"type": "local", "command": ["x"]}}
    assert extensions.read_manifest(tmp_path) == []


def test_removing_the_last_mcp_server_leaves_no_empty_block(tmp_path):
    extensions.add(tmp_path, {"kind": "mcp", "name": "crm", "config": {"type": "remote", "url": "u"}})
    extensions.remove(tmp_path, "mcp:crm")
    assert not (tmp_path / ".opencode" / "opencode.json").exists()


# ---- the shim ----------------------------------------------------------------------------------

def _catalog(tmp_path: Path) -> extensions.ExtensionCatalog:
    extensions.add(tmp_path, {"kind": "tool", "name": "lookup", "code": TOOL_TS, "readOnly": True})
    extensions.add(tmp_path, {"kind": "tool", "name": "push", "code": TOOL_TS})
    extensions.add(tmp_path, {"kind": "mcp", "name": "crm", "config": {"type": "remote", "url": "u"},
                              "tools": {"search": True, "update": False}})
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


def test_every_enabled_extension_tool_reaches_an_ordinary_turn(tmp_path):
    control = ModelControl(mode=Mode.IMPLEMENT, phase=Phase.IMPLEMENT)
    control.set_extensions(_catalog(tmp_path))
    names, sent = _sent(control)
    assert names == list(OFFERED)
    # Every skill still listed; the line after the block is #620's "Sage's instructions win".
    assert sent["messages"][0]["content"].startswith(SYSTEM.rstrip("\n"))
    assert "This Project added these skills: alpha, beta." in sent["messages"][0]["content"]


def test_a_switched_off_tool_or_server_is_not_offered(tmp_path):
    control = ModelControl(mode=Mode.IMPLEMENT, phase=Phase.IMPLEMENT)
    control.set_extensions(_catalog(tmp_path))
    control.arm_extensions_off(frozenset({"tool:push", "mcp:crm"}))
    names, _ = _sent(control)
    assert names == ["read", "edit", "lookup"]


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


@pytest.mark.parametrize("user, offered, said", [
    ("Push it with @push.", ["read", "lookup", "push", "push_many"],
     "The person named these tools with @: push. Call them to answer."),
    ("Ask @crm who owns it.", ["read", "lookup", "crm_search", "crm_update", "crm_other"],
     "The person named these MCP servers with @: crm. Use their tools to answer."),
])
def test_a_tool_or_server_named_with_at_beats_its_switch_and_the_read_only_rule(
        tmp_path, user, offered, said):
    """#633: both switched off, on a read-only turn; named, each is offered and asked for — for
    that turn only."""
    control = ModelControl(mode=Mode.AUTO)
    control.set_extensions(_catalog(tmp_path))
    control.arm_read_only("question")
    control.arm_extensions_off(frozenset({"tool:push", "mcp:crm"}))
    names, sent = _sent(control, user=user)
    assert names == offered
    assert said in sent["messages"][0]["content"]
    names, sent = _sent(control)
    assert names == ["read", "lookup"] and "with @:" not in sent["messages"][0]["content"]


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
    token = control.arm_extensions_off(frozenset({"tool:push"}))
    control.disarm_extensions_off(object())  # a stale disarm is a no-op
    assert control.snapshot().extensions_off == {"tool:push"}
    control.disarm_extensions_off(token)
    assert "push" in _sent(control)[0]


@pytest.mark.parametrize("arm", ["ask", "plan", "question"])
def test_a_read_only_turn_gets_only_tools_marked_read_only(tmp_path, arm):
    """Ask, a gated plan and an answer-only turn. An MCP tool the manifest does not list
    (`crm_other`) and an export under a tool's prefix (`push_many`) are not marked read-only."""
    control = ModelControl(mode=Mode.ASK if arm == "ask" else Mode.AUTO)
    control.set_extensions(_catalog(tmp_path))
    if arm != "ask":
        control.arm_read_only(arm)
    names, _ = _sent(control)
    assert names == ["read", "lookup", "crm_search"]


def test_a_data_artifact_turn_keeps_its_enabled_extension_tools(tmp_path):
    """That lane is an allowlist, which otherwise strips any tool it does not name, silently."""
    control = ModelControl(mode=Mode.IMPLEMENT, phase=Phase.IMPLEMENT)
    control.set_extensions(_catalog(tmp_path))
    control.arm_chat("thr_1")
    control.arm_chat_artifact()
    control.arm_extensions_off(frozenset({"tool:push"}))
    names, _ = _sent(control)
    assert names == ["read", "lookup", "crm_search", "crm_update", "crm_other"]


# ---- the orchestrator: reload, never under a turn ----------------------------------------------

def test_an_added_extension_reloads_chat_and_every_app_and_reaches_the_shim(tmp_path):
    orch, oc, _ = _build(tmp_path, [])
    orch.create_app(stack="react-vite")
    project = orch.project(start_preview=False)
    root = Path(project.record.path)

    orch.add_extension({"kind": "tool", "name": "lookup", "code": TOOL_TS, "readOnly": True})

    apps = {str(orch._wm.app_workspace(orch._project_id, a).path) for a in orch._wm.app_ids()}
    assert len(apps) == 2
    assert set(oc.disposed) == {str(root / ".sage" / "chat-work")} | apps
    assert project.control.snapshot().extensions.owner("lookup") == ("tool:lookup", True)

    orch.remove_extension("tool:lookup")
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
    orch.add_extension({"kind": "tool", "name": "lookup", "code": TOOL_TS})
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
    orch.add_extension({"kind": "tool", "name": "push", "code": TOOL_TS})
    orch.set_extension_enabled("tool:push", False, app=project.app_for_turn().app_id)
    project.control.set_mode(Mode.ASK)

    list(orch.build_stream("What does this app show?"))

    assert seen == [{"tool:push"}]
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


# ---- a reverted read-only turn names the tool -------------------------------------------------

@pytest.mark.parametrize("read_only_kind", ["gate", "answer"])
def test_a_reverted_read_only_turn_names_the_extension_tool_that_ran(tmp_path, read_only_kind):
    stack = STACKS["react-vite"]
    turn = Turn(text="# Plan\n\n## Plan\n1. Do the work.", tools=["lookup"],
                writes={stack.entry_file: "// forbidden write\n"})
    orch, _, _ = _build(tmp_path, [turn])
    orch._feedback = ScriptedFeedback(["introduced"])
    orch.create_app(stack="react-vite")
    project = orch.project(start_preview=False)
    project.control.pick("a", "low")
    orch.add_extension({"kind": "tool", "name": "lookup", "code": TOOL_TS, "readOnly": True})
    if read_only_kind == "answer":
        project.control.set_mode(Mode.ASK)
    prompt = "Build a sales dashboard." if read_only_kind == "gate" else "What does this app show?"

    events = list(orch.build_stream(prompt))

    error = next(e for e in events if e["type"] == "error")
    assert "your tool `lookup` edited files" in error["message"]
    assert "the agent" not in error["message"]


# ---- /api/project/extensions and /api/diag -----------------------------------------------------

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


def _diag(tmp_path, monkeypatch) -> dict:
    import sage.orchestrator.app as app_module

    monkeypatch.setattr(app_module.orchestrator, "_wm",
                        type("W", (), {"_dir": tmp_path})(), raising=False)
    monkeypatch.setattr(app_module.orchestrator, "_opencode_log_tail", lambda n=30: [], raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home" / ".config" / "opencode").mkdir(parents=True)
    return TestClient(app_module.control_app).get("/api/diag").json()["opencode_config"]


def test_an_mcp_server_in_the_manifest_does_not_read_as_shadowing(tmp_path, monkeypatch):
    extensions.add(tmp_path, {"kind": "mcp", "name": "crm", "config": {"type": "remote", "url": "u"}})
    assert _diag(tmp_path, monkeypatch)["shadowing_mcp"] == []


@pytest.mark.parametrize("planted", ["sage-live-read", "theirs"])
def test_a_reserved_or_unrecorded_server_beside_them_still_does(tmp_path, monkeypatch, planted):
    extensions.add(tmp_path, {"kind": "mcp", "name": "crm", "config": {"type": "remote", "url": "u"}})
    path = tmp_path / ".opencode" / "opencode.json"
    config = json.loads(path.read_text())
    config["mcp"][planted] = {"type": "remote", "url": "x"}
    path.write_text(json.dumps(config))
    assert _diag(tmp_path, monkeypatch)["shadowing_mcp"] == [str(path)]
