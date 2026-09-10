"""A declared Dataset narrows the models however it got into scope (ADR-0043).

The gate read the Binding manifest, and the manifest is the door people use least. These cover the
other two, plus what the descriptor cache is allowed to write down once a Dataset is declared.

Four claims, and each was a real hole rather than a hypothetical:

  - ATTACHED. A file under `public/data/<slug>` reaches the model through `@mention`, which inlines
    a descriptor carrying three verbatim rows. No Binding is written by attaching, so the lock did
    not fire for the door the Data panel puts in front of everybody.
  - PINNED. Chat has no Built App and writes no manifest at all, so the Chat lock could only ever
    fire on whatever the selected app happened to bind — never on a Chat act.
  - CROSSED. A `dsfile:` chip carried no Dataset into the Build manifest either, so the handoff did
    not repair it later.
  - AT REST. `.sage/attachments.json` is committed and the descriptor cache lives in it, while
    `public/data/` is gitignored so that data never enters git.

`test_sensitivity_gate.py` covers the gate's own reading and holds the Binding cases. This file is
about the scope handed TO it, so it goes through the Orchestrator rather than the gate.
"""
from __future__ import annotations

from pathlib import Path

from sage.assets.provider import Asset, FakeAssetProvider
from sage.orchestrator import handoff
from sage.orchestrator.service import Orchestrator
from sage.resources.provider import FakeResourceProvider
from sage.router.models import ModelCatalog

# The group FakeResourceProvider's own `list_alias_groups` answers for, holding `qwen-2-5`.
GROUP = "sensitive-approved"
APPROVED = "qwen-2-5"

# Three real rows, so a test that says "the manifest holds no rows" is checking bytes somebody could
# actually read rather than the absence of a key.
CLAIMS_CSV = (
    "claim_id,member,ssn,amount\n"
    "1,Ada Lovelace,078-05-1120,412.50\n"
    "2,Alan Turing,219-09-9999,88.10\n"
    "3,Grace Hopper,001-01-0001,1204.00\n"
)


def _template(tmp: Path) -> Path:
    t = tmp / "template"
    (t / "src").mkdir(parents=True, exist_ok=True)
    (t / "src" / "App.tsx").write_text("placeholder")
    (t / "package.json").write_text("{}")
    return t


def _catalog() -> ModelCatalog:
    return ModelCatalog(sovereign_plan=APPROVED, sovereign_implement=APPROVED,
                        sovereign_ask=APPROVED, plan="gpt-5.4", implement="gpt-5.4", ask="gpt-5.4")


def _assets(root: Path) -> FakeAssetProvider:
    """Two mounted Datasets with real bytes in them: `claims` declared, `logs` not.

    Files rather than empty directories because these tests attach and then describe, and a
    descriptor of nothing would pass the manifest assertions for the wrong reason.
    """
    mount = root / "mounts"
    (mount / "claims").mkdir(parents=True)
    (mount / "logs").mkdir(parents=True)
    (mount / "claims" / "rows.csv").write_text(CLAIMS_CSV)
    (mount / "logs" / "rows.csv").write_text(CLAIMS_CSV)
    provider = FakeAssetProvider(root=mount)
    provider.assets = [
        Asset("ds_claims", "claims", tags=["sensitive"], project="Revenue",
              mount_path=str(mount / "claims")),
        Asset("ds_logs", "logs", tags=["curated"], project="Revenue",
              mount_path=str(mount / "logs")),
    ]
    return provider


def _orch(tmp: Path) -> Orchestrator:
    """An Orchestrator with its Project already attached and no Vite behind it.

    `attach_file` calls `project()` with the preview on, and `project()` is get-or-attach — so the
    attach is settled here, once, rather than by every test starting a dev server it never reads.
    """
    orch = _build(tmp)
    orch.project(start_preview=False)
    return orch


def _build(tmp: Path) -> Orchestrator:
    return Orchestrator(
        workspace_dir=tmp / "mnt" / "code",
        template=_template(tmp),
        gateway=object(),
        catalog=_catalog(),
        project_id="Sage",
        assets=_assets(tmp),
        resources=FakeResourceProvider(),
        control_plane=None,
        domino_project_name="Revenue",
    )


def _manifest(orch: Orchestrator) -> list[dict]:
    return orch.project(start_preview=False).workspace.read_attachments()


def _descriptor_for(orch: Orchestrator, name: str) -> dict:
    entry = next(e for e in _manifest(orch) if str(e.get("path", "")).endswith(name))
    return entry.get("descriptor") or {}


# --- The attached door ---------------------------------------------------------------------------

def test_an_attached_declared_dataset_locks_the_turn_without_any_binding(tmp_path, monkeypatch):
    """The hole this whole change exists for.

    Attaching writes no Binding — `bind_dataset` is a separate act behind a separate button — so a
    creator who attached a declared Dataset and @mentioned a file got the full model list. The rows
    reached the model through the door with no lock on it.
    """
    monkeypatch.setenv("SAGE_SENSITIVE_MODEL_GROUP", GROUP)
    orch = _orch(tmp_path)
    orch.attach_file("ds_claims", "rows.csv")

    state = orch.sensitivity_state()

    assert state["locked"] is True
    assert state["datasets"] == ["claims"]
    assert state["approved"] == [APPROVED]
    assert _manifest(orch), "the attach is the only record here; without it this proves nothing"


def test_attaching_an_undeclared_dataset_leaves_every_model_open(tmp_path, monkeypatch):
    """The other half, and the one that keeps the change from being a blanket lock. `logs` carries
    a tag, just not a declaring one."""
    monkeypatch.setenv("SAGE_SENSITIVE_MODEL_GROUP", GROUP)
    orch = _orch(tmp_path)
    orch.attach_file("ds_logs", "rows.csv")

    state = orch.sensitivity_state()

    assert state["enabled"] is True
    assert state["locked"] is False
    assert state["datasets"] == []


def test_an_unreadable_dataset_listing_locks_an_attached_project_too(tmp_path, monkeypatch):
    """The fail-safe reaches every door, because it is keyed on the LISTING rather than on how the
    Dataset arrived. Sage cannot tell whether these rows are declared, and "could not check where
    the rows would go" is not a reason to send them."""
    monkeypatch.setenv("SAGE_SENSITIVE_MODEL_GROUP", GROUP)
    orch = _orch(tmp_path)
    orch.attach_file("ds_claims", "rows.csv")

    def refuse(*_a, **_k):
        raise RuntimeError("Domino is down")

    orch._assets.list_datasets = refuse

    assert orch.sensitivity_state()["locked"] is True


# --- The pinned door (Chat) ----------------------------------------------------------------------

def test_a_chat_chip_locks_the_conversation_it_was_pinned_to(tmp_path, monkeypatch):
    """Chat writes no manifest, so before this the Chat lock could not fire from a Chat act.

    Asked WITH the Conversation, because the chip belongs to the Thread rather than to the Project —
    which is also why the same read without one answers differently, below.
    """
    monkeypatch.setenv("SAGE_SENSITIVE_MODEL_GROUP", GROUP)
    orch = _orch(tmp_path)
    tid = orch.create_thread()["id"]
    orch.add_thread_context(tid, {
        "kind": "file", "name": "rows.csv", "path": "public/data/claims/rows.csv",
        "datasetId": "ds_claims", "datasetRelPath": "rows.csv", "datasetName": "claims",
        "resourceId": "dsfile:ds_claims:rows.csv", "parentId": "dataset:ds_claims",
    })

    assert orch.sensitivity_state(tid)["locked"] is True
    assert orch.sensitivity_state(tid)["datasets"] == ["claims"]


def test_a_chip_does_not_lock_a_conversation_it_was_not_pinned_to(tmp_path, monkeypatch):
    """A chip is one Conversation's, and so is the lock it causes. Leaking it across Conversations
    would grey a picker out for a chat holding none of those rows — and the person would have no
    Dataset to go and look at, because there is none on this one."""
    monkeypatch.setenv("SAGE_SENSITIVE_MODEL_GROUP", GROUP)
    orch = _orch(tmp_path)
    pinned = orch.create_thread()["id"]
    other = orch.create_thread()["id"]
    orch.add_thread_context(pinned, {
        "kind": "file", "name": "rows.csv", "path": "public/data/claims/rows.csv",
        "datasetId": "ds_claims", "datasetRelPath": "rows.csv", "datasetName": "claims",
    })

    assert orch.sensitivity_state(pinned)["locked"] is True
    assert orch.sensitivity_state(other)["locked"] is False


# --- The crossing --------------------------------------------------------------------------------

def test_a_dataset_file_chip_crosses_as_a_dataset_binding():
    """`_BINDABLE` matched on kind alone, and a Dataset arrives as `kind: "file"`. So a Chat that
    read declared rows crossed into a Build whose manifest recorded no Dataset at all — and the
    lock did not survive the crossing either."""
    binding = handoff.binding_from_context({
        "kind": "file", "name": "rows.csv",
        "datasetId": "ds_claims", "datasetRelPath": "rows.csv", "datasetName": "claims",
        "resourceId": "dsfile:ds_claims:rows.csv", "parentId": "dataset:ds_claims",
    })

    assert binding is not None
    assert (binding.kind, binding.id) == ("dataset", "ds_claims")
    # The DATASET's name, never the file's. A Binding labelled `rows.csv` names nothing anybody can
    # find in the rail.
    assert binding.name == "claims"


def test_a_whole_dataset_chip_crosses_as_a_dataset_binding():
    """The other chip shape, where the id is behind a `dataset:` prefix and there is no file."""
    binding = handoff.binding_from_context({
        "kind": "dataset", "name": "claims", "resourceId": "dataset:ds_claims",
    })

    assert binding is not None
    assert (binding.kind, binding.id, binding.name) == ("dataset", "ds_claims", "claims")


def test_a_chat_upload_still_crosses_as_nothing():
    """A file chip with no Dataset behind it is a Chat upload, and it crosses through
    `_promote_chat_file` rather than as a Binding. Reading `kind == "file"` as a Dataset would have
    written a Binding whose id is a scratch path."""
    assert handoff.binding_from_context(
        {"kind": "file", "name": "notes.md", "path": ".sage/scratch/notes.md"}
    ) is None


# --- What the committed manifest is allowed to hold ----------------------------------------------

def test_the_manifest_keeps_no_sample_rows_for_a_declared_dataset(tmp_path, monkeypatch):
    """`public/data/` is gitignored so that data never enters git. Three rows of that same data must
    not enter it through the manifest that describes it."""
    monkeypatch.setenv("SAGE_SENSITIVE_MODEL_GROUP", GROUP)
    orch = _orch(tmp_path)
    orch.attach_file("ds_claims", "rows.csv")
    orch._resolve_mentions(orch.project(start_preview=False), ["public/data/claims/rows.csv"])

    stored = _descriptor_for(orch, "rows.csv")

    assert stored["detail"] == ""
    assert stored["withheld"] == "declared"
    # The shape survives; only the content goes. A withheld descriptor that also forgot the row
    # count would have made the agent blind to the file rather than to its rows.
    assert "4 columns" in stored["summary"]
    assert "078-05-1120" not in Path(
        orch.project(start_preview=False).workspace.attachments_path).read_text()


def test_an_undeclared_dataset_keeps_its_cached_detail(tmp_path, monkeypatch):
    """ADR-0029 cached descriptors so a 200-file attach describes each file once rather than once
    per turn. That is untouched for every Dataset nobody declared, which is all of them on a
    deployment that never opted in."""
    monkeypatch.setenv("SAGE_SENSITIVE_MODEL_GROUP", GROUP)
    orch = _orch(tmp_path)
    orch.attach_file("ds_logs", "rows.csv")
    orch._resolve_mentions(orch.project(start_preview=False), ["public/data/logs/rows.csv"])

    stored = _descriptor_for(orch, "rows.csv")

    assert "078-05-1120" in stored["detail"]
    assert not stored.get("withheld")


def test_a_mention_still_carries_the_rows_to_an_approved_model(tmp_path, monkeypatch):
    """What is withheld is what gets WRITTEN DOWN, and only that. The turn carrying this detail is
    already locked to an approved model, and an approved model is allowed to read the rows — that
    is the feature rather than a gap in it."""
    monkeypatch.setenv("SAGE_SENSITIVE_MODEL_GROUP", GROUP)
    orch = _orch(tmp_path)
    orch.attach_file("ds_claims", "rows.csv")

    project = orch.project(start_preview=False)
    mentions = orch._resolve_mentions(project, ["public/data/claims/rows.csv"])

    assert mentions is not None
    assert "078-05-1120" in mentions[0]["detail"]


def test_a_dataset_tagged_after_the_attach_has_its_rows_scrubbed(tmp_path, monkeypatch):
    """A Domino tag is self-service and can be added at any time, so the ordinary case is a file
    attached BEFORE the declaration. Withholding at describe time cannot reach those; the rows are
    already in the committed manifest by then.

    Sage owns this file and rewriting it costs nothing, which is what separates this from the app
    already published against an untagged Dataset — there ADR-0043 leaves the state surfaced for a
    human, because no interception point exists. Here one does.
    """
    monkeypatch.setenv("SAGE_SENSITIVE_MODEL_GROUP", GROUP)
    orch = _orch(tmp_path)
    orch._assets.assets[0] = Asset(
        "ds_claims", "claims", tags=["curated"], project="Revenue",
        mount_path=str(tmp_path / "mounts" / "claims"),
    )
    orch.attach_file("ds_claims", "rows.csv")
    orch._resolve_mentions(orch.project(start_preview=False), ["public/data/claims/rows.csv"])
    assert "078-05-1120" in _descriptor_for(orch, "rows.csv")["detail"]

    # Somebody tags it in Domino. The gate holds an undeclared verdict for five seconds, so the
    # clock is moved rather than waited on.
    orch._assets.assets[0] = Asset(
        "ds_claims", "claims", tags=["sensitive"], project="Revenue",
        mount_path=str(tmp_path / "mounts" / "claims"),
    )
    orch._gate = None

    assert orch.sensitivity_state()["locked"] is True
    assert _descriptor_for(orch, "rows.csv")["detail"] == ""
    assert "078-05-1120" not in Path(
        orch.project(start_preview=False).workspace.attachments_path).read_text()
