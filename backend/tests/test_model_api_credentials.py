"""The pasted Model API snippet: parsed, verified, remembered, and written into the app (#9).

Worth pinning, in order of what would hurt most if it broke:

- **400 is a pass.** The one thing this issue actually got wrong in the probing. A token that
  authenticates and a body the model rejects are opposite results, and treating the second as a
  failure would refuse every good credential a creator ever pastes.
- The token never leaves Sage by any route that returns it.
- A snippet copied from the wrong Overview tab is caught, not stored.
- A Model API cannot be bound without one, so a Binding always means a call the app can make.
"""
from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from sage.gateway.client import FakeGatewayClient
from sage.orchestrator.service import Orchestrator
from sage.resources.bindings import KIND_MODEL_API, Binding
from sage.resources.model_api_credentials import (
    IGNORE_RULE,
    Credential,
    CredentialRequired,
    CredentialStore,
    verify_credential,
)
from sage.resources.model_api_snippet import parse_snippet
from sage.resources.pinned_model_api import CONFIG_PATH, agents_block, pinned_model_api, render_config
from sage.resources.provider import FakeResourceProvider, LlmAlias, ModelApi
from sage.router.models import ModelCatalog

TOKEN = "SsQBZCygwPP79P8Q57qLPrGIfj67YAFBm3nrTT6Sm7vuPhBPBJvAL7lHm6jp36qB"
MODEL_ID = "6a8727f40ff0450030085fb3"
URL = f"https://cloud-dogfood.domino.tech:443/models/{MODEL_ID}/latest/model"

# Domino's own jQuery tab, trimmed to the lines that carry anything.
JQUERY = f"""
var accessToken = "{TOKEN}";
$.ajax({{
    method: "POST",
    url: "{URL}",
    dataType: "json",
    contentType: "application/json",
    headers: {{ "Authorization": "Basic " + btoa(accessToken + ":" + accessToken) }},
    data: JSON.stringify({{ data: {{ start: 1, stop: 100 }} }}),
}})
"""

ALIASES = [LlmAlias("id-sonnet", "sonnet", "Claude Sonnet 4.6", None, ["chat"], {})]
MODEL_APIS = [ModelApi(MODEL_ID, "churn-risk", "Scores an account.", "Running")]


# ---- Parsing -------------------------------------------------------------------------------


def test_the_jquery_tab_yields_the_url_the_token_and_the_model_id():
    p = parse_snippet(JQUERY)
    assert (p.url, p.token, p.model_id) == (URL, TOKEN, MODEL_ID)
    assert p.complete and p.missing() is None


def test_the_curl_tab_yields_the_same_two_facts():
    p = parse_snippet(f"curl -s -X POST {URL} -H 'Content-Type: application/json' -u {TOKEN}:{TOKEN} -d '{{}}'")
    assert (p.url, p.token) == (URL, TOKEN)


def test_the_python_tab_yields_the_same_two_facts():
    p = parse_snippet(f'requests.post("{URL}", auth=("{TOKEN}", "{TOKEN}"), json={{"data": {{}}}})')
    assert (p.url, p.token) == (URL, TOKEN)


def test_a_preencoded_basic_header_is_decoded_back_to_the_token():
    # Some snippets ship the credential already encoded rather than as a visible variable.
    import base64

    encoded = base64.b64encode(f"{TOKEN}:{TOKEN}".encode()).decode()
    p = parse_snippet(f"curl -X POST {URL} -H 'Authorization: Basic {encoded}'")
    assert p.token == TOKEN


def test_a_basic_header_whose_halves_differ_is_left_alone():
    # `user:password` from some other service pasted into the same buffer is not this model's token,
    # and half-recovering it would store a credential that fails later with no clue why.
    import base64

    encoded = base64.b64encode(b"alice:hunter2").decode()
    assert parse_snippet(f"curl {URL} -H 'Authorization: Basic {encoded}'").token is None


def test_a_url_without_a_port_or_with_a_pinned_version_still_parses():
    for url in (
        f"https://cloud-dogfood.domino.tech/models/{MODEL_ID}/latest/model",
        f"https://cloud-dogfood.domino.tech/models/{MODEL_ID}/7/model",
    ):
        assert parse_snippet(f"{url} {TOKEN}").url == url


def test_the_token_is_never_taken_out_of_the_url_itself():
    # Nothing in a URL is 64 base62 characters today, but the snippet is Domino's to change and
    # mistaking the endpoint for the credential that opens it would be a hard failure to read.
    p = parse_snippet(URL)
    assert p.url == URL and p.token is None


@pytest.mark.parametrize(
    "text, wanted",
    [
        ("", "neither the model's URL nor its access token"),
        (TOKEN, "not the model's URL"),
        (URL, "not its access token"),
    ],
)
def test_an_incomplete_paste_says_which_half_is_missing(text, wanted):
    p = parse_snippet(text)
    assert not p.complete
    assert wanted in p.missing()


# ---- Verification --------------------------------------------------------------------------


class _Response:
    def __init__(self, status_code: int, text: str = ""):
        self.status_code = status_code
        self.text = text


def _answers(monkeypatch, response):
    import httpx

    def post(url, **kw):
        if isinstance(response, Exception):
            raise response
        post.seen = kw
        return response

    monkeypatch.setattr(httpx, "post", post)
    return post


def test_a_400_is_a_pass_because_the_token_authenticated(monkeypatch):
    # THE test in this file. 401 is refused at the door; 400 means the credential was accepted and
    # the model turned down the probe body, which is empty on purpose. Reading these the same way
    # would reject every working token.
    _answers(monkeypatch, _Response(400, '{"errors": ["predict() missing 1 required argument: score"]}'))
    result = verify_credential(URL, TOKEN)
    assert result.ok
    assert "missing 1 required argument: score" in result.detail


def test_a_200_is_a_pass(monkeypatch):
    _answers(monkeypatch, _Response(200, '{"result": {"score": 0.9}}'))
    assert verify_credential(URL, TOKEN).ok


def test_a_401_is_refused_and_says_the_token_may_have_been_regenerated(monkeypatch):
    _answers(monkeypatch, _Response(401, ""))
    result = verify_credential(URL, TOKEN)
    assert not result.ok
    assert "regenerated" in result.message


def test_a_stopped_model_says_to_start_it_rather_than_blaming_the_token(monkeypatch):
    _answers(monkeypatch, _Response(503, ""))
    assert "not running" in verify_credential(URL, TOKEN).message


def test_an_unreachable_domino_is_reported_as_not_checked_rather_than_as_a_bad_token(monkeypatch):
    _answers(monkeypatch, OSError("no route to host"))
    result = verify_credential(URL, TOKEN)
    assert not result.ok and "could not reach" in result.message


def test_the_credential_goes_out_as_basic_with_the_token_as_both_halves(monkeypatch):
    # The one shape a Model API accepts. Bearer and X-Domino-Api-Key both 401 against a model, even
    # though the latter is what Domino's docs say — model invocation is a separate auth domain.
    post = _answers(monkeypatch, _Response(200, "{}"))
    verify_credential(URL, TOKEN)
    assert post.seen["auth"] == (TOKEN, TOKEN)
    assert post.seen["json"] == {"data": {}}


# ---- The store -----------------------------------------------------------------------------


def test_a_stored_credential_comes_back_and_is_listed(tmp_path: Path):
    store = CredentialStore(tmp_path)
    assert store.get(MODEL_ID) is None and store.ids() == set()
    store.put(MODEL_ID, Credential(URL, TOKEN))
    assert store.get(MODEL_ID) == Credential(URL, TOKEN)
    assert store.ids() == {MODEL_ID}


def test_the_store_is_gitignored_before_it_is_written(tmp_path: Path):
    # Ordering, not just presence: a credential file that lands before its ignore rule can be staged
    # by anything watching the tree in between.
    (tmp_path / ".gitignore").write_text("node_modules\n")
    CredentialStore(tmp_path).put(MODEL_ID, Credential(URL, TOKEN))
    assert IGNORE_RULE in (tmp_path / ".gitignore").read_text().split()


def test_the_ignore_rule_is_added_once_however_often_a_credential_is_saved(tmp_path: Path):
    (tmp_path / ".gitignore").write_text("dist\n")
    store = CredentialStore(tmp_path)
    for _ in range(3):
        store.put(MODEL_ID, Credential(URL, TOKEN))
    assert (tmp_path / ".gitignore").read_text().split().count(IGNORE_RULE) == 1


def test_the_store_file_is_not_world_readable(tmp_path: Path):
    CredentialStore(tmp_path).put(MODEL_ID, Credential(URL, TOKEN))
    mode = stat.S_IMODE((tmp_path / ".sage" / "model-api-credentials.json").stat().st_mode)
    assert mode == 0o600


def test_a_half_written_entry_is_treated_as_absent(tmp_path: Path):
    # A hand-edited or truncated file must read as "ask again", never as a credential with an empty
    # token that the app would then ship and fail on.
    path = tmp_path / ".sage" / "model-api-credentials.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({MODEL_ID: {"url": URL, "token": ""}, "other": "nonsense"}))
    store = CredentialStore(tmp_path)
    assert store.get(MODEL_ID) is None and store.ids() == set()


def test_unreadable_json_reads_as_empty_rather_than_raising(tmp_path: Path):
    path = tmp_path / ".sage" / "model-api-credentials.json"
    path.parent.mkdir(parents=True)
    path.write_text("{ not json")
    assert CredentialStore(tmp_path).ids() == set()


# ---- What gets written into the app --------------------------------------------------------


def test_the_generated_config_carries_the_url_and_token_and_warns_about_the_bundle():
    text = render_config(Binding(KIND_MODEL_API, MODEL_ID, "churn-risk", "churn-risk"), Credential(URL, TOKEN))
    assert f'"{URL}"' in text and f'"{TOKEN}"' in text
    # The exposure is the whole reason this file is different from sageLlm.config.ts. Whoever opens
    # it later must not have to reconstruct why a secret is sitting in a committed file.
    assert "CAN READ IT" in text


def test_a_binding_whose_credential_has_gone_renders_as_no_model_api():
    text = render_config(Binding(KIND_MODEL_API, MODEL_ID, "churn-risk", "churn-risk"), None)
    assert "name: null" in text and "url: null" in text and "token: null" in text


def test_the_first_model_api_binding_wins_like_the_alias_pin():
    bindings = [
        Binding(KIND_MODEL_API, "first", "a", "a"),
        Binding(KIND_MODEL_API, "second", "b", "b"),
    ]
    assert pinned_model_api(bindings).id == "first"


def test_the_agents_block_is_empty_with_nothing_pinned_and_names_the_model_otherwise():
    assert agents_block(None) == ""
    block = agents_block(Binding(KIND_MODEL_API, MODEL_ID, "churn-risk", "churn-risk"))
    assert "churn-risk" in block and "callModelApi" in block
    # The agent's two failure modes: writing its own fetch, and guessing an input shape no Model API
    # publishes. Both are addressed explicitly, so a regression in the wording is worth catching.
    assert "never write a URL, token or `fetch` yourself" in block
    assert "Sage does not know its shape" in block


# ---- End to end through the orchestrator ---------------------------------------------------


def _orch(tmp_path: Path) -> Orchestrator:
    template = tmp_path / "template"
    (template / "src").mkdir(parents=True)
    (template / "src" / "App.tsx").write_text("placeholder")
    (template / "src" / "sageModelApi.ts").write_text("// helper")
    (template / "package.json").write_text("{}")
    orch = Orchestrator(
        workspace_dir=tmp_path / "mnt" / "code",
        template=template,
        gateway=FakeGatewayClient(),
        catalog=ModelCatalog("sq", "sq", "sq", "p", "i", "a"),
        project_id="Sage",
        resources=FakeResourceProvider(list(ALIASES), list(MODEL_APIS)),
    )
    orch.project(start_preview=False)  # memoize it, so nothing under test starts a dev server
    return orch


def test_a_model_api_cannot_be_bound_before_its_token_has_been_pasted(tmp_path: Path):
    # The invariant behind the whole feature: a recorded Model API is one the app can call. Without
    # this a Binding would report a working dependency for a model that opens for nothing.
    orch = _orch(tmp_path)
    with pytest.raises(CredentialRequired):
        orch.bind_model_api(MODEL_ID)
    assert orch.list_bindings() == []


def test_a_verified_paste_is_remembered_and_binding_then_writes_the_app_config(tmp_path: Path, monkeypatch):
    _answers(monkeypatch, _Response(400, "predict() missing 1 required argument"))
    orch = _orch(tmp_path)

    assert orch.save_model_api_credential(MODEL_ID, JQUERY) == {"ok": True, "url": URL}
    assert orch.model_api_credential_ids() == [MODEL_ID]

    orch.bind_model_api(MODEL_ID)
    config = (orch.project().workspace.path / CONFIG_PATH).read_text()
    assert TOKEN in config and URL in config
    assert "## The app's Model API" in (orch.project().workspace.path / "AGENTS.md").read_text()


def test_the_second_app_using_the_same_model_is_never_asked_again(tmp_path: Path, monkeypatch):
    # "Remember it and do not ask again for that Model API": once stored, the id is in the set the
    # rail reads, so the Use button binds straight away instead of opening the form.
    _answers(monkeypatch, _Response(200, "{}"))
    orch = _orch(tmp_path)
    orch.save_model_api_credential(MODEL_ID, JQUERY)
    orch.bind_model_api(MODEL_ID)
    orch.unbind(KIND_MODEL_API, MODEL_ID)
    assert orch.model_api_credential_ids() == [MODEL_ID]  # survives the unbind


def test_a_snippet_from_a_different_model_is_refused_rather_than_stored(tmp_path: Path, monkeypatch):
    # Two Overview tabs open, wrong one copied. Without this check the app calls somebody else's
    # model and every mismatch reads as a bad request body.
    called = _answers(monkeypatch, _Response(200, "{}"))
    orch = _orch(tmp_path)
    result = orch.save_model_api_credential("some-other-model", JQUERY)
    assert not result["ok"] and "different Model API" in result["error"]
    assert orch.model_api_credential_ids() == []
    assert not hasattr(called, "seen")  # refused before the model was ever called


def test_a_refused_token_is_reported_and_not_stored(tmp_path: Path, monkeypatch):
    _answers(monkeypatch, _Response(401, ""))
    orch = _orch(tmp_path)
    result = orch.save_model_api_credential(MODEL_ID, JQUERY)
    assert not result["ok"] and "regenerated" in result["error"]
    assert orch.model_api_credential_ids() == []


def test_unbinding_clears_the_token_out_of_the_app_source(tmp_path: Path, monkeypatch):
    # The store keeps the credential so Sage does not ask again, but the app that no longer uses the
    # model must stop shipping its token to every viewer.
    _answers(monkeypatch, _Response(200, "{}"))
    orch = _orch(tmp_path)
    orch.save_model_api_credential(MODEL_ID, JQUERY)
    orch.bind_model_api(MODEL_ID)
    orch.unbind(KIND_MODEL_API, MODEL_ID)
    config = (orch.project().workspace.path / CONFIG_PATH).read_text()
    assert TOKEN not in config and "token: null" in config
    assert "## The app's Model API" not in (orch.project().workspace.path / "AGENTS.md").read_text()


def test_the_route_never_returns_a_stored_token(tmp_path: Path, monkeypatch):
    from fastapi.testclient import TestClient

    import sage.orchestrator.app as appmod

    _answers(monkeypatch, _Response(200, "{}"))
    orch = _orch(tmp_path)
    orch.save_model_api_credential(MODEL_ID, JQUERY)
    monkeypatch.setattr(appmod, "orchestrator", orch)
    client = TestClient(appmod.control_app)

    listed = client.get("/api/model-api-credentials")
    assert listed.status_code == 200
    assert listed.json() == {"ids": [MODEL_ID]}
    assert TOKEN not in listed.text


def test_the_route_reports_a_bad_paste_as_an_answer_not_as_a_failure(tmp_path: Path, monkeypatch):
    # 200 with ok=false, because this is the creator's to fix in the form they are looking at. An
    # HTTP error would make the rail render "Sage broke" over an answer that says what to paste.
    from fastapi.testclient import TestClient

    import sage.orchestrator.app as appmod

    monkeypatch.setattr(appmod, "orchestrator", _orch(tmp_path))
    client = TestClient(appmod.control_app)

    res = client.post("/api/model-api-credentials", json={"id": MODEL_ID, "snippet": "nothing useful"})
    assert res.status_code == 200
    assert res.json()["ok"] is False
    assert "neither the model's URL nor its access token" in res.json()["error"]


def test_binding_without_a_credential_is_a_409_that_says_what_to_paste(tmp_path: Path, monkeypatch):
    from fastapi.testclient import TestClient

    import sage.orchestrator.app as appmod

    monkeypatch.setattr(appmod, "orchestrator", _orch(tmp_path))
    client = TestClient(appmod.control_app)

    res = client.post("/api/bindings", json={"kind": KIND_MODEL_API, "id": MODEL_ID})
    assert res.status_code == 409
    assert "copy the sample request" in res.json()["error"]
