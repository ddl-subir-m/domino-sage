"""`examples/<id>` folders that no Thread record claims are removed on the way into a Project.

The reverse of the leak in `test_a_restarted_builder_still_has_the_charts_its_conversations_made`.
That one committed the Chat Artifacts so a restart stops losing them; this is the bill for it. An
`examples/<id>` whose `.sage/threads/<id>` is gone entirely was invisible while the tree was
ignored — one stale directory on one volume. Committed, it is bytes in every clone of the Project,
for good, with nothing pointing at it.

Nothing swept them before. `tombstoned_ids` starts from a record and these have none: `delete`
always leaves `meta.json` behind (ADR-0036), so a Thread this store deleted is never one of these.
They come from the delete that predates that ADR, which removed the record whole, and from a
`.sage/threads/` that came back half-restored beside an `examples/` that came back whole.

Two things must survive the sweep, and each has a test below: a person's own folder under
`examples/`, and the Artifacts a live Built App's committed handoff digest still names — the same
answer `_artifacts_are_spoken_for` gives on the tombstone path, asked the only way an orphan
leaves open.
"""
from __future__ import annotations

from pathlib import Path

from sage.orchestrator.service import Orchestrator
from sage.router.models import ModelCatalog
from sage.workspace.threads import ThreadStore

from .fake_opencode import FakeOpenCode

CHART = b"\x89PNG events by drug"


def _template(tmp: Path) -> Path:
    t = tmp / "template"
    (t / "src").mkdir(parents=True, exist_ok=True)
    (t / "src" / "App.tsx").write_text("export default function App() { return null }\n")
    (t / "package.json").write_text('{"name": "template"}')
    (t / "AGENTS.md").write_text("# Building this app\n")
    (t / ".gitignore").write_text("node_modules\ndist\n")
    return t


def _orch(tmp: Path) -> tuple[Orchestrator, Path]:
    root = tmp / "mnt" / "code"
    orch = Orchestrator(workspace_dir=root, template=_template(tmp), gateway=object(),
                        catalog=ModelCatalog(sovereign_plan="s", sovereign_implement="s",
                                             sovereign_ask="s", plan="p", implement="i", ask="a"),
                        project_id="Sage", opencode_client=FakeOpenCode(root, []))
    return orch, root


def _artifacts(root: Path, thread_id: str) -> Path:
    d = root / "examples" / thread_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "chart1_events_by_drug.png").write_bytes(CHART)
    return d


def test_an_orphan_is_removed(tmp_path: Path):
    """The whole point: no record, so nothing can ever show these again."""
    orch, root = _orch(tmp_path)
    root.mkdir(parents=True, exist_ok=True)
    orphan = _artifacts(root, "thr_gone")

    orch.project(start_preview=False)

    assert not orphan.exists()


def test_a_live_conversations_artifacts_are_not_touched(tmp_path: Path):
    """The failure that would matter. A live Thread has a record, so it is never a candidate."""
    orch, root = _orch(tmp_path)
    project = orch.project(start_preview=False)
    store = ThreadStore(project.record.path)
    live = store.create("visualize the support data")["id"]
    kept = _artifacts(root, live)
    orphan = _artifacts(root, "thr_gone")

    orch._sweep_orphaned_artifacts(project)

    assert (kept / "chart1_events_by_drug.png").read_bytes() == CHART
    assert not orphan.exists()


def test_a_tombstoned_conversation_is_left_to_the_other_sweep(tmp_path: Path):
    """A delete leaves `meta.json`, so a tombstone is a record — and `_sweep_deleted_conversations`
    owns it, under the `_artifacts_are_spoken_for` rule this sweep cannot evaluate."""
    orch, root = _orch(tmp_path)
    project = orch.project(start_preview=False)
    store = ThreadStore(project.record.path)
    tid = store.create("deleted, but spoken for")["id"]
    _artifacts(root, tid)
    store.delete(tid, purge_artifacts=False)   # a live app's digest names them

    assert store.orphaned_artifact_ids() == []
    orch._sweep_orphaned_artifacts(project)
    assert (root / "examples" / tid / "chart1_events_by_drug.png").exists()


def test_a_folder_a_person_made_is_left_alone(tmp_path: Path):
    """`examples/` is an ordinary directory at the Project root. Only `thr_`-shaped names go."""
    orch, root = _orch(tmp_path)
    root.mkdir(parents=True, exist_ok=True)
    mine = root / "examples" / "my-own-notes"
    mine.mkdir(parents=True)
    (mine / "scratch.md").write_text("mine")

    orch.project(start_preview=False)

    assert (mine / "scratch.md").read_text() == "mine"


def test_an_orphan_a_live_apps_digest_still_names_is_kept(tmp_path: Path):
    """The data-loss guard. The digest is the only evidence left once the record is gone.

    Without this the sweep deletes exactly the files a live plan document points at — which is the
    case `_artifacts_are_spoken_for` exists to prevent, arriving by the one door it cannot watch."""
    orch, root = _orch(tmp_path)
    project = orch.project(start_preview=False)
    spoken_for = _artifacts(root, "thr_named")
    plain = _artifacts(root, "thr_gone")
    digest = project.workspace.path / ".sage" / "handoff.md"
    digest.parent.mkdir(parents=True, exist_ok=True)
    digest.write_text("## Artifacts\n\n- `examples/thr_named/chart1_events_by_drug.png` — by drug\n")

    orch._sweep_orphaned_artifacts(project)

    assert (spoken_for / "chart1_events_by_drug.png").read_bytes() == CHART
    assert not plain.exists()


def test_a_project_still_on_the_legacy_index_keeps_every_thread(tmp_path: Path):
    """The one that would have deleted everything.

    A Project written before ADR-0008 keeps its Threads in `.sage/threads.json` — the scan finds no
    `meta.json`, so without the adoption every LIVE Thread's Artifacts read as orphaned."""
    orch, root = _orch(tmp_path)
    root.mkdir(parents=True, exist_ok=True)
    (root / ".sage").mkdir(parents=True, exist_ok=True)
    (root / ".sage" / "threads.json").write_text(
        '[{"id": "thr_legacy", "title": "an old conversation"}]')
    kept = _artifacts(root, "thr_legacy")

    orch.project(start_preview=False)

    assert (kept / "chart1_events_by_drug.png").read_bytes() == CHART
