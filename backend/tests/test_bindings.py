"""Bindings — the recorded link between a Built App and a Resource it uses (#6).

Every test goes through the orchestrator on the fake provider, so nothing reaches a gateway. What is
worth pinning here is the manifest: that it is its OWN file (the attachments consumer drops entries
it does not recognise without a word), that a write publishes atomically, and that the record
outlives the process.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

from sage.gateway.client import FakeGatewayClient
from sage.orchestrator.service import Orchestrator
from sage.resources.bindings import KIND_LLM_ALIAS, KIND_MODEL_API, Binding, parse_bindings
from sage.resources.provider import FakeResourceProvider, LlmAlias, ModelApi
from sage.router.models import ModelCatalog

ALIASES = [
    LlmAlias("id-sonnet", "sonnet", "Claude Sonnet 4.6", None, ["chat"], {"input": 3.0}),
    LlmAlias("id-embed", "text-embedding-3-small", "Text Embedding 3 Small", None, ["embeddings"], {}),
]

# One running and one that is not, because a stopped Model API is still one an app can be built
# against — someone starts it before the app ships — so status must not gate the record.
MODEL_APIS = [
    ModelApi("id-churn", "churn-risk", "Scores an account's chance of cancelling.", "Running"),
    ModelApi("id-demand", "demand-forecast", None, "Stopped"),
]


def _template(tmp: Path) -> Path:
    t = tmp / "template"
    (t / "src").mkdir(parents=True, exist_ok=True)
    (t / "src" / "App.tsx").write_text("placeholder")
    (t / "package.json").write_text("{}")
    return t


def _orch(tmp_path: Path) -> Orchestrator:
    """A real workspace on disk — the manifest is the thing under test, so it is not faked."""
    orch = Orchestrator(
        workspace_dir=tmp_path / "mnt" / "code",
        template=_template(tmp_path),
        gateway=FakeGatewayClient(),
        catalog=ModelCatalog("sq", "sq", "sq", "p", "i", "a"),
        project_id="Sage",
        resources=FakeResourceProvider(list(ALIASES), list(MODEL_APIS)),
    )
    orch.project(start_preview=False)  # memoize it, so no method under test starts a dev server
    return orch


# ---- the record ---------------------------------------------------------------------------------


def test_binding_an_alias_records_the_label_the_row_showed():
    # The group has to render with the gateway down, so the manifest carries the label rather than a
    # pointer to fetch one.
    b = Binding(KIND_LLM_ALIAS, "id-sonnet", "sonnet", "Claude Sonnet 4.6")
    assert b.to_dict() == {"kind": "llm_alias", "id": "id-sonnet", "name": "sonnet",
                           "display_name": "Claude Sonnet 4.6"}


def test_an_entry_naming_nothing_is_dropped_and_an_unknown_kind_is_kept():
    got = parse_bindings([
        {"kind": "llm_alias", "id": "a"},
        {"id": "no-kind"},
        {"kind": "no-id"},
        "junk",
        {"kind": "data_source", "id": "z", "display_name": "Snowflake"},  # a newer Sage's record
    ])
    assert [(b.kind, b.id) for b in got] == [("llm_alias", "a"), ("data_source", "z")]
    assert got[0].display_name == "a"  # no label recorded -> the id is the only name there is


def test_a_bad_manifest_reads_as_empty_rather_than_crashing_the_panel(tmp_path: Path):
    orch = _orch(tmp_path)
    orch.project().workspace.bindings_path.parent.mkdir(parents=True, exist_ok=True)
    orch.project().workspace.bindings_path.write_text("{not json")
    assert orch.list_bindings() == []


# ---- create, list, remove ----------------------------------------------------------------------


def test_create_then_list(tmp_path: Path):
    orch = _orch(tmp_path)
    assert orch.list_bindings() == []
    out = orch.bind_llm_alias("id-sonnet")
    assert out == [{"kind": "llm_alias", "id": "id-sonnet", "name": "sonnet",
                    "display_name": "Claude Sonnet 4.6"}]
    assert orch.list_bindings() == out


def test_binding_the_same_alias_twice_leaves_one_row(tmp_path: Path):
    # A second click means "it is already up there", not "record it again".
    orch = _orch(tmp_path)
    orch.bind_llm_alias("id-sonnet")
    orch.bind_llm_alias("id-sonnet")
    assert len(orch.list_bindings()) == 1


def test_an_alias_the_caller_cannot_use_is_refused_rather_than_recorded(tmp_path: Path):
    # list_llm_aliases has already intersected the grants, so anything absent from it would be a
    # dependency on a call that cannot run.
    orch = _orch(tmp_path)
    try:
        orch.bind_llm_alias("id-not-granted")
    except LookupError:
        pass
    else:
        raise AssertionError("expected LookupError")
    assert orch.list_bindings() == []


def test_remove_drops_only_that_binding(tmp_path: Path):
    orch = _orch(tmp_path)
    orch.bind_llm_alias("id-sonnet")
    orch.bind_llm_alias("id-embed")
    left = orch.unbind(KIND_LLM_ALIAS, "id-sonnet")
    assert [b["id"] for b in left] == ["id-embed"]


def test_removing_something_already_gone_is_not_an_error(tmp_path: Path):
    orch = _orch(tmp_path)
    assert orch.unbind(KIND_LLM_ALIAS, "id-sonnet") == []


def test_an_id_is_only_unique_within_its_kind(tmp_path: Path):
    orch = _orch(tmp_path)
    orch.bind_llm_alias("id-sonnet")
    assert len(orch.unbind("data_source", "id-sonnet")) == 1  # same id, different kind: untouched


# ---- Model APIs (#9) ----------------------------------------------------------------------------


def test_binding_a_model_api_records_the_name_as_both_labels(tmp_path: Path):
    # A Model API has one name and no separate display name. Both fields carry it so the manifest is
    # one shape across kinds, and the row renders the name once rather than twice.
    orch = _orch(tmp_path)
    assert orch.bind_model_api("id-churn") == [
        {"kind": "model_api", "id": "id-churn", "name": "churn-risk", "display_name": "churn-risk"}
    ]


def test_a_stopped_model_api_can_still_be_recorded(tmp_path: Path):
    # Status says whether it would answer now, not whether it is worth composing with.
    orch = _orch(tmp_path)
    assert [b["name"] for b in orch.bind_model_api("id-demand")] == ["demand-forecast"]


def test_a_model_api_this_project_does_not_offer_is_refused(tmp_path: Path):
    # The listing is already scoped to this project and already permission-filtered by Domino, so
    # anything absent from it is not this app's to depend on.
    orch = _orch(tmp_path)
    try:
        orch.bind_model_api("id-someone-elses")
    except LookupError:
        pass
    else:
        raise AssertionError("expected LookupError")
    assert orch.list_bindings() == []


def test_binding_the_same_model_api_twice_leaves_one_row(tmp_path: Path):
    orch = _orch(tmp_path)
    orch.bind_model_api("id-churn")
    orch.bind_model_api("id-churn")
    assert len(orch.list_bindings()) == 1


def test_the_two_kinds_share_one_list_in_the_order_they_were_chosen(tmp_path: Path):
    orch = _orch(tmp_path)
    orch.bind_model_api("id-churn")
    orch.bind_llm_alias("id-sonnet")
    assert [(b["kind"], b["id"]) for b in orch.list_bindings()] == [
        ("model_api", "id-churn"), ("llm_alias", "id-sonnet"),
    ]


def test_a_model_api_recorded_first_does_not_take_the_alias_pin(tmp_path: Path):
    # The app's model is the first LLM ALIAS, not the first Binding (#7). A Model API recorded ahead
    # of one must not leave the app with no model pinned into its source.
    from sage.resources.pinned_model import CONFIG_PATH

    orch = _orch(tmp_path)
    orch.bind_model_api("id-churn")
    orch.bind_llm_alias("id-sonnet")
    assert '"sonnet"' in (orch.project().workspace.path / CONFIG_PATH).read_text()


# ---- persistence and the manifest itself --------------------------------------------------------


def test_bindings_survive_a_new_orchestrator_over_the_same_workspace(tmp_path: Path):
    _orch(tmp_path).bind_llm_alias("id-sonnet")
    assert [b["name"] for b in _orch(tmp_path).list_bindings()] == ["sonnet"]


def test_the_manifest_is_its_own_committed_file_and_not_the_attachment_one(tmp_path: Path):
    # The attachments consumer (template/react-vite/scripts/rehydrate-data.mjs) `continue`s past any
    # entry without path/dataset/dataset_rel_path and says nothing, so a Binding stored there would
    # be dropped in silence.
    orch = _orch(tmp_path)
    orch.bind_llm_alias("id-sonnet")
    ws = orch.project().workspace
    assert ws.bindings_path == ws.path / ".sage" / "bindings.json"
    assert json.loads(ws.bindings_path.read_text())[0]["name"] == "sonnet"
    assert ws.read_attachments() == []


def test_a_write_publishes_atomically_and_leaves_no_temp_behind(tmp_path: Path):
    # A stray .tmp inside committed .sage/ would land in the user's app repo.
    orch = _orch(tmp_path)
    orch.bind_llm_alias("id-sonnet")
    sage_dir = orch.project().workspace.path / ".sage"
    assert [p.name for p in sage_dir.glob("bindings.json*")] == ["bindings.json"]


def test_a_failed_write_leaves_the_previous_manifest_intact(tmp_path: Path):
    orch = _orch(tmp_path)
    orch.bind_llm_alias("id-sonnet")
    ws = orch.project().workspace

    def explode(entries: list[dict]) -> list[dict]:
        raise RuntimeError("boom")

    try:
        ws.update_bindings(explode)
    except RuntimeError:
        pass
    # os.replace publishes or does not; there is no half-written state to read.
    assert [b["name"] for b in orch.list_bindings()] == ["sonnet"]
    assert not list((ws.path / ".sage").glob("*.tmp"))


def test_two_writes_arriving_together_both_survive(tmp_path: Path):
    # This is the behaviour write_attachments does not have: unlocked read-modify-write drops one of
    # two concurrent edits.
    orch = _orch(tmp_path)
    done = threading.Barrier(2)

    def bind(alias_id: str):
        done.wait()
        orch.bind_llm_alias(alias_id)

    threads = [threading.Thread(target=bind, args=(a,)) for a in ("id-sonnet", "id-embed")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert sorted(b["id"] for b in orch.list_bindings()) == ["id-embed", "id-sonnet"]


# ---- through the routes -------------------------------------------------------------------------


def test_the_routes_create_list_and_remove(tmp_path: Path, monkeypatch):
    from fastapi.testclient import TestClient

    import sage.orchestrator.app as appmod

    monkeypatch.setattr(appmod, "orchestrator", _orch(tmp_path))
    client = TestClient(appmod.control_app)

    assert client.get("/api/bindings").json() == {"bindings": []}
    made = client.post("/api/bindings", json={"kind": KIND_LLM_ALIAS, "id": "id-sonnet"})
    assert made.status_code == 200
    assert made.json()["bindings"][0]["display_name"] == "Claude Sonnet 4.6"
    assert client.get("/api/bindings").json()["bindings"] == made.json()["bindings"]

    gone = client.delete(f"/api/bindings/{KIND_LLM_ALIAS}/id-sonnet")
    assert gone.status_code == 200 and gone.json() == {"bindings": []}


def test_the_route_refuses_an_alias_you_cannot_use_and_an_unknown_kind(tmp_path: Path, monkeypatch):
    from fastapi.testclient import TestClient

    import sage.orchestrator.app as appmod

    monkeypatch.setattr(appmod, "orchestrator", _orch(tmp_path))
    client = TestClient(appmod.control_app)

    nope = client.post("/api/bindings", json={"kind": KIND_LLM_ALIAS, "id": "id-not-granted"})
    assert nope.status_code == 404 and "not one you can use" in nope.json()["error"]

    # A different sentence for the other kind: "ask an admin for a grant" is the wrong advice for a
    # Model API that is simply not deployed in this project.
    no_api = client.post("/api/bindings", json={"kind": KIND_MODEL_API, "id": "id-someone-elses"})
    assert no_api.status_code == 404 and "not one this project offers" in no_api.json()["error"]

    bad_kind = client.post("/api/bindings", json={"kind": "sandwich", "id": "x"})
    assert bad_kind.status_code == 400 and "sandwich" in bad_kind.json()["error"]

    assert client.post("/api/bindings", json={"kind": KIND_LLM_ALIAS}).status_code == 400


def test_the_routes_record_and_remove_a_model_api(tmp_path: Path, monkeypatch):
    from fastapi.testclient import TestClient

    import sage.orchestrator.app as appmod

    monkeypatch.setattr(appmod, "orchestrator", _orch(tmp_path))
    client = TestClient(appmod.control_app)

    made = client.post("/api/bindings", json={"kind": KIND_MODEL_API, "id": "id-churn"})
    assert made.status_code == 200
    assert made.json()["bindings"][0]["display_name"] == "churn-risk"

    gone = client.delete(f"/api/bindings/{KIND_MODEL_API}/id-churn")
    assert gone.status_code == 200 and gone.json() == {"bindings": []}


def test_the_route_reports_a_domino_api_that_will_not_answer(tmp_path: Path, monkeypatch):
    # Model APIs come off the Domino API, not the gateway. It being down is not "you cannot use that
    # one" — a 404 would send the creator to go and check a permission that is fine.
    from fastapi.testclient import TestClient

    import sage.orchestrator.app as appmod
    from sage.resources.provider import ResourceUnavailable

    class Broken(FakeResourceProvider):
        def list_model_apis(self, project_id):
            raise ResourceUnavailable("The Domino API answered 503 at /api/modelServing/v1/modelApis.")

    orch = _orch(tmp_path)
    orch._resources = Broken()
    monkeypatch.setattr(appmod, "orchestrator", orch)
    client = TestClient(appmod.control_app)

    res = client.post("/api/bindings", json={"kind": KIND_MODEL_API, "id": "id-churn"})
    assert res.status_code == 502 and "The Domino API answered 503" in res.json()["error"]


def test_the_binding_list_answers_even_when_the_gateway_will_not(tmp_path: Path, monkeypatch):
    # The reason bindings are not part of /api/resources, which has nothing to offer for a kind whose
    # service is down.
    from fastapi.testclient import TestClient

    import sage.orchestrator.app as appmod
    from sage.resources.provider import ResourceUnavailable

    orch = _orch(tmp_path)
    orch.bind_llm_alias("id-sonnet")

    class Broken(FakeResourceProvider):
        def list_llm_aliases(self):
            raise ResourceUnavailable("The LLM Gateway answered 503 at /v1/models.")

    orch._resources = Broken()
    monkeypatch.setattr(appmod, "orchestrator", orch)
    client = TestClient(appmod.control_app)

    assert client.get("/api/resources").json()["llm_aliases"] == []
    assert [b["name"] for b in client.get("/api/bindings").json()["bindings"]] == ["sonnet"]
