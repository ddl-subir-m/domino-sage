"""A Project tool's answer reaches the model on the next request, through Sage, on the Chat lane.

A Build turn on `domino/gemini-3.7-flash` called the Project's `fx_rate` ten times and was stopped
by the repeat brake. Every call completed, and the rate it then wrote (0.92) is also the rate the
model knows unaided, so the run could not say whether the tool's answer was ever read. Asked here of
the pinned OpenCode: the Project's own `fx_rate.ts`, the codec routing the Chat lane to Sage's
`/v1/sage/chat/completions`, the shim, and a scripted gateway that records the next request.
"""
from __future__ import annotations

import json

import pytest

from sage.router.models import Mode

from .opencode_server import BINARY
from .test_fragmented_tool_arguments_reach_real_opencode import Rig
from .test_turn_keepalive import _served

# The Project's tool as the workspace has it.
FX_RATE = '''const RATES: Record<string, number> = { USD: 1, EUR: 0.92, GBP: 0.79, INR: 83.4, JPY: 149.8 }

export default {
  description: "Convert an amount between currencies at Acme's fixed planning rates (USD, EUR, GBP, INR, JPY).",
  args: {
    amount: { type: "number", description: "The amount to convert." },
    from: { type: "string", description: "ISO code of the currency the amount is in." },
    to: { type: "string", description: "ISO code of the currency to convert to." },
  },
  async execute(args: { amount: number; from: string; to: string }) {
    const from = RATES[args.from?.toUpperCase()]
    const to = RATES[args.to?.toUpperCase()]
    if (from === undefined || to === undefined) {
      throw new Error(`fx_rate knows only ${Object.keys(RATES).join(", ")}`)
    }
    const converted = (args.amount / from) * to
    return JSON.stringify({ amount: Math.round(converted * 100) / 100, currency: args.to.toUpperCase(), source: "fx_rate" })
  },
}
'''
ARGS = {"amount": 420000, "from": "USD", "to": "EUR"}
ANSWER = '{"amount":386400,"currency":"EUR","source":"fx_rate"}'


def _chat(delta: dict, finish: str):
    frame = {"id": "scripted", "object": "chat.completion.chunk", "model": "m",
             "choices": [{"index": 0, "delta": delta, "finish_reason": None}]}
    yield b"data: " + json.dumps(frame).encode() + b"\n\n"
    frame["choices"] = [{"index": 0, "delta": {}, "finish_reason": finish}]
    yield b"data: " + json.dumps(frame).encode() + b"\n\ndata: [DONE]\n\n"


@pytest.mark.opencode
@pytest.mark.skipif(not BINARY.exists(), reason="the pinned OpenCode binary is not installed")
def test_a_project_tools_answer_is_in_the_next_chat_request(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch)
    rig.project.control.pick("domino/gemini-3.7-flash")
    rig.project.control.set_mode(Mode.IMPLEMENT)
    rig.orch.add_extension({"kind": "tool", "name": "fx_rate", "code": FX_RATE})

    def call_fx(request):
        offered = {t["function"]["name"] for t in request.get("tools", [])}
        assert "fx_rate" in offered, sorted(offered)
        # Signed, as Gemini signs its calls: unsigned, the shim moves the next request to another
        # model (#155) and this would stop being the Chat lane.
        yield from _chat({"tool_calls": [{"index": 0, "id": "call_fx", "type": "function",
                                          "function": {"name": "fx_rate",
                                                       "arguments": json.dumps(ARGS)},
                                          "extra_content": {"google": {"thought_signature": "sig"}}}]},
                         "tool_calls")

    rig.gateway.expect(call_fx)
    rig.gateway.expect(lambda request: _chat({"content": "386,400 EUR."}, "stop"))
    with _served(rig.app) as sage_url, rig.opencode(sage_url):
        with rig.turn():
            messages = rig.prompt("Convert 420,000 USD to EUR with fx_rate.", inferences=2)

    (rig.runtime / "requests.json").write_text(json.dumps(rig.gateway.seen, indent=2))
    from sage.gateway.protocol import Protocol
    assert rig.gateway.protocols == [Protocol.CHAT, Protocol.CHAT]
    [part] = [p for p in rig.tool_parts(messages) if p.get("tool") == "fx_rate"]
    assert part["state"]["status"] == "completed", part
    assert part["state"]["input"] == ARGS
    assert part["state"]["output"] == ANSWER
    told = [m for m in rig.gateway.seen[1]["messages"] if m.get("role") == "tool"]
    assert [(m.get("tool_call_id"), m.get("content")) for m in told] == [("call_fx", ANSWER)], told
    assert not rig.gateway.scripts and not rig.gateway.failures
