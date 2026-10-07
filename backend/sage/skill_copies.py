"""Whether the files a build copied from a Project skill still match the skill's own (#682).

A skill can ship code beside its SKILL.md, and the build copies it into the app with its write tool,
so nothing stops a later turn editing the copy. Copies are found by BASENAME: a skill cannot know
the app's folder layout, so `deal-brief/dealDesk.js` lands as `static/dealDesk.js`. A copy renamed
on the way in is not found. The leading comment is not compared, because the model rewords it.
"""
from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path, PurePosixPath

from . import extensions

_SLASH_COMMENTED = {".js", ".ts", ".jsx", ".tsx"}


def shipped_basenames(project_root: Path) -> frozenset[str]:
    """The basenames of the non-markdown files the Project's skills ship, read off the manifest
    alone, so a Project whose skills ship no code can skip the check for the price of one read."""
    names = (PurePosixPath(rel).name for entry in extensions.read_manifest(project_root)
             if entry["kind"] == "skill" for rel in entry.get("files") or [] if isinstance(rel, str))
    return frozenset(name for name in names if not name.endswith(".md"))


def _normalised(text: str, suffix: str) -> list[str]:
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    i = 0
    if suffix in _SLASH_COMMENTED:
        while i < len(lines) and not lines[i]:
            i += 1
        if i < len(lines) and lines[i].lstrip().startswith("/*"):
            while i < len(lines) and "*/" not in lines[i]:
                i += 1
            i += 1
        else:
            while i < len(lines) and (not lines[i] or lines[i].lstrip().startswith("//")):
                i += 1
    elif suffix == ".py":
        while i < len(lines) and (not lines[i] or lines[i].lstrip().startswith("#")):
            i += 1
    body = lines[i:]
    while body and not body[0]:
        body.pop(0)
    while body and not body[-1]:
        body.pop()
    return body


def drift(project_root: Path, app_root: Path, changed: Iterable[str],
          owned: frozenset[str]) -> dict | None:
    """The `skill-copy-drift` row for the files in `changed` that differ from a skill file of the
    same basename, or None when every such copy matches."""
    shipped: dict[str, list[tuple[str, str, str]]] = {}
    for entry in extensions.read_manifest(project_root):
        if entry["kind"] != "skill":
            continue
        for f in extensions.skill_files(project_root, entry["id"]):
            name = PurePosixPath(f["path"]).name
            if not name.endswith(".md"):
                shipped.setdefault(name, []).append((entry["name"], f["path"], f["text"]))
    flagged = []
    for app_path in sorted(changed):
        candidates = shipped.get(PurePosixPath(app_path).name)
        if not candidates or app_path in owned or "node_modules" in PurePosixPath(app_path).parts:
            continue
        try:
            copy = (app_root / app_path).read_text()
        except (OSError, UnicodeDecodeError):
            continue
        suffix = PurePosixPath(app_path).suffix
        for skill, skill_file, text in candidates:
            if _normalised(copy, suffix) != _normalised(text, suffix):
                flagged.append({"app": app_path, "skill": skill, "skillFile": skill_file})
    if not flagged:
        return None
    return {"type": "skill-copy-drift", "files": flagged, "message": " ".join(
        f"{f['app']} no longer matches {f['skillFile']} in the {f['skill']} skill."
        for f in flagged)}
