"""The `_stack_unsupported_refusal` gate in `Orchestrator.build_stream` (ONE-APP-STATUS.md's Phase 0
decision 4): a Build turn refuses an app whose record names a stack this Sage no longer carries
(in practice, only `react-vite` — the stack the one-app pivot retired) rather than silently
re-seeding or crashing on it.

Found to have NO direct test until now (2026-09-23, live-laptop-testing session): a stale docstring
in `test_a_built_app_declares_its_stack_at_birth.py` pointed at "test_orchestrator.py's stack-refusal
tests" — no such tests exist there, or anywhere. Written after a real false alarm on a laptop, where
the product owner's very first Build click hit this exact refusal and it read as a pivot bug before
being traced to a leftover `apps/` directory from an earlier, pre-pivot run of Sage on that same
machine (`backend/workspaces/` is gitignored, so it survives every `git pull`/branch switch
untouched). The gate did exactly what it was designed to do; what it lacked was proof that it does
NOT also do that to a genuinely brand-new app, which is the scarier direction to get wrong — a false
refusal on every first build would have been a launch-blocking pivot bug, not a leftover-directory
one, and nothing before this file could tell the two apart.
"""
from __future__ import annotations

import json
from pathlib import Path

from sage.orchestrator.service import Orchestrator

from .fake_opencode import FakeOpenCode, Turn
from .test_chat_turn import OkFeedback, ScriptedGateway, _catalog


def _template(tmp: Path) -> Path:
    t = tmp / "template"
    (t / "static").mkdir(parents=True)
    (t / "static" / "app.js").write_text("// placeholder\n")
    (t / "app.py").write_text("# app\n")
    return t


def _orch(tmp: Path, turns) -> tuple[Orchestrator, Path]:
    ws = tmp / "mnt" / "code"
    oc = FakeOpenCode(ws, turns)
    orch = Orchestrator(workspace_dir=ws, template=_template(tmp), gateway=ScriptedGateway(),
                        catalog=_catalog(), project_id="Sage", feedback=OkFeedback(),
                        opencode_client=oc)
    return orch, ws


def _refusal(events: list[dict]) -> dict | None:
    return next((e for e in events if e.get("type") == "ask-blocked"
                 and "no longer carries" in e.get("message", "")), None)


def test_a_brand_new_project_never_hits_the_stack_refusal_build_first(tmp_path):
    """The scarier direction: nothing before this test proved a fresh app never trips the gate
    meant for a LEGACY one."""
    orch, ws = _orch(tmp_path, [Turn(text="ok")])
    assert not (ws / "apps").exists()  # genuinely nothing here yet, not just unseeded-looking

    events = list(orch.build_stream("build me a todo app"))

    assert _refusal(events) is None
    apps = list((ws / "apps").iterdir())
    assert len(apps) == 1
    settings = json.loads((apps[0] / ".sage" / "settings.json").read_text())
    assert settings["stack"] == "fastapi-antd"


def test_a_brand_new_project_never_hits_the_stack_refusal_chat_first(tmp_path):
    """The exact ordering a real Workbench session hits: Chat attaches the volume with
    `seed_app=False` (`test_chat_does_not_seed_the_app_template`) before Build ever runs, and that
    unseeded attach is cached for the Orchestrator's whole life (`Orchestrator.project()`'s
    memoization). `_ensure_seeded()`, not the cached `project()`, is what `_build_stream` calls to
    re-seed regardless — this proves that seam holds, not just that it reads like it should."""
    orch, ws = _orch(tmp_path, [Turn(text="hi"), Turn(text="ok")])
    tid = orch.create_thread()["id"]
    list(orch.chat_stream(tid, "hi"))
    assert not (ws / "apps").exists()  # confirmed: chat alone seeds nothing

    events = list(orch.build_stream("build me a todo app"))

    assert _refusal(events) is None
    settings = json.loads((next((ws / "apps").iterdir()) / ".sage" / "settings.json").read_text())
    assert settings["stack"] == "fastapi-antd"


def test_an_app_from_before_the_pivot_is_refused_not_silently_reseeded(tmp_path):
    """The gate's actual job: an app already on disk from before #490 (no `stack` key at all, or
    the literal `react-vite` — the two read identically, `workspace/stack.py`'s own docstring) gets
    a clear refusal naming Chat as the way in, never a silent re-seed over what might be real work
    and never a crash."""
    orch, ws = _orch(tmp_path, [Turn(text="ok")])
    legacy = ws / "apps" / "app_legacy"
    (legacy / ".sage").mkdir(parents=True)
    (legacy / ".sage" / "settings.json").write_text(json.dumps({"createdAt": "2025-01-01T00:00:00Z"}))
    (legacy / "package.json").write_text("{}")  # what an old react-vite app actually left behind

    events = list(orch.build_stream("add a button"))

    refusal = _refusal(events)
    assert refusal is not None
    assert "Chat" in refusal["message"]
    # Refused, not touched: nothing here re-seeds fastapi-antd's files over it.
    assert not (legacy / "app.py").exists()
    assert not (legacy / "static").exists()
