"""A custom tool is added from a TypeScript or Python file, or a git URL (#622).

ADR-0071. A `.ts` tool is copied in as it is, and its id is its filename, as OpenCode derives it. A
`.py` declares `SPEC` and `run(**args)`; Sage runs it with `--spec` to read and check the SPEC, then
generates the `<name>.ts` OpenCode loads, which runs the Python with the arguments as JSON on stdin
and hands back stdout. A non-zero exit is the tool's error. Read-only comes from the SPEC, or is set
on a TypeScript upload, and the panel can change either.
"""
from __future__ import annotations

import json
import shlex
import shutil
import subprocess
import sys
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
    assert "readOnly" not in entry, "a SPEC's readOnly is accepted and means nothing (#636)"
    tools = tmp_path / ".opencode" / "tools"
    assert (tools / "adder.py").read_text() == ADDER
    bridge = (tools / "adder.ts").read_text()
    assert '"Add two numbers."' in bridge
    assert '{"a": {"type": "number"}, "b": {"type": "number"}}' in bridge
    assert "@opencode-ai/plugin" not in bridge, "an npm import makes OpenCode fetch it at runtime"


@needs_python
def test_a_python_tool_is_removed_whole(tmp_path):
    extensions.add(tmp_path, {"kind": "tool", "python": _py()})
    assert extensions.remove(tmp_path, "tool:adder")
    assert list((tmp_path / ".opencode" / "tools").iterdir()) == []


@needs_python
@pytest.mark.parametrize(("source", "said"), [
    ("def run(**args):\n    return 1\n", "declares no SPEC"),
    ("SPEC = {'name': 'adder', 'description': 'Add.'}\n", r"no run\(\*\*args\)"),
    (_py(description=""), "description"),
    (_py(args=["a"]), "args"),
    (_py(args={"a": "number"}), "args"),
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


def _call_bridge(tmp_path: Path, args: dict, name: str = "adder") -> subprocess.CompletedProcess:
    """Run the generated `execute` under node: the bridge is plain JS apart from its extension."""
    tools = tmp_path / ".opencode" / "tools"
    shutil.copy(tools / f"{name}.ts", tools / f"{name}.mjs")
    script = (f"import('./{name}.mjs').then((m) => m.default.execute({json.dumps(args)}))"
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


# ---- the Project's interpreter, not Sage's (#634) ------------------------------------------------

PROJECT_ONLY = '''\
import only_in_project_634

SPEC = {"name": "rate", "description": "The planning rate.", "args": {}, "readOnly": True}


def run():
    return {"rate": only_in_project_634.RATE}
'''


@pytest.fixture()
def project_python(tmp_path_factory):
    """A `python3` that imports a module Sage's own interpreter cannot, as Domino's Project venv
    imports what the Environment and the user installed while Sage runs in `/opt/sage/backend/.venv`."""
    root = tmp_path_factory.mktemp("project-python")
    (root / "site").mkdir()
    (root / "site" / "only_in_project_634.py").write_text("RATE = 0.92\n")
    exe = root / "bin" / "python3"
    exe.parent.mkdir()
    exe.write_text(f"#!/bin/sh\nPYTHONPATH={shlex.quote(str(root / 'site'))} "
                   f"exec {shlex.quote(sys.executable)} \"$@\"\n")
    exe.chmod(0o755)
    return exe


def test_without_the_projects_python_a_tool_sees_only_sages(tmp_path, monkeypatch):
    monkeypatch.delenv("SAGE_PROJECT_PYTHON", raising=False)
    with pytest.raises(extensions.ExtensionError, match="only_in_project_634"):
        extensions.add(tmp_path, {"kind": "tool", "python": PROJECT_ONLY})


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")
def test_a_python_tool_is_read_and_run_by_the_projects_python(tmp_path, monkeypatch,
                                                              project_python):
    monkeypatch.setenv("SAGE_PROJECT_PYTHON", str(project_python))
    entry = extensions.add(tmp_path, {"kind": "tool", "python": PROJECT_ONLY})
    assert entry["id"] == "tool:rate"
    out = _call_bridge(tmp_path, {}, "rate")
    assert out.stdout == 'OK:{"rate": 0.92}', out.stdout + out.stderr


def test_the_bridge_names_no_interpreter_path(tmp_path, monkeypatch, project_python):
    """The bridge is committed with the Project, and the interpreter's path differs per image."""
    monkeypatch.setenv("SAGE_PROJECT_PYTHON", str(project_python))
    extensions.add(tmp_path, {"kind": "tool", "python": PROJECT_ONLY})
    bridge = (tmp_path / ".opencode" / "tools" / "rate.ts").read_text()
    assert str(project_python) not in bridge
    assert "process.env.SAGE_PROJECT_PYTHON" in bridge


APP_SH = Path(__file__).resolve().parents[2] / "environment" / "app.sh"


def test_app_sh_records_the_projects_python_before_it_moves_path():
    """Line by line, `/usr/local/bin:/usr/bin` and then `uv run` each put a python3 that is not the
    Project's ahead of it, so the capture has to come first."""
    sh = APP_SH.read_text()
    [line] = [ln for ln in sh.splitlines() if ln.startswith("export SAGE_PROJECT_PYTHON=")]
    assert sh.index(line) < sh.index('export PATH="/usr/local/bin:/usr/bin:${PATH}"')
    assert sh.index(line) < sh.index("exec uv run")


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is not on PATH")
def test_app_sh_takes_the_first_python3_unless_one_is_named(project_python):
    [line] = [ln for ln in APP_SH.read_text().splitlines()
              if ln.startswith("export SAGE_PROJECT_PYTHON=")]
    run = f'set -euo pipefail\n{line}\nprintf %s "$SAGE_PROJECT_PYTHON"'
    path = f"{project_python.parent}:/usr/bin:/bin"
    found = subprocess.run(["bash", "-c", run], env={"PATH": path},
                           capture_output=True, text=True, check=True)
    assert found.stdout == str(project_python)
    named = subprocess.run(["bash", "-c", run], env={"PATH": path,
                                                     "SAGE_PROJECT_PYTHON": "/named/python3"},
                           capture_output=True, text=True, check=True)
    assert named.stdout == "/named/python3"


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


def test_an_upload_records_its_source_and_no_read_only_mark(tmp_path):
    [entry] = extensions.add_tools(tmp_path, [{"kind": "tool", "name": "lookup", "code": TOOL_TS}],
                                   source={"type": "upload"})
    assert entry["source"] == {"type": "upload"} and "readOnly" not in entry


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


# ---- a manifest written while tools carried a read-only mark ------------------------------------

def test_a_manifest_with_read_only_marks_reads_without_them(tmp_path):
    """#636 dropped the mark. A Project added to before then still has it on disk, and it must
    neither reach the panel nor keep any tool off any turn."""
    (tmp_path / ".opencode").mkdir()
    (tmp_path / ".opencode" / "sage-extensions.json").write_text(json.dumps({"version": 1, "extensions": [
        {"id": "tool:lookup", "kind": "tool", "name": "lookup", "readOnly": True,
         "files": [".opencode/tools/lookup.ts"], "defaultEnabled": True},
        {"id": "mcp:deepwiki", "kind": "mcp", "name": "deepwiki", "files": [],
         "tools": {"read_wiki_structure": False, "ask_wiki_question": False},
         "defaultEnabled": True}]}))
    entries = {e["id"]: e for e in extensions.read_manifest(tmp_path)}
    assert "readOnly" not in entries["tool:lookup"]
    assert entries["mcp:deepwiki"]["tools"] == ["ask_wiki_question", "read_wiki_structure"]
    catalog = extensions.catalog_of(list(entries.values()))
    assert catalog.owner("lookup") == "tool:lookup"
    assert catalog.owner("deepwiki_ask_wiki_question") == "mcp:deepwiki"


# ---- the routes ----------------------------------------------------------------------------------

@needs_python
def test_the_routes_upload_and_import(tmp_path, monkeypatch):
    import sage.orchestrator.app as app_module

    orch, oc, _ = _build(tmp_path, [])
    monkeypatch.setattr(app_module, "orchestrator", orch)
    client = TestClient(app_module.control_app)

    ts = client.post("/api/project/extensions/tools?filename=lookup.ts", content=TOOL_TS.encode())
    assert [e["id"] for e in ts.json()["items"]] == ["tool:lookup"]
    assert oc.disposed, "the new tool reaches the next turn"
    py = client.post("/api/project/extensions/tools?filename=adder.py", content=ADDER.encode())
    assert [e["id"] for e in py.json()["items"]] == ["tool:adder"]
    bad = client.post("/api/project/extensions/tools?filename=x.py", content=_py(args=1).encode())
    assert bad.status_code == 400 and "args" in bad.json()["error"]
    assert client.post("/api/project/extensions/tools/git",
                       json={"url": "ssh://x"}).status_code == 400

    listed = client.get("/api/project/extensions").json()["items"]
    assert {e["id"] for e in listed} == {"tool:lookup", "tool:adder"}
    assert not any("readOnly" in e for e in listed)
    assert client.put("/api/project/extensions/tool:lookup/readOnly",
                      json={"readOnly": True}).status_code in (404, 405), "the override is gone"
