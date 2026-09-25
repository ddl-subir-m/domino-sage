"""A non-UTF-8 app `AGENTS.md` degrades the managed blocks, it does not stop the act (#325).

#303 guarded the two readers of `apps/<id>/AGENTS.md` that sit on the project-open path
(`_voice_agents_md`, `_splice_instructions`). It did not touch the two WRITERS, and they read the
same file the same way — a bare `read_text()`, no `try` at all. `UnicodeDecodeError` is a
`ValueError`, not an `OSError`, so nothing caught it.

Measured on this tree before the fix: attaching a file, attaching a folder, detaching a file,
detaching a folder, uploading, binding a Dataset and releasing one — seven acts — all died on it.
And the person was NOT shown a 500. `attach_file`'s route catches bare `ValueError`, so the decode
error arrived as **400 "invalid file path"** for a path that was perfectly valid: the product
blaming their input for a file they never chose to edit.

The bytes here are real, not a mocked exception, for the reason #303's file gives: a mock proves
the handler runs, it does not prove `read_text()` raises what the handler is written for.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from sage.assets.provider import FakeAssetProvider
from sage.orchestrator.service import Orchestrator
from sage.router.models import ModelCatalog

# Same bytes as #303's file, and for the same reason: UTF-16 opens with `\xff\xfe`, and `\xff` is
# not a legal UTF-8 start byte anywhere. This is what a Windows editor or a `git merge` gone binary
# leaves behind. AGENTS.md is committed to the person's own app repo, so both are reachable.
NOT_UTF8 = json.dumps([{"id": "ds-1"}]).encode("utf-16")


def _needs_utf8_locale(tmp_path: Path) -> None:
    """Skip unless `read_text()` really refuses these bytes.

    `read_text()` with no argument decodes in the LOCALE's encoding and nothing in this repo pins
    one. Under a latin-1-ish locale `\xff` decodes fine, the defect cannot arise at all, and the
    assertions below about what survives would be testing nothing.
    """
    probe = tmp_path / "probe"
    probe.write_bytes(NOT_UTF8)
    try:
        probe.read_text()
    except UnicodeDecodeError:
        return
    finally:
        probe.unlink()
    pytest.skip("this locale decodes 0xff, so read_text() never raises UnicodeDecodeError here")


def _template(tmp: Path) -> Path:
    t = tmp / "template"
    (t / "src").mkdir(parents=True, exist_ok=True)
    (t / "src" / "App.tsx").write_text("placeholder")
    (t / "package.json").write_text("{}")
    # Without AGENTS.md there is no file to bomb and every site returns before it reads anything.
    (t / "AGENTS.md").write_text("# Sage app\n")
    (t / ".gitignore").write_text("node_modules\n")
    return t


def _provider(tmp: Path) -> FakeAssetProvider:
    p = FakeAssetProvider(root=tmp / "mounts")
    asset = next(a for a in p.assets if a.name == "sales_2026")
    mount = p.roots[asset.id]
    for i in range(2):
        f = mount / "raw" / "2025" / f"part-{i}.csv"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("a,b\n1,2\n")
    (mount / "solo.csv").write_text("a,b\n3,4\n")
    return p


def _orch(tmp: Path) -> Orchestrator:
    return Orchestrator(
        workspace_dir=tmp / "mnt" / "code",
        template=_template(tmp),
        gateway=object(),
        catalog=ModelCatalog(sovereign_plan="s", sovereign_implement="s", sovereign_ask="s",
                             plan="p", implement="i", ask="a"),
        project_id="Sage",
        assets=_provider(tmp),
    )


def _ready(tmp_path: Path) -> tuple[Orchestrator, str, Path]:
    """An open Project with a Dataset to attach from, and the app's AGENTS.md."""
    _needs_utf8_locale(tmp_path)
    orch = _orch(tmp_path)
    agents = orch.project(start_preview=False).workspace.path / "AGENTS.md"
    return orch, next(a["id"] for a in orch.list_assets() if a["name"] == "sales_2026"), agents


def _manifest(orch: Orchestrator) -> list[dict]:
    record = orch.project(start_preview=False).workspace.path / ".sage" / "attachments.json"
    return json.loads(record.read_text()) if record.is_file() else []


def test_attaching_a_file_survives_a_non_utf8_agents_md(tmp_path: Path):
    orch, ds, agents = _ready(tmp_path)
    agents.write_bytes(NOT_UTF8)

    assert orch.attach_file(ds, "solo.csv")["attached"] == "solo.csv"


def test_the_manifest_still_follows_the_disk_when_agents_md_cannot_be_read(tmp_path: Path):
    """The condition that rules out raising here, not merely the one that rules out crashing.

    `_write_agents_data_block` runs BEFORE `write_attachments` at every attach site. A raise there
    leaves the symlink made and the manifest not updated — the record describing a file the preview
    does serve — which is the split state `detach_folder` goes to some length to avoid.
    """
    orch, ds, agents = _ready(tmp_path)
    agents.write_bytes(NOT_UTF8)

    orch.attach_file(ds, "solo.csv")

    assert [e["path"] for e in _manifest(orch)] == ["public/data/sales_2026/solo.csv"]


def test_the_unreadable_bytes_are_left_exactly_as_they_were(tmp_path: Path):
    """The data-loss condition. Reading the file as `""` and splicing into that would save a file
    holding nothing but the managed block — template body, the person's instructions block and
    every other managed region gone. That is the regression #303 shipped and caught one layer up,
    and it is why the guard leaves the file alone rather than swallowing into `""`."""
    orch, ds, agents = _ready(tmp_path)
    agents.write_bytes(NOT_UTF8)

    orch.attach_file(ds, "solo.csv")

    assert agents.read_bytes() == NOT_UTF8


def test_detaching_a_file_survives_a_non_utf8_agents_md(tmp_path: Path):
    orch, ds, agents = _ready(tmp_path)
    attached = orch.attach_file(ds, "solo.csv")["path"]
    agents.write_bytes(NOT_UTF8)

    orch.detach_file(attached)

    assert _manifest(orch) == []


def test_detaching_a_folder_survives_a_non_utf8_agents_md(tmp_path: Path):
    """This one did not merely crash: the decode error was caught by `detach_folder`'s own
    `except (OSError, ValueError)` and re-raised as `DetachStopped`, which tells the person a
    removal stopped part way when in fact every file came out."""
    orch, ds, agents = _ready(tmp_path)
    orch.attach_folder(ds, "raw/2025")
    agents.write_bytes(NOT_UTF8)

    orch.detach_folder(ds, "raw/2025")

    assert _manifest(orch) == []


def test_binding_and_releasing_a_resource_survive_a_non_utf8_agents_md(tmp_path: Path):
    """The `_splice_agents` half. A Binding change rewrites a different managed region of the same
    file, through a different function, with the same bare read."""
    orch, ds, agents = _ready(tmp_path)
    agents.write_bytes(NOT_UTF8)

    orch.bind_dataset(ds)
    orch.unbind("dataset", ds)

    assert agents.read_bytes() == NOT_UTF8


def test_the_block_comes_back_once_the_file_is_readable_again(tmp_path: Path):
    """The shrug is a skipped render, not a lost one. Every managed block is rebuilt from the
    manifest and the Bindings on each act, so repairing the encoding and attaching again restores
    what the skipped writes would have said — which is the other half of why refusing the act was
    the wrong trade."""
    orch, ds, agents = _ready(tmp_path)
    agents.write_bytes(NOT_UTF8)
    orch.attach_file(ds, "solo.csv")
    assert agents.read_bytes() == NOT_UTF8

    agents.write_text("# Sage app\n")
    orch.attach_folder(ds, "raw/2025")

    body = agents.read_text()
    assert "## Attached data" in body
    assert "solo.csv" in body          # the act that was skipped, rendered now
    assert "raw/2025" in body


def test_a_skipped_block_still_moves_the_running_turns_baseline(tmp_path: Path):
    """The shrug must not take `_rebaseline_turn` with it.

    Attach endpoints do not hold the turn lock, so `_write_agents_data_block` ends by moving a
    running turn's working-tree baseline over the writes the act just made. Those are not only
    AGENTS.md: `_ensure_gitignored` has already written `.gitignore`, which is tracked. Returning
    early from the splice skips the rebaseline, the turn's end-of-run comparison then blames the
    agent for a file Sage wrote, and on a read-only turn that is a bogus "gate violated" and a
    `discard_changes()` that deletes what the person just attached (#37). So the guard steps over
    the splice, not out of the method.
    """
    orch, ds, agents = _ready(tmp_path)
    project = orch.project(start_preview=False)
    project.turn_tree_baseline = "a-baseline-taken-before-the-attach"
    agents.write_bytes(NOT_UTF8)

    orch.attach_file(ds, "solo.csv")

    assert project.turn_tree_baseline != "a-baseline-taken-before-the-attach"
