"""A Project skill named with @ is put into the turn, and the turn's record says so (#737).

Before this a mention only added a sentence asking the model to load the skill, and nothing
recorded whether it did: Haiku answered a demo prompt that named `revops-conventions` while
breaking the rule the skill states, and no surface could say whether the skill was ever read. Now
the shim puts the skill's SKILL.md into every model request of the turn, and the `done` row carries
`skills`, which names each one and whether its text went out.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from sage import extensions
from sage.gateway.client import FakeGatewayClient
from sage.router.model_control import ModelControl
from sage.router.models import Mode, Phase
from sage.shim.enforcement import EnforcementShim

from .fake_opencode import FakeOpenCode, Turn
from .test_a_projects_own_extensions_reach_the_next_turn import CATALOG, SYSTEM
from .test_chat_turn import IntentGateway
from .test_chat_turn import _orch as _chat_orch
from .test_no_edit_recovery_uses_the_active_stack import (
    _no_waiting,  # noqa: F401  (autouse)
)
from .test_turn_path import _build

RULE = "Weighted pipeline: AMOUNT * PROBABILITY / 100."
SKILL = "---\nname: {name}\ndescription: A test skill.\n---\n{body}\n"


def _skill(name: str, body: str = RULE) -> dict:
    return {"kind": "skill", "name": name, "files": {"SKILL.md": SKILL.format(name=name, body=body)}}


def _control(tmp_path: Path, *bodies: tuple[str, str]) -> ModelControl:
    for name, body in bodies or (("alpha", RULE), ("beta", "Beta rule.")):
        extensions.add(tmp_path, _skill(name, body))
    control = ModelControl(mode=Mode.IMPLEMENT, phase=Phase.IMPLEMENT)
    control.set_extensions(extensions.load_catalog(tmp_path))
    return control


def _sent(control: ModelControl, user="hi") -> dict:
    gateway = FakeGatewayClient()
    request = {"messages": [{"role": "system", "content": SYSTEM},
                            {"role": "user", "content": user}]}
    list(EnforcementShim(control, CATALOG, gateway).handle(request, project="p"))
    return gateway.seen[-1][0]


def _user_text(sent: dict) -> str:
    content = sent["messages"][-1]["content"]
    return content if isinstance(content, str) else " ".join(
        p.get("text", "") for p in content if isinstance(p, dict))


# ---- delivery ----------------------------------------------------------------------------------

def _turn(control: ModelControl, prompt, off=frozenset()) -> dict:
    """One turn's first request, armed the way Chat and Build arm it: from the person's prompt."""
    text = prompt if isinstance(prompt, str) else " ".join(p["text"] for p in prompt)
    control.arm_extensions_off(frozenset(off), text)
    return _sent(control, prompt)


def test_a_named_skill_rides_the_request_and_is_recorded(tmp_path):
    control = _control(tmp_path)
    sent = _turn(control, "Chart pipeline and follow @alpha.")
    text = _user_text(sent)
    assert RULE in text and "Beta rule." not in text
    # Sage's instructions still win, and the model is not asked to fetch what it already has.
    assert "does not override Sage's instructions" in text
    assert "Load each with the skill tool before answering" not in sent["messages"][0]["content"]
    assert control.skills_sent() == [{"name": "alpha", "sent": True,
                                      "bytes": len(SKILL.format(name="alpha", body=RULE)),
                                      "cut": False}]


def test_a_skill_switched_off_is_still_given_when_named(tmp_path):
    """#628: a mention beats the switch, so the text goes too."""
    control = _control(tmp_path)
    sent = _turn(control, "use @beta", off={"skill:beta"})
    assert "Beta rule." in _user_text(sent) and "<name>beta</name>" in sent["messages"][0]["content"]
    assert [s["name"] for s in control.skills_sent()] == ["beta"]


def test_an_unknown_name_gives_nothing_and_records_nothing(tmp_path):
    control = _control(tmp_path)
    text = _user_text(_turn(control, "use @nope, @alphabet and mail bob@alpha.com"))
    assert RULE not in text and "<project_skill" not in text
    assert control.skills_sent() == []


def test_a_skill_named_twice_is_given_once(tmp_path):
    control = _control(tmp_path)
    text = _user_text(_turn(control, "use @alpha, and again @alpha"))
    assert text.count(RULE) == 1
    assert [s["name"] for s in control.skills_sent()] == ["alpha"]


def test_two_named_skills_are_both_given(tmp_path):
    control = _control(tmp_path)
    text = _user_text(_turn(control, [{"type": "text", "text": "use @beta"},
                                      {"type": "text", "text": "and @alpha"}]))
    assert RULE in text and "Beta rule." in text
    assert [s["name"] for s in control.skills_sent()] == ["alpha", "beta"]


def test_a_very_large_skill_md_is_cut_at_the_bound(tmp_path):
    body = "x" * (extensions.INLINE_SKILL_BYTES * 3)
    control = _control(tmp_path, ("big", body))
    text = _user_text(_turn(control, "use @big"))
    given = text.count("x")
    assert extensions.INLINE_SKILL_BYTES - 100 < given <= extensions.INLINE_SKILL_BYTES
    assert "load it with the skill tool for the rest" in text
    [record] = control.skills_sent()
    assert record["cut"] is True and record["bytes"] == len(SKILL.format(name="big", body=body))


def test_a_skill_whose_text_cannot_be_read_is_recorded_as_not_sent(tmp_path):
    control = _control(tmp_path)
    (tmp_path / ".opencode" / "skills" / "alpha" / "SKILL.md").unlink()
    control.set_extensions(extensions.load_catalog(tmp_path))
    sent = _turn(control, "use @alpha")
    assert RULE not in _user_text(sent)
    # The old ask is the fallback: OpenCode may still find the skill.
    assert "Load each with the skill tool" in sent["messages"][0]["content"]
    assert control.skills_sent() == [{"name": "alpha", "sent": False}]


def test_every_request_of_the_turn_carries_it_and_the_next_turn_does_not(tmp_path):
    """A nudge is a new user message without the mention; it is still this turn."""
    control = _control(tmp_path)
    _turn(control, "use @alpha")
    assert RULE in _user_text(_sent(control, "Keep going."))
    token = control.arm_extensions_off(frozenset(), "Keep going.")
    assert control.skills_sent() == []
    assert RULE not in _user_text(_sent(control, "Keep going."))
    control.disarm_extensions_off(token)
    assert control.skills_sent() == []


def test_a_mention_from_an_earlier_turn_in_the_prompt_is_not_this_turns(tmp_path):
    """Chat's prompt carries the conversation so far; only the person's sentence names."""
    control = _control(tmp_path)
    text = _user_text(_turn(control, "Now chart it by team."))
    assert RULE not in _user_text(_sent(control, "Earlier: use @alpha\n\nNow chart it by team."))
    assert RULE not in text and control.skills_sent() == []


# ---- the record, on both lanes -----------------------------------------------------------------

class _ShimOpenCode(FakeOpenCode):
    """Each prompt goes through the project's shim as one model request, as OpenCode's would."""

    def __init__(self, workspace: Path, turns: list[Turn] | None = None) -> None:
        super().__init__(workspace, turns)
        self.shim = None
        self.outgoing: list[dict] = []

    def send_prompt(self, session_id, text, *args, tail: str = "", **kwargs) -> None:
        if self.shim is not None:
            request = {"messages": [{"role": "system", "content": SYSTEM},
                                    {"role": "user", "content": text + tail}]}
            self.outgoing.append(self.shim.prepare(request, "p")[0])
        super().send_prompt(session_id, text, *args, tail=tail, **kwargs)


def _done_rows(history: list[dict]) -> list[dict]:
    return [row for row in history if row.get("type") == "done"]


def test_a_chat_turn_that_names_a_skill_gives_it_and_its_done_row_says_so(tmp_path):
    orch, oc = _chat_orch(tmp_path, [Turn(text="ok"), Turn(text="ok")],
                          gateway=IntentGateway({"label": "other_chat", "confidence": 0.9}),
                          client=_ShimOpenCode)
    project = orch.project(start_preview=False)
    orch.add_extension(_skill("alpha"))
    oc.shim = project.shim
    thread = orch.create_thread()["id"]

    list(orch.chat_stream(thread, "Chart pipeline and follow @alpha"))
    assert oc.outgoing and all(RULE in _user_text(r) for r in oc.outgoing)
    [done] = _done_rows(orch.thread_history(thread))
    assert [(s["name"], s["sent"]) for s in done["skills"]] == [("alpha", True)]

    list(orch.chat_stream(thread, "And now without it"))
    assert "skills" not in _done_rows(orch.thread_history(thread))[-1]


def test_a_build_turn_that_names_a_skill_gives_it_and_its_done_row_says_so(tmp_path):
    orch, oc, _ = _build(tmp_path, [Turn(text="It shows sales.")], fake=_ShimOpenCode)
    orch.create_app(stack="react-vite")
    project = orch.project(start_preview=False)
    orch.add_extension(_skill("alpha"))
    oc.shim = project.shim
    project.control.set_mode(Mode.ASK)

    events = list(orch.build_stream("What does this app show? Use @alpha"))
    assert oc.outgoing and all(RULE in _user_text(r) for r in oc.outgoing)
    [done] = [e for e in events if e.get("type") == "done"]
    assert [(s["name"], s["sent"]) for s in done["skills"]] == [("alpha", True)]
    persisted = _done_rows(project.app_for_turn().read_history(project.build_conversation))
    assert persisted[-1]["skills"] == done["skills"]
    assert project.control.skills_sent() == []



# ---- the Workbench draws it -------------------------------------------------------------------

_HARNESS = Path(__file__).resolve().parent / "js" / "skills_sent_harness.mjs"
_needs_node = pytest.mark.skipif(shutil.which("node") is None,
                                 reason="node is not on PATH (it is in the Sage image)")


def _drawn(lane: str, skills: list[dict] | None) -> list[dict]:
    done = {"type": "done", "ok": True, "decision": "answered", **({"skills": skills} if skills else {})}
    history = [{"type": "user", "text": "Chart pipeline and follow @revops-conventions"},
               {"type": "agent", "kind": "text", "text": "Weighted pipeline is $1.2M."}, done]
    out = subprocess.run(["node", str(_HARNESS)],
                         input=json.dumps({"lane": lane, "history": history, "done": done}),
                         capture_output=True, text=True, timeout=60, check=False)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])["statuses"]


@_needs_node
@pytest.mark.parametrize("lane", ["chat", "build", "live"])
def test_the_turn_says_which_skills_went_to_the_model(lane):
    skills = [{"name": "big", "sent": True, "bytes": 99000, "cut": True},
              {"name": "revops-conventions", "sent": True, "bytes": 900, "cut": False}]
    assert _drawn(lane, skills) == [{
        "value": "Skills given to the model: big (its start only), revops-conventions.",
        "ok": True, "warn": False}]


@_needs_node
@pytest.mark.parametrize("lane", ["chat", "build", "live"])
def test_a_named_skill_that_was_not_given_is_said_plainly(lane):
    [line] = _drawn(lane, [{"name": "revops-conventions", "sent": False}])
    assert line["warn"] and "Named but not given" in line["value"]
    assert "revops-conventions" in line["value"]


@_needs_node
@pytest.mark.parametrize("lane", ["chat", "build", "live"])
def test_a_turn_that_named_no_skill_says_nothing_about_skills(lane):
    assert _drawn(lane, None) == []
