"""The third holder question on `remove_project_resource` (ADR-0048, #263).

The removal asked which Built Apps **bind** a Resource and which conversations hold a **chip** on
it, and never which apps carry **files** from it. So a Dataset a Build mention had attached left the
working set without a word, and what stayed behind had no way out: the sensitivity lock still armed,
showing in Chat, naming no Dataset, and its only release control a removal in Build for a row that
no longer exists.

The refusal is one sentence for all three classes. Two holders that report one at a time send a
person off to fix it and straight back into a second refusal they were never warned about.

TEST TRAP, because it cost a cycle and looks like the feature is broken rather than the test:
`_attached_per_app` prefers the list held in MEMORY for the app on screen and for the app a running
turn pinned (#77), and reads the manifest only for the rest. So writing `attachments.json` for the
app the test is standing on proves nothing — the guard reads an empty in-memory list and lets the
removal through. Every test below selects away from the carrying app, which is also the case the
guard exists for.
"""
from pathlib import Path

import pytest

from sage.orchestrator.service import (
    FOLDER_COLLAPSE_THRESHOLD,
    Orchestrator,
    ResourceStillBound,
    _named_with_rest,
)
from sage.resources.bindings import KIND_DATASET, Binding
from sage.router.models import ModelCatalog


def _catalog() -> ModelCatalog:
    return ModelCatalog(sovereign_plan="s", sovereign_implement="s", sovereign_ask="s",
                        plan="p", implement="i", ask="a")


def _orch(tmp: Path) -> Orchestrator:
    template = tmp / "template"
    (template / "src").mkdir(parents=True)
    (template / "src" / "App.tsx").write_text("export default function App() { return null }\n")
    (template / "package.json").write_text("{}")
    orch = Orchestrator(workspace_dir=tmp / "mnt" / "code", template=template, gateway=object(),
                        catalog=_catalog(), project_id="Sage")
    orch.project(start_preview=False)
    return orch


def _new_app(orch: Orchestrator, name: str) -> str:
    app_id = orch._wm.create_app("Sage").app_id
    orch.select_app(app_id)
    orch.rename_app(app_id, name)
    return app_id


def _selected(orch: Orchestrator):
    return orch.project(start_preview=False).workspace


def _add_dataset(orch: Orchestrator) -> None:
    orch.add_project_resource({
        "id": "dataset:ds-1", "kind": "dataset", "name": "sales_2026",
    })


def _carry(orch: Orchestrator, *rel: str, dataset_id: str = "ds-1") -> None:
    """Write the SELECTED app's attachment manifest — what `attach_file` leaves behind, which is
    the record the guard reads. The act itself needs a Dataset mounted on this disk, and what is
    under test is the question the removal asks rather than the copy that answers it."""
    _selected(orch).write_attachments([
        {"path": f"public/data/sales_2026/{r}", "dataset": "sales_2026",
         "dataset_id": dataset_id, "file": Path(r).name, "source": "dataset",
         "dataset_rel_path": r}
        for r in rel
    ])


def _bind_the_dataset(orch: Orchestrator) -> None:
    binding = Binding(KIND_DATASET, "ds-1", "sales_2026", "sales_2026")
    _selected(orch).update_bindings(lambda _: [binding.to_dict()])


def test_removing_a_dataset_an_app_carries_is_refused(tmp_path: Path):
    """The bug, from the app nobody was looking at. `project.attached` is one app's tree, so the
    question has to be asked of every Built App the way the Binding question already is."""
    orch = _orch(tmp_path)
    _add_dataset(orch)
    first = _selected(orch).app_id
    _new_app(orch, "Support Pulse")
    _carry(orch, "q3.csv")
    orch.select_app(first)                      # look away from the app that carries it

    with pytest.raises(ResourceStillBound) as refused:
        orch.remove_project_resource("dataset:ds-1")

    assert refused.value.apps == ["Support Pulse"]
    assert refused.value.carriers == ["sales_2026"]
    assert "Support Pulse still needs sales_2026" in str(refused.value)
    assert [r["id"] for r in orch.list_project_resources()] == ["dataset:ds-1"]


def test_a_dataset_no_app_carries_still_leaves_the_project(tmp_path: Path):
    """The guard refuses; it does not hold on. An app carrying some OTHER Dataset's files is not
    a holder of this one, and a question that cannot say no is not a question."""
    orch = _orch(tmp_path)
    _add_dataset(orch)
    first = _selected(orch).app_id
    _new_app(orch, "Support Pulse")
    _selected(orch).write_attachments([
        {"path": "public/data/churn_2025/q3.csv", "dataset": "churn_2025",
         "dataset_id": "ds-9", "file": "q3.csv", "source": "dataset"},
    ])
    orch.select_app(first)

    assert orch.remove_project_resource("dataset:ds-1") is True
    assert orch.list_project_resources() == []


def test_the_refusal_rolls_a_hundred_carried_files_up_to_their_folder(tmp_path: Path):
    """A folder attach writes one entry per file (ADR-0029), so the refusal has to survive a
    hundred of them. `_by_folder` is the same roll-up the managed block and the `@` menu use, and
    the truncation beside it is the same one — a second copy of either is how they come to
    disagree about what a Dataset looks like."""
    orch = _orch(tmp_path)
    _add_dataset(orch)
    first = _selected(orch).app_id
    _new_app(orch, "Support Pulse")
    _carry(orch, *(f"raw/2026/{i:02d}/part.csv" for i in range(1, 13)),
           *(f"curated/part_{i:03d}.csv" for i in range(100)))
    orch.select_app(first)

    with pytest.raises(ResourceStillBound) as refused:
        orch.remove_project_resource("dataset:ds-1")

    # Two names for 112 files, and the shape the roll-up chose rather than a floor it was pushed
    # to: `_by_folder` stops as soon as there are few enough groups to read, so a Dataset
    # partitioned to the month keeps the level that says something.
    assert refused.value.carriers == ["sales_2026/curated, sales_2026/raw/2026"]
    assert "part.csv" not in str(refused.value)


def test_more_folders_than_the_threshold_are_cut_with_a_count():
    """The roll-up's floor is `public/data/<slug>`, which is one folder for one Dataset — so the
    cut is what stands between the panel and a Dataset whose files two apps carry under a hundred
    served roots. It reuses the truncation rather than reinventing a second cut."""
    names = [f"sales_2026/f{i:03d}" for i in range(FOLDER_COLLAPSE_THRESHOLD + 5)]

    said = _named_with_rest(names)

    assert said.endswith(", and 5 more")
    assert said.count("sales_2026/") == FOLDER_COLLAPSE_THRESHOLD


def test_an_app_that_binds_and_an_app_that_carries_refuse_once_naming_both(tmp_path: Path):
    """Three questions, one refusal. Reporting the binding app and going quiet about the carrying
    one sends the creator to unbind and straight back into a second refusal."""
    orch = _orch(tmp_path)
    _add_dataset(orch)
    orch.rename_app(_selected(orch).app_id, "Desk exposure")
    _bind_the_dataset(orch)
    first = _selected(orch).app_id
    _new_app(orch, "Support Pulse")
    _carry(orch, "q3.csv")
    orch.select_app(first)

    with pytest.raises(ResourceStillBound) as refused:
        orch.remove_project_resource("dataset:ds-1")

    assert refused.value.apps == ["Desk exposure", "Support Pulse"]      # oldest app first
    # Named with its app, for the reason `refs` names its own: the served root is the same string
    # in every app that carries it, so a bare path would say neither which app nor how many.
    assert refused.value.carriers == ["Support Pulse — sales_2026"]
    assert "Desk exposure" in str(refused.value)
    assert "Support Pulse" in str(refused.value)


def test_one_app_that_both_binds_and_carries_is_one_holder(tmp_path: Path):
    """Two records of two different things (ADR-0039), and one app to go and visit. Naming it
    twice would read as two apps to open."""
    orch = _orch(tmp_path)
    _add_dataset(orch)
    orch.rename_app(_selected(orch).app_id, "Desk exposure")
    first = _selected(orch).app_id
    _new_app(orch, "Support Pulse")
    _bind_the_dataset(orch)
    _carry(orch, "q3.csv")
    orch.select_app(first)

    with pytest.raises(ResourceStillBound) as refused:
        orch.remove_project_resource("dataset:ds-1")

    assert refused.value.apps == ["Support Pulse"]
    assert refused.value.carriers == ["sales_2026"]


def test_an_entry_that_kept_no_dataset_id_still_holds_it_by_its_served_root(tmp_path: Path):
    """`_rehydrate_attached`'s symlink scan writes no `dataset_id`, so a workspace rebuilt from a
    clone records only where the files are served from. The third question inherits that predicate
    whole, collision and all — two Datasets that slugify alike meet there (ADR-0048)."""
    orch = _orch(tmp_path)
    _add_dataset(orch)
    first = _selected(orch).app_id
    _new_app(orch, "Support Pulse")
    _selected(orch).write_attachments([
        {"path": "public/data/sales_2026/q3.csv", "dataset": "sales_2026", "file": "q3.csv"},
    ])
    orch.select_app(first)

    with pytest.raises(ResourceStillBound) as refused:
        orch.remove_project_resource("dataset:ds-1")

    assert refused.value.apps == ["Support Pulse"]


def test_a_data_source_is_asked_no_carry_question(tmp_path: Path):
    """Only a Dataset has files to carry. Every other kind is a record with nothing behind it, so
    the question has no answer to give rather than a negative one — and a Data Source whose id
    happens to sit under a Dataset's served root must not be held back by it."""
    orch = _orch(tmp_path)
    orch.add_project_resource({
        "id": "data_source:ds-1", "kind": "datasource", "name": "sales_2026",
    })
    first = _selected(orch).app_id
    _new_app(orch, "Support Pulse")
    _carry(orch, "q3.csv")
    orch.select_app(first)

    assert orch.remove_project_resource("data_source:ds-1") is True
    assert orch.list_project_resources() == []


def test_the_route_answers_409_naming_the_apps_that_carry_its_files(tmp_path: Path, monkeypatch):
    """The panel reads `carriers` off the body the way it reads `refs`, so the contract lives in
    the API rather than only in the markup that renders it."""
    from fastapi.testclient import TestClient

    import sage.orchestrator.app as appmod

    orch = _orch(tmp_path)
    _add_dataset(orch)
    first = _selected(orch).app_id
    _new_app(orch, "Support Pulse")
    _carry(orch, "q3.csv")
    orch.select_app(first)
    monkeypatch.setattr(appmod, "orchestrator", orch)

    answer = TestClient(appmod.control_app).request(
        "DELETE", "/api/project/resources", params={"id": "dataset:ds-1"})

    assert answer.status_code == 409
    assert answer.json()["apps"] == ["Support Pulse"]
    assert answer.json()["carriers"] == ["sales_2026"]
    assert answer.json()["refs"] == []


def test_dropping_the_attachment_record_lets_the_dataset_leave(tmp_path: Path):
    """The refusal is a door rather than a wall: detaching the files is the act that releases it,
    and the retry after it has to go through."""
    orch = _orch(tmp_path)
    _add_dataset(orch)
    first = _selected(orch).app_id
    app = _new_app(orch, "Support Pulse")
    _carry(orch, "q3.csv")
    orch.select_app(first)

    orch._wm.app_workspace("Sage", app).write_attachments([])

    assert orch.remove_project_resource("dataset:ds-1") is True
    assert orch.list_project_resources() == []


def test_a_manifest_that_will_not_parse_holds_nothing_back(tmp_path: Path):
    """`read_attachments` answers `[]` for an unreadable manifest, and the guard inherits that
    rather than second-guessing it — a Resource no readable record claims leaves the project."""
    orch = _orch(tmp_path)
    _add_dataset(orch)
    first = _selected(orch).app_id
    _new_app(orch, "Support Pulse")
    path = _selected(orch).attachments_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ not json")
    orch.select_app(first)

    assert orch.remove_project_resource("dataset:ds-1") is True


def test_the_folder_is_named_in_the_words_the_destination_uses(tmp_path: Path):
    """A pointer names its destination in the words the reader will see on arrival (ADR-0011). The
    destination is the App dependencies modal, whose "Files it carries" rows draw a basename over
    the DATASET NAME — `public/data/<slug>/` is on screen nowhere, and the slug is a second spelling
    of a name the reader already has."""
    orch = _orch(tmp_path)
    orch.add_project_resource({"id": "dataset:ds-2", "kind": "dataset", "name": "My Data"})
    first = _selected(orch).app_id
    _new_app(orch, "Support Pulse")
    _selected(orch).write_attachments([
        {"path": "public/data/My_Data/raw/q3.csv", "dataset": "My Data",
         "dataset_id": "ds-2", "file": "q3.csv", "source": "dataset"},
    ])
    orch.select_app(first)

    with pytest.raises(ResourceStillBound) as refused:
        orch.remove_project_resource("dataset:ds-2")

    assert refused.value.carriers == ["My Data/raw"]
    assert "public/data" not in str(refused.value.carriers)
    assert "My_Data" not in str(refused.value.carriers)


def test_a_dataset_renamed_after_the_attach_is_still_named_not_pathed(tmp_path: Path):
    """The files go on being served from the OLD slug and the entry goes on matching by
    `dataset_id`, so a strip that only knew today's name would fail and print the raw
    `public/data/<old-slug>/raw` — the one form ADR-0011 says must not reach the reader.

    HALF THE PROPERTY, and the half that is missing is named here so this test is not read as
    guaranteeing the whole. ADR-0011 wants the pointer and its destination to print the same
    string. This pins the POINTER. The destination — the App dependencies modal's "Files it
    carries" rows — draws its subtitle through `attachmentRow` (`util.js`), which shows the
    ATTACH-TIME `entry.dataset` when the entry kept a Dataset id and the served path when it kept
    none. So a renamed Dataset still reads `Sales 2026 EMEA/raw` in the refusal and `sales 2026`
    in the modal.

    Deliberately not fixed here: `attachmentRow` is shared by three readers — that list, the `@`
    menu and `collectTurnRefs` — and changing what all of them see is not this ticket's to do.
    Tracked separately."""
    orch = _orch(tmp_path)
    orch.add_project_resource({"id": "dataset:ds-3", "kind": "dataset", "name": "sales 2026"})
    first = _selected(orch).app_id
    _new_app(orch, "Support Pulse")
    _selected(orch).write_attachments([
        {"path": "public/data/sales_2026/raw/q3.csv", "dataset": "sales 2026",
         "dataset_id": "ds-3", "file": "q3.csv", "source": "dataset"},
    ])
    orch.select_app(first)
    orch.project(start_preview=False).record.update_project_resources(
        lambda items: [{**r, "name": "Sales 2026 EMEA"} if r["id"] == "dataset:ds-3" else r
                       for r in items])

    with pytest.raises(ResourceStillBound) as refused:
        orch.remove_project_resource("dataset:ds-3")

    assert refused.value.carriers == ["Sales 2026 EMEA/raw"]
    assert "public/data" not in str(refused.value.carriers)


def test_a_row_with_no_name_does_not_refuse_on_another_datasets_files(tmp_path: Path):
    """`_attach_root("")` is `public/data/`, which every Dataset attachment in the project sits
    under. Without a floor, a nameless row's probe matches the served-root half against somebody
    else's files and refuses its own removal by naming their folders."""
    orch = _orch(tmp_path)
    orch.project(start_preview=False).record.update_project_resources(
        lambda items: items + [{"id": "dataset:ds-9", "kind": "dataset", "name": ""}])
    first = _selected(orch).app_id
    _new_app(orch, "Support Pulse")
    _carry(orch, "q3.csv")                      # belongs to sales_2026, not to the nameless row
    orch.select_app(first)

    assert orch.remove_project_resource("dataset:ds-9") is True


def test_the_working_set_row_names_the_dataset_the_refusal_is_about(tmp_path: Path):
    """A carry-only refusal has no Binding to take a label from, so the name comes off the
    membership row — an id in a sentence meant to say `sales_2026` is the same dead end the
    conversation guard already fixed."""
    orch = _orch(tmp_path)
    _add_dataset(orch)
    first = _selected(orch).app_id
    _new_app(orch, "Support Pulse")
    _carry(orch, "q3.csv")
    orch.select_app(first)

    with pytest.raises(ResourceStillBound) as refused:
        orch.remove_project_resource("dataset:ds-1")

    assert refused.value.name == "sales_2026"
    assert "ds-1" not in str(refused.value)
