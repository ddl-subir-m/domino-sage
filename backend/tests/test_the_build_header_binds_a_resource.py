"""The Build header binds a Resource (#141, ADR-0021).

WHAT MOVED. Every act that adds is offered on the surface that owns the scope it writes. A Binding
is the Built App's, so its door is the app's own surface — the Build header — and not a row two
items below `Use in this chat` in a panel that owns no app scope. The labels were never the problem:
ADR-0011 had already made them name their scopes, and naming a scope does not carry the weight of
one act writing a chip and the next writing a manifest a published app depends on weeks later.

THIS WAS THE EXPAND HALF. The header's door opened while the panel's `Use in {app}` stayed exactly
where it was, so no window ever existed in which no door was open. #144 closed the window, and the
panel's absence is asserted from there — `test_the_resource_browser_stops_offering_use_in_app.py`.

WHAT WAS NOT HERE WHEN THIS SHIPPED. Data Sources. A Data Source Binding carried a Scope, the Scope
was still the cascade position the creator was standing on (#129), and a picker row had none to
pass — so the door for that kind waited until the Scope stopped being a cascade position. #142 did
that, and the kind is in the picker now: the assertions below list it because the ordering claim is
about the WHOLE menu, and the acts it can complete are that ticket's own file.

THE PREFACTOR. The working-set-before-catalogue ordering was inline in the composer's `@` menu. It
is `SW.util.workingSetFirst` now, and both menus read it, because the header's picker was written
from the menu's shape and two copies is how a person's one learned model comes apart.

Nothing is mounted — see `js/build_header_harness.mjs` for why.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from sage.assets.provider import FakeAssetProvider
from sage.orchestrator.service import Orchestrator
from sage.resources.bindings import KIND_DATASET, KIND_LLM_ALIAS, parse_bindings
from sage.router.models import ModelCatalog

_HARNESS = Path(__file__).resolve().parent / "js" / "build_header_harness.mjs"
_JS = Path(__file__).resolve().parents[1] / "sage" / "workbench" / "js"

needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not on PATH (it is in the Sage image)"
)


def _run(steps: list[dict]) -> list[dict]:
    out = subprocess.run(
        ["node", str(_HARNESS)],
        input=json.dumps(steps),
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def _labels(items: list[dict]) -> list[str]:
    return [i["label"] for i in items]


# ---- the door itself -------------------------------------------------------


@needs_node
def test_the_header_offers_the_act_that_adds_to_the_selected_app():
    """#92 left this row a read-only glance, on the argument that a summary should point rather than
    act. That argument held while it was a summary and stops holding the moment it is the door.

    The words are the panel's own, deliberately: two doors onto one act, and a second label for it
    would be a second thing to learn (ADR-0011). The app is named because a Project holds many Built
    Apps (ADR-0008), so `Use in this app` would answer nothing."""
    step = _run([{"addIn": True, "thread": "thr_many", "select": "app_c"}])[-1]
    assert step["label"] == "Use in Rate curve viewer"
    # A control that adds has to say what the addition costs, and the cost is that publish reads
    # it. It names the destination list by the label that list carries, not by the record (ADR-0025).
    assert "needs to run" in step["tooltip"]


@needs_node
def test_the_picker_lists_the_working_set_before_the_wider_catalogue():
    """The ordering criterion, read as one list. The catalogue goes last because it is the only
    group whose rows are not here already — picking one joins the Project on the way in (ADR-0018) —
    and its heading says so rather than leaving that to be discovered after the click.

    Sorted by name, or concatenated the other way, `Nova micro` would come out above `Tick
    archive`. It does not."""
    step = _run([{"addIn": True, "thread": "thr_many", "select": "app_c"}])[-1]
    assert _labels(step["items"]) == [
        "In this project",
        "🧠 Claude Sonnet 4",
        "🤖 Churn risk",
        "📦 Tick archive",
        "🔌 Market data EOD",
        "🔌 Risk warehouse",
        "🔌 Ledger export",
        "Elsewhere in Domino — joins this project",
        "🧠 Nova micro",
        "📦 Cold storage",
    ]
    # The two headings are headings and not rows: a click on either would bind nothing.
    assert [i["group"] for i in step["items"]] == [
        True, False, False, False, False, False, False, True, False, False]


@needs_node
def test_a_project_that_has_joined_everything_draws_one_group_and_no_second_heading():
    """A heading over nothing names a list the person does not have. The header's own rule since #92
    — a kind with nothing in it is not the same state, so it is not named."""
    step = _run([
        {"addIn": True, "thread": "thr_many", "select": "app_c", "nocatalogue": True},
    ])[-1]
    assert _labels(step["items"]) == [
        "In this project", "🧠 Claude Sonnet 4", "🤖 Churn risk", "📦 Tick archive",
        "🔌 Market data EOD", "🔌 Risk warehouse", "🔌 Ledger export",
    ]


@needs_node
def test_the_picker_leaves_out_what_the_app_already_holds():
    """`app_a` binds the Alias and the Model API in the fixture; `app_c` binds neither. Re-binding
    rewrites the same record with the same values, so the row would be an act with no effect — and
    what the app already holds is named two inches to the left, by this very row."""
    a, c = _run([
        {"addIn": True, "thread": "thr_many", "select": "app_a"},
        {"addIn": True, "thread": "thr_many", "select": "app_c"},
    ])
    assert "🧠 Claude Sonnet 4" not in _labels(a["items"])
    assert "🤖 Churn risk" not in _labels(a["items"])
    assert "🧠 Claude Sonnet 4" in _labels(c["items"])
    assert "🤖 Churn risk" in _labels(c["items"])
    # The Dataset is bound by nobody, so it survives both.
    assert "📦 Tick archive" in _labels(a["items"])
    assert "📦 Tick archive" in _labels(c["items"])


@needs_node
def test_an_empty_picker_says_why_and_the_two_empties_are_two_sentences():
    """A disabled item never fires, so the reason has to be the label itself — the rule the panel's
    `No writable Dataset is mounted here` already follows.

    Two empties, because they are two states with two different ways out. An app that holds
    everything on offer is finished; a Project with nothing in it is one nobody has picked into yet,
    and Browse Domino is where that is fixed. One sentence for both would send half the people who
    read it to the wrong screen."""
    held, empty = _run([
        {"addIn": True, "thread": "thr_many", "select": "app_a", "noresources": True},
        {"addIn": True, "thread": "thr_many", "select": "app_b", "noresources": True},
    ])
    assert _labels(held["items"]) == [
        "Desk dashboard already uses everything you can add here"]
    assert _labels(empty["items"]) == ["Nothing to add yet — pick one in Browse Domino"]
    assert [i["disabled"] for i in held["items"] + empty["items"]] == [True, True]


@needs_node
def test_a_long_catalogue_is_truncated_and_says_so_and_never_squeezes_out_the_working_set():
    """The cap comes off the catalogue, never off the end of the whole list. A cap applied after the
    ordering would take its rows from the group the ordering deliberately puts LAST, so a Project
    holding a dozen bindable Resources would lose the entire second group — and the ordering
    criterion would be satisfied by a menu that never drew the half it orders last.

    The remainder is counted rather than dropped, and it is named as Browse Domino's, because
    walking the whole catalogue is that surface's job and not a menu's."""
    step = _run([{
        "addIn": True, "thread": "thr_many", "select": "app_c", "bigcatalogue": True,
    }])[-1]
    labels = _labels(step["items"])
    # The working set is whole and still first — the group a global cap would have protected while
    # eating the other one.
    assert labels[:7] == [
        "In this project", "🧠 Claude Sonnet 4", "🤖 Churn risk", "📦 Tick archive",
        "🔌 Market data EOD", "🔌 Risk warehouse", "🔌 Ledger export"]
    assert labels[7] == "Elsewhere in Domino — joins this project"
    # Eight shown out of eleven, and the three held back are counted rather than silently gone.
    assert len(labels[8:]) == 9
    assert labels[-1] == "3 more in Browse Domino"
    # A count is not a control: it says how many, and it cannot be clicked into binding anything.
    assert step["items"][-1]["disabled"] is True


# ---- what a click writes ---------------------------------------------------


@needs_node
def test_binding_an_llm_alias_posts_the_bare_id_not_the_prefixed_one():
    """The same trap `test_use_in_app_binds_an_llm_alias.py` was written around, asked of the new
    door. A Project Resource id is prefixed (`llm_alias:al_1`); a Binding carries the bare id beside
    its kind. `POST /bindings` resolves against the live listing, so the prefixed id matches nothing,
    answers 404, and leaves the header redrawing exactly what it drew — a failure shaped like
    success."""
    step = _run([{
        "addIn": True, "thread": "thr_many", "select": "app_c", "pick": "llm_alias:al_1",
    }])[-1]
    assert step["posted"] == [{"kind": "llm_alias", "id": "al_1"}]
    assert "POST /bindings" in step["calls"]
    assert "llm_alias:al_1" in step["bindings"]


@needs_node
def test_binding_a_dataset_from_the_header_records_the_binding():
    """A Dataset is bindable from this door. It is not an Attachment and does not become one: an
    Attachment is a FILE copied into the app's tree and rebuilt at deploy time, and the glossary's
    rule that a file never becomes a Binding is untouched, because a Dataset is not a file."""
    step = _run([{
        "addIn": True, "thread": "thr_many", "select": "app_c", "pick": "dataset:as_ticks",
    }])[-1]
    assert step["posted"] == [{"kind": "dataset", "id": "as_ticks"}]
    assert "dataset:as_ticks" in step["bindings"]


@needs_node
def test_binding_a_model_api_reaches_the_same_route_and_reports_its_refusal():
    """The Model API behaves from this door as it does from any other, which is the criterion —
    including the refusal, which is the whole of what a Model API Binding means. `bind_model_api`
    turns down a model Sage holds no access token for, because a Binding recorded without one pins a
    model the app cannot call and reports it as a dependency that works.

    The header reports the server's own sentence rather than redrawing as if the record had landed:
    a refusal that leaves the screen looking like success is the failure `Use in {app}` was written
    around in the first place (#127)."""
    ok, refused = _run([
        {"addIn": True, "thread": "thr_many", "select": "app_c", "pick": "model_api:ma_1"},
        {"addIn": True, "thread": "thr_many", "select": "app_c", "pick": "model_api:ma_1",
         "refuse": "Sage needs this Model API's access token before an app can call it."},
    ])
    assert ok["posted"] == [{"kind": "model_api", "id": "ma_1"}]
    assert "model_api:ma_1" in ok["bindings"]

    # The right Resource was named either way — a door posting the prefixed id would be refused too,
    # for a different reason, and the sentence alone cannot tell the two apart.
    assert refused["posted"] == [{"kind": "model_api", "id": "ma_1"}]
    assert "model_api:ma_1" not in refused["bindings"]
    said = " ".join(refused["said"])
    assert "access token" in said
    # And no receipt: nothing was reversed, so there is nothing to say how to reverse.
    assert "now uses" not in said


@needs_node
def test_binding_a_row_the_project_has_not_joined_posts_it_the_same_way():
    """The catalogue half of the picker reaches the same route with the same body. Membership is a
    record of use and follows the act rather than standing in front of it (ADR-0018), so there is no
    join-then-bind pair here — there is one click."""
    step = _run([{
        "addIn": True, "thread": "thr_many", "select": "app_c", "pick": "dataset:as_cold",
    }])[-1]
    assert step["posted"] == [{"kind": "dataset", "id": "as_cold"}]


@needs_node
def test_the_act_says_what_it_did_and_how_to_reverse_it():
    """The receipt ADR-0021 asks for in place of the confirm it refused: separation carries the
    weight, so the sentence comes AFTER and names the scope and the way back. Naming the app twice
    is the point — the scope is what tells this act from `Use in this chat`, and the way out is
    named in the words the reader will see on the way to it (ADR-0011).

    The way out and the way in are now the same surface. That was the change #151 made and it is
    not a retreat from ADR-0011: the rule is that an object is removed from the list that owns its
    scope, and the app's own list left the Project's panel for the app's own modal, taking its
    removal with it."""
    step = _run([{
        "addIn": True, "thread": "thr_many", "select": "app_c", "pick": "llm_alias:al_1",
    }])[-1]
    said = " ".join(step["said"])
    assert "Rate curve viewer now uses Claude Sonnet 4" in said
    assert "Remove it under App dependencies" in said


# ---- the ordering is shared, not copied ------------------------------------


def test_the_ordering_lives_in_one_place():
    """The prefactor, asked of the source. Both menus offer the same choice and one of them had the
    order inline; a second copy is how the picker and the `@` menu come to disagree about where a
    Resource is, which is the one thing a person carries between the two surfaces.

    Read as source rather than behaviour because the claim IS about there being one copy — two
    implementations that happen to agree today pass every behavioural test there is."""
    composer = (_JS / "components" / "composer.js").read_text(encoding="utf-8")
    builder = (_JS / "modes" / "builder.js").read_text(encoding="utf-8")
    util = (_JS / "util.js").read_text(encoding="utf-8")
    assert "workingSetFirst" in util
    assert "SW.util.workingSetFirst" in composer
    assert "SW.util.workingSetFirst" in builder
    # The inline copy is gone rather than left beside the shared one, which would be two orderings
    # with one of them unused and the next reader unable to tell which is live.
    assert "return context.concat(" not in composer


# ---- the record the server writes ------------------------------------------


def _template(tmp: Path) -> Path:
    t = tmp / "template"
    (t / "src").mkdir(parents=True, exist_ok=True)
    (t / "src" / "App.tsx").write_text("placeholder")
    (t / "package.json").write_text("{}")
    return t


def _orch(tmp: Path) -> Orchestrator:
    return Orchestrator(
        workspace_dir=tmp / "mnt" / "code",
        template=_template(tmp),
        gateway=object(),
        catalog=ModelCatalog(sovereign_plan="s", sovereign_implement="s", sovereign_ask="s",
                             plan="p", implement="i", ask="a"),
        project_id="Sage",
        assets=FakeAssetProvider())


def test_binding_a_dataset_records_it_and_joins_the_working_set(tmp_path: Path):
    """The two halves the criterion names. The join is `_record`'s, which every door that binds
    already passes through — so the Dataset door gets membership for free and cannot drift out of
    step with the three that had it (ADR-0018)."""
    orch = _orch(tmp_path)
    proj = orch.project(start_preview=False)
    asset = next(a for a in orch.list_assets() if a["name"] == "sales_2026")

    rows = orch.bind_dataset(asset["id"])
    assert [(b["kind"], b["id"]) for b in rows] == [(KIND_DATASET, asset["id"])]

    recorded = parse_bindings(proj.workspace.read_bindings())
    assert [(b.kind, b.id, b.display_name) for b in recorded] == [
        (KIND_DATASET, asset["id"], "sales_2026")]
    # No Scope: those three fields are a Data Source's, and a Dataset is reached whole.
    assert recorded[0].scope == ""

    held = {r["id"] for r in orch.list_project_resources()}
    assert f"{KIND_DATASET}:{asset['id']}" in held


def test_binding_a_dataset_twice_replaces_in_place(tmp_path: Path):
    """`Binding.key` is kind and id, so the second click is a no-op rather than a second dependency
    — the rule `_record` already holds for the other three kinds."""
    orch = _orch(tmp_path)
    orch.project(start_preview=False)
    asset = next(a for a in orch.list_assets() if a["name"] == "app_logs")
    orch.bind_dataset(asset["id"])
    rows = orch.bind_dataset(asset["id"])
    assert len(rows) == 1


def test_a_dataset_this_project_does_not_mount_is_a_lookup_error(tmp_path: Path):
    """Resolved against the listing for the reason `bind_llm_alias` resolves an Alias against its
    own: the listing is Domino's answer about what this caller can reach, and recording a dependency
    on something outside it would be recording a build that cannot run. The route turns this into a
    404 with a sentence of its own, because "not mounted into this project" is not the advice an
    ungranted Data Source or an undeployed Model API needs."""
    orch = _orch(tmp_path)
    orch.project(start_preview=False)
    with pytest.raises(LookupError):
        orch.bind_dataset("ds_not_here")


def test_a_dataset_binding_is_inert_in_everything_that_reads_bindings_by_kind(tmp_path: Path):
    """The new kind adds a record and changes no behaviour. Nothing is pinned into the app's source,
    no schema is written for it, and the staleness check is handed no listing for `dataset` — so it
    can never be called gone by a listing that was never fetched."""
    orch = _orch(tmp_path)
    proj = orch.project(start_preview=False)
    asset = next(a for a in orch.list_assets() if a["name"] == "customer_pii")
    orch.bind_dataset(asset["id"])

    from sage.resources.pinned_model import pinned_alias
    from sage.resources.preflight import stale_bindings

    recorded = parse_bindings(proj.workspace.read_bindings())
    assert pinned_alias(recorded) is None
    assert stale_bindings(recorded, {KIND_LLM_ALIAS: []}) == []
    assert not (proj.workspace.path / ".sage" / "schema.json").exists()
