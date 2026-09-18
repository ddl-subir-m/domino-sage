"""#409: a custom tool's fetch comes back, even when the route it posts to does not.

Both custom tools post to a route this process serves, and neither wait was bounded. Nothing has
been seen to hang there — this is a latent wait, not a diagnosed one — but an unbounded wait has a
fixed consequence when it does happen: the tool sends nothing between `called` and its result, so
`_CHAT_TOOL_QUIET_TIMEOUT_S` (240s) ends the TURN in silence and the assistant is never handed a
sentence. Bounded, the TOOL fails instead, the failure vocabulary each file already owns comes back
as the tool's result, and the turn goes on.

The claim here is about the BOUND FIRING, which is why this drives the real `.ts` under node rather
than reading it. A source-text assertion that `signal:` appears is not evidence: it passes against a
signal wired to a clock nothing arms.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from .test_a_shape_only_table_artifact_renders_as_a_receipt import needs_node

ROOT = Path(__file__).resolve().parents[1]

# Short enough that the suite does not wait out a production bound, long enough that a tool which
# answered instantly cannot be mistaken for one that waited. Reached through each file's own env
# var, which is the idiom already there for SAGE_CONTROL_PORT: the production value is the default.
_BOUND_MS = 400

# Well under the production default (210_000), so an UNBOUNDED fetch — the defect — ends this as a
# TimeoutExpired rather than as a suite that appears to hang.
_PATIENCE_S = 30

# The stub is the whole test. It models the route that never answers, and it settles on ONE thing
# only: the tool's own abort. Two ways to get a green that proves nothing, both avoided here —
#   * `async (url, options) => {...}` (the shape the neighbouring harnesses use) answers at once, so
#     the bound is never reached and the assertion below rides on the happy path;
#   * a stub that touches `options.signal` unconditionally THROWS when the signal is missing, and
#     that TypeError lands in the same catch and returns the same readable sentence — an unbounded
#     fetch would pass. So a missing signal is modelled as what it actually is: a hang.
# The interval is what makes the wait a wait. A real fetch holds an open socket, which keeps node's
# event loop alive; this stub holds nothing, and `AbortSignal.timeout` arms an UNREF'd timer, so
# without it the loop drains and node exits on an unsettled await before any bound can fire.
_HARNESS = """
import { readFileSync } from 'node:fs';
const [, file, exported, args] = process.argv;
const source = readFileSync(file);
const mod = await import('data:text/javascript;base64,' + source.toString('base64'));
globalThis.fetch = (_url, options) => new Promise((_resolve, reject) => {
  const alive = setInterval(() => {}, 1000);
  if (!options || !options.signal) return;
  options.signal.addEventListener('abort', () => { clearInterval(alive); reject(options.signal.reason); });
});
const started = Date.now();
const said = await mod[exported].execute(JSON.parse(args));
console.log(JSON.stringify({ said, ms: Date.now() - started }));
"""

# No stub at all: the tool's own `fetch` against the port the environment points it at.
_REAL = """
import { readFileSync } from 'node:fs';
const [, file, exported, args] = process.argv;
const source = readFileSync(file);
const mod = await import('data:text/javascript;base64,' + source.toString('base64'));
const said = await mod[exported].execute(JSON.parse(args));
console.log(JSON.stringify({ said }));
"""

_TOOLS = [
    pytest.param(
        "sage/delegated/tools/delegated_model_call.ts", "default",
        "SAGE_DELEGATED_CALL_TIMEOUT_MS",
        {"token": "t", "alias": "opus", "prompt": "Classify these rows"},
        "Nothing was asked and no answer came back.",
        id="delegated_model_call"),
    pytest.param(
        "sage/liveread/tools/live_read.ts", "table",
        "SAGE_LIVE_READ_TIMEOUT_MS",
        {"token": "t", "source": "DWH", "table": "SALES"},
        "Nothing was put on the person's screen.",
        id="live_read_table"),
    pytest.param(
        "sage/liveread/tools/live_read.ts", "files",
        "SAGE_LIVE_READ_TIMEOUT_MS",
        {"token": "t", "dataset": "transactions"},
        "Nothing was put on the person's screen.",
        id="live_read_files"),
]


@needs_node
@pytest.mark.parametrize("path,exported,env_var,args,fell_through", _TOOLS)
def test_a_route_that_never_answers_comes_back_as_a_sentence_the_model_can_act_on(
        path: str, exported: str, env_var: str, args: dict, fell_through: str):
    out = subprocess.run(
        ["node", "--input-type=module", "-e", _HARNESS,
         str(ROOT / path), exported, json.dumps(args)],
        check=False, capture_output=True, text=True, timeout=_PATIENCE_S,
        env={**os.environ, env_var: str(_BOUND_MS)})

    assert out.returncode == 0, out.stderr
    result = json.loads(out.stdout.strip().splitlines()[-1])

    assert fell_through in result["said"], (
        "the bound has to land in the catch the file already has, so the assistant reads the "
        f"sentence that tells it the work did not happen: {result['said']!r}")
    assert f"did not answer within {_BOUND_MS / 1000}s" in result["said"], (
        "and it has to say the route was reached and went quiet, naming the bound that fired. "
        "'Could not be reached' is the OTHER condition and is false of this one — the route "
        f"accepted the connection: {result['said']!r}")
    assert "could not be reached" not in result["said"], (
        f"and it must not also claim the route was unreachable: {result['said']!r}")
    assert result["ms"] >= _BOUND_MS // 2, (
        f"a tool that answered in {result['ms']}ms did not wait for anything, so nothing about a "
        "timeout was exercised")


@needs_node
@pytest.mark.parametrize("path,exported,env_var,args,fell_through", _TOOLS)
def test_a_route_that_is_really_unreachable_still_says_so(
        path: str, exported: str, env_var: str, args: dict, fell_through: str):
    """The other arm of the same catch, and the reason it is a separate test: one plant cannot show
    that a branch PICKS. A tool that answered "did not answer within 210s" unconditionally would
    pass the test above, and it would be lying about every connection failure there is.

    No fetch stub here. The tool builds its route from SAGE_CONTROL_PORT, so pointing that at a port
    nothing listens on makes the REAL fetch fail to connect — the production condition, not a
    modelled one.

    The bound is deliberately left at its production default rather than lowered to `_BOUND_MS`.
    Lowering it would put the two arms in a RACE: on any host where connecting to 127.0.0.1:1 does
    not fail within the bound — a DROP firewall rule rather than a refusal, or a loaded box — the
    timeout arm wins, both assertions below flip, and the red reads like a regression in the branch
    under review. The short bound buys nothing here; a refused connection is already immediate."""
    out = subprocess.run(
        ["node", "--input-type=module", "-e", _REAL,
         str(ROOT / path), exported, json.dumps(args)],
        check=False, capture_output=True, text=True, timeout=_PATIENCE_S,
        env={**os.environ, env_var: "", "SAGE_CONTROL_PORT": "1"})

    assert out.returncode == 0, out.stderr
    result = json.loads(out.stdout.strip().splitlines()[-1])

    assert fell_through in result["said"], result["said"]
    assert "could not be reached" in result["said"], (
        "a connection that was refused is exactly what 'could not be reached' is for, and the "
        f"timeout arm must not swallow it: {result['said']!r}")
    assert "did not answer within" not in result["said"], (
        f"and it must not be reported as a timeout, which it was not: {result['said']!r}")


# A stub that answers at once, which is the WRONG shape for the test above and the right one here:
# the question is whether the module loaded and reached the route at all.
_LOADS = """
import { readFileSync } from 'node:fs';
const [, file, exported, args] = process.argv;
const source = readFileSync(file);
const mod = await import('data:text/javascript;base64,' + source.toString('base64'));
let seen = null;
globalThis.fetch = async (_url, options) => {
  seen = options.signal;
  return { ok: true, json: async () => ({ result: { content: [{ text: 'ANSWERED' }] } }) };
};
const said = await mod[exported].execute(JSON.parse(args));
console.log(JSON.stringify({ said, bounded: seen instanceof AbortSignal, aborted: seen && seen.aborted }));
"""


# A body that never finishes arriving. The headers are already in — `ok` is true and the tool is
# past the first catch — so the abort lands in the SECOND one, which is a different sentence.
_SLOW_BODY = """
import { readFileSync } from 'node:fs';
const [, file, exported, args] = process.argv;
const source = readFileSync(file);
const mod = await import('data:text/javascript;base64,' + source.toString('base64'));
globalThis.fetch = async (_url, options) => ({
  ok: true,
  status: 200,
  json: () => new Promise((_resolve, reject) => {
    const alive = setInterval(() => {}, 1000);
    options.signal.addEventListener('abort', () => { clearInterval(alive); reject(options.signal.reason) });
  }),
});
const said = await mod[exported].execute(JSON.parse(args));
console.log(JSON.stringify({ said }));
"""


@needs_node
@pytest.mark.parametrize("path,exported,env_var,args,fell_through", _TOOLS)
def test_a_body_that_stops_arriving_is_a_timeout_and_not_a_malformed_reply(
        path: str, exported: str, env_var: str, args: dict, fell_through: str):
    """The bound covers the body, not just the headers, so there are TWO catches an abort can land
    in. The second one's sentence says the reply was unreadable — which blames the payload for the
    clock, and points the model at a route that was in fact answering perfectly well, just slowly.
    The condition is reachable in production: `analyze_text` streams its result back through here."""
    out = subprocess.run(
        ["node", "--input-type=module", "-e", _SLOW_BODY,
         str(ROOT / path), exported, json.dumps(args)],
        check=False, capture_output=True, text=True, timeout=_PATIENCE_S,
        env={**os.environ, env_var: str(_BOUND_MS)})

    assert out.returncode == 0, out.stderr
    result = json.loads(out.stdout.strip().splitlines()[-1])

    assert fell_through in result["said"], result["said"]
    assert f"did not answer within {_BOUND_MS / 1000}s" in result["said"], (
        f"a body that stopped arriving is the clock running out, not a bad payload: {result['said']!r}")
    assert "unreadable" not in result["said"], (
        f"and saying 'unreadable' would blame the reply for the timeout: {result['said']!r}")


@needs_node
@pytest.mark.parametrize("bad", ["not-a-number", "", "0", "-1", "0.5", "5000000000"])
@pytest.mark.parametrize("path,exported,env_var,args,fell_through", _TOOLS)
def test_a_timeout_that_cannot_be_read_falls_back_instead_of_taking_the_tool_with_it(
        path: str, exported: str, env_var: str, args: dict, fell_through: str, bad: str):
    """The bound is read from the environment, and an environment is not a promise. Every value
    `AbortSignal.timeout` rejects throws a RangeError — inside `execute`, so the throw is caught and
    the tool stays present and readable. That is the trap: it looks survivable. What it actually is
    is a tool that returns the SAME refusal to every call it is ever given, having never once
    reached the route — a capability that is gone while every surface reports it installed.

    The list is the point. A truthiness fallback (`Number(...) || default`) covers the first three
    and passes `-1`, `0.5` and `5e9` straight through to the throw, so a test that planted only
    "not-a-number" would go green over three live ways to lose the tool. `5000000000` is the worst
    of them: node does not refuse it, it warns and silently sets the duration to 1ms, which is a
    bound that fires on every call before the route can answer."""
    out = subprocess.run(
        ["node", "--input-type=module", "-e", _LOADS,
         str(ROOT / path), exported, json.dumps(args)],
        check=False, capture_output=True, text=True, timeout=_PATIENCE_S,
        env={**os.environ, env_var: bad})

    assert out.returncode == 0, (
        f"the module did not load with {env_var}=not-a-number, so the tool is absent: {out.stderr}")
    result = json.loads(out.stdout.strip().splitlines()[-1])

    assert result["said"] == "ANSWERED", "and the call still reaches the route"
    assert result["bounded"], "still bounded — falling back must not mean falling back to no signal"
    assert result["aborted"] is False, "and the fallback is a real interval, not an already-fired one"
