"""A Project's own skills (ADR-0071).

They live in OpenCode's project slot, `.opencode/` at the root of the Project volume, so OpenCode
loads them itself. `.opencode/sage-extensions.json` is Sage's manifest: what each extension is
and which files it owns. The shim reads the manifest through an `ExtensionCatalog`, never the
files.
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import zipfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

from .implementation_request import _OPTIONAL_BLOCKS as BUILTIN_SECTIONS
from .provision.credentials import extract_token

SLOT = Path(".opencode")
MANIFEST = SLOT / "sage-extensions.json"
KINDS = ("skill",)
RESERVED_PREFIXES = ("sage-", "sage_")
_REPO = Path(__file__).resolve().parents[2]
# Where the skills Sage ships live: installed globally, or seeded into each Built App.
_BUILTIN_SKILL_GLOBS = ("template/skills/*/SKILL.md", "template/*/.opencode/skills/*/SKILL.md")
_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
_FRONTMATTER_NAME = re.compile(r"\A---\s*\n(?:.*\n)*?name:\s*['\"]?([^'\"\n]*?)['\"]?\s*\n")
# What one upload or git import may bring in, across every skill it holds.
_MAX_SKILL_BYTES = 5 * 1024 * 1024
_MAX_SKILL_FILES = 200
# How much of one SKILL.md an @-mention puts into the turn (#737). The rest stays loadable with the
# skill tool.
INLINE_SKILL_BYTES = 32 * 1024
_LOCK = threading.Lock()
# A clone's credential helper prints the token from its environment, so only the variable's name
# is ever in argv.
_CLONE_TOKEN_ENV = "SAGE_CLONE_TOKEN"
_CLONE_HELPER = (f'!f() {{ test "$1" = get && '
                 f'printf "username=x-access-token\\npassword=%s\\n" "${_CLONE_TOKEN_ENV}"; }}; f')


class ExtensionError(ValueError):
    """An extension that cannot be added as asked. The message is for the person."""


@dataclass(frozen=True)
class ExtensionCatalog:
    """Which skills belong to which extension."""

    skills: dict[str, str] = field(default_factory=dict)
    # Built-in skill or instruction section -> the id of the Project skill that replaces it while
    # enabled.
    replaced: dict[str, str] = field(default_factory=dict)
    # Skill name -> its SKILL.md cut to INLINE_SKILL_BYTES, and the file's whole size in bytes. Read
    # when OpenCode reloads, so it is the text OpenCode's own skill tool would load. A skill whose
    # SKILL.md cannot be read is absent.
    skill_md: dict[str, tuple[str, int]] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return bool(self.skills)


EMPTY = ExtensionCatalog()


def catalog_of(entries: list[dict]) -> ExtensionCatalog:
    skills: dict[str, str] = {}
    replaced: dict[str, str] = {}
    for entry in entries:
        ext_id, name = entry["id"], entry["name"]
        if entry["kind"] == "skill":
            skills[name] = ext_id
            if isinstance(entry.get("replaces"), str) and entry["replaces"]:
                replaced[entry["replaces"]] = ext_id
    return ExtensionCatalog(skills, replaced)


def named_in(text: str, names) -> set[str]:
    """The `names` that `text` mentions as a whole `@<name>`."""
    return {n for n in names if re.search(rf"(?<![\w@])@{re.escape(n)}(?![\w-])", text)}


def skill_description(text: str) -> str:
    """The frontmatter `description` of a SKILL.md's text, or "" when there is none.

    The one field OpenCode filters on. Read with a regex rather than a YAML parser because there is
    no YAML dependency declared in `backend/pyproject.toml`. A block indicator (`>`/`|`) counts as
    present, as do the indented lines of a plain multi-line scalar; CRLF and a closing `---` with
    nothing after it both read too. Multi-line values come back unwrapped: `|` keeps its line
    breaks, `>` and a plain scalar fold onto one line.
    """
    front = re.match(r"^---\r?\n(.*?)\r?\n---[ \t]*(\r?\n|\Z)", text, re.DOTALL)
    if not front:
        return ""
    found = re.search(r"^description:[ \t]*(.*(?:\n[ \t]+\S.*)*)$", front.group(1), re.MULTILINE)
    if not found:
        return ""
    head, *rest = [line.strip() for line in found.group(1).split("\n")]
    if re.fullmatch(r"[>|]([-+]?\d?|\d[-+])", head):
        return ("\n" if head[0] == "|" else " ").join(rest).strip()
    return " ".join([head, *rest]).strip().strip("\"'")


def _frontmatter_name(text: str) -> str:
    declared = _FRONTMATTER_NAME.match(text)
    return declared.group(1).strip() if declared else ""


def builtin_skills(repo: Path | None = None) -> dict[str, str]:
    """The skills Sage ships, by the name OpenCode gives them, with their descriptions.

    Read from the template rather than listed, so a skill Sage adds is reserved and replaceable
    without an edit here. Measured on OpenCode 1.18.4 (#620): when two skills share a name, Sage's
    copy is the one offered, from the global slot and from an app's own `.opencode/` alike.
    """
    found: dict[str, str] = {}
    for pattern in _BUILTIN_SKILL_GLOBS:
        for skill_md in sorted((repo or _REPO).glob(pattern)):
            text = skill_md.read_text(errors="replace")
            found.setdefault(_frontmatter_name(text) or skill_md.parent.name,
                             skill_description(text))
    return found


def read_manifest(root: Path) -> list[dict]:
    try:
        body = json.loads((Path(root) / MANIFEST).read_text())
    except (OSError, ValueError):
        return []
    entries = body.get("extensions") if isinstance(body, dict) else None
    return [e for e in entries or [] if isinstance(e, dict) and e.get("kind") in KINDS
            and isinstance(e.get("id"), str) and isinstance(e.get("name"), str)]


def load_catalog(root: Path) -> ExtensionCatalog:
    """Without shadowed skills: the entry OpenCode offers under that name is Sage's, so switching
    the Project's off must not hide it, nor may it be described as the Project's."""
    catalog = catalog_of([e for e in list_extensions(root) if not e.get("shadowed")])
    skill_md: dict[str, tuple[str, int]] = {}
    for name in catalog.skills:
        path = Path(root) / SLOT / "skills" / name / "SKILL.md"
        try:
            with path.open("rb") as f:
                head = f.read(INLINE_SKILL_BYTES)
            skill_md[name] = (head.decode("utf-8", errors="ignore"), path.stat().st_size)
        except OSError:
            continue
    return ExtensionCatalog(catalog.skills, catalog.replaced, skill_md)


def disabled(entries: list[dict], overrides: dict) -> frozenset[str]:
    """The ids switched off for one Thread or App. `overrides` is that record's `extensions` map."""
    overrides = overrides if isinstance(overrides, dict) else {}
    return frozenset(e["id"] for e in entries
                     if not overrides.get(e["id"], e.get("defaultEnabled", True)))


def _write_manifest(root: Path, entries: list[dict]) -> None:
    _write_json(root / MANIFEST, {"version": 1, "extensions": entries})


def _write_json(path: Path, body: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(body, indent=2) + "\n")
    tmp.replace(path)


def _check_name(name: object, entries: list[dict]) -> str:
    if not isinstance(name, str) or not _NAME.match(name):
        raise ExtensionError("A name is lowercase letters, digits, '-' and '_', starting with a "
                             "letter or digit, at most 63 characters.")
    if name.startswith(RESERVED_PREFIXES):
        raise ExtensionError(f"'{name}' starts with 'sage-' or 'sage_', which Sage keeps for its own.")
    if name in builtin_skills():
        raise ExtensionError(f"'{name}' is the name of a skill Sage ships. Rename yours, or keep "
                             f"the name you like and choose 'Replaces {name}'.")
    if any(entry["name"] == name for entry in entries):
        raise ExtensionError(f"This Project already has a skill called '{name}'.")
    return name


def _relative(path: object) -> PurePosixPath:
    if not isinstance(path, str) or not path:
        raise ExtensionError("Every file needs a path.")
    rel = PurePosixPath(path)
    if rel.is_absolute() or ".." in rel.parts or "\\" in path:
        raise ExtensionError(f"'{path}' is not a path inside the extension.")
    return rel


def list_extensions(root: Path) -> list[dict]:
    """The manifest, with each skill that a skill Sage ships has since taken the name of marked
    `shadowed`: OpenCode offers Sage's copy, so the Project's is never seen until renamed."""
    builtins = builtin_skills()
    return [{**e, "shadowed": True} if e["kind"] == "skill" and e["name"] in builtins else e
            for e in read_manifest(root)]


def add(root: Path, body: dict, *, _created: list[Path] | None = None) -> dict:
    """Write one extension into the project slot and record it. Answers its manifest entry."""
    root = Path(root)
    kind = body.get("kind")
    if kind not in KINDS:
        raise ExtensionError("kind is skill.")
    name = body.get("name")
    files = body.get("files")
    if name is None and isinstance(files, dict) and isinstance(files.get("SKILL.md"), str):
        name = _frontmatter_name(files["SKILL.md"]) or None
    with _LOCK:
        entries = read_manifest(root)
        name = _check_name(name, entries)
        entry: dict = {"id": f"{kind}:{name}", "kind": kind, "name": name,
                       "source": body.get("source") if isinstance(body.get("source"), dict)
                       else {"type": "upload"},
                       "defaultEnabled": True}
        replaces = body.get("replaces") or ""
        if replaces:
            _check_replaces(replaces, entry["id"], entries)
            entry["replaces"] = replaces
        entry["files"] = _write_skill(root, name, files, created=_created)
        entries.append(entry)
        _write_manifest(root, entries)
        return entry


def remove(root: Path, ext_id: str) -> bool:
    root = Path(root)
    with _LOCK:
        entries = read_manifest(root)
        entry = next((e for e in entries if e["id"] == ext_id), None)
        if entry is None:
            return False
        shutil.rmtree(root / SLOT / "skills" / entry["name"], ignore_errors=True)
        _write_manifest(root, [e for e in entries if e["id"] != ext_id])
        return True


def _check_replaces(replaces: str, ext_id: str, entries: list[dict]) -> None:
    if replaces not in (*builtin_skills(), *BUILTIN_SECTIONS):
        raise ExtensionError(f"'{replaces}' is not a skill Sage ships, nor a section of its build "
                             "instructions.")
    holder = next((e for e in entries if e.get("replaces") == replaces and e["id"] != ext_id), None)
    if holder:
        raise ExtensionError(f"'{holder['name']}' already replaces '{replaces}'.")


def set_replaces(root: Path, ext_id: str, replaces: str) -> dict:
    """What a Project skill stands in for, changed after it was added; "" for nothing. Raises
    KeyError for an unknown skill."""
    root = Path(root)
    with _LOCK:
        entries = read_manifest(root)
        entry = next((e for e in entries if e["id"] == ext_id and e["kind"] == "skill"), None)
        if entry is None:
            raise KeyError(ext_id)
        if replaces:
            _check_replaces(replaces, ext_id, entries)
            entry["replaces"] = replaces
        else:
            entry.pop("replaces", None)
        _write_manifest(root, entries)
        return entry


def skill_files(root: Path, ext_id: str) -> list[dict]:
    """A Project skill's files as written, each `{path, text}` with `path` inside the skill,
    SKILL.md first. Raises KeyError for an unknown skill."""
    root = Path(root)
    entry = next((e for e in read_manifest(root) if e["id"] == ext_id and e["kind"] == "skill"),
                 None)
    if entry is None:
        raise KeyError(ext_id)
    prefix = (SLOT / "skills" / entry["name"]).as_posix() + "/"
    out = []
    for rel in entry.get("files") or []:
        try:
            text = (root / _relative(rel)).read_text()
        except (OSError, UnicodeDecodeError):
            continue
        out.append({"path": rel.removeprefix(prefix), "text": text})
    return sorted(out, key=lambda f: (f["path"] != "SKILL.md", f["path"]))


def update_skill(root: Path, ext_id: str, files: dict[str, str], source: dict) -> dict:
    """Rewrite a Project skill's files from a fresh read of its source, keeping its id, what it
    replaces, and every switch on it. Raises KeyError for an unknown skill."""
    root = Path(root)
    with _LOCK:
        entries = read_manifest(root)
        entry = next((e for e in entries if e["id"] == ext_id and e["kind"] == "skill"), None)
        if entry is None:
            raise KeyError(ext_id)
        folder = root / SLOT / "skills" / entry["name"]
        # Outside `skills/`, so OpenCode never loads the old copy as a second skill meanwhile.
        aside = Path(tempfile.mkdtemp(dir=root / SLOT)) / entry["name"]
        if folder.exists():
            folder.rename(aside)
        try:
            entry["files"] = _write_skill(root, entry["name"], files)
        except Exception:
            shutil.rmtree(folder, ignore_errors=True)
            if aside.exists():
                aside.rename(folder)
            raise
        finally:
            shutil.rmtree(aside.parent, ignore_errors=True)
        entry["source"] = source
        _write_manifest(root, entries)
        return entry


def _check_description(name: str, skill_md: str) -> None:
    if not skill_description(skill_md):
        raise ExtensionError(f"'{name}' has no description in its SKILL.md frontmatter. OpenCode "
                             "loads a skill without one and never offers it to the model.")


def _same_skill_files(folder: Path, files: dict[PurePosixPath, str]) -> bool:
    """A copied skill may be registered, but only its exact, local files may become Sage-owned."""
    try:
        if any(p.is_symlink() or not p.is_dir()
               for p in (folder, folder.parent, folder.parent.parent)):
            return False
        expected = set(files) | {parent for rel in files for parent in rel.parents
                                 if parent != PurePosixPath(".")}
        existing = list(folder.rglob("*"))
        if {p.relative_to(folder) for p in existing} != expected:
            return False
        if any(p.is_symlink() or not (p.is_file() or p.is_dir()) for p in existing):
            return False
        return all((folder / rel).is_file() and (folder / rel).read_bytes() == text.encode("utf-8")
                   for rel, text in files.items())
    except OSError:
        return False


def _write_skill(root: Path, name: str, files: object, *,
                 created: list[Path] | None = None) -> list[str]:
    if not isinstance(files, dict) or not isinstance(files.get("SKILL.md"), str):
        raise ExtensionError("A skill needs a SKILL.md.")
    # OpenCode names a skill from its frontmatter, so a different name there would get past the
    # checks on `name`.
    if _frontmatter_name(files["SKILL.md"]) != name:
        raise ExtensionError(f"SKILL.md's frontmatter must say 'name: {name}'.")
    _check_description(name, files["SKILL.md"])
    paths = {_relative(p): text for p, text in files.items()}
    if not all(isinstance(text, str) for text in paths.values()):
        raise ExtensionError("Every skill file is text.")
    folder = root / SLOT / "skills" / name
    try:
        folder.mkdir(parents=True)
    except FileExistsError:
        if _same_skill_files(folder, paths):
            return sorted((SLOT / "skills" / name / rel).as_posix() for rel in paths)
        raise ExtensionError(f"{folder.relative_to(root)} already exists and is not Sage's.")
    if created is not None:
        created.append(folder)
    written = []
    for rel, text in paths.items():
        target = folder / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
        written.append((SLOT / "skills" / name / rel).as_posix())
    return sorted(written)


def add_skills(root: Path, skills: list[dict[str, str]], *, replaces: str = "",
               source: dict | None = None) -> list[dict]:
    """Add every skill one upload or import holds, or none of them."""
    if replaces and len(skills) != 1:
        raise ExtensionError(f"This holds {len(skills)} skills. Only one can replace '{replaces}'.")
    root = Path(root)
    created: list[Path] = []
    added: list[dict] = []
    try:
        for files in skills:
            added.append(add(root, {"kind": "skill", "files": files, "replaces": replaces,
                                    "source": source or {"type": "upload"}}, _created=created))
    except ExtensionError:
        if added:
            with _LOCK:
                for folder in created:
                    shutil.rmtree(folder, ignore_errors=True)
                ids = {entry["id"] for entry in added}
                _write_manifest(root, [e for e in read_manifest(root) if e["id"] not in ids])
        raise
    return added


def upload_files(filename: str, data: bytes) -> dict[str, Callable[[], bytes]]:
    """The files of an uploaded skill `.md`, or of a zip of skill folders, by path."""
    lowered = filename.lower()
    if lowered.endswith(".md"):
        return md_files({PurePosixPath(filename).name: data})
    if not lowered.endswith(".zip"):
        raise ExtensionError("Upload .md files, or a .zip of skill folders.")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise ExtensionError(f"{filename} is not a zip file.") from e
    return {info.filename: (lambda info=info: archive.read(info))
            for info in archive.infolist() if not info.is_dir()}


def md_files(named: dict[str, bytes]) -> dict[str, Callable[[], bytes]]:
    """Loose `.md` files laid out as skill folders. Each one whose frontmatter names it is a skill,
    in a folder of its own; the rest are that skill's files, so they need exactly one skill."""
    skills = [n for n, data in named.items() if _frontmatter_name(data.decode("utf-8", "replace"))]
    if not skills:
        raise ExtensionError("None of these .md files has a name in its frontmatter, so none is a "
                             "skill.")
    rest = [n for n in named if n not in skills]
    if rest and len(skills) > 1:
        raise ExtensionError(f"{', '.join(rest)} could belong to any of {len(skills)} skills. Add "
                             "each skill with its own files, or zip them in skill folders.")
    files = {f"{PurePosixPath(n).stem}/SKILL.md": (lambda n=n: named[n]) for n in skills}
    files.update({f"{PurePosixPath(skills[0]).stem}/{n}": (lambda n=n: named[n]) for n in rest})
    return files


def _host_token(host: str) -> str | None:
    return extract_token(host)


def clone(url: object, dest: str) -> str:
    """Shallow-clone `url` into `dest` and answer the commit it is at.

    A private repo is read with the git credential this container holds for the URL's host, the
    one Domino wired for the Project's checkout. It reaches git through a one-shot credential
    helper and the child's environment, never argv, disk, the manifest or a message."""
    if not isinstance(url, str) or not url.startswith("https://"):
        raise ExtensionError("A git URL starts with https://.")
    host = (urlsplit(url).hostname or "").lower()
    token = _host_token(host) if host else None
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    auth: list[str] = []
    if token:
        env[_CLONE_TOKEN_ENV] = token
        auth = ["-c", "credential.helper=", "-c", f"credential.helper={_CLONE_HELPER}"]
    try:
        subprocess.run(["git", *auth, "clone", "--depth", "1", "--quiet", "--", url, dest],
                       check=True, capture_output=True, text=True, timeout=120, env=env)
    except subprocess.TimeoutExpired as e:
        raise ExtensionError(f"Cloning {url} took longer than two minutes.") from e
    except subprocess.CalledProcessError as e:
        said = (e.stderr or "").strip().splitlines()
        hint = "" if token else (f" Sage holds no git credential for {host} here, so only a "
                                 "public repo there can be read.")
        raise ExtensionError(f"git could not clone {url}: {said[-1] if said else e}.{hint}") from e
    return subprocess.run(["git", "-C", dest, "rev-parse", "HEAD"], capture_output=True,
                          text=True, check=True, env=env).stdout.strip()


@contextmanager
def git_files(url: object) -> Iterator[tuple[dict[str, Callable[[], bytes]], str]]:
    """Clone `url` and yield its files by path, readable while the block runs, with the commit."""
    with tempfile.TemporaryDirectory() as tmp:
        commit = clone(url, tmp)
        base = Path(tmp)
        yield {p.relative_to(base).as_posix(): p.read_bytes for p in sorted(base.rglob("*"))
               if p.is_file() and ".git" not in p.relative_to(base).parts}, commit


def _skill_folders(files: dict[str, Callable[[], bytes]], where: str) -> dict[str, list[str]]:
    """Each folder holding a SKILL.md, in any case, with the paths of the files it owns: that
    SKILL.md first. Anything outside every such folder is not a skill's, and a file in nested ones
    belongs to the deepest."""
    paths = [p for p in files if not p.startswith("__MACOSX/")]
    markers = [p for p in paths if PurePosixPath(p).name.lower() == "skill.md"]
    folders = sorted({str(PurePosixPath(p).parent) for p in markers})
    if not folders:
        raise ExtensionError(f"There is no SKILL.md in {where}.")
    deepest_first = sorted(folders, key=len, reverse=True)
    owned: dict[str, list[str]] = {folder: [] for folder in folders}
    for path in paths:
        folder = next((f for f in deepest_first if f == "." or path.startswith(f + "/")), None)
        if folder:
            owned[folder].append(path)
    for folder in folders:
        mine = [p for p in markers if str(PurePosixPath(p).parent) == folder]
        if len(mine) > 1:
            raise ExtensionError(f"{' and '.join(mine)} are the same file to OpenCode. Keep one.")
        owned[folder].remove(mine[0])
        owned[folder].insert(0, mine[0])
    return owned


def _inside(folder: str, path: str) -> str:
    return path if folder == "." else path[len(folder) + 1:]


def _skill_rel(folder: str, paths: list[str], path: str) -> str:
    """`path`'s place in its skill. The SKILL.md is written as OpenCode spells it, whatever case it
    came in, because OpenCode loads only that spelling."""
    return "SKILL.md" if path == paths[0] else _inside(folder, path)


def _refused(skill_md: str) -> str:
    """What `add` would refuse this SKILL.md with in a Project holding no skill yet, or ''. One
    already in the Project is the picker's to show, from the Project's own list."""
    name = _frontmatter_name(skill_md)
    try:
        _check_name(name or None, [])
        _check_description(name, skill_md)
    except ExtensionError as e:
        return str(e)
    return ""


def found_skills(files: dict[str, Callable[[], bytes]], where: str) -> list[dict]:
    """What `skills_in_files` could add, read from each SKILL.md alone: per folder, the skill's
    name, its description, its files, and why `add` would refuse it, if it would."""
    found = []
    for folder, paths in _skill_folders(files, where).items():
        text = files[paths[0]]().decode("utf-8", "replace")
        found.append({"folder": folder, "name": _frontmatter_name(text),
                      "description": skill_description(text),
                      "files": [_skill_rel(folder, paths, p) for p in paths],
                      "refused": _refused(text)})
    return found


def skills_in_files(files: dict[str, Callable[[], bytes]], where: str,
                    pick: list[str] | None = None) -> tuple[list[dict[str, str]], list[str]]:
    """The skills in the folders `pick` names (every one when it is None), each as a map of its
    files by path inside it, and the paths of the files left out because they are not text."""
    folders = _skill_folders(files, where)
    if pick is not None:
        unknown = sorted(set(pick) - set(folders))
        if unknown:
            raise ExtensionError(f"There is no skill folder {', '.join(unknown)} in {where}.")
        folders = {f: paths for f, paths in folders.items() if f in pick}
        if not folders:
            raise ExtensionError("Pick at least one skill to add.")
    chosen = [(folder, p) for folder, paths in folders.items() for p in paths]
    if len(chosen) > _MAX_SKILL_FILES:
        raise ExtensionError(f"{where} holds more than {_MAX_SKILL_FILES} skill files.")
    skills: dict[str, dict[str, str]] = {folder: {} for folder in folders}
    skipped: list[str] = []
    total = 0
    for folder, path in chosen:
        data = files[path]()
        rel = _skill_rel(folder, folders[folder], path)
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as e:
            if rel == "SKILL.md":
                raise ExtensionError(f"{path} is not a text file.") from e
            skipped.append(path)
            continue
        total += len(data)
        if total > _MAX_SKILL_BYTES:
            raise ExtensionError(f"The skills in {where} are larger than 5 MB.")
        skills[folder][rel] = text
    return list(skills.values()), skipped
