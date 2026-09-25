"""A file that will not parse is put in front of the model inside the turn that wrote it (#547).

Measured: a GLM approve turn wrote two files in one call, and Sage's after-turn check found one
`SyntaxError`. The model learned about it only once its turn was over, and the repair turn that
followed ran 268s without making a single edit. The error existed for the whole of the turn that
could most cheaply have fixed it, and nothing told it.

So each written file is checked the moment its write lands, and only a file that fails says
anything: a clean write, a file this stack does not check, and a check that could not run all add
nothing to the next request. The after-turn check and the circuit breaker are untouched.

WHICH tool counts as a write is read off the ARGUMENTS and not off the tool's name. OpenCode picks
a model's edit tools from the model HANDLE (#539), so a `gpt-` build is offered `apply_patch` and
never `edit`/`write`, and a reader keyed on a `path` argument would be blind on every one of them.
"""
from __future__ import annotations

import json
from dataclasses import replace as _replace
from pathlib import Path

from sage.build_policy import BuildPolicy
from sage.feedback.runner import check_file
from sage.gateway.capabilities import RouteCapability
from sage.gateway.client import FakeGatewayClient
from sage.gateway.protocol import Protocol
from sage.orchestrator.service import _note_written_file_errors, _written_paths
from sage.router.model_control import ModelControl
from sage.router.models import Mode, ModelCatalog, Phase
from sage.shim.enforcement import EnforcementShim

from .fake_opencode import Turn
from .test_a_turn_that_stops_changing_anything_is_budgeted import (
    ScriptedPartsOpenCode,
    _orch,
    _write,
)

BROKEN_PY = "def f(:\n"
CLEAN_PY = "def f():\n    return 1\n"
BROKEN_JS = "function f( {\n"

POLICY = _replace(BuildPolicy(), stop_grace_seconds=1.0)


# ---- the fakes the checker is asked through ---------------------------------------------------

class _Shim:
    def __init__(self) -> None:
        self.notes: dict[str, str] = {}

    def note_syntax_error(self, file: str, detail: str) -> None:
        self.notes[file] = detail


class _App:
    def __init__(self, path: Path) -> None:
        self.path = path


class _Project:
    def __init__(self, path: Path) -> None:
        self._path = path
        self.shim = _Shim()

    def app_for_turn(self) -> _App:
        return _App(self._path)


def _app(tmp: Path, stack: str = "fastapi-antd") -> Path:
    """An app of a named stack. The stack is the app's own record, never detected from the files
    (`sage/workspace/stack.py`), so writing that record is all it takes to be one."""
    app = tmp / "app"
    (app / ".sage").mkdir(parents=True, exist_ok=True)
    (app / ".sage" / "settings.json").write_text(json.dumps({"stack": stack}))
    return app


def _landed(path: Path, source: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source)


# ---- which files a landed call wrote ----------------------------------------------------------

def test_the_written_files_are_read_off_the_arguments_not_off_the_tool_name():
    """The #539 trap, stated as a property.

    A `gpt-` handle is offered `apply_patch` and no `edit`/`write`; every other handle is offered
    `edit`/`write` and no `apply_patch`. So neither argument shape can be the only one read, and
    the tool's NAME cannot answer which to read — the same model id serves both over time.
    """
    assert _written_paths({"filePath": "app.py", "content": "x"}) == ["app.py"]
    assert _written_paths({"path": "static/app.js"}) == ["static/app.js"]

    # `apply_patch` carries a patch and no path at all. A reader that only knew about `path` would
    # get None here and check nothing on every GPT build.
    patch = ("*** Begin Patch\n"
             "*** Update File: app.py\n"
             "@@\n-old\n+new\n"
             "*** End Patch\n")
    assert _written_paths({"patchText": patch}) == ["app.py"]

    # Nothing to read is not an error: an alias in WRITE_TOOLS that no driver here offers, or a
    # call whose arguments never parsed into a dict (measured live — OpenCode hands the raw string).
    assert _written_paths({"todos": []}) == []
    assert _written_paths("write app.py") == []
    assert _written_paths(None) == []


def test_a_patch_that_touches_three_files_names_three_files():
    """`_patch_detail` answers "which file is this CARD about" and returns on the first header.
    This question is different: every file the patch wrote has to be checked, so the loop runs to
    the end. A delete is left out — there is no file left to check."""
    patch = ("*** Begin Patch\n"
             "*** Update File: app.py\n@@\n-a\n+b\n"
             "*** Add File: static/app.js\n+x\n"
             "*** Delete File: old.py\n"
             "*** Update File: helpers.py\n@@\n-c\n+d\n"
             "*** End Patch\n")
    assert _written_paths({"patchText": patch}) == ["app.py", "static/app.js", "helpers.py"]


# ---- one file's check -------------------------------------------------------------------------

def test_a_file_the_stack_does_not_check_is_not_checked(tmp_path: Path):
    """Three ways to be out of scope, and all three are silence rather than a clean report.

    `None` and `ok` are different answers and the caller treats them the same, but they must not
    be conflated here: a react-vite app's check is `tsc`, which is project-wide by construction,
    and reporting it "passed" per file would be a claim nobody made.
    """
    app = _app(tmp_path)
    _landed(app / "notes.md", "# not source\n")
    _landed(app / "static" / "vendor" / "react.js", BROKEN_JS)
    assert check_file(app, "notes.md") is None            # not a checked extension
    assert check_file(app, "static/vendor/react.js") is None   # a vendored bundle, not the app's
    assert check_file(app, "does-not-exist.py") is None   # a write that left nothing on disk

    # A file outside the app is not this app's to check, whatever its extension.
    outside = tmp_path / "elsewhere.py"
    _landed(outside, BROKEN_PY)
    assert check_file(app, str(outside)) is None

    # And the whole react-vite stack is out, because it has no per-file check to run.
    vite = _app(tmp_path / "vite-app", stack="react-vite")
    _landed(vite / "app.py", BROKEN_PY)
    assert check_file(vite, "app.py") is None


def test_a_broken_file_reports_the_line_and_a_clean_one_reports_nothing(tmp_path: Path):
    app = _app(tmp_path)
    _landed(app / "app.py", BROKEN_PY)
    _landed(app / "ok.py", CLEAN_PY)

    broken = check_file(app, "app.py")
    assert broken is not None and not broken.ok
    assert [(e.file, e.line, e.code) for e in broken.errors] == [("app.py", 1, "SyntaxError")]

    clean = check_file(app, "ok.py")
    assert clean is not None and clean.ok and clean.errors == []


def test_an_absolute_path_is_the_same_file_as_a_relative_one(tmp_path: Path):
    """OpenCode's `write` reports an absolute path and a patch header carries a relative one, and
    both have to land on the same file — and on the same workspace-relative name in the note, or
    a file written twice would be reported twice."""
    app = _app(tmp_path)
    _landed(app / "app.py", BROKEN_PY)
    by_relative = check_file(app, "app.py")
    by_absolute = check_file(app, str(app / "app.py"))
    assert by_relative is not None and by_absolute is not None
    assert [e.file for e in by_relative.errors] == [e.file for e in by_absolute.errors] == ["app.py"]


# ---- what reaches the model --------------------------------------------------------------------

def test_a_broken_write_queues_the_error_and_a_clean_one_queues_nothing(tmp_path: Path):
    app = _app(tmp_path)
    _landed(app / "app.py", BROKEN_PY)
    _landed(app / "ok.py", CLEAN_PY)

    project = _Project(app)
    _note_written_file_errors(project, {"filePath": "app.py"})
    assert list(project.shim.notes) == ["app.py"]
    assert project.shim.notes["app.py"] == "app.py:1:1 SyntaxError: invalid syntax"

    clean = _Project(app)
    _note_written_file_errors(clean, {"filePath": "ok.py"})
    assert clean.shim.notes == {}

    skipped = _Project(app)
    _landed(app / "notes.md", "# not source\n")
    _note_written_file_errors(skipped, {"filePath": "notes.md"})
    assert skipped.shim.notes == {}


def test_a_patch_gets_the_same_treatment_as_a_write(tmp_path: Path):
    """The GPT path. Same broken file, same note, reached through `patchText` instead of a path —
    and every file the patch touched is checked, not just the first."""
    app = _app(tmp_path)
    _landed(app / "app.py", BROKEN_PY)
    _landed(app / "helpers.py", BROKEN_PY)
    _landed(app / "static" / "app.js", BROKEN_JS)

    project = _Project(app)
    _note_written_file_errors(project, {"patchText": (
        "*** Begin Patch\n"
        "*** Update File: app.py\n@@\n-a\n+b\n"
        "*** Add File: static/app.js\n+x\n"
        "*** Update File: helpers.py\n@@\n-c\n+d\n"
        "*** End Patch\n")})

    assert sorted(project.shim.notes) == ["app.py", "helpers.py", "static/app.js"]
    assert "SyntaxError" in project.shim.notes["static/app.js"]


def test_several_broken_files_are_all_told_to_the_model_at_once(tmp_path: Path):
    """Accumulated per file, not overwritten.

    The progress note next door carries a COUNT, where only the newest is worth telling. This
    carries a SET of distinct errors: dropping four of five would tell the model to fix one file
    per turn across five turns, which is the loop this issue is about.
    """
    oc = ScriptedPartsOpenCode(tmp_path / "mnt" / "code", [], [Turn(text="hi")])
    shim = _orch(tmp_path, oc, POLICY).project(start_preview=False).shim

    assert shim._take_syntax_note() == ""
    shim.note_syntax_error("app.py", "app.py:1:1 SyntaxError: invalid syntax")
    shim.note_syntax_error("static/app.js", "static/app.js:2:1 SyntaxError: Unexpected end of input")
    note = shim._take_syntax_note()
    assert "app.py:1:1 SyntaxError: invalid syntax" in note
    assert "static/app.js:2:1 SyntaxError: Unexpected end of input" in note
    # One-shot, like the progress note: a note read twice is a warning the turn did not earn.
    assert shim._take_syntax_note() == ""

    # A file written twice in a turn is one entry, at its latest state.
    shim.note_syntax_error("app.py", "first")
    shim.note_syntax_error("app.py", "second")
    note = shim._take_syntax_note()
    assert "second" in note and "first" not in note


def test_the_note_reaches_the_model_as_a_system_message_on_the_next_request():
    """The delivery half, through `prepare` — the only thing that puts the note on the wire.

    `system` rather than `user` for the reason the progress note beside it is: `_current_turn`
    treats a user message as a turn boundary, so a `user` note would truncate the window the phase
    classifier and the rescue signals read. And ONE request only: a note read twice is a warning
    the turn did not earn.
    """
    control = ModelControl(mode=Mode.IMPLEMENT, phase=Phase.IMPLEMENT)
    shim = EnforcementShim(control, ModelCatalog("m", "m", "m", "m", "m", "m"),
                           FakeGatewayClient(), build_policy=BuildPolicy())
    shim.resolve_capability = lambda _model: RouteCapability(Protocol.CHAT, True)
    request = {"model": "m", "messages": [{"role": "user", "content": "build me a chart"}]}

    shim.note_syntax_error("app.py", "app.py:1:1 SyntaxError: invalid syntax")
    prepared, *_ = shim.prepare(dict(request), "project", "session")
    notes = [m for m in prepared["messages"]
             if m.get("role") == "system" and "[sage] Syntax check:" in str(m.get("content"))]
    assert len(notes) == 1
    assert "app.py:1:1 SyntaxError: invalid syntax" in notes[0]["content"]

    # Taken, so the request after it carries nothing.
    again, *_ = shim.prepare(dict(request), "project", "session")
    assert not [m for m in again["messages"] if "[sage] Syntax check:" in str(m.get("content"))]


# ---- through the build loop ---------------------------------------------------------------------

def _build(tmp_path: Path, parts: list[dict], source: str = BROKEN_PY):
    """One build turn over a fastapi-antd app whose `app.py` is already on disk in `source`.

    The scripted part CLAIMS the write; the file is what the write left behind. That split is the
    honest one for this loop, which never sees the content — it sees a completed call and a tree.

    The turn is given no `writes` of its own on purpose. `Turn.writes` produces a completed write
    PART as well as the file, so a turn that wrote `app.py` that way would queue the note by
    itself — and the test for a REFUSED write would pass on a note the refusal did not cause. It
    is exactly that: the refused-write test failed here until the turn stopped writing.
    """
    oc = ScriptedPartsOpenCode(tmp_path / "mnt" / "code", [[part] for part in parts],
                               [Turn(text="built it")])
    orch = _orch(tmp_path, oc, POLICY)
    project = orch.project(start_preview=False)
    app = project.app_for_turn().path
    (app / ".sage").mkdir(parents=True, exist_ok=True)
    settings = app / ".sage" / "settings.json"
    record = json.loads(settings.read_text()) if settings.is_file() else {}
    settings.write_text(json.dumps({**record, "stack": "fastapi-antd"}))
    _landed(app / "app.py", source)
    list(orch.build_stream("build me a chart"))
    return project.shim._take_syntax_note()


def test_a_landed_write_of_a_broken_file_reaches_the_model_inside_the_turn(tmp_path: Path):
    """The symptom, end to end: the turn that wrote the file is told, while it is still running."""
    note = _build(tmp_path, [_write(1, path="app.py")])
    assert "app.py:1:1 SyntaxError: invalid syntax" in note


def test_a_refused_write_checks_nothing(tmp_path: Path):
    """A write that came back refused wrote nothing, so there is nothing its author can fix.

    The same distinction #508 drew for `agent_wrote()`: the file on disk here is broken, and the
    only reason to say nothing about it is that THIS call did not put it there.
    """
    refused = {"id": "w-1", "type": "tool", "tool": "write",
               "state": {"status": "error", "input": {"filePath": "app.py"}}}
    assert _build(tmp_path, [refused]) == ""


def test_a_landed_write_of_a_clean_file_reaches_the_model_with_nothing(tmp_path: Path):
    assert _build(tmp_path, [_write(1, path="app.py")], source=CLEAN_PY) == ""
