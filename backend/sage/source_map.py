"""What a Built App's own files define and how they connect, read off disk (#700).

Two readers, one listing. The Build prompt's source note (`build_source_note`) is a flat list of
paths and the names each file defines, sent on every Build turn. `lookup` answers the
`sage_source_map` tool on demand: definitions with lines, the relationships the stack makes
literal — a `window.app.Name`, a `<script src>`, a FastAPI route, a named-query call — and, where
it cannot resolve one, an `unknown` entry rather than a silence. Both start from `source_paths`, so
the tool can never be asked about a file the listing does not know, nor the listing disagree with
the rule that decides whether a prompt NAMED a file.

No parser beyond the standard library: `ast` for Python, `html.parser` for the page, the regex
`feedback/runner.py` already checks window members with, and the query catalog through the
template's own loader. A React import is reported as unknown coverage — resolving it needs the
TypeScript compiler, which v1 does not carry.

Never stale: every call rereads the app's files, and the only cache is keyed on a file's CONTENT
hash, so an edit that keeps a file's size and mtime still misses it.
"""
from __future__ import annotations

import ast
import hashlib
import json
import re
import threading
from collections import OrderedDict
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from .feedback.runner import _WINDOW_MEMBER_RE
from .resources.builtapp import serve_module
from .workspace.stack import stack_of

# How many source paths the Build prompt lists, and how many names it gives per listed file. The
# path cap is long-standing (a listing that can't dominate the request); the name cap is what stops
# one generated module of 500 exports from crowding out the file the request is actually about.
_SOURCE_PATHS_MAX = 60
_NAMES_PER_FILE = 8
_NAMES_MAX_FILE_BYTES = 200_000
# What a file DEFINES, matched on line starts only. Deliberately not a parser: this runs on every
# Build turn over up to 60 files, the answer is a hint for choosing which file to open, and a miss
# costs the model nothing it did not already have. `name` is the one group every pattern carries.
_NAME_PATTERNS = {
    ".py": re.compile(r"^(?:async\s+def|def|class)\s+(?P<name>\w+)"),
    **{suffix: re.compile(
        r"^(?:export\s+(?:default\s+)?)?(?:async\s+)?(?:function|class|const|let|var)\s+"
        r"(?P<name>\w+)")
       for suffix in (".js", ".jsx", ".ts", ".tsx", ".mjs")},
}


def source_paths(root: Path) -> list[str]:
    """The app's own source files, relative and sorted. Which paths those are is its stack's to
    say (#490); a vendored bundle is not one of them.

    Its own function because two callers need the same answer for different reasons — the
    listing sent to the model, and the rule that decides whether a prompt NAMED one of these
    files. A rule keyed on a second, slightly different glob would disagree with the listing
    the model was given, which is the one thing that must not happen here.
    """
    # `ValueError` is an app with no stack to say which files are its own (#503) — unborn, or
    # one whose files argue — and its map is empty, which is what `scope.py` answers for the
    # same glob.
    try:
        kind = stack_of(root)
        return sorted({p.relative_to(root).as_posix() for glob in kind.source_globs
                       for p in root.glob(glob)
                       if p.is_file() and not any(part.startswith(".")
                                                  for part in p.relative_to(root).parts)
                       and not p.relative_to(root).as_posix().startswith(kind.vendored)})
    except (OSError, ValueError):
        return []


def top_level_names(root: Path, paths: list[str]) -> dict[str, list[str]]:
    """What each source file DEFINES, by name, read off the line starts.

    Paths alone did not stop the re-orientation they were added for. Measured 2026-09-11 and
    again in #496: a one-line change to a built app spent two whole round trips on `read`,
    `glob`, `read` x5 before its single edit, with this listing already in the prompt — because
    a list of paths cannot say which file holds the chart. A name can.

    Names only, never a value: the point is to let the model pick the file, and a `const` on
    the right-hand side is the person's data. Bounded the same way the listing is, and by a
    per-file cap, so a generated 500-export module cannot crowd out the rest.
    """
    out: dict[str, list[str]] = {}
    for rel in paths:
        pattern = _NAME_PATTERNS.get(Path(rel).suffix)
        if pattern is None:
            continue
        try:
            p = root / rel
            if p.stat().st_size > _NAMES_MAX_FILE_BYTES:
                continue
            text = p.read_text(errors="replace")
        except OSError:
            continue
        names: list[str] = []
        for line in text.splitlines():
            m = pattern.match(line)
            if m and (name := m.group("name")) not in names:
                names.append(name)
                if len(names) == _NAMES_PER_FILE:
                    break
        if names:
            out[rel] = names
    return out


def build_source_note(root: Path) -> str:
    """Supply exact source paths and what each file defines, without sending any source.

    Built from disk at every call, and the call sites are what keep it true: the first send of a
    turn (so it carries whatever the last turn wrote, and anything edited in the workspace by
    hand), and the broken-call retry, which mints a NEW session that heard nothing else — and
    which used to restore the string built before the failed attempt, so a retry after a
    partial write was told a map that was already wrong. It deliberately does NOT ride a nudge:
    a nudge talks to the session that made the edits, and re-sending the map there would put a
    user-role message in the transcript, which is a turn boundary to the rescue scorer.
    """
    paths = source_paths(root)
    if not paths:
        return ""
    shown = paths[:_SOURCE_PATHS_MAX]
    note = ("Existing source paths (JSON array, relative to the app directory):\n"
            + json.dumps(shown)
            + (f"\nListing limited to the first {_SOURCE_PATHS_MAX} paths."
               if len(paths) > _SOURCE_PATHS_MAX else ""))
    if names := top_level_names(root, shown):
        note += ("\nTop-level names in each of those files (JSON object, path -> names):\n"
                 + json.dumps(names))
    return note + ("\nOpen the relevant files together before editing — the ones whose names "
                   "the request touches, in ONE message. "
                   "Search only if the needed path is not listed.")


# ---- the map the tool answers from ------------------------------------------------------------

TOOL_NAME = "sage_source_map"
SCHEMA_VERSION = 1
# The whole answer, in UTF-8 bytes. Big enough for the file a request is about and its neighbours,
# small enough that a map can never cost more context than the reads it saves.
RESPONSE_BUDGET = 8192
_DEFINITIONS_PER_FILE = 40
_RELATIONSHIPS_PER_FILE = 40
_SIGNATURE_MAX = 120
_PATHS_ASKED_MAX = 20
_ASKED_CHARS_MAX = 200
_QUERIES_REL = ".sage/queries.json"
_JS_SUFFIXES = (".js", ".jsx", ".ts", ".tsx", ".mjs")
_MAPPED_SUFFIXES = (".py", ".html", *_JS_SUFFIXES)
_ROUTE_METHODS = frozenset({"get", "post", "put", "patch", "delete", "head", "options"})
_COMPUTED_MEMBER_RE = re.compile(r"\bwindow\.[A-Za-z_$][\w$]*\s*\[")
_QUERY_CALL_RE = re.compile(r"(?<!function )\b(?:useQuery|runQuery)\(\s*(?P<arg>[^\s)])")
_QUERY_LITERAL_RE = re.compile(r"""(["'`])(?P<name>[^"'`\\]+)\1""")
_IMPORT_RE = re.compile(r"^\s*(?:import\b|export\b[^\n]*\bfrom\s*[\"'])")

# Per-file analyses keyed on (suffix, sha256 of the bytes): never on a path, size or mtime, so the
# only way to be served an old answer is to have the old bytes. Bounded; the oldest goes first.
_CACHE_MAX = 4096
_cache: OrderedDict[tuple[str, str], dict] = OrderedDict()
_cache_lock = threading.Lock()


def _line_of(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """`def name(a, *, b)`: parameter names and markers only, never a default or an annotation —
    a default is a value, and a value is the person's."""
    a = node.args
    parts = [p.arg for p in a.posonlyargs]
    if a.posonlyargs:
        parts.append("/")
    parts += [p.arg for p in a.args]
    if a.vararg:
        parts.append(f"*{a.vararg.arg}")
    elif a.kwonlyargs:
        parts.append("*")
    parts += [p.arg for p in a.kwonlyargs]
    if a.kwarg:
        parts.append(f"**{a.kwarg.arg}")
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    return f"{prefix} {node.name}({', '.join(parts)})"[:_SIGNATURE_MAX]


def _analyze_python(text: str) -> dict:
    defs, rels, unknown = [], [], []
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError) as e:
        return {"definitions": [], "relationships": [],
                "unknown": [{"line": getattr(e, "lineno", None) or 1, "reason": "does not parse"}]}
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            defs.append({"name": node.name, "kind": "class", "line": node.lineno})
            continue
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        defs.append({"name": node.name, "kind": "function", "line": node.lineno,
                     "signature": _signature(node)})
        for deco in node.decorator_list:
            if not (isinstance(deco, ast.Call) and isinstance(deco.func, ast.Attribute)
                    and deco.func.attr in _ROUTE_METHODS):
                continue
            first = deco.args[0] if deco.args else None
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                rels.append({"kind": "route", "to": f"{deco.func.attr.upper()} {first.value}",
                             "name": node.name, "line": deco.lineno})
            else:
                unknown.append({"line": deco.lineno, "reason": "route path is not a literal"})
    return {"definitions": defs, "relationships": rels, "unknown": unknown}


def _analyze_js(text: str, suffix: str) -> dict:
    defs, rels, unknown = [], [], []
    defined: set[str] = set()
    for m in _WINDOW_MEMBER_RE.finditer(text):
        if m[3]:
            name = f"window.{m[1]}.{m[2]}"
            if name not in defined:
                defined.add(name)
                defs.append({"name": name, "kind": "window", "line": _line_of(text, m.start())})
    pattern = _NAME_PATTERNS[suffix]
    seen: set[str] = set()
    for number, line in enumerate(text.splitlines(), 1):
        if (m := pattern.match(line)) and (name := m.group("name")) not in seen:
            seen.add(name)
            defs.append({"name": name, "kind": "name", "line": number})
        if _IMPORT_RE.match(line):
            unknown.append({"line": number, "reason": "import"})
    referenced: set[str] = set()
    for m in _WINDOW_MEMBER_RE.finditer(text):
        name = f"window.{m[1]}.{m[2]}"
        if not m[3] and name not in defined and name not in referenced:
            referenced.add(name)
            rels.append({"kind": "references", "to": name, "line": _line_of(text, m.start())})
    for m in _COMPUTED_MEMBER_RE.finditer(text):
        unknown.append({"line": _line_of(text, m.start()), "reason": "computed window member"})
    for m in _QUERY_CALL_RE.finditer(text):
        line = _line_of(text, m.start())
        literal = _QUERY_LITERAL_RE.match(text, m.start("arg"))
        if literal:
            rels.append({"kind": "calls_query", "to": f"query:{literal['name']}", "line": line})
        else:
            unknown.append({"line": line, "reason": "query name is not a literal"})
    unknown.sort(key=lambda u: u["line"])
    return {"definitions": defs, "relationships": rels, "unknown": unknown}


class _Scripts(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.found: list[tuple[str, int]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "script" and (src := dict(attrs).get("src")):
            self.found.append((src, self.getpos()[0]))


def _analyze_html(text: str) -> dict:
    parser = _Scripts()
    try:
        parser.feed(text)
        parser.close()
    except Exception:
        return {"definitions": [], "relationships": [],
                "unknown": [{"line": 1, "reason": "does not parse"}]}
    rels = []
    for order, (src, line) in enumerate(parser.found, 1):
        rels.append({"kind": "loads", "to": src, "order": order, "line": line})
    return {"definitions": [], "relationships": rels, "unknown": []}


def _analysis(suffix: str, data: bytes) -> dict:
    key = (suffix, hashlib.sha256(data).hexdigest())
    with _cache_lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key]
    text = data.decode(errors="replace")
    if suffix == ".py":
        out = _analyze_python(text)
    elif suffix == ".html":
        out = _analyze_html(text)
    else:
        out = _analyze_js(text, suffix)
    with _cache_lock:
        _cache[key] = out
        while len(_cache) > _CACHE_MAX:
            _cache.popitem(last=False)
    return out


def _local_script(src: str) -> str | None:
    """A `<script src>` as an app-relative path, or None for one served from elsewhere."""
    if "://" in src or src.startswith("//"):
        return None
    src = src.split("?", 1)[0].split("#", 1)[0]
    while src.startswith("./"):
        src = src[2:]
    return src.lstrip("/") or None


def _queries_entry(root: Path, kind: Any) -> dict | None:
    """The named-query catalog as one file of definitions — names and parameter types, never SQL."""
    path = root / _QUERIES_REL
    if path.is_symlink() or not path.is_file():
        return None
    module = serve_module(kind.template_dir)
    if module is None:
        return None
    data = path.read_bytes()
    text = data.decode(errors="replace")
    defs = []
    for name, query in module.load_queries(root).items():
        m = re.search(r'"name"\s*:\s*' + re.escape(json.dumps(name)), text)
        defs.append({"name": name, "kind": "query", "line": _line_of(text, m.start()) if m else 1,
                     "params": [{"name": p.name, "type": p.type} for p in query.params]})
    return {"path": _QUERIES_REL, "sha": hashlib.sha256(data).hexdigest(),
            "definitions": defs, "relationships": [], "unknown": []}


def _build_map(root: Path) -> list[dict]:
    """Every mapped file of the app, freshly read. A file that resolves outside the app — a symlink
    into another app — is not this app's, whatever its name says."""
    try:
        kind = stack_of(root)
        real_root = root.resolve()
    except (OSError, ValueError):
        return []
    sage_owned = set(kind.owned_sources) | set(kind.deploy_files)
    entries = []
    for rel in source_paths(root):
        suffix = Path(rel).suffix
        if suffix not in _MAPPED_SUFFIXES or rel in sage_owned:
            continue
        path = root / rel
        try:
            if not path.resolve().is_relative_to(real_root):
                continue
            if path.stat().st_size > _NAMES_MAX_FILE_BYTES:
                entries.append({"path": rel, "sha": "", "definitions": [], "relationships": [],
                                "unknown": [{"line": 1, "reason": "too large to map"}]})
                continue
            data = path.read_bytes()
        except OSError:
            continue
        found = _analysis(suffix, data)
        rels = found["relationships"]
        if suffix == ".html":
            rels = []
            for r in found["relationships"]:
                target = _local_script(r["to"])
                if target and not target.startswith(kind.vendored):
                    rels.append({**r, "to": target})
        entries.append({"path": rel, "sha": hashlib.sha256(data).hexdigest(),
                        "definitions": found["definitions"], "relationships": rels,
                        "unknown": found["unknown"]})
    try:
        if queries := _queries_entry(root, kind):
            entries.append(queries)
    except OSError:
        pass
    return entries


def _symbol_matches(definition_name: str, symbol: str) -> bool:
    return definition_name == symbol or definition_name.endswith("." + symbol)


def _rank(entries: list[dict], paths: list[str], symbol: str) -> tuple[list[dict], list[str]]:
    """The files to answer with, most relevant first, and the asked paths no file answered."""
    by_path = {e["path"]: e for e in entries}
    if not paths and not symbol:
        return sorted(entries, key=lambda e: e["path"]), []
    unmatched = [p for p in paths if p not in by_path]
    first = [by_path[p] for p in dict.fromkeys(paths) if p in by_path]
    if symbol:
        first += [e for e in entries
                  if any(_symbol_matches(d["name"], symbol) for d in e["definitions"])]
    first = list({e["path"]: e for e in first}.values())
    chosen = {e["path"] for e in first}
    names = {d["name"] for e in first for d in e["definitions"]}
    names |= {f"query:{d['name']}" for e in first for d in e["definitions"] if d["kind"] == "query"}
    targets = {r["to"] for e in first for r in e["relationships"]}
    hop = []
    for e in sorted(entries, key=lambda e: e["path"]):
        if e["path"] in chosen:
            continue
        defines = {d["name"] for d in e["definitions"]}
        defines |= {f"query:{d['name']}" for d in e["definitions"] if d["kind"] == "query"}
        if (defines & targets or e["path"] in targets
                or any(r["to"] in names or r["to"] in chosen for r in e["relationships"])):
            hop.append(e)
    return first + hop, unmatched


def lookup(root: Path, args: dict) -> str:
    """The map's answer to one tool call, as JSON inside `RESPONSE_BUDGET` bytes.

    `paths` must be paths the app's own map holds, matched exactly — a path that climbs out, a
    dotfile, a data file or a symlink into another app is simply not in it, and comes back in
    `unmatched` with nothing else. `omittedCount` is how many ranked files did not fit.
    """
    raw_paths = args.get("paths")
    paths = [str(p)[:_ASKED_CHARS_MAX] for p in raw_paths[:_PATHS_ASKED_MAX]] \
        if isinstance(raw_paths, list) else []
    symbol = str(args.get("symbol") or "").strip()[:_ASKED_CHARS_MAX]
    entries = _build_map(root)
    digest = hashlib.sha256("\n".join(
        f"{e['path']}\0{e['sha']}" for e in sorted(entries, key=lambda e: e["path"])
    ).encode()).hexdigest()
    ranked, unmatched = _rank(entries, paths, symbol)

    out: dict[str, Any] = {"schemaVersion": SCHEMA_VERSION, "sourceDigest": digest, "files": [],
                           "relationships": [], "coverage": {"status": "complete", "unknown": []},
                           "omittedCount": 0, "unmatched": unmatched}

    def size() -> int:
        return len(json.dumps(out, ensure_ascii=False, separators=(",", ":")).encode())

    for i, e in enumerate(ranked):
        defs = e["definitions"][:_DEFINITIONS_PER_FILE]
        rels = [{"kind": r["kind"], "from": e["path"],
                 **{k: v for k, v in r.items() if k != "kind"}}
                for r in e["relationships"][:_RELATIONSHIPS_PER_FILE]]
        unknown = [{"path": e["path"], **u} for u in e["unknown"][:_RELATIONSHIPS_PER_FILE]]
        out["files"].append({"path": e["path"], "hash": e["sha"][:16], "definitions": defs})
        out["relationships"] += rels
        out["coverage"]["unknown"] += unknown
        out["omittedCount"] = len(ranked) - i - 1
        out["coverage"]["status"] = "unknown" if out["coverage"]["unknown"] else "complete"
        if size() > RESPONSE_BUDGET:
            out["files"].pop()
            del out["relationships"][len(out["relationships"]) - len(rels):]
            del out["coverage"]["unknown"][len(out["coverage"]["unknown"]) - len(unknown):]
            out["omittedCount"] = len(ranked) - i
            out["coverage"]["status"] = "unknown" if out["coverage"]["unknown"] else "complete"
            break
    return json.dumps(out, ensure_ascii=False, separators=(",", ":"))


# ---- the tool's framing -----------------------------------------------------------------------


def handle(message: dict, *, run) -> dict | None:
    """Answer one JSON-RPC `tools/call` from `sage_source_map.ts`. `None` for a notification.

    `run(arguments)` returns the text the model sees; a refusal is ordinary text. Anything it
    raises comes back as a sentence that sends the model to the reads it would have made anyway.
    """
    if not isinstance(message, dict):
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid Request"}}
    mid = message.get("id")
    if mid is None:
        return None
    if message.get("method") != "tools/call":
        return {"jsonrpc": "2.0", "id": mid,
                "error": {"code": -32601, "message": f"Method not found: {message.get('method')}"}}
    params = message.get("params") or {}
    if params.get("name") != TOOL_NAME:
        return {"jsonrpc": "2.0", "id": mid,
                "error": {"code": -32602, "message": f"No tool named {params.get('name')}"}}
    args = params.get("arguments")
    if not isinstance(args, dict):
        args = {}
    try:
        text = run(args)
    except Exception as e:
        text = (f"The source map could not be read ({type(e).__name__}). "
                "Read and search the app's files instead.")
        return {"jsonrpc": "2.0", "id": mid,
                "result": {"content": [{"type": "text", "text": text}], "isError": True}}
    return {"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": text}]}}
