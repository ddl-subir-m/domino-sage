"""The third writer of a `.table.json`, and the one nothing was watching (#259, ADR-0045).

ADR-0045 is one rule over every writer. Two of the three were closed: a Live read never lets its
rows touch disk (#254), and a table the **Chat** agent composes is rewritten at the Chat turn ends
(#253). A table a **Build** turn's own agent composes passed no gate at all — the Build turn end
records Live read Artifacts into the manifest and rewrites nothing — so it reached `_save_to_git`
→ `git add -A` with its rows in it, and was pushed to a host nobody named.

Reachable because `examples/` is in front of a Build turn: `_ensure_examples_link` symlinks the
Project's Artifact tree into every Built App (#251), so the convention is legible from the files
themselves even though `template/chat/AGENTS.md`, which teaches the filename, is not read here.

The fix is the same turn-end call Chat makes, at the seam that already takes Build's before/after
snapshot for the Artifact scan. Nothing here is a second mechanism: `withhold_table_rows` and its
`before` semantics are pinned in
test_a_table_chat_wrote_commits_its_shape_not_its_rows.py, and this holds the wiring.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from sage.orchestrator.service import Orchestrator
from sage.router.models import Mode

from .fake_opencode import FakeOpenCode, Turn
from .test_chat_turn import OkFeedback, ScriptedGateway, _catalog

ROWS = [["ada@example.com", "4111111111111111", "078-05-1120"],
        ["grace@example.com", "4012888888881881", "219-09-9999"]]
COLUMNS = ["email", "card_number", "ssn"]
TABLE = json.dumps({"title": "Sample rows", "columns": COLUMNS, "rows": ROWS})


def _orch(tmp: Path) -> tuple[Orchestrator, FakeOpenCode]:
    """A Project where a Build turn really runs, with no scripted turns yet — each test writes its
    own script, because what the agent left behind is the whole subject here."""
    template = tmp / "template"
    (template / "src").mkdir(parents=True, exist_ok=True)
    (template / "src" / "App.tsx").write_text("export default function App() { return null }\n")
    (template / "package.json").write_text('{"name": "template"}')
    (template / "AGENTS.md").write_text("# Building an app\n")
    oc = FakeOpenCode(tmp / "mnt" / "code", [])
    orch = Orchestrator(workspace_dir=oc.workspace, template=template, gateway=ScriptedGateway(),
                        catalog=_catalog(), project_id="Sage", feedback=OkFeedback(),
                        opencode_client=oc)
    orch.project(start_preview=False).record.write_settings({"skip_planning": True})
    return orch, oc


def _build_that_writes_a_table(tmp_path: Path, *, kept_rows: bool,
                               name: str = "sample-rows.table.json") -> Path:
    """One Build turn, end to end, whose agent composes a table. Returns the file it left behind —
    which is the file the save commits, since the commit takes the tree as it stands."""
    orch, oc = _orch(tmp_path)
    project = orch.project(start_preview=False)
    project.record.set_kept_rows(kept_rows)
    tid = orch.create_thread()["id"]
    oc.turns = [Turn(text="Here are five rows.",
                     writes={f"examples/{tid}/{name}": TABLE, "src/App.tsx": "app\n"})]

    list(orch.build_stream("show me some rows from the file", conversation=tid))

    return project.record.path / "examples" / tid / name


def test_a_build_turn_that_answers_with_a_table_leaves_no_rows_behind(tmp_path: Path):
    """The leak, in the bytes that would have been pushed: no email, no card number, no SSN."""
    path = _build_that_writes_a_table(tmp_path, kept_rows=False)

    text = path.read_text()
    for row in ROWS:
        for value in row:
            assert value not in text, f"{value} reached the file a push would carry"
    kept = json.loads(text)
    assert kept["rows"] == []
    assert kept["rowCount"] == 2
    assert kept["columns"] == COLUMNS
    assert kept["title"] == "Sample rows"
    assert kept["keptRows"] is False


def test_a_project_that_keeps_rows_gets_the_file_the_agent_composed(tmp_path: Path):
    """The opt-in reaches this writer too, or the toggle would mean one thing in Chat and another
    in Build over the same file in the same repo."""
    kept = json.loads(_build_that_writes_a_table(tmp_path, kept_rows=True).read_text())

    assert kept["rows"] == ROWS
    assert "keptRows" not in kept


def test_the_card_still_rides_the_done(tmp_path: Path):
    """The rewrite runs before the Artifact scan, and the scan compares against the same before
    snapshot — so a rewritten table is still a file this turn wrote, and still reaches the
    transcript. A pass that ran after the scan would have been invisible here and wrong on disk."""
    orch, oc = _orch(tmp_path)
    tid = orch.create_thread()["id"]
    oc.turns = [Turn(text="Here are five rows.",
                     writes={f"examples/{tid}/sample-rows.table.json": TABLE})]

    events = list(orch.build_stream("show me some rows", conversation=tid))

    done = [e for e in events if e.get("type") == "done"][-1]
    paths = [a["path"] for a in (done.get("artifacts") or [])]
    assert f"examples/{tid}/sample-rows.table.json" in paths, f"done carried: {paths}"


def test_a_table_an_earlier_turn_wrote_is_not_decided_again(tmp_path: Path):
    """`before` means here what it means in Chat: this turn's writes. A table from an earlier turn
    was already decided under whatever answer the Project gave then, and re-deciding it now would
    rewrite a Conversation nobody had opened."""
    orch, oc = _orch(tmp_path)
    project = orch.project(start_preview=False)
    project.record.set_kept_rows(True)
    tid = orch.create_thread()["id"]
    oc.turns = [Turn(text="Here are five rows.",
                     writes={f"examples/{tid}/sample-rows.table.json": TABLE})]
    list(orch.build_stream("show me some rows", conversation=tid))

    project.record.set_kept_rows(False)
    oc.turns = [Turn(text="Made it dark.", writes={"src/App.tsx": "dark\n"})]
    list(orch.build_stream("make it dark", conversation=tid))

    kept = json.loads(
        (project.record.path / "examples" / tid / "sample-rows.table.json").read_text())
    assert kept["rows"] == ROWS


# ---- the phased build, which owns its own commit ------------------------------------------------

PLAN = """A dashboard for exploring the transactions file.

## Plan

### 1. Sample the file
- Files — src/data.ts
- Do — Read a few rows so the shape is known.
- Done when — The columns are written down.

### 2. Render them
- Files — src/Table.tsx
- Do — Render the rows in a table.
- Done when — The preview shows a table.

### 3. Filter them
- Files — src/Filter.tsx
- Do — Add a dropdown above the table.
- Done when — Picking a value narrows the rows.
"""


@pytest.fixture
def _no_waiting(monkeypatch):
    """A phased build sleeps between polls and waits on the preview for a runtime error. Neither is
    what these tests are about, and both are the whole of their wall clock."""
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    monkeypatch.setattr(Orchestrator, "_await_runtime_error", lambda *a, **k: None)


def test_a_phase_of_an_approved_plan_leaves_no_rows_behind(tmp_path: Path, _no_waiting):
    """`_phased_approve` owns what a phase does not — the revert point, the single `done`, and the
    git commit — so it owns this too. A phase runs `_build_stream` with `owns_turn` False, which is
    what makes its own turn end the wrong place to gate: it never reaches one.

    Without this, approving a multi-step plan was the way past the gate the unphased turn passes.
    """
    orch, oc = _orch(tmp_path)
    project = orch.project(start_preview=False)
    project.record.write_settings({"phased_build": True})
    project.control.set_mode(Mode.AUTO)
    tid = orch.create_thread()["id"]
    oc.turns = [
        Turn(text=PLAN),
        Turn(writes={f"examples/{tid}/sample-rows.table.json": TABLE, "src/data.ts": "rows\n"}),
        Turn(writes={"src/Table.tsx": "table\n"}),
        Turn(writes={"src/Filter.tsx": "filter\n"}),
    ]

    list(orch.build_stream("build me a transactions dashboard", conversation=tid))
    events = list(orch.approve_stream(conversation=tid))

    assert [e for e in events if e.get("type") == "build-plan"], "the plan really did run phased"
    path = project.record.path / "examples" / tid / "sample-rows.table.json"
    text = path.read_text()
    for row in ROWS:
        for value in row:
            assert value not in text, f"{value} reached the file the phased commit would carry"
    assert json.loads(text)["rowCount"] == 2


# ---- Stop, which is the way around a gate that only runs at a turn end --------------------------
#
# A stopped turn reverts its code and leaves `examples/` alone — both Build reverts are rooted in
# the Built App (`apps/<appId>/`), and the Artifact tree is a symlink out of it, so `reset --hard`
# and `clean -fd` never descend into it. That is the right behaviour for an Artifact, which is an
# answer someone can still use; it is the wrong behaviour for the rows inside one.
#
# The reason it has to be gated HERE rather than left to the next turn: that turn's `before`
# snapshot already contains these exact bytes, so `withhold_table_rows` reads the file as one an
# earlier turn wrote and skips it, correctly and forever. A table that misses its own turn end
# misses every gate there will ever be.


def test_a_stopped_build_turn_leaves_no_rows_behind(tmp_path: Path, _no_waiting):
    orch, oc = _orch(tmp_path)
    project = orch.project(start_preview=False)
    tid = orch.create_thread()["id"]
    oc.turns = [Turn(text="Here are five rows.",
                     writes={f"examples/{tid}/sample-rows.table.json": TABLE,
                             "src/App.tsx": "app\n"})]

    for ev in orch.build_stream("show me some rows", conversation=tid):
        if ev.get("type") == "typecheck-start":
            project.stop_requested = True

    text = (project.record.path / "examples" / tid / "sample-rows.table.json").read_text()
    for row in ROWS:
        for value in row:
            assert value not in text, f"{value} survived a stopped turn"


def test_a_stopped_phase_leaves_no_rows_behind(tmp_path: Path, _no_waiting):
    """A Stop takes the whole phased build back to its base — which reaches the app's code and not
    the Artifacts beside it, so the rows have to be taken out on the way past."""
    orch, oc = _orch(tmp_path)
    project = orch.project(start_preview=False)
    project.record.write_settings({"phased_build": True})
    project.control.set_mode(Mode.AUTO)
    tid = orch.create_thread()["id"]
    oc.turns = [
        Turn(text=PLAN),
        Turn(writes={f"examples/{tid}/sample-rows.table.json": TABLE, "src/data.ts": "rows\n"}),
        Turn(writes={"src/Table.tsx": "table\n"}),
        Turn(writes={"src/Filter.tsx": "filter\n"}),
    ]
    list(orch.build_stream("build me a transactions dashboard", conversation=tid))

    events = []
    for ev in orch.approve_stream(conversation=tid):
        events.append(ev)
        if ev.get("type") == "step-start" and ev.get("n") == 2:
            project.stop_requested = True

    assert [e for e in events if e.get("type") == "stopped"], "the build really was stopped"
    text = (project.record.path / "examples" / tid / "sample-rows.table.json").read_text()
    for row in ROWS:
        for value in row:
            assert value not in text, f"{value} survived a stopped phased build"
