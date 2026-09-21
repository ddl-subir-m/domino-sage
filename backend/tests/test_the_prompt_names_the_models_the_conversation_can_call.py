"""The prompt tells the agent which models it may call, by the name it must use (#439).

MEASURED, not reasoned. A live turn asked `delegated_model_call` for `gpt-5.4`, was refused, and
asked again for `sonnet` 46 seconds later — one wasted model round trip on a turn that had already
computed its answer. `sonnet` was callable the whole time.

The agent reached for `gpt-5.4` because that is the only model name it reliably holds: `opencode.json`
names it top-level, no agent overrides it, and `shim/enforcement.py` rewrites every request to the
Ask slot WITHOUT telling OpenCode. The list of names it could have used never reached the prompt —
`_delegated_aliases` returns `labels`, the runtime `delegated.Turn` reads them, and `_chat_prompt`
never did. Its whole account of the list was a static sentence saying `alias` is the model's name
"as this prompt names it", which named nothing, plus a pointer at the refusal. Discovery by guessing,
at ~47s per guess.

WHAT THESE TESTS PIN. That the names reach the PROMPT, and that they are the same reading the GATE
performs. Not the refusal's wording, not the shim's silent rewrite — that rewrite decides which
wrong name gets guessed and #439 leaves it alone deliberately.

THROUGH THE REAL TURN, never `_chat_prompt` by hand. The prompt is assembled inside `_chat_stream`
after `arm_chat`, and the model answering the turn joins the callable set only while that pin is
held (#424). A hand-rendered prompt would drop that act and pass while production lost a name.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from sage.orchestrator.service import Orchestrator
from sage.resources.provider import FakeResourceProvider

from .fake_opencode import FakeOpenCode, Turn
from .test_chat_turn import OkFeedback, ScriptedGateway, _catalog

OPUS_ID, OPUS_NAME, OPUS_LABEL = "f-opus", "opus", "Claude Opus 4.6"


def _orch(tmp: Path) -> tuple[Orchestrator, FakeOpenCode]:
    template = tmp / "template"
    (template / "src").mkdir(parents=True)
    (template / "src" / "App.tsx").write_text("export default function App() { return null }\n")
    (template / "package.json").write_text("{}")
    ws = tmp / "mnt" / "code"
    oc = FakeOpenCode(ws, [Turn(text="ok"), Turn(text="ok")])
    orch = Orchestrator(workspace_dir=ws, template=template, gateway=ScriptedGateway(),
                        catalog=_catalog(), project_id="Sage", feedback=OkFeedback(),
                        opencode_client=oc, resources=FakeResourceProvider())
    orch.project(start_preview=False)
    return orch, oc


def _turn(orch: Orchestrator, oc: FakeOpenCode, tid: str) -> str:
    """Run one Chat turn and give back the prompt the agent was actually sent."""
    list(orch.chat_stream(tid, "classify these"))
    return oc.prompts[-1]["text"]


def _note(text: str) -> str:
    """The one paragraph that teaches `delegated_model_call`, and nothing else in the prompt.

    THE WHOLE PROMPT IS THE ADJACENT PATH. A chip draws a `Session context:` row carrying the same
    label, so a claim made against the whole text can pass on that row while the paragraph an agent
    reads about models says nothing. Measured, not feared: the unresolved-chip test below was
    written loose, and deleting the clause it exists to pin left it green.
    """
    return next(ln for ln in text.splitlines() if "delegated_model_call" in ln)


def _callable_name(orch: Orchestrator, tid: str) -> str:
    """The one call name the gate will accept this turn — the premise every test below rests on."""
    aliases, _, _ = orch._delegated_aliases(orch._chat_project(), tid)
    assert len(aliases) == 1, f"the fixture made {len(aliases)} models callable, not one: {aliases}"
    return aliases[0][0]


def test_a_chip_puts_the_models_call_name_in_the_prompt(tmp_path: Path):
    """AC1. One callable Alias, and the prompt says the name the tool wants.

    The label is what the person typed and what the panel shows; the call name is what
    `delegated_model_call` matches on. Asserting the NAME rather than the label is the whole point —
    the agent has to hand the gate a string the gate accepts, and those are different strings.
    """
    orch, oc = _orch(tmp_path)
    tid = orch.create_thread()["id"]
    orch.add_thread_context(tid, {
        "kind": "llm_alias", "name": OPUS_LABEL,
        "resourceId": f"llm_alias:{OPUS_ID}", "bindingKey": ["llm_alias", OPUS_ID],
    })

    name = _callable_name(orch, tid)
    assert name in _note(_turn(orch, oc, tid)), (
        f"the conversation can call {name!r} and the prompt never says so, so the agent has only "
        "the name its config gives it — which is #439 exactly"
    )


def test_a_model_bound_in_the_app_is_named_even_with_no_chip(tmp_path: Path):
    """AC2. The act that has no chip to fall back on.

    **Use in app** writes a Binding, and #410 made that Binding reach the turn. It is not a Session
    context chip, so it draws no context row — before this change it was callable and named NOWHERE
    the agent could read. #439 got wider with #410, not narrower, and this is the case that says so.
    """
    orch, oc = _orch(tmp_path)
    tid = orch.create_thread()["id"]
    orch.bind_llm_alias(OPUS_ID)

    assert not (orch.thread_context(tid).get("items") or []), (
        "the premise is a model with no chip; a chip here would let AC1's path carry this test"
    )
    name = _callable_name(orch, tid)
    assert name in _note(_turn(orch, oc, tid)), (
        f"a model bound through the app is callable and unnamed: the agent cannot reach {name!r} "
        "except by guessing it"
    )


def test_a_conversation_with_no_callable_model_is_told_so(tmp_path: Path):
    """AC3. Nothing callable, and the prompt says nothing is — rather than pointing at a refusal.

    The old copy made the refusal the documented discovery mechanism. On a turn where NO model can
    be called, following that instruction costs a round trip to learn a fact the prompt already
    knew. Silence and "there are none" are different sentences, and only one of them saves the call.
    """
    orch, oc = _orch(tmp_path)
    tid = orch.create_thread()["id"]

    # The premise, and it has to be read DURING the turn. The picker act supplies a model with
    # nobody having bound anything (#424) — but only while `arm_chat` holds the pin, so the same
    # helper called from out here answers the empty set and would make this test pass for the one
    # reason it must not: not because the empty case is told, but because every case is empty.
    real, seen = orch._delegated_aliases, []

    def recording(*a, **k):
        seen.append(real(*a, **k)[0])
        return (), {}, ()

    orch._delegated_aliases = recording  # type: ignore[method-assign]
    text = _turn(orch, oc, tid)
    assert seen and seen[0], (
        "the prompt never asked which models are callable, so emptying the answer proves nothing")

    note = _note(text)
    assert "no language model" in note.lower(), (
        "a turn that can call nothing was handed the sentence that sends it to the refusal to find "
        f"out. The paragraph said: {note!r}"
    )


def test_the_prompt_and_the_gate_are_one_reading(tmp_path: Path):
    """AC4. The same helper answers both, so the two cannot drift.

    The same argument `_bindings_here` carries for the panel's tick (#410): two surfaces making one
    claim have to derive it from one function, or they are two lists that happen to agree until an
    act is added to one of them. Proven by substituting the helper and watching BOTH surfaces move —
    a comparison of the two real lists would pass on a prompt that rebuilt the set by hand.
    """
    orch, oc = _orch(tmp_path)
    tid = orch.create_thread()["id"]
    sentinel = "a-model-no-fixture-here-defines"
    orch._delegated_aliases = (  # type: ignore[method-assign]
        lambda *_a, **_k: (((sentinel, "Sentinel"),), {sentinel: "Sentinel"}, ()))

    assert sentinel in _note(_turn(orch, oc, tid)), "the prompt did not read `_delegated_aliases`"
    assert sentinel in {n for n, _ in orch._delegated_turn_for(tid).aliases}, (
        "the gate did not read `_delegated_aliases`"
    )


def test_a_chip_the_listing_cannot_resolve_is_named_as_unreachable(tmp_path: Path):
    """Not callable and not absent, and the prompt has to tell those apart.

    A chip resolves through the live Alias listing, so a listing that will not answer drops it out
    of the callable set — fail-closed, and correct. But the person still put that model in front of
    this Conversation, and the panel still draws it. Reporting only "no language model has been
    given to this conversation" would call the person's own act absent rather than unreachable, and
    would send an agent to argue with somebody looking at the chip they added.

    `unresolved` comes back from the same call as the names, so saying this costs nothing extra.
    """
    orch, oc = _orch(tmp_path)
    tid = orch.create_thread()["id"]
    label = "A model Domino will not list"
    orch.add_thread_context(tid, {
        "kind": "llm_alias", "name": label,
        "resourceId": "llm_alias:f-no-such-alias",
        "bindingKey": ["llm_alias", "f-no-such-alias"],
    })

    _, _, unresolved = orch._delegated_aliases(orch._chat_project(), tid)
    assert label in unresolved, "the premise: the chip is in the Thread and does not resolve"

    note = _note(_turn(orch, oc, tid))
    assert label in note, (
        "a model the person attached went unmentioned where models are discussed, so an agent "
        f"reading that paragraph is told this conversation has none. It said: {note!r}"
    )


def test_a_read_that_fails_costs_the_list_and_not_the_turn(tmp_path: Path):
    """The list is worth a wasted call; it is not worth the turn.

    `_delegated_aliases` reads a manifest and a Thread's context off disk, and either can fail. A
    prompt that cannot be rendered is a turn that cannot run, so this falls back to the instruction
    without the list — the pre-#439 behaviour, which costs a round trip and answers the question.
    """
    orch, oc = _orch(tmp_path)
    tid = orch.create_thread()["id"]

    def boom(*_a, **_k):
        raise OSError("the manifest could not be read")

    orch._delegated_aliases = boom  # type: ignore[method-assign]
    text = _turn(orch, oc, tid)

    assert "delegated_model_call" in text, (
        "a failed read of the callable set took the whole instruction with it, so the turn lost a "
        "tool rather than losing a list"
    )


def test_a_cold_prompt_does_not_fetch_a_cosmetic_model_label(tmp_path, monkeypatch):
    orch, oc = _orch(tmp_path)
    tid = orch.create_thread()["id"]
    calls = []
    original = orch._resources.list_llm_aliases

    def listed():
        calls.append(True)
        return original()

    monkeypatch.setattr(orch._resources, "list_llm_aliases", listed)
    assert orch._alias_listing_at is None
    text = _turn(orch, oc, tid)

    assert "`a`" in _note(text), "the model's callable name must still reach the prompt"
    assert calls == [], "a display label put a gateway request before the answer"


def test_an_expired_cached_label_can_render_without_refreshing_its_listing(tmp_path, monkeypatch):
    orch, oc = _orch(tmp_path)
    tid = orch.create_thread()["id"]
    aliases = {a.id: a for a in orch._resources.list_llm_aliases()}
    aliases[OPUS_ID] = replace(aliases[OPUS_ID], name="a")
    orch._alias_listing_at = (0.0, aliases)
    calls = []
    monkeypatch.setattr(orch._resources, "list_llm_aliases", lambda: calls.append(True) or [])

    text = _turn(orch, oc, tid)

    assert f"`a` ({OPUS_LABEL})" in _note(text)
    assert calls == [], "only the label is stale; this is not a permission lookup"


def test_a_model_chip_still_requires_a_fresh_resolution_during_prompt_setup(tmp_path, monkeypatch):
    orch, oc = _orch(tmp_path)
    tid = orch.create_thread()["id"]
    orch.add_thread_context(tid, {
        "kind": "llm_alias", "name": OPUS_LABEL,
        "resourceId": f"llm_alias:{OPUS_ID}", "bindingKey": ["llm_alias", OPUS_ID],
    })
    orch._alias_listing_at = (0.0, {a.id: a for a in orch._resources.list_llm_aliases()})
    calls = []
    monkeypatch.setattr(orch._resources, "list_llm_aliases", lambda: calls.append(True) or [])

    note = _note(_turn(orch, oc, tid))

    assert calls == [True], "a chip's call name must be resolved from a current listing"
    assert f"`{OPUS_NAME}`" not in note
    assert OPUS_LABEL in note and "not reachable this turn" in note
