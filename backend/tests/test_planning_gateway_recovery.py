"""Only a completed root planning call can retire an earlier gateway failure (#557 P2)."""
import json
from types import SimpleNamespace

import pytest

from sage.gateway.protocol import Protocol

from .test_native_model_controls import active, dispatch, request_body
from .test_native_model_controls import running as _running


@pytest.fixture
def running(tmp_path, monkeypatch):
    return _running.__wrapped__(tmp_path, monkeypatch)


ROUTES = [
    ("native", Protocol.MESSAGES, "Opus-4.8"),
    ("native", Protocol.RESPONSES, "gpt-5.4"),
    ("native", Protocol.CHAT, "GLM 5.3 OR"),
    ("legacy", Protocol.CHAT, "GLM 5.3 OR"),
]


def post(client, headers, route, protocol, model):
    if route == "legacy":
        return client.post("/v1/chat/completions", headers=headers,
                           json=request_body(protocol, model))
    return dispatch(client, headers, protocol, model)


@pytest.mark.parametrize("route,protocol,model", ROUTES)
def test_successful_root_plan_retry_retires_only_its_prior_failure(running, route, protocol, model):
    client, orch, gateway = running
    project = orch._project
    client.post("/api/project/model", json={"mode": "plan", "pick": model})
    token = project.control.arm_read_only("plan")
    try:
        with active(orch) as headers:
            gateway.failure = "before"
            assert post(client, headers, route, protocol, model).status_code == 502
            previous = project.last_gateway_error
            assert previous is not None
            gateway.failure = None
            original = gateway.route

            def recovered(*args, **kwargs):
                assert project.last_gateway_error is previous, "request start cleared the failure"
                yield from original(*args, **kwargs)

            gateway.route = recovered
            assert post(client, headers, route, protocol, model).status_code == 200
            assert project.last_gateway_error is None
    finally:
        project.control.disarm_read_only(token)


@pytest.mark.parametrize("route", ["native", "legacy"])
@pytest.mark.parametrize("change", ["new_error", "root_session", "owner", "plan_scope"])
def test_late_success_cannot_clear_a_new_failure_or_owner(running, route, change, monkeypatch):
    client, orch, gateway = running
    project = orch._project
    client.post("/api/project/model", json={"mode": "plan", "pick": "GLM 5.3 OR"})
    token = project.control.arm_read_only("plan")
    previous = {"message": "earlier failure"}
    # A later call can fail for the same reason: equal contents do not make it the old error.
    expected = dict(previous) if change == "new_error" else previous
    try:
        with active(orch) as headers:
            project.last_gateway_error = previous
            original = gateway.route

            def recovered(*args, **kwargs):
                frames = iter(original(*args, **kwargs))
                yield next(frames)
                if change == "new_error":
                    project.last_gateway_error = expected
                elif change == "root_session":
                    project.active_session_id = "ses_successor"
                elif change == "owner":
                    monkeypatch.setattr(orch._turns, "running", lambda: object())
                else:
                    project.control.disarm_read_only(token)
                yield from frames

            gateway.route = recovered
            assert post(client, headers, route, Protocol.CHAT, "GLM 5.3 OR").status_code == 200
            assert project.last_gateway_error is expected
    finally:
        project.control.disarm_read_only(token)


def test_recovered_gateway_call_allows_the_completed_main_plan(running, monkeypatch):
    from .fake_opencode import FakeOpenCode, Turn
    from .test_a_plan_document_captions_without_naming import NAMED

    client, orch, gateway = running
    project = orch._project
    client.post("/api/project/model", json={"mode": "plan", "pick": "GLM 5.3 OR"})
    driver = FakeOpenCode(project.workspace.path, [Turn(text=NAMED)])
    monkeypatch.setattr(orch, "_oc_client", driver)
    sid = driver.create_session(directory=str(project.workspace.path))
    original = driver.send_prompt

    def recovered(session, *args, **kwargs):
        headers = {"X-Session-Id": session}
        gateway.failure = "before"
        assert dispatch(client, headers, Protocol.CHAT, "GLM 5.3 OR").status_code == 502
        gateway.failure = None
        assert dispatch(client, headers, Protocol.CHAT, "GLM 5.3 OR").status_code == 200
        original(session, *args, **kwargs)

    monkeypatch.setattr(driver, "send_prompt", recovered)
    with active(orch):
        plan, _ = orch._run_sage_execution_plan(
            project, "write a plan", sid, where="test", source_request_count=1)
    assert plan == NAMED.strip()


@pytest.mark.parametrize("route", ["native", "legacy"])
@pytest.mark.parametrize("scope", ["child", "not_plan", "no_header"])
def test_unrelated_success_cannot_clear_a_planning_failure(running, route, scope, monkeypatch):
    client, orch, _gateway = running
    project = orch._project
    client.post("/api/project/model", json={"mode": "plan", "pick": "GLM 5.3 OR"})
    token = project.control.arm_read_only("ask" if scope == "not_plan" else "plan")
    previous = {"message": "root failure"}
    try:
        with active(orch) as headers:
            project.last_gateway_error = previous
            if scope == "child":
                headers = {"X-Session-Id": "ses_child"}
                monkeypatch.setattr(orch, "_oc_client", SimpleNamespace(
                    session_belongs_to=lambda child, root: child == "ses_child" and root == "ses_native"))
            elif scope == "no_header":
                headers = {}
            response = post(client, headers, route, Protocol.CHAT, "GLM 5.3 OR")
            assert response.status_code == (400 if route == "native" and scope == "no_header" else 200)
            assert project.last_gateway_error is previous
    finally:
        project.control.disarm_read_only(token)


@pytest.mark.parametrize("route", ["native", "legacy"])
@pytest.mark.parametrize("ending", ["eof", "length", "refusal", "error"])
def test_partial_or_refused_retry_never_clears_prior_failure(running, route, ending):
    client, orch, gateway = running
    project = orch._project
    client.post("/api/project/model", json={"mode": "plan", "pick": "GLM 5.3 OR"})
    token = project.control.arm_read_only("plan")
    previous = {"message": "earlier failure"}

    def incomplete(*_args, **_kwargs):
        events = [{"choices": [{"delta": {"content": "plausible partial plan"}}]}]
        if ending == "length":
            events += [{"choices": [{"delta": {}, "finish_reason": "length"}]}]
        elif ending == "refusal":
            events += [{"choices": [{"delta": {"refusal": "declined"}, "finish_reason": "stop"}]}]
        elif ending == "error":
            events += [{"error": {"message": "provider failed"}}]
        wire = b"".join(b"data: " + json.dumps(event).encode() + b"\n\n" for event in events)
        # The completion check must inspect protocol frames, not transport boundaries.
        for offset in range(0, len(wire), 11):
            yield wire[offset:offset + 11]

    gateway.route = incomplete
    try:
        with active(orch) as headers:
            project.last_gateway_error = previous
            post(client, headers, route, Protocol.CHAT, "GLM 5.3 OR")
            assert project.last_gateway_error is not None
    finally:
        project.control.disarm_read_only(token)
