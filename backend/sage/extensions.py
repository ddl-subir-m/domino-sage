"""A Project's own skills, custom tools and MCP servers (ADR-0071).

They live in OpenCode's project slot, `.opencode/` at the root of the Project volume, so OpenCode
loads them itself. `.opencode/sage-extensions.json` is Sage's manifest: what each extension is,
which files it owns, and which of its tools are read-only. The shim reads the manifest through an
`ExtensionCatalog`, never the files.
"""
from __future__ import annotations

import json
import re
import shutil
import threading
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
# The skills Sage ships (`template/skills`, and each stack's `.opencode/skills`).
_BUILTIN_SKILLS = frozenset({"data-table", "investigate-weak-signals"})
_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
_FRONTMATTER_NAME = re.compile(r"\A---\s*\n(?:.*\n)*?name:\s*['\"]?([^'\"\n]*?)['\"]?\s*\n")
_LOCK = threading.Lock()


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
    for entry in entries:
        ext_id, name = entry["id"], entry["name"]
        if entry["kind"] == "skill":
            skills[name] = ext_id
        elif entry["kind"] == "tool":
            tools[name] = ToolOwner(ext_id, bool(entry.get("readOnly")))
            prefixes[name + "_"] = ext_id
        elif entry["kind"] == "mcp":
            prefixes[name + "_"] = ext_id
            for tool, read_only in (entry.get("tools") or {}).items():
                tools[f"{name}_{tool}".lower()] = ToolOwner(ext_id, bool(read_only))
    return ExtensionCatalog(tools, prefixes, skills)


def read_manifest(root: Path) -> list[dict]:
    try:
        body = json.loads((Path(root) / MANIFEST).read_text())
    except (OSError, ValueError):
        return []
    entries = body.get("extensions") if isinstance(body, dict) else None
    return [e for e in entries or [] if isinstance(e, dict) and e.get("kind") in KINDS
            and isinstance(e.get("id"), str) and isinstance(e.get("name"), str)]


def load_catalog(root: Path) -> ExtensionCatalog:
    return catalog_of(read_manifest(root))


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
    if kind == "skill" and name in _BUILTIN_SKILLS:
        raise ExtensionError(f"'{name}' is the name of a skill Sage ships.")
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
    return read_manifest(root)


def add(root: Path, body: dict) -> dict:
    """Write one extension into the project slot and record it. Answers its manifest entry."""
    root = Path(root)
    kind = body.get("kind")
    if kind not in KINDS:
        raise ExtensionError("kind is one of: skill, tool, mcp.")
    with _LOCK:
        entries = read_manifest(root)
        name = _check_name(body.get("name"), kind, entries)
        entry: dict = {"id": f"{kind}:{name}", "kind": kind, "name": name,
                       "source": body.get("source") if isinstance(body.get("source"), dict)
                       else {"type": "upload"},
                       "defaultEnabled": True}
        if kind == "skill":
            entry["files"] = _write_skill(root, name, body.get("files"))
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
    declared = _FRONTMATTER_NAME.match(files["SKILL.md"])
    if not declared or declared.group(1).strip() != name:
        raise ExtensionError(f"SKILL.md's frontmatter must say 'name: {name}'.")
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
    current = _read_mcp_config(root)
    servers = current.setdefault("mcp", {})
    if name in servers:
        raise ExtensionError(f"{MCP_CONFIG} already declares '{name}' and it is not Sage's.")
    servers[name] = config
    _write_json(root / MCP_CONFIG, current)
