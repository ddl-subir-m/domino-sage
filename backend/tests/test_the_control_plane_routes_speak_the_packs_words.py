"""The control-plane routes' user-visible strings carry the pack's words (#107).

One migrate batch of ADR-0014. Every string below leaves `app.py` as `{"error": …}` and the
Workbench renders it verbatim: `api.js:27` reads `payload.detail || payload.error ||
payload.message` and hands it on as `err.message`. That is the surface the whole overlay exists
for — a partner meets the real names the first time anything fails.

What is worth pinning is not each literal but the three-way rule in the ADR's title, and the
strings that carry more than one arm of it at once:

  - **prose re-brands** — `{assistantName}`, `{platformName}` and the noun map;
  - **identifiers and paths stay** — `DOMINO_API_HOST` and the git host in the same sentence;
  - **text Sage did not write keeps its words** — a platform page's own name, a pass-through body.

Every test boots a partner pack, because the default pack renders exactly the literals these
routes used to hold: a run against Domino's own defaults could not tell a resolved string from an
unresolved one.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

APP_PY = Path(__file__).resolve().parents[1] / "sage" / "orchestrator" / "app.py"


@pytest.fixture(autouse=True)
def _isolate_brand(monkeypatch, tmp_path):
    monkeypatch.setattr("sage.orchestrator.brand._BAKED", tmp_path / "no-baked-brand.json")
    monkeypatch.delenv("SAGE_BRAND_FILE", raising=False)
    monkeypatch.setattr("sage.orchestrator.brand._WARNED", set())


@pytest.fixture
def acme(tmp_path, monkeypatch):
    """A partner who renamed everything: the product, the agent, the platform and the nouns.

    `platformName` is set on the understanding that the partner set the platform's own
    /admin/whitelabel first — the precondition brand.md states and Sage does not verify.
    """
    path = tmp_path / "brand.json"
    path.write_text(json.dumps({
        "productName": "Acme Studio",
        "assistantName": "Ada",
        "platformName": "Acme Cloud",
        "nouns": {
            "dataset": {"singular": "Cube", "plural": "Cubes"},
            "dataSource": {"singular": "Warehouse", "plural": "Warehouses"},
            "modelApi": {"singular": "Model Endpoint", "plural": "Model Endpoints"},
            "llmAlias": {"singular": "Model Name", "plural": "Model Names"},
        },
    }))
    monkeypatch.setenv("SAGE_BRAND_FILE", str(path))
    return path


@pytest.fixture
def client():
    import sage.orchestrator.app as appmod

    return TestClient(appmod.control_app)


def _error(response) -> str:
    return response.json()["error"]


# ---- the door and the chip, off the platform ---------------------------------------------------


def test_the_door_names_the_pack_when_it_cannot_reach_the_platform(acme, client):
    """Four brand words in one sentence, two of them different: the agent that cannot reach, the
    platform it cannot reach, the builder it cannot open, and the platform's API host. The App's
    Environment and the Git credential are the platform's own things and keep their words."""
    r = client.post("/api/door")

    assert r.status_code == 503
    assert _error(r) == (
        "Ada can't reach Acme Cloud from this App, so it can't open your Ada Builder. Check the "
        "App's Environment has the Acme Cloud API host and a Git credential, then restart it."
    )


def test_creating_a_project_off_the_platform_names_the_pack(acme, client):
    r = client.post("/api/projects", json={"name": "Sales"})

    assert r.status_code == 503
    assert _error(r) == (
        "Ada can't reach Acme Cloud from this container, so it can't create a Project. This build "
        "runs against the project it is bound to."
    )


def test_opening_another_project_off_the_platform_names_the_pack(acme, client):
    r = client.post("/api/projects/p-1/open")

    assert r.status_code == 503
    assert _error(r) == (
        "Ada can't reach Acme Cloud from this container, so it can't open another Project. This "
        "build runs against the project it is bound to."
    )


def test_the_missing_git_credential_names_the_pack_and_keeps_the_git_host(acme, monkeypatch):
    """Three arms in one sentence. The agent to restart re-brands; `github.com` is the literal the
    credential is actually stored under; and `Account Settings > Git Credentials` is the platform's
    own menu path, which Sage cannot rename and does not try to."""
    import sage.orchestrator.app as appmod
    from sage.provision import credentials

    monkeypatch.setattr(credentials, "extract_token", lambda host: "")
    monkeypatch.setenv("SAGE_GIT_HOST", "github.com")
    service = appmod._build_provision_service(object())

    with pytest.raises(RuntimeError) as e:
        service._push_token_provider()

    assert str(e.value) == (
        "no HTTPS git credential for github.com in this container (an SSH-key credential can't be "
        "extracted). Add an HTTPS Git credential under Account Settings > Git Credentials, then "
        "restart Ada."
    )


# ---- Resources the platform would not hand over ------------------------------------------------


def test_a_data_source_the_platform_will_not_show_names_the_pack(acme, client, monkeypatch):
    import sage.orchestrator.app as appmod

    def boom(*a):
        raise LookupError()

    monkeypatch.setattr(appmod.orchestrator, "list_data_source_databases", boom)
    r = client.get("/api/data-sources/ds-1/databases")

    assert r.status_code == 404
    assert _error(r) == "That Warehouse is not one Acme Cloud offers you, so Ada cannot look inside it."


def test_binding_refusals_name_the_packs_platform_and_nouns(acme, client, monkeypatch):
    """Three kinds, three sentences, three different reasons — and each names the noun the rail
    used to offer the thing under."""
    import sage.orchestrator.app as appmod
    from sage.resources.model_api_credentials import CredentialRequired

    def boom(*a, **k):
        raise LookupError()

    monkeypatch.setattr(appmod.orchestrator, "bind_data_source", boom)
    monkeypatch.setattr(appmod.orchestrator, "bind_llm_alias", boom)
    monkeypatch.setattr(appmod.orchestrator, "bind_model_api", boom)

    ds = client.post("/api/bindings", json={"kind": "data_source", "id": "ds-1"})
    assert ds.status_code == 404
    assert _error(ds) == (
        "That Warehouse is not one Acme Cloud offers you, so the app cannot depend on it."
    )

    alias = client.post("/api/bindings", json={"kind": "llm_alias", "id": "a-1"})
    assert alias.status_code == 404
    assert _error(alias) == "That Model Name is not one you can use, so the app cannot depend on it."

    api = client.post("/api/bindings", json={"kind": "model_api", "id": "m-1"})
    assert api.status_code == 404
    assert _error(api) == (
        "Acme Cloud would not describe that Model Endpoint to you, and Ada holds no access token "
        "for it, so the app cannot depend on it. Paste its sample request to add it."
    )

    def needs_credential(*a, **k):
        raise CredentialRequired("m-1")

    monkeypatch.setattr(appmod.orchestrator, "bind_model_api", needs_credential)
    r = client.post("/api/bindings", json={"kind": "model_api", "id": "m-1"})
    assert r.status_code == 409
    assert _error(r) == (
        "Ada needs this Model Endpoint's access token before an app can call it. Open the Model "
        "Endpoint's Overview page in Acme Cloud, copy the sample request, and paste it into Ada."
    )


def test_the_paste_prompt_names_the_packs_noun_and_platform(acme, client):
    r = client.post("/api/model-api-credentials", json={"id": "m-1", "snippet": "   "})

    assert _error(r) == (
        "Paste the sample request from the Model Endpoint's Overview page in Acme Cloud."
    )


def test_an_app_that_reads_no_store_names_the_packs_noun(acme, client, monkeypatch):
    """The service's own copy of this sentence already reads through the pack; this is the fallback
    the route holds for when the service raised without one, and the two have to agree."""
    import sage.orchestrator.app as appmod

    def boom(*a, **k):
        raise LookupError()

    monkeypatch.setattr(appmod.orchestrator, "share_sample_rows", boom)
    r = client.post("/api/project/samples", json={"tables": ["T"]})

    assert r.status_code == 404
    assert _error(r) == "This app is not recorded as using a Warehouse."


# ---- Assets --------------------------------------------------------------------------------


def test_the_asset_routes_name_the_packs_dataset(acme, client, monkeypatch):
    import sage.orchestrator.app as appmod
    from sage.orchestrator.service import UploadUnavailable

    def missing(*a, **k):
        raise LookupError()

    monkeypatch.setattr(appmod.orchestrator, "list_asset_files", missing)
    assert _error(client.get("/api/project/assets/ds-1/files")) == "Cube not found"

    monkeypatch.setattr(appmod.orchestrator, "attach_file", missing)
    r = client.post("/api/project/assets/ds-1/files/attach", json={"path": "a.csv"})
    assert _error(r) == "Cube not found"

    def no_file(*a, **k):
        raise FileNotFoundError()

    monkeypatch.setattr(appmod.orchestrator, "attach_file", no_file)
    r = client.post("/api/project/assets/ds-1/files/attach", json={"path": "a.csv"})
    assert _error(r) == "file not found in the Cube"

    def unavailable(*a, **k):
        raise UploadUnavailable()

    monkeypatch.setattr(appmod.orchestrator, "upload_file", unavailable)
    monkeypatch.setattr(appmod.orchestrator, "upload_scratch", unavailable)
    picked = client.post("/api/project/upload?name=a.csv&dataset=ds-1", content=b"x")
    assert _error(picked) == "The Cube you picked isn't mounted and writable in this workspace."
    none = client.post("/api/project/upload?name=a.csv", content=b"x")
    assert _error(none) == "No writable Cube is available to store uploads in this project."

    monkeypatch.setattr(appmod.orchestrator, "promote_scratch_to_dataset", unavailable)
    r = client.post("/api/project/scratch/promote", json={"path": "a.csv", "dataset": "ds-1"})
    assert _error(r) == "The Cube you picked isn't mounted and writable in this workspace."

    monkeypatch.setattr(appmod.orchestrator, "promote_scratch_to_dataset", missing)
    r = client.post("/api/project/scratch/promote", json={"path": "a.csv", "dataset": "ds-1"})
    assert _error(r) == "Cube not found"


# ---- the guard ---------------------------------------------------------------------------------


def _rendered_literals() -> list[tuple[int, str]]:
    """Every string literal this file hands the Workbench to render verbatim.

    The positions `api.js:27` reads — `detail`, `error`, `message` — plus the `RuntimeError`
    messages the routes turn into one of them with `str(e)`. Deliberately AST over those positions
    rather than a grep over the source: this file is mostly prose explaining itself, and a grep
    breaks the moment somebody writes a comment (ADR-0014, "Proof").

    It follows one hop through a local name, because several refusals are built a line or two
    above the response they ride in (`bind, missing = …`, `msg = "…" if x else "…"`). A guard that
    stopped at the dict would have been blind to exactly the sentences that are hardest to spot.
    """
    tree = ast.parse(APP_PY.read_text())
    out: list[tuple[int, str]] = []

    def literals(node, into):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            into.append((node.lineno, node.value))
        elif isinstance(node, ast.JoinedStr):          # f"…{value}…": the written half is ours
            for part in node.values:
                literals(part, into)
        elif isinstance(node, ast.IfExp):              # msg = "…" if x else "…"
            literals(node.body, into)
            literals(node.orelse, into)
        elif isinstance(node, ast.BoolOp):             # str(e) or "…"
            for value in node.values:
                literals(value, into)

    assigned: dict[str, list[tuple[int, str]]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets, values = node.targets[0], node.value
            pairs = (zip(targets.elts, values.elts)
                     if isinstance(targets, ast.Tuple) and isinstance(values, ast.Tuple)
                     else [(targets, values)])
            for target, value in pairs:
                if isinstance(target, ast.Name):
                    literals(value, assigned.setdefault(target.id, []))

    def collect(node):
        if isinstance(node, ast.Name):
            out.extend(assigned.get(node.id, []))
        else:
            literals(node, out)

    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and key.value in ("detail", "error", "message"):
                    collect(value)
        elif isinstance(node, ast.Call) and getattr(node.func, "id", None) == "RuntimeError":
            for arg in node.args:
                collect(arg)
    return out


def test_no_route_can_answer_with_a_name_the_pack_cannot_replace():
    """The batch's own regression guard, and the reason it is a test rather than a review note.

    A name reaching this list is a partner meeting `Sage` or `Domino` at the first failure. Nouns
    are checked in their Title Case form only: a lowercase `dataset` inside `dataset_id` is an
    identifier, and `Datasets` in a log line is invisible to a person.
    """
    banned = ("Sage", "Domino", "ML Studio", "Data Source", "Model API", "LLM Alias", "Dataset",
              "Built App", "Gallery")
    leaks = [(line, text, word)
             for line, text in _rendered_literals()
             for word in banned if word in text]

    assert leaks == []
