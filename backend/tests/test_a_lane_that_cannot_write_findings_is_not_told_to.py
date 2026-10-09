"""A Chat lane that holds no tool able to write `findings.md` is never told to write it (#733).

Demo rerun on `e9bfaea`, prompt 2: the answer opened "I cannot write to the findings file through
artifact_write, but I can provide a summary." The bounded lanes — answer-only and the artifact lane
— keep `read`, `glob`, `grep`, Live read and at most `artifact_write`, and none of those can write
`.sage/threads/<threadId>/findings.md`. Yet every one of them was told to keep findings: by the
pinned prompt, by the turn prompt's findings note, by the reserved slice at the ceiling, and by the
`investigate-weak-signals` skill the pinned prompt points at.

Keyed on the tools the request actually carries, not on a label: the class is "no tool here can
write the file", and the tool list is the one place that says so.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sage.gateway.client import FakeGatewayClient
from sage.router.model_control import ModelControl
from sage.router.models import Mode
from sage.shim.enforcement import EnforcementShim

from .fake_opencode import Turn
from .test_a_projects_own_extensions_reach_the_next_turn import CATALOG
from .test_chat_turn import IntentGateway, _orch

ROOT = Path(__file__).resolve().parents[2]
SKILL = "investigate-weak-signals"
OFFERED = ("read", "glob", "grep", "bash", "edit", "write", "skill", "live_read_query",
           "artifact_write")


def _system() -> str:
    prompt = json.loads((ROOT / "opencode.json").read_text(encoding="utf-8"))[
        "agent"]["sage-chat"]["prompt"]
    skills = "".join(f"  <skill>\n    <name>{n}</name>\n    <description>D.</description>\n"
                     "  </skill>\n" for n in (SKILL, "other-skill"))
    return f"{prompt}\n<available_skills>\n{skills}</available_skills>\n"


def _sent(arm) -> tuple[list[str], str]:
    control = ModelControl(mode=Mode.AUTO)
    control.arm_chat("thr_a")
    arm(control)
    gateway = FakeGatewayClient()
    request = {"messages": [{"role": "system", "content": _system()},
                            {"role": "user", "content": "Which competitors come up on calls?"}],
               "tools": [{"type": "function", "function": {"name": n}} for n in OFFERED]}
    list(EnforcementShim(control, CATALOG, gateway).handle(request, project="p"))
    sent = gateway.seen[-1][0]
    return [t["function"]["name"] for t in sent.get("tools", [])], sent["messages"][0]["content"]


LANES = {
    "answer only": lambda c: c.arm_read_only("question"),
    "artifact": lambda c: c.arm_chat_artifact(),
}


@pytest.mark.parametrize("lane", LANES)
def test_a_lane_with_no_writer_is_not_told_about_findings(lane):
    tools, system = _sent(LANES[lane])

    assert not {"bash", "edit", "write"} & set(tools), "the premise: this lane holds no writer"
    assert "findings" not in system.lower()
    assert f"<name>{SKILL}</name>" not in system
    # The rest of the prompt is untouched.
    assert "<name>other-skill</name>" in system
    assert "Do not write under `src/`, `public/`, or `.sage/`" in system
    assert "## Visuals" in system


def test_a_lane_that_can_write_still_keeps_findings():
    tools, system = _sent(lambda c: None)

    assert "bash" in tools
    assert system == _system()


# --- the turn prompt and the ceiling -------------------------------------------------------------

def _first_prompt(tmp_path, label: str, question: str) -> str:
    orch, oc = _orch(tmp_path, [Turn(text="Four competitors come up on calls.")],
                     gateway=IntentGateway({"label": label, "confidence": 0.92}))
    tid = orch.create_thread()["id"]
    list(orch.chat_stream(tid, question))
    return oc.prompts[0]["text"]


@pytest.mark.parametrize("label", ["data_answer", "data_artifact", "plain_answer"])
def test_a_bounded_turn_prompt_names_no_findings_file(tmp_path, label):
    said = _first_prompt(tmp_path, label, "Which competitors come up most on Gong calls?")
    assert "findings" not in said.lower()


def test_a_turn_that_can_write_is_still_told_where_findings_go(tmp_path):
    said = _first_prompt(tmp_path, "other_chat", "Fit a regression of revenue on seats in Python")
    assert "findings.md" in said


def test_a_fallback_answer_only_turn_is_not_asked_for_findings_at_the_ceiling(tmp_path, monkeypatch):
    """The label is not the lane. An unclassified plain question is armed answer-only by
    `_plain_chat_answer_only`, holds no writer, and must not be handed the findings slice."""
    from sage.orchestrator import chat_intent

    from .test_a_turn_stopped_at_the_ceiling_keeps_what_it_measured import (
        WorksUntilStopped,
        _short_ceiling,
    )
    from .test_a_turn_stopped_at_the_ceiling_keeps_what_it_measured import _orch as _ceiling_orch

    class Classified:
        def result(self):
            return chat_intent.Intent(label="other_chat", confidence=0.95)

    _short_ceiling(monkeypatch)
    monkeypatch.setattr(chat_intent, "start", lambda *a, **k: Classified())
    oc = WorksUntilStopped(tmp_path / "mnt" / "code", [Turn(text="working on it")])
    orch = _ceiling_orch(tmp_path, oc)
    project = orch.project(start_preview=False)
    seen: list[bool] = []
    real = project.control.arm_read_only

    def arm_read_only(reason=""):
        seen.append(True)
        return real(reason)

    monkeypatch.setattr(project.control, "arm_read_only", arm_read_only)
    tid = orch.create_thread()["id"]

    list(orch.chat_stream(tid, "What is a p-value?"))

    assert seen, "the premise: this turn was armed answer-only"
    assert len(oc.prompts) == 1, "a lane that cannot write findings is not asked for them"


# --- the skill ------------------------------------------------------------------------------------

def test_the_skill_tells_a_lane_with_no_findings_file_to_keep_none_and_say_nothing():
    text = (ROOT / "template" / "skills" / SKILL / "SKILL.md").read_text(encoding="utf-8")
    start = text.index("**If your prompt does not name a findings file")
    rule = text[start:text.index("\n\n", start)]
    assert "keep no notes" in rule
    assert "say in your answer" not in rule
    assert "do not mention" in rule
