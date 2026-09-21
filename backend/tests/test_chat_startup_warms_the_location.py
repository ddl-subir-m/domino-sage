"""A server health check does not initialize Chat's location services (#417)."""
from types import SimpleNamespace

import httpx
import pytest

from sage.driver.opencode import OpenCodeClient
from sage.orchestrator import app


@pytest.mark.parametrize("existing", [False, True])
def test_startup_warms_chat_without_creating_a_session_or_rewriting_thread_links(
        tmp_path, monkeypatch, existing):
    work = tmp_path / ".sage" / "chat-work"
    if existing:
        records = work / ".sage" / "threads"
        records.mkdir(parents=True)
        (records / "thr_kept").symlink_to(tmp_path, target_is_directory=True)
        (work / "AGENTS.md").write_text("Keep this turn's instructions.")
    seen = []

    def warm(directory):
        assert work.is_dir()
        seen.append(directory)

    # No create_session method: startup must not mint a session that a Thread could adopt.
    client = SimpleNamespace(warm_directory=warm)
    monkeypatch.setattr(app, "orchestrator", SimpleNamespace(
        _ensure_opencode=lambda: client, _wm=SimpleNamespace(_dir=tmp_path)))

    app._warm_opencode()

    assert seen == [str(work)]
    if existing:
        assert (records / "thr_kept").is_symlink()
        assert (work / "AGENTS.md").read_text() == "Keep this turn's instructions."
    else:
        assert list(work.iterdir()) == []


def test_location_warmup_uses_v2_and_names_the_chat_directory(monkeypatch, tmp_path):
    seen = []

    def get(url, **kwargs):
        seen.append((url, kwargs))
        return httpx.Response(200, request=httpx.Request("GET", url), json={"data": []})

    monkeypatch.setattr(httpx, "get", get)
    client = OpenCodeClient("http://opencode.test", timeout_s=17)
    directory = str(tmp_path / "Chat workspace")

    client.warm_directory(directory)

    assert seen == [("http://opencode.test/api/agent", {
        "params": {"location[directory]": directory}, "timeout": 17,
    })]
    assert client._dirs == {}


def test_location_warmup_reports_an_http_failure(monkeypatch):
    def get(url, **kwargs):
        return httpx.Response(503, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", get)
    with pytest.raises(httpx.HTTPStatusError):
        OpenCodeClient("http://opencode.test").warm_directory("/chat")


def test_a_failed_background_warmup_leaves_the_lazy_turn_path_available(
        tmp_path, monkeypatch, caplog):
    def warm(_directory):
        raise httpx.ReadTimeout("location is not ready")

    client = SimpleNamespace(warm_directory=warm)
    monkeypatch.setattr(app, "orchestrator", SimpleNamespace(
        _ensure_opencode=lambda: client, _wm=SimpleNamespace(_dir=tmp_path)))

    app._warm_opencode()

    assert "location is not ready" in caplog.text
    assert app.orchestrator._ensure_opencode() is client
