"""The preview's acks and crash reports reach Sage's control API, not the app's own server (#657).

Since each app got its own preview (2026-09-27), the preview is served at `<prefix>/preview/<appId>/`.
The reporters derived Sage's API by stripping a trailing `preview` off that base, which no longer
matched, so they posted to `<prefix>/preview/<appId>/api/preview/ack` — the app's own server, a 404.
No page check was ever acknowledged ("the preview didn't load the changed page within 10s") and no
runtime error ever reached the repair loop, on either stack.

Run from the templates' own files under Node, so the URLs asserted are the ones a browser would hit.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

HARNESS = Path(__file__).parent / "js" / "preview_api_base_harness.mjs"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")

# The prefix a live Domino workspace served the preview under on 2026-10-07.
PREFIX = "/subir_mansukhani/sage-signal-room/notebookSession/6ac65872398bda09cf18a69c"
APP = "app_1a1172838cca970051269"


def _harness(payload: dict) -> dict:
    out = subprocess.run(["node", str(HARNESS)], input=json.dumps(payload),
                         capture_output=True, text=True, timeout=30, check=True)
    return json.loads(out.stdout)


def _control_calls(prefix: str) -> set[str]:
    return {f"{prefix}/api/preview/ack", f"{prefix}/api/preview/runtime-error",
            f"{prefix}/api/preview/data-error", f"{prefix}/api/project/build/state"}


# fastapi-antd's `sage.base` is the prefix its server received, checked with and without a trailing
# slash. react-vite's is Vite's `BASE_URL`, which always ends in one. Both take the bare `/preview`
# an app served before it had its own preview too.
SLASHED = [
    (f"{PREFIX}/preview/{APP}/", PREFIX),
    (f"{PREFIX}/preview/", PREFIX),
    (f"/preview/{APP}/", ""),
    ("/preview/", ""),
]
UNSLASHED = [(f"{PREFIX}/preview/{APP}", PREFIX), (f"{PREFIX}/preview", PREFIX), (f"/preview/{APP}", "")]
CASES = [("fastapi-antd", *case) for case in SLASHED + UNSLASHED] + \
        [("react-vite", *case) for case in SLASHED]


@pytest.mark.parametrize(("stack", "base", "prefix"), CASES)
def test_the_reporter_posts_to_sages_api(stack: str, base: str, prefix: str):
    calls = _harness({"stack": stack, "base": base})["calls"]
    sent = {c for c in calls if "/api/queries/" not in c}
    assert sent == _control_calls(prefix)


@pytest.mark.parametrize(("prefix", "app", "api"), [
    (PREFIX, APP, f"{PREFIX}/api/"),
    (PREFIX, "", f"{PREFIX}/api/"),
    ("", APP, "/api/"),
    ("", "", "/api/"),
])
def test_the_vite_overlay_asks_sages_api(prefix: str, app: str, api: str):
    assert _harness({"stack": "vite-config", "prefix": prefix, "app": app})["api"] == api
