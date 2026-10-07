"""Which reasoning settings an Alias accepts is measured, and only a full verdict becomes a row (#646).

The probe used to live only in `scripts/reasoning-evidence.py`, where nothing tested it: every rule
below was learned from a live gateway and held only by the next person reading the docstrings. It
now lives in `sage.gateway.measure` with its transport injected, so each rule is held here against
a gateway that answers the way the measured ones did.
"""
from __future__ import annotations

import json

from sage.gateway.measure import NONSENSE, Prober

ROOT = "https://gw.example"
ROW = {"id": "a1", "name": "model-x", "provider_id": "p1", "provider_type": "openai",
       "provider_model": "x", "updated_at": "2026-10-05T00:00:00", "fallback_chain": [],
       "gateway": ROOT}


def _wire(url: str) -> str:
    return ("messages" if url.endswith("/anthropic/v1/messages")
            else "responses" if url.endswith("/v1/responses") else "chat")


def _effort(wire: str, body: dict) -> str | None:
    """The level a request carries, read the way each wire spells it."""
    if wire == "messages":
        if (body.get("thinking") or {}).get("type") == "disabled":
            return "none"
        return (body.get("output_config") or {}).get("effort")
    if wire == "responses":
        return (body.get("reasoning") or {}).get("effort")
    return body.get("reasoning_effort")


class Gateway:
    """A gateway whose answers are set per wire. Records every request it was sent."""

    def __init__(self, *, native=False, validates=("chat",), accepts=(), accepts_with_tools=None,
                 control=200, broken_level=None, controls_after=None, unechoed=()):
        self.native = native
        self.unechoed = set(unechoed)          # levels answered 200 on responses without the echo
        self.validates = set(validates)       # wires that 400 the nonsense value
        self.accepts = set(accepts)
        self.accepts_with_tools = set(accepts if accepts_with_tools is None else accepts_with_tools)
        self.control = control
        self.broken_level = broken_level      # a level this gateway answers 500 to
        self.controls_after = controls_after  # status of every control after the first
        self.sent: list[tuple[str, dict]] = []
        self._controls = 0

    def __call__(self, url: str, body: dict) -> tuple[int, str]:
        wire = _wire(url)
        self.sent.append((wire, body))
        if "sage_nonce" in (body.get("metadata") or {}):  # the Responses contract check
            echo = ({"store": False, "metadata": body["metadata"], "reasoning": body["reasoning"]}
                    if self.native else {"store": None})
            return 200, json.dumps(echo)
        effort = _effort(wire, body)
        if effort is None:
            self._controls += 1
            status = self.control if self._controls == 1 or self.controls_after is None \
                else self.controls_after
            return status, json.dumps({"error": {"message": "quota spent until 2026-11-01"}}
                                      if status != 200 else {})
        if effort == NONSENSE:
            return (400 if wire in self.validates else 200), "{}"
        if effort == self.broken_level:
            return 500, json.dumps({"error": "upstream exploded"})
        allowed = self.accepts_with_tools if "tools" in body else self.accepts
        if effort in allowed and wire == "responses":
            # Echoed only on a stream, as the runtime reads it; a level in `unechoed` comes back
            # with no effort, the way a gateway that rewrote or dropped it answers.
            response = {"store": body.get("store"), "metadata": body.get("metadata"),
                        "reasoning": {} if effort in self.unechoed else body["reasoning"]}
            if not body.get("stream"):
                return 200, json.dumps(response)
            events = [{"type": "response.created", "response": response},
                      {"type": "response.completed", "response": response}]
            return 200, "".join(f"data: {json.dumps(e)}\n\n" for e in events)
        return (200 if effort in allowed else 400), "{}"


def _measure(gateway: Gateway, previous: dict | None = None, row: dict = ROW) -> dict | None:
    return Prober(ROOT, gateway).measure(row, previous)


def test_a_native_responses_route_is_recorded_with_its_levels():
    row = _measure(Gateway(native=True, validates=("chat", "responses"), accepts=("low", "high")))
    assert row["protocol"] == "responses" and row["native"] is True
    assert row["efforts"] == row["efforts_with_tools"] == ["low", "high"]


def test_a_level_answered_200_without_its_echo_is_not_recorded():
    """Gemini 3.8 Flash (#664): `none` answered 200 and was recorded, and then every classifier
    call that sent it was refused at runtime because the level did not come back."""
    row = _measure(Gateway(native=True, validates=("chat", "responses"),
                           accepts=("none", "low", "high"), unechoed=("none",)))
    assert row["efforts"] == row["efforts_with_tools"] == ["low", "high"]


def test_the_wire_that_reads_the_field_is_recorded_not_the_one_that_discards_it():
    gateway = Gateway(validates=("messages",), accepts=("none", "low", "high", "max"))
    row = _measure(gateway)
    assert row["protocol"] == "messages" and row["native"] is False
    assert row["efforts"] == ["none", "low", "high", "max"]
    # Production's body, not a hand-written one: Messages carries no `reasoning_effort` at all.
    assert all("reasoning_effort" not in body for wire, body in gateway.sent if wire == "messages")


def test_a_route_that_discards_the_field_is_measured_as_offering_nothing():
    row = _measure(Gateway(validates=(), accepts=("low",)))
    assert row is not None, "an alias that discards the field is a verdict, not a gap"
    assert row["protocol"] == "chat" and row["efforts"] == row["efforts_with_tools"] == []


def test_levels_refused_beside_tools_are_kept_apart():
    row = _measure(Gateway(accepts=("none", "low", "high"), accepts_with_tools=("none",)))
    assert row["efforts"] == ["none", "low", "high"]
    assert row["efforts_with_tools"] == ["none"]


def test_a_gateway_that_refuses_the_control_writes_no_row():
    gateway = Gateway(control=400, accepts=("low",))
    assert _measure(gateway) is None
    assert len(gateway.sent) == 1, "nothing past the control is worth asking"


def test_a_level_with_no_verdict_writes_no_row():
    assert _measure(Gateway(accepts=("low", "high"), broken_level="medium")) is None


def test_a_sweep_the_quota_ended_writes_no_row():
    """Every level 400s once the quota is spent, which would read as "offers nothing"."""
    assert _measure(Gateway(accepts=("low",), controls_after=400)) is None


def test_a_recorded_messages_route_is_not_downgraded_where_messages_cannot_be_seen():
    previous = ROW | {"protocol": "messages", "reason": ""}
    assert _measure(Gateway(validates=("chat",), accepts=("low",)), previous) is None


def test_a_fallback_route_is_never_asked():
    gateway = Gateway(accepts=("low",))
    assert _measure(gateway, row=ROW | {"fallback_chain": ["other"]}) is None
    assert gateway.sent == []


def test_a_sentence_somebody_wrote_about_the_model_is_carried():
    previous = ROW | {"protocol": "chat", "reason": "Needs a manual thinking budget."}
    row = _measure(Gateway(accepts=("low",)), previous)
    assert row["reason"] == "Needs a manual thinking budget."
