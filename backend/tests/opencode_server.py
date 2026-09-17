"""One lock for every real-OpenCode boot in the suite.

Two test files boot the pinned OpenCode binary. One of them took a machine-global `flock` and the
other booted inline with no lock at all, so a serialized group of three raced a fourth boot. That
race is what reds the 60s boot budget with the process still alive: OpenCode has started and simply
has not served `/global/health` yet. Both sites enter here now, so there is one lock.

Not a `conftest.py` fixture. A fixture changes what `--dist load` hands each worker, and the boot
budget this protects was tuned under the current distribution.

The lock is held for the SERVER'S WHOLE LIFETIME, not just its boot: `LOCK_UN` sits in the `finally`
after `terminate()`. That is deliberate. A boot that has not yet answered `/global/health` is still
a live OpenCode competing for the machine, so a lock narrowed to "until healthy" re-opens exactly
the flake this closes. The cost is that every real-OpenCode test runs strictly in series.

The caller keeps its own teardown. This owns the OpenCode process and nothing else — the HTTP
gateway a test stands up, its thread, its control token and its `env` dict all stay at the call
site. A helper that adopted one test's teardown could not serve the next boot site, which is the
failure this module exists to close.
"""

import fcntl
import os
import socket
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest

REPO = Path(__file__).resolve().parents[2]
BINARY = REPO / "node_modules" / ".bin" / "opencode"


@contextmanager
def _opencode_server(runtime, env):
    lock_path = Path(os.environ.get("SAGE_OPENCODE_TEST_LOCK", "/tmp/sage-opencode-test.lock"))
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        log = (runtime / "opencode.log").open("w")
        process = subprocess.Popen([str(BINARY), "serve", "--port", str(port),
                                    "--hostname", "127.0.0.1"],
                                   cwd=runtime, env=env, stdout=log, stderr=log)
        try:
            url = f"http://127.0.0.1:{port}"
            # 60s. 15s was enough only when a boot happened to run first in its worker: OpenCode
            # stays alive and simply has not served `/global/health` yet, so the short budget
            # failed as "did not start". Lowering it again re-opens that, and each measurement of
            # a lower number costs a real boot.
            for _ in range(600):
                try:
                    if httpx.get(url + "/global/health", timeout=1).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                assert process.poll() is None, (runtime / "opencode.log").read_text()
                time.sleep(0.1)
            else:
                pytest.fail("Isolated OpenCode did not start")
            yield url
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
            log.close()
            fcntl.flock(lock, fcntl.LOCK_UN)
