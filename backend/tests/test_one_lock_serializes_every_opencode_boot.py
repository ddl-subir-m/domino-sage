"""Every real-OpenCode boot in the suite goes through one lock, and no site escapes it.

Two files booted the pinned binary: one behind a machine-global `flock`, one inline with none. The
race it made is not reproducible on an idle machine, so these tests check the conditions that make
it impossible instead — one boot at a time, from spawn to shutdown, with no second way to start one.
The escape check is the guard against a THIRD boot site, which is the failure the one-sided lock was
an instance of.

The lock tests boot a stub, not OpenCode. They are about `flock`, not about OpenCode, so a stub
keeps them outside the `BINARY.exists()` skip and off the machine's one suite slot.

What each lock test would let through if it were written the obvious way, and why it is not:
a test that watches only the `with` body proves the BODY waited, not that nothing was SPAWNED — and
a second OpenCode competes for the machine from the moment it is spawned, not from the moment it is
healthy. So both tests watch a marker the stub writes as its first act.
"""

import ast
import fcntl
import os
import sys
import threading
from pathlib import Path

from . import opencode_server
from .opencode_server import _opencode_server

TESTS = Path(__file__).resolve().parent
HELPER = TESTS / "opencode_server.py"

LAUNCHERS = {"Popen", "run", "call", "check_call", "check_output"}
# The pinned binary's path, taken from the helper rather than spelled here, so this file holds no
# literal that its own check could match and so the check follows the binary if it ever moves.
PATH_PARTS = {opencode_server.BINARY.parent.name, opencode_server.BINARY.name}
PATH_JOINED = "/".join(opencode_server.BINARY.parts[-2:])


def _stub(tmp_path):
    """A binary that answers `/global/health`, so a lock test costs a python boot, not OpenCode's.

    It touches `spawned` in its working directory before anything else, which is how a test tells
    "the helper waited" from "the helper spawned a competitor and then waited"."""
    stub = tmp_path / "stub-opencode"
    stub.write_text(
        f"#!{sys.executable}\n"
        "import sys\n"
        "open('spawned', 'w').close()\n"
        "from http.server import BaseHTTPRequestHandler, HTTPServer\n"
        "port = int(sys.argv[sys.argv.index('--port') + 1])\n"
        "class H(BaseHTTPRequestHandler):\n"
        "    def do_GET(self):\n"
        "        self.send_response(200)\n"
        "        self.send_header('Content-Length', '2')\n"
        "        self.end_headers()\n"
        "        self.wfile.write(b'{}')\n"
        "    def log_message(self, *ignored):\n"
        "        pass\n"
        "HTTPServer(('127.0.0.1', port), H).serve_forever()\n"
    )
    stub.chmod(0o755)
    return stub


def _runtime(tmp_path, name):
    runtime = tmp_path / name
    runtime.mkdir()
    return runtime, runtime / "spawned"


def _booter(runtime, entered, finish, failures):
    """A thread body that enters the helper, says so, and holds the server until told to stop."""
    def boot():
        try:
            with _opencode_server(runtime, dict(os.environ)):
                entered.set()
                finish.wait(60)
        except BaseException as error:
            failures.append(repr(error))
            entered.set()
    return threading.Thread(target=boot, daemon=True)


def test_a_boot_waits_while_another_holds_the_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(opencode_server, "BINARY", _stub(tmp_path))
    lock_path = tmp_path / "opencode.lock"
    monkeypatch.setenv("SAGE_OPENCODE_TEST_LOCK", str(lock_path))
    runtime, spawned = _runtime(tmp_path, "runtime")
    entered, finish, failures = threading.Event(), threading.Event(), []
    waiter = _booter(runtime, entered, finish, failures)

    try:
        with lock_path.open("w") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            waiter.start()
            # `failures` is in every message: the booter sets `entered` on its way out of a
            # crash too, so a stub that never ran at all would otherwise read as a broken lock.
            assert not entered.wait(2), f"a boot ran while another held the lock: {failures}"
            assert not spawned.exists(), f"a boot was SPAWNED while another held it: {failures}"
            fcntl.flock(held, fcntl.LOCK_UN)
            assert entered.wait(60), f"a boot never ran after the lock was released: {failures}"
    finally:
        finish.set()
        waiter.join(timeout=60)
    assert not waiter.is_alive()
    assert failures == []


def test_the_lock_is_held_until_the_server_is_gone(tmp_path, monkeypatch):
    """The lock covers the whole server lifetime, not the boot.

    A boot that has not yet answered `/global/health` is still a live OpenCode competing for the
    machine, so `LOCK_UN` sits in the helper's `finally` after `terminate()`. That costs wall clock
    — every real-OpenCode test runs strictly in series — and it is the deliberate answer, recorded
    here so narrowing the lock back to "until healthy" cannot pass silently."""
    monkeypatch.setattr(opencode_server, "BINARY", _stub(tmp_path))
    lock_path = tmp_path / "opencode.lock"
    monkeypatch.setenv("SAGE_OPENCODE_TEST_LOCK", str(lock_path))
    first, _ = _runtime(tmp_path, "first")
    second, second_spawned = _runtime(tmp_path, "second")
    serving, stop_serving, failures = threading.Event(), threading.Event(), []
    waiting, stop_waiting = threading.Event(), threading.Event()
    holder = _booter(first, serving, stop_serving, failures)
    latecomer = _booter(second, waiting, stop_waiting, failures)

    try:
        holder.start()
        assert serving.wait(60), f"the first boot never served: {failures}"
        latecomer.start()
        assert not waiting.wait(2), f"a second boot ran while the first server was up: {failures}"
        assert not second_spawned.exists(), f"a second boot was SPAWNED while it was up: {failures}"
        stop_serving.set()
        assert waiting.wait(60), f"the second boot never ran after the first shut down: {failures}"
    finally:
        stop_serving.set()
        stop_waiting.set()
        holder.join(timeout=60)
        latecomer.join(timeout=60)
    assert not holder.is_alive() and not latecomer.is_alive()
    assert failures == []


def _reaches(node, names):
    """True when this expression mentions one of `names`, as a bare name or as an attribute.

    Attributes matter: `opencode_server.BINARY` is how a file that imports the MODULE reaches the
    binary, and it is the spelling this very file uses."""
    return any((isinstance(n, ast.Name) and n.id in names)
               or (isinstance(n, ast.Attribute) and n.attr in names) for n in ast.walk(node))


def _binary_names(tree):
    """Every name in this module that reaches the pinned binary's path, through renames too."""
    names = {"BINARY"}
    while True:
        # Only the TARGETS are bound. Binding every name in the assignment would drag `str` and
        # `Path` in with it, and then an unrelated `subprocess.run(["node", ...])` in the same file
        # would be reported as an OpenCode boot — a red whose message points at the wrong repair.
        grown = set(names)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            if _reaches(node.value, names) or _builds_path(node.value):
                for target in node.targets:
                    grown |= {n.id for n in ast.walk(target) if isinstance(n, ast.Name)}
                    grown |= {n.attr for n in ast.walk(target) if isinstance(n, ast.Attribute)}
        if grown == names:
            return names
        names = grown


def _builds_path(node):
    """True when this one expression spells the binary's path, out of parts or joined."""
    parts = {n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    return PATH_PARTS <= parts or any(PATH_JOINED in part for part in parts)


def _escapes(path):
    """Lines in `path` that reach the OpenCode binary without going through the helper.

    Read as a syntax tree, not as lines: the line-matching version of this check could not see
    a `Popen(` whose argv wrapped onto the next line, which is what any formatter produces.
    A site that reaches the binary through something this cannot follow — a name it received as
    an argument, a shell string — is outside its reach. It catches the shapes a boot site is
    actually written in."""
    tree = ast.parse(path.read_text())
    names = _binary_names(tree)
    found = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.BinOp, ast.Call)) and _builds_path(node):
            found[node.lineno] = "spells the OpenCode binary's own path"
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        called = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if called in LAUNCHERS and _reaches(node, names):
            found[node.lineno] = f"launches it with `{called}`"
    return [f"{path.relative_to(TESTS)}:{line}: {why}" for line, why in sorted(found.items())]


def test_no_boot_site_sits_outside_the_shared_helper():
    escapes = []
    for path in sorted(TESTS.rglob("*.py")):
        if path.resolve() != HELPER:
            escapes += _escapes(path)
    assert escapes == [], (
        "These reach the OpenCode binary outside `opencode_server._opencode_server`. A boot that "
        "does not take its lock races every other real-OpenCode test on the machine. Enter the "
        "shared helper, and keep this test's own teardown at the call site:\n" + "\n".join(escapes)
    )
