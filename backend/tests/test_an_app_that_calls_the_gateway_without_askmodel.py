"""An app that calls the LLM Gateway around `askModel` loses more than the record (#94).

The premise this was filed on was wrong, and the ticket says so. Through `template/react-vite/src/
sageLlm.ts` an undeclared Alias does NOT work: `pick` returns null and the call throws a sentence
written for the viewer. So the declaration already gates a call made through the helper.

What survives is the call that goes around it. An app that declares one Alias has a live gateway URL
in `src/sageLlm.config.ts`, and nothing stops a raw `fetch` at it. What that loses is not mainly the
record: it loses the `X-LLM-Tag-sage-*` cost tags, which are the only thing attributing this app's
spend to Sage; the error messages written for the viewer; session-expiry detection; and streaming.
And the nastiest shape is the mirror — `/api/llm` is Sage's PREVIEW proxy, so a call copied from
what the agent watched working is a call that works until the app is published.

Three disciplines hold these tests together:

NOTHING GATES. No publish is refused and no build fails. The nudge is `_detect_leaks`'s: bounded,
and the turn completes either way. The last two tests in the loop group are that claim.

THE SCAN IS LOCAL AND PURE. `gateway_bypass` makes no gateway call — the declared Aliases come off
`.sage/bindings.json`, which the caller has already read — so every judgement below is testable
without a workspace.

ONLY A PERSON CAN BIND AN ALIAS (ADR-0010). Rewriting a raw call to `askModel` fixes it for a
declared Alias and BREAKS it for one the app never declared, which is the honest outcome and the
one half the agent cannot finish alone. That is the only thing said to the creator, and it is said
as a sentence, never as a prompt and never as a Binding recorded on the agent's behalf.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from sage.feedback.runner import FeedbackReport
from sage.orchestrator.service import Orchestrator
from sage.resources.gateway_bypass import raw_gateway_calls, unbound_alias_notice
from sage.resources.pinned_model import CONFIG_PATH, HELPER_PATH, render_config
from sage.resources.provider import FakeResourceProvider, LlmAlias
from sage.router.models import Mode, ModelCatalog

from .fake_opencode import FakeOpenCode, Turn

BASE = "https://apps.example.com/apps/llm_gateway/v1"
OWNED = frozenset({HELPER_PATH, CONFIG_PATH})

ALIASES = [
    LlmAlias("id-sonnet", "sonnet", "Claude Sonnet 4.6", None, ["chat"], {"input": 3.0}),
    LlmAlias("id-qwen", "qwen-2-5", "Qwen 2.5 (Domino-hosted)", None, ["chat"], {}),
]

REPO_TEMPLATE = Path(__file__).resolve().parents[2] / "template" / "react-vite"

# What an agent writes when it goes around the helper: the pinned base, an OpenAI-shaped body naming
# a model, and no import of `askModel`.
RAW_AT_BASE = f'''
export async function ask(q: string) {{
  const res = await fetch("{BASE}/chat/completions", {{
    method: "POST",
    credentials: "include",
    body: JSON.stringify({{ model: "sonnet", messages: [{{ role: "user", content: q }}] }}),
  }});
  return (await res.json()).choices[0].message.content;
}}
'''

# The mirror, and the shape nothing else reports: copied from what the agent watched answering in
# the preview, correct until the app is published.
RAW_AT_PROXY = RAW_AT_BASE.replace(f"{BASE}/chat/completions", "/api/llm/chat/completions")

# A host the app worked out for itself rather than the base Sage pinned.
RAW_AT_PATH = RAW_AT_BASE.replace(f"{BASE}/chat/completions", "/v1/chat/completions")

# The base carrying the finding on its own. A raw chat call at the pinned base contains
# `/v1/chat/completions` too — the base ends in `/v1` — so it is matched by either shape and proves
# neither. This one is the availability check written by hand, and only the base can find it.
RAW_AT_BASE_ONLY = f'const models = await fetch("{BASE}/models", {{ credentials: "include" }});\n'

USES_HELPER = '''
import { askModel } from "./sageLlm";

export async function ask(q: string) {
  return askModel([{ role: "user", content: q }], { alias: "sonnet" });
}
'''


# ---- the scan, which is pure -----------------------------------------------------------------


def _found(text: str, *, declared: list[str] | None = None, base: str | None = BASE,
           rel: str = "src/Chat.tsx") -> list[tuple[str, list[str]]]:
    return raw_gateway_calls([(rel, text)], declared or ["sonnet"], base, OWNED)


def test_a_fetch_at_the_pinned_gateway_base_is_flagged():
    assert _found(RAW_AT_BASE) == [("src/Chat.tsx", [])]


def test_a_fetch_at_the_previews_proxy_path_is_flagged():
    # The variant nothing else reports. It answers correctly through the whole build — the proxy is
    # Sage's own — and is gone the moment the app is served from Domino instead.
    assert _found(RAW_AT_PROXY) == [("src/Chat.tsx", [])]


def test_a_bare_completions_path_is_flagged():
    assert _found(RAW_AT_PATH) == [("src/Chat.tsx", [])]


def test_the_pinned_base_is_matched_on_its_own_not_only_where_a_known_path_follows_it():
    # The base is the shape that finds a call to any of the gateway's other routes — here the
    # availability check `checkModel` exists to make — and a scan that only ever matched
    # `/v1/chat/completions` would report this app as clean.
    assert _found(RAW_AT_BASE_ONLY) == [("src/Chat.tsx", [])]


def test_the_helper_that_holds_all_three_shapes_by_definition_is_not_flagged():
    # `src/sageLlm.ts` IS the legitimate caller: it carries the base, the preview path and the
    # completions path on purpose. Flagging it would be the scan reporting the thing it protects.
    helper = (REPO_TEMPLATE / HELPER_PATH).read_text()
    assert raw_gateway_calls([(HELPER_PATH, helper)], ["sonnet"], BASE, OWNED) == []


def test_an_app_that_calls_askmodel_is_flagged_nowhere():
    # The whole cost of this feature for an app that did the right thing.
    assert _found(USES_HELPER) == []


def test_an_app_with_no_model_has_no_pinned_base_to_match_on():
    # `base` is None for an app that declares no Alias — `render_config` nulls it — and an absent
    # base must match nothing rather than everything.
    assert raw_gateway_calls([("src/Chat.tsx", "const url = 'https://example.com/thing';")],
                             [], None, OWNED) == []


def test_a_non_code_file_is_never_read_for_a_call():
    # `_scan_app_sources` hands over data files with `text` None so a copied CSV is still listed by
    # basename. There is no call in a file we did not read.
    assert raw_gateway_calls([("public/notes.csv", None)], ["sonnet"], BASE, OWNED) == []


# ---- what the findings say to a person ---------------------------------------------------------


def test_a_declared_alias_fetched_raw_names_nobody():
    # The agent can finish this one alone: `askModel("sonnet")` works, because the app declares it.
    calls = _found(RAW_AT_BASE, declared=["sonnet"])
    assert calls == [("src/Chat.tsx", [])]
    assert unbound_alias_notice(calls) is None


def test_an_undeclared_alias_is_named_to_the_creator_with_the_act_only_they_can_do():
    calls = _found(RAW_AT_BASE, declared=["qwen-2-5"])
    assert calls == [("src/Chat.tsx", ["sonnet"])]
    notice = unbound_alias_notice(calls)
    assert "sonnet" in notice and "src/Chat.tsx" in notice
    # Binding is a person's act (ADR-0010), so the sentence has to end somewhere they can go.
    assert "Resources panel" in notice
    # And it says what the fix does to this app, which is break it until they go there.
    assert "askModel" in notice


def test_a_field_that_merely_ends_in_model_is_not_read_as_one():
    # A neighbouring field would otherwise be handed to the creator as a model to go and bind.
    raw = RAW_AT_BASE.replace('model: "sonnet"', 'submodel: "not-an-alias", model: "sonnet"')
    assert _found(raw, declared=["sonnet"]) == [("src/Chat.tsx", [])]


def test_a_model_named_far_from_the_call_is_not_that_calls_model():
    # The real errand: an app whose raw call asks for a model it HAS, and whose chart config three
    # hundred lines away names a fit. Reading the file as a whole would send the creator to the
    # Resources panel to look for an LLM Alias called `linear-fit`, which nobody will ever register.
    raw = RAW_AT_BASE + "\n" + ("// padding\n" * 200) + 'const chart = { model: "linear-fit" };\n'
    assert _found(raw, declared=["sonnet"]) == [("src/Chat.tsx", [])]


def test_a_model_named_just_above_its_own_fetch_is_still_that_calls_model():
    # A request body is as often assembled above the `fetch` as written inside it, so the window
    # reaches backwards too — and this one IS the call's model.
    raw = ('const body = { model: "mimo-v2.5", messages: [] };\n'
           f'const res = await fetch("{BASE}/chat/completions", '
           '{ method: "POST", body: JSON.stringify(body) });\n')
    assert _found(raw, declared=["sonnet"]) == [("src/Chat.tsx", ["mimo-v2.5"])]


def test_two_undeclared_models_are_named_together_rather_than_once_each():
    calls = [("src/Chat.tsx", ["mimo-v2.5"]), ("src/Report.tsx", ["gpt-5.4"])]
    notice = unbound_alias_notice(calls)
    assert "gpt-5.4 and mimo-v2.5" in notice
    assert "are models" in notice and "Aliases" in notice


def test_a_file_flagged_with_no_undeclared_model_is_left_out_of_the_sentence():
    # The sentence is about models nobody bound. A file whose raw call names a model the app DOES
    # have is the agent's job and has nothing in it for a person to read.
    calls = [("src/Chat.tsx", []), ("src/Report.tsx", ["mimo-v2.5"])]
    assert "src/Chat.tsx" not in unbound_alias_notice(calls)


# ---- through the build loop, which is where it reaches anybody ----------------------------------


class OkFeedback:
    """Typecheck always passes. A type error would only add a second reason for a turn to end and
    blur which one a failing assertion meant."""

    def check(self, path: Path) -> FeedbackReport:
        return FeedbackReport(ok=True, errors=[], raw="")


class ScriptedGateway:
    """One scripted word for the scope classifier, the only caller on this path."""

    def route(self, request, labels):
        body = json.dumps({"choices": [{"delta": {"content": "BUILD"}}]})
        yield f"data: {body}\n\ndata: [DONE]\n\n".encode()


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    """The two waits a scripted turn can only spend, never use — the poll sleep, and the four-second
    wait for a preview that `start_preview=False` never started."""
    import time

    monkeypatch.setattr(time, "sleep", lambda *_: None)
    monkeypatch.setattr(Orchestrator, "_await_runtime_error", lambda *a, **k: None)


def _template(tmp: Path) -> Path:
    """The shipped helper, verbatim. A stub would let the `_SAGE_OWNED_SOURCES` skip pass on a file
    that holds none of the three shapes, which is the opposite of the test."""
    t = tmp / "template"
    (t / "src").mkdir(parents=True, exist_ok=True)
    (t / "src" / "App.tsx").write_text("export default function App() { return null }\n")
    (t / HELPER_PATH).write_text((REPO_TEMPLATE / HELPER_PATH).read_text())
    (t / CONFIG_PATH).write_text(render_config([], None, None))
    (t / "package.json").write_text("{}")
    (t / "app.sh").write_text("#!/bin/bash\nexec npx vite preview\n")
    (t / "AGENTS.md").write_text("# Template rules\n")
    return t


def _orch(tmp: Path, turns: list[Turn]) -> tuple[Orchestrator, FakeOpenCode]:
    ws = tmp / "mnt" / "code"
    oc = FakeOpenCode(ws, turns)
    orch = Orchestrator(
        workspace_dir=ws,
        template=_template(tmp),
        gateway=ScriptedGateway(),
        catalog=ModelCatalog(sovereign_plan="s", sovereign_implement="s", sovereign_ask="s",
                             plan="p", implement="i", ask="a"),
        project_id="Sage",
        feedback=OkFeedback(),
        opencode_client=oc,
        resources=FakeResourceProvider(list(ALIASES)),
        browser_gateway_base=BASE,
        cost_project_label="my-app",
    )
    orch.project(start_preview=False)
    return orch, oc


def _build(orch: Orchestrator) -> list[dict]:
    """The real first-build flow: the gate proposes, the approval builds. The scripted turns are
    consumed in order, so turn 1 is the plan and turn 2 is the build under test."""
    orch.project(start_preview=False).control.set_mode(Mode.AUTO)
    list(orch.build_stream("build me a chat box"))
    return list(orch.approve_stream())


def _plan_then(writes: dict[str, str], repeats: int = 0) -> list[Turn]:
    """A plan turn, a build turn that writes `writes`, then `repeats` turns that touch a scratch
    file and leave the offending one alone — an agent handed the nudge and failing to act on it.

    Each retry has to write SOMETHING: a turn that changes no file is a turn that never reaches the
    end-of-turn scans at all, because the loop takes the "you planned but wrote no code" branch
    above them instead.
    """
    return ([Turn(text="A chat box.\n\n## Plan\n1. **Box** — Ask the model.\n"),
             Turn(text="Built it.", writes=writes)]
            + [Turn(text="Trying again.", writes={f"src/note{i}.ts": f"export const n = {i};\n"})
               for i in range(repeats)])


def _of(events: list[dict], kind: str) -> list[dict]:
    return [e for e in events if e.get("type") == kind]


def test_a_raw_gateway_call_nudges_the_agent_to_rewrite_it(tmp_path: Path):
    orch, oc = _orch(tmp_path, _plan_then({"src/Chat.tsx": RAW_AT_BASE}))
    orch.bind_llm_alias("id-sonnet")

    events = _build(orch)

    assert [e["file"] for e in _of(events, "gateway-call")] == ["src/Chat.tsx"]
    assert any("askModel" in e.get("reason", "") for e in _of(events, "iterate"))
    # The nudge is what the agent was actually handed, and it names the file and the helper.
    nudge = next(p["text"] for p in oc.prompts if "askModel" in p["text"])
    assert "src/Chat.tsx" in nudge
    # And the shape no test the agent could run would ever catch.
    assert "/api/llm" in nudge
    # It carries the fix itself rather than pointing at a heading in AGENTS.md: `agents_block`
    # writes no model section for an app with no Alias, and titles it in the plural for an app with
    # several, so a quoted heading is wrong in two of the three cases.
    assert "AGENTS.md" not in nudge
    assert "src/sageLlm.ts" in nudge


def test_an_app_that_uses_the_helper_is_flagged_nowhere(tmp_path: Path):
    # Including `src/sageLlm.ts` itself, which is sitting in this workspace holding all three shapes.
    orch, _oc = _orch(tmp_path, _plan_then({"src/Chat.tsx": USES_HELPER}))
    orch.bind_llm_alias("id-sonnet")

    events = _build(orch)

    assert _of(events, "gateway-call") == []
    assert _of(events, "gateway-alias-unbound") == []


def test_a_declared_alias_nudges_the_agent_and_says_nothing_to_the_creator(tmp_path: Path):
    # There is no decision for a person here: the app already has this model, so rewriting the call
    # to `askModel` is the whole fix, and a sentence would be interruption with nothing to act on.
    orch, _oc = _orch(tmp_path, _plan_then({"src/Chat.tsx": RAW_AT_BASE}))
    orch.bind_llm_alias("id-sonnet")

    events = _build(orch)

    assert _of(events, "gateway-call")
    assert _of(events, "gateway-alias-unbound") == []


def test_an_undeclared_alias_says_so_to_the_creator_too(tmp_path: Path):
    # The app declares qwen-2-5 and the raw call asks for sonnet. Rewriting it to `askModel` makes
    # the app honestly broken-until-bound rather than quietly working off the record, and only a
    # person can bind the Alias that would fix it.
    orch, _oc = _orch(tmp_path, _plan_then({"src/Chat.tsx": RAW_AT_BASE}))
    orch.bind_llm_alias("id-qwen")

    events = _build(orch)

    said = _of(events, "gateway-alias-unbound")
    assert len(said) == 1
    assert "sonnet" in said[0]["message"] and "Resources panel" in said[0]["message"]


def test_the_nudge_is_bounded_and_the_turn_finishes_anyway(tmp_path: Path):
    # An agent that will not fix it must not loop, and must not fail the build either: this is
    # `_detect_leaks`'s bargain, not a gate.
    orch, _oc = _orch(tmp_path, _plan_then({"src/Chat.tsx": RAW_AT_BASE}, repeats=4))
    orch.bind_llm_alias("id-sonnet")

    events = _build(orch)

    assert len(_of(events, "gateway-call")) == 2      # MAX_GATEWAY_FIXES, then it stops asking
    done = _of(events, "done")[-1]
    assert done["ok"] is True
    # And the offending file is still there — nothing reverted it, nothing refused over it.
    assert (orch.project().workspace.path / "src" / "Chat.tsx").read_text() == RAW_AT_BASE


def test_a_leak_and_a_raw_call_in_one_turn_are_nudged_one_at_a_time_leaks_first(tmp_path: Path):
    # Two defects want two nudges, and the `continue` means only one can fire per iteration. The
    # leak block runs first and keeps the iteration until it gives up, so the Gateway call is caught
    # on a later pass rather than folded into one message the agent has to disentangle.
    orch, _oc = _orch(tmp_path, _plan_then(
        {"src/sales.csv": "a,b\n1,2\n", "src/Chat.tsx": RAW_AT_BASE}, repeats=4))
    orch.bind_llm_alias("id-sonnet")
    orch.upload_file("sales.csv", b"a,b\n1,2\n")

    events = _build(orch)

    order = [e["type"] for e in events if e["type"] in ("data-leak", "gateway-call")]
    assert "data-leak" in order and "gateway-call" in order
    assert order == sorted(order, key=lambda t: t != "data-leak")   # every leak before every call


def test_both_events_survive_a_reload(tmp_path: Path):
    # The transcript is what a person comes back to. An event the live stream carries and
    # `.sage/history.jsonl` does not is one the creator sees only if they happened to be watching
    # the turn — and the sentence about an unbound Alias is the half that is still true tomorrow.
    orch, _oc = _orch(tmp_path, _plan_then({"src/Chat.tsx": RAW_AT_BASE}))
    orch.bind_llm_alias("id-qwen")

    _build(orch)

    replayed = {e["type"] for e in orch.history()}
    assert "gateway-call" in replayed and "gateway-alias-unbound" in replayed


# ---- and what the creator actually sees ---------------------------------------------------------


_HARNESS = Path(__file__).resolve().parent / "js" / "build_events_harness.mjs"

needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is not on PATH (it is in the Sage image)"
)


def _drawn(history: list[dict]) -> dict:
    out = subprocess.run(["node", str(_HARNESS)], input=json.dumps({"history": history}),
                         check=False, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


@needs_node
def test_the_sentence_for_the_creator_is_drawn():
    # `mentions-unresolved`'s precedent: an `ev.message` with its own branch in the chain. Without
    # the branch the row persists and nobody ever reads it.
    drawn = _drawn([{"type": "user", "text": "add a chat box"},
                    {"type": "gateway-alias-unbound", "message": "src/Chat.tsx asks for sonnet."}])
    assert "src/Chat.tsx asks for sonnet." in drawn["values"]


@needs_node
def test_the_flagged_file_is_drawn():
    drawn = _drawn([{"type": "user", "text": "add a chat box"},
                    {"type": "gateway-call", "file": "src/Chat.tsx"}])
    assert any("src/Chat.tsx" in v and "askModel" in v for v in drawn["values"])


@needs_node
def test_a_data_leak_is_drawn_too():
    # It persisted from the day it shipped and had no branch, so the defect it reports — attached
    # data copied into src/ — reached the transcript and nobody ever saw it. Fixed here rather than
    # copied: shipping a second invisible event beside the first is the thing this ticket is about.
    drawn = _drawn([{"type": "user", "text": "chart the sales"},
                    {"type": "data-leak", "file": "sales.csv", "where": ["src/sales.csv"]}])
    assert any("sales.csv" in v and "src/sales.csv" in v for v in drawn["values"])
