"""Hold the control port from the first second of the container, so nothing ever gets a 502.

The platform flips a workspace session to `running` when its execution is up. `environment/app.sh`
then resolves the venv and imports this package, and only then does uvicorn bind the port — several
seconds later on a cold container. For that whole gap the workspace proxy has no upstream and
answers `502 Bad Gateway`, which was the first page a new viewer ever saw.

The gap was raced twice and lost twice. It cannot be won from the server side: the workspace ingress
authenticates by browser session cookie and answers 404 to every other request, so a readiness probe
run from another container reads 404 on a builder that is up and on one that does not exist, and can
never tell them apart. So this fills the gap instead of racing it. `app.sh` starts this script before
anything slow, it answers the port with a page that refreshes itself, and `run()` kills it in the
instant before uvicorn binds the same port.

Deliberately stdlib-only and run by path, never imported as a module: the point is to answer before
the venv is resolved, so it must not need the venv. Nothing here may grow an import that does.

No `brand.text()` here, and not an oversight. This page is the one surface that has to render before
the brand pack can be read, so its copy is written with no word the pack renames — that is why it
says "workspace" and never names the product. Keep it that way rather than reaching for the pack.
"""
from __future__ import annotations

import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# How long a boot is allowed to look normal. Past this the copy stops promising a few seconds and
# says what to do instead, because a builder that never binds the port would otherwise leave a
# reassuring page refreshing itself forever. Serving on is still right: exiting would hand the
# viewer back the 502 this file exists to remove.
PATIENCE_S = 180.0

_STYLE = """
  :root { color-scheme: light; }
  body { margin: 0; min-height: 100vh; display: flex; align-items: center;
         justify-content: center; background: #fff;
         font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
  main { max-width: 26rem; padding: 2rem; text-align: center; }
  h1 { margin: 0 0 .5rem; font-size: 20px; font-weight: 600; color: #3F4547; }
  p { margin: 0; color: #7F8385; }
"""


def page(elapsed_s: float) -> str:
    """The whole page, for a boot that has been going `elapsed_s` seconds."""
    if elapsed_s < PATIENCE_S:
        heading = "Your workspace is starting"
        detail = "This page refreshes on its own. It usually takes a few seconds."
        refresh = 2
    else:
        heading = "Your workspace is taking longer than expected"
        detail = "This page keeps refreshing. If it does not change, stop the workspace and start it again."
        refresh = 10
    return (
        "<!doctype html>\n"
        '<html lang="en">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f'<meta http-equiv="refresh" content="{refresh}">\n'
        "<title>Starting…</title>\n"
        f"<style>{_STYLE}</style>\n"
        "</head>\n<body>\n"
        f"<main>\n<h1>{heading}</h1>\n<p>{detail}</p>\n</main>\n"
        "</body>\n</html>\n"
    )


class _Handler(BaseHTTPRequestHandler):
    # Same answer for every path. There is no app behind this yet, so routing would be a fiction:
    # a viewer who deep-linked into the builder gets the same wait as one who opened the root, and
    # the refresh lands them where they asked once the real server is up.
    protocol_version = "HTTP/1.1"
    started = time.monotonic()

    def _respond(self, body: bytes, content_type: str) -> None:
        # 503, not 200: this is a real "not yet", and the refresh header says when to come back.
        # Browsers render the body of a 503, so the page is still what the viewer sees.
        self.send_response(503)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Retry-After", "2")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    # do_GET/do_POST are BaseHTTPRequestHandler's spelling, not ours.
    def do_GET(self) -> None:
        if self.path.split("?")[0].endswith("/healthz"):
            body = json.dumps({"status": "starting"}).encode()
            self._respond(body, "application/json")
            return
        self._respond(page(time.monotonic() - self.started).encode(), "text/html; charset=utf-8")

    def do_POST(self) -> None:
        self.do_GET()

    def log_message(self, fmt: str, *args: object) -> None:
        # One line per refresh, per viewer, for as long as the boot lasts. The container log is
        # where the boot is read, and this would bury it.
        return


def make_server(host: str, port: int) -> ThreadingHTTPServer:
    ThreadingHTTPServer.allow_reuse_address = True
    ThreadingHTTPServer.daemon_threads = True
    return ThreadingHTTPServer((host, port), _Handler)


def main() -> None:
    host = os.environ.get("SAGE_CONTROL_HOST", "127.0.0.1")
    port = int(os.environ.get("SAGE_CONTROL_PORT", "8080"))
    # No signal handling: the default disposition of SIGTERM already ends the process, and the
    # kernel frees the port with it. `run()` sends that signal and then waits for the port.
    make_server(host, port).serve_forever()


if __name__ == "__main__":
    main()
