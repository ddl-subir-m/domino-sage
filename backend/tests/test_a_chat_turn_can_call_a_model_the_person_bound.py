"""ADR-0057, #370: a Chat turn can call an LLM Alias the person put in the conversation.

The symptom this closes was not a crash. A person added `opus` to a Conversation and asked for an
LLM-driven classification pass; Sage did the work without the model and said so, accurately —
*"I wasn't able to reach the LLM Gateway (opus) from this environment — it requires browser-based
authentication."* The agent had read `template/react-vite/src/appLlm.ts`, which says exactly that and
is right about the published app's own call. What was missing was a route a turn can take.

So these tests are about a capability AND its bounds, and the bounds are the half that can rot
quietly: a call that is made when it should have been refused leaves an answer on screen that looks
like every other answer. One test per condition, and each names the condition rather than the code.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from sage.delegated import call as delegated
from sage.delegated import mcp as delegated_mcp
from sage.orchestrator.service import _DELEGATED_CALLS_MAX, Orchestrator
from sage.resources.provider import (
    ApprovedModels,
    FakeResourceProvider,
    ResourceUnavailable,
)
from sage.workspace.threads import ThreadStore

from .fake_opencode import FakeOpenCode, Turn
from .test_a_shape_only_table_artifact_renders_as_a_receipt import _node, needs_node
from .test_chat_turn import OkFeedback, ScriptedGateway, _catalog

ROOT = Path(__file__).resolve().parents[2]

# `opus` in the fake catalogue: the id a chip carries, the name a request carries, the label a
# person reads. All three are different strings, which is the whole reason `_delegated_aliases` has
# to resolve rather than pass one of them through.
OPUS_ID, OPUS_NAME, OPUS_LABEL = "f-opus", "opus", "Claude Opus 4.6"
SONNET_LABEL = "Claude Sonnet 4.6"


class AnswerGateway(ScriptedGateway):
    """A gateway that answers a Delegated model call with something a test can recognise."""

    def __init__(self, answer: str = "REFUND REQUEST") -> None:
        super().__init__()
        self.answer = answer

    def route(self, request, labels):
        if getattr(labels, "component", "") != "chat-delegated":
            yield from super().route(request, labels)
            return
        self.seen.append((request, labels))
        body = json.dumps({"choices": [{"delta": {"content": self.answer}}]})
        yield f"data: {body}\n\ndata: [DONE]\n\n".encode()

    @property
    def delegated(self) -> list:
        return [(r, l) for r, l in self.seen if getattr(l, "component", "") == "chat-delegated"]


class Aliases(FakeResourceProvider):
    """The fake catalogue, with a switch for a gateway that will not list."""

    def __init__(self) -> None:
        super().__init__()
        self.listing_fails = False

    def list_llm_aliases(self):
        if self.listing_fails:
            raise ResourceUnavailable("the gateway answered 503")
        return super().list_llm_aliases()


def _orch(tmp: Path, gateway=None, resources=None, turns=None):
    template = tmp / "template"
    (template / "src").mkdir(parents=True)
    (template / "src" / "App.tsx").write_text("export default function App() { return null }\n")
    (template / "package.json").write_text("{}")
    ws = tmp / "mnt" / "code"
    oc = FakeOpenCode(ws, turns or [Turn(text="ok")])
    orch = Orchestrator(workspace_dir=ws, template=template, gateway=gateway or AnswerGateway(),
                        catalog=_catalog(), project_id="Sage", feedback=OkFeedback(),
                        opencode_client=oc, resources=resources or Aliases())
    orch.project(start_preview=False)
    return orch, oc


def _token(oc) -> str:
    m = re.search(r"Read token: (lrt_[A-Za-z0-9_-]+)", oc.prompts[-1]["text"])
    assert m, f"no token in the turn prompt:\n{oc.prompts[-1]['text'][:400]}"
    return m.group(1)


def _chip(orch, tid, alias_id=OPUS_ID, label=OPUS_LABEL):
    """What **Use in this conversation** posts for a language model — see `api.js:528`."""
    return orch.add_thread_context(tid, {
        "kind": "llm_alias", "name": label,
        "resourceId": f"llm_alias:{alias_id}", "bindingKey": ["llm_alias", alias_id],
    })


def _ask(orch, token, **args) -> str:
    reply = orch.delegated_model_call({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": delegated.TOOL_NAME, "arguments": {"token": token, **args}},
    })
    return reply["result"]["content"][0]["text"]


def _ready(tmp: Path, gateway=None, resources=None):
    """A Conversation with `opus` in it and one turn already run, so a token exists."""
    orch, oc = _orch(tmp, gateway=gateway, resources=resources)
    tid = orch.create_thread()["id"]
    _chip(orch, tid)
    list(orch.chat_stream(tid, "classify these support cases"))
    return orch, oc, tid


# ---- the name, which three files have to agree on ----------------------------------------------


def test_a_delegated_model_call_is_named_the_same_either_way():
    """OpenCode names a default-export custom tool after its FILE, so the filename is the tool name
    — and the prompts, the dispatcher and the context line all have to say the same string. Live
    read lost a day to exactly this class of drift twice (ADR-0041), which is why it is pinned here
    rather than left to be noticed."""
    ts = ROOT / "backend" / "sage" / "delegated" / "tools" / f"{delegated.TOOL_NAME}.ts"
    assert ts.is_file(), "the file's NAME is the tool's name; renaming it renames the tool"
    assert [t["name"] for t in delegated_mcp.TOOLS] == [delegated.TOOL_NAME], (
        "and the dispatcher keys on that same bare name")
    assert f'name: "{delegated.TOOL_NAME}"' in ts.read_text(), (
        "the tool posts its own bare name back to Python, so the .ts and the dispatcher pin "
        "together and neither can be renamed alone")
    assert delegated.TOOL_NAME in (ROOT / "template" / "chat" / "AGENTS.md").read_text(), (
        "and the prompt teaches the name that exists. `template/chat/AGENTS.md` is hand-mirrored "
        "into opencode.json, and `test_a_chat_turn_is_told_a_columns_values_not_just_its_name` "
        "pins those two to each other")


def test_the_custom_tool_is_installed_beside_live_reads_and_posts_the_mcp_call(tmp_path: Path):
    """The tool is a real file OpenCode loads, and it lives in its OWN module's `tools/` directory —
    so the installer has to read both, and a collision between them is refused rather than resolved
    by iteration order. Exercised through node for the reason `artifact_write`'s own test is: the
    schema cannot say "optional", so the nulls are dropped HERE, and Python is never asked to learn
    a second spelling of absent."""
    import subprocess

    from sage.orchestrator.app import _install_opencode_tools

    global_dir = tmp_path / "opencode"
    _install_opencode_tools(ROOT, global_dir)
    tool = global_dir / "tools" / f"{delegated.TOOL_NAME}.ts"
    assert tool.is_file()
    assert (global_dir / "tools" / "live_read.ts").is_file(), (
        "and the first module's tools are still installed — a second source directory must add to "
        "the set, never replace it")

    script = """
import { readFileSync } from 'node:fs';
const source = readFileSync(process.argv[1]);
const { default: tool } = await import('data:text/javascript;base64,' + source.toString('base64'));
const args = { token: 'lrt_x', alias: 'opus', prompt: 'Classify', system: null, max_tokens: null };
globalThis.fetch = async (url, options) => {
  if (!url.endsWith('/mcp/delegated-model') || options.method !== 'POST') throw Error('wrong route');
  const body = JSON.parse(options.body);
  if (body.params.name !== 'delegated_model_call') throw Error('wrong tool name');
  const sent = Object.keys(body.params.arguments).sort().join(',');
  if (sent !== 'alias,prompt,token') throw Error('nulls were not dropped: ' + sent);
  return { ok: true, json: async () => ({ result: { content: [{ text: 'REFUND' }] } }) };
};
if (await tool.execute(args) !== 'REFUND') throw Error('the answer did not come back');
globalThis.fetch = async () => { throw Error('offline') };
if (!(await tool.execute(args)).includes('Nothing was asked')) throw Error('missing network failure');
globalThis.fetch = async () => ({ ok: false, status: 500, json: async () => ({}) });
if (!(await tool.execute(args)).includes('HTTP 500')) throw Error('missing HTTP failure');
"""
    subprocess.run(["node", "--input-type=module", "-e", script, str(tool)],
                   check=True, capture_output=True)


def test_the_turn_teaches_the_tool_and_both_tools_share_one_token(tmp_path: Path):
    """One token per turn, not two. The token is what names the Conversation, and asking the
    assistant to juggle two of them is a second chance to relay the wrong one — while the thing the
    mechanism protects against is precisely an assistant naming a scope of its own (ADR-0038)."""
    _orch, oc, _tid = _ready(tmp_path)
    prompt = oc.prompts[-1]["text"]

    assert delegated.TOOL_NAME in prompt
    assert prompt.count(_token(oc)) == 1, "one mint, said once, used by both tools"
    assert f"Up to {_DELEGATED_CALLS_MAX} calls per turn" in prompt


# ---- the capability ------------------------------------------------------------------------------


def test_an_alias_this_conversation_named_is_called_and_its_answer_comes_back(tmp_path: Path):
    gateway = AnswerGateway("REFUND REQUEST")
    orch, oc, _tid = _ready(tmp_path, gateway=gateway)

    said = _ask(orch, _token(oc), alias=OPUS_LABEL, prompt="Classify: my card was charged twice")

    assert said == "REFUND REQUEST"
    (request, _labels), = gateway.delegated
    assert request["model"] == OPUS_NAME, (
        "the CALLABLE name, not the chip's label and not the catalogue id — three different "
        "strings, and only one of them is what `request['model']` takes")
    assert request["messages"] == [
        {"role": "user", "content": "Classify: my card was charged twice"}]


def test_the_call_is_tagged_chat_delegated_so_built_app_keeps_its_meaning(tmp_path: Path):
    """`built-app` means the previewed or published app's own model call. A Chat turn's delegated
    call is not one, and tagging it so would make the gateway's usage dashboard unable to separate
    two kinds of spend nobody can tell apart afterwards (ADR-0057)."""
    gateway = AnswerGateway()
    orch, oc, tid = _ready(tmp_path, gateway=gateway)

    _ask(orch, _token(oc), alias=OPUS_NAME, prompt="hello")

    (_request, labels), = gateway.delegated
    assert labels.component == "chat-delegated"
    assert labels.session == tid, "costable against the Conversation that asked for it"


def _listing_down(tmp_path: Path):
    """A Conversation holding a BOUND `opus` and a CHIPPED `sonnet`, with the listing then failing."""
    resources = Aliases()
    gateway = AnswerGateway()
    orch, oc = _orch(tmp_path, gateway=gateway, resources=resources)
    orch.bind_llm_alias(OPUS_ID)
    tid = orch.create_thread()["id"]
    _chip(orch, tid, alias_id="f-sonnet", label=SONNET_LABEL)
    list(orch.chat_stream(tid, "classify these"))
    resources.listing_fails = True
    return orch, oc, gateway


def test_an_alias_bound_to_the_app_survives_a_gateway_that_will_not_list(tmp_path: Path):
    """A Binding keeps its own `name` and `display_name`, and `bindings.py` says why in so many
    words: the record has to render when the gateway is unreachable, which is exactly when knowing
    what an app depends on matters most. So a bound Alias is callable with the listing down."""
    orch, oc, gateway = _listing_down(tmp_path)

    assert _ask(orch, _token(oc), alias=OPUS_NAME, prompt="hi") == "REFUND REQUEST"
    assert gateway.delegated[0][0]["model"] == OPUS_NAME


def test_a_chip_sage_could_not_resolve_is_callable_under_no_name_at_all(tmp_path: Path):
    """A chip keeps no callable name — the panel posts the label and `bindingKey`, and
    `add_thread_context` strips the catalogue's `alias` field back off as a membership field. So a
    chip resolves through the live listing, and a listing that will not answer drops it rather than
    guessing a string to put in `request['model']`.

    Asked by the LABEL and not only by the name it would have had. Under the failure this guards
    against, the label is precisely what a fallback would install as the callable name, and a test
    that only tried `sonnet` would pass while the chip was wide open under `Claude Sonnet 4.6`."""
    orch, oc, gateway = _listing_down(tmp_path)

    for asked in (SONNET_LABEL, "sonnet", "f-sonnet"):
        refused = _ask(orch, _token(oc), alias=asked, prompt="hi")
        assert "language model" in refused, asked
        assert "REFUND" not in refused, asked
    assert gateway.delegated == [], "nothing reached the gateway under any spelling"


def test_a_chip_the_listing_could_not_answer_for_is_not_called_absent(tmp_path: Path):
    """Two facts, two sentences. "Not in this conversation" is false of a model the person put
    there, and it sends them to add a thing that is already on screen — so the one case Sage caused
    says so and asks for a retry, while a name nobody added still names the set."""
    orch, oc, _gateway = _listing_down(tmp_path)

    mine = _ask(orch, _token(oc), alias=SONNET_LABEL, prompt="hi")
    theirs = _ask(orch, _token(oc), alias="gpt-5.4", prompt="hi")

    assert "couldn't read the list of language models this Turn" in mine
    assert "isn't a language model in this conversation" not in mine
    assert "gpt-5.4 isn't a language model in this conversation" in theirs
    assert OPUS_LABEL in theirs, "and that one still names what DID resolve"


# ---- the bounds ----------------------------------------------------------------------------------


def test_a_model_this_conversation_never_named_is_refused_and_the_set_is_named(tmp_path: Path):
    """The bind is the consent. Refused rather than substituted: a person who asked for one model
    and got an answer from another, with no sentence saying so, is the defect #293 and #317 are both
    open about — and it is invisible, because the answer looks like any other answer."""
    gateway = AnswerGateway()
    orch, oc, _tid = _ready(tmp_path, gateway=gateway)

    said = _ask(orch, _token(oc), alias="gpt-5.4", prompt="Classify this")

    assert gateway.delegated == [], "nothing reached the gateway"
    assert "gpt-5.4 isn't a language model in this conversation" in said
    assert OPUS_LABEL in said, "the refusal names the set"
    assert "Use in this conversation" in said, "and the act that fixes it"


def test_a_conversation_with_no_model_in_it_is_told_that_and_not_shown_an_empty_set(tmp_path: Path):
    """A different fact and a different act. Naming an empty set as though it were a choice reads
    as a bug, and sends the agent looking for a model that was never there."""
    orch, oc = _orch(tmp_path)
    tid = orch.create_thread()["id"]
    list(orch.chat_stream(tid, "classify these"))

    said = _ask(orch, _token(oc), alias="opus", prompt="Classify this")

    assert "No language model is in this conversation" in said
    assert "Add one with Use in this conversation" in said


def test_the_cap_refuses_loudly_rather_than_letting_a_delegated_loop_spend_the_turn(tmp_path: Path):
    """Uncapped, a delegated loop is a turn that never goes quiet — every call refreshes the
    activity clock — so the only thing that could end it is the 600-second ceiling, and the person
    waits all of it for nothing. Refused loudly, because an agent told nothing cannot tell a cap
    from a model with nothing to say."""
    gateway = AnswerGateway()
    orch, oc, _tid = _ready(tmp_path, gateway=gateway)
    token = _token(oc)

    for _ in range(_DELEGATED_CALLS_MAX):
        assert _ask(orch, token, alias=OPUS_NAME, prompt="x") == "REFUND REQUEST"
    over = _ask(orch, token, alias=OPUS_NAME, prompt="x")

    assert len(gateway.delegated) == _DELEGATED_CALLS_MAX
    assert f"already called a language model {_DELEGATED_CALLS_MAX} times" in over
    assert "Finish with what you have" in over


def test_the_next_turn_starts_the_count_over(tmp_path: Path):
    """The count is per TURN, and it is reset when the next turn's token is minted rather than when
    the last one ended: a turn that was stopped, timed out or took the process with it would
    otherwise leave its count standing, and the next question would meet a cap it never spent."""
    gateway = AnswerGateway()
    orch, oc, tid = _ready(tmp_path, gateway=gateway)
    for _ in range(_DELEGATED_CALLS_MAX):
        _ask(orch, _token(oc), alias=OPUS_NAME, prompt="x")

    list(orch.chat_stream(tid, "now do the next batch"))

    assert _ask(orch, _token(oc), alias=OPUS_NAME, prompt="x") == "REFUND REQUEST"


def test_a_model_outside_the_sensitivity_lock_is_refused_by_name(tmp_path: Path):
    """The lock, asked with the Conversation NAMED (ADR-0057). A Delegated model call is a turn of
    that Conversation, so the transcript's declared rows are exactly what decides where this call
    may go — the door it arrived through does not change that."""
    gateway = AnswerGateway()
    orch, oc, tid = _ready(tmp_path, gateway=gateway)
    asked: list = []

    def locked(project, conversation):
        asked.append(conversation)
        return ApprovedModels(names=frozenset({"qwen-2-5"}), order=("qwen-2-5",),
                              group_name="approved", members=1), ""

    orch._sensitivity_for_turn = locked

    said = _ask(orch, _token(oc), alias=OPUS_NAME, prompt="Classify this")

    assert asked == [tid], "the gate is asked, and it is asked about THIS Conversation"
    assert gateway.delegated == [], "nothing reached the gateway"
    assert f"{OPUS_LABEL} isn't approved for the data in this conversation" in said
    assert "Approved here: qwen-2-5" in said


def test_a_gate_that_cannot_be_read_refuses_rather_than_calling(tmp_path: Path):
    """FAILS CLOSED, and this is the asymmetry ADR-0057 spells out: the preview door may fail open
    because a preview call is not a turn, and this may not. Failing open here moves a person's rows
    to an unapproved model on the strength of a read that failed."""
    gateway = AnswerGateway()
    orch, oc, _tid = _ready(tmp_path, gateway=gateway)

    def broken(project, conversation):
        raise ResourceUnavailable("the gateway answered 503")

    orch._sensitivity_for_turn = broken

    said = _ask(orch, _token(oc), alias=OPUS_NAME, prompt="Classify this")

    assert gateway.delegated == [], "nothing reached the gateway"
    assert "couldn't check which models the data in this conversation allows" in said


def test_a_token_from_another_turn_names_no_conversation(tmp_path: Path):
    """What makes the grant a rule rather than a request. One OpenCode server hosts many
    Conversations, so an assistant that could name its own would hold the scope that scope is
    protecting against (ADR-0038)."""
    gateway = AnswerGateway()
    orch, _oc, _tid = _ready(tmp_path, gateway=gateway)

    said = _ask(orch, "lrt_not-a-real-token", alias=OPUS_NAME, prompt="Classify this")

    assert gateway.delegated == []
    assert "not current" in said


# ---- what the person sees, and what the transcript keeps -----------------------------------------


class CallingOpenCode(FakeOpenCode):
    """An agent that makes Delegated model calls while its turn runs, which is when they happen.

    The rest of this file calls the tool after the turn, because that is enough to test the rule.
    The step line and the receipt are about the TURN, so they need a call inside one.
    """

    def __init__(self, workspace: Path, turns, orch_box: dict, calls: int = 2) -> None:
        super().__init__(workspace, turns)
        self._box = orch_box
        self._calls = calls

    def send_prompt(self, session_id, text, **kwargs):
        super().send_prompt(session_id, text, **kwargs)
        orch = self._box.get("orch")
        token = re.search(r"Read token: (lrt_[A-Za-z0-9_-]+)", text)
        if orch is None or token is None:
            return
        for i in range(self._calls):
            _ask(orch, token.group(1), alias=OPUS_NAME, prompt=f"Classify case {i}")


def _turn_with_calls(tmp_path: Path, calls: int = 2):
    box: dict = {}
    template = tmp_path / "template"
    (template / "src").mkdir(parents=True)
    (template / "src" / "App.tsx").write_text("export default function App() { return null }\n")
    (template / "package.json").write_text("{}")
    ws = tmp_path / "mnt" / "code"
    oc = CallingOpenCode(ws, [Turn(text="Two cases were refunds.")], box, calls=calls)
    orch = Orchestrator(workspace_dir=ws, template=template, gateway=AnswerGateway(),
                        catalog=_catalog(), project_id="Sage", feedback=OkFeedback(),
                        opencode_client=oc, resources=Aliases())
    orch.project(start_preview=False)
    box["orch"] = orch
    tid = orch.create_thread()["id"]
    _chip(orch, tid)
    return orch, tid, list(orch.chat_stream(tid, "classify these support cases"))


def test_the_person_sees_a_step_line_naming_the_alias_and_the_count(tmp_path: Path):
    """Not a card — this may fire many times in one turn and a card each time is not proportionate
    to one model call. Not silence either: it is spend, and the person watching has a Stop button
    that is only useful if they know something is running (ADR-0057)."""
    _orch, _tid, events = _turn_with_calls(tmp_path, calls=2)

    lines = [e for e in events if e.get("kind") == "tool" and e.get("doing") == "model"]
    assert [e["detail"] for e in lines] == [
        f"{OPUS_LABEL} (1 of {_DELEGATED_CALLS_MAX})",
        f"{OPUS_LABEL} (2 of {_DELEGATED_CALLS_MAX})",
    ]
    assert all(e["tool"] == delegated.TOOL_NAME for e in lines)
    assert not any(e.get("type") == "artifacts" for e in lines), "a step line, never a card"


def test_the_transcript_keeps_a_receipt_and_not_the_exchanges(tmp_path: Path):
    """A pass over several hundred cases would otherwise put several hundred model answers into a
    transcript that is read back on every later turn. That is a scope problem before it is a cost
    one: those answers are about the person's rows (ADR-0057)."""
    orch, tid, events = _turn_with_calls(tmp_path, calls=2)
    history = ThreadStore(orch.project(start_preview=False).record.path).read_history(tid)

    receipts = [e for e in history if e.get("type") == "delegated-calls"]
    assert len(receipts) == 1
    assert receipts[0]["calls"] == 2
    assert receipts[0]["aliases"] == [OPUS_LABEL]
    assert receipts[0]["message"] == f"Asked {OPUS_LABEL} 2 times this Turn."

    kept = json.dumps(history)
    assert "Classify case 0" not in kept, "the call does not enter the transcript"
    assert "REFUND REQUEST" not in kept, "and neither does the answer"
    assert "Two cases were refunds." in kept, "the agent's own answer persists as it always did"
    live = [e for e in events if e.get("type") == "delegated-calls"]
    assert [e["message"] for e in live] == [receipts[0]["message"]], (
        "and the person is shown it live as well as on reload — one sentence, not two that can "
        "drift")

    steps = [e for e in history if e.get("kind") == "tool"]
    assert steps == [], "the step lines are the turn happening, not the record of it"


def test_a_turn_that_called_nothing_leaves_no_receipt(tmp_path: Path):
    """A receipt for nothing is a line every Thread carries and nobody can act on."""
    orch, tid, _events = _turn_with_calls(tmp_path, calls=0)
    history = ThreadStore(orch.project(start_preview=False).record.path).read_history(tid)

    assert [e for e in history if e.get("type") == "delegated-calls"] == []


# ---- the shape of a failure ----------------------------------------------------------------------


def test_a_gateway_that_raises_tells_the_assistant_nothing_was_asked(tmp_path: Path):
    """An assistant handed only "the gateway failed" goes off, answers another way — which is right
    — and then describes the result as though a model had produced it. Nothing was asked, and that
    has to be the first sentence."""
    class Dead(AnswerGateway):
        def route(self, request, labels):
            if getattr(labels, "component", "") == "chat-delegated":
                raise RuntimeError("connection reset")
            yield from ScriptedGateway.route(self, request, labels)

    orch, oc, _tid = _ready(tmp_path, gateway=Dead())

    said = _ask(orch, _token(oc), alias=OPUS_NAME, prompt="Classify this")

    assert said.startswith("The model was not called:")
    assert "Nothing was asked and no answer came back" in said
    assert "do not report an answer no model gave you" in said


@pytest.mark.parametrize(("args", "says"), [
    ({"alias": OPUS_NAME}, "Send the text to ask the model"),
    ({"prompt": "Classify this"}, "Name the language model to call"),
])
def test_a_call_missing_half_its_arguments_is_told_which_half(tmp_path: Path, args, says):
    """Each half says which half. One sentence for both would send an agent that forgot the prompt
    off to re-check the Alias it got right."""
    orch, oc, _tid = _ready(tmp_path)
    assert says in _ask(orch, _token(oc), **args)


# ---- the client half, driven through the real store ----------------------------------------------
#
# Two branches render this: the live SSE reducer and the reload path that rebuilds a Thread from
# history. They have drifted before, so both are asked the same question here rather than one of
# them being read and believed.


@needs_node
def test_the_step_line_names_the_alias_while_the_call_is_running():
    """What the person actually reads while a delegated pass runs. `activityLabel` has a default
    arm that says "Thinking…" for a `doing` it does not know, so an unhandled kind is not a missing
    line — it is a line that says the wrong thing, which is why this is asked of the real store."""
    result = _node("chat_stream_harness.mjs", [
        {"type": "agent", "kind": "tool", "tool": delegated.TOOL_NAME, "doing": "model",
         "detail": f"{OPUS_LABEL} (1 of {_DELEGATED_CALLS_MAX})"},
        {"type": "agent", "kind": "tool", "doing": "idle"},
        {"type": "agent", "kind": "text", "text": "Two cases were refunds."},
        {"type": "done", "ok": True},
    ])
    assert f"Asking {OPUS_LABEL} (1 of {_DELEGATED_CALLS_MAX})…" in result["typings"]


@needs_node
def test_the_receipt_reads_the_same_live_and_on_reload():
    """One sentence, in two renderers. The live branch and the replay branch agree only until one
    of them is edited, so the claim is an equality rather than two separate assertions."""
    said = f"Asked {OPUS_LABEL} 2 times this Turn."
    turn = [
        {"type": "agent", "kind": "text", "text": "Two cases were refunds."},
        {"type": "delegated-calls", "calls": 2, "aliases": [OPUS_LABEL], "message": said},
        {"type": "done", "ok": True},
    ]
    live = _node("chat_stream_harness.mjs", turn)["final"]
    replay = _node("table_artifact_harness.mjs", [
        {"thread": {"id": "thr_d", "history": turn}}, {"open": "thr_d"},
    ])[1]["blocks"]

    receipt = {"type": "status", "ok": True, "value": said}
    assert receipt in live, "shown while the turn runs"
    assert receipt in replay, "and still there when the Thread is reopened"


def test_the_cap_holds_when_several_calls_arrive_at_once(tmp_path: Path):
    """OpenCode can put several tool calls in one step, so the cap has to be a reservation and not a
    count somebody reads and then raises. Read-then-raise holds on average, which is not what a cap
    is for: the whole point is that the turn cannot be spent.

    Asserted as an exact total rather than an upper bound — under a correct reservation every call
    up to the cap succeeds too, and a guard that refused too many would pass an upper-bound test."""
    import threading

    orch, oc, _tid = _ready(tmp_path)
    token = _token(oc)
    at_once = _DELEGATED_CALLS_MAX + 8
    ready = threading.Barrier(at_once)
    answers: list[str] = []
    lock = threading.Lock()

    def one():
        ready.wait()
        said = _ask(orch, token, alias=OPUS_NAME, prompt="x")
        with lock:
            answers.append(said)

    threads = [threading.Thread(target=one) for _ in range(at_once)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert answers.count("REFUND REQUEST") == _DELEGATED_CALLS_MAX
    assert sum("is the limit for one" in a for a in answers) == at_once - _DELEGATED_CALLS_MAX
