"""Bound attachment recovery without allowing a timed-out download to publish later."""
from __future__ import annotations

import concurrent.futures
import os
import shutil
import tempfile
import threading
from contextlib import nullcontext
from pathlib import Path

from ..resources.provider import ResourceUnavailable

TIMEOUT_S = 20.0


def repair(entry: dict, dest: Path, find_asset, assets, timeout_s: float,
           *, publish_guard=None) -> bool:
    # The SDK has no deadline argument. Its worker may outlive this wait, so it owns only a
    # private staging directory. Only the waiting caller can publish into the app. Cleanup on
    # late completion is safe even if the person has removed the attachment meanwhile.
    stage = Path(tempfile.mkdtemp(prefix="sage-attachment-"))
    result = concurrent.futures.Future()

    def fetch():
        try:
            asset = find_asset(entry.get("dataset_id"))
            rel = entry.get("dataset_rel_path") or entry.get("file") or ""
            if asset.mount_path:
                mount = Path(asset.mount_path).resolve()
                source = Path(os.path.normpath(mount / rel))
                if not source.is_relative_to(mount) or not source.is_file():
                    raise FileNotFoundError("attachment source is unavailable")
                result.set_result((source, True))
            else:
                source = stage / "download"
                assets.download_file(asset, rel, source)
                if not source.is_file():
                    raise FileNotFoundError("attachment download is unavailable")
                result.set_result((source, False))
        except Exception as exc:
            result.set_exception(exc)

    threading.Thread(target=fetch, daemon=True, name="sage-attachment-repair").start()
    try:
        source, is_link = result.result(timeout=max(0, timeout_s))
    except concurrent.futures.TimeoutError as exc:
        result.add_done_callback(lambda _: shutil.rmtree(stage, ignore_errors=True))
        raise ResourceUnavailable("Attachment recovery timed out. Try attaching the file again.") from exc
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    try:
        with (publish_guard() if publish_guard else nullcontext(True)) as current:
            if not current:
                return False
            dest.parent.mkdir(parents=True, exist_ok=True)
            # Stage beside the destination for an atomic replace even when /tmp and the app differ.
            fd, name = tempfile.mkstemp(prefix=".sage-restore-", dir=dest.parent)
            os.close(fd)
            staged = Path(name)
            try:
                if is_link:
                    staged.unlink()
                    staged.symlink_to(source)
                else:
                    shutil.copyfile(source, staged)
                os.replace(staged, dest)
            finally:
                staged.unlink(missing_ok=True)
            return True
    finally:
        shutil.rmtree(stage, ignore_errors=True)
