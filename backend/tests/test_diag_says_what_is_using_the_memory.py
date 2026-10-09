"""/api/diag/resources says what is holding the workspace's memory (#738).

The Signal Room workspace ran out of memory twice on 2026-10-09 and Sage had nothing to say about
why: no `/proc` reader, no cgroup reader, and no shell in the workspace to look with. These read a
FAKE `/proc` and cgroup tree, so they hold on any host — the reader takes its roots as arguments,
and the route reads them off module attributes a test can point elsewhere.
"""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from sage import footprint
from sage.driver.opencode import OpenCodeClient
from sage.orchestrator import app as app_module
from sage.preview.queries import CachingExecutor

ME = os.getpid()


def _proc(root: Path, pid: int, ppid: int, rss_kb: int | None, *argv: str, name: str = "") -> None:
    d = root / str(pid)
    d.mkdir(parents=True)
    rss = "" if rss_kb is None else f"VmRSS:\t{rss_kb} kB\n"
    (d / "status").write_text(f"Name:\t{name or Path(argv[0]).name[:15]}\nPPid:\t{ppid}\n{rss}")
    (d / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + (b"\0" if argv else b""))


@pytest.fixture
def proc(tmp_path) -> Path:
    """One workspace's process tree, with every role the ticket names and the shapes that must not
    count: init, a kernel thread, and a process that exited half way through the scan."""
    root = tmp_path / "proc"
    _proc(root, 1, 0, 2_000, "/sbin/tini", "--")
    _proc(root, ME, 1, 300_000, "/opt/venv/bin/python", "-m", "uvicorn", "sage.orchestrator.app")
    (root / str(ME) / "cgroup").write_text("0::/\n")
    _proc(root, 200, ME, 400_000, "/usr/local/bin/opencode", "serve")
    _proc(root, 201, 200, 4_000, "/bin/bash", "-c", "npx tsc --noEmit")
    _proc(root, 202, 201, 250_000, "node", "/w/apps/sales/node_modules/typescript/bin/tsc")
    _proc(root, 203, 200, 30_000, "/w/apps/sales/node_modules/@oxlint/linux-x64/oxlint", ".")
    _proc(root, 300, ME, 120_000, "node", "/w/apps/sales/node_modules/.bin/vite")
    _proc(root, 301, 300, 20_000, "/w/apps/sales/node_modules/@esbuild/linux-x64/bin/esbuild")
    _proc(root, 400, ME, 60_000, "/opt/venv/bin/python", "-m", "uvicorn", "--reload")
    _proc(root, 401, 400, 80_000, "/opt/venv/bin/python", "-c", "from multiprocessing")
    _proc(root, 500, ME, 40_000, "node", "/opt/sage/preview/page_check.mjs")
    _proc(root, 501, 500, 200_000, "/ms-playwright/chromium_headless_shell-1/chrome-linux/"
                                   "headless_shell", "--headless")
    _proc(root, 502, 501, 150_000, "/ms-playwright/chromium-1/chrome-linux/chrome",
          "--type=renderer")
    _proc(root, 600, 2, None, name="kworker/0:1")          # kernel thread: no cmdline, no RSS
    (root / "700").mkdir()                                 # exited between listdir and read
    (root / "self").mkdir()
    return root


def _cgroup_v2(root: Path) -> Path:
    cg = root / "cgroup"
    cg.mkdir()
    (cg / "memory.current").write_text("1073741824\n")
    (cg / "memory.max").write_text("max\n")
    (cg / "memory.events").write_text("low 0\nhigh 0\nmax 3\noom 2\noom_kill 1\n")
    return cg


ROOTS = {ME: "orchestrator", 200: "opencode", 300: "preview:sales", 400: "preview:api"}


def test_each_process_under_sage_is_grouped_by_its_role(proc, tmp_path):
    got = footprint.read(proc, _cgroup_v2(tmp_path), orchestrator_pid=ME, roots=ROOTS)

    groups = {g["role"]: (g["processes"], g["rss_bytes"]) for g in got["groups"]}
    kb = 1024
    assert groups == {
        "orchestrator": (2, (300_000 + 40_000) * kb),  # page_check's node is the orchestrator's
        "opencode": (2, (400_000 + 4_000) * kb),
        "tsc": (1, 250_000 * kb),
        "oxlint": (1, 30_000 * kb),
        "preview:sales": (2, (120_000 + 20_000) * kb),
        "preview:api": (2, (60_000 + 80_000) * kb),
        "chromium": (2, (200_000 + 150_000) * kb),
    }
    assert got["sage_rss_bytes"] == sum(rss for _, rss in groups.values())
    by_pid = {p["pid"]: p for p in got["processes"]}
    assert set(by_pid) == {ME, 200, 201, 202, 203, 300, 301, 400, 401, 500, 501, 502}
    assert by_pid[202] == {"pid": 202, "ppid": 201, "rss_bytes": 250_000 * kb, "cmd": "node",
                           "role": "tsc"}
    assert [g["rss_bytes"] for g in got["groups"]] == sorted(
        (g["rss_bytes"] for g in got["groups"]), reverse=True)


def test_cgroup_v2_memory_and_its_oom_events_are_read(proc, tmp_path):
    got = footprint.read(proc, _cgroup_v2(tmp_path), orchestrator_pid=ME, roots=ROOTS)

    assert got["memory"] == {"cgroup": "v2", "current_bytes": 1073741824, "max_bytes": None,
                             "events": {"oom": 2, "oom_kill": 1}}


def test_cgroup_v2_is_read_at_the_path_the_orchestrator_is_in(proc, tmp_path):
    """Without a cgroup namespace the mount's root is the HOST's and has no `memory.current`; the
    container's numbers sit at the path `/proc/<pid>/cgroup` names."""
    (proc / str(ME) / "cgroup").write_text("0::/kubepods/pod1/c1\n")
    cg = tmp_path / "cgroup"
    leaf = cg / "kubepods" / "pod1" / "c1"
    leaf.mkdir(parents=True)
    (leaf / "memory.current").write_text("5\n")
    (leaf / "memory.max").write_text("8589934592\n")

    got = footprint.read(proc, cg, orchestrator_pid=ME, roots=ROOTS)["memory"]

    assert (got["current_bytes"], got["max_bytes"], got["events"]) == (5, 8589934592, {})


def test_cgroup_v1_memory_is_read_when_there_is_no_v2(proc, tmp_path):
    (proc / str(ME) / "cgroup").write_text("4:memory:/docker/abc\n0::/\n")
    mem = tmp_path / "cgroup" / "memory"
    mem.mkdir(parents=True)
    (mem / "memory.usage_in_bytes").write_text("2048\n")
    (mem / "memory.limit_in_bytes").write_text("9223372036854771712\n")  # v1's "no limit"
    (mem / "memory.oom_control").write_text("oom_kill_disable 0\nunder_oom 0\noom_kill 4\n")

    got = footprint.read(proc, tmp_path / "cgroup", orchestrator_pid=ME, roots=ROOTS)["memory"]

    assert got == {"cgroup": "v1", "current_bytes": 2048, "max_bytes": None,
                   "events": {"under_oom": 0, "oom_kill": 4}}


def test_a_host_with_no_proc_and_no_cgroup_says_so_and_does_not_raise(tmp_path):
    got = footprint.read(tmp_path / "no-proc", tmp_path / "no-cgroup", orchestrator_pid=ME,
                         roots=ROOTS)

    assert got["memory"]["cgroup"] is None and "no-cgroup" in got["memory"]["detail"]
    assert got["processes"] == [] and got["groups"] == [] and "no-proc" in got["detail"]


def test_the_cache_reports_what_it_holds():
    rows = {"rows": [{"a": 1}, {"a": 2}, {"a": 3}]}
    cache = CachingExecutor(lambda query, params: rows)
    cache(SimpleNamespace(name="q", sql="select 1"), {})
    cache(SimpleNamespace(name="q", sql="select 1"), {"x": 1})

    held = cache.footprint()

    assert held["entries"] == 2 and held["rows"] == 6
    assert held["bytes"] == 2 * len('{"rows": [{"a": 1}, {"a": 2}, {"a": 3}]}')


def test_every_session_the_client_creates_is_counted(monkeypatch):
    class _Resp:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"id": "s1"}

    monkeypatch.setattr("sage.driver.opencode.httpx.post", lambda *a, **k: _Resp())
    monkeypatch.setattr("sage.driver.opencode.httpx.patch", lambda *a, **k: _Resp())
    before = OpenCodeClient.sessions_created()

    OpenCodeClient("http://x").create_session("/w/a")
    OpenCodeClient("http://y").create_session("/w/b")  # a restarted server's client counts too

    assert OpenCodeClient.sessions_created() == before + 2


def _view(app_id: str, pid: int | None, *, running: bool, last_traffic: float | None,
          cache: CachingExecutor | None = None):
    sup = SimpleNamespace(_proc=None if pid is None else SimpleNamespace(pid=pid),
                          running=running, last_traffic=last_traffic)
    return SimpleNamespace(workspace=SimpleNamespace(app_id=app_id), supervisor=sup,
                           queries=SimpleNamespace(executor=cache))


@pytest.fixture
def bound(proc, tmp_path, monkeypatch):
    """The route against the fake tree and a project holding three previews."""
    monkeypatch.setattr(footprint, "PROC_ROOT", proc)
    monkeypatch.setattr(footprint, "CGROUP_ROOT", _cgroup_v2(tmp_path))
    cache = CachingExecutor(lambda query, params: {"rows": [{"a": 1}]})
    cache(SimpleNamespace(name="q", sql="s"), {})
    sales = _view("sales", 300, running=True, last_traffic=12.0, cache=cache)
    api = _view("api", 400, running=True, last_traffic=None)
    idle = _view("idle", None, running=False, last_traffic=None)
    project = SimpleNamespace(_views={"sales": sales, "api": api, "idle": idle},
                              _selected_view=sales,
                              shim=SimpleNamespace(data_use=SimpleNamespace(
                                  operations={"op1": (), "op2": ()})))
    monkeypatch.setattr(app_module.orchestrator, "_project", project)
    monkeypatch.setattr(app_module.orchestrator, "_oc_server",
                        SimpleNamespace(_proc=SimpleNamespace(pid=200)))
    return project


def _get() -> dict:
    client = TestClient(app_module.control_app)  # no `with`: the lifespan boots the orchestrator
    try:
        r = client.get("/api/diag/resources")
    finally:
        client.close()
    assert r.status_code == 200, r.text
    return r.json()


def test_the_route_labels_each_preview_tree_with_its_app(bound):
    got = _get()

    roles = {g["role"]: g["processes"] for g in got["groups"]}
    assert roles["preview:sales"] == 2 and roles["preview:api"] == 2 and roles["opencode"] == 2
    assert got["memory"]["max_bytes"] is None and got["memory"]["events"]["oom_kill"] == 1
    previews = got["counts"]["previews"]
    assert (previews["supervisors"], previews["alive"], previews["last_traffic_none"]) == (3, 2, 2)
    assert previews["cap"] == 3
    apps = {a["app_id"]: a for a in previews["apps"]}
    assert apps["sales"]["pid"] == 300 and apps["idle"]["pid"] is None
    assert apps["sales"]["cache"]["entries"] == 1 and apps["api"]["cache"] is None
    assert got["counts"]["data_use_operations"] == 2
    assert isinstance(got["counts"]["opencode_sessions_created"], int)


def test_the_route_never_raises_on_a_project_it_cannot_read(bound, monkeypatch):
    monkeypatch.setattr(app_module.orchestrator, "_project", SimpleNamespace())
    monkeypatch.setattr(app_module.orchestrator, "_oc_server", None)

    got = _get()

    assert "AttributeError" in got["counts"]["previews"]
    assert "AttributeError" in got["counts"]["data_use_operations"]
    assert {g["role"] for g in got["groups"]} >= {"orchestrator"}


def test_the_route_with_no_project_reports_the_process_side(bound, monkeypatch):
    monkeypatch.setattr(app_module.orchestrator, "_project", None)

    got = _get()

    assert got["counts"]["previews"] is None and got["counts"]["data_use_operations"] is None
    assert got["memory"]["cgroup"] == "v2"
