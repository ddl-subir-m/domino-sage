"""The mark beside a row and the set a turn may call are one answer, drawn twice (#410).

The report was a panel listing a language model that the conversation then refused. Both surfaces
were behaving as written: the panel read Session context chips, and `_delegated_aliases` counted a
Binding as reaching the conversation too. Neither said so, so the row offered to add something a
turn was already using.

BOTH SIDES RUN HERE. The Python half performs each act against a real orchestrator and reads
`_delegated_aliases`; the JS half is `js/context_mark_harness.mjs`, which boots the real store and
renders the real panel over the rows this file just produced. A source assertion could not make this
claim: the predicate and the field it reads are in different languages, and the defect was the join
between them rather than either end.

DERIVED FROM THE ACTS, not from a list of kinds. `ACTS` below is one entry per act
`_delegated_aliases` counts, and each one performs it through the same door a person uses. A test
enumerating resource kinds would have gone on passing while an act went unread — which is what the
panel did.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sage.orchestrator import brand
from sage.orchestrator.service import Orchestrator
from sage.resources.model_api_credentials import Credential, CredentialStore
from sage.resources.provider import FakeResourceProvider

from .fake_opencode import FakeOpenCode, Turn
from .test_a_shape_only_table_artifact_renders_as_a_receipt import _node, needs_node
from .test_chat_turn import OkFeedback, ScriptedGateway, _catalog

OPUS_ID, OPUS_LABEL = "f-opus", "Claude Opus 4.6"
# The picker's model is a DIFFERENT one from the other two acts', so that a row drawn for it cannot
# be a row some earlier act already earned the mark on.
SONNET_ID, SONNET_NAME, SONNET_LABEL = "f-sonnet", "sonnet", "Claude Sonnet 4.6"


def _orch(tmp: Path):
    template = tmp / "template"
    (template / "src").mkdir(parents=True)
    (template / "src" / "App.tsx").write_text("export default function App() { return null }\n")
    (template / "package.json").write_text("{}")
    ws = tmp / "mnt" / "code"
    oc = FakeOpenCode(ws, [Turn(text="ok")])
    orch = Orchestrator(workspace_dir=ws, template=template, gateway=ScriptedGateway(),
                        catalog=_catalog(), project_id="Sage", feedback=OkFeedback(),
                        opencode_client=oc, resources=FakeResourceProvider())
    orch.project(start_preview=False)
    return orch


def _member(orch, alias_id: str, label: str) -> None:
    """Put the Alias in the working set, which is what draws a row to carry the mark.

    Every act below needs this, and for the picker act it is the whole point: that model reaches the
    conversation without anybody having touched the panel, so nothing else would put a row on screen
    and "no row drawn" would pass this file's last assertion for the wrong reason.
    """
    orch.add_project_resource({
        "id": f"llm_alias:{alias_id}", "kind": "model_llm", "name": label,
        "alias": alias_id, "bindingKey": ["llm_alias", alias_id],
    })


def _chip(orch, project, tid: str) -> tuple[str, str]:
    """What **Use in this conversation** posts for a language model — see `api.js:528`."""
    orch.add_thread_context(tid, {
        "kind": "llm_alias", "name": OPUS_LABEL,
        "resourceId": f"llm_alias:{OPUS_ID}", "bindingKey": ["llm_alias", OPUS_ID],
    })
    return OPUS_ID, OPUS_LABEL


def _bind(orch, project, tid: str) -> tuple[str, str]:
    """What **Use in app** records. The act #410 reported: it reaches the turn and drew no mark."""
    orch.bind_llm_alias(OPUS_ID)
    return OPUS_ID, OPUS_LABEL


def _picker(orch, project, tid: str) -> tuple[str, str]:
    """The third act, and the one whose mark is deliberately withheld.

    Picking the model that answers a turn hands it the whole conversation, so `_delegated_aliases`
    counts it (#424). It is not an act on the ROW, though, and a tick nobody clicked for reads worse
    than the `+` it would replace — so the panel is expected to disagree here, and the disagreement
    is asserted rather than tolerated.

    The pin has to be ARMED for this act to happen at all. `_delegated_aliases` reads the routed
    model only while `state.chat_thread_id` names this Thread, and a finished turn has put that
    back down — so without the two calls below the act is absent, `_delegated_aliases` returns the
    empty set, and the row draws a `+` for the one reason this test must not accept: not because the
    picker is held out of the mark, but because nothing picked anything. `arm_chat` is what a live
    Chat turn holds while it runs (`model_control.py:172`).
    """
    project.control.pick_chat(SONNET_NAME)
    project.control.arm_chat(tid)
    return SONNET_ID, SONNET_LABEL


# One entry per act `_delegated_aliases` counts, performed through the same door a person uses, and
# each one says which Alias it made callable. `marks` is whether the panel is expected to draw the
# tick: three acts reach the conversation and two of them are acts on the row.
ACTS = [
    pytest.param(_chip, True, id="chip"),
    pytest.param(_bind, True, id="binding"),
    pytest.param(_picker, False, id="picker"),
]


def _drawn(orch, tid: str, *, mode: str = "chat", unlisted: tuple[str, ...] = ()) -> dict:
    """Run the panel over what the server actually answers, and return its rows by id."""
    members = orch.list_project_resources()
    context = orch.thread_context(tid)
    drawn = _node("context_mark_harness.mjs",
                  {"members": members, "context": context, "mode": mode,
                   "unlisted": list(unlisted)})
    rows = drawn["rows"]
    assert rows, "the panel drew no rows at all"
    # The mark is withheld from a row Domino no longer lists, so a fixture that quietly served a
    # missing row would make every claim below a claim about the wrong state. It did, once: the
    # harness matched one of the two membership spellings for an Alias and the bound row came back
    # `missing`, green, for a whole afternoon. Checked here rather than trusted.
    stale = [r["name"] for r in rows
             if r["liveness"] == "missing" and r["id"] not in set(unlisted)]
    assert not stale, f"the fixture served rows Domino does not list: {stale}"
    return {row["id"]: row for row in rows}


@needs_node
@pytest.mark.parametrize("act, marks", ACTS)
def test_the_row_and_the_callable_set_agree_for_each_act(tmp_path: Path, act, marks):
    orch = _orch(tmp_path)
    project = orch.project(start_preview=False)
    tid = orch.create_thread()["id"]
    list(orch.chat_stream(tid, "classify these"))

    alias_id, label = act(orch, project, tid)
    # After the act, and idempotent: binding joins the working set by itself, and the picker joins
    # nothing at all. The row has to exist either way or the last assertion here passes because the
    # panel drew nothing rather than because it drew a `+`.
    _member(orch, alias_id, label)

    # What the conversation may call, by call name, from the one helper that knows all three acts.
    aliases, _, _ = orch._delegated_aliases(project, tid)
    callable_ids = {a["id"] for a in orch.list_llm_aliases()
                    if a["name"] in {name for name, _ in aliases}}
    assert alias_id in callable_ids, "the act did not reach the conversation, so nothing is proven"

    row = _drawn(orch, tid)[f"llm_alias:{alias_id}"]

    if marks:
        assert row["slot"] == "mark", (
            f"{act.__name__}: the conversation can call this and the row still draws "
            f"{row['slot']!r}, which is #410 exactly"
        )
        assert not row["offersAdd"], "and it must stop offering an act already taken"
    else:
        assert row["slot"] == "add", (
            f"{act.__name__}: the row drew {row['slot']!r}. The picker is deliberately left out "
            "of the mark — if that decision changed, change it here and say so on #410"
        )
        assert row["offersAdd"], "and the offer stands, because the act on the row was never done"


@needs_node
def test_the_two_acts_that_earn_the_mark_do_not_borrow_each_others_sentence(tmp_path: Path):
    """One tick, two reasons, and the words behind it have to say which.

    `IN_CONTEXT_TITLE` sends a reader to the chip above the message box. On a row that only an app
    binds there is no chip there, so reusing that sentence would repeat #410's own mistake one layer
    in: pointing somebody at a control that is not on their screen.
    """
    orch = _orch(tmp_path)
    tid = orch.create_thread()["id"]
    list(orch.chat_stream(tid, "classify these"))
    _member(orch, OPUS_ID, OPUS_LABEL)

    _chip(orch, None, tid)
    chipped = _drawn(orch, tid)[f"llm_alias:{OPUS_ID}"]

    # A second orchestrator rather than taking the chip back off the first: the two sentences have
    # to be compared with exactly one act behind each, and a row that has held a chip and lost it is
    # a third state this claim is not about.
    orch2 = _orch(tmp_path / "second")
    tid2 = orch2.create_thread()["id"]
    list(orch2.chat_stream(tid2, "classify these"))
    _member(orch2, OPUS_ID, OPUS_LABEL)
    _bind(orch2, None, tid2)
    bound = _drawn(orch2, tid2)[f"llm_alias:{OPUS_ID}"]

    assert chipped["slot"] == bound["slot"] == "mark", "both acts earn the same mark"
    assert "chip" in chipped["title"], "the chip's sentence names the chip rail"
    assert chipped["title"] != bound["title"], (
        "the Binding borrowed the chip's sentence, which sends a reader to a chip that is not there"
    )
    # Through the pack, never spelled out, for the reason the rest of this branch's copy is:
    # `paranoid-pack` scans `sage/` only, so a hard-coded noun here is the one that survives a
    # rename and goes on asserting the old product's word.
    assert brand.text("{builtApp}") in bound["title"], (
        "the Binding's sentence names the record that put it there instead"
    )

    # And the same distinction where the mark cannot be seen at all. A screen reader told only
    # "is in this conversation" would go looking for a chip to take off that was never there —
    # which would leave the one reader who depends on words entirely the least well told.
    assert chipped["aria"] != bound["aria"], "the two acts read identically to a screen reader"
    assert brand.text("{builtApp}") in bound["aria"], "the Binding's says why it is reachable"


@needs_node
def test_a_bound_model_api_is_not_ticked_because_no_turn_can_reach_it(tmp_path: Path):
    """The one bindable kind that **Use in app** does not put within a turn's reach.

    A Model API sits in `liveread.grant.CALLED`, so a Live read of one is refused outright — "called
    by the app, not read like a store or file" — and it is not an Alias, so `_delegated_aliases`
    will not call it either. Bound, listed on the panel, and unreachable.

    So the mark has to come off the ACTS a turn can actually follow rather than off "is there a
    Binding". Ticking this row would rebuild #410's defect in a new kind: a surface promising what
    another refuses. `_binding_reaches_a_turn` asks the two readers instead of holding a list, and
    this is the case that makes the difference visible.
    """
    orch = _orch(tmp_path)
    tid = orch.create_thread()["id"]
    list(orch.chat_stream(tid, "classify these"))

    api = orch.list_model_apis()[0]
    # The credential first: `bind_model_api` refuses without one, because a Model API opens for
    # nothing but its access token. Not this test's subject, so stored rather than pasted.
    CredentialStore(orch.project(start_preview=False).workspace.path).put(
        api["id"], Credential(f"https://example.domino.tech:443/models/{api['id']}/latest/model",
                              "SsQBZCygwPP79P8Q57qLPrGIfj67YAFBm3nrTT6Sm7vuPhBPBJvAL7lHm6jp36qB"))
    orch.bind_model_api(api["id"])

    row = next(r for r in _drawn(orch, tid).values() if r["name"] == api["name"])
    assert row["boundHere"] is False, (
        "the server marked a Model API reachable; `_binding_reaches_a_turn` should hold it out"
    )
    assert row["slot"] == "add", "and the row keeps the offer, because nothing here is reachable yet"


@needs_node
def test_in_build_a_binding_draws_no_conversation_mark(tmp_path: Path):
    """The mark speaks Chat's sentence, so it belongs to Chat.

    Build says a Binding twice already, in its own grammar: the `Required by {app}` bar and the
    `saysAppUse` subtitle. Before this change the slot drew a spacer there — `attachedIds` holds no
    chips in Build — and widening the mark without a mode guard would have put "is in this
    conversation" on every bound row of a surface that is not a conversation.
    """
    orch = _orch(tmp_path)
    project = orch.project(start_preview=False)
    tid = orch.create_thread()["id"]
    list(orch.chat_stream(tid, "classify these"))
    _bind(orch, project, tid)
    _member(orch, OPUS_ID, OPUS_LABEL)

    in_chat = _drawn(orch, tid)[f"llm_alias:{OPUS_ID}"]
    in_build = _drawn(orch, tid, mode="build")[f"llm_alias:{OPUS_ID}"]

    assert in_chat["slot"] == "mark", "the same Binding, on the surface whose sentence this is"
    assert in_build["slot"] == "spacer", (
        "Build drew the conversation's mark; the row's own `Required by` bar is Build's way to say it"
    )


@needs_node
def test_a_bound_resource_domino_no_longer_lists_is_not_ticked(tmp_path: Path):
    """The mark claims the Conversation can REACH it, and a row Domino dropped cannot be reached.

    `boundHere` is computed off the app's manifest alone, which keeps recording a Binding long after
    the Resource behind it is gone — that is deliberate, and it is what lets the row offer a door
    that opens (#161, ADR-0034). But the manifest is not evidence of reach. Without the liveness
    guard the row draws the missing mark and this one at once, and a Live read of it refuses.
    """
    orch = _orch(tmp_path)
    project = orch.project(start_preview=False)
    tid = orch.create_thread()["id"]
    list(orch.chat_stream(tid, "classify these"))
    _bind(orch, project, tid)
    _member(orch, OPUS_ID, OPUS_LABEL)

    rid = f"llm_alias:{OPUS_ID}"
    row = _drawn(orch, tid, unlisted=(rid,))[rid]

    assert row["boundHere"] is True, "the manifest still records the Binding, which is the premise"
    assert row["liveness"] == "missing", "and the listing no longer names it"
    assert row["slot"] == "add", (
        "the row was ticked as reachable while the same row was drawn as gone from Domino"
    )


@needs_node
def test_a_binding_does_not_tick_another_kinds_row_that_shares_its_id(tmp_path: Path):
    """The join carries the type or it joins the wrong things.

    A Binding records a BARE id beside its kind; a membership row records `kind:id`. Both sides
    already write the prefixed spelling, so the intersection can be exact — but adding the bare id
    to it as well, which is the obvious way to make the two meet, quietly makes the type optional.
    Domino ids are namespaced per resource type, so nothing rules out a Dataset and an Alias that
    share one, and then binding either would tick both.

    Written because the guard against it was UNPROVEN: reverting it left every other test in this
    file green, which is no evidence at all. This is the pair that makes it evidence.
    """
    orch = _orch(tmp_path)
    project = orch.project(start_preview=False)
    tid = orch.create_thread()["id"]
    list(orch.chat_stream(tid, "classify these"))

    _bind(orch, project, tid)
    _member(orch, OPUS_ID, OPUS_LABEL)
    # The collision: a different kind, the same bare id, nothing bound to it.
    orch.add_project_resource({
        "id": f"dataset:{OPUS_ID}", "kind": "dataset", "name": "Rows that share an id",
    })

    rows = _drawn(orch, tid)
    assert rows[f"llm_alias:{OPUS_ID}"]["boundHere"] is True, "the bound row, which is the premise"
    assert rows[f"dataset:{OPUS_ID}"]["boundHere"] is False, (
        "a Binding on one kind reached another kind's row, so the join lost the type"
    )
    assert rows[f"dataset:{OPUS_ID}"]["slot"] == "add", "and the row keeps its offer"
