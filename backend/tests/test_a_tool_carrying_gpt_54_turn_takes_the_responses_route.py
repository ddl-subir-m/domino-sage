"""#505: gpt-5.4's turns on this deployment take `/v1/responses`, not `/v1/chat/completions`.

Measured live on cloud-dogfood 2026-09-22. The alias has been repointed upstream — `/api/aliases`
now reports `provider_model: gpt-5.6-sol` — and on `/v1/chat/completions` that model refuses a
request that carries function tools unless `reasoning_effort` is present:

    tools + reasoning_effort:"none"  -> 200
    tools + reasoning_effort:"low"   -> 400
    tools + NO field at all          -> 400   <- what a Model-default turn sends

Every Build turn carries tools and `auto-plan` resolves to gpt-5.4, so a new workspace died on its
first Build turn, in ~730ms, with `invalid_request_error`. The gateway's own refusal says the way
out: "To use function tools, use /v1/responses or set reasoning_effort". Sending
`reasoning_effort: "none"` was rejected as the fix — `enforcement.py` adds the field from
`configured` and from nowhere else, and for this alias `none` means NO reasoning, so a hidden one
here would silently downgrade every Build plan turn. (#545 later gave an unset Build level the
STAGE's level, in the open and a dozen lines earlier; it is still not a fallback chosen here.)

So the route moves instead, and the whole move is one measured evidence row: nothing here is a new
seam. `RouteCapability` already carries the protocol, `native_routes.py` already serves
`/v1/sage/responses` with its `sage_route_check` nonce contract, and `driver/provider.mjs` already
switches on `route.protocol === 'responses'`. The reason this deployment went to chat is that
`reasoning-evidence.json` had no cloud-dogfood row for gpt-5.4 at all — only one for a different
gateway — so `resolve` fell through to a bare `RouteCapability`, whose protocol is CHAT and whose
effort lists are empty.

Measured on the wire the same day, streaming, with the 13 tools a Build plan turn carries and the
body `prepare_native` emits (store:false, include reasoning.encrypted_content, the nonce in
`metadata`), read back through the production `StreamEvents` parser:

    tools + NO reasoning field   200, contract held, `read` tool call parsed end to end
    tools + reasoning low        200, contract held, same
    no tools, no field           200, contract held, text deltas

and `scripts/reasoning-evidence.py` measured every level usable there WITH tools —
none, low, medium, high, xhigh, max — where the chat wire offered none of them.
"""
from __future__ import annotations

import copy

from sage.gateway.capabilities import evidence, resolve
from sage.gateway.protocol import Protocol

# Evidence is deployment-specific (ADR-0066): the gateway root is part of the identity, so a row
# measured on one deployment says nothing about another. Named here because the file carries a
# gpt-5.4 row for a SECOND gateway, and that row is what made this defect hard to see.
DOGFOOD = "https://apps.cloud-dogfood.domino.tech/apps/llm_gateway"


def row(name: str, gateway: str = DOGFOOD) -> dict | None:
    return copy.deepcopy(next((r for r in evidence()
                               if r["name"] == name and r["gateway"] == gateway), None))


def test_this_deployment_has_a_measured_gpt_54_row_and_it_names_the_responses_wire():
    proof = row("gpt-5.4")
    assert proof is not None, "no gpt-5.4 row for this deployment — every turn falls back to chat"
    assert proof["protocol"] == "responses" and proof["native"] is True
    assert proof["efforts_with_tools"], "a row with no tool-side level offers the picker nothing"


def test_a_tool_carrying_turn_resolves_to_the_responses_protocol_and_its_effort_spelling():
    """The two halves the send path reads: which endpoint, and how an effort is expressed on it.

    `reasoning: {effort}` is the Responses spelling — `reasoning_effort` is the chat one, and it is
    the pair (tools, that field) the repointed alias refuses. Model default stays absent on this
    wire too: the route is what fixes the 400, not a hidden override.
    """
    proof = row("gpt-5.4")
    assert proof is not None
    capability = resolve(DOGFOOD, proof, evidence())
    assert capability.protocol is Protocol.RESPONSES and capability.native
    assert capability.settings(None, tools=True) == {}
    assert capability.settings("low", tools=True) == {"reasoning": {"effort": "low"}}


def test_a_turn_without_tools_takes_the_same_route_and_keeps_every_measured_level():
    """A protocol is recorded per ROUTE, not per request shape, so a turn carrying no tools moves
    with it. Measured 200 on the wire, and the no-tools level column is recorded beside the
    tool-side one so the picker offers the same levels either way."""
    proof = row("gpt-5.4")
    assert proof is not None
    capability = resolve(DOGFOOD, proof, evidence())
    assert capability.efforts == capability.efforts_with_tools
    assert capability.settings("low", tools=False) == {"reasoning": {"effort": "low"}}


def test_the_other_aliases_on_this_deployment_keep_the_wires_they_were_measured_on():
    """No collateral. Each of these was measured on its own wire and none of them was re-measured
    here, so a sweep that quietly moved one would be reporting something nobody asked about."""
    for name, protocol in (("sonnet", Protocol.MESSAGES), ("opus", Protocol.MESSAGES),
                           ("haiku", Protocol.MESSAGES), ("etan-opus-4.6", Protocol.MESSAGES),
                           ("GLM 5.3 OR", Protocol.CHAT)):
        proof = row(name)
        assert proof is not None, name
        assert resolve(DOGFOOD, proof, evidence()).protocol is protocol, name
