"""`askJson` resolves with a value or the caller's fallback, and never throws (#681).

Live (2026-10-07): a brief did `const res = await sage.askModel(...)`, read `res.text` off what is a
plain string, fell through its own JSON parse to `null`, and drew a "Regenerate" button over
nothing, with no error anywhere. Another asked for JSON with a cap too small to finish and showed
the parser's error. `askJson` owns the parts each app got wrong: the room to finish, the "JSON only"
instruction, the fence a model wraps around it, and the parse, and it says which one failed.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

HARNESS = Path(__file__).parent / "js" / "app_ask_json_harness.mjs"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")

TEMPLATES = ["fastapi-antd", "react-vite"]
ASK = [{"role": "user", "content": "Summarise the deal."}]
FALLBACK = {"summary": "", "risks": []}


def _answer(content: str, finish: str = "stop", status: int = 200) -> dict:
    if status != 200:
        return {"status": status, "body": {}}
    return {"status": 200, "body": {"model": "m", "choices": [
        {"message": {"content": content}, "finish_reason": finish}]}}


def _run(template: str, cases: dict) -> dict:
    payload = [{"name": name, **case} for name, case in cases.items()]
    out = subprocess.run(["node", str(HARNESS)], input=json.dumps({"template": template, "cases": payload}),
                         capture_output=True, text=True, timeout=30, check=True)
    return json.loads(out.stdout)


def _json(template: str, reply: dict, opts: dict | None = None) -> dict:
    row = _run(template, {"c": {"call": "askJson", "args": [ASK, {"fallback": FALLBACK, **(opts or {})}],
                                "reply": reply}})["c"]
    assert "thrown" not in row, row
    return row


@pytest.mark.parametrize("template", TEMPLATES)
def test_a_json_answer_resolves_ok_with_the_parsed_value(template: str):
    row = _json(template, _answer('{"summary": "Renewal at risk", "risks": ["price"]}'))
    assert row["result"]["ok"] is True
    assert row["result"]["value"] == {"summary": "Renewal at risk", "risks": ["price"]}
    assert row["result"]["evidence"]["requestId"] == "req-1"


@pytest.mark.parametrize("template", TEMPLATES)
def test_a_fenced_answer_is_read_through_its_fence(template: str):
    row = _json(template, _answer('```json\n{"summary": "ok", "risks": []}\n```'))
    assert row["result"] == {**row["result"], "ok": True, "value": {"summary": "ok", "risks": []}}


@pytest.mark.parametrize("template", TEMPLATES)
def test_prose_resolves_with_the_fallback_and_says_why(template: str):
    result = _json(template, _answer("## Deal brief\n\nThe renewal is **at risk**."))["result"]
    assert result["ok"] is False and result["value"] == FALLBACK
    assert result["kind"] == "invalid_json" and result["error"]


@pytest.mark.parametrize("template", TEMPLATES)
@pytest.mark.parametrize(("reply", "kind"), [(_answer('{"summary": "cut', finish="length"), "incomplete"),
                                             (_answer("", status=403), "access")],
                         ids=["cut-short", "no-access"])
def test_a_failed_call_resolves_with_the_fallback_and_its_kind(template: str, reply: dict, kind: str):
    result = _json(template, reply)["result"]
    assert result["ok"] is False and result["value"] == FALLBACK
    assert result["kind"] == kind and result["error"]


@pytest.mark.parametrize("template", TEMPLATES)
def test_an_alias_the_app_does_not_use_still_does_not_throw(template: str):
    result = _json(template, _answer("{}"), {"alias": "not-ours"})["result"]
    assert result["ok"] is False and result["value"] == FALLBACK
    assert "not-ours" in result["error"]


@pytest.mark.parametrize("template", TEMPLATES)
def test_the_request_asks_for_json_only_with_the_hint_and_never_streams(template: str):
    sent = _json(template, _answer("{}"), {"schemaHint": '{ "summary": string, "risks": string[] }'})["requests"][0]
    system = sent["messages"][0]
    assert system["role"] == "system" and "JSON" in system["content"]
    assert '{ "summary": string, "risks": string[] }' in system["content"]
    assert sent["messages"][1:] == ASK
    assert sent["stream"] is False


@pytest.mark.parametrize("template", TEMPLATES)
def test_a_callers_system_message_is_kept_and_carries_the_json_rule(template: str):
    ask = [{"role": "system", "content": "You write deal briefs."}, *ASK]
    row = _run(template, {"c": {"call": "askJson", "args": [ask, {"fallback": None}], "reply": _answer("{}")}})["c"]
    messages = row["requests"][0]["messages"]
    assert [m["role"] for m in messages] == ["system", "user"]
    assert messages[0]["content"].startswith("You write deal briefs.") and "JSON" in messages[0]["content"]


@pytest.mark.parametrize("template", TEMPLATES)
@pytest.mark.parametrize(("asked", "sent"), [(None, 1500), (300, 1200), (4000, 4000)])
def test_the_answer_gets_room_to_finish(template: str, asked, sent: int):
    opts = {} if asked is None else {"maxTokens": asked}
    assert _json(template, _answer("{}"), opts)["requests"][0]["max_tokens"] == sent


# ---- the single-object call form (#681, showcase prompt 9) ----------------------------------------
# An app called `sage.askModel({ messages, maxTokens: 1200, alias: 'haiku' })`. The whole object
# became `messages`, `alias` and `maxTokens` were dropped, and the gateway answered 422, which the
# viewer read as "The model did not answer (error 422)". Both forms are now the same call.


@pytest.mark.parametrize("template", TEMPLATES)
def test_ask_model_accepts_one_object_holding_the_messages_and_the_options(template: str):
    got = _run(template, {
        "one": {"call": "askModel", "args": [{"messages": ASK, "alias": "second", "maxTokens": 1200}],
                "reply": _answer("Renewal at risk.")},
        "two": {"call": "askModel", "args": [ASK, {"alias": "second", "maxTokens": 1200}],
                "reply": _answer("Renewal at risk.")},
    })
    assert got["one"]["result"] == got["two"]["result"] == "Renewal at risk."
    assert got["one"]["requests"] == got["two"]["requests"]
    sent = got["one"]["requests"][0]
    assert (sent["model"], sent["max_tokens"], sent["messages"]) == ("second", 1200, ASK)


@pytest.mark.parametrize("template", TEMPLATES)
def test_ask_json_accepts_one_object_holding_the_messages_and_the_options(template: str):
    opts = {"alias": "second", "fallback": FALLBACK, "schemaHint": "{ summary: string }"}
    got = _run(template, {
        "one": {"call": "askJson", "args": [{"messages": ASK, **opts}], "reply": _answer('{"summary": "x"}')},
        "two": {"call": "askJson", "args": [ASK, opts], "reply": _answer('{"summary": "x"}')},
    })
    assert got["one"]["result"] == got["two"]["result"]
    assert got["one"]["result"]["value"] == {"summary": "x"}
    assert got["one"]["requests"] == got["two"]["requests"]
    assert got["one"]["requests"][0]["model"] == "second"
