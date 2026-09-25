"""A `.sage/` JSON file whose bytes are not UTF-8 degrades, it does not brick the Project (#303).

Every one of these readers wrapped `Path.read_text()` in `except (json.JSONDecodeError, OSError)`.
`UnicodeDecodeError` is neither: it is a SIBLING of `JSONDecodeError` under `ValueError`, so a file
that is not UTF-8 at all escaped the catch. `read_attachments` is reached by `_rehydrate_attached`,
and so by `project()` — one such file stopped the Project opening, not just the app that owned it.

The bytes here are real, not a mocked exception. A mock proves the handler runs; it does not prove
`read_text()` raises what the handler is written for, which is the half that was wrong.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from sage.gateway.client import FakeGatewayClient
from sage.orchestrator.service import Orchestrator
from sage.resources.provider import FakeResourceProvider
from sage.router.models import ModelCatalog
from sage.workspace.manager import WorkspaceManager

# A UTF-16 JSON document: it opens with the byte-order mark `\xff\xfe`, and `\xff` is not a legal
# UTF-8 start byte in any position. This is what a Windows editor or a `git merge` gone binary
# leaves behind, and it is well-formed JSON to anything that decodes it correctly — the fault is
# the ENCODING, which is why every reader here reached for a JSON error and missed.
NOT_UTF8 = json.dumps([{"id": "ds-1"}]).encode("utf-16")


def _template(tmp: Path) -> Path:
    t = tmp / "template"
    (t / "src").mkdir(parents=True, exist_ok=True)
    (t / "src" / "App.tsx").write_text("placeholder")
    (t / "package.json").write_text("{}")
    # A real template carries both, and without AGENTS.md the two readers that still bricked the
    # open — `_voice_agents_md` and `_splice_instructions` — return before they read anything.
    (t / "AGENTS.md").write_text("# Sage app\n")
    (t / ".gitignore").write_text("node_modules\n")
    return t


def _orch(tmp_path: Path) -> Orchestrator:
    return Orchestrator(
        workspace_dir=tmp_path / "mnt" / "code",
        template=_template(tmp_path),
        gateway=FakeGatewayClient(),
        catalog=ModelCatalog("sq", "sq", "sq", "p", "i", "a"),
        project_id="Sage",
        resources=FakeResourceProvider([], []),
    )


def test_read_text_really_does_raise_unicodedecodeerror_and_it_is_not_a_json_error(tmp_path: Path):
    """The premise the six catches were written against, checked rather than assumed."""
    p = tmp_path / "attachments.json"
    p.write_bytes(NOT_UTF8)
    try:
        p.read_text()
    except UnicodeDecodeError as exc:
        assert not isinstance(exc, json.JSONDecodeError)
        assert not isinstance(exc, OSError)
        assert isinstance(exc, ValueError)
    else:
        # `read_text()` with no argument decodes in the LOCALE's encoding, and nothing in this repo
        # pins one. Under a latin-1-ish locale `\xff` decodes fine and the defect this file is about
        # cannot arise at all — so say that rather than fail. The JSON readers below would still
        # pass there (the decoded text is not JSON), but the two that assert on decoded CONTENT
        # would not, so they carry `_needs_utf8_locale()` rather than a claim that they hold.
        pytest.skip("this locale decodes 0xff, so read_text() never raises UnicodeDecodeError here")


def _needs_utf8_locale(tmp_path: Path) -> None:
    """Skip unless `read_text()` really refuses these bytes. The tests that call this assert on
    DECODED content, so under a latin-1-ish locale they would not merely miss the defect — they
    would fail, having read the UTF-16 bytes as a garbage string that is perfectly truthy."""
    probe = tmp_path / "probe"
    probe.write_bytes(NOT_UTF8)
    try:
        probe.read_text()
    except UnicodeDecodeError:
        return
    finally:
        probe.unlink()
    pytest.skip("this locale decodes 0xff, so read_text() never raises UnicodeDecodeError here")


def _workspace(tmp_path: Path):
    mgr = WorkspaceManager(workspace_dir=tmp_path / "ws", template=_template(tmp_path))
    return mgr.ensure("proj1")


def test_attachments_that_are_not_utf8_read_as_empty(tmp_path: Path):
    ws = _workspace(tmp_path)
    ws.attachments_path.parent.mkdir(parents=True, exist_ok=True)
    ws.attachments_path.write_bytes(NOT_UTF8)
    assert ws.read_attachments() == []


def test_bindings_that_are_not_utf8_read_as_empty(tmp_path: Path):
    ws = _workspace(tmp_path)
    ws.bindings_path.parent.mkdir(parents=True, exist_ok=True)
    ws.bindings_path.write_bytes(NOT_UTF8)
    assert ws.read_bindings() == []


def _record(tmp_path: Path):
    mgr = WorkspaceManager(workspace_dir=tmp_path / "ws", template=_template(tmp_path))
    mgr.ensure("proj1")
    return mgr.project_record("proj1")


def test_settings_that_are_not_utf8_read_as_empty(tmp_path: Path):
    rec = _record(tmp_path)
    rec.settings_path.parent.mkdir(parents=True, exist_ok=True)
    rec.settings_path.write_bytes(NOT_UTF8)
    assert rec.read_settings() == {}


def test_project_resources_that_are_not_utf8_read_as_empty(tmp_path: Path):
    rec = _record(tmp_path)
    rec.project_resources_path.parent.mkdir(parents=True, exist_ok=True)
    rec.project_resources_path.write_bytes(NOT_UTF8)
    assert rec.read_project_resources() == []


def test_session_id_that_is_not_utf8_reads_as_none(tmp_path: Path):
    rec = _record(tmp_path)
    p = rec.build_session_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(NOT_UTF8)
    assert rec.read_session_id() is None


def test_plan_doc_meta_that_is_not_utf8_reads_as_none(tmp_path: Path):
    rec = _record(tmp_path)
    doc = rec.create_plan_doc("# a plan", title="A plan")
    plan_id = doc["id"]
    (rec.plan_docs_dir / plan_id / "meta.json").write_bytes(NOT_UTF8)
    # Straight at the reader, not only through `read_plan_doc`: that caller catches `ValueError`
    # for `safe_id`, which would swallow this one by accident and certify a guard that isn't there.
    assert rec._read_plan_doc_meta(plan_id) is None


def test_a_non_utf8_attachments_manifest_does_not_stop_the_project_opening(tmp_path: Path):
    """The headline. `_rehydrate_attached` reads every app's manifest on `project()`."""
    first = _orch(tmp_path)
    project = first.project(start_preview=False)
    ws = project.workspace
    ws.attachments_path.parent.mkdir(parents=True, exist_ok=True)
    ws.attachments_path.write_bytes(NOT_UTF8)

    reopened = _orch(tmp_path).project(start_preview=False)
    assert reopened.workspace.read_attachments() == []


def test_a_non_utf8_instructions_file_does_not_stop_the_project_opening(tmp_path: Path):
    """Through `project()`, not just the reader. The first version of this test asserted on a bare
    `ProjectRecord` while its name promised the open path — and the open path was still broken."""
    _needs_utf8_locale(tmp_path)
    first = _orch(tmp_path)
    project = first.project(start_preview=False)
    project.record.write_instructions("Always use a bar chart.")
    project.record.instructions_path.write_bytes(NOT_UTF8)

    reopened = _orch(tmp_path).project(start_preview=False)
    assert reopened.record.read_instructions() == ""


def test_instructions_that_cannot_be_read_are_not_deleted_from_the_file_the_agent_reads(
        tmp_path: Path):
    """The shrug must not become a deletion. `_splice_instructions` renders the instructions into
    the app's AGENTS.md and treats `""` as "there are none", stripping the block and saving — so
    the lenient read, left alone, would durably destroy the text it could not decode."""
    _needs_utf8_locale(tmp_path)
    first = _orch(tmp_path)
    project = first.project(start_preview=False)
    project.record.write_instructions("Always use a bar chart.")
    agents = _orch(tmp_path).project(start_preview=False).workspace.path / "AGENTS.md"
    assert "Always use a bar chart." in agents.read_text()

    project.record.instructions_path.write_bytes(NOT_UTF8)
    _orch(tmp_path).project(start_preview=False)
    assert "Always use a bar chart." in agents.read_text()


def test_a_non_utf8_app_agents_md_does_not_stop_the_project_opening(tmp_path: Path):
    """`_voice_agents_md` reads this one BEFORE `_splice_instructions` does, so guarding the
    instructions sidecar alone left the open still broken on the file it renders into."""
    first = _orch(tmp_path)
    agents = first.project(start_preview=False).workspace.path / "AGENTS.md"
    agents.write_bytes(NOT_UTF8)
    assert _orch(tmp_path).project(start_preview=False)


def test_a_non_utf8_project_gitignore_does_not_stop_the_project_opening(tmp_path: Path):
    """The earliest brick of all: `ensure()` manages this file on its FIRST line, and it lives in
    the person's own repo where they and the agent both edit it."""
    first = _orch(tmp_path)
    root = first.project(start_preview=False).record.path
    (root / ".gitignore").write_bytes(NOT_UTF8)
    assert _orch(tmp_path).project(start_preview=False)


def test_an_ignore_rule_still_lands_in_a_gitignore_that_is_not_utf8(tmp_path: Path):
    """And it lands without disturbing the bytes already there. Guarding the read instead would
    have swapped the crash for a rule that silently stops being applied, which is how data that
    was meant to stay out of git gets committed."""
    from sage.workspace.manager import ensure_ignore_line, remove_ignore_line

    p = tmp_path / ".gitignore"
    p.write_bytes(NOT_UTF8)
    ensure_ignore_line(p, "public/data")
    assert p.read_bytes().startswith(NOT_UTF8)          # nothing already there was rewritten
    assert p.read_bytes().endswith(b"public/data\n")

    ensure_ignore_line(p, "public/data")                # idempotent, and still reading bytes
    assert p.read_bytes().count(b"public/data") == 1
    assert remove_ignore_line(p, "public/data")
    assert b"public/data" not in p.read_bytes()


def test_a_non_utf8_legacy_root_agents_md_does_not_stop_the_workspace_seeding(tmp_path: Path):
    """`ensure()` handles this file unconditionally — the FIRST step of opening a Project, so this
    one bricked earlier than the manifest did.

    It used to be voiced in place, which meant READING it, which is how these bytes bricked the
    open. Since #548 it is renamed out of OpenCode's walk-up instead, so nothing decodes it at all
    and the defect cannot come back by that route. Kept pointed at the open rather than at the
    method, so it still covers whatever `ensure()` does to this file next."""
    mgr = WorkspaceManager(workspace_dir=tmp_path / "ws", template=_template(tmp_path))
    mgr.ensure("proj1")
    (tmp_path / "ws" / "AGENTS.md").write_bytes(NOT_UTF8)
    assert WorkspaceManager(workspace_dir=tmp_path / "ws", template=_template(tmp_path)).ensure("proj1")


def test_strict_does_not_refuse_on_an_instructions_file_that_was_never_written(tmp_path: Path):
    """`strict` is about bytes that cannot be decoded, not about absence. Clearing your
    instructions UNLINKS the file, so refusing on a missing one would leave the block standing in
    AGENTS.md for good — which is what `test_empty_content_removes_the_block_cleanly` caught."""
    rec = _record(tmp_path)
    assert not rec.instructions_path.exists()
    assert rec.read_instructions(strict=True) == ""


def test_a_save_is_not_reported_as_done_when_the_block_could_not_be_written(tmp_path: Path):
    """The open path shrugs; an explicit save must not. The person typed the text and is waiting to
    be told what happened, so `PUT /api/project/instructions` answering `{"ok": True}` over a file
    that was never touched is a worse failure than the crash it replaced."""
    first = _orch(tmp_path)
    project = first.project(start_preview=False)
    (project.workspace.path / "AGENTS.md").write_bytes(NOT_UTF8)

    orch = _orch(tmp_path)
    reopened = orch.project(start_preview=False)          # opening still works
    with pytest.raises((ValueError, OSError)):
        orch.write_instructions(reopened, "Always use a bar chart.")


def test_removing_a_rule_leaves_every_other_byte_alone(tmp_path: Path):
    """`splitlines()` would break on a lone `\r`, which in UTF-16 is half of `0D 00` — rejoining
    would hand back a bare `0A` and quietly rewrite bytes nobody asked about."""
    from sage.workspace.manager import ensure_ignore_line, remove_ignore_line

    p = tmp_path / ".gitignore"
    head = "keep\r\nstay".encode("utf-16")          # a CR that is half a UTF-16 code unit
    p.write_bytes(head)
    ensure_ignore_line(p, "public/data")
    assert remove_ignore_line(p, "public/data")
    out = p.read_bytes()
    # Not a round trip: `ensure_ignore_line` legitimately added a separator newline to a file that
    # had none, and that stays. The property is that nothing ELSE moved — the `\r` is still the two
    # bytes of its UTF-16 code unit and has not been rewritten as a bare `0A`.
    assert out.startswith(head), "removing one rule rewrote bytes it was not asked to touch"
    assert out.count(b"\r\x00") == 1
    assert b"public/data" not in out
