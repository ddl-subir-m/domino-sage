"""Removal lands in the "In this app" section, labelled by scope (#96, ADR-0011).

WHAT WAS MISSING. `DELETE /api/bindings/{kind}/{resource_id}` and `POST /api/project/files/detach`
have been live and uncalled since they were written: nothing in `js/` reached either, so a Binding or
an Attachment, once made, could only be removed through the API. ADR-0010 promised the way out of a
Binding you do not want; there was no door.

WHERE IT LANDS. The panel's app section, which is where #92's header pointer already sends people —
a pointer is a promise that the destination can act. The header keeps its pointers and grows no
controls: one act, one guard, and `unbind`/`detach_file` each report the app source that still uses
what just went, which a second copy in a one-line summary would have to keep in step with.

WHAT THE LABELS SAY. Three lists can hold a Resource, so every removal names which one it acts on.
The section already carried two of the three scopes; this adds the third, and the harness finds it by
that rule rather than by a menu key — an item that stopped naming its scope would not be found.

THE REPORT COMES AFTER THE ACT. Both routes read usage BEFORE the record goes and hand it back, so
the notice is post-hoc by construction. A pre-warning would have to run `_scan_app_sources` per row
on menu open, and ADR-0010 reserves the live scan for the one-off deliberate act — a warning shown
before the act also reads as a gate however it is worded.

THE CONFIRM IS ASYMMETRIC, and that tracks real cost. Re-attaching a file is one click on the same
Dataset file. Re-binding a Data Source means choosing its Scope again, because the Scope goes with
the Binding record and nothing else holds it. The Model API's access token does NOT go — it lives in
`CredentialStore`, keyed by model id, which `unbind` never touches — so the confirm says so rather
than letting someone expect the worse outcome.

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

# The sentence both surfaces say when the selected app records neither kind. Written once in the
# source too — see the test at the bottom, which is what keeps the two from drifting again.
TAIL = "Chat's resources and files land here after Open Builder."


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


def _panel(select: str = "app_a", thread: str = "thr_many") -> dict:
    return _run([{"panel": thread, "select": select}])[-1]


def _remove(name: str, select: str = "app_a", **extra) -> dict:
    return _run([{"removeFrom": name, "thread": "thr_many", "select": select, **extra}])[-1]


def _texts(step: dict, marker: str) -> list[str]:
    """What the elements whose class carries `marker` said, and nothing else on screen."""
    return [t for p in step["parts"] if marker in p["className"] for t in p["texts"]]


def _app_rows(step: dict) -> list[dict]:
    """The app's own list, which is the App dependencies modal now (#151). It used to be a section
    inside the panel; the panel is the Project's list and holds no app's records."""
    return step["appRows"]


def _row(step: dict, name: str) -> dict:
    found = [r for r in _app_rows(step) if name in r["texts"]]
    assert len(found) == 1, f"{name} appears {len(found)} times in the app's section"
    return found[0]


def _removal_item(row: dict) -> dict:
    """The one menu item on `row` that names a scope other than the Conversation."""
    found = [
        i
        for i in row.get("items", [])
        if i["label"].startswith("Remove from ")
    ]
    assert len(found) == 1, f"{row['texts']} carries {[i['label'] for i in row.get('items', [])]}"
    return found[0]


# ---- the section: a head, two groups, and both kinds in it ------------------------------------


@needs_node
def test_the_list_is_headed_by_the_app_it_is_about():
    """ADR-0008 makes "this app" a question every surface has to answer, and a Project holds many.
    A head reading "In this app" over a list that follows the app control answers it nowhere.

    The head is the modal's title now (#151). The list moved onto the app's own surface, and a
    modal opened from that app's menu still has to say which app, for the same reason: the
    selection can move while it sits open, and the routes behind these removals carry no app id."""
    assert "App dependencies · Desk dashboard" in _panel("app_a")["appWords"]
    assert "App dependencies · Rate curve viewer" in _panel("app_c")["appWords"]
    # An app that records nothing yet is the one that most needs telling whose list this is.
    assert "App dependencies · P&L report" in _panel("app_b")["appWords"]


@needs_node
def test_the_panel_is_headed_by_the_project_and_names_no_app():
    """The other half of the move, and the reason for it. The panel held two lists at two scopes
    under two heads; a reader had to work out which of them a row belonged to before its menu made
    sense. It has one heading, it names no app, and the app's own records are not in it."""
    step = _panel("app_a")
    assert step["sections"] == ["In this project"]
    assert not [r for r in step["rows"] if r["section"] and "Desk dashboard" in r["section"]]
    # And the file the app carries is not among the Project's rows — it is one row, in one scope.
    assert not [r for r in step["rows"] if "margins.csv" in r["texts"]]


@needs_node
def test_both_kinds_are_listed_under_their_own_name():
    """Two names rather than one umbrella (#92): the two records have two consumers and two costs
    to undo. Until this, the section held Bindings only — which is why #92's Attachments pointer
    named a group that was not there. Both are named on screen by the app's relationship to them,
    never by the record or by the kind the rail below already divides on (ADR-0025)."""
    step = _panel("app_a")
    groups = _texts(step, "sw-app-group")
    assert groups == ["Needs to run", "Files it carries"]
    names = [t for r in _app_rows(step) for t in r["texts"]]
    assert "Market data EOD" in names and "Churn risk" in names
    assert "margins.csv" in names and "legacy.csv" in names


@needs_node
def test_the_list_names_an_empty_kind_where_the_header_omits_it():
    """#92's rule — a kind with nothing in it is not named — is right for a glance and wrong for a
    destination. This is where someone arrived intending to act, so a group reading "Files it
    carries — none" answers the question they came with. The label keeps the word *file* for
    exactly that reason, where the Binding's drops the word *binding* (ADR-0025).

    The rule came across with the list (#151): the modal IS the destination now, so it is the
    surface that owes the answer."""
    c = _panel("app_c")  # Bindings, no files
    assert _texts(c, "sw-app-group") == ["Needs to run", "Files it carries — none"]
    d = _panel("app_d")  # files, no Bindings
    assert _texts(d, "sw-app-group") == ["Needs to run — none", "Files it carries"]


@needs_node
def test_an_app_with_neither_takes_the_headers_own_sentence():
    """Both empty is not two empty groups — it is the state the header already has a sentence for,
    and the panel says the same thing so the two surfaces cannot drift apart again. It names the
    handoff rather than an upload: the composer's upload writes a scratch file and a Conversation
    chip, so a first-timer doing as they were told would see this section unchanged."""
    step = _panel("app_b")
    caption = " ".join(_texts(step, "sw-appdeps-intro"))
    assert caption.endswith(TAIL)
    assert "sw-app-group" not in " ".join(p["className"] for p in step["parts"])
    # It used to be said twice — once by the header's strip and once by the panel's section — and
    # the pair was the reason for writing it in `SW.util`. The two surfaces are one since
    # `624ff9b` and ADR-0035, so this asserts what that leaves: the sentence is still said, on the
    # one surface that describes an empty app, and the helper is still where it comes from.
    header = _run([{"build": "thr_many", "select": "app_b"}])[-1]
    assert " ".join(header["appDeps"]["said"]).endswith(TAIL)


@needs_node
def test_the_apps_rows_carry_ids_the_project_answers_in():
    """The check #96 asked for, now load-bearing for a different thing. With the app's list off the
    panel, the ONLY sign in the Project's list that this app needs a row is the mark on the row
    itself — and the mark is drawn by matching `SW.util.bindingId(b)` against the Project row's own
    id. A Binding holds a bare Domino id beside its kind, and `${kind}:${id}` is the prefixed id a
    Project Resource carries, so the two ARE the same string. Drift there and the panel silently
    stops saying what the app uses, which is the whole of what it says about the app now."""
    step = _panel("app_a")
    ids = {r["id"] for r in _app_rows(step)}
    project = {r["id"]: r for r in step["rows"] if r["id"]}
    assert {"data_source:ds_1", "llm_alias:al_1"} <= ids
    assert {"data_source:ds_1", "llm_alias:al_1"} <= set(project)
    # And the mark actually lands: matched, not merely matchable.
    for rid in ("data_source:ds_1", "llm_alias:al_1"):
        assert "is-required" in project[rid]["className"], rid
    # A Resource this app does not bind carries no mark, or the mark says nothing.
    unbound = [r for r in step["rows"] if r["id"] and r["id"] not in ids]
    assert unbound, "the fixture has no unbound Project row to tell the mark apart with"
    assert all("is-required" not in r["className"] for r in unbound)


@needs_node
def test_the_list_costs_no_network_call_on_render():
    """It draws two written records the store already holds, both assigned together by
    `refreshAppScope`. ADR-0010 keeps anything that renders per app switch off the live scan."""
    for select in ("app_a", "app_b", "app_c", "app_d"):
        assert _panel(select)["renderCalls"] == []


# ---- both removals, each labelled with its scope -----------------------------------------------


@needs_node
def test_both_kinds_offer_a_removal_that_names_the_app():
    """The third of the three scopes. The section already offered "Stop using here"
    and Project rows offer "Remove from {project}"; a bare "Remove" here would be the one gesture
    three lists could claim."""
    step = _panel("app_a")
    for name in ("Market data EOD", "margins.csv"):
        item = _removal_item(_row(step, name))
        assert item["label"] == "Remove from Desk dashboard"
        assert item["danger"] is True


@needs_node
def test_the_removal_label_follows_the_selected_app():
    """Without this the label could be a constant and every assertion above would still pass."""
    item = _removal_item(_row(_panel("app_c"), "Qwen 2.5"))
    assert item["label"] == "Remove from Rate curve viewer"


@needs_node
def test_removing_a_binding_calls_the_route_and_takes_the_row_away():
    step = _remove("Market data EOD", confirm=True)
    # The membership re-read behind it is #161's: `usedBy` is computed per read off the apps' own
    # manifests, so an unbind changes what the Project rail says about this Resource and only a
    # fresh read can tell it. Membership and the app's manifest, and NOT the platform listing —
    # giving a Binding back cannot change what Domino holds (#162).
    assert step["calls"] == [
        "DELETE /bindings/data_source/ds_1",
        "GET /project/resources",
        "GET /project",
    ]
    assert "Market data EOD" not in step["bindings"]
    assert not [r for r in _app_rows(step) if "Market data EOD" in r["texts"]]


@needs_node
def test_removing_an_attachment_calls_the_route_and_takes_the_row_away():
    step = _remove("margins.csv")
    assert step["calls"] == ["POST /project/files/detach"]
    assert "margins.csv" not in step["attachments"]
    assert not [r for r in _app_rows(step) if "margins.csv" in r["texts"]]


# ---- the confirm, which is asymmetric ----------------------------------------------------------


@needs_node
def test_removing_a_binding_confirms_and_names_what_re_picking_costs():
    """The Scope goes with the Binding record and nothing else holds it, so re-binding is not the
    one click re-attaching is. There is no undo, and the confirm is where that is said."""
    step = _remove("Market data EOD", confirm=True)
    assert step["confirm"] is not None
    assert step["confirm"]["title"] == "Remove Market data EOD from Desk dashboard?"
    assert "Scope" in step["confirm"]["content"]
    assert "no undo" in step["confirm"]["content"]
    assert step["confirm"]["danger"] is True


@needs_node
def test_the_binding_confirm_says_the_model_api_token_does_not_go():
    """Verified against `unbind`, which touches the bindings manifest and `_write_app_resources`
    and nothing else — the credential lives in `CredentialStore`, keyed by model id. Saying so
    stops someone expecting the worse outcome and keeping a Binding they do not want."""
    step = _remove("Churn risk", confirm=True)
    content = step["confirm"]["content"]
    assert "token" in content
    assert "sample request" in content
    # The Scope belongs to a Data Source Binding. Saying it here would be a cost that is not real.
    assert "Scope" not in content


@needs_node
def test_a_binding_confirm_over_a_kind_with_neither_cost_claims_neither():
    """An LLM Alias carries no Scope and no credential, so re-picking it costs the pick. Without
    this the two sentences above could be the only two written and every Alias would be told it was
    losing something it never had."""
    content = _remove("Claude Sonnet 4", confirm=True)["confirm"]["content"]
    assert "Pick it again from Project resources." in content
    assert "Scope" not in content and "token" not in content


@needs_node
def test_the_confirms_own_button_names_the_scope_too():
    """The glossary bans a bare "Remove" because it does not say which of the three lists it acts
    on, and the button is the last thing read before the act."""
    assert _remove("Market data EOD", confirm=True)["confirm"]["okText"] == "Remove from Desk dashboard"


@needs_node
def test_a_binding_confirm_that_is_cancelled_calls_nothing():
    """The guard is a guard. Without this the confirm could be decoration over a removal that had
    already happened."""
    step = _remove("Market data EOD", confirm=False)
    assert step["confirm"] is not None  # it was asked, and answered no
    assert step["calls"] == []
    assert "Market data EOD" in step["bindings"]


@needs_node
def test_removing_an_attachment_does_not_confirm():
    """Re-attaching is one click on the same Dataset file. A confirm over that is a gate charged
    for nothing, and it would teach that the two removals cost the same."""
    step = _remove("margins.csv")
    assert step["confirm"] is None


# ---- the report, after the act, inside the section ---------------------------------------------


@needs_node
def test_the_binding_removal_reports_the_app_source_that_still_uses_it():
    """`refs` is read BEFORE the record goes — a Data Source's queries are found THROUGH it — and
    reported after. The notice is in the section, not a toast: five seconds is not long enough to
    read a file list and decide."""
    step = _remove("Market data EOD", confirm=True)
    notice = " ".join(_texts(step, "sw-appdeps-notice-text"))
    assert "Market data EOD is out of Desk dashboard." in notice
    assert "src/queries.py" in notice and "public/panel.js" in notice
    assert step["said"] == []


@needs_node
def test_a_removal_nothing_refers_to_says_so_rather_than_nothing():
    """`al_1` is bound and used by no file. Without this branch the notice could be the file list
    and a creator who removed something clean would get no acknowledgement at all."""
    step = _remove("Claude Sonnet 4", confirm=True)
    notice = " ".join(_texts(step, "sw-appdeps-notice-text"))
    assert "Claude Sonnet 4 is out of Desk dashboard." in notice
    assert "Nothing in the app's code refers to it." in notice


@needs_node
def test_the_attachment_report_names_the_dataset_the_file_stays_in():
    """`detach_file` takes the symlink, the manifest entry, the AGENTS.md block and any raw copy the
    agent leaked into the app tree. It keeps the Dataset bytes — which is the half worth saying."""
    step = _remove("margins.csv")
    notice = " ".join(_texts(step, "sw-appdeps-notice-text"))
    assert "margins.csv is out of Desk dashboard." in notice
    assert "desks" in notice
    assert "src/data/margins.csv" in notice  # the leaked copy that went with it
    assert "src/load.py" in notice  # the inlined use that did not


@needs_node
def test_an_attachment_with_no_dataset_does_not_invent_a_source():
    """`detach_file`'s docstring records rehydrated entries with no dataset_id. For those there is
    no source to name, so the sentence says that instead of promising a file stays somewhere.

    The question is only real because the fixture carries a `dataset` and no `dataset_id`, which is
    what `_rehydrate_attached` writes: it fills `dataset` from the symlink's parent directory
    (`service.py:2769`), so the field is always there and for these entries means nothing. A
    sentence keyed on it would print `rehydrated` — a directory — as the Dataset the bytes are safe
    in, which is the invention the ADR forbids."""
    step = _remove("legacy.csv")
    notice = " ".join(_texts(step, "sw-appdeps-notice-text"))
    assert "legacy.csv is out of Desk dashboard." in notice
    assert "no Dataset" in notice
    assert "rehydrated" not in notice
    # The same field decides the row's subtitle, so it can lie in the same way.
    row = _row(_panel("app_a"), "legacy.csv")
    assert "rehydrated" not in row["texts"]


@needs_node
def test_the_notice_can_be_dismissed():
    step = _remove("Market data EOD", confirm=True, dismiss=True)
    assert _texts(step, "sw-appdeps-notice") == []


@needs_node
def test_the_cleanup_action_fills_the_composer_and_sends_nothing():
    """A button that fires a build turn can be refused by the per-project turn lock, and it would
    put work past a plan gate the person never read. So it writes the prompt and stops."""
    step = _remove("Market data EOD", confirm=True, cleanup=True)
    assert step["cleanupCalls"] == []
    assert "Market data EOD" in step["seeded"]
    assert "src/queries.py" in step["seeded"] and "public/panel.js" in step["seeded"]


@needs_node
def test_a_removal_nothing_refers_to_offers_no_cleanup():
    """The offer is only worth having when there is something to act on."""
    step = _remove("Claude Sonnet 4", confirm=True)
    assert _texts(step, "sw-appdeps-notice-text")  # the notice is there
    assert not [t for t in _texts(step, "sw-appdeps-notice") if "clean" in t.lower()]


# ---- the two scopes that move on their own -----------------------------------------------------


@needs_node
def test_removing_a_binding_leaves_the_conversations_chips_alone():
    """The one moment the two scopes visibly disagree: the app stops being allowed to read
    `sales-db` while `sales-db` is still on the composer. It is the mirror of the sentence
    `removeFromConversation` draws, and it is correct — not a bug to fix later."""
    step = _remove("Market data EOD", confirm=True)
    assert "Market data EOD" not in step["bindings"]  # the Binding did go
    assert step["chips"] == step["chipsBefore"]
    # `ctx_source` IS `data_source:ds_1` — the same Resource the app was just unbound from.
    assert "ctx_source" in step["chips"]


# ---- the words, and where they are written -----------------------------------------------------


@needs_node
def test_no_menu_item_says_a_bare_remove_or_a_code_word():
    """The glossary's Remove rule: the scope is the only thing that tells the three lists apart,
    and `detach`/`unbind` name the app-scoped pair in code, never on screen."""
    step = _panel("app_a")
    labels = [
        i["label"]
        for r in step["rows"] + step["appRows"]
        for i in r.get("items", [])
    ]
    assert labels, "neither surface drew a menu at all"
    assert "Remove" not in labels
    assert not [label for label in labels if "nbind" in label or "etach" in label]


def test_the_code_words_stay_out_of_the_copy():
    """Including the sentence a refused Project removal draws, which used to send people to
    "Unbind it in Build" — a word the glossary bans and an act that now has a label of its own."""
    for rel in ("store.js", "components/resource-panel.js", "modes/builder.js"):
        src = (_JS / rel).read_text()
        for word in ("Unbind", "unbind it", "Detach", "detach it"):
            assert word not in src, f"{rel} puts {word} on screen"


def test_the_empty_sentence_is_written_once():
    """The panel takes the header's wording, so the two cannot drift apart again — which only
    holds if there is one copy of it."""
    holder = (_JS / "util.js").read_text()
    assert holder.count(TAIL) == 1
    for rel in ("modes/builder.js", "components/resource-panel.js"):
        assert TAIL not in (_JS / rel).read_text(), f"{rel} keeps its own copy"


@needs_node
def test_the_bind_receipt_names_the_surface_that_can_reverse_it():
    """A pointer names its destination in the words the reader will see on the way to it
    (ADR-0011). It said "Project resources, under {app}" while the panel held a section per app;
    the panel is the Project's one list now and the app's records — with the removal that acts on
    them — are behind the header's "App dependencies" item, so that is what the receipt says."""
    step = _run([{
        "addIn": True, "thread": "thr_many", "select": "app_c", "pick": "llm_alias:al_1",
    }])[-1]
    said = " ".join(step["said"])
    assert "Remove it under App dependencies." in said
    assert "Project resources" not in said


def test_the_composer_takes_the_seed_without_sending_it():
    """The seed is a draft. `onSend` is reached from the send control and from nowhere else."""
    composer = (_JS / "components" / "composer.js").read_text()
    end = composer.index("}, [composerSeed]);")
    effect = composer[composer.rindex("useEffect(", 0, end) : end]
    assert "setText(composerSeed)" in effect
    assert "onSend" not in effect


def test_a_confirm_left_open_across_an_app_switch_removes_nothing():
    """Neither removal route carries an app id — both resolve through whatever the server has
    selected — so a modal that names one app and is answered under another would take the Binding
    out of an app the person never pointed at. The title is a promise about which app loses it.

    A narrowing rather than a proof: the server still resolves the app itself, so the window is one
    request round trip instead of however long somebody leaves a modal open. Closing it entirely
    means the route naming its app."""
    step = _remove("Market data EOD", confirm=True, switchTo="app_b")

    assert not [c for c in step["calls"] if c.startswith("DELETE /bindings")]
