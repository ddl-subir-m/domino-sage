"""Measure which reasoning settings one gateway Alias accepts, and on which wire (#646).

`capabilities.resolve` answers only from evidence rows. This is what produces a row: the route
first, then each level alone and beside a function tool. `scripts/reasoning-evidence.py` drives it
from a laptop against one deployment; the transport is injected so the same measurement can run
anywhere that holds a gateway credential, and so it can be tested without one.

Every request is built by `RouteCapability.settings()` — the same call the shim makes in production.
Writing the effort field by hand here would measure a DIFFERENT question than the one Sage asks:
`messages` sends `thinking` plus `output_config.effort` and never `reasoning_effort`, so a probe
that sent `reasoning_effort` to an Anthropic route would record a 200 that no production turn can
reproduce.

Two things are asked before any level is:

    the route     a status code cannot answer this. Every accessible alias on cloud-dogfood
                  answers 200 on all three wires and replies in the shape of whichever was asked,
                  so "the native address answered" is true of a model the gateway is translating
                  for. ADR-0066's Responses contract is the discriminator instead, and it is about
                  content: a native route echoes a per-request nonce, `store: false` and the
                  requested effort, and a translated one drops them. Messages has no such contract,
                  so no Messages row is marked `native` here — but which WIRE to record is a
                  separate and answerable question, below.
    a nonsense value
                  an alias that HONOURS the field and one that THROWS IT AWAY both answer 200 to
                  `low`. Only an illegal value separates them: 400 means the field was validated,
                  200 means it was discarded and the level you sent bought nothing. Skip this and
                  every level reads as usable on a model that supports none of them.

                  Asked once per WIRE, it also answers which wire to record. The Anthropic aliases
                  discard the field on `chat` and validate it on `messages`; recording `chat` for
                  them is not the cautious answer but the wrong one, because the row then lands
                  with no usable level and the model quietly loses every reasoning setting it has.

Then each level is sent twice, alone and beside a function tool, because the pair is refused where
neither half is: gpt-5.4 takes an effort and takes tools, and on `/v1/chat/completions` refuses them
together. `efforts_with_tools` is the column that decides whether an effort is usable during a
build, where every turn carries tools.

`reason` is NOT measured. It is a sentence a person wrote about a model — haiku's says it needs a
manual thinking budget — and nothing in a status code implies it. A previous row's `reason` is
carried over; a new row gets `""` and wants a human.

A partial row is worse than no row, because it looks complete and the levels missing from it cannot
be told apart from refused. So every path that did not get a verdict returns None.
"""
from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from .capabilities import RouteCapability
from .events import StreamEvents
from .protocol import Protocol, endpoint

# Not a level anything advertises, and not a typo for one either: a value some provider quietly
# rounds to `low` would report "honours it" on an alias that does not.
NONSENSE = "banana"
# Every spelling seen across the providers the gateway fronts. Sent one at a time, because an alias
# that takes the field still refuses levels its backing model has no room for.
LEVELS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")

# (url, body) -> (status, body-or-explanation). A transport failure is status 0, never raised, so
# one dead alias cannot end a sweep — and 0 is not a measurement, which keeps it out of a row.
Call = Callable[[str, dict], tuple[int, str]]


def detail(raw: str) -> str:
    """The one sentence worth reading out of a refusal body."""
    try:
        parsed = json.loads(raw)
    except ValueError:
        # A deployment that bounces an unauthenticated call to its login page answers 200 with
        # HTML, which is the least obvious way a wrong key can present.
        return ("not JSON — a sign-in page, so the key was not accepted"
                if raw.lstrip()[:9].lower().startswith(("<!doctype", "<html"))
                else raw[:160].replace("\n", " "))
    while isinstance(parsed, dict):
        nxt = parsed.get("error") or parsed.get("detail") or parsed.get("message")
        if nxt is None:
            break
        parsed = nxt
    return str(parsed)[:160].replace("\n", " ")


def _body(alias: str, protocol: Protocol, effort: str | None, tools: bool) -> dict:
    """A smallest legal request for `protocol`, carrying the settings production would send."""
    if protocol is Protocol.MESSAGES:
        body: dict = {"model": alias, "max_tokens": 16,
                      "messages": [{"role": "user", "content": "hi"}]}
        if tools:
            body["tools"] = [{"name": "noop", "description": "does nothing",
                              "input_schema": {"type": "object", "properties": {}}}]
    elif protocol is Protocol.RESPONSES:
        body = {"model": alias, "input": "hi", "max_output_tokens": 16}
        if tools:
            body["tools"] = [{"type": "function", "name": "noop", "description": "does nothing",
                              "parameters": {"type": "object", "properties": {}}}]
    else:
        body = {"model": alias, "max_tokens": 16,
                "messages": [{"role": "user", "content": "hi"}]}
        if tools:
            body["tools"] = [{"type": "function",
                              "function": {"name": "noop", "description": "does nothing",
                                           "parameters": {"type": "object", "properties": {}}}}]
    # Built by the production helper, so the probe and the shim cannot drift apart. The capability
    # is constructed to allow exactly the level being asked about, because `settings` refuses a
    # level it has no evidence for — which is the whole point of it everywhere else.
    allowed = () if effort is None else (effort,)
    route = RouteCapability(protocol, efforts=allowed, efforts_with_tools=allowed, reason="")
    return body | route.settings(effort, tools=tools)


@dataclass
class Prober:
    """One deployment's gateway, and where the running commentary goes."""

    root: str
    call: Call
    say: Callable[[str], None] = lambda _line: None

    def _ask(self, alias: str, protocol: Protocol, effort: str | None,
             tools: bool = False) -> tuple[int, str]:
        return self.call(endpoint(self.root, protocol), _body(alias, protocol, effort, tools))

    def _passthrough(self, alias: str) -> tuple[bool, str]:
        """Does `/v1/responses` carry this alias natively, and the sentence saying how that was told.

        A status code answers nothing here. Measured on cloud-dogfood 2026-09-21: EVERY accessible
        alias answers 200 on all three wires and replies in the shape of whichever wire was asked,
        so "the native address answered" is true of a model the gateway is translating for.

        ADR-0066:117-122 gives the discriminator, and it is content and not status: a native
        Responses route must echo a per-request metadata nonce, `store: false`, and the REQUESTED
        effort, and "the gateway's translated response does not satisfy that contract". Measured
        against a negative control the same day — `domino/gemini-3.7-flash`, which ADR-0066:43
        records as a compatibility route — the echo is the thing that separates them:

            domino/gemini-3.7-flash   store=None   nonce missing   effort missing   translated
            bedrock-qwen3-coder       store=None   nonce missing   effort missing   translated
            qwen-2-5                  store=None   nonce echoed    effort echoed    partial
            GLM 5.3 OR                store=False  nonce echoed    effort echoed    native
            gpt-5.4                   store=False  nonce echoed    effort echoed    native

        All three are required, so the partial row is reported as what it is rather than rounded
        up: an alias that returns the effort but drops `store` has not shown the request reached the
        vendor unstored, which is the half of the contract that is about where state lives.
        """
        nonce = uuid.uuid4().hex
        body = {"model": alias, "input": "hi", "max_output_tokens": 16, "store": False,
                "metadata": {"sage_nonce": nonce}, "reasoning": {"effort": "low"}}
        status, raw = self.call(endpoint(self.root, Protocol.RESPONSES), body)
        if status != 200:
            return False, f"/v1/responses answered {status}"
        try:
            reply = json.loads(raw)
        except ValueError:
            return False, "/v1/responses answered 200 but not JSON"
        echoed = {"nonce": (reply.get("metadata") or {}).get("sage_nonce") == nonce,
                  "store": reply.get("store") is False,
                  "effort": (reply.get("reasoning") or {}).get("effort") == "low"}
        missing = [name for name, ok in echoed.items() if not ok]
        return not missing, "the contract holds" if not missing else f"dropped {', '.join(missing)}"

    def _validates(self, alias: str, protocol: Protocol) -> bool | None:
        """Does this wire READ the effort field, or throw it away? None = it answered neither.

        Measured on cloud-dogfood 2026-09-21, status for `banana` on each wire:

            sonnet / opus / haiku / etan-opus-4.6    chat 200    messages 400    responses 200
            GLM 5.3 OR / gpt-5.4 / qwen-2-5          chat 400    messages 200    responses 400
            domino/gemini-3.7-flash                  chat 400    messages 200    responses 200
            bedrock-qwen3-coder                      chat 200    messages 200    responses 200

        The second, third and fourth lines are what give the first one its meaning. Five
        non-Anthropic aliases answer 200 to `banana` on `messages`, so the Anthropic 400 there is
        not this gateway validating everything that arrives on that wire — something holding
        Anthropic's schema is reading it. The same four also refuse `xhigh` while accepting `max`,
        a distinction Anthropic draws and the gateway draws nowhere else.
        """
        status, _ = self._ask(alias, protocol, NONSENSE)
        return status == 400 if status in (200, 400) else None

    def _route(self, alias: str, previous: dict | None) -> tuple[Protocol, bool] | None:
        """Which wire to record for this alias, and whether it is vendor-native. None = unmeasured.

        Chat is asked first as the control: it is the compatibility route, every alias has one, and
        a gateway that refuses it is refusing everything. Then the Responses contract above decides
        native, because nothing else can.

        Messages has no such contract. ADR-0066:130 says it "relies on the exact recent metadata and
        measured native route" — a measurement made elsewhere, from gateway audit rows this key
        cannot read. So `native` is never set from this wire.

        The WIRE is a different question from `native`, and this is the one that matters at
        runtime: `settings()` shapes the body from `protocol`, and `native_routes` fails a turn
        whose wire disagrees with it, while `native` is only reported onward. Recording `chat` for
        an alias whose chat wire discards the field writes a row with no usable level at all — so
        the wire to record is the one that READS the field, and where only `messages` does, that is
        `messages`.
        """
        control = self._ask(alias, Protocol.CHAT, None)
        if control[0] != 200:
            # The gateway's own sentence, not a guess at it. A workspace that has spent its API
            # quota refuses every route with a 400 that names the date access returns, and
            # "unusable or stopped" would send someone to look at the wrong thing entirely.
            self.say(f"  NOT MEASURED — no route answered 200. {detail(control[1])}")
            return None
        native, why = self._passthrough(alias)
        self.say(f"  /v1/responses: {why}")
        if native:
            return Protocol.RESPONSES, True
        on_messages = self._validates(alias, Protocol.MESSAGES)
        on_chat = self._validates(alias, Protocol.CHAT)
        if on_messages is None or on_chat is None:
            self.say("  NOT MEASURED — a wire answered neither 200 nor 400 to the nonsense value, "
                     "so which wire reads the effort field cannot be told from which one discards "
                     "it.")
            return None
        if on_messages and not on_chat:
            self.say("  /anthropic/v1/messages reads the effort field and /v1/chat/completions "
                     "discards it, so `messages` is the only wire that can carry a reasoning "
                     "setting here.")
            return Protocol.MESSAGES, False
        if previous and str(previous.get("protocol")) == str(Protocol.MESSAGES):
            self.say("  NOT MEASURED — the existing row records a Messages route, and this "
                     "deployment's Messages wire does not read the effort field. Left as it is "
                     "rather than downgraded; re-measure it where the gateway's audit rows can be "
                     "read.")
            return None
        return Protocol.CHAT, False

    def _echoed(self, alias: str, level: str, tools: bool) -> tuple[int, str, bool]:
        """(status, body, whether the level came back) for one level on a native Responses route.

        Asked the way the runtime asks: streamed, unstored, with a `sage_route_check` nonce, and
        read by the same `StreamEvents` contract that refuses the stream when any of the three is
        not echoed. A 200 alone is not enough — Gemini 3.8 Flash answered 200 to its lowest level
        and did not send it back, so a level recorded on status was refused on every call (#664).
        """
        nonce = uuid.uuid4().hex
        body = _body(alias, Protocol.RESPONSES, level, tools) | {
            "stream": True, "store": False, "include": ["reasoning.encrypted_content"],
            "metadata": {"sage_route_check": nonce}}
        status, raw = self.call(endpoint(self.root, Protocol.RESPONSES), body)
        if status != 200:
            return status, raw, False
        events = StreamEvents(Protocol.RESPONSES, response_contract={"nonce": nonce, "effort": level})
        try:
            events.feed(raw.encode())
            events.finish()
        except (ValueError, TypeError):
            return status, raw, False
        return status, raw, True

    def _levels(self, alias: str, protocol: Protocol, tools: bool) -> list[str] | None:
        """The levels this route accepts. None = a level went unanswered, so no row is writable."""
        usable, unanswered = [], []
        for level in LEVELS:
            if protocol is Protocol.RESPONSES:
                status, raw, echoed = self._echoed(alias, level, tools)
                if status == 200 and not echoed:
                    self.say(f"  {level}{' with tools' if tools else ''} answered 200 but did not "
                             "come back on the stream, so it is not usable")
                    continue
            else:
                status, raw = self._ask(alias, protocol, level, tools)
            if status == 200:
                usable.append(level)
            elif status != 400:
                unanswered.append(f"{level} ({status}: {detail(raw)})")
        if unanswered:
            self.say(f"  INCOMPLETE{' with tools' if tools else ''} — no verdict on "
                     f"{'; '.join(unanswered)}. Re-run before recording.")
            return None
        return usable

    def measure(self, row: dict, previous: dict | None = None) -> dict | None:
        """`row` (an identity, as `route_identity` reads it) with its measured capability, or None.

        `previous` is the row this one replaces, if any: its `reason` is carried, and a recorded
        Messages route is not downgraded on a deployment that cannot see Messages validate.
        """
        alias = str(row["name"])
        if row.get("fallback_chain"):
            # `resolve` refuses these before it reads a proof, so measuring one would record a row
            # nothing can ever match.
            self.say("  skipped — an unverified fallback route; reasoning is refused for it by "
                     "design")
            return None
        route = self._route(alias, previous)
        if route is None:
            return None
        protocol, native = route
        self.say(f"  route {protocol} (native {native})")
        junk = self._ask(alias, protocol, NONSENSE)
        if junk[0] == 200:
            # Not an empty row by omission: the alias answers happily to a level that does not
            # exist, so every level it "accepts" is a level it discarded.
            self.say(f"  discards the field — {NONSENSE} answered 200, so no level buys anything")
            efforts, with_tools = [], []
        elif junk[0] != 400:
            self.say(f"  NOT MEASURED — the gateway answered {junk[0]} to the nonsense value, not "
                     f"a verdict: {detail(junk[1])}")
            return None
        else:
            efforts = self._levels(alias, protocol, tools=False)
            with_tools = self._levels(alias, protocol, tools=True) if efforts is not None else None
            if efforts is None or with_tools is None:
                return None
            # The control, asked AGAIN after the sweep. A 400 means "this level is refused" only
            # while the route still works at all, and a workspace can spend its API quota partway
            # through thirty calls — after which every remaining level 400s with a message about a
            # date. That reads as a model which accepts no effort, and it writes an empty row that
            # looks measured. A control that no longer answers says the sweep stopped being a
            # measurement; it cannot say when, so nothing from it is kept.
            after = self._ask(alias, protocol, None)
            if after[0] != 200:
                self.say(f"  NOT MEASURED — the route stopped answering during the sweep, so the "
                         f"levels above are not evidence: {detail(after[1])}")
                return None
            self.say(f"  efforts            {', '.join(efforts) or 'none'}")
            self.say(f"  efforts_with_tools {', '.join(with_tools) or 'none'}")
        # Carried, never invented: `reason` is a sentence about the MODEL, and no status code
        # implies one.
        return row | {"protocol": str(protocol), "native": native, "efforts": efforts,
                      "efforts_with_tools": with_tools,
                      "reason": (previous or {}).get("reason", "")}
