"""A shared table reaches `values_allowed` as `True`, not as an `AttributeError` (#330).

`Orchestrator` defined `_shared_samples` twice. The second definition — the one grouping shared
tables by store for the AGENTS.md region — shadowed the first at class-creation time, so the Live
read grant was handed `[(store, [tables])]` where it declares `tuple[tuple[str, str], ...]`.
`grant.values_allowed` unpacks each pair and calls `.casefold()` on the second element, and a list
has no `casefold`.

It stayed quiet because the shadowing definition filters out empty groups, so with nothing shared it
returns `[]` and `values_allowed` answers `False` — correct by accident. The fault arrives exactly
when a creator shares a table's rows, which is the one case `values_allowed` exists to answer `True`
for.

Driven through `_live_read_turn_for` rather than through a hand-built `run.Turn`, because a builder
that sets `shared=` itself is the one seam where this defect cannot appear: the two shapes agreeing
in a type annotation is precisely what failed here. The assertion is about what the ORCHESTRATOR
puts in the Turn.
"""
from __future__ import annotations

import json
from pathlib import Path

from sage.gateway.client import FakeGatewayClient
from sage.liveread import grant
from sage.orchestrator.service import SAMPLES_PATH, Orchestrator
from sage.resources.provider import FakeResourceProvider
from sage.router.models import ModelCatalog
from sage.workspace.threads import ThreadStore

TABLE = "DWH.MARTS.ORDERS"
BINDING = "bnd_warehouse"
OTHER_TABLE = "APP.PUBLIC.ACCOUNTS"
OTHER_BINDING = "bnd_app_db"


def _template(tmp: Path) -> Path:
    t = tmp / "template"
    (t / "src").mkdir(parents=True, exist_ok=True)
    (t / "src" / "App.tsx").write_text("placeholder")
    (t / "package.json").write_text("{}")
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


def _project_sharing_a_table(tmp_path: Path, orch: Orchestrator, *,
                             binding: str | None = BINDING):
    """A Project whose creator has shared one table's rows out of one bound Data Source.

    Both halves are written, because either alone hides the defect: with no shared table the
    shadowing method returns `[]` and the grant answers `False` without ever unpacking a pair.
    """
    project = orch.project(start_preview=False)
    project.workspace.update_bindings(lambda rows: rows + [{
        # `data_source`, which is what `parse_bindings` reads — an entry whose kind is spelled
        # `datasource` is DROPPED, and a dropped Binding makes the shadowing method return `[]`,
        # which is the one state where this defect cannot arise.
        "id": BINDING, "kind": "data_source", "name": "warehouse",
        "display_name": "warehouse", "database": "DWH", "schema": "MARTS",
    }])
    _share(project, TABLE, binding=binding)
    return project


def _share(project, table: str, *, binding: str | None = BINDING) -> None:
    """Add one shared table to the record. `binding=None` writes a pre-#33 entry, which names none.

    The Binding is a parameter and not a constant, because the entry that names no Binding is the one
    state a fixture writing `BINDING` every time cannot produce — and it is the state that decides
    whether the grant reaches across stores.
    """
    samples = project.workspace.path / SAMPLES_PATH
    samples.parent.mkdir(parents=True, exist_ok=True)
    raw = json.loads(samples.read_text()) if samples.exists() else {"tables": []}
    entry = {"name": table, "columns": ["ID", "TOTAL"], "rows": [[1, 9]]}
    if binding is not None:
        entry["binding"] = binding
    raw["tables"].append(entry)
    samples.write_text(json.dumps(raw))


def _thread(project) -> str:
    return str(ThreadStore(project.record.path).create("A conversation")["id"])


def test_the_turn_is_handed_binding_and_table_pairs(tmp_path: Path):
    """The shape, at the seam that was wrong. A pair of strings, not a store and a list."""
    orch = _orch(tmp_path)
    project = _project_sharing_a_table(tmp_path, orch)

    turn = orch._live_read_turn_for(_thread(project))

    assert list(turn.shared) == [(BINDING, TABLE)]


def test_a_shared_table_is_allowed_rather_than_raising(tmp_path: Path):
    """End to end, as the ticket measured it: the grant answers the question it exists to answer.

    `values_allowed` is asked exactly as `liveread.result` asks it, so a shape the grant cannot
    unpack reddens here rather than at a caller this test mocks out.
    """
    orch = _orch(tmp_path)
    project = _project_sharing_a_table(tmp_path, orch)
    turn = orch._live_read_turn_for(_thread(project))

    assert grant.values_allowed(BINDING, TABLE, shared=turn.shared) is True


def test_a_table_nobody_shared_is_still_refused(tmp_path: Path):
    """The other half of the grant, so the fix cannot be "answer True to everything"."""
    orch = _orch(tmp_path)
    project = _project_sharing_a_table(tmp_path, orch)
    turn = orch._live_read_turn_for(_thread(project))

    assert grant.values_allowed(BINDING, "DWH.MARTS.SALARIES", shared=turn.shared) is False


def _second_store_sharing_a_table(project) -> None:
    """A second bound Data Source with its own shared table.

    Two stores, because grouping by store is only OBSERVABLE with two: `_samples_section` names the
    store per group only once there is more than one group, so a single-store render says nothing
    about whether anything grouped. A first draft of the test below asserted the store's name over a
    one-store region and passed — off a sentence in a LATER section about a Binding that is asked
    nothing. Green, and about nothing.
    """
    project.workspace.update_bindings(lambda rows: rows + [{
        "id": OTHER_BINDING, "kind": "data_source", "name": "app_db",
        "display_name": "app database", "database": "APP", "schema": "PUBLIC",
    }])
    _share(project, OTHER_TABLE, binding=OTHER_BINDING)


def _samples_section_of(agents: str) -> str:
    """The Sample rows section alone, ended at the next heading.

    Bounded on both sides, because the region carries several sections and the Binding id appears
    legitimately in the query-manifest example above this one. An unbounded tail reads the whole rest
    of the file, which is how the first draft of this test passed for the wrong reason.
    """
    tail = agents.split("### Sample rows", 1)[1]
    return tail.split("\n### ", 1)[0]


def test_the_agents_md_region_still_groups_the_shared_tables_by_store(tmp_path: Path):
    """The other half of the rename, rendered rather than read (#330, criterion 2).

    `_shared_samples_by_store` is what the AGENTS.md region asks, and it is the definition that used
    to hold the name. Nothing drove this path through the Orchestrator before: `test_bound_schema`
    hands `agents_block` a `samples=` it builds itself, which is the same blindness as building a
    `run.Turn` by hand — it cannot see which method the Orchestrator calls, or what shape arrives.

    So this renders the region off a Project sharing a table out of each of two stores, and asserts
    the sentence the agent reads. A rename that updated the definition and not the caller would
    `AttributeError` here; one wired to the grant's method would put Binding ids where the stores'
    names belong.
    """
    orch = _orch(tmp_path)
    project = _project_sharing_a_table(tmp_path, orch)
    _second_store_sharing_a_table(project)

    orch._write_app_data(project)
    section = _samples_section_of((project.workspace.path / "AGENTS.md").read_text())

    assert TABLE in section and OTHER_TABLE in section
    # Each table attributed to the store it came out of. This is the whole point of the grouping, and
    # it is what a Binding-keyed shape cannot say.
    assert "in **warehouse**" in section
    assert "in **app database**" in section
    assert BINDING not in section and OTHER_BINDING not in section, (
        "the section names the stores, never the Binding ids")


def test_the_two_methods_answer_different_questions(tmp_path: Path):
    """Both shapes, side by side, off one Project. The pair this ticket separated.

    Asserted together on purpose: the defect was that ONE name answered both questions, so a check
    that either answer is right on its own is what let it stand for as long as it did.
    """
    orch = _orch(tmp_path)
    project = _project_sharing_a_table(tmp_path, orch)

    assert orch._shared_samples(project) == ((BINDING, TABLE),)
    assert orch._shared_samples_by_store(project) == [("warehouse", [TABLE])]


def test_a_pre_33_entry_does_not_grant_the_same_table_in_another_store(tmp_path: Path):
    """An entry that names no Binding is attributed, not treated as naming every store (#330).

    `.sage/samples.json` written before #33 carries no Binding. `values_allowed` is deliberately
    tolerant there — an empty Binding matches whatever it is asked about — and `_shared` is the method
    that makes that safe, by resolving the empty one to the first Data Source, "the one the picker
    read from when there was no other".

    Reading the record raw skips that. Two stores bound, one legacy entry sharing ORDERS out of the
    warehouse, and the grant then answers True for ORDERS in the app database — a table whose rows the
    creator never shared. It was unreachable while the shadowing stood, because this path raised
    before it could answer, so un-shadowing is what made it live.
    """
    orch = _orch(tmp_path)
    project = _project_sharing_a_table(tmp_path, orch, binding=None)
    project.workspace.update_bindings(lambda rows: rows + [{
        "id": OTHER_BINDING, "kind": "data_source", "name": "app_db",
        "display_name": "app database",
    }])
    turn = orch._live_read_turn_for(_thread(project))

    # The first Data Source is where it can only have come from, so that one still answers True.
    assert grant.values_allowed(BINDING, TABLE, shared=turn.shared) is True
    assert grant.values_allowed(OTHER_BINDING, TABLE, shared=turn.shared) is False, (
        "a pre-#33 entry names no Binding; read raw it matches every store, which grants rows out of "
        "a store the creator never shared from")
