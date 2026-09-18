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

#416 added the two things this file could not say about itself. Its subjects were a hand-written
list of three ids over two files, and a third tool — `artifact_write.ts` — fetched with no bound at
all while this file passed beside it, because a list cannot know it is short: the census at the
bottom is what notices the fourth one. And its guard had a ceiling taken from the RUNTIME's range
rather than from the job, which left every value between the turn's quiet window and 2^31-1 — 23
days, say — accepted as a bound. Every plant here pushed the same way, so nine red cases certified a
range that was open at the other end.
"""
from __future__ import annotations

import json
import os
import re
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

# `reply` is what a route that ANSWERS hands back, and `answered` is what the tool then says. They
# are per-tool because the reply shape is: the two MCP-shaped tools unwrap `result.content[0].text`,
# and `artifact_write` posts to a plain route and reads `path` off the top level. A single shared
# stub would hand `artifact_write` a body with no `path` in it, and `Artifact written: undefined` is
# a green that proves the tool never read the reply at all.
_MCP_REPLY = json.dumps({"result": {"content": [{"text": "ANSWERED"}]}})

_TOOLS = [
    pytest.param(
        "sage/delegated/tools/delegated_model_call.ts", "default",
        "SAGE_DELEGATED_CALL_TIMEOUT_MS",
        {"token": "t", "alias": "opus", "prompt": "Classify these rows"},
        "Nothing was asked and no answer came back.",
        _MCP_REPLY, "ANSWERED",
        id="delegated_model_call"),
    pytest.param(
        "sage/liveread/tools/live_read.ts", "table",
        "SAGE_LIVE_READ_TIMEOUT_MS",
        {"token": "t", "source": "DWH", "table": "SALES"},
        "Nothing was put on the person's screen.",
        _MCP_REPLY, "ANSWERED",
        id="live_read_table"),
    pytest.param(
        "sage/liveread/tools/live_read.ts", "files",
        "SAGE_LIVE_READ_TIMEOUT_MS",
        {"token": "t", "dataset": "transactions"},
        "Nothing was put on the person's screen.",
        _MCP_REPLY, "ANSWERED",
        id="live_read_files"),
    # ADR-0058's tool, and it is here because the census below refused to let it ship without a
    # subject — the list did not know it was short, and the thing built for exactly that noticed.
    # Same bound and same sentence as its two siblings because it shares their `call`, which is the
    # claim being checked rather than an assumption worth inheriting: a fourth export added to
    # `live_read.ts` gets the bound only if `call` is what it goes through.
    pytest.param(
        "sage/liveread/tools/live_read.ts", "query",
        "SAGE_LIVE_READ_TIMEOUT_MS",
        {"token": "t", "source": "DWH", "sql": "SELECT COUNT(*) AS N FROM DWH.MARTS.EVENTS"},
        "Nothing was put on the person's screen.",
        _MCP_REPLY, "ANSWERED",
        id="live_read_query"),
    # #416: the third tool. It was not absent from this list because anyone judged it exempt — the
    # list is a list, and it cannot know it is short. `test_every_tool_that_fetches_is_bounded`
    # below is what notices the fourth one.
    pytest.param(
        "sage/liveread/tools/artifact_write.ts", "default",
        "SAGE_ARTIFACT_WRITE_TIMEOUT_MS",
        {"thread_id": "thr_t", "path": "examples/thr_t/x.table.json",
         "content": "{}", "encoding": "utf8"},
        "No artifact was confirmed.",
        # The reply's path is deliberately NOT the path in `args`. When they matched, a tool that
        # echoed its own argument and never read the reply at all returned the expected sentence
        # and the assertion held — the tautology the per-tool reply was added to prevent, still
        # there in a quieter form. Only a name the caller never sent can tell the two apart.
        json.dumps({"path": "examples/thr_t/CONFIRMED.table.json"}),
        "Artifact written: examples/thr_t/CONFIRMED.table.json",
        id="artifact_write"),
]


@needs_node
@pytest.mark.parametrize("path,exported,env_var,args,fell_through,reply,answered", _TOOLS)
def test_a_route_that_never_answers_comes_back_as_a_sentence_the_model_can_act_on(
        path: str, exported: str, env_var: str, args: dict, fell_through: str,
        reply: str, answered: str):
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
@pytest.mark.parametrize("path,exported,env_var,args,fell_through,reply,answered", _TOOLS)
def test_a_route_that_is_really_unreachable_still_says_so(
        path: str, exported: str, env_var: str, args: dict, fell_through: str,
        reply: str, answered: str):
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
#
# It also records the number the tool hands `AbortSignal.timeout`, which is the only way to see the
# CEILING from outside. A signal armed for 23 days is an `AbortSignal` like any other and reports
# `aborted === false` like any other, so every assertion below it would stay green over a bound that
# can never fire. The stub goes in before the import so that a tool which ever arms at module scope
# is recorded too.
_LOADS = """
import { readFileSync } from 'node:fs';
const [, file, exported, args, reply] = process.argv;
const source = readFileSync(file);
const real = AbortSignal.timeout.bind(AbortSignal);
let armed = null;
let arms = 0;
AbortSignal.timeout = (value) => { arms += 1; armed = value; return real(value); };
const mod = await import('data:text/javascript;base64,' + source.toString('base64'));
// Everything armed so far was armed while the MODULE loaded, not while a call was made. The
// counter is read and then reset, so `arms` below counts only what `execute` itself arms — a tool
// holding one module-scope signal reports atImport 1 and arms 0, and a counter that was never
// reset would have called that a perfectly ordinary single arm.
const atImport = arms;
arms = 0;
armed = null;
let seen = null;
globalThis.fetch = async (_url, options) => {
  seen = options.signal;
  return { ok: true, status: 200, json: async () => JSON.parse(reply) };
};
const said = await mod[exported].execute(JSON.parse(args));
console.log(JSON.stringify({ said, armed, arms, atImport, bounded: seen instanceof AbortSignal, aborted: seen && seen.aborted }));
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
@pytest.mark.parametrize("path,exported,env_var,args,fell_through,reply,answered", _TOOLS)
def test_a_body_that_stops_arriving_is_a_timeout_and_not_a_malformed_reply(
        path: str, exported: str, env_var: str, args: dict, fell_through: str,
        reply: str, answered: str):
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
@pytest.mark.parametrize("path,exported,env_var,args,fell_through,reply,answered", _TOOLS)
def test_a_timeout_that_cannot_be_read_falls_back_instead_of_taking_the_tool_with_it(
        path: str, exported: str, env_var: str, args: dict, fell_through: str,
        reply: str, answered: str, bad: str):
    """The bound is read from the environment, and an environment is not a promise. Every value
    `AbortSignal.timeout` rejects throws a RangeError — inside `execute`, so the throw is caught and
    the tool stays present and readable. That is the trap: it looks survivable. What it actually is
    is a tool that returns the SAME refusal to every call it is ever given, having never once
    reached the route — a capability that is gone while every surface reports it installed.

    The list is the point. A truthiness fallback (`Number(...) || default`) covers the first three
    and passes `-1`, `0.5` and `5e9` straight through to the throw, so a test that planted only
    "not-a-number" would go green over three live ways to lose the tool. `5000000000` is the worst
    of them: node does not refuse it, it warns and silently sets the duration to 1ms, which is a
    bound that fires on every call before the route can answer.

    These are the values that are invalid or TOO SHORT. Every one of them pushes the same way, and
    a range has two ends: `test_the_bound_a_tool_arms_can_still_fire_inside_the_turn` below is the
    other one, and it is a separate test because a bound that is merely too LONG fails none of the
    assertions here — it loads, it answers, it is an `AbortSignal`, and it reports `aborted` false
    exactly like a good one."""
    out = subprocess.run(
        ["node", "--input-type=module", "-e", _LOADS,
         str(ROOT / path), exported, json.dumps(args), reply],
        check=False, capture_output=True, text=True, timeout=_PATIENCE_S,
        env={**os.environ, env_var: bad})

    assert out.returncode == 0, (
        f"the module did not load with {env_var}={bad!r}, so the tool is absent: {out.stderr}")
    result = json.loads(out.stdout.strip().splitlines()[-1])

    assert result["said"] == answered, "and the call still reaches the route"
    assert result["bounded"], "still bounded — falling back must not mean falling back to no signal"
    assert result["aborted"] is False, "and the fallback is a real interval, not an already-fired one"


# The turn's own quiet window, and the reason the ceiling is a product number rather than a runtime
# one. `_CHAT_TOOL_QUIET_TIMEOUT_S` ends the TURN when a tool goes quiet, so a bound at or past it
# cannot fire while there is still a turn to hand the sentence to — it only moves the silence from
# the tool to the turn, which is the exact failure this file exists to prevent. The census below
# reads the same number out of `service.py` and refuses to let the two drift apart.
_QUIET_WINDOW_MS = 240_000

# The fallback every tool takes when the environment gives it nothing it can use.
_DEFAULT_MS = 210_000


@needs_node
@pytest.mark.parametrize("configured,expected_ms", [
    # Honoured: inside the window.
    (str(_BOUND_MS), _BOUND_MS),
    # 239_999 is honoured, and this row records what the guard DOES rather than endorsing it. A
    # bound one millisecond under the window leaves one millisecond to get the sentence back, which
    # is as useless as 240_000 — the guard admits it, and the comment above each `QUIET_WINDOW_MS`
    # says so and says why no headroom constant was invented to close it. Read this row as the
    # residual written down, not as a bound anyone should set.
    ("239999", 239_999),
    # Refused, and this is the end the shipped guard was open at. `240000` is the window itself, so
    # a bound there races the clock that kills the turn and can never win it outright.
    ("240000", _DEFAULT_MS),
    ("240001", _DEFAULT_MS),
    # 23 days. The guard #409 shipped accepted this — it is a valid `AbortSignal.timeout` argument
    # on both runtimes, an integer, positive, and under 2^31-1 — and a tool holding it is exactly as
    # unbounded as one holding no signal at all. This row is the one that reds if the ceiling ever
    # goes back to being the runtime's number.
    ("2000000000", _DEFAULT_MS),
])
@pytest.mark.parametrize("path,exported,env_var,args,fell_through,reply,answered", _TOOLS)
def test_the_bound_a_tool_arms_can_still_fire_inside_the_turn(
        path: str, exported: str, env_var: str, args: dict, fell_through: str,
        reply: str, answered: str, configured: str, expected_ms: int):
    """The upper end of the range, which no plant in this file used to push against.

    The ceiling cannot come from `AbortSignal.timeout`'s own range, and that is not a tidiness
    argument. Measured 2026-09-18, node v22.22.3 against bun 1.3.11: node silently sets the duration
    to 1ms above 2^31-1 and throws only above 2^32-1, while bun arms every value either way,
    including the one node refuses outright. Which runtime runs these tools is not settled here —
    `driver/server.py` launches OpenCode through `npx`, and OpenCode ships bun-compiled binaries —
    so a ceiling read off the runtime is a different number depending on a fact nobody established.
    240_000 is the same number on both, and it is the number the job actually imposes.

    This asserts the millisecond count handed to `AbortSignal.timeout`, not that a signal exists,
    because a signal armed for 23 days is an `AbortSignal` and reports `aborted === false` just like
    a working one. Every assertion in the test above stays green over it."""
    out = subprocess.run(
        ["node", "--input-type=module", "-e", _LOADS,
         str(ROOT / path), exported, json.dumps(args), reply],
        check=False, capture_output=True, text=True, timeout=_PATIENCE_S,
        env={**os.environ, env_var: configured})

    assert out.returncode == 0, out.stderr
    result = json.loads(out.stdout.strip().splitlines()[-1])

    assert result["armed"] == expected_ms, (
        f"{env_var}={configured!r} should arm {expected_ms}ms, not {result['armed']}ms")
    # One arm per CALL, which is a different claim from "a bound exists" and the one that catches
    # the worst shape a signal can take: `const SIGNAL = AbortSignal.timeout(MS)` at module scope,
    # reused for every call. Its clock starts at OpenCode BOOT, so the first call more than 210s
    # after boot aborts on arrival and every call after it aborts instantly — the capability is
    # gone while every other assertion here, `armed` included, still reads exactly right.
    assert result["arms"] == 1, (
        f"the tool armed {result['arms']} signals during the call. A bound armed anywhere but "
        "inside the call is measuring from the wrong moment")
    assert result["atImport"] == 0, (
        f"the tool armed {result['atImport']} signal(s) while the module merely LOADED, so its "
        "clock starts at OpenCode boot rather than at the call")
    assert result["armed"] < _QUIET_WINDOW_MS, (
        f"a bound of {result['armed']}ms cannot fire before _CHAT_TOOL_QUIET_TIMEOUT_S "
        f"({_QUIET_WINDOW_MS}ms) ends the turn, so the assistant is handed nothing either way")
    assert result["said"] == answered, "and the tool still reaches the route"


def _code(source: str) -> str:
    """The file's source with its comments dropped, both `//` lines and `/* */` blocks.

    Whole-line `//` only, and deliberately so: a blunter strip at the first `//` would eat the rest
    of the line that carries `http://127.0.0.1` — which in every one of these files is the line the
    `fetch(` itself sits on. None of these files puts a comment after code on the same line.

    The block strip is not hypothetical tidiness. The assertions below count a `fetch(` and look
    for the arm beside it, and those two fail in OPPOSITE directions: a `fetch(` left inside a
    comment is counted and reds, which is safe, but an ARM left inside a comment is credited to a
    fetch that does not have one, which is a green over a live unbounded call. These three files
    carry long prose about `AbortSignal.timeout` and are safe today only because those lines happen
    to start with `//`. A `/* */` block or a JSDoc `*` continuation carrying the same words would
    fail open, so the comment forms are stripped rather than trusted to stay in their lane."""
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    return "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith("//"))


def _fetch_calls(code: str) -> list[str]:
    """Each `fetch(...)` call's own argument text, parens balanced.

    Counting `fetch(` and `signal:` as two separate whole-file tallies and comparing the totals is
    not the same claim and can be satisfied without them ever meeting:

        const INIT = { method: "POST", signal: AbortSignal.timeout(TIMEOUT_MS) }   // armed, unused
        const res  = await fetch(ROUTE, { method: "POST" })                        // UNBOUNDED

    One `fetch(`, one arm, totals agree, and the call that ships has no bound on it. So the arm has
    to be found INSIDE the call it is supposed to be arming, which means reading the call's own
    span rather than the file's."""
    calls = []
    for match in re.finditer(r"\bfetch\s*\(", code):
        start = match.end() - 1
        depth = 0
        for index in range(start, len(code)):
            if code[index] == "(":
                depth += 1
            elif code[index] == ")":
                depth -= 1
                if depth == 0:
                    calls.append(code[start:index + 1])
                    break
        else:
            # Unbalanced. Hand back the rest of the file: it cannot contain the close, so whatever
            # this is, it is not a call this census can certify.
            calls.append(code[start:])
    return calls


def _shipped_tools() -> list[tuple[str, Path, str]]:
    """Every `.ts` the installer ships to OpenCode, as (path relative to ROOT, path, code).

    The directories come from `_OPENCODE_TOOL_DIRS` — the installer's own list — and not from a
    glob written here. A glob is a shape, and a shape is a list with the same blind spot as the
    hand-written one above: `sage/*/tools/*.ts` reads like "every tool" and means "every tool
    exactly two levels down", so a module registered at any other depth would be shipped and unseen.
    That is this ticket's defect wearing the test's clothes. Reading the installer's list means a
    directory added there arrives here in the same commit.

    A `.ts` in a directory the installer does NOT ship from is out of scope: it never reaches
    OpenCode, so it is not a tool and has no turn to go quiet in. Registering it is the act that
    makes it one, and that act is what these censuses watch."""
    from sage.orchestrator.app import _OPENCODE_TOOL_DIRS

    # `_OPENCODE_TOOL_DIRS` is relative to the REPO root; ROOT here is `backend/`.
    directories = [ROOT.parent.joinpath(*parts) for parts in _OPENCODE_TOOL_DIRS]
    assert directories, "the installer ships tools from nowhere, which cannot be right"
    for directory in directories:
        assert directory.is_dir(), (
            f"the installer ships tools from {directory}, which does not exist — either the list "
            "is stale or the tools moved, and either way this census is reading the wrong tree")

    shipped = [(path.relative_to(ROOT).as_posix(), path, _code(path.read_text()))
               for directory in directories for path in sorted(directory.glob("*.ts"))]
    assert shipped, (
        "the shipped tool directories hold no .ts at all, so every assertion resting on this is "
        "vacuous — the census would be certifying an empty set")
    return shipped


def _exports(code: str) -> set[str]:
    """The tool names a file produces, which is what OpenCode installs and the model can call.

    OpenCode names a default export after its file and a named export `<file>_<export>`, so
    `live_read.ts` is two tools and `artifact_write.ts` is one. Coverage keyed on the FILE would
    count a file as covered the moment any one of its exports was a subject — and a new
    `export const rows` beside `table` and `files` would ship as `live_read_rows`, reach the model,
    and be tested by nothing, while every census here stayed green."""
    names = set(re.findall(r"^export const (\w+)", code, re.MULTILINE))
    if re.search(r"^export default\b", code, re.MULTILINE):
        names.add("default")
    return names


def test_every_tool_that_fetches_is_bounded_and_is_a_subject_of_the_tests_above():
    """#416: the tests above name their subjects by hand, so they cannot see a tool nobody added.

    That is how `artifact_write.ts` shipped with no bound at all while a file called
    "a tool call that bounds itself" sat next to it and passed. A list cannot know it is short.

    This is a source census, and it answers a different question from every other test here: not
    "does the bound fire" — that claim is about behaviour and needs node to settle it — but "is
    every tool that fetches covered at all". Two questions, two tests. The census is the one that
    cannot go short, so it asserts COVERAGE of the hand-written list above and not merely the
    presence of a `signal`: a fourth tool added to the tree and to nothing else must red here even
    if it is perfectly bounded, because otherwise the census goes green while the runtime tests
    stay silent about it.

    Coverage is NOT keyed on the tool making a `fetch(`, and that is the second lesson rather than
    a detail. Keying it there means one spelling carries both guarantees at once: a tool that posts
    through an alias, through `globalThis["fetch"]`, or through `node:http` has no `fetch(` in it,
    so it is neither checked for a bound nor required to be a subject — it falls out of the census
    entirely and takes its own coverage with it. Every SHIPPED tool must be a subject, whatever it
    is built out of; the bound check then applies to the calls it actually makes."""
    shipped = _shipped_tools()

    for name, _path, code in shipped:
        for call in _fetch_calls(code):
            assert "AbortSignal.timeout(" in call, (
                f"{name} makes a fetch with no bound on it: {' '.join(call.split())[:120]}. An "
                "unbounded fetch sends nothing between `called` and its result, so the TURN dies "
                "quiet at _CHAT_TOOL_QUIET_TIMEOUT_S and the assistant is handed no sentence")

    discovered = {(name, export) for name, _path, code in shipped for export in _exports(code)}
    assert discovered, "no shipped tool exports anything, so OpenCode installs no tools at all"

    subjects = {(param.values[0], param.values[1]) for param in _TOOLS}
    assert discovered == subjects, (
        "every tool OpenCode installs has to be a subject of the runtime tests in this file, which "
        "are what prove the bound FIRES and says something the model can act on. Source ships "
        f"{sorted(discovered)}; the list above names {sorted(subjects)}")


def test_the_ceiling_in_the_tools_is_the_turns_own_quiet_window():
    """The ceiling is a product number, so it can drift away from the product.

    `_CHAT_TOOL_QUIET_TIMEOUT_S` lives in Python and the guards live in TypeScript, with no import
    between them and no way for either to notice the other moving. Lower the quiet window and every
    guard silently becomes a ceiling above it again — the same defect as before, arrived at from the
    other side and with nothing to announce it."""
    service = (ROOT / "sage" / "orchestrator" / "service.py").read_text()
    quiet = re.search(r"^_CHAT_TOOL_QUIET_TIMEOUT_S = ([0-9.]+)$", service, re.MULTILINE)
    assert quiet, (
        "_CHAT_TOOL_QUIET_TIMEOUT_S was not found in service.py, so this test compared nothing "
        "against nothing. It was renamed or moved, and the guards below now answer to no one")
    assert float(quiet.group(1)) * 1000 == _QUIET_WINDOW_MS, (
        f"the turn's quiet window moved to {quiet.group(1)}s and this file still says "
        f"{_QUIET_WINDOW_MS}ms")

    for name, _path, code in _shipped_tools():
        if not _fetch_calls(code):
            continue
        declared = re.findall(r"^const QUIET_WINDOW_MS = ([0-9_]+)$", code, re.MULTILINE)
        assert declared, f"{name} declares no QUIET_WINDOW_MS, so its ceiling is unreadable here"
        # Every one of them, not the first. A file that grew a second tool and a second constant
        # would pass a `search` on the strength of the one that is still right.
        for value in declared:
            assert int(value.replace("_", "")) == _QUIET_WINDOW_MS, (
                f"{name} caps a bound at {value}ms, but the turn goes quiet at {_QUIET_WINDOW_MS}ms")
