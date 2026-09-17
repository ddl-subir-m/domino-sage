"""Every real-OpenCode boot in the suite goes through one lock, and no site escapes it.

Two files booted the pinned binary: one behind a machine-global `flock`, one inline with none. The
race it made is not reproducible on an idle machine, so these two tests check the two conditions
that make it impossible instead — the lock is held across a boot, and there is no second way to
start one. The escape check is the guard against a THIRD boot site, which is the failure the
one-sided lock was an instance of.

The lock test boots a stub, not OpenCode. It is about `flock`, not about OpenCode, and a stub keeps
it outside the `BINARY.exists()` skip and off the suite slot.
"""

import fcntl
import os
import re
import sys
import threading
from pathlib import Path

from . import opencode_server
from .opencode_server import _opencode_server

TESTS = Path(__file__).resolve().parent

# The pinned binary's path, in the two spellings a `Path` expression for it takes. A site that
# reaches the binary by some third route — building the path a character at a time, or a shell
# string — is outside this check's reach; these are the spellings the two real sites used.
BINARY_PATH = re.compile(r'''\.bin["']\s*/\s*["']opencode|\.bin/opencode''')
LAUNCH = re.compile(r"\b(?:Popen|run|call|check_call|check_output)\(")


def _stub(tmp_path):
    """A binary that answers `/global/health`, so the lock test costs a python boot, not OpenCode's."""
    stub = tmp_path / "stub-opencode"
    stub.write_text(
        f"#!{sys.executable}\n"
        "import sys\n"
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


def test_a_boot_waits_while_another_holds_the_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(opencode_server, "BINARY", _stub(tmp_path))
    lock_path = tmp_path / "opencode.lock"
    monkeypatch.setenv("SAGE_OPENCODE_TEST_LOCK", str(lock_path))
    runtime = tmp_path / "runtime"
    runtime.mkdir()

    entered, finish = threading.Event(), threading.Event()
    failures = []

    def boot():
        try:
            with _opencode_server(runtime, dict(os.environ)):
                entered.set()
                finish.wait(60)
        except BaseException as error:
            failures.append(repr(error))
            entered.set()

    waiter = threading.Thread(target=boot, daemon=True)
    with lock_path.open("w") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        waiter.start()
        assert not entered.wait(2), "a boot started while another held the lock"
        fcntl.flock(held, fcntl.LOCK_UN)
        assert entered.wait(60), "a boot never started after the lock was released"
    finish.set()
    waiter.join(timeout=60)
    assert not waiter.is_alive()
    assert failures == []


def test_no_boot_site_sits_outside_the_shared_helper():
    escapes = []
    for path in sorted(TESTS.rglob("*.py")):
        if path.name == "opencode_server.py":
            continue
        lines = path.read_text().split("\n")
        # Launching a `BINARY` imported from the helper is a boot outright. Spelling the binary's
        # own path is only a boot in a file that also launches something — which is why the two
        # patterns above can sit in this file without tripping it.
        launches = any(LAUNCH.search(line) for line in lines)
        for number, line in enumerate(lines, 1):
            imported = LAUNCH.search(line) and "BINARY" in line
            if imported or (launches and BINARY_PATH.search(line)):
                escapes.append(f"{path.relative_to(TESTS)}:{number}: {line.strip()}")
    assert escapes == [], (
        "These lines reach the OpenCode binary outside `opencode_server._opencode_server`. "
        "A boot that does not take its lock races every other real-OpenCode test on the machine. "
        "Enter the shared helper and keep this test's own teardown at the call site:\n"
        + "\n".join(escapes)
    )
