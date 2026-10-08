"""Sage loads the changed page itself, in headless Chromium (#707).

The Workbench loads `preview/<app>/?sageValidation=<id>` only while a person is looking at that app in
Build mode, so a build nobody watches never had its page checked. This opens the same document from
inside the container. It judges nothing: the page's own `reportRuntimeError` posts the ack and any
crash, exactly as it does in the Workbench, and `_validate_page` decides from those.

One short-lived process per check, in its own process group, so `close()` takes Chromium with it.
No browser is downloaded at runtime: the image installs one (environment/Dockerfile), and a machine
without one gets the page check it had before, with the reason on the record.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import signal
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import quote

from .prefix import domino_base_prefix

log = logging.getLogger("sage.preview.page_check")

SCRIPT = Path(__file__).with_name("page_check.mjs")
_SHELLS = ("chromium_headless_shell-*/*/chrome-headless-shell", "chromium_headless_shell-*/*/headless_shell")
_DEFAULT_CACHES = (Path.home() / ".cache" / "ms-playwright",
                   Path.home() / "Library" / "Caches" / "ms-playwright")
# Long enough for the script's own SIGTERM handler to close Chromium; a hung one is then killed.
_TERM_GRACE_SECONDS = 3.0


class Unavailable(Exception):
    """No headless browser here; the check runs as it did before #707."""


def local_url(app_id: str, validation_id: str) -> str:
    """The preview as this process serves it on loopback, the origin the page's relative `API`
    resolves against. The Domino prefix is kept, not stripped: Vite bakes it into the page's asset
    URLs, and the prefix middleware accepts it on loopback as it does through the proxy."""
    port = os.environ.get("SAGE_CONTROL_PORT", "8080")
    return (f"http://127.0.0.1:{port}{domino_base_prefix()}/preview/{quote(app_id, safe='')}/"
            f"?sageValidation={quote(validation_id, safe='')}")


def find_chromium() -> Path | None:
    explicit = os.environ.get("SAGE_PAGE_CHECK_CHROMIUM")
    if explicit:
        return Path(explicit) if Path(explicit).is_file() else None
    configured = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    roots = (Path(configured),) if configured else _DEFAULT_CACHES
    found = [path for root in roots for pattern in _SHELLS for path in root.glob(pattern) if path.is_file()]

    def revision(path: Path) -> int:
        match = re.search(r"-(\d+)$", path.parent.parent.name)
        return int(match.group(1)) if match else -1

    return max(found, key=revision, default=None)


def _node() -> str | None:
    return shutil.which("node")


def _has_playwright_core() -> bool:
    """Where the script's bare `import "playwright-core"` resolves: up the `node_modules` chain."""
    return any((parent / "node_modules" / "playwright-core").is_dir() for parent in SCRIPT.parents)


def unavailable() -> str | None:
    """Why no headless page check can run here, or None when one can."""
    why = ("node is not on PATH" if _node() is None else
           "playwright-core is not installed" if not _has_playwright_core() else
           "SAGE_PAGE_CHECK_CHROMIUM is not a file"
           if os.environ.get("SAGE_PAGE_CHECK_CHROMIUM") and find_chromium() is None else
           "no Chromium found" if find_chromium() is None else None)
    return None if why is None else f"no headless browser in this environment ({why})"


def _command(url: str, timeout: float) -> list[str]:
    return [_node() or "node", str(SCRIPT), url, str(find_chromium()), f"{timeout:g}"]


class PageCheck:
    def __init__(self, process: subprocess.Popen, stderr, stdout):
        self.process, self._stderr, self._stdout = process, stderr, stdout

    def walked(self) -> bool:
        """Whether the script has opened every tab it will (#709), or has exited."""
        if self._stdout.closed or self.process.poll() is not None:
            return True
        self._stdout.seek(0)
        return b"done" in self._stdout.read()

    def close(self) -> None:
        """Stop the script and everything it started, and reap it. Safe to call twice."""
        if self.process.returncode is None:
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(self.process.pid, sig)
                except ProcessLookupError:
                    pass
                try:
                    self.process.wait(_TERM_GRACE_SECONDS if sig == signal.SIGTERM else None)
                    break
                except subprocess.TimeoutExpired:
                    continue
        self._stdout.close()
        if not self._stderr.closed:
            self._stderr.seek(0)
            said = self._stderr.read().decode(errors="replace").strip()
            self._stderr.close()
            if said:
                log.warning("headless page check: %s", said[-2000:])


def start(url: str, timeout: float) -> PageCheck:
    """Open `url` in headless Chromium for at most `timeout` seconds. The caller must `close()` it."""
    why = unavailable()
    if why is not None:
        raise Unavailable(why)
    stderr = tempfile.TemporaryFile()  # noqa: SIM115 - PageCheck.close() owns it
    stdout = tempfile.TemporaryFile()  # noqa: SIM115 - PageCheck.close() owns it
    try:
        process = subprocess.Popen(_command(url, timeout), stdin=subprocess.DEVNULL,
                                   stdout=stdout, stderr=stderr, start_new_session=True)
    except BaseException:
        stderr.close()
        stdout.close()
        raise
    return PageCheck(process, stderr, stdout)
