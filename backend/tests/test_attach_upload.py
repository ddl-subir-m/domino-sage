"""Upload / delete / manifest behavior for the builder Context panel.

Uses the FakeAssetProvider's seeded datasets (writable temp mounts): sales_2026 (non-sensitive,
the resolved default), customer_pii (tagged `sensitive`), app_logs (untagged). Non-sensitive
uploads land under uploads/; sensitive uploads to the default dataset land under sensitive/."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from sage.assets.provider import FakeAssetProvider
from sage.orchestrator.service import DataReferenced, Orchestrator, UploadUnavailable
from sage.provision.domino import FakeControlPlane
from sage.router.models import ModelCatalog


def _template(tmp: Path) -> Path:
    t = tmp / "template"
    (t / "src").mkdir(parents=True, exist_ok=True)
    (t / "src" / "App.tsx").write_text("placeholder")
    (t / "package.json").write_text("{}")
    return t


def _catalog() -> ModelCatalog:
    return ModelCatalog(sovereign_plan="s", sovereign_implement="s", sovereign_ask="s",
                        plan="p", implement="i", ask="a")


def _orch(tmp: Path, assets=None, control_plane=None) -> Orchestrator:
    return Orchestrator(workspace_dir=tmp / "mnt" / "code", template=_template(tmp),
                        gateway=object(), catalog=_catalog(), project_id="Sage", assets=assets,
                        control_plane=control_plane)


def _dataset(orch: Orchestrator, name: str) -> str:
    return next(a["id"] for a in orch.list_assets() if a["name"] == name)


def _manifest(ws: Path) -> list[dict]:
    return json.loads((ws / ".sage" / "attachments.json").read_text())


def test_upload_writes_to_default_dataset_mount_and_attaches(tmp_path: Path):
    orch = _orch(tmp_path)
    ws = orch.project(start_preview=False).workspace.path

    res = orch.upload_file("my data.csv", b"a,b\n1,2\n", sensitive=False)

    assert res["sensitive"] is False
    link = ws / res["path"]
    assert link.is_symlink() and link.read_bytes() == b"a,b\n1,2\n"
    assert "/uploads/" in str(link.resolve())          # bytes live on the dataset mount, not copied
    entry = _manifest(ws)[0]
    assert entry["source"] == "upload" and entry["dataset_rel_path"] == "uploads/my_data.csv"
    assert not orch.project().control.locked            # non-sensitive -> no lock


def test_agents_block_gives_exact_served_path_and_guardrails(tmp_path: Path):
    # The agent must be told the EXACT nested served URL (not a flat /data/<name> it would guess,
    # which 404s to the SPA fallback and reads as null data) and be steered off the git-leaking
    # workaround of copying data into src/.
    orch = _orch(tmp_path)
    ws = orch.project(start_preview=False).workspace.path
    orch.upload_file("my data.csv", b"a,b\n1,2\n", sensitive=False)

    agents = (ws / "AGENTS.md").read_text()
    assert "fetch `data/sales_2026/uploads/my_data.csv`" in agents   # nested, base-relative
    # Base-aware fetch by string concatenation, NOT new URL(path, BASE_URL) — BASE_URL is a path,
    # so new URL() throws "Invalid base URL" and crashes the built app on load.
    assert 'import.meta.env.BASE_URL + "data/' in agents
    assert "Invalid base URL" in agents                              # warns off the crashing pattern
    assert "src/" in agents and "gitignored" in agents               # don't-copy-into-git guardrail


def test_sensitive_upload_to_default_dataset_uses_sensitive_subfolder_and_locks(tmp_path: Path):
    orch = _orch(tmp_path)
    ws = orch.project(start_preview=False).workspace.path

    res = orch.upload_file("secret.csv", b"ssn\n1\n", sensitive=True)

    assert res["sensitive"] is True
    assert res["dataset"] == "sales_2026"               # the resolved shared default dataset
    entry = _manifest(ws)[0]
    assert entry["dataset_rel_path"] == "sensitive/secret.csv"   # sensitive subfolder, not uploads/
    assert "/sensitive/" in str((ws / res["path"]).resolve())    # bytes on the mount, under sensitive/
    assert orch.project().control.locked
    # The default dataset itself is NOT tagged sensitive — its non-sensitive data stays unmarked.
    assert next(a["sensitive"] for a in orch.list_assets() if a["name"] == "sales_2026") is False


def test_manifest_rehydrates_attachments_and_restores_lock(tmp_path: Path):
    orch = _orch(tmp_path)
    orch.project(start_preview=False)
    orch.upload_file("secret.csv", b"x", sensitive=True)

    # A fresh orchestrator over the same volume rebuilds from the committed manifest.
    proj = _orch(tmp_path).project(start_preview=False)
    assert [e["file"] for e in proj.attached] == ["sensitive/secret.csv"]
    assert proj.control.locked                          # sticky sovereign lock restored from manifest


def test_delete_removes_uploaded_symlink_and_dataset_bytes(tmp_path: Path):
    orch = _orch(tmp_path)
    ws = orch.project(start_preview=False).workspace.path
    res = orch.upload_file("d.csv", b"x", sensitive=False)
    src = (ws / res["path"]).resolve()
    assert src.is_file()

    orch.delete_file(res["path"])

    assert not (ws / res["path"]).exists() and not src.exists()  # symlink AND dataset bytes gone
    assert _manifest(ws) == []


def test_delete_never_removes_a_pre_existing_dataset_files_bytes(tmp_path: Path):
    orch = _orch(tmp_path)
    orch.project(start_preview=False)
    ds = _dataset(orch, "sales_2026")
    res = orch.attach_file(ds, "train.csv")
    asset = next(a for a in orch._assets.list_datasets("Sage") if a.id == ds)
    src = Path(asset.mount_path) / "train.csv"
    assert src.is_file()

    orch.delete_file(res["path"])            # delete on a dataset-sourced file is detach-only

    assert src.is_file()                     # the user's original data is preserved


def test_delete_removes_bytes_for_an_uploads_file_reattached_as_dataset(tmp_path: Path):
    # A Sage upload that later shows up as a dataset-browser attachment (source flips to
    # 'dataset', e.g. rehydrated that way) still lives under a Sage folder (sensitive/ here), so
    # it's Sage-managed and delete must remove its bytes — sensitivity is irrelevant to this.
    orch = _orch(tmp_path)
    ws = orch.project(start_preview=False).workspace.path
    res = orch.upload_file("d.csv", b"x", sensitive=True)   # sensitive -> default dataset sensitive/
    src = (ws / res["path"]).resolve()
    assert src.is_file()
    for e in orch.project().attached:                        # simulate the re-attach reclassification
        e["source"] = "dataset"

    orch.delete_file(res["path"])

    assert not (ws / res["path"]).exists() and not src.exists()  # symlink AND dataset bytes gone
    assert _manifest(ws) == []


def test_delete_blocked_while_app_fetches_the_file(tmp_path: Path):
    # Deleting data the dashboard fetches at runtime would orphan that code — block it (Detach stays).
    orch = _orch(tmp_path)
    ws = orch.project(start_preview=False).workspace.path
    res = orch.upload_file("d.csv", b"a,b\n1,2\n", sensitive=False)
    (ws / "src" / "App.tsx").write_text('fetch(import.meta.env.BASE_URL + "data/sales_2026/uploads/d.csv")')

    with pytest.raises(DataReferenced) as ei:
        orch.delete_file(res["path"])

    assert ei.value.refs and not ei.value.copies
    assert (ws / res["path"]).exists()               # nothing removed — the block ran first


def test_delete_blocked_when_data_was_copied_into_src(tmp_path: Path):
    # A copied file (same basename under the app tree) is the git-leak — and why delete "does nothing".
    orch = _orch(tmp_path)
    ws = orch.project(start_preview=False).workspace.path
    res = orch.upload_file("d.csv", b"a,b\n1,2\n", sensitive=False)
    (ws / "src" / "data").mkdir(parents=True, exist_ok=True)
    (ws / "src" / "data" / "d.csv").write_text("a,b\n1,2\n")     # agent copied it into src/

    with pytest.raises(DataReferenced) as ei:
        orch.delete_file(res["path"])

    assert ei.value.copies == ["src/data/d.csv"]


def test_delete_allowed_when_app_does_not_use_the_file(tmp_path: Path):
    # The template App.tsx is a placeholder that never references the upload -> delete proceeds.
    orch = _orch(tmp_path)
    ws = orch.project(start_preview=False).workspace.path
    res = orch.upload_file("d.csv", b"x", sensitive=False)

    orch.delete_file(res["path"])                    # no DataReferenced

    assert not (ws / res["path"]).exists() and _manifest(ws) == []


def test_data_usage_flags_inlined_bytes_as_a_copy(tmp_path: Path):
    orch = _orch(tmp_path)
    proj = orch.project(start_preview=False)
    body = b"name,score\n" + b"".join(b"row%d,%d\n" % (i, i) for i in range(20))
    res = orch.upload_file("d.csv", body, sensitive=False)
    (proj.workspace.path / "src" / "rows.ts").write_text("export const RAW = `" + body.decode() + "`;")

    entry = next(e for e in proj.attached if e["path"] == res["path"])
    usage = orch._data_usage(proj, entry)
    assert "src/rows.ts" in usage["copies"]


def test_detect_leaks_finds_copied_data_for_the_commit_backstop(tmp_path: Path):
    orch = _orch(tmp_path)
    proj = orch.project(start_preview=False)
    orch.upload_file("sales.csv", b"a,b\n1,2\n", sensitive=False)
    (proj.workspace.path / "src" / "sales.csv").write_text("a,b\n1,2\n")   # agent copied it into src/

    assert orch._detect_leaks(proj) == [("sales.csv", ["src/sales.csv"])]
    assert orch._leaked_copy_paths(proj) == ["src/sales.csv"]


def test_no_leak_when_app_only_fetches_from_data(tmp_path: Path):
    orch = _orch(tmp_path)
    proj = orch.project(start_preview=False)
    orch.upload_file("sales.csv", b"a,b\n1,2\n", sensitive=False)
    (proj.workspace.path / "src" / "App.tsx").write_text('fetch("data/sales_2026/uploads/sales.csv")')

    assert orch._detect_leaks(proj) == []          # a fetch is the intended pattern, not a leak
    assert orch._leaked_copy_paths(proj) == []


def test_detach_removes_a_leaked_copy_so_it_cant_reach_git(tmp_path: Path):
    # The core hole: while attached, a copy in src/ is kept out of commits by _detect_leaks. Detaching
    # forgets the entry, so the commit backstop stops covering it — detach must delete the copy itself.
    orch = _orch(tmp_path)
    proj = orch.project(start_preview=False)
    res = orch.upload_file("d.csv", b"a,b\n1,2\n", sensitive=True)
    (proj.workspace.path / "src" / "data").mkdir(parents=True, exist_ok=True)
    (proj.workspace.path / "src" / "data" / "d.csv").write_text("a,b\n1,2\n")   # agent copied it into src/

    out = orch.detach_file(res["path"])

    assert out["removed_copies"] == ["src/data/d.csv"]
    assert not (proj.workspace.path / "src" / "data" / "d.csv").exists()   # leaked bytes gone from the tree
    assert orch._leaked_copy_paths(proj) == []                            # nothing left for the backstop
    assert _manifest(proj.workspace.path) == []


def test_detach_reports_still_referenced_files_without_deleting_source(tmp_path: Path):
    # A fetch (or bytes inlined into a code file) is app logic, not a raw-file copy: detach must NOT
    # delete the source, but must report it so the UI can warn and offer the agent cleanup.
    orch = _orch(tmp_path)
    proj = orch.project(start_preview=False)
    res = orch.upload_file("d.csv", b"x", sensitive=False)
    (proj.workspace.path / "src" / "App.tsx").write_text('fetch("data/sales_2026/uploads/d.csv")')

    out = orch.detach_file(res["path"])

    assert out["removed_copies"] == []
    assert "src/App.tsx" in out["refs"]
    assert (proj.workspace.path / "src" / "App.tsx").exists()   # app code left untouched


def test_detach_reports_a_hardcoded_sample_of_the_file_not_just_a_full_copy(tmp_path: Path):
    # The agent hardcoded the prompt PREVIEW (leading rows) into the app instead of fetching the
    # file. That's a partial copy: the app renders a stale sample, so detach must still report it.
    orch = _orch(tmp_path)
    proj = orch.project(start_preview=False)
    rows = "event_id,patient,outcome\n" + "\n".join(
        f"EV{i:04d},patient_{i:04d},outcome_value_{i}" for i in range(200))  # 200-row dataset
    res = orch.upload_file("big.csv", rows.encode(), sensitive=False)
    sample = "\n".join(rows.splitlines()[:6])                              # header + first 5 rows only
    (proj.workspace.path / "src" / "App.tsx").write_text(f"const data = `{sample}`;")

    out = orch.detach_file(res["path"])

    assert out["removed_copies"] == []                    # inlined into code -> source left in place
    assert "src/App.tsx" in out["refs"]                   # but reported so the UI warns
    assert (proj.workspace.path / "src" / "App.tsx").exists()


def test_read_file_previews_an_attached_symlink_but_still_blocks_escapes(tmp_path: Path, monkeypatch):
    # The attachment is a symlink under public/data/ pointing at the dataset mount (outside the
    # workspace); the file-open endpoint must preview it read-only, while a real escape still 400s.
    from fastapi.testclient import TestClient

    import sage.orchestrator.app as appmod

    orch = _orch(tmp_path)
    orch.project(start_preview=False)
    res = orch.upload_file("d.csv", b"a,b\n1,2\n", sensitive=False)
    monkeypatch.setattr(appmod, "orchestrator", orch)
    client = TestClient(appmod.control_app)

    ok = client.get("/api/project/file", params={"path": res["path"]})
    assert ok.status_code == 200 and ok.json()["content"] == "a,b\n1,2\n"

    escape = client.get("/api/project/file", params={"path": "../../../../../../etc/passwd"})
    assert escape.status_code == 400            # not a known attachment -> resolver rejects the escape


def test_resolve_mentions_only_honors_known_attachments(tmp_path: Path):
    orch = _orch(tmp_path)
    proj = orch.project(start_preview=False)
    res = orch.upload_file("d.csv", b"x", sensitive=False)

    assert [m["path"] for m in orch._resolve_mentions(proj, [res["path"]])] == [res["path"]]
    assert orch._resolve_mentions(proj, ["public/data/not-attached.csv"]) is None  # unknown -> ignored
    assert orch._resolve_mentions(proj, None) is None


def test_non_sensitive_upload_to_default_dataset_does_not_lock(tmp_path: Path):
    # Guards the on_assets_changed([sensitive]) fix: a non-sensitive upload must NOT lock.
    orch = _orch(tmp_path)
    ws = orch.project(start_preview=False).workspace.path

    res = orch.upload_file("plain.csv", b"a,b\n1,2\n", sensitive=False)

    assert res["sensitive"] is False
    assert _manifest(ws)[0]["dataset_rel_path"] == "uploads/plain.csv"
    assert not orch.project().control.locked


def test_sensitive_upload_without_a_sensitive_dataset_uses_default_subfolder(tmp_path: Path):
    # No `sensitive`-tagged dataset needs to exist any more: sensitive uploads fall back to the
    # shared default dataset's sensitive/ subfolder (manifest-driven lock), not an error.
    prov = FakeAssetProvider()
    prov.assets = [a for a in prov.assets if "sensitive" not in {t.lower() for t in a.tags}]
    orch = _orch(tmp_path, assets=prov)
    ws = orch.project(start_preview=False).workspace.path

    res = orch.upload_file("x.csv", b"x", sensitive=True)

    assert res["sensitive"] is True
    assert _manifest(ws)[0]["dataset_rel_path"] == "sensitive/x.csv"
    assert orch.project().control.locked


def test_upload_is_unavailable_when_no_writable_dataset_exists(tmp_path: Path):
    # UploadUnavailable now fires only when there's genuinely nowhere writable to store bytes.
    prov = FakeAssetProvider()
    prov.assets = []
    orch = _orch(tmp_path, assets=prov)
    orch.project(start_preview=False)

    with pytest.raises(UploadUnavailable):
        orch.upload_file("x.csv", b"x", sensitive=True)


def test_upload_to_already_sensitive_dataset_forces_sensitive(tmp_path: Path):
    # Picking a dataset that's already tagged sensitive marks the file sensitive even if the box is
    # unchecked — you can't drop a non-sensitive file into a sensitive dataset.
    orch = _orch(tmp_path)
    ws = orch.project(start_preview=False).workspace.path
    pii = _dataset(orch, "customer_pii")

    res = orch.upload_file("note.csv", b"x", sensitive=False, dataset_id=pii)

    assert res["sensitive"] is True and res["dataset"] == "customer_pii"
    assert _manifest(ws)[0]["dataset_rel_path"] == "uploads/note.csv"   # picked dataset -> uploads/
    assert orch.project().control.locked


def test_sensitive_upload_to_untagged_picked_dataset_tags_it(tmp_path: Path):
    # Marking a file sensitive while targeting an untagged dataset tags the WHOLE dataset.
    cp = FakeControlPlane()
    orch = _orch(tmp_path, control_plane=cp)
    orch.project(start_preview=False)
    logs = _dataset(orch, "app_logs")   # untagged fake dataset

    res = orch.upload_file("audit.csv", b"x", sensitive=True, dataset_id=logs)

    assert res["sensitive"] is True
    assert logs in cp.tagged_sensitive                 # the dataset was tagged via the control plane
    assert orch.project().control.locked


def test_sensitive_upload_to_untagged_dataset_without_control_plane_is_best_effort(tmp_path: Path):
    # No control plane (local/off-Domino): the tag can't be written, but the upload still succeeds
    # and the per-file sovereign lock still fires from the manifest.
    orch = _orch(tmp_path)   # control_plane=None
    ws = orch.project(start_preview=False).workspace.path
    logs = _dataset(orch, "app_logs")

    res = orch.upload_file("audit.csv", b"x", sensitive=True, dataset_id=logs)

    assert res["sensitive"] is True
    assert _manifest(ws)[0]["dataset_rel_path"] == "uploads/audit.csv"
    assert orch.project().control.locked


# --- typed descriptors: the agent gets each file's SHAPE, never its bytes ------------------------

def test_mentions_hand_the_agent_the_workspace_path_not_the_mount_path(tmp_path: Path):
    """The regression that matters: OpenCode's read tool hangs forever on absolute /mnt/data paths
    (outside its project root), while the in-root public/data/ symlink reads fine."""
    orch = _orch(tmp_path, assets=FakeAssetProvider())
    project = orch.project(start_preview=False)
    orch.upload_file("q3.csv", b"region,revenue\nwest,10\neast,20\n")
    path = project.attached[0]["path"]

    out = orch._resolve_mentions(project, [path])

    assert len(out) == 1
    assert out[0]["path"] == path == "public/data/sales_2026/uploads/q3.csv"
    assert out[0]["name"] == "q3.csv"
    assert not any("/mnt/" in str(v) for v in out[0].values())


def test_descriptor_is_cached_in_the_manifest_so_the_mount_is_read_once(tmp_path: Path):
    orch = _orch(tmp_path, assets=FakeAssetProvider())
    ws = orch.project(start_preview=False).workspace.path
    orch.upload_file("q3.csv", b"region,revenue\nwest,10\neast,20\n")

    d = _manifest(ws)[0]["descriptor"]
    assert d["kind"] == "tabular"
    assert "region" in d["detail"] and "revenue" in d["detail"]

    # Second use must not re-read the mount — the cached descriptor is returned verbatim.
    project = orch.project()
    project.attached[0]["descriptor"]["summary"] = "sentinel"
    assert orch._resolve_mentions(project, [project.attached[0]["path"]])[0]["summary"] == "sentinel"


def test_agents_md_lists_each_attachment_with_a_one_line_shape(tmp_path: Path):
    """The AGENTS.md block is re-read every turn, so it carries the one-line summary only — the full
    descriptor is inlined by send_prompt for @mentioned files alone."""
    orch = _orch(tmp_path, assets=FakeAssetProvider())
    ws = orch.project(start_preview=False).workspace.path
    orch.upload_file("q3.csv", b"region,revenue\nwest,10\neast,20\n")

    block = (ws / "AGENTS.md").read_text()
    line = next(ln for ln in block.splitlines() if "q3.csv" in ln and ln.startswith("- disk"))
    assert "CSV" in line
    assert "fetch `data/sales_2026/uploads/q3.csv`" in line


def test_a_binary_attachment_never_puts_decoded_bytes_in_front_of_the_agent(tmp_path: Path):
    """A PDF used to be utf-8-decoded into the prompt as a 'SCHEMA SAMPLE' of mojibake."""
    orch = _orch(tmp_path, assets=FakeAssetProvider())
    project = orch.project(start_preview=False)
    orch.upload_file("report.pdf", b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<<>>\nendobj\n")

    out = orch._resolve_mentions(project, [project.attached[0]["path"]])[0]

    assert "�" not in out["detail"] and "�" not in out["summary"]
    assert project.attached[0]["descriptor"]["kind"] not in ("tabular", "text")


def _png_bytes(pad: int = 0) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + (800).to_bytes(4, "big") \
        + (600).to_bytes(4, "big") + b"\x08\x06\x00\x00\x00" + bytes(pad)


def test_an_attached_image_is_inlined_as_a_data_uri_for_the_agent(tmp_path: Path):
    """Images are the one type where the pixels ARE the shape, so the descriptor isn't enough.
    A data: URI is required — OpenCode emits malformed media for every file-path form."""
    orch = _orch(tmp_path, assets=FakeAssetProvider())
    project = orch.project(start_preview=False)
    orch.upload_file("shot.png", _png_bytes())

    out = orch._resolve_mentions(project, [project.attached[0]["path"]])[0]

    assert out["image_uri"].startswith("data:image/png;base64,")
    assert out["summary"] == "PNG image — 800x600"     # descriptor still travels alongside


def test_an_oversized_image_degrades_to_its_descriptor_instead_of_bloating_the_prompt(tmp_path: Path):
    from sage.orchestrator import service as svc

    orch = _orch(tmp_path, assets=FakeAssetProvider())
    project = orch.project(start_preview=False)
    orch.upload_file("huge.png", _png_bytes(pad=svc._MAX_INLINE_IMAGE_BYTES + 1))

    out = orch._resolve_mentions(project, [project.attached[0]["path"]])[0]

    assert out["image_uri"] is None
    assert "PNG image" in out["summary"]


def test_a_tabular_attachment_carries_no_image_uri(tmp_path: Path):
    orch = _orch(tmp_path, assets=FakeAssetProvider())
    project = orch.project(start_preview=False)
    orch.upload_file("q3.csv", b"a,b\n1,2\n")

    assert "image_uri" not in orch._resolve_mentions(project, [project.attached[0]["path"]])[0]
