"""What stands in the place of withheld data must not read back to the model as its own work.

#507 made the replacement a VALID instance of the value it replaced — a `bash` call kept its
`command`, so it was no longer refused for a missing key. #510 is the other half of that same
sentence, measured live on `03826eb`: the replacement was valid and it was a NO-OP, and the model,
reading it back as its own prior command, ran it five times until the repeated-call guard stopped
the turn, and wrote it as the body of four heredocs.

Two properties, and the first cannot carry the contract on its own:

    the marker says what it is        lowers the odds; nothing written in a command slot could
                                      make imitation impossible, because anything runnable is
                                      imitable
    the echo is answered              the closer. The shim cannot refuse an execution, so the
                                      model has already run it; the tool RESULT is the seam

The third test here is not about imitation. It is the same defect one layer over: a placeholder
that collapses two different steps into one string takes away the model's own record of which
steps it has taken.
"""

import copy
import json
import logging
from types import SimpleNamespace

from sage.driver.opencode import with_attachment_listing
from sage.liveread import data_use as data_use_module
from sage.liveread.data_use import DataUse

PATH = "public/data/upload/uploads/sales.csv"
OTHER = "public/data/upload/uploads/returns.csv"
SALES = "region,revenue,email\n" + "".join(
    f"{'North' if i % 2 == 0 else 'South'},{(i + 1) * 10},person{i}@example.invalid\n"
    for i in range(12))


def prompt(paths=(PATH,)):
    return with_attachment_listing(
        "what is in the uploads",
        [{"path": path, "name": path.rsplit("/", 1)[-1], "summary": "CSV - 3 columns, 12 rows",
          "detail": "columns: region, revenue, email"} for path in paths],
        chat=True,
    )


def bash(cid, command):
    return {"role": "assistant", "tool_calls": [{"id": cid, "type": "function", "function": {
        "name": "bash", "arguments": json.dumps({"command": command, "description": "a step"})}}]}


def marker_the_model_is_shown(data, command, cid="call1"):
    """The command slot as `prepare` rewrites it — read off the real path, never written here."""
    prepared, _used = data.prepare({"messages": [
        {"role": "user", "content": prompt()},
        bash(cid, command),
        {"role": "tool", "tool_call_id": cid, "content": SALES + "\nCommand exited with code 0."},
    ]})
    sent = prepared["messages"][1]["tool_calls"][0]["function"]["arguments"]
    return json.loads(sent)["command"]


def test_the_command_a_model_copied_off_a_placeholder_is_answered_not_receipted():
    """The measured loop, end to end: take what the model is shown, hand it back as its own next
    command, and assert on what comes back."""
    data = DataUse()
    mark = marker_the_model_is_shown(data, "cat " + PATH)
    assert mark.startswith("#"), "#507's contract: the command slot still holds a valid command"
    assert "not a command" in mark, "and it now says what it is"

    prepared, _used = data.prepare({"messages": [
        {"role": "user", "content": prompt()},
        bash("call1", "cat " + PATH),
        {"role": "tool", "tool_call_id": "call1", "content": SALES},
        bash("call2", mark),
        {"role": "tool", "tool_call_id": "call2", "content": "\nCommand exited with code 0."},
    ]})

    answer = prepared["messages"][-1]["content"]
    sent = prepared["messages"][-2]["tool_calls"][0]["function"]["arguments"]
    sent_args = json.loads(sent)
    assert isinstance(answer, str)
    assert "local data withheld" not in sent, "the repaired history cannot teach the marker again"
    assert "command" in sent_args, "the tool-call schema stays valid"
    assert sent_args["description"] != "a step", "repair must not undo the earlier data redaction"
    assert "not a command" in answer and "already ran" in answer
    assert "did nothing" not in answer, "the rewrite cannot undo a tool execution"
    assert not _is_receipt(answer), (
        "a receipt reports a local execution that succeeded — which is the model's imitation "
        "being confirmed to it")


def test_a_heredoc_that_carries_the_marker_is_answered_too():
    """Four of the nine measured calls were this shape: the marker as a file's CONTENTS, not as
    the whole command. A test keyed on equality with the marker would pass and mean nothing."""
    data = DataUse()
    written = "cat > static/tflTable.js << EOF\n# <local data withheld: N sources>\nEOF\necho ok"

    prepared, _used = data.prepare({"messages": [
        {"role": "user", "content": prompt()},
        bash("call1", written),
        {"role": "tool", "tool_call_id": "call1", "content": "ok\nCommand exited with code 0."},
    ]})

    assert "not a command" in prepared["messages"][-1]["content"]


def test_a_tool_that_is_not_bash_is_answered_on_the_same_terms():
    """`bash` is where it was measured, not what the defect is about. Every sanitised string
    argument is a slot the model can imitate, so the guard is drawn around the placeholder."""
    data = DataUse()
    prepared, _used = data.prepare({"messages": [
        {"role": "user", "content": prompt()},
        {"role": "assistant", "tool_calls": [{"id": "call1", "type": "function", "function": {
            "name": "write", "arguments": json.dumps(
                {"filePath": "app/table.js", "content": "[local data withheld: 2 sources]"})}}]},
        {"role": "tool", "tool_call_id": "call1", "content": "wrote app/table.js"},
    ]})

    assert "not a command" in prepared["messages"][-1]["content"]


def test_two_commands_over_two_files_do_not_read_back_as_one_command_run_twice():
    """The model keys its own history on what it can see. Collapsing distinct steps into one
    string is how it loses track of which steps it has taken."""
    data = DataUse()
    prepared, _used = data.prepare({"messages": [
        {"role": "user", "content": prompt((PATH, OTHER))},
        bash("call1", "wc -l " + PATH),
        {"role": "tool", "tool_call_id": "call1", "content": SALES},
        bash("call2", "wc -l " + OTHER),
        {"role": "tool", "tool_call_id": "call2", "content": SALES},
    ]})

    first = json.loads(prepared["messages"][1]["tool_calls"][0]["function"]["arguments"])["command"]
    second = json.loads(prepared["messages"][3]["tool_calls"][0]["function"]["arguments"])["command"]
    assert first != second, "two steps over two files are two steps"
    assert PATH in first and OTHER in second, "each names the source it stood in for"
    assert "person0@example.invalid" not in json.dumps(prepared["messages"]), "still withheld"


def test_a_call_with_no_named_source_does_not_claim_zero_sources():
    """The branch that withholds a call for QUOTING local output has no source path to name, and
    printed "0 sources" — which reads as a defect, and was the count the model copied."""
    data = DataUse()
    row = "North,10,person0@example.invalid"
    prepared, _used = data.prepare({"messages": [
        {"role": "user", "content": prompt()},
        {"role": "assistant", "tool_calls": [{"id": "call1", "type": "function", "function": {
            "name": "read", "arguments": json.dumps({"filePath": PATH})}}]},
        {"role": "tool", "tool_call_id": "call1", "content": SALES},
        bash("call2", f"echo '{row}' >> totals.csv"),
        {"role": "tool", "tool_call_id": "call2", "content": "\nCommand exited with code 0."},
    ]})

    sent = json.loads(prepared["messages"][3]["tool_calls"][0]["function"]["arguments"])
    # `description` rather than `command`: the count lives in the VALUE form, and the command slot
    # takes the branch that names sources and never prints a count. Asserting on the command here
    # passed against a plant that restored "0 sources", which is how this line was found.
    assert sent["description"] == "[local data withheld]", "nothing was withheld from zero sources"
    assert "local data withheld" in sent["command"], "the command is still marked as withheld"
    assert "0 source" not in json.dumps(sent), "no slot claims a count it does not have"
    assert row not in json.dumps(prepared["messages"]), "still withheld"


def test_an_ordinary_command_is_left_to_the_branch_that_owns_it(caplog):
    """The guard keys on a string this module emits. A turn that never meets a placeholder must
    not be able to tell this change is here."""
    data = DataUse()
    prepared, _used = data.prepare({"messages": [
        {"role": "user", "content": prompt()},
        bash("call1", "cat " + PATH),
        {"role": "tool", "tool_call_id": "call1", "content": SALES + "\nCommand exited with code 0."},
    ]})

    answer = prepared["messages"][-1]["content"]
    assert _is_receipt(answer), "a real local execution still gets its receipt"
    assert "not a command" not in json.dumps(answer)
    assert not [r for r in caplog.records if r.name == "sage.liveread"]


def test_marker_warnings_report_each_new_echo_once_without_private_content(caplog, monkeypatch):
    data = DataUse()
    monkeypatch.setattr(data_use_module.timing, "current", lambda: SimpleNamespace(
        turn_id="turn_safe", calls=[SimpleNamespace(n=7)]))
    request = {"messages": [
        bash("private-call-id", "echo 'local data withheld; private-command-value'"),
        {"role": "tool", "tool_call_id": "private-call-id", "content": "private-result-value"},
        bash("second-private-id", "# <local data withheld: 0 sources>"),
        {"role": "tool", "tool_call_id": "second-private-id", "content": "private-data-row"},
    ]}
    original = copy.deepcopy(request)
    with caplog.at_level(logging.WARNING, logger="sage.liveread"):
        for _ in range(2):
            data.prepare(request)
    records = [r for r in caplog.records if r.name == "sage.liveread"]
    assert len(records) == 2, "one warning per newly completed echo, never on replay"
    assert all(r.levelno == logging.WARNING for r in records)
    assert all("new_marker_echoes=1" in r.message for r in records)
    assert ["observed_marker_echoes=1" in records[0].message,
            "observed_marker_echoes=2" in records[1].message] == [True, True]
    assert all("turn_id=turn_safe" in r.message and "model_call_ordinal=7" in r.message
               for r in records)
    assert "private-" not in caplog.text
    assert request == original, "stored history and its real results remain intact"


def test_legitimate_marker_phrase_is_observable_without_claiming_a_noop(caplog):
    data = DataUse()
    prepared, _ = data.prepare({"messages": [
        bash("governance", "printf '%s' 'local data withheld'"),
        {"role": "tool", "tool_call_id": "governance", "content": "local data withheld"},
    ]})
    assert "already ran" in prepared["messages"][-1]["content"]
    assert "may have changed files" in prepared["messages"][-1]["content"]
    assert "new_marker_echoes=1" in caplog.text


def test_completed_echo_arguments_are_repaired_without_mutating_history():
    data = DataUse()
    request = {"messages": [
        {"role": "assistant", "tool_calls": [{"id": "nested", "type": "function",
         "function": {"name": "task", "arguments": json.dumps({
             "prompt": "use local data withheld here",
             "options": ["local data withheld", {"note": "safe"}],
         })}}]},
        {"role": "tool", "tool_call_id": "nested", "content": "private result"},
    ]}
    original = copy.deepcopy(request)

    prepared, _ = data.prepare(request)

    outbound = prepared["messages"][0]["tool_calls"][0]["function"]["arguments"]
    assert "local data withheld" not in outbound
    args = json.loads(outbound)
    assert set(args) == {"prompt", "options"} and isinstance(args["options"], list)
    assert "already ran" in prepared["messages"][1]["content"]
    assert request == original


def _is_receipt(text):
    try:
        body = json.loads(text)
    except (ValueError, TypeError):
        return False
    return isinstance(body, dict) and str(body.get("kind", "")).endswith("_receipt")
