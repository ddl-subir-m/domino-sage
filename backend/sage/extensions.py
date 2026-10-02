"""A Project's own skills, custom tools and MCP servers (ADR-0071).

They live in OpenCode's project slot, `.opencode/` at the root of the Project volume, so OpenCode
loads them itself. `.opencode/sage-extensions.json` is Sage's manifest: what each extension is,
which files it owns, and which of its tools are read-only. The shim reads the manifest through an
`ExtensionCatalog`, never the files.
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
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import NamedTuple

from .router.phase_classifier import READ_TOOLS, SHELL_TOOLS, TODO_TOOLS, WEB_TOOLS, WRITE_TOOLS

SLOT = Path(".opencode")
MANIFEST = SLOT / "sage-extensions.json"
MCP_CONFIG = SLOT / "opencode.json"
KINDS = ("skill", "tool", "mcp")
RESERVED_PREFIXES = ("sage-", "sage_")
# OpenCode 1.18.4's own tools, and the custom tools Sage installs globally. A Project file with one
# of these names replaces the built-in for every turn in that Project.
_BUILTIN_TOOLS = frozenset({
    "bash", "read", "write", "edit", "apply_patch", "glob", "grep", "list", "task", "todowrite",
    "todoread", "webfetch", "websearch", "codesearch", "skill", "question", "lsp", "batch",
    "invalid", "live_read", "artifact_write", "delegated_model_call",
}) | READ_TOOLS | WRITE_TOOLS | SHELL_TOOLS | TODO_TOOLS | WEB_TOOLS
_REPO = Path(__file__).resolve().parents[2]
# Where the skills Sage ships live: installed globally, or seeded into each Built App.
_BUILTIN_SKILL_GLOBS = ("template/skills/*/SKILL.md", "template/*/.opencode/skills/*/SKILL.md")
_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
_FRONTMATTER_NAME = re.compile(r"\A---\s*\n(?:.*\n)*?name:\s*['\"]?([^'\"\n]*?)['\"]?\s*\n")
# What one upload or git import may bring in, across every skill it holds.
_MAX_SKILL_BYTES = 5 * 1024 * 1024
_MAX_SKILL_FILES = 200
_LOCK = threading.Lock()
# How OpenCode substitutes a variable from its own environment into config text.
ENV_REF = re.compile(r"\{env:([^}]*)\}")
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class ExtensionError(ValueError):
    """An extension that cannot be added as asked. The message is for the person."""


class ToolOwner(NamedTuple):
    id: str
    read_only: bool


@dataclass(frozen=True)
class ExtensionCatalog:
    """Which offered tool names and skills belong to which extension.

    A custom tool `foo` is offered as `foo`, or `foo_<export>` for a file with several exports. An
    MCP server `bar` has every tool offered as `bar_<tool>`. A name under an owner's prefix that
    the manifest does not list is treated as not read-only.
    """

    tools: dict[str, ToolOwner] = field(default_factory=dict)
    prefixes: dict[str, str] = field(default_factory=dict)
    skills: dict[str, str] = field(default_factory=dict)
    # Built-in skill name -> the id of the Project skill that replaces it while enabled.
    replaced: dict[str, str] = field(default_factory=dict)

    def owner(self, tool_name: str) -> ToolOwner | None:
        name = tool_name.lower()
        if name in self.tools:
            return self.tools[name]
        for prefix, ext_id in self.prefixes.items():
            if name.startswith(prefix):
                return ToolOwner(ext_id, False)
        return None

    def __bool__(self) -> bool:
        return bool(self.tools or self.prefixes or self.skills)


EMPTY = ExtensionCatalog()


def catalog_of(entries: list[dict]) -> ExtensionCatalog:
    tools: dict[str, ToolOwner] = {}
    prefixes: dict[str, str] = {}
    skills: dict[str, str] = {}
    replaced: dict[str, str] = {}
    for entry in entries:
        ext_id, name = entry["id"], entry["name"]
        if entry["kind"] == "skill":
            skills[name] = ext_id
            if isinstance(entry.get("replaces"), str) and entry["replaces"]:
                replaced[entry["replaces"]] = ext_id
        elif entry["kind"] == "tool":
            tools[name] = ToolOwner(ext_id, bool(entry.get("readOnly")))
            prefixes[name + "_"] = ext_id
        elif entry["kind"] == "mcp":
            prefixes[name + "_"] = ext_id
            for tool, read_only in (entry.get("tools") or {}).items():
                tools[f"{name}_{tool}".lower()] = ToolOwner(ext_id, bool(read_only))
    return ExtensionCatalog(tools, prefixes, skills, replaced)


def skill_description(text: str) -> str:
    """The frontmatter `description` of a SKILL.md's text, or "" when there is none.

    The one field OpenCode filters on. Read with a regex rather than a YAML parser because there is
    no YAML dependency declared in `backend/pyproject.toml`. A block indicator (`>`/`|`) counts as
    present, as do the indented lines of a plain multi-line scalar; CRLF and a closing `---` with
    nothing after it both read too.
    """
    front = re.match(r"^---\r?\n(.*?)\r?\n---[ \t]*(\r?\n|\Z)", text, re.DOTALL)
    if not front:
        return ""
    found = re.search(r"^description:[ \t]*(.*(?:\n[ \t]+\S.*)*)$", front.group(1), re.MULTILINE)
    return found.group(1).strip().strip("\"'") if found else ""


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
    return catalog_of([e for e in list_extensions(root) if not e.get("shadowed")])


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


def _check_name(name: object, kind: str, entries: list[dict]) -> str:
    if not isinstance(name, str) or not _NAME.match(name):
        raise ExtensionError("A name is lowercase letters, digits, '-' and '_', starting with a "
                             "letter or digit, at most 63 characters.")
    if name.startswith(RESERVED_PREFIXES):
        raise ExtensionError(f"'{name}' starts with 'sage-' or 'sage_', which Sage keeps for its own.")
    if kind != "skill" and name in _BUILTIN_TOOLS:
        raise ExtensionError(f"'{name}' is the name of a built-in tool.")
    if kind == "skill" and name in builtin_skills():
        raise ExtensionError(f"'{name}' is the name of a skill Sage ships. Rename yours, or keep "
                             f"the name you like and choose 'Replaces {name}'.")
    for entry in entries:
        other = entry["name"]
        if entry["kind"] == kind == "skill" and other == name:
            raise ExtensionError(f"This Project already has a skill called '{name}'.")
        # Tools and MCP servers share one namespace: both are matched by name and `<name>_`.
        if kind != "skill" and entry["kind"] != "skill" and (
                other == name or name.startswith(other + "_") or other.startswith(name + "_")):
            raise ExtensionError(f"'{name}' clashes with the {entry['kind']} '{other}' already here.")
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


def add(root: Path, body: dict) -> dict:
    """Write one extension into the project slot and record it. Answers its manifest entry."""
    root = Path(root)
    kind = body.get("kind")
    if kind not in KINDS:
        raise ExtensionError("kind is one of: skill, tool, mcp.")
    name = body.get("name")
    files = body.get("files")
    if kind == "skill" and name is None and isinstance(files, dict) \
            and isinstance(files.get("SKILL.md"), str):
        name = _frontmatter_name(files["SKILL.md"]) or None
    with _LOCK:
        entries = read_manifest(root)
        name = _check_name(name, kind, entries)
        entry: dict = {"id": f"{kind}:{name}", "kind": kind, "name": name,
                       "source": body.get("source") if isinstance(body.get("source"), dict)
                       else {"type": "upload"},
                       "defaultEnabled": True}
        replaces = body.get("replaces") or ""
        if replaces:
            if kind != "skill" or replaces not in builtin_skills():
                raise ExtensionError(f"'{replaces}' is not a skill Sage ships.")
            holder = next((e for e in entries if e.get("replaces") == replaces), None)
            if holder:
                raise ExtensionError(f"'{holder['name']}' already replaces '{replaces}'.")
            entry["replaces"] = replaces
        if kind == "skill":
            entry["files"] = _write_skill(root, name, files)
        elif kind == "tool":
            entry["files"] = _write_tool(root, name, body.get("code"))
            entry["readOnly"] = bool(body.get("readOnly"))
        else:
            tools = body.get("tools") or {}
            if not isinstance(tools, dict):
                raise ExtensionError("tools maps each MCP tool name to whether it is read-only.")
            _write_mcp(root, name, body.get("config"))
            entry["files"] = [MCP_CONFIG.as_posix()]
            entry["tools"] = {str(k): bool(v) for k, v in tools.items()}
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
        if entry["kind"] == "skill":
            shutil.rmtree(root / SLOT / "skills" / entry["name"], ignore_errors=True)
        elif entry["kind"] == "tool":
            for rel in entry.get("files") or []:
                (root / _relative(rel)).unlink(missing_ok=True)
        else:
            config = _read_mcp_config(root)
            (config.get("mcp") or {}).pop(entry["name"], None)
            if not config.get("mcp") and set(config) <= {"$schema", "mcp"}:
                (root / MCP_CONFIG).unlink(missing_ok=True)
            else:
                _write_json(root / MCP_CONFIG, config)
        _write_manifest(root, [e for e in entries if e["id"] != ext_id])
        return True


def _write_skill(root: Path, name: str, files: object) -> list[str]:
    if not isinstance(files, dict) or not isinstance(files.get("SKILL.md"), str):
        raise ExtensionError("A skill needs a SKILL.md.")
    # OpenCode names a skill from its frontmatter, so a different name there would get past the
    # checks on `name`.
    if _frontmatter_name(files["SKILL.md"]) != name:
        raise ExtensionError(f"SKILL.md's frontmatter must say 'name: {name}'.")
    if not skill_description(files["SKILL.md"]):
        raise ExtensionError(f"'{name}' has no description in its SKILL.md frontmatter. OpenCode "
                             "loads a skill without one and never offers it to the model.")
    paths = {_relative(p): text for p, text in files.items()}
    if not all(isinstance(text, str) for text in paths.values()):
        raise ExtensionError("Every skill file is text.")
    folder = root / SLOT / "skills" / name
    if folder.exists():
        raise ExtensionError(f"{folder.relative_to(root)} already exists and is not Sage's.")
    written = []
    for rel, text in paths.items():
        target = folder / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
        written.append((SLOT / "skills" / name / rel).as_posix())
    return sorted(written)


def _write_tool(root: Path, name: str, source: object) -> list[str]:
    if not isinstance(source, str) or not source.strip():
        raise ExtensionError("A tool needs its TypeScript source.")
    rel = SLOT / "tools" / f"{name}.ts"
    if (root / rel).exists():
        raise ExtensionError(f"{rel} already exists and is not Sage's.")
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_text(source)
    return [rel.as_posix()]


def _read_mcp_config(root: Path) -> dict:
    try:
        config = json.loads((root / MCP_CONFIG).read_text())
    except FileNotFoundError:
        return {"$schema": "https://opencode.ai/config.json"}
    except ValueError as e:
        raise ExtensionError(f"{MCP_CONFIG} is not valid JSON, so Sage will not rewrite it.") from e
    return config if isinstance(config, dict) else {}


def _write_mcp(root: Path, name: str, config: object) -> None:
    if not isinstance(config, dict) or config.get("type") not in ("remote", "local"):
        raise ExtensionError("An MCP server's config has type 'remote' (with url) or 'local' "
                             "(with command).")
    if config["type"] == "remote" and not isinstance(config.get("url"), str):
        raise ExtensionError("A remote MCP server needs a url.")
    if config["type"] == "local" and not (
            isinstance(config.get("command"), list) and config["command"]
            and all(isinstance(a, str) for a in config["command"])):
        raise ExtensionError("A local MCP server needs a command, as a list of strings.")
    for key in ("headers", "environment"):
        values = config.get(key)
        if values is not None and not (isinstance(values, dict) and all(
                isinstance(k, str) and isinstance(v, str) for k, v in values.items())):
            raise ExtensionError(f"An MCP server's {key} maps names to text.")
    bad = [name for name in variables(config) if not _ENV_NAME.match(name)]
    if bad:
        raise ExtensionError(f"'{bad[0]}' is not a variable name: letters, digits and '_'.")
    current = _read_mcp_config(root)
    servers = current.setdefault("mcp", {})
    if name in servers:
        raise ExtensionError(f"{MCP_CONFIG} already declares '{name}' and it is not Sage's.")
    servers[name] = config
    _write_json(root / MCP_CONFIG, current)


def variables(value: object) -> list[str]:
    """Every `{env:VAR}` name `value` references, once each, in order."""
    if isinstance(value, str):
        return list(dict.fromkeys(ENV_REF.findall(value)))
    items = value.values() if isinstance(value, dict) else value if isinstance(value, list) else []
    return list(dict.fromkeys(name for item in items for name in variables(item)))


def unset_variables(config: object) -> list[str]:
    """The variables `config` names that this process's environment, which OpenCode inherits,
    does not set. OpenCode substitutes nothing for one and loads the server anyway."""
    return [name for name in variables(config) if name not in os.environ]


def mcp_servers(root: Path) -> dict:
    """The `mcp` block of the project slot's config, as written."""
    servers = _read_mcp_config(Path(root)).get("mcp")
    return servers if isinstance(servers, dict) else {}


def _update_mcp_tools(root: Path, ext_id: str, change: Callable[[dict], dict]) -> dict:
    root = Path(root)
    with _LOCK:
        entries = read_manifest(root)
        entry = next((e for e in entries if e["id"] == ext_id and e["kind"] == "mcp"), None)
        if entry is None:
            raise KeyError(ext_id)
        entry["tools"] = change(dict(entry.get("tools") or {}))
        _write_manifest(root, entries)
        return entry


def set_tool_read_only(root: Path, ext_id: str, tool: str, read_only: bool) -> dict:
    """The panel's override of one MCP tool's mark. Raises KeyError for an unknown server or tool."""
    def change(tools: dict) -> dict:
        if tool not in tools:
            raise KeyError(tool)
        return {**tools, tool: bool(read_only)}
    return _update_mcp_tools(root, ext_id, change)


def replace_mcp_tools(root: Path, ext_id: str, listed: dict[str, bool]) -> dict:
    """The tools a server listed when read. One already known keeps its mark, which may be the
    person's override; one the server no longer lists is dropped."""
    return _update_mcp_tools(root, ext_id, lambda known: {
        name: bool(known.get(name, read_only)) for name, read_only in listed.items()})


def add_skills(root: Path, skills: list[dict[str, str]], *, replaces: str = "",
               source: dict | None = None) -> list[dict]:
    """Add every skill one upload or import holds, or none of them."""
    if replaces and len(skills) != 1:
        raise ExtensionError(f"This holds {len(skills)} skills. Only one can replace '{replaces}'.")
    added: list[dict] = []
    try:
        for files in skills:
            added.append(add(root, {"kind": "skill", "files": files, "replaces": replaces,
                                    "source": source or {"type": "upload"}}))
    except ExtensionError:
        for entry in added:
            remove(root, entry["id"])
        raise
    return added


def skills_in_upload(filename: str, data: bytes) -> list[dict[str, str]]:
    """The skills in an uploaded `SKILL.md`, or in a zip of one or more skill folders."""
    lowered = filename.lower()
    if lowered.endswith(".md"):
        return skills_in_files({"SKILL.md": lambda: data}, filename)
    if not lowered.endswith(".zip"):
        raise ExtensionError("Upload a SKILL.md, or a .zip of a skill folder.")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise ExtensionError(f"{filename} is not a zip file.") from e
    return skills_in_files({info.filename: (lambda info=info: archive.read(info))
                           for info in archive.infolist() if not info.is_dir()}, filename)


def skills_from_git(url: object) -> tuple[list[dict[str, str]], str]:
    """Clone `url` and answer the skill folders in it, with the commit they were read at."""
    if not isinstance(url, str) or not url.startswith("https://"):
        raise ExtensionError("A git URL starts with https://.")
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    with tempfile.TemporaryDirectory() as tmp:
        try:
            subprocess.run(["git", "clone", "--depth", "1", "--quiet", "--", url, tmp],
                           check=True, capture_output=True, text=True, timeout=120, env=env)
        except subprocess.TimeoutExpired as e:
            raise ExtensionError(f"Cloning {url} took longer than two minutes.") from e
        except subprocess.CalledProcessError as e:
            said = (e.stderr or "").strip().splitlines()
            raise ExtensionError(f"git could not clone {url}: {said[-1] if said else e}") from e
        commit = subprocess.run(["git", "-C", tmp, "rev-parse", "HEAD"], capture_output=True,
                                text=True, check=True, env=env).stdout.strip()
        base = Path(tmp)
        files = {p.relative_to(base).as_posix(): p.read_bytes for p in sorted(base.rglob("*"))
                 if p.is_file() and ".git" not in p.relative_to(base).parts}
        return skills_in_files(files, url), commit


def skills_in_files(files: dict[str, Callable[[], bytes]], where: str) -> list[dict[str, str]]:
    """Each folder holding a SKILL.md, as a map of its files by path inside it."""
    paths = [p for p in files if not p.startswith("__MACOSX/")]
    folders = sorted({str(PurePosixPath(p).parent) for p in paths
                      if PurePosixPath(p).name == "SKILL.md"})
    if not folders:
        raise ExtensionError(f"There is no SKILL.md in {where}.")
    deepest_first = sorted(folders, key=len, reverse=True)

    def owner(path: str) -> str | None:
        return next((f for f in deepest_first if f == "." or path.startswith(f + "/")), None)

    chosen = [(folder, p) for p in paths if (folder := owner(p))]
    if len(chosen) > _MAX_SKILL_FILES:
        raise ExtensionError(f"{where} holds more than {_MAX_SKILL_FILES} skill files.")
    skills: dict[str, dict[str, str]] = {folder: {} for folder in folders}
    total = 0
    for folder, path in chosen:
        data = files[path]()
        total += len(data)
        if total > _MAX_SKILL_BYTES:
            raise ExtensionError(f"The skills in {where} are larger than 5 MB.")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as e:
            raise ExtensionError(f"{path} is not a text file. A skill's files are text.") from e
        rel = path if folder == "." else path[len(folder) + 1:]
        skills[folder][rel] = text
    return list(skills.values())
