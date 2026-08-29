"""The orchestrator service's user-visible strings carry the pack's words (#106).

One migrate batch of ADR-0014. What is worth pinning is not each of the fifty-odd literals but the
three-way rule in the ADR's title, because the interesting strings carry more than one arm at once:

- **prose re-brands** — `{assistantName}`, `{platformName}` and the noun map, resolved where the
  string is written;
- **identifiers and paths stay** — `DOMINO_PROJECT_ID`, `domino_data.datasets`, `/mnt/code`;
- **text Sage did not write keeps its words** — a platform error body passed through, and a
  Resource name the creator chose.

Every test below boots a partner pack. The default pack renders these exactly as the literals used
to read, which is why the rest of the suite did not move — and which is also why a test run against
the default could not tell a resolved string from an unresolved one.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from sage.gateway.client import FakeGatewayClient
from sage.orchestrator import brand
from sage.orchestrator.service import (
    Orchestrator,
    _app_display_name,
    _chat_context_line,
    turn_busy_message,
    turn_context_changed_message,
    turn_pending_message,
)
from sage.provision.domino import FakeControlPlane
from sage.resources.provider import DataSource, FakeResourceProvider
from sage.router.models import ModelCatalog

SOURCE = DataSource("ds-dwh", "Snowflake-Data-Warehouse", "Snowflake", "Shared",
                    None, True, connector_type="SnowflakeConfig")


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
            "builtApp": {"singular": "Creation", "plural": "Creations"},
        },
    }))
    monkeypatch.setenv("SAGE_BRAND_FILE", str(path))
    return path


# ---- the three sentences a turn answers with (#79, #97) ---------------------------------------


def test_the_turn_sentences_name_the_packs_agent(acme):
    assert "Ada cannot start another one here" in turn_busy_message(wedged=True)
    assert "Ada runs one turn at a time" in turn_pending_message(1)
    assert "so Ada did not run it" in turn_context_changed_message()


def test_an_ordinary_refusal_names_nobody_and_is_left_alone(acme):
    """The non-wedged half names no product, so the overlay has nothing to do to it. Asserted so a
    later edit cannot introduce a bare name here and pass on the wedged sentence alone."""
    assert turn_busy_message(wedged=False, action="publish") == (
        "A build is already running. Wait for it to finish or stop it first, then publish."
    )


def test_the_queue_position_still_counts_turns(acme):
    """The count rides in as a value, so re-branding the sentence did not cost it its subject."""
    assert "Queued behind 3 turns." in turn_pending_message(3)


# ---- the Session-context rows sage-chat reads --------------------------------------------------
#
# The agent SPEAKS the mapped nouns (brand.md, Voice). These rows are where it learns them for a
# Resource, and they are the sharpest case of one sentence carrying several roles at once.


def test_a_mounted_dataset_row_renames_the_noun_and_not_the_name(acme):
    line = _chat_context_line(
        {"kind": "dataset", "name": "domino-demo", "path": "/mnt/data/domino-demo"}
    )
    assert line.startswith("- Cube domino-demo, files at /mnt/data/domino-demo.")
    assert "Dataset" not in line


def test_an_unmounted_dataset_row_keeps_the_import_path_it_names(acme):
    """Prose and identifier in one breath: the library is the platform's, so the WORD moves — the
    module path `domino_data.datasets` is code the agent is about to type, so it does not."""
    line = _chat_context_line({"kind": "dataset", "name": "sales", "id": "ds-1", "project": "Demo"})
    assert "- Cube sales (project Demo). Not mounted here, so read it with the Acme Cloud data" in line
    assert "from domino_data.datasets import DatasetClient" in line
    assert "that is not this Cube." in line


def test_a_dataset_with_no_identifier_says_so_in_the_packs_words(acme):
    line = _chat_context_line({"kind": "dataset", "name": "sales"})
    assert "- Cube sales. Ada has no identifier for it" in line
    assert "that is not this Cube." in line


def test_a_file_inside_a_dataset_carries_both_treatments(acme):
    line = _chat_context_line(
        {"kind": "file", "name": "q3.csv", "id": "ds-1", "datasetId": "ds-1",
         "datasetName": "sales", "datasetRelPath": "q3.csv"}
    )
    assert "- file q3.csv in Cube sales." in line
    assert "with the Acme Cloud data library" in line
    assert "from domino_data.datasets import DatasetClient" in line


def test_a_file_in_an_unidentified_dataset_says_so_in_the_packs_words(acme):
    line = _chat_context_line(
        {"kind": "file", "name": "q3.csv", "datasetId": "ds-1", "datasetRelPath": "q3.csv"}
    )
    assert "Ada has no identifier for that Cube" in line
    assert "Dataset" not in line


def test_a_scoped_table_row_renames_the_noun_and_quotes_the_source_as_given(acme):
    line = _chat_context_line({
        "kind": "table", "name": "clickstream", "sourceName": "BigQuery_Demo",
        "scope": {"database": "db", "schema": "public", "table": "clickstream"},
        "columns": [{"name": "id", "type": "INT64"}],
    })
    assert line.startswith("- Warehouse BigQuery_Demo, table ")
    assert "get_datasource('BigQuery_Demo')" in line
    assert "Data Source" not in line


def test_an_unqueryable_data_source_row_renames_the_noun(acme):
    line = _chat_context_line({"kind": "data_source", "name": "Snowflake_Prod"})
    assert line == (
        "- Warehouse Snowflake_Prod. This workspace cannot query it live. Do not invent rows. "
        "Say that you cannot open it."
    )


def test_a_resource_named_after_a_brand_token_is_never_resolved(acme):
    """The reason substitution is author-time. A creator may name a Dataset `{dataset}`; a filter
    over outgoing bytes could not tell that from our own word, and the helper does not have to —
    a substituted value is not scanned again."""
    line = _chat_context_line({"kind": "dataset", "name": "{dataset}", "path": "/mnt/data/x"})
    assert line.startswith("- Cube {dataset}, files at /mnt/data/x.")


# ---- the rail's name for an app nobody named ---------------------------------------------------


class _BlankWorkspace:
    """Just enough Workspace for `_app_display_name`: no stored name and no plan to borrow one
    from, which is the only path that reaches the fallback."""

    def display_name(self) -> str:
        return ""

    def read_plan(self) -> str:
        return ""

    def read_archived_plan(self) -> str:
        return ""


def test_an_unnamed_app_is_named_for_what_it_is_in_the_packs_words(acme):
    assert _app_display_name(_BlankWorkspace()) == "Unnamed Creation"


def test_a_caller_supplied_fallback_is_a_name_somebody_chose(acme):
    """Publish passes the Domino project's name. That is the user's word rather than ours, so it is
    used exactly as it arrived even when it says Domino."""
    assert _app_display_name(_BlankWorkspace(), "domino-quickstart") == "domino-quickstart"


# ---- what a refused publish says ---------------------------------------------------------------


def _template(tmp: Path) -> Path:
    t = tmp / "template"
    (t / "src").mkdir(parents=True, exist_ok=True)
    (t / "src" / "App.tsx").write_text("placeholder")
    (t / "package.json").write_text("{}")
    (t / "app.sh").write_text("#!/bin/bash\nexec npx vite preview\n")
    return t


def _orch(tmp: Path, *, control_plane: FakeControlPlane | None = None,
          domino_project_id: str = "proj-1") -> Orchestrator:
    orch = Orchestrator(
        workspace_dir=tmp / "mnt" / "code",
        template=_template(tmp),
        gateway=FakeGatewayClient(),
        catalog=ModelCatalog("sq", "sq", "sq", "p", "i", "a"),
        project_id="Sage",
        resources=FakeResourceProvider(data_sources=[SOURCE]),
        control_plane=control_plane,
        domino_project_id=domino_project_id,
        domino_project_name="Sales dashboard",
    )
    orch.project(start_preview=False)
    return orch


def test_publishing_from_the_workbenchs_own_app_refuses_in_the_packs_words(acme, tmp_path,
                                                                          monkeypatch):
    """Three roles in two sentences: the workspace and the product are ours and move, `/mnt/code`
    is a path and does not."""
    monkeypatch.setenv("SAGE_PROXY_MODE", "app")
    orch = _orch(tmp_path, control_plane=FakeControlPlane())

    with pytest.raises(RuntimeError) as e:
        orch.publish()
    assert str(e.value) == (
        "Publish is only available in a Ada Builder workspace whose app repo is /mnt/code. "
        "This Acme Studio App is Ada itself, not a Creation."
    )


def test_publishing_off_platform_names_the_pack_and_keeps_the_env_var(acme, tmp_path):
    """`DOMINO_PROJECT_ID` is the name of an environment variable, so it is named, not renamed —
    the whole point of telling somebody which variable is missing is that they can go and set it."""
    orch = _orch(tmp_path, control_plane=None, domino_project_id="")

    with pytest.raises(RuntimeError) as e:
        orch.publish()
    assert str(e.value) == (
        "Publish is only available when this builder runs on Acme Cloud (missing control-plane or "
        "DOMINO_PROJECT_ID)."
    )


def test_publish_status_off_platform_names_the_pack(acme, tmp_path):
    orch = _orch(tmp_path, control_plane=None)
    with pytest.raises(RuntimeError) as e:
        orch.publish_status("app-1")
    assert str(e.value) == (
        "Publish status is only available when this builder runs on Acme Cloud."
    )


def test_an_app_that_reads_no_store_says_so_in_the_packs_noun(acme, tmp_path):
    orch = _orch(tmp_path, control_plane=FakeControlPlane())
    with pytest.raises(LookupError) as e:
        orch._binding_for(orch.project(start_preview=False), "")
    assert str(e.value) == "This app is not recorded as using a Warehouse."


def test_a_mismatched_snippet_names_the_packs_noun(acme, tmp_path):
    """A complete paste for the WRONG model: two Overview tabs open is how this happens, so the
    refusal has to name the thing they were adding in the words the rest of the rail uses."""
    token = "SsQBZCygwPP79P8Q57qLPrGIfj67YAFBm3nrTT6Sm7vuPhBPBJvAL7lHm6jp36qB"
    url = "https://cloud-dogfood.domino.tech:443/models/6a8727f40ff0450030085fb3/latest/model"
    orch = _orch(tmp_path, control_plane=FakeControlPlane())

    answer = orch.save_model_api_credential(
        "a-different-model-id",
        f"curl -X POST {url} -H 'Content-Type: application/json' -u {token}:{token} -d '{{}}'",
    )
    assert answer["ok"] is False
    assert answer["error"] == (
        "That snippet is for a different Model Endpoint. Copy the sample request from the "
        "Overview page of the model you are adding."
    )


# ---- text the platform wrote ------------------------------------------------------------------


def test_a_passed_through_platform_error_keeps_its_own_words(acme):
    """The sentence around it is ours and re-brands; the platform's body is not ours to rewrite, so
    it arrives as a value and comes out as it went in — Domino and all. This is the shape every
    pass-through in the file uses (a failed Chat step, a refused App deletion)."""
    assert brand.text("{assistantName} could not finish this turn — {reason}",
                      reason="Domino returned 502 for /api/datasetrw/v2/datasets") == (
        "Ada could not finish this turn — Domino returned 502 for /api/datasetrw/v2/datasets"
    )
