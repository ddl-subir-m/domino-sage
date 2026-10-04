"""A builder's secret is a Domino Project variable that reaches OpenCode and the preview without a
workspace restart, and never reaches the model (#641).

No real Domino: `FakeControlPlane` holds the Project variables, and `DominoControlPlane` is driven
through an `httpx.MockTransport` where its own paths and error text are the claim.
"""
from __future__ import annotations

import base64
import json
import logging
import threading
import time
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from sage import project_secrets
from sage.driver.server import OpenCodeServer
from sage.gateway.client import FakeGatewayClient
from sage.orchestrator.service import Orchestrator
from sage.preview.supervisor import UvicornSupervisor, ViteSupervisor
from sage.provision.domino import DominoControlPlane, FakeControlPlane
from sage.router.model_control import ModelControl
from sage.router.models import Mode, ModelCatalog, Phase
from sage.shim.enforcement import EnforcementShim

PLANTED = "planted-secret-value-7f3a9c"
PID = "proj-1"


@pytest.fixture
def cp(tmp_path, monkeypatch) -> FakeControlPlane:
    """A Project on Domino with the secrets store installed, as `app.py` installs it at import."""
    plane = FakeControlPlane()
    plane.env_vars[PID] = {}
    monkeypatch.setattr(project_secrets, "_active", None)  # restored at teardown
    monkeypatch.setattr(project_secrets, "_reason", None)
    project_secrets.install(plane, PID, tmp_path / ".sage" / "secrets.json")
    return plane


@pytest.fixture
def client() -> TestClient:
    import sage.orchestrator.app as appmod
    return TestClient(appmod.control_app)


@pytest.fixture
def orch(tmp_path, monkeypatch) -> Orchestrator:
    """The orchestrator the endpoints restart, with a stand-in for its OpenCode server."""
    import sage.orchestrator.app as appmod
    o = Orchestrator(workspace_dir=tmp_path / "ws", template=tmp_path / "template",
                     gateway=FakeGatewayClient(), catalog=ModelCatalog("sq", "sq", "sq", "p", "i", "a"))
    monkeypatch.setattr(appmod, "orchestrator", o)
    return o


class _Server:
    def __init__(self, log: list[str]) -> None:
        self._log = log

    def stop(self) -> None:
        self._log.append("stop")


# --- the endpoint contract ---


def test_off_domino_the_list_says_why_and_nothing_can_be_written(client, tmp_path, monkeypatch):
    monkeypatch.setattr(project_secrets, "_active", None)
    monkeypatch.setattr(project_secrets, "_reason", None)
    project_secrets.install(None, PID, tmp_path / "secrets.json")

    body = client.get("/api/project/secrets").json()
    assert body["available"] is False and body["secrets"] == []
    assert isinstance(body["reason"], str) and body["reason"]
    assert client.put("/api/project/secrets/OPENAI_KEY", json={"value": PLANTED}).status_code == 409


def test_without_a_project_id_the_list_says_so(client, tmp_path, monkeypatch):
    monkeypatch.setattr(project_secrets, "_active", None)
    monkeypatch.setattr(project_secrets, "_reason", None)
    project_secrets.install(FakeControlPlane(), "", tmp_path / "secrets.json")

    body = client.get("/api/project/secrets").json()
    assert body["available"] is False and body["reason"]


def test_create_list_note_update_and_delete(client, cp, orch, tmp_path):
    assert client.put("/api/project/secrets/OPENAI_KEY", json={"note": "no value"}).status_code == 400

    r = client.put("/api/project/secrets/OPENAI_KEY", json={"value": PLANTED, "note": "OpenAI, for the app"})
    assert r.status_code == 200
    assert r.json() == {"name": "OPENAI_KEY", "note": "OpenAI, for the app"}
    assert cp.env_vars[PID]["OPENAI_KEY"] == PLANTED

    listed = client.get("/api/project/secrets").json()
    assert listed == {"available": True, "reason": None,
                      "secrets": [{"name": "OPENAI_KEY", "note": "OpenAI, for the app"}]}

    r = client.put("/api/project/secrets/OPENAI_KEY", json={"note": "rotated monthly"})
    assert r.json() == {"name": "OPENAI_KEY", "note": "rotated monthly"}
    assert cp.env_vars[PID]["OPENAI_KEY"] == PLANTED  # a note-only update leaves the value alone

    r = client.put("/api/project/secrets/OPENAI_KEY", json={"value": "replacement-value-0001"})
    assert r.json() == {"name": "OPENAI_KEY", "note": "rotated monthly"}
    assert cp.env_vars[PID]["OPENAI_KEY"] == "replacement-value-0001"

    assert client.delete("/api/project/secrets/OPENAI_KEY").json() == {"ok": True}
    assert client.get("/api/project/secrets").json()["secrets"] == []
    assert "OPENAI_KEY" not in json.loads((tmp_path / ".sage" / "secrets.json").read_text())


def test_deleting_a_secret_that_is_already_gone_is_ok(client, cp, orch):
    assert client.delete("/api/project/secrets/NEVER_SET").json() == {"ok": True}


@pytest.mark.parametrize("name", ["1KEY", "A-B", "has.dot", "SAGE_APP_KEY", "sage_mine", "Domino_TOKEN"])
def test_a_bad_or_reserved_name_is_a_400_the_ui_can_show(client, cp, orch, name):
    r = client.put(f"/api/project/secrets/{name}", json={"value": PLANTED})
    assert r.status_code == 400 and r.json()["error"]
    assert name not in cp.env_vars[PID]
    assert client.delete(f"/api/project/secrets/{name}").status_code == 400


def test_the_list_hides_sage_and_domino_variables(client, cp):
    cp.env_vars[PID] = {"SAGE_SELF_UPDATE": "1", "domino_thing": "x", "SAGE_APP_KEY": "k" * 44,
                        "OPENAI_KEY": PLANTED}
    assert [s["name"] for s in client.get("/api/project/secrets").json()["secrets"]] == ["OPENAI_KEY"]


def test_a_value_is_never_in_a_response_a_log_line_or_a_sage_file(client, cp, orch, tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    texts = [
        client.put("/api/project/secrets/OPENAI_KEY", json={"value": PLANTED, "note": "n"}).text,
        client.get("/api/project/secrets").text,
        client.put("/api/project/secrets/OPENAI_KEY", json={"note": "m"}).text,
        client.put("/api/project/secrets/OPENAI_KEY", json={"value": PLANTED}).text,
        client.get("/api/project/secrets").text,
    ]
    project_secrets.process_env()
    project_secrets.preview_env()
    texts.append(client.delete("/api/project/secrets/OPENAI_KEY").text)

    assert all(PLANTED not in t for t in texts)
    assert PLANTED not in caplog.text
    for f in (tmp_path / ".sage").rglob("*"):
        if f.is_file():
            assert PLANTED not in f.read_text(), f


def test_a_domino_error_does_not_echo_the_value_back(caplog):
    caplog.set_level(logging.DEBUG)

    def answer(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text=f"invalid variable: {request.content.decode()}")

    plane = DominoControlPlane("https://domino.test", lambda: "tok", environment_id="e",
                               hardware_tier_id="t", transport=httpx.MockTransport(answer))
    with pytest.raises(RuntimeError) as err:
        plane.set_project_env_var(PID, "OPENAI_KEY", PLANTED)
    assert PLANTED not in str(err.value) and "400" in str(err.value)
    assert PLANTED not in caplog.text


def test_the_domino_client_speaks_the_project_variables_api():
    seen: list[tuple[str, str, bytes]] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, request.content))
        if request.method == "GET":
            return httpx.Response(200, json={"vars": [{"name": "A_KEY", "value": "aaaaaaaa"}]})
        return httpx.Response(200, json={})

    plane = DominoControlPlane("https://domino.test", lambda: "tok", environment_id="e",
                               hardware_tier_id="t", transport=httpx.MockTransport(answer))
    assert plane.project_env_vars(PID) == {"A_KEY": "aaaaaaaa"}
    plane.set_project_env_var(PID, "B_KEY", "bbbbbbbb")
    plane.delete_project_env_var(PID, "B_KEY")

    path = f"/v4/projects/{PID}/environmentVariables"
    assert [(m, p) for m, p, _ in seen] == [("GET", path), ("POST", path), ("DELETE", f"{path}/B_KEY")]
    assert json.loads(seen[1][2]) == {"name": "B_KEY", "value": "bbbbbbbb"}


# --- what Sage starts reads the secret without a workspace restart ---


def test_opencode_spawns_with_a_secret_set_after_start(cp, tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_KEY", "the-value-the-workspace-started-with")
    server = OpenCodeServer(cwd=tmp_path / "oc")

    cp.env_vars[PID]["OPENAI_KEY"] = PLANTED
    cp.env_vars[PID]["ADDED_LATER"] = "added-after-the-workspace-started"

    env = server._env()
    assert env["OPENAI_KEY"] == PLANTED  # the API wins over the stale process environment
    assert env["ADDED_LATER"] == "added-after-the-workspace-started"


@pytest.mark.parametrize("cls", [ViteSupervisor, UvicornSupervisor])
def test_a_preview_spawns_with_a_secret_set_after_start(cp, tmp_path, monkeypatch, cls):
    launched: list[dict] = []
    monkeypatch.setattr(cls, "_launch", lambda self, command, env, port, generation: launched.append(env))
    sup = cls(tmp_path / "app")

    cp.env_vars[PID]["ADDED_LATER"] = PLANTED
    sup._spawn()

    assert launched and launched[0]["ADDED_LATER"] == PLANTED
    assert launched[0][project_secrets.APP_KEY]  # the cookie key exists before the preview runs


# --- a change restarts OpenCode and running previews, never the workspace ---


def test_setting_and_deleting_a_secret_restarts_opencode(client, cp, orch):
    stops: list[str] = []
    orch._oc_server, orch._oc_client = _Server(stops), object()

    client.put("/api/project/secrets/OPENAI_KEY", json={"value": PLANTED})
    assert stops == ["stop"] and orch._oc_server is None and orch._oc_client is None

    orch._oc_server, orch._oc_client = _Server(stops), object()
    client.put("/api/project/secrets/OPENAI_KEY", json={"note": "only the note"})
    assert stops == ["stop"], "a note is not in any environment, so nothing restarts"

    client.delete("/api/project/secrets/OPENAI_KEY")
    assert stops == ["stop", "stop"] and orch._oc_server is None


def test_the_opencode_restart_waits_for_the_running_turn(orch):
    stops: list[str] = []
    orch._oc_server, orch._oc_client = _Server(stops), object()
    assert orch._turn_lock.acquire(blocking=False)  # a turn is in flight
    try:
        orch.restart_for_secrets()
        time.sleep(0.5)
        assert stops == [], "stopping OpenCode mid-turn would end the turn"
    finally:
        orch._turn_lock.release()
    deadline = time.monotonic() + 5
    while not stops and time.monotonic() < deadline:
        time.sleep(0.05)
    assert stops == ["stop"] and orch._oc_client is None
    waiter = orch._secrets_waiter
    if waiter is not None:
        waiter.join(timeout=2)
    assert orch._secrets_waiter is None


def test_running_previews_restart_and_stopped_ones_are_left(orch):
    class _Sup:
        def __init__(self, running: bool) -> None:
            self.running, self.retries = running, []

        def upstream(self) -> str:
            if not self.running:
                raise RuntimeError("not ready")
            return "http://127.0.0.1:1"

        def retry_start(self, *, explicit: bool = False) -> bool:
            self.retries.append(explicit)
            return True

    running, stopped, selected = _Sup(True), _Sup(False), _Sup(True)
    view = lambda sup: SimpleNamespace(supervisor=sup)
    orch._project = SimpleNamespace(_views={"a": view(running), "b": view(stopped)},
                                    _selected_view=view(selected))

    orch.restart_for_secrets()

    assert running.retries == [True] and selected.retries == [True] and stopped.retries == []


# --- SAGE_APP_KEY ---


def test_the_app_key_is_created_once_and_never_overwritten(cp, tmp_path):
    store = project_secrets.active()
    store.ensure_app_key()
    store.ensure_app_key()
    project_secrets.preview_env()

    key = cp.env_vars[PID][project_secrets.APP_KEY]
    assert len(base64.urlsafe_b64decode(key)) == 32
    assert cp.env_var_writes == [("POST", project_secrets.APP_KEY)]

    fresh = project_secrets.ProjectSecrets(cp, PID, tmp_path / "other.json")
    fresh.ensure_app_key()
    assert cp.env_vars[PID][project_secrets.APP_KEY] == key
    assert cp.env_var_writes == [("POST", project_secrets.APP_KEY)]


def test_an_existing_app_key_is_kept(cp):
    cp.env_vars[PID][project_secrets.APP_KEY] = "set-by-somebody-else"
    project_secrets.ensure_app_key()
    assert cp.env_vars[PID][project_secrets.APP_KEY] == "set-by-somebody-else"
    assert cp.env_var_writes == []


def test_publishing_ensures_the_app_key(cp, tmp_path):
    t = tmp_path / "template"
    (t / "src").mkdir(parents=True)
    (t / "src" / "App.tsx").write_text("placeholder")
    (t / "package.json").write_text("{}")
    (t / "app.sh").write_text("#!/bin/bash\nexec npx vite preview\n")
    o = Orchestrator(workspace_dir=tmp_path / "mnt" / "code", template=t, gateway=object(),
                     catalog=ModelCatalog("sq", "sq", "sq", "p", "i", "a"), project_id="Sage",
                     control_plane=cp, domino_project_id=PID, domino_project_name="Sales")
    o.project(start_preview=False)

    assert o.publish()["published"] is True
    assert cp.env_vars[PID][project_secrets.APP_KEY]


# --- the shim ---


def _shim(gw: FakeGatewayClient) -> EnforcementShim:
    catalog = ModelCatalog("sq", "sq", "sq", "gpt-5.4", "bedrock-qwen3-coder", "gpt-5.4")
    return EnforcementShim(ModelControl(mode=Mode.AUTO, phase=Phase.PLAN), catalog, gw)


def test_the_shim_hides_a_value_in_a_tool_result_and_notes_the_mention(cp):
    cp.env_vars[PID] = {"OPENAI_KEY": PLANTED, "SHORT": "abc1234"}
    project_secrets.active().refresh()
    gw = FakeGatewayClient()
    messages = [
        {"role": "user", "content": "Call OpenAI with {env:OPENAI_KEY}"},
        {"role": "assistant", "tool_calls": [{"id": "c1", "type": "function", "function": {
            "name": "bash", "arguments": json.dumps({"command": "echo $OPENAI_KEY"})}}]},
        {"role": "tool", "tool_call_id": "c1", "content": f"{PLANTED}\nabc1234\n"},
    ]

    list(_shim(gw).handle({"model": "x", "messages": messages}, project="p"))

    sent = json.dumps(gw.seen[-1][0]["messages"])
    assert PLANTED not in sent
    tool = gw.seen[-1][0]["messages"][2]
    assert "{env:OPENAI_KEY}" in tool["content"]
    assert "abc1234" in tool["content"]  # under 8 characters: too common to match on
    user = gw.seen[-1][0]["messages"][0]["content"]
    assert 'secret("OPENAI_KEY")' in user and "sage_secrets" in user


def test_the_shim_hides_a_value_inside_text_parts_and_tool_arguments(cp):
    cp.env_vars[PID] = {"OPENAI_KEY": PLANTED}
    project_secrets.active().refresh()
    gw = FakeGatewayClient()
    messages = [
        {"role": "user", "content": [{"type": "text", "text": f"my key is {PLANTED}"}]},
        {"role": "assistant", "tool_calls": [{"id": "c1", "type": "function", "function": {
            "name": "bash", "arguments": json.dumps({"command": f"curl -H 'k: {PLANTED}'"})}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "ok"},
    ]

    list(_shim(gw).handle({"model": "x", "messages": messages}, project="p"))

    sent = gw.seen[-1][0]["messages"]
    assert PLANTED not in json.dumps(sent)
    # The pasted value became a mention, so the model is told what the mention means.
    assert any('secret("OPENAI_KEY")' in p["text"] for p in sent[0]["content"])


def test_without_secrets_the_shim_leaves_messages_alone(monkeypatch):
    monkeypatch.setattr(project_secrets, "_active", None)
    gw = FakeGatewayClient()
    messages = [{"role": "user", "content": "hello"}]
    list(_shim(gw).handle({"model": "x", "messages": messages}, project="p"))
    assert gw.seen[-1][0]["messages"][0]["content"] == "hello"


def test_concurrent_preview_starts_write_one_app_key(cp):
    threads = [threading.Thread(target=project_secrets.ensure_app_key) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert cp.env_var_writes == [("POST", project_secrets.APP_KEY)]
