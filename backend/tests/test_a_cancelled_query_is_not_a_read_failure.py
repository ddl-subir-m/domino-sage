"""A cancelled query is not a failed read, and a per-app path keeps its kind (#693).

The template's query hooks abort a superseded request and ignore the AbortError. The reporters
posted `preview/data-error` for that abort as for any rejection, and the server stripped only
through `/preview/`, so `<prefix>/preview/<appId>/api/queries/<name>` arrived as a platform read
of `/unrecognized`. Every Build turn on a per-app preview then ended in a refused-read repair.
"""
from __future__ import annotations

import shutil
from types import SimpleNamespace

import pytest

from sage.orchestrator.service import Orchestrator

from .test_the_preview_reports_reach_sage import APP, PREFIX, _harness


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")
@pytest.mark.parametrize("stack", ["fastapi-antd", "react-vite"])
def test_an_aborted_query_posts_no_data_error(stack: str):
    calls = _harness({"stack": stack, "base": f"{PREFIX}/preview/{APP}/", "abort": True})["calls"]
    assert f"{PREFIX}/preview/{APP}/api/queries/q" in calls
    assert f"{PREFIX}/api/preview/data-error" not in calls


def _record(path: str) -> list[tuple]:
    heard = []
    host = SimpleNamespace(
        capture_preview_read=lambda vid, p, kind: heard.append((p, kind)) or {"read": {}},
        record_platform_read_failure=lambda status, p, context: heard.append((status, p)))
    Orchestrator.record_preview_data_error(host, "v1", path)
    return heard


@pytest.mark.parametrize("base", [f"{PREFIX}/preview/{APP}", f"/preview/{APP}", PREFIX + "/preview", ""])
def test_a_query_path_stays_a_query_read(base: str):
    assert _record(f"{base}/api/queries/sales") == [
        ("/api/queries/sales", "query"), (None, "/api/queries/sales")]


@pytest.mark.parametrize("base", [f"{PREFIX}/preview/{APP}", f"/preview/{APP}", PREFIX + "/preview", ""])
def test_a_domino_path_maps_to_the_platform_path(base: str):
    path = "/v4/datasetrw/datasets-v2"
    assert _record(f"{base}/api/domino{path}") == [(path, "platform"), (None, path)]
