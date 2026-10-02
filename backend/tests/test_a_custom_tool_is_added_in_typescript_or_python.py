"""A custom tool is added from a TypeScript or Python file, or a git URL (#622).

ADR-0071. A `.ts` tool is copied in as it is, and its id is its filename, as OpenCode derives it. A
`.py` declares `SPEC` and `run(**args)`; Sage runs it with `--spec` to read and check the SPEC, then
generates the `<name>.ts` OpenCode loads, which runs the Python with the arguments as JSON on stdin
and hands back stdout. A non-zero exit is the tool's error. Read-only comes from the SPEC, or is set
on a TypeScript upload, and the panel can change either.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sage import extensions

from .test_no_edit_recovery_uses_the_active_stack import _no_waiting  # noqa: F401  (autouse)
from .test_turn_path import _build

TOOL_TS = "export default { description: 'x', args: {}, async execute() { return 'ok' } }\n"
ADDER = '''\
SPEC = {
    "name": "adder",
    "description": "Add two numbers.",
    "args": {"a": {"type": "number"}, "b": {"type": "number"}},
    "readOnly": True,
}


def run(a, b):
    if a < 0:
        raise ValueError("adder refuses negative numbers")
    return {"sum": a + b}
'''

needs_python = pytest.mark.skipif(shutil.which("python3") is None, reason="python3 is not on PATH")


def _py(**spec) -> str:
    base = {"name": "adder", "description": "Add.", "args": {}}
    return f"SPEC = {json.dumps({**base, **spec})!s}\n\ndef run(**args):\n    return 'ok'\n"


# ---- Python: the SPEC and the bridge -------------------------------------------------------------

@needs_python
def test_a_python_tool_is_read_by_its_spec_and_gets_a_generated_bridge(tmp_path):
    entry = extensions.add(tmp_path, {"kind": "tool", "python": ADDER})
    assert entry["id"] == "tool:adder"
    assert entry["files"] == [".opencode/tools/adder.py", ".opencode/tools/adder.ts"]
    assert entry["readOnly"] is True, "readOnly comes from the SPEC"
    tools = tmp_path / ".opencode" / "tools"
    assert (tools / "adder.py").read_text() == ADDER
    bridge = (tools / "adder.ts").read_text()
    assert '"Add two numbers."' in bridge
    assert '{"a": {"type": "number"}, "b": {"type": "number"}}' in bridge
    assert "@opencode-ai/plugin" not in bridge, "an npm import makes OpenCode fetch it at runtime"


@needs_python
def test_read_only_defaults_to_false_and_a_python_tool_is_removed_whole(tmp_path):
    entry = extensions.add(tmp_path, {"kind": "tool", "python": _py()})
    assert entry["readOnly"] is False
    assert extensions.remove(tmp_path, "tool:adder")
    assert list((tmp_path / ".opencode" / "tools").iterdir()) == []


@needs_python
@pytest.mark.parametrize(("source", "said"), [
    ("def run(**args):\n    return 1\n", "declares no SPEC"),
    ("SPEC = {'name': 'adder', 'description': 'Add.'}\n", r"no run\(\*\*args\)"),
    (_py(description=""), "description"),
    (_py(args=["a"]), "args"),
    (_py(args={"a": "number"}), "args"),
    (_py(readOnly="yes"), "readOnly"),
    (_py(name="Adder"), "lowercase"),
    (_py(name="bash"), "built-in"),
    ("SPEC = {\n", "SyntaxError"),
    ("import no_such_module_622\n", "ModuleNotFoundError"),
])
def test_a_python_tool_sage_cannot_use_is_refused_with_the_reason(tmp_path, source, said):
    with pytest.raises(extensions.ExtensionError, match=said):
        extensions.add(tmp_path, {"kind": "tool", "python": source})
    assert extensions.read_manifest(tmp_path) == []
    assert not (tmp_path / ".opencode" / "tools").exists() or \
        list((tmp_path / ".opencode" / "tools").iterdir()) == []


@needs_python
def test_a_name_given_beside_a_python_tool_must_match_its_spec(tmp_path):
    with pytest.raises(extensions.ExtensionError, match="SPEC"):
        extensions.add(tmp_path, {"kind": "tool", "name": "summer", "python": ADDER})


@needs_python
def test_a_python_tool_never_overwrites_a_file_sage_did_not_write(tmp_path):
    (tmp_path / ".opencode" / "tools").mkdir(parents=True)
    (tmp_path / ".opencode" / "tools" / "adder.ts").write_text("// theirs\n")
    with pytest.raises(extensions.ExtensionError, match="not Sage's"):
        extensions.add(tmp_path, {"kind": "tool", "python": ADDER})
    assert not (tmp_path / ".opencode" / "tools" / "adder.py").exists()


def _call_bridge(tmp_path: Path, args: dict) -> subprocess.CompletedProcess:
    """Run the generated `execute` under node: the bridge is plain JS apart from its extension."""
    tools = tmp_path / ".opencode" / "tools"
    shutil.copy(tools / "adder.ts", tools / "adder.mjs")
    script = (f"import('./adder.mjs').then((m) => m.default.execute({json.dumps(args)}))"
              ".then((out) => process.stdout.write('OK:' + out),"
              " (err) => process.stdout.write('ERR:' + err.message))")
    return subprocess.run(["node", "-e", script], cwd=tools, capture_output=True, text=True,
                          timeout=60, check=False)


@needs_python
@pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")
def test_the_bridge_hands_the_arguments_to_run_and_returns_stdout(tmp_path):
    extensions.add(tmp_path, {"kind": "tool", "python": ADDER})
    out = _call_bridge(tmp_path, {"a": 2, "b": 3})
    assert out.stdout == 'OK:{"sum": 5}', out.stderr


@needs_python
@pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")
def test_an_exception_in_run_is_the_tools_error(tmp_path):
    extensions.add(tmp_path, {"kind": "tool", "python": ADDER})
    out = _call_bridge(tmp_path, {"a": -1, "b": 3})
    assert out.stdout.startswith("ERR:"), out.stdout + out.stderr
    assert "ValueError: adder refuses negative numbers" in out.stdout


# ---- upload and git ------------------------------------------------------------------------------

def test_a_typescript_upload_is_named_by_its_filename():
    assert extensions.tool_in_upload("lookup.ts", TOOL_TS.encode()) == {
        "kind": "tool", "name": "lookup", "code": TOOL_TS}


def test_a_python_upload_is_read_by_its_spec():
    assert extensions.tool_in_upload("whatever.py", ADDER.encode()) == {
        "kind": "tool", "python": ADDER}


@pytest.mark.parametrize(("filename", "data", "said"), [
    ("tool.js", b"x", r"\.ts or a \.py"),
    ("tool.ts", b"\xff\xfe\x00", "not a text file"),
])
def test_an_upload_that_is_not_a_tool_says_why(filename, data, said):
    with pytest.raises(extensions.ExtensionError, match=said):
        extensions.tool_in_upload(filename, data)


@needs_python
def test_an_import_adds_all_of_its_tools_or_none(tmp_path):
    bodies = [{"kind": "tool", "name": "lookup", "code": TOOL_TS},
              {"kind": "tool", "python": _py(description="")}]
    with pytest.raises(extensions.ExtensionError, match="description"):
        extensions.add_tools(tmp_path, bodies)
    assert extensions.read_manifest(tmp_path) == []
    assert not (tmp_path / ".opencode" / "tools" / "lookup.ts").exists()


def test_read_only_set_on_upload_applies_to_typescript_tools(tmp_path):
    [entry] = extensions.add_tools(tmp_path, [{"kind": "tool", "name": "lookup", "code": TOOL_TS}],
                                   read_only=True, source={"type": "upload"})
    assert entry["readOnly"] is True and entry["source"] == {"type": "upload"}


def _origin(tmp_path: Path, files: dict[str, str]) -> tuple[Path, str]:
    origin = tmp_path / "origin"
    for rel, text in files.items():
        (origin / rel).parent.mkdir(parents=True, exist_ok=True)
        (origin / rel).write_text(text)
    git = ["git", "-C", str(origin), "-c", "user.email=t@t", "-c", "user.name=t"]
    subprocess.run(["git", "init", "-q", str(origin)], check=True)
    subprocess.run([*git, "add", "."], check=True)
    subprocess.run([*git, "commit", "-qm", "tools"], check=True)
    head = subprocess.run([*git, "rev-parse", "HEAD"], capture_output=True, text=True,
                          check=True).stdout.strip()
    return origin, head


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not on PATH")
def test_a_git_url_brings_the_tools_in_the_folder_named_and_records_its_commit(tmp_path, monkeypatch):
    origin, head = _origin(tmp_path, {"tools/lookup.ts": TOOL_TS, "tools/adder.py": ADDER,
                                      "tools/README.md": "x", "tools/deep/other.ts": TOOL_TS,
                                      "setup.py": "x"})
    # The https URL is rewritten to the local origin, so the real clone runs with no network.
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", f"url.{origin.as_uri()}.insteadOf")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "https://git.example/team/tools")

    bodies, commit = extensions.tools_from_git("https://git.example/team/tools", "tools")
    assert commit == head
    assert bodies == [{"kind": "tool", "python": ADDER},
                      {"kind": "tool", "name": "lookup", "code": TOOL_TS}]
    one, _ = extensions.tools_from_git("https://git.example/team/tools", "tools/lookup.ts")
    assert [b.get("name") for b in one] == ["lookup"]
    with pytest.raises(extensions.ExtensionError, match="no .ts or .py tool"):
        extensions.tools_from_git("https://git.example/team/tools", "tools/deep/none")


def test_a_git_url_that_is_not_https_or_a_path_outside_it_is_refused():
    with pytest.raises(extensions.ExtensionError, match="https://"):
        extensions.tools_from_git("file:///etc", "")
    with pytest.raises(extensions.ExtensionError, match="not a path"):
        extensions.tools_from_git("https://git.example/x", "../etc")


# ---- read-only, overridden in the panel ----------------------------------------------------------

def test_the_panel_can_change_a_tools_read_only_flag_and_the_catalog_follows(tmp_path):
    extensions.add(tmp_path, {"kind": "tool", "name": "lookup", "code": TOOL_TS})
    assert extensions.load_catalog(tmp_path).owner("lookup").read_only is False
    assert extensions.set_read_only(tmp_path, "tool:lookup", True) is True
    assert extensions.load_catalog(tmp_path).owner("lookup").read_only is True
    assert extensions.set_read_only(tmp_path, "tool:nope", True) is False


def test_only_a_custom_tool_takes_a_read_only_flag(tmp_path):
    extensions.add(tmp_path, {"kind": "skill", "name": "alpha", "files": {
        "SKILL.md": "---\nname: alpha\ndescription: A.\n---\nGo.\n"}})
    with pytest.raises(extensions.ExtensionError, match="custom tool"):
        extensions.set_read_only(tmp_path, "skill:alpha", True)


def test_a_changed_read_only_flag_reaches_the_shim(tmp_path):
    orch, _, _ = _build(tmp_path, [])
    project = orch.project(start_preview=False)
    orch.add_extension({"kind": "tool", "name": "lookup", "code": TOOL_TS})
    orch.set_extension_read_only("tool:lookup", True)
    assert project.control.snapshot().extensions.owner("lookup") == ("tool:lookup", True)
    with pytest.raises(KeyError):
        orch.set_extension_read_only("tool:nope", True)


# ---- the routes ----------------------------------------------------------------------------------

@needs_python
def test_the_routes_upload_import_and_override_read_only(tmp_path, monkeypatch):
    import sage.orchestrator.app as app_module

    orch, oc, _ = _build(tmp_path, [])
    monkeypatch.setattr(app_module, "orchestrator", orch)
    client = TestClient(app_module.control_app)

    ts = client.post("/api/project/extensions/tools?filename=lookup.ts&readOnly=true",
                     content=TOOL_TS.encode())
    assert [(e["id"], e["readOnly"]) for e in ts.json()["items"]] == [("tool:lookup", True)]
    assert oc.disposed, "the new tool reaches the next turn"
    py = client.post("/api/project/extensions/tools?filename=adder.py", content=ADDER.encode())
    assert [(e["id"], e["readOnly"]) for e in py.json()["items"]] == [("tool:adder", True)]
    bad = client.post("/api/project/extensions/tools?filename=x.py", content=_py(args=1).encode())
    assert bad.status_code == 400 and "args" in bad.json()["error"]
    assert client.post("/api/project/extensions/tools/git",
                       json={"url": "ssh://x"}).status_code == 400

    assert client.put("/api/project/extensions/tool:lookup/readOnly",
                      json={"readOnly": False}).json() == {"ok": True}
    listed = {e["id"]: e["readOnly"] for e in client.get("/api/project/extensions").json()["items"]}
    assert listed == {"tool:lookup": False, "tool:adder": True}
    assert client.put("/api/project/extensions/tool:nope/readOnly",
                      json={"readOnly": True}).status_code == 404
