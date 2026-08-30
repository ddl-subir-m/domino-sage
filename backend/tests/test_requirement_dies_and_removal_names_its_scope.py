"""Requirement dies, and `detach` stops naming two scopes (#99, ADR-0011).

WHAT WENT. **Requirement** was a third name for what the glossary calls a **Binding**, and its
plumbing never worked: `api.js`'s `appRequires`/`addRequirement`/`removeRequirement` were stubs that
fetched nothing and returned nothing, so `state.requires` was `[]` on every path through the store.
ADR-0011 refuses to wire the pair to the real routes — two words on one concept in one panel — so
the stubs, the state key, the refresh, the promote/demote actions and the menu items they backed are
all deleted.

WHAT MOVED. Two consumers of `requires` were good ideas with no data behind them, and they are
re-pointed at `bindings` rather than deleted with the stub. Neither has EVER rendered, so these are
tests for new behaviour, not regression guards:

  1. Dropping a chip for a Resource the selected app is bound to says the app still needs it. It is
     the mirror of ADR-0011's removal case: the two lists move on their own, and this is the one
     sentence that says so from the Conversation's side.
  2. A Project row for a Resource the selected app is bound to reads "Required by {app}".

THE ID SPACES, WHICH ARE THE WHOLE JOB. A Binding carries a BARE Domino id (`ds_1`) beside its kind;
a Project Resource id and an attachment's `resourceId` are both PREFIXED (`data_source:ds_1`).
Joining on `b.id` matches nothing, silently, and the screen looks exactly like the empty state that
was there before. So the join key is `${b.kind}:${b.id}`, and the fixtures below carry the prefixed
ids the server really answers with — a fixture that shortened them would prove the wrong thing.

WHAT DID NOT CHANGE HERE. The app-scoped removal control is #96, which this blocked and which has
since landed — its own claims live in `test_removal_lands_in_the_in_this_app_section.py`, and nothing
below asserts anything about it. The In-this-app rows keep their `required: true`, which is those
rows describing themselves, but
lose the fake `app: {name: 'this app'}` that would have had them read "Required by this app" under a
head that already names the app.

Nothing is mounted — see `js/build_header_harness.mjs` for why.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "js" / "build_header_harness.mjs"
_JS = Path(__file__).resolve().parents[1] / "sage" / "workbench" / "js"

needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is not on PATH (it is in the Sage image)"
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


def _sources() -> dict[str, str]:
    """Every Workbench source file, so "the word is gone" is asked of all of them at once."""
    return {
        str(p.relative_to(_JS)): p.read_text()
        for p in sorted(_JS.rglob("*.js"))
    }


def _row(rows: list[dict], name: str, section: str) -> dict:
    """The one row under `section` whose first word is `name`."""
    found = [r for r in rows if r["section"] == section and name in r["texts"]]
    assert len(found) == 1, f"{name} appears {len(found)} times under {section}: {rows}"
    return found[0]


# ---- the chip that leaves a Conversation the app still depends on -------------------


@needs_node
def test_dropping_a_chip_the_app_is_bound_to_says_the_app_still_needs_it():
    """The branch that has never executed. `app_a` is bound to `data_source:ds_1`, and the chip
    named `ctx_source` is the same Resource in the Conversation — so the two lists disagree on
    purpose, and the sentence is what keeps that from reading as a leak."""
    step = _run([{"dropChip": "ctx_source", "thread": "thr_many", "select": "app_a"}])[-1]
    said = " ".join(step["said"])
    assert "Market data EOD is out of this conversation." in said
    assert "Desk dashboard still needs it." in said
    # It left the Conversation all the same. The message reports the split; it does not refuse.
    assert step["left"] == ["ctx_dataset"]


@needs_node
def test_dropping_a_chip_no_app_is_bound_to_still_names_the_project():
    """The other half of the same branch, which is what proves the join is a join rather than a
    constant. `dataset:desks` is in nobody's Bindings, so the app is not mentioned."""
    step = _run([{"dropChip": "ctx_dataset", "thread": "thr_many", "select": "app_a"}])[-1]
    said = " ".join(step["said"])
    assert "Desk margins is out of context" in said
    assert "still needs" not in said


@needs_node
def test_the_sentence_follows_the_selected_app_not_the_project():
    """Removal is scoped to the SELECTED app and nothing in the routes says so (ADR-0011), so the
    same chip under a different app has to draw the other sentence. `app_c` is bound to an LLM
    Alias only, so it does not need the Data Source `app_a` does."""
    step = _run([{"dropChip": "ctx_source", "thread": "thr_many", "select": "app_c"}])[-1]
    said = " ".join(step["said"])
    assert "Market data EOD is out of context" in said
    assert "still needs" not in said


# ---- "Required by {app}" on a Project row --------------------------------------------


@needs_node
def test_a_project_row_the_app_is_bound_to_reads_required_by_that_app():
    """The subtitle, off Bindings. Joined on `${kind}:${id}` — the Binding's own `id` is `ds_1` and
    the row's is `data_source:ds_1`, so a join on the bare id renders nothing at all."""
    step = _run([{"panel": "thr_many", "select": "app_a"}])[-1]
    bound = _row(step["rows"], "Market data EOD", "Project resources")
    assert "Required by Desk dashboard" in bound["texts"]
    assert "is-required" in bound["className"]


@needs_node
def test_a_project_row_no_app_is_bound_to_says_nothing_of_the_kind():
    """`data_source:ds_9` is in the Project and bound by nobody. Without this the subtitle could be
    a constant and every test above would still pass."""
    step = _run([{"panel": "thr_many", "select": "app_a"}])[-1]
    loose = _row(step["rows"], "Risk warehouse", "Project resources")
    assert not any("Required by" in t for t in loose["texts"])
    assert "is-required" not in loose["className"]


@needs_node
def test_the_subtitle_follows_the_selected_app():
    """`app_c` is bound to `al_2`, which is in nobody's Project rows here — so under `app_c` the
    same three rows carry no subtitle. The list is the app's, not the Project's."""
    step = _run([{"panel": "thr_many", "select": "app_c"}])[-1]
    assert step["app"] == "Rate curve viewer"
    assert not any("Required by" in t for r in step["rows"] for t in r["texts"])


@needs_node
def test_nothing_reads_as_required_twice():
    """The In-this-app rows pass `required: true` as a literal — they ARE the app's list. Once the
    Project rows' subtitle is real, that literal must not also draw "Required by this app" under a
    head that already names the app (#96 gave the section that head)."""
    step = _run([{"panel": "thr_many", "select": "app_a"}])[-1]
    # `app_a` is bound to two Resources, so both sections draw both — the subtitle belongs to
    # exactly one of the two copies, and it is the one whose head does not already say it.
    for name in ("Market data EOD", "Claude Sonnet 4"):
        in_app = _row(step["rows"], name, "In Desk dashboard")
        assert not any("Required by" in t for t in in_app["texts"])
        # The In-this-app row still carries the marker; it just stops repeating its own section.
        assert "is-required" in in_app["className"]
        assert "Required by Desk dashboard" in _row(step["rows"], name, "Project resources")["texts"]
    said = [t for r in step["rows"] for t in r["texts"] if "Required by" in t]
    assert said == ["Required by Desk dashboard", "Required by Desk dashboard"]


# ---- the words that are gone ----------------------------------------------------------


def test_no_requirement_plumbing_survives_anywhere_in_js():
    """Deleted, not left dangling behind a live consumer. `requires` is checked as a whole word so
    that `requiresAuth`-shaped names elsewhere are not read as this one."""
    for rel, src in _sources().items():
        for word in ("appRequires", "addRequirement", "removeRequirement", "reloadRequires",
                     "refreshRequires", "promoteResource", "demoteResource",
                     "state.requires", "requiredIds = new Set(requires"):
            assert word not in src, f"{rel} still carries {word}"


def test_nothing_in_js_calls_a_conversation_scoped_act_detach():
    """The one place the word spanned two scopes. `detach` stays the app-scoped pair's word in the
    backend (`attach_file`/`detach_file`); in `js/` it must name nothing Conversation-scoped."""
    for rel, src in _sources().items():
        for word in ("store.detach", "detachResource", "'detach'", "'detach-resource'"):
            assert word not in src, f"{rel} still carries {word}"


def test_the_conversation_scoped_remover_is_named_for_the_call_it_makes():
    """`SW.api.removeFromConversation` was already right; the store's action now matches it, and
    the label on the menu — now "Stop using here" — is reached by the same action."""
    store = (_JS / "store.js").read_text()
    assert "async removeFromConversation(attachment)" in store
    assert "removeResourceFromConversation(resourceId)" in store
