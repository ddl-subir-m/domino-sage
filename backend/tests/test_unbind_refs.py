"""Unbinding a Resource says what the app still needs.

Removing the record is not removing the code. An app whose Summarise button calls an Alias the app
no longer has keeps the button, and the creator finds out in the preview with nothing on screen
connecting the dead button to the removal that caused it. `unbind` therefore reports `refs` the way
`detach_file` does, and the UI turns that into the cleanup offer.

The trap these tests exist for is the opposite failure: Sage's OWN files name every bound Resource by
definition, and `_write_app_resources` rewrites them during the very same unbind. Reporting one would
send the creator to fix a file AGENTS.md forbids the agent to touch — a warning that is always wrong,
on every unbind, which is worse than no warning at all.
"""
from __future__ import annotations

import json
from pathlib import Path

from sage.gateway.client import FakeGatewayClient
from sage.orchestrator.service import Orchestrator
from sage.resources.bindings import KIND_DATA_SOURCE, Binding
from sage.resources.app_helpers import TEMPLATE
from sage.resources.pinned_model import render_config

CONFIG_PATH = TEMPLATE.llm_config_path
HELPER_PATH = TEMPLATE.llm_path
from sage.resources.provider import FakeResourceProvider, LlmAlias
from sage.router.models import ModelCatalog

ALIASES = [
    LlmAlias("id-sonnet", "sonnet", "Claude Sonnet 4.6", None, ["chat"], {"input": 3.0}),
    LlmAlias("id-mimo", "mimo-v2.5", "MiMo 2.5", None, ["chat"], {}),
]
CATALOG = ModelCatalog(
    sovereign_plan="sonnet", sovereign_implement="sonnet", sovereign_ask="sonnet",
    plan="sonnet", implement="sonnet", ask="sonnet",
)


def _orch(tmp_path: Path) -> Orchestrator:
    t = tmp_path / "template"
    (t / "src").mkdir(parents=True, exist_ok=True)
    (t / "src" / "App.tsx").write_text("placeholder")
    (t / HELPER_PATH).write_text("// stub helper\n")
    (t / CONFIG_PATH).write_text(render_config([], None, None))
    (t / "package.json").write_text("{}")
    (t / "AGENTS.md").write_text("# Template rules\n")
    orch = Orchestrator(
        workspace_dir=tmp_path / "mnt" / "code",
        template=t,
        gateway=FakeGatewayClient(),
        catalog=CATALOG,
        project_id="Sage",
        resources=FakeResourceProvider(list(ALIASES)),
        browser_gateway_base="https://apps.example.com/apps/llm_gateway/v1",
    )
    orch.project(start_preview=False)
    return orch


def _write(orch: Orchestrator, rel: str, text: str) -> None:
    path = orch.project().workspace.path / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# ---- LLM Aliases ---------------------------------------------------------------------------------

def test_unbinding_an_alias_names_the_app_code_that_still_calls_it(tmp_path):
    orch = _orch(tmp_path)
    orch.bind_llm_alias("id-mimo")
    _write(orch, "src/Cluster.tsx",
           'import { askModel } from "./appLlm";\n'
           'await askModel(msgs, { alias: "mimo-v2.5" });\n')

    result = orch.unbind("llm_alias", "id-mimo")

    assert "src/Cluster.tsx" in result["refs"]
    assert result["name"] == "mimo-v2.5"
    assert result["kind"] == "llm_alias"


def test_sages_own_files_are_never_reported_as_references(tmp_path):
    """The whole reason this can't be a plain grep for the Alias name.

    `src/appLlm.config.ts` lists every bound Alias — that is its job — and this unbind is what
    rewrites it. If it counted, EVERY unbind would warn, and the one file it named would be one the
    creator must not edit.
    """
    orch = _orch(tmp_path)
    orch.bind_llm_alias("id-mimo")
    assert "mimo-v2.5" in (orch.project().workspace.path / CONFIG_PATH).read_text()

    assert orch.unbind("llm_alias", "id-mimo")["refs"] == []


def test_an_alias_the_app_never_called_leaves_nothing_behind(tmp_path):
    # Binding a model and then changing your mind is not a mess to clean up, and a warning here
    # would train the creator to dismiss the one that matters.
    orch = _orch(tmp_path)
    orch.bind_llm_alias("id-sonnet")
    _write(orch, "src/App.tsx", "export default function App() { return <div>hi</div>; }\n")

    assert orch.unbind("llm_alias", "id-sonnet")["refs"] == []


def test_unbinding_a_record_that_is_already_gone_is_not_an_error(tmp_path):
    orch = _orch(tmp_path)
    result = orch.unbind("llm_alias", "id-never-bound")
    assert result["bindings"] == []
    assert result["refs"] == []


def test_the_binding_list_still_comes_back_so_the_rail_can_re_render(tmp_path):
    # applyBindings reads `bindings` off this body; dropping it to return only refs would empty the
    # rail on every removal.
    orch = _orch(tmp_path)
    orch.bind_llm_alias("id-sonnet")
    orch.bind_llm_alias("id-mimo")

    result = orch.unbind("llm_alias", "id-sonnet")

    assert [b["name"] for b in result["bindings"]] == ["mimo-v2.5"]


# ---- Data Sources --------------------------------------------------------------------------------

def _bind_data_source(orch: Orchestrator, rid: str, name: str) -> None:
    """Write the record straight into the manifest.

    `bind_data_source` validates against the provider's listing, which is a different thing from what
    is under test here — these tests are about what the scan finds once a record exists.
    """
    binding = Binding(KIND_DATA_SOURCE, rid, name, name, "db", "public", "events", "SnowflakeConfig")
    orch.project().workspace.update_bindings(lambda _: [binding.to_dict()])


def test_a_data_source_is_found_through_its_queries_not_its_name(tmp_path):
    """A Data Source is never named in the app's code — the app calls queries by name. Scanning for
    the Resource name would find nothing and report a clean removal over a broken dashboard."""
    orch = _orch(tmp_path)
    _bind_data_source(orch, "ds-1", "BigQuery_Demo")
    _write(orch, ".sage/queries.json", json.dumps([
        {"name": "clicks_by_day", "binding": "ds-1", "sql": "SELECT 1", "params": []},
        {"name": "other_source_query", "binding": "ds-2", "sql": "SELECT 2", "params": []},
    ]))
    _write(orch, "src/Ads.tsx", 'const r = await runQuery("clicks_by_day");\n')
    _write(orch, "src/Other.tsx", 'const r = await runQuery("other_source_query");\n')

    refs = orch.unbind(KIND_DATA_SOURCE, "ds-1")["refs"]

    assert "src/Ads.tsx" in refs
    assert "src/Other.tsx" not in refs      # belongs to a Data Source that is still bound
    # The catalog holds statements that now run against a store this app no longer records, and the
    # agent owns that file — so it is named too, or the cleanup leaves the dead SQL in place.
    assert ".sage/queries.json" in refs


def test_a_data_source_with_no_queries_reports_nothing(tmp_path):
    orch = _orch(tmp_path)
    _bind_data_source(orch, "ds-1", "BigQuery_Demo")
    _write(orch, "src/App.tsx", "export default function App() { return <div>hi</div>; }\n")

    assert orch.unbind(KIND_DATA_SOURCE, "ds-1")["refs"] == []


def test_an_unparseable_query_catalog_does_not_fail_the_unbind(tmp_path):
    # The agent writes this file. A half-written catalog already has its own check; here it can only
    # cost us the warning, and refusing to remove a Resource over it would be the worse trade.
    orch = _orch(tmp_path)
    _bind_data_source(orch, "ds-1", "BigQuery_Demo")
    _write(orch, ".sage/queries.json", "{not json")

    assert orch.unbind(KIND_DATA_SOURCE, "ds-1")["refs"] == []


def test_refusing_a_membership_removal_names_the_same_files(tmp_path):
    """The refusal is the other door onto the same problem.

    Removing a Resource from the project is refused while the app still binds it — membership is not
    a back door to unbind. But "this app still needs it" on its own is a dead end: the creator can
    neither act on it nor find out what to change. It carries the refs the unbind would have given,
    so the panel can say which files to fix first.
    """
    from sage.orchestrator.service import ResourceStillBound

    orch = _orch(tmp_path)
    orch.add_project_resource({"id": "llm_alias:id-mimo", "kind": "model_llm", "name": "MiMo 2.5"})
    orch.bind_llm_alias("id-mimo")
    _write(orch, "src/Cluster.tsx",
           'import { askModel } from "./appLlm";\n'
           'await askModel(msgs, { alias: "mimo-v2.5" });\n')

    try:
        orch.remove_project_resource("llm_alias:id-mimo")
    except ResourceStillBound as e:
        assert "src/Cluster.tsx" in e.refs
    else:
        raise AssertionError("expected the removal to be refused while the app still binds it")

