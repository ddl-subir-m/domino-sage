"""The working set is complete, including Bindings made before membership-on-bind existed (#140).

`_join_project_on_bind` landed on 2026-09-01 with no backfill, so every Binding recorded before
that date has no membership row. Under ADR-0020 the section's only job is to be true, so that is a
correctness gap and not a cosmetic one: a creator opening a Project whose Snowflake Data Source was
bound last month reads a rail saying the Project uses nothing, beside an app that plainly does.

The repair is a ONE-SHOT migration, and ADR-0020 rejects the union on read it was weighed against.
The union was nearly free — `list_project_resources` already scans `_app_bindings()` to compute
`usedBy` — and it turns the file into a view: a Resource legitimately removed would reappear on the
next read for as long as any app bound it, and the noun would stop being definable. So the tests
below pin the migration AND pin the union's absence, because the cheap thing looking correct is
exactly why it needs a test standing in front of it.

The seam is the service, as it is in `test_membership_is_a_record_of_use.py`: `_record` is where a
live bind joins, `list_project_resources` is where the rail reads, and the migration belongs
between them rather than in a door or in the workspace.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from sage.gateway.client import FakeGatewayClient
from sage.orchestrator.service import _MEMBERSHIP_BACKFILLED, Orchestrator, ResourceStillBound
from sage.resources.bindings import KIND_DATA_SOURCE, KIND_LLM_ALIAS, KIND_MODEL_API, Binding
from sage.router.models import ModelCatalog

# A name no template, no fixture and no piece of Sage's own copy contains, so a single occurrence
# anywhere under the volume is this Resource and nothing else.
SENTINEL = "Zzyzx-Membership-Only-Warehouse"


def _orch(tmp_path: Path) -> Orchestrator:
    """Open the Project on this volume. Called twice per test: once to lay the volume out the way
    an older Sage left it, and once to open it again the way a creator does — the migration runs on
    the way in, so "opened again" is the act under test."""
    template = tmp_path / "template"
    (template / "src").mkdir(parents=True, exist_ok=True)
    (template / "src" / "App.tsx").write_text("export default function App() { return null }\n")
    (template / "package.json").write_text("{}")
    orch = Orchestrator(
        workspace_dir=tmp_path / "mnt" / "code", template=template,
        gateway=FakeGatewayClient(),
        catalog=ModelCatalog(sovereign_plan="s", sovereign_implement="s", sovereign_ask="s",
                             plan="p", implement="i", ask="a"),
        project_id="Sage",
    )
    orch.project(start_preview=False)
    return orch


def _bound_before_the_join(orch: Orchestrator, *bindings: Binding) -> None:
    """Bindings as an older Sage left them: in the app's manifest, with no membership row beside.

    Written straight to the manifest rather than through `bind_*`, because every bind method now
    passes through `_record` and joins on the way — which is the state this migration exists to
    repair the absence of. Whatever else is in the working set stays: a Project from before the
    join still has everything Browse Domino put there, and only the bound Resources are missing.
    """
    orch.project(start_preview=False).workspace.update_bindings(
        lambda entries: entries + [b.to_dict() for b in bindings])
    _forget_the_migration(orch)


def _forget_the_migration(orch: Orchestrator) -> None:
    """Put the volume back to before the migration ran, so the next open is a first open.

    The flag is written on attach, and these tests attach once to lay the volume out. Clearing it
    here rather than writing the manifests behind a closed Project keeps the fixture honest about
    what it is faking: the calendar, not the file format.
    """
    record = orch.project(start_preview=False).record
    settings = record.read_settings()
    settings.pop(_MEMBERSHIP_BACKFILLED, None)
    record.write_settings(settings)


def _opened_again(tmp_path: Path) -> Orchestrator:
    """The same volume, opened by a second Orchestrator — the migration runs on the way in, so this
    is the act every test below is really about."""
    return _orch(tmp_path)


def _member(orch: Orchestrator, resource_id: str) -> dict:
    return next(r for r in orch.list_project_resources() if r["id"] == resource_id)


# ---- The gap the migration closes ---------------------------------------------------------------


def test_a_data_source_bound_before_the_join_shipped_reaches_the_working_set(tmp_path: Path):
    _bound_before_the_join(_orch(tmp_path), Binding(
        KIND_DATA_SOURCE, "ds-dwh", "Snowflake-Data-Warehouse", "Snowflake-Data-Warehouse",
        "DWH", "MARTS", None, "SnowflakeConfig"))

    row = _member(_opened_again(tmp_path), "data_source:ds-dwh")

    assert row["kind"] == "data_source"
    assert row["name"] == "Snowflake-Data-Warehouse"
    # The prefixed id, the space every other surface keys a Resource on. A bare `ds-dwh` would draw
    # a second row for a Resource the Project already holds and leave the removal guard unable to
    # join the two.
    assert row["bindingKey"] == ["data_source", "ds-dwh"]
    # The rail's subtitle reads off the app's own manifest, so the migrated row arrives with the
    # answer to "where is this used" already attached.
    assert [u["scope"] for u in row["usedBy"]] == ["DWH.MARTS"]


def test_every_bound_kind_is_backfilled(tmp_path: Path):
    """All three kinds a Binding can name, because the gap is per Binding and not per kind."""
    _bound_before_the_join(
        _orch(tmp_path),
        Binding(KIND_LLM_ALIAS, "f-sonnet", "sonnet", "Claude Sonnet 4.6"),
        Binding(KIND_MODEL_API, "f-churn", "churn-risk", "churn-risk"),
        Binding(KIND_DATA_SOURCE, "ds-dwh", "Snowflake-Data-Warehouse",
                "Snowflake-Data-Warehouse"),
    )

    assert sorted(r["id"] for r in _opened_again(tmp_path).list_project_resources()) == [
        "data_source:ds-dwh", "llm_alias:f-sonnet", "model_api:f-churn",
    ]


def test_a_backfilled_alias_is_one_the_model_picker_can_select(tmp_path: Path):
    """The field that makes an Alias row usable rather than merely present.

    The picker falls back to these rows whenever the live Alias listing is unavailable, and matches
    on `alias` — the gateway name the model is called by. A row written without it is an option
    drawn blank that nothing can select, which is the defect ADR-0018 names. The migration reuses
    `_join_project_on_bind`, so what a live bind writes out of the Binding, this writes too.
    """
    _bound_before_the_join(_orch(tmp_path),
                           Binding(KIND_LLM_ALIAS, "f-sonnet", "sonnet", "Claude Sonnet 4.6"))

    row = _member(_opened_again(tmp_path), "llm_alias:f-sonnet")

    assert row["alias"] == "sonnet"
    assert row["name"] == "Claude Sonnet 4.6"
    assert row["bindingKey"] == ["llm_alias", "f-sonnet"]


def test_bindings_across_several_built_apps_are_all_backfilled(tmp_path: Path):
    """A Binding names one app and a Project holds many (ADR-0008). The migration reads every app's
    manifest, or a Project whose oldest app predates the join keeps a rail that is still wrong."""
    orch = _orch(tmp_path)
    _bound_before_the_join(orch, Binding(KIND_LLM_ALIAS, "f-sonnet", "sonnet", "Claude Sonnet 4.6"))
    orch.select_app(orch._wm.create_app("Sage").app_id)
    _bound_before_the_join(orch, Binding(
        KIND_DATA_SOURCE, "ds-dwh", "Snowflake-Data-Warehouse", "Snowflake-Data-Warehouse"))

    assert sorted(r["id"] for r in _opened_again(tmp_path).list_project_resources()) == [
        "data_source:ds-dwh", "llm_alias:f-sonnet",
    ]


# ---- Once, and only once ------------------------------------------------------------------------


def test_the_migration_writes_nothing_the_second_time(tmp_path: Path):
    """One-shot in the literal sense: the second read does not join, and does not touch the file.

    Not merely idempotent-by-luck. A backfill that recomputed the missing rows on every read would
    also write nothing here, and would quietly repair the very defect ADR-0020 says must stay
    visible — a future door that binds without joining is a correctness bug, not a gap for the rail
    to heal behind it.
    """
    _bound_before_the_join(_orch(tmp_path),
                           Binding(KIND_LLM_ALIAS, "f-sonnet", "sonnet", "Claude Sonnet 4.6"))
    migrated = _opened_again(tmp_path)
    first = migrated.list_project_resources()
    path = migrated.project(start_preview=False).record.project_resources_path
    written = path.read_bytes()

    joins = {"n": 0}
    original = Orchestrator._join_project_on_bind
    Orchestrator._join_project_on_bind = (
        lambda self, new, catalogue: (joins.__setitem__("n", joins["n"] + 1),
                                      original(self, new, catalogue))[1])
    try:
        again = _opened_again(tmp_path).list_project_resources()
    finally:
        Orchestrator._join_project_on_bind = original

    assert joins["n"] == 0
    assert again == first
    assert path.read_bytes() == written


def test_a_row_the_project_already_holds_is_not_renamed_by_the_migration(tmp_path: Path):
    """The name on the row is the one this Project has. The migration records use; it is not a
    reason to move the name the creator is already reading in the rail."""
    orch = _orch(tmp_path)
    orch.add_project_resource({
        "id": "llm_alias:f-sonnet", "kind": "model_llm", "name": "Sonnet, as we call it here",
    })
    _bound_before_the_join(orch, Binding(KIND_LLM_ALIAS, "f-sonnet", "sonnet", "Claude Sonnet 4.6"))

    rows = _opened_again(tmp_path).list_project_resources()

    assert len(rows) == 1
    assert rows[0]["name"] == "Sonnet, as we call it here"
    assert rows[0]["kind"] == "model_llm"


def test_a_project_with_nothing_to_backfill_is_left_exactly_as_it_was(tmp_path: Path):
    orch = _orch(tmp_path)

    assert orch.list_project_resources() == []
    assert not orch.project(start_preview=False).record.project_resources_path.exists()


# ---- The union on read, which ADR-0020 rejected -------------------------------------------------


def test_the_membership_file_stays_the_single_source_of_truth(tmp_path: Path):
    """The union's tell, and the reason it was rejected: a Resource gone from the file must stay
    gone even while an app still binds it. Under a union it would reappear on the next read, and
    "Remove from <project>" would become a no-op that looks like a bug."""
    _bound_before_the_join(_orch(tmp_path),
                           Binding(KIND_LLM_ALIAS, "f-sonnet", "sonnet", "Claude Sonnet 4.6"))
    migrated = _opened_again(tmp_path)
    assert [r["id"] for r in migrated.list_project_resources()] == ["llm_alias:f-sonnet"]

    # Emptied on disk, which is what a removal would leave behind if the guard were not in front of
    # it — the state a union cannot tell from a Project that never joined.
    path = migrated.project(start_preview=False).record.project_resources_path
    path.write_text(json.dumps({"items": []}))

    assert migrated.list_project_resources() == []
    # And still empty on the next open, because the migration has already run for this Project.
    assert _opened_again(tmp_path).list_project_resources() == []


def test_remove_from_the_project_still_refuses_while_a_built_app_binds_it(tmp_path: Path):
    """The migration writes the row the removal guard reads. Backfilling a Resource and then
    letting it be dropped out from under the app that binds it would be worse than not
    backfilling at all (ADR-0011)."""
    _bound_before_the_join(_orch(tmp_path), Binding(
        KIND_DATA_SOURCE, "ds-dwh", "Snowflake-Data-Warehouse", "Snowflake-Data-Warehouse"))
    orch = _opened_again(tmp_path)
    assert [r["id"] for r in orch.list_project_resources()] == ["data_source:ds-dwh"]

    with pytest.raises(ResourceStillBound, match="Snowflake-Data-Warehouse"):
        orch.remove_project_resource("data_source:ds-dwh")

    orch.unbind(KIND_DATA_SOURCE, "ds-dwh")
    assert orch.remove_project_resource("data_source:ds-dwh") is True


# ---- The explicit no: orientation, never context (ADR-0020) -------------------------------------


def _files_naming(root: Path, needle: str) -> list[Path]:
    out: list[Path] = []
    for p in sorted(root.rglob("*")):
        if not p.is_file() or ".git" in p.parts:
            continue
        try:
            text = p.read_text(errors="ignore")
        except OSError:
            continue
        if needle in text:
            out.append(p)
    return out


def test_the_working_set_reaches_no_prompt_and_no_instruction_file(tmp_path: Path):
    """A decision pinned, not an omission repaired.

    An injected working set would tell the agent about Resources it cannot reach: membership gates
    nothing, an app reaches a Data Source because it holds a Binding, and publish reads that Binding
    and nothing else. So a list of members handed to the model is a list of things it will believe
    are usable and then fail on — the same false availability that made the dead ends in #132, only
    reissued to the model instead of to the person (ADR-0020).

    A member with no Binding and no Session context chip is the whole case: the agent is told about
    dependency, and told about membership by nothing at all.
    """
    orch = _orch(tmp_path)
    orch.add_project_resource({
        "id": "data_source:ds-zzyzx", "kind": "data_source", "name": SENTINEL,
        "description": SENTINEL,
    })
    project = orch.project(start_preview=False)
    # Everything Sage writes on the creator's behalf for the agent to read, run the way a turn runs
    # it: the pinned-Resource files, the AGENTS.md data block, and the turn's own inputs.
    orch._write_app_resources(project)
    orch._write_agents_data_block(project)
    orch._refresh_agent_inputs(project)

    assert _files_naming(project.record.path, SENTINEL) == [project.record.project_resources_path]
    # The Chat prompt, composed with no Session context, is the one place a Resource name reaches
    # the model as prose — and it carries chips, never members.
    assert SENTINEL not in orch._chat_prompt("t1", "which warehouses do we have?", {"items": []})


def test_nothing_outside_the_rail_reads_the_working_set(tmp_path: Path) -> None:
    """The other half of the same no, and the durable half.

    "It reaches no prompt, no instruction file and no tool listing" is a claim about every caller,
    which no single turn can demonstrate. What can be pinned is that the list has no reader outside
    the rail's own read path, its own writer, and the one-shot migration between them — so no prompt
    builder, no AGENTS.md region and no tool listing is in a position to carry it.

    A new entry here is not a test to update. It is a reader to justify against ADR-0020 first.
    """
    root = Path(__file__).resolve().parents[1] / "sage"
    allowed = {
        # The rail's list endpoint — the whole reason the file exists (ADR-0020).
        ("orchestrator/service.py", "list_project_resources"),
        # The one-shot backfill, which reads to find what is missing (#140).
        ("orchestrator/service.py", "_backfill_membership_from_bindings"),
        # Its own writer, reading before it republishes.
        ("workspace/manager.py", "update_project_resources"),
    }

    found: set[tuple[str, str]] = set()

    class Readers(ast.NodeVisitor):
        """Every call, credited to the function that makes it rather than to every function it
        happens to sit inside — a nested `change()` closure is its own caller."""

        def __init__(self, module: str) -> None:
            self.module = module
            self.stack: list[str] = []

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

        visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

        def visit_Call(self, node: ast.Call) -> None:
            if isinstance(node.func, ast.Attribute) and node.func.attr == "read_project_resources":
                found.add((self.module, self.stack[-1] if self.stack else "<module>"))
            self.generic_visit(node)

    for path in sorted(root.rglob("*.py")):
        Readers(path.relative_to(root).as_posix()).visit(ast.parse(path.read_text()))

    assert found == allowed
