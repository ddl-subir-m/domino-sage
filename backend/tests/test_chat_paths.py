from sage.shim.chat_paths import chat_path_allowed, strip_denied_writes, write_path_from_tool_call


def test_chat_path_allows_only_this_thread_examples_and_meta():
    tid = "thr_01abc"
    assert chat_path_allowed(f"examples/{tid}/exposure.png", tid)
    assert chat_path_allowed(f"examples/{tid}/limits.table.json", tid)
    assert chat_path_allowed(f".sage/threads/{tid}/context.json", tid)
    assert not chat_path_allowed("src/App.tsx", tid)
    assert not chat_path_allowed("public/data/x.csv", tid)
    assert not chat_path_allowed("package.json", tid)
    assert not chat_path_allowed("AGENTS.md", tid)
    assert not chat_path_allowed(".sage/plan.md", tid)
    assert not chat_path_allowed(".sage/history.jsonl", tid)
    assert not chat_path_allowed("examples/thr_other/x.png", tid)
    assert not chat_path_allowed(f"examples/{tid}", tid)  # the dir itself, not a file under it


def test_chat_path_strips_workspace_prefixes():
    tid = "thr_01abc"
    assert chat_path_allowed(f"/mnt/code/examples/{tid}/a.png", tid)
    assert not chat_path_allowed("/mnt/code/src/App.tsx", tid)


def test_write_path_from_openai_tool_call():
    call = {"id": "c1", "function": {"name": "write", "arguments": '{"filePath": "src/App.tsx"}'}}
    assert write_path_from_tool_call(call) == "src/App.tsx"


def test_strip_denied_writes_drops_src_tool_calls_and_results():
    tid = "thr_01abc"
    messages = [
        {"role": "assistant", "tool_calls": [
            {"id": "bad", "function": {"name": "write", "arguments": '{"filePath": "src/App.tsx"}'}},
            {"id": "ok", "function": {"name": "write",
                                     "arguments": '{"filePath": "examples/thr_01abc/t.table.json"}'}},
        ]},
        {"role": "tool", "tool_call_id": "bad", "content": "wrote"},
        {"role": "tool", "tool_call_id": "ok", "content": "wrote"},
    ]
    out = strip_denied_writes(messages, tid)
    calls = out[0]["tool_calls"]
    assert [c["id"] for c in calls] == ["ok"]
    assert [m.get("tool_call_id") for m in out if m.get("role") == "tool"] == ["ok"]
