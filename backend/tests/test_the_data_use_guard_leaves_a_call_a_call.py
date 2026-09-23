"""What the data-use guard puts in place of local data must be a valid instance of what it replaced.

The guard has always been asserted on the thing it removes: no row reaches the model. That is half
a promise. The other half is what stands in the removed thing's place, and #507 measured three
sites where the replacement was not a valid instance of the value it replaced:

    `_sanitize_call`      a `bash` call lost its `command` key, so the next call the model wrote
                          in that shape was refused: `SchemaError(Missing key ...`, four times
    the part rewriter     `""` is a `str`, so an EMPTY error became a receipt — a call that
                          succeeded read back as a call that failed
    the message branch    an assistant message became a receipt object, and the model, reading
                          its own last answer as JSON, answered the person in JSON

One test per site. Each asserts the replacement still validates against the shape of the thing it
replaced, which is the assertion the existing files do not make: they check that a receipt is
PRESENT, never that a call is still a call. The withholding itself is asserted in every one of
them too — this contract is in addition to that one, never instead of it.
"""

import json

from sage.driver.opencode import with_attachment_listing
from sage.liveread.data_use import DataUse

PATH = "public/data/upload/uploads/sales.csv"
SALES = "region,revenue,email\n" + "".join(
    f"{'North' if i % 2 == 0 else 'South'},{(i + 1) * 10},person{i}@example.invalid\n"
    for i in range(12))


def prompt():
    return with_attachment_listing(
        "what is in @sales.csv",
        [{"path": PATH, "name": "sales.csv", "summary": "CSV - 3 columns, 12 rows",
          "detail": "columns: region, revenue, email"}],
        chat=True,
    )


def test_a_sanitised_bash_call_is_still_a_valid_bash_call():
    """Site 1. Keys and types are read off the ORIGINAL call rather than a schema written here, so
    this cannot go stale against a tool whose arguments change."""
    data = DataUse()
    arguments = {"command": "cat " + PATH, "description": "show the upload"}
    request = {"messages": [
        {"role": "user", "content": prompt()},
        {"role": "assistant", "tool_calls": [{"id": "call1", "type": "function", "function": {
            "name": "bash", "arguments": json.dumps(arguments)}}]},
        {"role": "tool", "tool_call_id": "call1",
         "content": SALES + "\nCommand exited with code 0."},
    ]}

    prepared, _used = data.prepare(request)

    sent = json.loads(prepared["messages"][1]["tool_calls"][0]["function"]["arguments"])
    assert set(sent) == set(arguments), "every key the tool required is still present"
    for key, value in arguments.items():
        assert type(sent[key]) is type(value), f"{key} kept its type"
    assert sent["command"], "a bash call with an empty command is not a bash call either"
    assert "person0@example.invalid" not in json.dumps(prepared["messages"]), "still withheld"


def test_an_empty_error_on_a_part_that_succeeded_stays_empty():
    """Site 2. `""` is a `str`, and writing a receipt into it turns a successful call into a
    failed one in the only record the model has of it."""
    data = DataUse()
    request = {"messages": [
        {"role": "user", "content": prompt()},
        {"role": "assistant", "content": [{"type": "tool", "tool": "bash", "state": {
            "status": "completed",
            "input": {"command": "python total.py " + PATH},
            "output": SALES,
            "error": "",
            "metadata": {"output": SALES, "error": ""},
        }}]},
    ]}

    prepared, _used = data.prepare(request)

    state = prepared["messages"][1]["content"][0]["state"]
    assert state["error"] == "", "an empty error is what a call that succeeded looks like"
    assert state["metadata"]["error"] == "", "and the same holds one level down"
    assert json.loads(state["output"])["kind"] == "local_execution_receipt", "output still withheld"
    assert "person0@example.invalid" not in json.dumps(prepared["messages"]), "still withheld"


def test_a_message_that_quoted_local_output_is_still_a_message():
    """Site 3. A message whose content is a receipt object is not a message the model can imitate
    safely — the measured consequence was the person being shown the receipt as their answer."""
    data = DataUse()
    request = {"messages": [
        {"role": "user", "content": prompt()},
        {"role": "assistant", "tool_calls": [{"id": "call1", "type": "function", "function": {
            "name": "read", "arguments": json.dumps({"filePath": PATH})}}]},
        {"role": "tool", "tool_call_id": "call1", "content": SALES},
        {"role": "assistant", "content": [
            {"type": "text", "text": "Here is the file:\n" + SALES},
            {"type": "reasoning", "text": "The rows say " + SALES},
            {"type": "text", "text": "I will total the revenue next."},
        ]},
        {"role": "user", "content": "Background task completed with:\n" + SALES},
    ]}

    prepared, _used = data.prepare(request)

    parts = prepared["messages"][3]["content"]
    assert [p["type"] for p in parts] == ["text", "reasoning", "text"], "every part is still there"
    for part in parts:
        assert isinstance(part["text"], str) and part["text"]
        assert not _is_receipt(part["text"]), "text was replaced by an object the model imitates"
    later = prepared["messages"][4]["content"]
    assert isinstance(later, str) and not _is_receipt(later), "a string message stays a string"
    assert "person0@example.invalid" not in json.dumps(prepared["messages"]), "still withheld"


def _is_receipt(text):
    try:
        body = json.loads(text)
    except ValueError:
        return False
    return isinstance(body, dict) and str(body.get("kind", "")).endswith("_receipt")
