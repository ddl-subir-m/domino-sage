"""/api/diag names the interpreter it is answering from, not just whether its imports worked.

`data_library` has reported a bare yes/no since the cascade first needed one, and on a deployment
where `domino_data` was present it still said "the Domino data library is not installed here" —
because the orchestrator runs from uv's isolated venv while the package ships in the Domino base
image's system python (`resources/provider.py:1281`). One bit cannot tell those two apart, so #399
— a Data Source query that works from the builder's `bash` step and fails from Sage's own process —
had nothing to read.

Every assertion here is about the LIVE process. A path this code computes would agree with itself
in both interpreters, which is exactly the disagreement being hunted, so a test that accepted a
constructed answer would certify the one thing the block exists to rule out.

Run against the real route with `TestClient` rather than by calling `_python_diag()` directly: the
failure being guarded against is a 500 that costs the reader `sage_rev`, `agents`, `mcp` and the
log tail as well, and a helper called in isolation cannot show that.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sage.orchestrator import app as app_module


@pytest.fixture
def diag(tmp_path, monkeypatch):
    """GET /api/diag with HOME pointed somewhere empty, so no real config is read."""
    home = tmp_path / "home"
    (home / ".config" / "opencode").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("SAGE_CONTROL_PORT", "1")  # nothing listening; the probes report that

    def _get() -> dict:
        r = TestClient(app_module.control_app).get("/api/diag")
        assert r.status_code == 200, r.text
        return r.json()

    return _get


@pytest.fixture
def domino_data_somewhere_odd(tmp_path):
    """Install a real, importable `domino_data` in a directory no path guess would produce.

    A real package on `sys.path` rather than an object dropped into `sys.modules`: the claim under
    test is that the reported location comes from resolution, and only an actual resolution can
    fail if the code stops doing one.
    """
    saved_path = list(sys.path)
    saved_modules = {k: v for k, v in sys.modules.items()
                     if k == "domino_data" or k.startswith("domino_data.")}

    def _install(*, version: str | None = "0.0.0-planted", extend_with: str = "") -> Path:
        home = tmp_path / "an-unexpected-corner" / "site-packages"
        (home / "domino_data").mkdir(parents=True)
        init = home / "domino_data" / "__init__.py"
        body = "" if version is None else f'__version__ = "{version}"\n'
        if extend_with:
            body += f'__path__.append({extend_with!r})\n'
        init.write_text(body)
        sys.path.insert(0, str(home))
        for name in list(saved_modules):
            sys.modules.pop(name, None)
        importlib.invalidate_caches()
        return init

    yield _install

    sys.path[:] = saved_path
    for name in [k for k in sys.modules if k == "domino_data" or k.startswith("domino_data.")]:
        del sys.modules[name]
    sys.modules.update(saved_modules)
    importlib.invalidate_caches()


def test_a_pyarrow_that_will_not_import_costs_the_page_nothing(
    diag, monkeypatch, domino_data_somewhere_odd,
):
    """The guarded failure is a 500, not a wrong value.

    Forced with `sys.modules["pyarrow"] = None`, which makes the import raise whichever way the
    ambient environment happens to be built. This tree has no pyarrow installed, so asserting on
    the ambient state would assert nothing — the field would already read as a failure and the
    guard could be absent.

    The sibling fields are asserted in the same breath because the other way to lose the block is
    to wrap the whole of it in one `try`, which reads as handled and reports one error where six
    facts should be.
    """
    planted = domino_data_somewhere_odd()
    monkeypatch.setitem(sys.modules, "pyarrow", None)

    page = diag()
    block = page["python"]

    assert isinstance(block["pyarrow"], str)
    assert "pyarrow" in block["pyarrow"]
    assert "Error" in block["pyarrow"]  # the exception TEXT, not a bare False

    assert block["executable"] == sys.executable
    assert block["version"] == sys.version.split()[0]
    assert block["cwd"]
    assert block["domino_data"]["path"] == str(planted)
    assert "virtual_env" in block

    # The whole reason the guard exists: the rest of the page is what the reader came for. Keys,
    # not values — `sage_rev` is legitimately null off a deployment, and the claim here is that the
    # route still answered, not what it answered.
    assert {"sage_rev", "agents", "mcp", "data_library", "log_tail"} <= set(page)


def test_a_domino_data_that_will_not_import_reports_the_reason_and_keeps_pyarrow(
    diag, monkeypatch,
):
    """The same condition on the other module, because the two are guarded separately.

    A single `try` around both would pass the pyarrow test above and still lose this one.
    """
    monkeypatch.setitem(sys.modules, "domino_data", None)

    block = diag()["python"]

    assert isinstance(block["domino_data"], str)
    assert "domino_data" in block["domino_data"]
    assert block["executable"] == sys.executable
    assert "pyarrow" in block


def test_the_executable_and_cwd_are_read_from_this_process_not_from_the_environment(
    diag, monkeypatch, tmp_path,
):
    """`PWD` and `PYTHONEXECUTABLE` are set to decoys a caller could plausibly have set.

    Both are names an implementation might reasonably reach for, and both are attacker- or
    caller-controlled in the sense that matters here: a Workspace's `bash` step exports `PWD` on
    every `cd`. If the block ever quoted them, it would report the builder's shell while claiming
    to report Sage, which is the exact confusion #399 needs settled.
    """
    where = tmp_path / "a-real-directory"
    where.mkdir()
    monkeypatch.chdir(where)
    monkeypatch.setenv("PWD", "/a/decoy/that/is/not/the/cwd")
    monkeypatch.setenv("PYTHONEXECUTABLE", "/a/decoy/that/is/not/the/interpreter")

    block = diag()["python"]

    assert block["cwd"] == str(Path.cwd())
    assert block["cwd"] != "/a/decoy/that/is/not/the/cwd"
    assert block["executable"] == sys.executable
    assert block["executable"] != "/a/decoy/that/is/not/the/interpreter"


def test_domino_data_is_reported_where_the_import_found_it(diag, domino_data_somewhere_odd):
    """Resolution, not construction.

    The planted package sits under a tmp directory whose name appears in no `sys.prefix`, no
    site-packages layout and no constant in the repo. A block that built its answer from
    `sys.prefix` would report a path that exists and is wrong — which is worse than reporting
    nothing, because it looks like an answer.
    """
    planted = domino_data_somewhere_odd()

    block = diag()["python"]

    assert block["domino_data"]["path"] == str(planted)
    assert "an-unexpected-corner" in block["domino_data"]["path"]
    assert block["domino_data"]["version"] == "0.0.0-planted"
    assert block["domino_data"]["search_paths"] == [str(planted.parent)]


def test_a_package_that_spans_two_directories_reports_both_of_them(
    diag, domino_data_somewhere_odd, tmp_path,
):
    """`__file__` alone names one directory and silently drops the other.

    This is not a curiosity — it is the shape the ticket is for. A `domino_data` assembled with
    `pkgutil.extend_path` has a real `__file__` in whichever copy won the `sys.path` race and the
    other in `__path__`, and the submodules are imported from the second. Reporting the file alone
    would show the venv while the data library came from the base image's system python, which is
    the venv-vs-system-python split named as the wrong answer with full confidence.
    """
    other = tmp_path / "the-system-python" / "domino_data"
    other.mkdir(parents=True)
    planted = domino_data_somewhere_odd(extend_with=str(other))

    block = diag()["python"]

    assert block["domino_data"]["path"] == str(planted)
    assert str(other) in block["domino_data"]["search_paths"]
    assert len(block["domino_data"]["search_paths"]) == 2


def test_a_module_that_declares_no_version_says_so_rather_than_breaking_the_field(
    diag, domino_data_somewhere_odd,
):
    """A missing `__version__` is a fact about the package, not a reason to lose its location."""
    planted = domino_data_somewhere_odd(version=None)

    block = diag()["python"]

    assert block["domino_data"]["path"] == str(planted)
    assert "__version__" in block["domino_data"]["version"]


def test_virtual_env_is_null_when_unset_rather_than_an_empty_string(diag, monkeypatch):
    """A venv that never exported VIRTUAL_ENV and one that exported "" are different facts.

    Reported as JSON null and "" respectively. Collapsing them would leave a reader unable to tell
    "this process is not in a venv" from "something cleared the variable", and the second is the
    interesting one.
    """
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    assert diag()["python"]["virtual_env"] is None

    monkeypatch.setenv("VIRTUAL_ENV", "")
    assert diag()["python"]["virtual_env"] == ""

    monkeypatch.setenv("VIRTUAL_ENV", "/opt/sage/.venv")
    assert diag()["python"]["virtual_env"] == "/opt/sage/.venv"
