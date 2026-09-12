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
    `public/data/` is gitignored so that data never enters git. This one has since stopped being
    about the declaration at all: #237 withholds `detail` from the cache for EVERY file, because
    the tag answers nothing on a deployment that never opted in, and the reader the rows are owed
    protection from is a Project collaborator with a clone rather than a model.

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


def test_a_whole_dataset_chip_locks_the_conversation_too(tmp_path, monkeypatch):
    """The same door at the coarser grain, and it was open.

    A Dataset reaches a Conversation as a chip in TWO shapes. A file Chat fetched is `kind: "file"`
    carrying `datasetId`; the Dataset row mentioned WHOLE carries no such field at all, because the
    resource id is the Dataset. This reader took `datasetId` alone and saw only the first, so
    `@`-mentioning a declared Dataset itself in Chat armed nothing and the turn ran on whatever
    vendor model was picked.

    What makes it a hole rather than a gap is that the same chip DID cross into Build as a Dataset
    Binding the whole time — `_dataset_binding` has read both shapes since it was written, and its
    own docstring says the lock has to survive the crossing. Two readers of one chip, one of them a
    leak. Now one reader, so they cannot disagree again.
    """
    monkeypatch.setenv("SAGE_SENSITIVE_MODEL_GROUP", GROUP)
    orch = _orch(tmp_path)
    tid = orch.create_thread()["id"]
    # Exactly what `addToConversation` posts for the catalogue row: no `datasetId` anywhere.
    orch.add_thread_context(tid, {
        "kind": "dataset",
        "name": "claims",
        "resourceId": "dataset:ds_claims",
    })
    assert orch.sensitivity_state(tid)["locked"] is True
    assert orch.sensitivity_state(tid)["datasets"] == ["claims"]


def test_a_whole_dataset_chip_for_an_undeclared_dataset_locks_nothing(tmp_path, monkeypatch):
    """The other half of the same read. Matching the shape must not be matching every shape: a
    reader that locked on any `dataset:` chip would narrow the picker for Datasets nobody declared,
    which is the badge-disagrees-with-the-lock failure at the other end.

    `ds_logs`, which the fixture defines and tags `curated`, rather than an id nothing holds. An
    unknown id takes the not-found path and would stay green with the declared-ness check deleted
    for every real Dataset — an assertion about the wrong half of the reader.
    """
    monkeypatch.setenv("SAGE_SENSITIVE_MODEL_GROUP", GROUP)
    orch = _orch(tmp_path)
    tid = orch.create_thread()["id"]
    orch.add_thread_context(tid, {
        "kind": "dataset",
        "name": "logs",
        "resourceId": "dataset:ds_logs",
    })
    assert orch.sensitivity_state(tid)["locked"] is False


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
    assert stored["withheld"]
    # The shape survives; only the content goes. A withheld descriptor that also forgot the row
    # count would have made the agent blind to the file rather than to its rows.
    assert "4 columns" in stored["summary"]
    assert "078-05-1120" not in Path(
        orch.project(start_preview=False).workspace.attachments_path).read_text()


def test_an_undeclared_dataset_keeps_no_rows_either(tmp_path):
    """The regression #237 names. No tag, no `SAGE_SENSITIVE_MODEL_GROUP`, nothing declared — which
    is every deployment — and the committed manifest still holds no rows.

    ADR-0029's cache is what makes a 200-file attach describe each file once rather than once per
    turn, and it keeps doing that: `kind`, `summary` and `size` are still written. Only `detail` is
    left out, and only one caller ever reads it."""
    orch = _orch(tmp_path)
    orch.attach_file("ds_logs", "rows.csv")
    orch._resolve_mentions(orch.project(start_preview=False), ["public/data/logs/rows.csv"])

    stored = _descriptor_for(orch, "rows.csv")

    assert stored["detail"] == ""
    assert stored["withheld"]
    assert "4 columns" in stored["summary"]
    assert "078-05-1120" not in Path(
        orch.project(start_preview=False).workspace.attachments_path).read_text()


def test_a_mention_carries_the_shape_and_not_the_rows(tmp_path):
    """#237 made this about what gets WRITTEN DOWN, and left the prompt as it was: an @mention
    re-read the file, so three verbatim rows went into the user's own message every time.

    #250 is where that stopped. Measured 2026-09-11: "sample 3 rows from @<a raw table>" put three
    customers' emails, card numbers and SSNs into the message, OpenCode replayed the message list on
    every later request, and a build turn in a different Conversation was refused on them
    thirty-five minutes later. The file it named had been removed. Nothing a person could do reached
    the rows, because they were never in a file — they were in the sentence they had typed.

    The assistant is not made worse at reading the file it was pointed at: `shape` names every
    column, its type and its vocabulary, and the file is still on disk at the path in the same
    block. What it can no longer do is quote somebody's row without being asked to.
    """
    orch = _orch(tmp_path)
    orch.attach_file("ds_logs", "rows.csv")

    mentions = orch._resolve_mentions(
        orch.project(start_preview=False), ["public/data/logs/rows.csv"])

    assert mentions is not None
    assert "078-05-1120" not in mentions[0]["detail"]
    assert "columns" in mentions[0]["detail"], "the shape is still there to work from"


def test_an_approved_model_does_not_get_the_rows_either(tmp_path, monkeypatch):
    """This used to assert the opposite, and the reasoning was sound at the time: ADR-0043 locks a
    turn carrying a declared Dataset to an approved model, and an approved model may read the rows.

    What changed is not that permission. It is that the @mention descriptor stopped being the way
    rows are read at all. Live read (ADR-0041) shows rows on the card without putting them in the
    message, and it covers Datasets as well as bound tables — so the capability has a door, and it
    is one where the rows do not outlive the turn that asked for them.

    Two rules would have been worse than one. "Approved models get rows through a second door" is
    not a sentence anyone can hold in their head while diagnosing a refusal, and the refusal is
    where this always gets found.
    """
    monkeypatch.setenv("SAGE_SENSITIVE_MODEL_GROUP", GROUP)
    orch = _orch(tmp_path)
    orch.attach_file("ds_claims", "rows.csv")

    project = orch.project(start_preview=False)
    mentions = orch._resolve_mentions(project, ["public/data/claims/rows.csv"])

    assert mentions is not None
    assert "078-05-1120" not in mentions[0]["detail"]


def test_a_manifest_written_before_the_fix_is_scrubbed_on_open(tmp_path):
    """Descriptors have been cached in a committed file for as long as they have existed, so an
    established Project already holds three verbatim rows per CSV. Opening it is where those get
    rewritten — the same place the other three migrations run.

    It rewrites the file and nothing more. The rows already in the history behind it stay there,
    which is why withholding them is the fix and this is only the cleanup after it.
    """
    orch = _orch(tmp_path)
    orch.attach_file("ds_claims", "rows.csv")

    # Put the rows back, the way every manifest written before #237 carries them.
    project = orch.project(start_preview=False)
    entries = project.workspace.read_attachments()
    for e in entries:
        if str(e.get("path", "")).endswith("rows.csv"):
            e["descriptor"] = {**e["descriptor"], "detail": CLAIMS_CSV, "withheld": ""}
    project.workspace.write_attachments(entries)
    assert "078-05-1120" in Path(project.workspace.attachments_path).read_text()

    orch._project = None   # the next call re-opens the Project, which is where migrations run
    orch.project(start_preview=False)

    assert _descriptor_for(orch, "rows.csv")["detail"] == ""
    assert "078-05-1120" not in Path(project.workspace.attachments_path).read_text()
