"""The search that turns "something in this conversation" into "this file".

The gateway is a yes/no oracle — its refusal body is 93 bytes and names only the guardrail — so the
only way to learn more is to ask it repeatedly with different parts withheld. These tests stand in
for the gateway with a fake that refuses any payload still carrying a marker, which is exactly the
shape of a value-matching guardrail.
"""
from __future__ import annotations

from sage.orchestrator import withhold
from sage.orchestrator.withhold import BLOCKED, CLEAN, UNKNOWN, Carrier, carriers, search
from sage.shim.chat_paths import apply_withheld, file_key, text_key

POISON = "222-33-4444"


def _read(cid: str, path: str, body: str) -> list[dict]:
    """One file read, as the two messages it actually is on the wire."""
    return [
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": cid, "type": "function",
             "function": {"name": "read", "arguments": '{"filePath": "%s"}' % path}}]},
        {"role": "tool", "tool_call_id": cid, "content": body},
    ]


def _asker(marker: str = POISON):
    """A gateway that refuses any payload still carrying `marker`, and counts what it was sent."""
    seen: list[list[dict]] = []

    def ask(messages: list[dict]) -> str:
        seen.append(messages)
        blob = "\n".join(str(m.get("content") or "") for m in messages)
        return BLOCKED if marker in blob else CLEAN

    return ask, seen


def _convo(*, clean_files: int = 3, poisoned: str = "raw.csv") -> list[dict]:
    msgs: list[dict] = [{"role": "system", "content": "You are Sage."},
                        {"role": "user", "content": "Read the files I attached."}]
    for i in range(clean_files):
        msgs += _read(f"c{i}", f"clean_{i}.csv", f"ticker,week\nVLTA,2026-0{i+1}-02")
    msgs += _read("bad", poisoned, f"name,ssn\nJ Doe,{POISON}")
    msgs += [{"role": "user", "content": "Chart weekly panel spend."}]
    return msgs


def test_it_names_the_one_file_that_is_refused():
    msgs = _convo()
    ask, _ = _asker()
    found = search(msgs, ask)
    assert [c.label for c in found.carriers] == ["raw.csv"]
    assert found.complete is True


def test_the_search_costs_about_two_log_n_calls():
    """The measured budget, held as a test so a rewrite cannot quietly make it linear.

    Six carriers here — four file reads and the two things the person typed — so the bisect itself
    is the measured `2*log2(6)+1` ~= 6, and the search spends three more on framing: the root probe
    that says this is a guardrail refusal at all, the floor probe that says the cause is reachable,
    and the verify probe that says withholding actually clears it. Those three are what stop the
    result being a guess, and they do not grow with the conversation.
    """
    ask, _ = _asker()
    found = search(_convo(clean_files=3), ask)
    assert found.calls <= 6 + 3


def test_it_finds_every_carrier_not_just_the_first():
    """The failure that makes a find-first bisect worse than useless: Sage names a file, takes it
    away, and the next turn is refused identically."""
    msgs = _convo()
    msgs += _read("bad2", "export.csv", f"account,ssn\n42,{POISON}")
    ask, _ = _asker()
    found = search(msgs, ask)
    assert sorted(c.label for c in found.carriers) == ["export.csv", "raw.csv"]
    assert found.complete is True


def test_pasted_text_is_a_carrier_even_though_no_file_is_involved():
    msgs = [{"role": "system", "content": "You are Sage."},
            {"role": "user", "content": f"what do you make of ssn {POISON}"}]
    ask, _ = _asker()
    found = search(msgs, ask)
    assert [c.label for c in found.carriers] == ["the message you sent"]
    assert found.carriers[0].is_file is False
    assert found.complete is True


def test_sages_own_instructions_are_never_a_carrier():
    """An agent that loses its system prompt answers as a stranger — the failure ADR-0022 refused."""
    msgs = [{"role": "system", "content": f"You are Sage. Never say {POISON}."},
            {"role": "user", "content": "hello"}]
    assert all(c.label != "an earlier answer in this conversation" for c in carriers(msgs))
    assert not [c for c in carriers(msgs) if "system" in c.label]
    found = search(msgs, _asker()[0])
    assert found.carriers == []
    assert found.complete is False


def test_one_file_read_three_times_is_one_carrier():
    msgs = _convo(clean_files=0)
    msgs += _read("bad_b", "raw.csv", f"name,ssn\nJ Doe,{POISON}")
    msgs += _read("bad_c", "raw.csv", f"name,ssn\nJ Doe,{POISON}")
    assert [c.key for c in carriers(msgs)].count(file_key("raw.csv")) == 1
    found = search(msgs, _asker()[0])
    assert [c.label for c in found.carriers] == ["raw.csv"]
    assert found.complete is True


def test_a_refusal_that_survives_the_withhold_is_reported_not_papered_over():
    """A carrier that cannot be isolated must not be reported as fixed."""
    msgs = _convo()

    def ask(messages: list[dict]) -> str:
        # Refuses everything, whatever is withheld — a guardrail matching something this search
        # cannot reach (the system prompt, or the request's own shape).
        return BLOCKED

    found = search(msgs, ask)
    assert found.complete is False


def test_a_turn_that_failed_for_another_reason_is_left_alone():
    ask, seen = _asker(marker="nothing here matches")
    found = search(_convo(), ask)
    assert found.carriers == []
    assert found.stopped == "not blocked"
    assert len(seen) == 1, "one probe is enough to learn this is not ours to explain"


def test_no_verdict_stops_the_search():
    found = search(_convo(), lambda messages: UNKNOWN)
    assert found.carriers == []
    assert found.complete is False
    assert found.stopped == "no verdict"


def test_the_call_budget_is_honoured():
    msgs = [{"role": "system", "content": "You are Sage."}]
    for i in range(40):
        msgs += _read(f"c{i}", f"f_{i}.csv", POISON if i == 39 else "clean")
    ask, seen = _asker()
    found = search(msgs, ask, cap=5)
    assert len(seen) <= 5
    assert found.stopped == "out of calls"


def test_every_probe_keeps_the_tool_pairing_intact():
    """Measured: a tool result dropped while its tool_call stays is a 400 on every provider. The
    search must never produce a payload that would 400 for a reason of its own."""
    msgs = _convo()
    ask, seen = _asker()
    search(msgs, ask)
    for payload in seen:
        assert len(payload) == len(msgs)
        ids = {str(m.get("tool_call_id")) for m in payload if m.get("role") == "tool"}
        for m in payload:
            for call in (m.get("tool_calls") or []) if isinstance(m, dict) else []:
                assert str(call.get("id")) in ids, "a tool_call lost its result"


def test_the_placeholder_tells_the_model_to_stop_retrying():
    """`denied_write_result`'s lesson: a placeholder that does not say what happened gets retried."""
    msgs = _convo()
    withheld = apply_withheld(msgs, {file_key("raw.csv")})
    said = next(m["content"] for m in withheld if m.get("role") == "tool" and "withheld" in str(m["content"]))
    assert "raw.csv" in said
    assert "not being sent" in said
    assert "Do not try to read it again" in said
    assert POISON not in said


def test_a_fingerprint_is_recorded_never_the_text():
    """The transcript records WHICH message to stop sending, never what was in it."""
    m = {"role": "user", "content": f"ssn {POISON}"}
    assert POISON not in text_key(m)
    assert text_key(m) == text_key({"role": "user", "content": f"ssn {POISON}"})
    assert text_key(m) != text_key({"role": "user", "content": "ssn 111-22-3333"})
