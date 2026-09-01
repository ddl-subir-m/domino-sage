"""Upload / delete / manifest behavior for the builder Context panel.

Uses the FakeAssetProvider's seeded datasets (writable temp mounts): sales_2026 (the resolved
default), customer_pii, app_logs. Uploads land under uploads/."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from sage.assets.provider import Asset, DatasetFile, FakeAssetProvider
from sage.orchestrator.service import (
    AttachTooLarge,
    DataReferenced,
    Orchestrator,
    UploadUnavailable,
)
from sage.resources.bindings import KIND_DATA_SOURCE, Binding
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


def _orch(tmp: Path, assets=None) -> Orchestrator:
    return Orchestrator(workspace_dir=tmp / "mnt" / "code", template=_template(tmp),
                        gateway=object(), catalog=_catalog(), project_id="Sage", assets=assets)


def _dataset(orch: Orchestrator, name: str) -> str:
    return next(a["id"] for a in orch.list_assets() if a["name"] == name)


def _manifest(ws: Path) -> list[dict]:
    return json.loads((ws / ".sage" / "attachments.json").read_text())


def test_upload_writes_to_default_dataset_mount_and_attaches(tmp_path: Path):
    orch = _orch(tmp_path)
    ws = orch.project(start_preview=False).workspace.path

    res = orch.upload_file("my data.csv", b"a,b\n1,2\n")

    link = ws / res["path"]
    assert link.is_symlink() and link.read_bytes() == b"a,b\n1,2\n"
    assert "/uploads/" in str(link.resolve())          # bytes live on the dataset mount, not copied
    entry = _manifest(ws)[0]
    assert entry["source"] == "upload" and entry["dataset_rel_path"] == "uploads/my_data.csv"


def test_agents_block_gives_exact_served_path_and_guardrails(tmp_path: Path):
    # The agent must be told the EXACT nested served URL (not a flat /data/<name> it would guess,
    # which 404s to the SPA fallback and reads as null data) and be steered off the git-leaking
    # workaround of copying data into src/.
    orch = _orch(tmp_path)
    ws = orch.project(start_preview=False).workspace.path
    orch.upload_file("my data.csv", b"a,b\n1,2\n")

    agents = (ws / "AGENTS.md").read_text()
    assert "fetch `data/sales_2026/uploads/my_data.csv`" in agents   # nested, base-relative
    # Base-aware fetch by string concatenation, NOT new URL(path, BASE_URL) — BASE_URL is a path,
    # so new URL() throws "Invalid base URL" and crashes the built app on load.
    assert 'import.meta.env.BASE_URL + "data/' in agents
    assert "Invalid base URL" in agents                              # warns off the crashing pattern
    assert "src/" in agents and "gitignored" in agents               # don't-copy-into-git guardrail


def test_manifest_rehydrates_attachments(tmp_path: Path):
    orch = _orch(tmp_path)
    orch.project(start_preview=False)
    orch.upload_file("secret.csv", b"x")

    # A fresh orchestrator over the same volume rebuilds from the committed manifest.
    proj = _orch(tmp_path).project(start_preview=False)
    assert [e["file"] for e in proj.attached] == ["uploads/secret.csv"]


def test_delete_removes_uploaded_symlink_and_dataset_bytes(tmp_path: Path):
    orch = _orch(tmp_path)
    ws = orch.project(start_preview=False).workspace.path
    res = orch.upload_file("d.csv", b"x")
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
    # 'dataset', e.g. rehydrated that way) still lives under a Sage folder, so it's Sage-managed
    # and delete must remove its bytes.
    orch = _orch(tmp_path)
    ws = orch.project(start_preview=False).workspace.path
    res = orch.upload_file("d.csv", b"x")
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
    res = orch.upload_file("d.csv", b"a,b\n1,2\n")
    (ws / "src" / "App.tsx").write_text('fetch(import.meta.env.BASE_URL + "data/sales_2026/uploads/d.csv")')

    with pytest.raises(DataReferenced) as ei:
        orch.delete_file(res["path"])

    assert ei.value.refs and not ei.value.copies
    assert (ws / res["path"]).exists()               # nothing removed — the block ran first


def test_delete_blocked_when_data_was_copied_into_src(tmp_path: Path):
    # A copied file (same basename under the app tree) is the git-leak — and why delete "does nothing".
    orch = _orch(tmp_path)
    ws = orch.project(start_preview=False).workspace.path
    res = orch.upload_file("d.csv", b"a,b\n1,2\n")
    (ws / "src" / "data").mkdir(parents=True, exist_ok=True)
    (ws / "src" / "data" / "d.csv").write_text("a,b\n1,2\n")     # agent copied it into src/

    with pytest.raises(DataReferenced) as ei:
        orch.delete_file(res["path"])

    assert ei.value.copies == ["src/data/d.csv"]


def test_delete_allowed_when_app_does_not_use_the_file(tmp_path: Path):
    # The template App.tsx is a placeholder that never references the upload -> delete proceeds.
    orch = _orch(tmp_path)
    ws = orch.project(start_preview=False).workspace.path
    res = orch.upload_file("d.csv", b"x")

    orch.delete_file(res["path"])                    # no DataReferenced

    assert not (ws / res["path"]).exists() and _manifest(ws) == []


def test_data_usage_flags_inlined_bytes_as_a_copy(tmp_path: Path):
    orch = _orch(tmp_path)
    proj = orch.project(start_preview=False)
    body = b"name,score\n" + b"".join(b"row%d,%d\n" % (i, i) for i in range(20))
    res = orch.upload_file("d.csv", body)
    (proj.workspace.path / "src" / "rows.ts").write_text("export const RAW = `" + body.decode() + "`;")

    entry = next(e for e in proj.attached if e["path"] == res["path"])
    usage = orch._data_usage(proj, entry)
    assert "src/rows.ts" in usage["copies"]


def test_detect_leaks_finds_copied_data_for_the_commit_backstop(tmp_path: Path):
    orch = _orch(tmp_path)
    proj = orch.project(start_preview=False)
    orch.upload_file("sales.csv", b"a,b\n1,2\n")
    (proj.workspace.path / "src" / "sales.csv").write_text("a,b\n1,2\n")   # agent copied it into src/

    assert orch._detect_leaks(proj) == [("sales.csv", ["src/sales.csv"])]
    # The exclude is handed to git, which runs at the Project root, so it names the app's directory.
    assert orch._leaked_copy_paths(proj) == [f"apps/{proj.workspace.app_id}/src/sales.csv"]


def test_no_leak_when_app_only_fetches_from_data(tmp_path: Path):
    orch = _orch(tmp_path)
    proj = orch.project(start_preview=False)
    orch.upload_file("sales.csv", b"a,b\n1,2\n")
    (proj.workspace.path / "src" / "App.tsx").write_text('fetch("data/sales_2026/uploads/sales.csv")')

    assert orch._detect_leaks(proj) == []          # a fetch is the intended pattern, not a leak
    assert orch._leaked_copy_paths(proj) == []


def test_detach_removes_a_leaked_copy_so_it_cant_reach_git(tmp_path: Path):
    # The core hole: while attached, a copy in src/ is kept out of commits by _detect_leaks. Detaching
    # forgets the entry, so the commit backstop stops covering it — detach must delete the copy itself.
    orch = _orch(tmp_path)
    proj = orch.project(start_preview=False)
    res = orch.upload_file("d.csv", b"a,b\n1,2\n")
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
    res = orch.upload_file("d.csv", b"x")
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
    res = orch.upload_file("big.csv", rows.encode())
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
    res = orch.upload_file("d.csv", b"a,b\n1,2\n")
    monkeypatch.setattr(appmod, "orchestrator", orch)
    client = TestClient(appmod.control_app)

    ok = client.get("/api/project/file", params={"path": res["path"]})
    assert ok.status_code == 200 and ok.json()["content"] == "a,b\n1,2\n"

    escape = client.get("/api/project/file", params={"path": "../../../../../../etc/passwd"})
    assert escape.status_code == 400            # not a known attachment -> resolver rejects the escape


def test_resolve_mentions_only_honors_known_attachments(tmp_path: Path):
    orch = _orch(tmp_path)
    proj = orch.project(start_preview=False)
    res = orch.upload_file("d.csv", b"x")

    assert [m["path"] for m in orch._resolve_mentions(proj, [res["path"]])] == [res["path"]]
    assert orch._resolve_mentions(proj, ["public/data/not-attached.csv"]) is None  # unknown -> ignored
    assert orch._resolve_mentions(proj, None) is None


def test_a_mention_the_turn_cannot_use_is_reported_rather_than_dropped(tmp_path: Path):
    # The picker offers more than a build can honor — Chat's own uploads live at the Project root, and
    # a Resource is usable only by the app holding a Binding for it — and both used to be skipped in
    # silence. That silence is how a turn builds from the wrong file while the right one sits in the
    # panel, so every mention the turn drops now says so, and says what to do about it.
    orch = _orch(tmp_path)
    proj = orch.project(start_preview=False)
    res = orch.upload_file("d.csv", b"x")

    used = orch._resolve_mentions(proj, [res["path"]])
    # It WAS used: nothing to say, and nothing to offer a button for either.
    assert orch._unusable_mentions(proj, used, [res["path"]], None) == ("", [])

    chat, _ = orch._unusable_mentions(proj, None, [".sage/scratch/events.csv"], None)
    assert "@events.csv" in chat and "Chat file" in chat and "Data panel" in chat

    plain, _ = orch._unusable_mentions(proj, None, ["public/data/gone.csv"], None)
    assert "@gone.csv" in plain and "not attached to this app" in plain

    ref = {"kind": KIND_DATA_SOURCE, "id": "ds1", "name": "Warehouse"}
    unbound, _ = orch._unusable_mentions(proj, None, None, [ref])
    assert "@Warehouse" in unbound and "Resources panel" in unbound
    # And a Resource this app IS bound to is not reported — the report reads the same Binding list the
    # turn honors, so a bound Resource must never come back as one the turn refused.
    proj.workspace.update_bindings(
        lambda entries: [*entries, Binding(KIND_DATA_SOURCE, "ds1", "Warehouse", "Warehouse").to_dict()])
    assert orch._unusable_mentions(proj, None, None, [ref]) == ("", [])


def test_upload_is_unavailable_when_no_writable_dataset_exists(tmp_path: Path):
    # UploadUnavailable fires only when there's genuinely nowhere writable to store bytes.
    prov = FakeAssetProvider()
    prov.assets = []
    orch = _orch(tmp_path, assets=prov)
    orch.project(start_preview=False)

    with pytest.raises(UploadUnavailable):
        orch.upload_file("x.csv", b"x")


def test_upload_to_a_picked_dataset_lands_under_uploads(tmp_path: Path):
    orch = _orch(tmp_path)
    ws = orch.project(start_preview=False).workspace.path
    pii = _dataset(orch, "customer_pii")

    res = orch.upload_file("note.csv", b"x", dataset_id=pii)

    assert res["dataset"] == "customer_pii"
    assert _manifest(ws)[0]["dataset_rel_path"] == "uploads/note.csv"


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


def _png_bytes(px: int = 40) -> bytes:
    """A REAL png. Stub headers no longer suffice: fit_image verifies the pixels decode, because a
    corrupt image that sails through would fail at the provider after the UI promised the agent
    could see it. Random pixels so the file can't compress away when we need it large."""
    import io as _io
    import os as _os

    from PIL import Image
    buf = _io.BytesIO()
    Image.frombytes("RGB", (px, px), _os.urandom(px * px * 3)).save(buf, "PNG")
    return buf.getvalue()


def test_an_attached_image_is_inlined_as_a_data_uri_for_the_agent(tmp_path: Path):
    """Images are the one type where the pixels ARE the shape, so the descriptor isn't enough.
    A data: URI is required — OpenCode emits malformed media for every file-path form."""
    orch = _orch(tmp_path, assets=FakeAssetProvider())
    project = orch.project(start_preview=False)
    orch.upload_file("shot.png", _png_bytes())

    out = orch._resolve_mentions(project, [project.attached[0]["path"]])[0]

    assert out["image_uri"].startswith("data:image/png;base64,")
    assert out["summary"] == "PNG image — 40x40"       # descriptor still travels alongside


def test_an_oversized_image_is_shrunk_so_the_agent_can_still_see_it(tmp_path: Path):
    """Refusing an oversized image costs the agent the whole picture, and a phone photo or hi-DPI
    screenshot is exactly what users attach. Verified live: gpt-5.4 read the correct quadrants off
    the shrunk copy of a file that the old hard cap rejected outright."""
    import base64 as _b64

    from sage.orchestrator import service as svc

    orch = _orch(tmp_path, assets=FakeAssetProvider())
    project = orch.project(start_preview=False)
    big = _png_bytes(1400)
    assert len(big) > svc._MAX_INLINE_IMAGE_BYTES
    orch.upload_file("huge.png", big)

    out = orch._resolve_mentions(project, [project.attached[0]["path"]])[0]

    assert out["image_uri"].startswith("data:image/")
    inlined = _b64.b64decode(out["image_uri"].split(",", 1)[1])
    assert len(inlined) <= svc._MAX_INLINE_IMAGE_BYTES
    assert len(inlined) < len(big)                     # actually shrunk, not passed through


def test_an_undecodable_image_reaches_the_agent_with_no_pixels(tmp_path: Path):
    orch = _orch(tmp_path, assets=FakeAssetProvider())
    project = orch.project(start_preview=False)
    orch.upload_file("broken.png", b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR"
                     + (900).to_bytes(4, "big") * 2 + b"junk" * 50)

    out = orch._resolve_mentions(project, [project.attached[0]["path"]])[0]

    assert out["image_uri"] is None                    # send_prompt turns this into the "NOT shown" note


def test_a_tabular_attachment_carries_no_image_uri(tmp_path: Path):
    orch = _orch(tmp_path, assets=FakeAssetProvider())
    project = orch.project(start_preview=False)
    orch.upload_file("q3.csv", b"a,b\n1,2\n")

    assert "image_uri" not in orch._resolve_mentions(project, [project.attached[0]["path"]])[0]


def test_a_failed_upload_leaves_no_orphan_bytes_on_the_dataset_mount(tmp_path: Path, monkeypatch):
    """The bytes land on the mount (outside git) before anything records them. Without a rollback a
    mid-upload failure strands data on a shared mount that detach/delete can't even see."""
    orch = _orch(tmp_path, assets=FakeAssetProvider())
    project = orch.project(start_preview=False)
    asset = next(a for a in orch._assets.list_datasets("Sage") if a.name == "sales_2026")
    monkeypatch.setattr(Orchestrator, "_write_agents_data_block",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("read-only workspace")))

    with pytest.raises(OSError):
        orch.upload_file("q3.csv", b"a,b\n1,2\n")

    assert not (Path(asset.mount_path) / "uploads" / "q3.csv").exists()   # bytes undone
    # Symlink undone and its dirs pruned back to public/data/, which detach leaves standing too.
    assert not (project.workspace.path / "public" / "data" / "sales_2026").exists()
    assert project.attached == []
    assert _manifest(project.workspace.path) == []


def test_a_failed_re_upload_does_not_delete_the_bytes_that_were_already_there(tmp_path: Path,
                                                                              monkeypatch):
    """Overwriting a same-named upload already destroyed the old bytes — deleting the file on
    rollback would turn one lost version into no file at all."""
    orch = _orch(tmp_path, assets=FakeAssetProvider())
    orch.project(start_preview=False)
    orch.upload_file("q3.csv", b"original\n")
    dest = next(a for a in orch._assets.list_datasets("Sage")
                if a.name == "sales_2026").mount_path
    monkeypatch.setattr(Orchestrator, "_write_agents_data_block",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("boom")))

    with pytest.raises(OSError):
        orch.upload_file("q3.csv", b"replacement\n")

    assert (Path(dest) / "uploads" / "q3.csv").exists()   # kept, not compounded into a deletion


def test_agents_block_warns_that_search_cannot_see_attached_files(tmp_path: Path):
    """Live failure: the agent grepped an attached CSV for a value on line 619, got no matches, and
    answered "not found". ripgrep skips gitignored paths and won't follow symlinks — attachments are
    both — so search silently returns nothing. That's a wrong answer, not an error."""
    orch = _orch(tmp_path, assets=FakeAssetProvider())
    ws = orch.project(start_preview=False).workspace.path
    orch.upload_file("q3.csv", b"region,note\nZZ,ORION-7734\n")

    agents = (ws / "AGENTS.md").read_text()
    assert "read tool on its exact disk path" in agents
    assert "Do NOT use grep/search" in agents.replace("\n", " ")
    assert "finds nothing here proves nothing" in agents


def test_an_attached_image_records_whether_the_agent_will_see_it(tmp_path: Path):
    """The Data panel needs this: a user who attaches an image the agent can't see currently gets
    no signal at all — the agent just answers "unknown" and nothing explains why."""
    from PIL import Image

    orch = _orch(tmp_path, assets=FakeAssetProvider())
    ws = orch.project(start_preview=False).workspace.path
    good = tmp_path / "ok.png"
    Image.new("RGB", (40, 40), (230, 30, 30)).save(good, "PNG")

    res = orch.upload_file("ok.png", good.read_bytes())

    assert res["descriptor"]["kind"] == "image"
    assert res["descriptor"]["shown"] is True          # returned inline, so the panel can flag now
    assert _manifest(ws)[0]["descriptor"]["shown"] is True


def test_an_undecodable_image_is_flagged_as_unseen_for_the_data_panel(tmp_path: Path):
    orch = _orch(tmp_path, assets=FakeAssetProvider())
    orch.project(start_preview=False)
    broken = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + (900).to_bytes(4, "big") * 2 + b"junk" * 50

    res = orch.upload_file("broken.png", broken)

    assert res["descriptor"]["kind"] == "image"
    assert res["descriptor"]["shown"] is False


def test_upload_during_a_turn_is_not_mistaken_for_the_agent_writing(tmp_path: Path):
    """Uploading a file mid-turn writes AGENTS.md, .gitignore and a public/data/ symlink — all
    inside the snapshotted working tree, none of them the agent's doing. Before the fix, the turn's
    end-of-run tree comparison read those as "the agent wrote code", which on a gated (Plan) turn
    meant a false `gate violated` AND a discard_changes() that deleted the fresh upload. The upload
    path must move the running turn's baseline forward instead."""
    orch = _orch(tmp_path)
    project = orch.project(start_preview=False)

    # Stand in for a turn in flight: the baseline build_stream would have taken at its start.
    project.turn_tree_baseline = project.snapshot.working_tree_hash()
    before = project.turn_tree_baseline

    orch.upload_file("mid_turn.csv", b"a,b\n1,2\n")

    assert project.snapshot.working_tree_hash() != before      # the upload really did change the tree
    assert project.turn_tree_baseline == project.snapshot.working_tree_hash()  # ...and was absorbed


def test_upload_outside_a_turn_leaves_the_baseline_alone(tmp_path: Path):
    """No turn running means no baseline to move — the rebaseline hook must stay a no-op rather
    than seed one, or the next turn would start by comparing against a stale hash."""
    orch = _orch(tmp_path)
    project = orch.project(start_preview=False)
    assert project.turn_tree_baseline == ""

    orch.upload_file("idle.csv", b"a,b\n1,2\n")

    assert project.turn_tree_baseline == ""


def test_a_turn_that_deletes_an_attachment_gets_it_put_back(tmp_path: Path):
    # #37, live 2026-08-24: told to "remove everything you have built", the agent took the user's
    # uploaded CSV with it. The file left the @ menu and they had to attach it again to say the same
    # sentence. Neither obvious enforcement point can carry this — the shim gates by tool NAME and a
    # bash `rm` is not a write tool, and the turn snapshot stages with `add -A`, which honours the
    # .gitignore attach_file writes `public/data/` into. project.attached is process memory, which no
    # tool reaches, so that is what the repair rebuilds from.
    orch = _orch(tmp_path, assets=FakeAssetProvider())
    project = orch.project(start_preview=False)
    orch.upload_file("q3.csv", b"region,revenue\nwest,10\neast,20\n")
    link = project.workspace.path / project.attached[0]["path"]
    assert link.is_symlink()

    link.unlink()                        # what the turn did
    project.workspace.attachments_path.unlink()
    orch._restore_attachments()

    assert link.is_symlink()             # the file is back in the @ menu
    assert link.read_bytes() == b"region,revenue\nwest,10\neast,20\n"   # and points at the real rows
    assert [e["path"] for e in _manifest(project.workspace.path)] == [project.attached[0]["path"]]
    ev = [e for e in project.workspace.read_history() if e["type"] == "attachments-restored"]
    assert len(ev) == 1 and ev[0]["paths"] == [project.attached[0]["path"]]   # said out loud, not silent


def test_a_turn_that_deletes_nothing_restores_nothing_and_says_nothing(tmp_path: Path):
    # The repair runs at the end of EVERY turn, so a quiet turn must stay quiet: no warning card for
    # a build that behaved.
    orch = _orch(tmp_path, assets=FakeAssetProvider())
    project = orch.project(start_preview=False)
    orch.upload_file("q3.csv", b"region,revenue\nwest,10\neast,20\n")

    orch._restore_attachments()

    assert not [e for e in project.workspace.read_history() if e["type"] == "attachments-restored"]


def test_scratch_upload_does_not_need_a_dataset(tmp_path: Path):
    orch = _orch(tmp_path)
    # Scratch is Chat's, so it lives with the Project rather than inside the Built App.
    root = orch.project(start_preview=False).record.path
    res = orch.upload_scratch("my data.csv", b"a,b\n1,2\n")
    assert res["source"] == "scratch"
    assert res["path"] == ".sage/scratch/my_data.csv"
    assert (root / res["path"]).read_bytes() == b"a,b\n1,2\n"
    gi = (root / ".gitignore").read_text()
    assert ".sage/scratch/" in gi
    assert "scratch" in {e["source"] for e in orch.project(start_preview=False).status()["scratch"]}


def test_promote_scratch_copies_onto_a_dataset_and_drops_the_scratch_copy(tmp_path: Path):
    orch = _orch(tmp_path)
    ws = orch.project(start_preview=False).workspace.path
    scratch = orch.upload_scratch("note.csv", b"x")
    ds = orch.default_dataset_id()
    res = orch.promote_scratch_to_dataset(scratch["path"], ds)
    assert not (ws / scratch["path"]).exists()
    assert res["path"].startswith("public/data/")
    assert (ws / res["path"]).read_bytes() == b"x"


def test_list_asset_files_accepts_a_membership_id(tmp_path: Path):
    orch = _orch(tmp_path)
    files = orch.list_asset_files("dataset:ds_sales_2026")
    names = {f["path"] for f in files}
    assert "train.csv" in names


class _UnmountedAssets:
    """One Dataset this container has no mount for — the ordinary case for anything shared.

    Mounts are fixed when the execution starts and only ever cover one project, so a Dataset the
    person can read is usually not on this disk. It is still readable through the data library.
    """

    def __init__(self, payload: bytes = b"a,b\n1,2\n"):
        self.asset = Asset(id="ds_shared", name="Oil-and-Gas-Demo", project="Oil-and-Gas-Demo")
        self.payload = payload
        self.downloads: list[str] = []

    def list_datasets(self, project_id):
        return [self.asset]

    def list_files(self, asset):
        return [DatasetFile("raw/wells.csv", 0)]   # the API listing carries no sizes

    def download_file(self, asset, rel_path, dest):
        self.downloads.append(rel_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self.payload)
        return len(self.payload)


def test_a_dataset_with_no_mount_still_lists_its_files(tmp_path: Path):
    orch = _orch(tmp_path, assets=_UnmountedAssets())
    files = orch.list_asset_files("ds_shared")
    assert [f["path"] for f in files] == ["raw/wells.csv"]
    assert files[0]["attached"] is False


def test_attaching_from_an_unmounted_dataset_downloads_the_bytes(tmp_path: Path):
    assets = _UnmountedAssets()
    orch = _orch(tmp_path, assets=assets)
    ws = orch.project(start_preview=False).workspace.path

    res = orch.attach_file("ds_shared", "raw/wells.csv")

    dest = ws / res["path"]
    assert dest.is_file() and not dest.is_symlink()      # nothing to link to; the bytes are copied
    assert dest.read_bytes() == b"a,b\n1,2\n"
    assert assets.downloads == ["raw/wells.csv"]
    assert res["size"] == 8
    assert not list(dest.parent.glob("*.part"))          # no half-written leftovers


def test_an_over_cap_download_is_discarded_rather_than_attached(tmp_path: Path):
    # The listing has no sizes, so the cap can only be judged after the bytes arrive. What must not
    # happen is a file that blew the cap being left behind as an attachment.
    assets = _UnmountedAssets(payload=b"x" * 4096)
    orch = _orch(tmp_path, assets=assets)
    orch._attach_max_bytes = 16
    ws = orch.project(start_preview=False).workspace.path

    with pytest.raises(AttachTooLarge):
        orch.attach_file("ds_shared", "raw/wells.csv")

    assert orch.project(start_preview=False).attached == []
    assert not list((ws / "public" / "data").rglob("*"))


def test_a_chat_fetch_lands_in_scratch_and_leaves_the_app_alone(tmp_path: Path):
    """A question has no app. Routing a chip through attach_file put the bytes in the published
    app's asset tree and wrote them into the committed manifest, so asking what was in a file
    enrolled it in every later publish of an app that may never reference it."""
    orch = _orch(tmp_path)
    project = orch.project(start_preview=False)
    ws, root = project.workspace.path, project.record.path

    res = orch.fetch_dataset_file_for_chat(_dataset(orch, "sales_2026"), "train.csv")

    assert res["path"] == ".sage/scratch/datasets/sales_2026/train.csv"
    link = root / res["path"]
    assert link.is_symlink()                                  # mounted: still no byte copy
    assert link.read_text().startswith("month,revenue")
    assert not (ws / "public" / "data").exists()
    assert orch.project(start_preview=False).attached == []
    assert ".sage/scratch/" in (root / ".gitignore").read_text()


def test_two_chips_naming_the_same_file_fetch_it_once(tmp_path: Path):
    assets = _UnmountedAssets()
    orch = _orch(tmp_path, assets=assets)
    orch.project(start_preview=False)

    first = orch.fetch_dataset_file_for_chat("ds_shared", "raw/wells.csv")
    again = orch.fetch_dataset_file_for_chat("ds_shared", "raw/wells.csv")

    assert first["path"] == again["path"]
    assert assets.downloads == ["raw/wells.csv"]


def test_a_handoff_hands_the_scratch_bytes_over_instead_of_fetching_them_again(tmp_path: Path):
    """The handoff is where a Thread becomes an app, so it is where the app's data tree gets the
    file. The bytes are already here — asking Domino for them a second time is the copy this whole
    split exists to avoid — and the scratch copy stays, because the Thread's chip still names it."""
    assets = _UnmountedAssets()
    orch = _orch(tmp_path, assets=assets)
    project = orch.project(start_preview=False)
    ws, root = project.workspace.path, project.record.path
    fetched = orch.fetch_dataset_file_for_chat("ds_shared", "raw/wells.csv")

    orch._promote_chat_file({"kind": "file", "datasetId": "ds_shared",
                             "datasetRelPath": "raw/wells.csv", "path": fetched["path"]})

    assert assets.downloads == ["raw/wells.csv"]              # once, not once per surface
    entry = _manifest(ws)[0]
    assert entry["path"] == "public/data/Oil-and-Gas-Demo/raw/wells.csv"
    served = ws / entry["path"]
    assert served.read_bytes() == b"a,b\n1,2\n"
    assert (root / fetched["path"]).is_file()                 # the chip's path still resolves


def _chip(orch, thread_id: str) -> dict:
    return orch.add_thread_context(thread_id, {
        "kind": "file", "name": "wells.csv",
        "datasetId": "ds_shared", "datasetRelPath": "raw/wells.csv",
    })


def test_closing_the_last_chip_releases_the_bytes_it_fetched(tmp_path: Path):
    """Nothing else releases them, and every fetch counts against the cap — so scratch filled up
    and then quietly refused new fetches, with nothing on screen to say why."""
    orch = _orch(tmp_path, assets=_UnmountedAssets())
    root = orch.project(start_preview=False).record.path
    tid = orch.create_thread()["id"]
    row = _chip(orch, tid)
    fetched = root / row["path"]
    assert fetched.is_file()

    assert orch.remove_thread_context(tid, row["id"]) is True

    assert not fetched.exists()
    assert not fetched.parent.exists()          # and no empty folders left standing
    assert (root / ".sage" / "scratch" / "datasets").is_dir()


def test_a_chip_in_another_thread_keeps_the_file(tmp_path: Path):
    """A fetch is shared. The person closing one chip is not speaking for the other conversation."""
    assets = _UnmountedAssets()
    orch = _orch(tmp_path, assets=assets)
    root = orch.project(start_preview=False).record.path
    one, two = orch.create_thread()["id"], orch.create_thread()["id"]
    first, second = _chip(orch, one), _chip(orch, two)
    assert first["path"] == second["path"]
    assert assets.downloads == ["raw/wells.csv"]

    orch.remove_thread_context(one, first["id"])

    assert (root / second["path"]).is_file()


def test_a_file_the_app_now_stands_on_is_not_released(tmp_path: Path):
    """After a handoff the app's data path is a symlink onto these bytes. Deleting them would
    leave the app pointing at nothing."""
    orch = _orch(tmp_path, assets=_UnmountedAssets())
    project = orch.project(start_preview=False)
    ws, root = project.workspace.path, project.record.path
    tid = orch.create_thread()["id"]
    row = _chip(orch, tid)
    orch._promote_chat_file({"kind": "file", "datasetId": "ds_shared",
                             "datasetRelPath": "raw/wells.csv", "path": row["path"]})

    orch.remove_thread_context(tid, row["id"])

    assert (root / row["path"]).is_file()
    assert (ws / _manifest(ws)[0]["path"]).read_bytes() == b"a,b\n1,2\n"


def test_deleting_a_thread_releases_what_it_fetched(tmp_path: Path):
    """The chips go with the Thread, so the only record of what it fetched goes with it too."""
    orch = _orch(tmp_path, assets=_UnmountedAssets())
    ws = orch.project(start_preview=False).workspace.path
    tid = orch.create_thread()["id"]
    row = _chip(orch, tid)

    orch.delete_thread(tid)

    assert not (ws / row["path"]).exists()


def test_closing_a_chip_never_touches_an_attachment_the_app_owns(tmp_path: Path):
    """A file attached in Build can be pinned into a Thread. Closing that chip is not a detach."""
    orch = _orch(tmp_path)
    ws = orch.project(start_preview=False).workspace.path
    ds = _dataset(orch, "sales_2026")
    res = orch.attach_file(ds, "train.csv")
    tid = orch.create_thread()["id"]
    row = orch.add_thread_context(tid, {
        "kind": "file", "name": "train.csv", "path": res["path"],
        "datasetId": ds, "datasetRelPath": "train.csv",
    })

    orch.remove_thread_context(tid, row["id"])

    assert (ws / res["path"]).exists()
    assert _manifest(ws)[0]["path"] == res["path"]


def test_a_chip_that_is_not_a_dataset_file_is_not_promoted(tmp_path: Path):
    """The handoff loop sees every chip. A Data Source has no bytes to hand over."""
    orch = _orch(tmp_path)
    ws = orch.project(start_preview=False).workspace.path

    orch._promote_chat_file({"kind": "data_source", "name": "trades"})
    orch._promote_chat_file({"kind": "file", "name": "notes.md", "path": ".sage/scratch/notes.md"})

    assert orch.project(start_preview=False).attached == []
    assert not (ws / "public" / "data").exists()


def test_an_unmounted_attachment_is_rehydrated_by_downloading_it_again(tmp_path: Path):
    assets = _UnmountedAssets()
    orch = _orch(tmp_path, assets=assets)
    ws = orch.project(start_preview=False).workspace.path
    rel = orch.attach_file("ds_shared", "raw/wells.csv")["path"]

    (ws / rel).unlink()                                   # the agent deleted it mid-turn
    orch._restore_attachments()

    assert (ws / rel).read_bytes() == b"a,b\n1,2\n"
    assert assets.downloads == ["raw/wells.csv", "raw/wells.csv"]


def test_the_exclude_list_covers_a_copy_in_an_idle_built_app(tmp_path: Path):
    # The commit runs `git add -A` at the Project root and stages every Built App, so an exclude
    # list drawn from the app being built lets a copy sitting in the other one ride out with it
    # (#81). The nudge stays narrow: this agent did not make that copy and cannot move it.
    orch = _orch(tmp_path)
    proj = orch.project(start_preview=False)
    idle = proj.workspace.app_id
    orch.upload_file("sales.csv", b"a,b\n1,2\n")
    (proj.workspace.path / "src" / "sales.csv").write_text("a,b\n1,2\n")   # agent copied it into src/

    orch.create_app()                                    # mint a second app and build in that one

    assert orch._detect_leaks(proj) == []
    assert orch._leaked_copy_paths(proj) == [f"apps/{idle}/src/sales.csv"]


def test_the_turns_own_app_stays_covered_after_the_person_looks_away(tmp_path: Path):
    # #77: a build carries on in the app it started in while the person reads another. The tree the
    # agent copied into is the pinned one, and it is still in the commit the turn ends with.
    orch = _orch(tmp_path)
    proj = orch.project(start_preview=False)
    building = proj.workspace.app_id
    orch.upload_file("sales.csv", b"a,b\n1,2\n")
    (proj.workspace.path / "src" / "sales.csv").write_text("a,b\n1,2\n")
    proj.turn_app, proj.turn_attached = proj.workspace, list(proj.attached)   # what a turn pins

    orch.create_app()                                                        # person looks away

    assert orch._detect_leaks(proj) == [("sales.csv", ["src/sales.csv"])]
    assert orch._leaked_copy_paths(proj) == [f"apps/{building}/src/sales.csv"]
