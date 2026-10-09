"""Feedback runner (SPEC C10, PLAN 5.1, AC11).

After an agent edit, check the workspace and turn the result into a structured report the agent
can consume on its next turn. For a react-vite app the check is the typecheck (`tsc --noEmit`): the
fast, high-value signal that catches most of what small models get wrong (types, missing imports,
bad JSX) without a full build. For a fastapi-antd app (#490) there is nothing to type: the check
compiles every `.py` and syntax-checks every `.js` the page loads, which catches the file that
would have failed to import or to parse before a viewer does. On both stacks oxlint then reads the
app's own scripts for what parses and still crashes — a hook after an early return (#679). Browser-
console capture is a Phase-1 add (needs a headless view of the preview).

Deep module, narrow interface: `FeedbackRunner.check(workspace) -> FeedbackReport`. Which check
runs is the workspace's stack's to say; the parsers are pure and unit-tested.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from ..workspace.stack import FASTAPI_ANTD, REACT_VITE, stack_of

# tsc line: "src/App.tsx(12,5): error TS2304: Cannot find name 'foo'."
_TSC_RE = re.compile(r"^(?P<file>[^(]+)\((?P<line>\d+),(?P<col>\d+)\):\s+error\s+(?P<code>TS\d+):\s+(?P<msg>.*)$")
# py_compile: a `File "app.py", line 12` line, then the source, a caret, and the error line.
_PY_FILE_RE = re.compile(r'^\s*File "(?P<file>[^"]+)", line (?P<line>\d+)')
_PY_ERR_RE = re.compile(r"^(?P<code>\w*Error):\s*(?P<msg>.*)$")
# node --check: the path and line on one line, then the source, a caret, and `SyntaxError: msg`.
_NODE_FILE_RE = re.compile(r"^(?P<file>.+?):(?P<line>\d+)$")
# Sage's own scripts and the vendored bundles are not the agent's to fix, and a bundle is one
# 1.2 MB line that `node --check` would spend a second on for nothing.
_JS_SKIP = ("static/vendor", "static/sage")
# `window.app.AskReview = …` defines a name; any other `window.app.AskReview` reads it.
_WINDOW_MEMBER_RE = re.compile(r"\bwindow\.([A-Za-z_$][\w$]*)\.([A-Za-z_$][\w$]*)(\s*=(?!=))?")
# `window.dealDesk = { score }` defines every `window.dealDesk.*`; `window.app = window.app || {}`,
# which every component file opens with, defines nothing.
_WINDOW_NAMESPACE_RE = re.compile(r"\bwindow\.([A-Za-z_$][\w$]*)\s*=(?!=)\s*(?!window\.\1\b)")
_SCRIPT_SRC_RE = re.compile(r"""<script\b[^>]*\bsrc\s*=\s*["']([^"'?#]+)""", re.IGNORECASE)
# What a plain script puts on the page for the others (#691): an unindented declaration (one
# inside a wrapper function is that function's), or a `window.x =` anywhere.
_SCRIPT_GLOBAL_RE = re.compile(
    r"^(?:(?:async\s+)?function\s*\*?|const|let|var|class)\s+([A-Za-z_$][\w$]*)"
    r"|\bwindow\.([A-Za-z_$][\w$]*)\s*=(?!=)", re.MULTILINE)
_NO_UNDEF_RE = re.compile(r"^'([^']+)' is not defined\.$")
_RELATIVE_IMPORT_RE = re.compile(r"""^\s*import\s[^;'"]*?from\s+["'](\./[^"']+)["']""", re.MULTILINE)


@dataclass(frozen=True)
class FeedbackError:
    file: str
    line: int
    col: int
    code: str
    message: str


@dataclass
class FeedbackReport:
    ok: bool
    errors: list[FeedbackError] = field(default_factory=list)
    raw: str = ""
    # What ran, as the agent's message names it. "Typecheck" for tsc; "Syntax check" for the
    # compile-and-parse pass a stack with no types gets.
    kind: str = "Typecheck"

    def signature(self) -> str:
        """Stable key for no-progress detection: the set of (file,line,code) sorted."""
        return "|".join(sorted(f"{e.file}:{e.line}:{e.code}" for e in self.errors))

    def as_agent_message(self, max_errors: int = 20) -> str:
        """Structured summary to inject into the agent's next turn."""
        if self.ok:
            return f"{self.kind} passed. No errors."
        lines = [f"{self.kind} found {len(self.errors)} error(s). Fix these:"]
        for e in self.errors[:max_errors]:
            lines.append(f"- {e.file}:{e.line}:{e.col} {e.code}: {e.message}")
        if len(self.errors) > max_errors:
            lines.append(f"...and {len(self.errors) - max_errors} more.")
        return "\n".join(lines)


def parse_tsc(output: str) -> list[FeedbackError]:
    errors: list[FeedbackError] = []
    for line in output.splitlines():
        m = _TSC_RE.match(line.strip())
        if m:
            errors.append(
                FeedbackError(
                    file=m["file"].strip(),
                    line=int(m["line"]),
                    col=int(m["col"]),
                    code=m["code"],
                    message=m["msg"].strip(),
                )
            )
    return errors


def parse_py_compile(output: str, workspace: Path | None = None) -> list[FeedbackError]:
    """The errors in `python -m py_compile` output. A file line is remembered until the error line
    that belongs to it arrives; paths are made workspace-relative so the agent reads the path it
    knows."""
    errors: list[FeedbackError] = []
    file, line = "", 0
    for raw in output.splitlines():
        if m := _PY_FILE_RE.match(raw):
            file, line = m["file"], int(m["line"])
            continue
        if file and (m := _PY_ERR_RE.match(raw.strip())):
            errors.append(FeedbackError(file=_relative(file, workspace), line=line, col=1,
                                        code=m["code"], message=m["msg"].strip()))
            file, line = "", 0
    return errors


def parse_node_check(output: str, workspace: Path | None = None) -> list[FeedbackError]:
    """The errors in `node --check` output, in the same shape."""
    errors: list[FeedbackError] = []
    file, line = "", 0
    for raw in output.splitlines():
        if m := _NODE_FILE_RE.match(raw.strip()):
            file, line = m["file"], int(m["line"])
            continue
        if file and (m := _PY_ERR_RE.match(raw.strip())):
            errors.append(FeedbackError(file=_relative(file, workspace), line=line, col=1,
                                        code=m["code"], message=m["msg"].strip()))
            file, line = "", 0
    return errors


def parse_oxlint(output: str, workspace: Path | None = None) -> list[FeedbackError]:
    """The errors in `oxlint --format json` output. Warnings are not failures, and output that is
    not the report (a launcher that could not start) is no errors rather than a crash."""
    try:
        diagnostics = json.loads(output).get("diagnostics", [])
    except (ValueError, AttributeError):
        return []
    errors: list[FeedbackError] = []
    for d in diagnostics:
        if d.get("severity") != "error":
            continue
        span = ((d.get("labels") or [{}])[0]).get("span", {})
        file = str(Path(workspace, d["filename"])) if workspace is not None else d["filename"]
        errors.append(FeedbackError(file=_relative(file, workspace), line=int(span.get("line", 1)),
                                    col=int(span.get("column", 1)), code=d.get("code", ""),
                                    message=d.get("message", "")))
    return sorted(errors, key=lambda e: (e.file, e.line, e.col))


def _oxlint(workspace: Path) -> str | None:
    """The app's own oxlint (a react-vite app links the template's `node_modules`), else the React
    template's — a fastapi-antd app has no `node_modules`, and the image installs the template's
    in full. The template is the one the workspace manager seeds from (`SAGE_TEMPLATE`)."""
    template = Path(os.environ.get("SAGE_TEMPLATE", REACT_VITE.template_dir))
    for root in (workspace, template):
        binary = root / "node_modules" / ".bin" / "oxlint"
        if binary.is_file():
            return str(binary)
    return None


def _lint(workspace: Path, files: list[Path], timeout_s: float) -> tuple[str, list[FeedbackError]] | None:
    """oxlint over workspace-relative `files`, with the stack template's config — Sage's rules, so an
    app seeded before the config shipped is read the same as a new one. `None` when there is no
    binary to run: the check is then what it was before oxlint. Raises `TimeoutExpired`."""
    binary = _oxlint(workspace)
    if binary is None:
        return None
    if not files:
        return "", []
    stack = stack_of(workspace)
    config = stack.template_dir / ".oxlintrc.json"
    proc = subprocess.run([binary, "-c", str(config), "--format", "json", *map(str, files)],
                          cwd=workspace, capture_output=True, text=True, timeout=timeout_s, check=False)
    errors = parse_oxlint(proc.stdout or "", workspace)
    if stack.checker == "python":
        shared = _script_globals(workspace)
        errors = [e for e in errors if not (e.code == "eslint(no-undef)" and (m := _NO_UNDEF_RE.match(e.message))
                                            and m[1] in shared)]
    return (proc.stdout or "") + (proc.stderr or ""), errors


def _script_globals(workspace: Path) -> set[str]:
    """The top-level names the app's own page scripts declare. The page loads them as plain scripts,
    so each one's are defined in the others, and the template's fixed `globals` cannot list them."""
    names: set[str] = set()
    for js in (workspace / "static").rglob("*.js"):
        if any(js.relative_to(workspace).as_posix().startswith(skip) for skip in _JS_SKIP):
            continue
        for m in _SCRIPT_GLOBAL_RE.finditer(js.read_text(errors="ignore")):
            names.add(m[1] or m[2])
    return names


def _relative_imports(entry: Path) -> list[Path]:
    """The `.ts`/`.tsx` files `entry` imports from beside or below it, e.g. `./screens/MainScreen`."""
    found = []
    for spec in _RELATIVE_IMPORT_RE.findall(entry.read_text()):
        if ".." in Path(spec).parts:
            continue
        base = entry.parent / spec
        found += [p for p in (base, base.with_name(base.name + ".tsx"), base.with_name(base.name + ".ts"))
                  if p.suffix in (".ts", ".tsx") and p.is_file()][:1]
    return found


def _relative(path: str, workspace: Path | None) -> str:
    if workspace is None:
        return path
    try:
        return Path(path).resolve().relative_to(Path(workspace).resolve()).as_posix()
    except ValueError:
        return path


def check_python_stack(workspace: Path, timeout_s: float = 120.0) -> FeedbackReport:
    """The end-of-turn check for a fastapi-antd app: every .py compiles, every .js the page loads
    parses, and the starter placeholder is gone. No types, so no typecheck — but a file that will
    not compile is a server that will not start, and a script that will not parse is a blank page,
    and both are worth a turn before a viewer sees them."""
    workspace = Path(workspace)
    py_files = sorted(p for p in workspace.rglob("*.py")
                      if not any(part.startswith(".") or part == "__pycache__" for part in
                                 p.relative_to(workspace).parts))
    js_files = sorted(p for p in workspace.rglob("*.js")
                      if p.relative_to(workspace).parts[0] == "static"
                      and not any(p.relative_to(workspace).as_posix().startswith(skip) for skip in _JS_SKIP))
    raw_parts: list[str] = []
    errors: list[FeedbackError] = []
    try:
        if py_files:
            proc = subprocess.run(
                [_python(), "-m", "py_compile", *map(str, py_files)],
                cwd=workspace, capture_output=True, text=True, timeout=timeout_s, check=False,
            )
            out = (proc.stdout or "") + (proc.stderr or "")
            raw_parts.append(out)
            errors += parse_py_compile(out, workspace)
        node = shutil.which("node")
        if js_files and node:
            for js in js_files:
                proc = subprocess.run([node, "--check", str(js)], cwd=workspace, capture_output=True,
                                      text=True, timeout=timeout_s, check=False)
                out = (proc.stdout or "") + (proc.stderr or "")
                raw_parts.append(out)
                errors += parse_node_check(out, workspace)
        elif js_files:
            raw_parts.append("node is not on PATH, so the page's scripts were not syntax-checked")
        # Only a script that parses is linted: a syntax error is already its file's one error.
        broken = {e.file for e in errors}
        parsed = [rel for rel in (p.relative_to(workspace) for p in js_files) if rel.as_posix() not in broken]
        if (lint := _lint(workspace, parsed, timeout_s)) is not None:
            raw_parts.append(lint[0])
            errors += lint[1]
    except subprocess.TimeoutExpired as e:
        return FeedbackReport(ok=False, raw=f"syntax check timed out after {timeout_s}s: {e}",
                              kind="Syntax check")
    errors += _unloaded_definitions(workspace, js_files)
    errors += _loaded_after_entry(workspace)
    errors += [e for js in js_files for e in _untyped_view_fields(workspace, js)]
    errors += _hand_written_url_state(workspace, js_files, FASTAPI_ANTD.view_state, "sage.useViewState")
    errors += [e for js in js_files for e in _list_series_colours(workspace, js)]
    if not errors:
        errors += [_placeholder_error(rel) for rel in _shown_scripts(workspace)
                   if re.search(r"""className:\s*["']sage-placeholder["']""",
                                (workspace / rel).read_text(errors="ignore"))]
    return FeedbackReport(ok=not errors, errors=errors, raw="\n".join(raw_parts), kind="Syntax check")


def _shown_scripts(workspace: Path) -> list[str]:
    """The entry, and each app script the page loads whose `window.<ns>.<Name>` another loaded
    script names: a new app's placeholder is in its first screen (#697). A screen the page no
    longer loads, or that nothing mounts any more, is not what a viewer sees."""
    loaded = [rel for rel, _ in _loaded_scripts(workspace)
              if not rel.startswith(_JS_SKIP) and (workspace / rel).is_file()]
    sources = {rel: (workspace / rel).read_text(errors="ignore") for rel in loaded}
    if (workspace / "static/app.js").is_file():
        sources.setdefault("static/app.js", (workspace / "static/app.js").read_text(errors="ignore"))
    # A comment naming a screen does not mount it; the starter shell's own header names MainScreen.
    code = {rel: re.sub(r"/\*.*?\*/|^[ \t]*//[^\n]*", "", source, flags=re.DOTALL | re.MULTILINE)
            for rel, source in sources.items()}
    shown = ["static/app.js"] if "static/app.js" in sources else []
    for rel in loaded:
        if rel == "static/app.js":
            continue
        names = {m[2] for m in _WINDOW_MEMBER_RE.finditer(sources[rel]) if m[3]}
        if not names or any(re.search(rf"\b{re.escape(name)}\b", source)
                            for other, source in code.items() if other != rel for name in names):
            shown.append(rel)
    return shown


def _placeholder_error(rel: str) -> FeedbackError:
    return FeedbackError(
        file=rel, line=1, col=1, code="SAGE001",
        message="The starter placeholder is still the app's screen. Replace it with "
                "the requested app and connect the components you wrote.",
    )


def _loaded_scripts(workspace: Path) -> list[tuple[str, int]]:
    """Each `<script src>` in `static/index.html`, in page order, with its line."""
    index = workspace / "static" / "index.html"
    if not index.is_file():
        return []
    html = index.read_text(errors="ignore")
    return [(re.sub(r"^(\./|/)+", "", m[1].strip()), html.count("\n", 0, m.start()) + 1)
            for m in _SCRIPT_SRC_RE.finditer(html)]


def _loaded_after_entry(workspace: Path) -> list[FeedbackError]:
    """An app script the page loads after `static/app.js` that puts a name on the page (#697).
    `app.js` mounts the app as it runs, so a screen registered after it is not there yet, and
    `SAGE002` passes because the tag exists. A script that defines nothing is left alone."""
    scripts = _loaded_scripts(workspace)
    entry = next((i for i, (rel, _) in enumerate(scripts) if rel == "static/app.js"), None)
    if entry is None:
        return []
    errors = []
    for rel, line in scripts[entry + 1:]:
        path = workspace / rel
        if rel.startswith(_JS_SKIP) or not path.is_file():
            continue
        source = path.read_text(errors="ignore")
        defined = ([f"window.{m[1]}.{m[2]}" for m in _WINDOW_MEMBER_RE.finditer(source) if m[3]]
                   or [m[1] or f"window.{m[2]}" for m in _SCRIPT_GLOBAL_RE.finditer(source)])
        if defined:
            errors.append(FeedbackError(
                file="static/index.html", line=line, col=1, code="SAGE003",
                message=(f"static/index.html loads {rel} after static/app.js, so {defined[0]} is "
                         f"not defined yet when static/app.js mounts the app. Move its <script> "
                         f"line above static/app.js."),
            ))
    return errors


def _unloaded_definitions(workspace: Path, js_files: list[Path]) -> list[FeedbackError]:
    """A `window.<ns>.<Name>` that one script reads and only scripts `static/index.html` never loads
    define (#684). The page runs the scripts it lists and no others, so the name is undefined where
    it is read — React error #130 for a component — and every file still parses. A name nothing
    here defines is not this check's: Sage's helpers and the vendored globals are loaded by the
    template, and are not in `js_files`."""
    index = workspace / "static" / "index.html"
    if not index.is_file():
        return []
    loaded = {re.sub(r"^(\./|/)+", "", src.strip())
              for src in _SCRIPT_SRC_RE.findall(index.read_text(errors="ignore"))}
    defined: dict[tuple[str, str], set[str]] = {}
    namespaces: dict[str, set[str]] = {}
    read: dict[tuple[str, str], set[str]] = {}
    for js in js_files:
        rel = js.relative_to(workspace).as_posix()
        source = js.read_text(errors="ignore")
        for m in _WINDOW_MEMBER_RE.finditer(source):
            (defined if m[3] else read).setdefault((m[1], m[2]), set()).add(rel)
        for ns in _WINDOW_NAMESPACE_RE.findall(source):
            namespaces.setdefault(ns, set()).add(rel)
    unloaded: dict[str, tuple[str, str, str]] = {}
    for (ns, name), readers in sorted(read.items()):
        definers = defined.get((ns, name)) or namespaces.get(ns, set())
        others = sorted((readers - definers) & loaded)
        if definers and others and not definers & loaded:
            unloaded.setdefault(min(definers), (ns, name, others[0]))
    return [FeedbackError(
        file="static/index.html", line=1, col=1, code="SAGE002",
        message=(f"{file} defines window.{ns}.{name}, which {reader} uses, but static/index.html "
                 f'has no <script src="{file}"> for it. Add one above the script that uses it.'),
    ) for file, (ns, name, reader) in sorted(unloaded.items())]


_VIEW_STATE_CALL_RE = re.compile(r"\buseViewState\s*\(\s*")
_VIEW_FIELD_KEY_RE = re.compile(r"""\s*(?:(["'])\s*\1|[A-Za-z_$][\w$]*)\s*:\s*""")
# How a value that is not an object starts, once `_blank_text` has emptied its strings.
_NOT_AN_OBJECT_RE = re.compile(r"""["'`/\[\d.\-]|(?:true|false|null|undefined)\b""")
_IDENT_RE = re.compile(r"[A-Za-z_$][\w$]*")


def _blank_text(source: str) -> str:
    """`source` with its comments, and the insides of its strings and regex literals, blanked to
    spaces: the same length and lines, so a brace or a comma left in it is code. A `/` opens a regex
    after an operator or an opening bracket, which is all a schema holds; this is no JS parser."""
    out, i, n, prev = list(source), 0, len(source), "("

    def blank(start: int, end: int) -> None:
        out[start:end] = [ch if ch == "\n" else " " for ch in source[start:end]]

    while i < n:
        c = source[i]
        if source.startswith("//", i):
            end = source.find("\n", i)
            end = n if end < 0 else end
            blank(i, end)
            i = end
        elif source.startswith("/*", i):
            end = source.find("*/", i + 2)
            end = n if end < 0 else end + 2
            blank(i, end)
            i = end
        elif c in "'\"`" or (c == "/" and prev in "(,=:[!&|?{};"):
            end, in_class = i + 1, False
            while end < n and (in_class or source[end] != c) and (c == "`" or source[end] != "\n"):
                if c == "/" and source[end] in "[]":
                    in_class = source[end] == "["
                end += 2 if source[end] == "\\" else 1
            blank(i + 1, end)
            prev, i = c, end + 1
        else:
            if not c.isspace():
                prev = c
            i += 1
    return "".join(out)


def _bound_value(code: str, name: str) -> int | None:
    """Where `const|let|var <name> =`'s value starts in blanked `code`, when this file binds it."""
    m = re.search(rf"\b(?:const|let|var)\s+{re.escape(name)}\s*=\s*", code)
    return m.end() if m else None


def _not_an_object(code: str, value: str) -> bool:
    """`value` is plainly not an object: a literal, or a name this file binds to one."""
    if _IDENT_RE.fullmatch(value) and not _NOT_AN_OBJECT_RE.match(value):
        bound = _bound_value(code, value)
        return bound is not None and bool(_NOT_AN_OBJECT_RE.match(code, bound))
    return bool(_NOT_AN_OBJECT_RE.match(value))


def _object_entries(code: str, start: int) -> list[tuple[int, int]]:
    """The `(begin, stop)` of each top-level entry of the object literal opening at `code[start]`."""
    entries, depth, begin = [], 0, start + 1
    for k in range(start + 1, len(code)):
        if code[k] in "{[(":
            depth += 1
        elif code[k] in "}])" and depth:
            depth -= 1
        elif code[k] in ",}" and not depth:
            entries.append((begin, k))
            begin = k + 1
            if code[k] == "}":
                break
    return entries


# The helper's own list: `var TYPES = { string: true, ... }` or `new Set(["string", ...])`.
_HELPER_TYPES_RE = re.compile(r"\bTYPES\s*=\s*(?:\{([^}]*)\}|new\s+Set\s*\(\s*\[([^\]]*)\])")


def _helper_types(workspace: Path) -> list[str] | None:
    """The field types the app's `static/sage/viewState.js` accepts, read from the helper itself so
    the check cannot drift from the code that throws; None when this app has no helper to read."""
    helper = workspace / FASTAPI_ANTD.view_state
    m = _HELPER_TYPES_RE.search(helper.read_text(errors="ignore")) if helper.is_file() else None
    if not m:
        return None
    names = (re.findall(r"""["']?([A-Za-z_$][\w$]*)["']?\s*:""", m[1]) if m[1] is not None
             else re.findall(r"""["'](\w+)["']""", m[2]))
    return names or None


def _listed(types: list[str] | None) -> str:
    return ", ".join(types) if types else "the types listed at the top of static/sage/viewState.js"


def _field_problem(code: str, source: str, at: int, value: str, types: list[str] | None) -> str | None:
    """What the helper throws on in the field `value` at `code[at]`, or that an enum lists no
    `values`. Only an object this file shows — written there or a `const` bound to one — whose
    `type` is a string literal; a spread or a computed type is not guessed at."""
    if _IDENT_RE.fullmatch(value):
        at = _bound_value(code, value) or at
    if code[at:at + 1] != "{":
        return None
    props: dict[str, str] = {}
    for begin, stop in _object_entries(code, at):
        key = _VIEW_FIELD_KEY_RE.match(code, begin, stop)
        if not key:
            if code[begin:stop].strip():  # a spread, a shorthand or a computed key: not shown here
                return None
            continue
        prop = re.sub(r"\s*:\s*$", "", source[begin:key.end()]).strip().strip("'\"")
        props[prop] = source[key.end():stop].strip()
    if "type" not in props:
        return f"names no type, and the helper throws: it accepts {_listed(types)}"
    literal = re.fullmatch(r"""(["'`])(\w*)\1""", props["type"])
    if not literal:
        return None
    kind = literal[2]
    if types is not None and kind not in types:
        return f"has type '{kind}', and the helper throws: it accepts only {_listed(types)}"
    if kind == "enum" and "values" not in props:
        return "is an enum with no values, so it never accepts one: list them as values: [...]"
    if kind == "string" and props.get("shareable") == "true" and "pattern" not in props:
        return "is a shareable string with no pattern, and the helper throws: free text never goes in the URL"
    return None


def _untyped_view_fields(workspace: Path, js: Path) -> list[FeedbackError]:
    """A `useViewState` field `static/sage/viewState.js` throws on at the first render, while every
    file still parses: a value that is not an object (#706), a type the helper does not accept, a
    shareable string with no pattern, the reserved key `v` (#722) — and an enum with no `values`,
    which the helper does not throw on but which never accepts a value. Only a schema this file
    shows is read — written in the call, or a `const` object here passed by name. One from anywhere
    else is another file's, and is not guessed at."""
    source = js.read_text(errors="ignore")
    if "useViewState" not in source:
        return []
    code = _blank_text(source)
    types = _helper_types(workspace)
    errors = []
    for call in _VIEW_STATE_CALL_RE.finditer(code):
        start: int | None = call.end()
        name = _IDENT_RE.match(code, start)
        if name and code[name.end():].lstrip()[:1] in (")", ","):
            start = _bound_value(code, name[0])
        if start is None or code[start:start + 1] != "{":
            continue
        for begin, stop in _object_entries(code, start):
            key = _VIEW_FIELD_KEY_RE.match(code, begin, stop)
            value = code[key.end():stop].rstrip() if key else ""
            if not value:
                continue
            lead = begin + len(code[begin:stop]) - len(code[begin:stop].lstrip())
            field = re.sub(r"\s*:\s*$", "", source[lead:key.end()]).strip("'\"")
            if _not_an_object(code, value):
                shown = source[key.end():key.end() + len(value)]
                message = (f"useViewState field '{field}' is {shown[:40]}, but static/sage/viewState.js "
                           f"needs each field to be an object naming its type, and the app crashes on "
                           f"its first render without one. Write it as {field}: {{ type: \"enum\", "
                           f"values: [...], default: ..., shareable: true }} or {field}: {{ type: "
                           f"\"string\", default: \"\" }}. Types are {_listed(types)}; a shareable "
                           f"string also needs a pattern.")
            elif field == "v":
                message = ("useViewState field 'v' is reserved: static/sage/viewState.js keeps the URL's "
                           "version there and throws on the app's first render. Rename the field.")
            elif problem := _field_problem(code, source, key.end(), value, types):
                message = (f"useViewState field '{field}' {problem}. The schema each type needs is "
                           f"at the top of static/sage/viewState.js.")
            else:
                continue
            errors.append(FeedbackError(
                file=js.relative_to(workspace).as_posix(), line=source.count("\n", 0, lead) + 1,
                col=1, code="SAGE004", message=message))
    return errors


# Writing the URL or reading its query string: `history.replaceState(` / `.pushState(`, `location.search`.
_URL_STATE_RE = re.compile(r"\bhistory\s*\.\s*(?:replaceState|pushState)\s*\(|\blocation\s*\.\s*search\b")


def _hand_written_url_state(workspace: Path, files: list[Path], helper: str, hook: str) -> list[FeedbackError]:
    """App code that keeps view state in the URL itself rather than through `hook` (#717). It gets
    none of the helper's types, defaults, shareable flag or working Back, and it parses. Only where
    the app has `helper` to steer to; `files` are the app's own, never Sage's, which do this on
    purpose. Comments and strings are blanked first, so naming the calls is not making them."""
    if not (workspace / helper).is_file():
        return []
    errors = []
    for path in files:
        source = path.read_text(errors="ignore")
        if "history" not in source and "location" not in source:
            continue
        code, shown = _blank_text(source), source.splitlines()
        for line in sorted({code.count("\n", 0, m.start()) + 1 for m in _URL_STATE_RE.finditer(code)}):
            errors.append(FeedbackError(
                file=path.relative_to(workspace).as_posix(), line=line, col=1, code="SAGE005",
                message=(f"`{shown[line - 1].strip()[:60]}` keeps view state in the URL by hand. Keep it "
                         f"in {hook} from {helper} instead: it puts the shareable fields in the URL, "
                         f"types and defaults each one, and makes Back work. Read the state it returns, "
                         f"never location: const [view, patchView] = {hook}({{ metric: "
                         f"{{ type: \"enum\", values: [\"dau\", \"wau\"], default: \"dau\", shareable: "
                         f"true }} }}); then patchView({{ metric }}) when it changes."),
            ))
    return errors


# A `series:` key, whose value (to the comma or bracket that closes it) holds the series config.
_SERIES_KEY_RE = re.compile(r"\bseries\s*:\s*")
# A `color:` whose value is a list: an array literal, or a `.map(` straight off a name. Not
# `colors:`, which takes a list, and not a ternary or a member read, which give one colour.
_LIST_COLOUR_RE = re.compile(r"\bcolor\s*:\s*(?=\[|[A-Za-z_$][\w$.]*\s*\.\s*map\s*\()")


def _value_end(code: str, start: int) -> int:
    """Where the value starting at `start` in blanked `code` ends: the first `,` or closing
    bracket at its own depth."""
    depth = 0
    for k in range(start, len(code)):
        if code[k] in "{[(":
            depth += 1
        elif code[k] in "}])":
            if not depth:
                return k
            depth -= 1
        elif code[k] == "," and not depth:
            return k
    return len(code)


def _list_series_colours(workspace: Path, js: Path) -> list[FeedbackError]:
    """A Highcharts series `color:` given a list (#717). A series takes one colour; a list draws
    every bar with no fill, so the chart looks empty, and the file parses. Only what sits inside a
    `series:` value is read — a series the file builds elsewhere and passes by name is not."""
    source = js.read_text(errors="ignore")
    if "series" not in source:
        return []
    code = _blank_text(source)
    errors = []
    for series in _SERIES_KEY_RE.finditer(code):
        for m in _LIST_COLOUR_RE.finditer(code, series.end(), _value_end(code, series.end())):
            value = " ".join(source[m.end():_value_end(code, m.end())].split())
            errors.append(FeedbackError(
                file=js.relative_to(workspace).as_posix(), line=code.count("\n", 0, m.start()) + 1,
                col=1, code="SAGE006",
                message=(f"Highcharts series color is {value[:40]}, a list, but a series takes one "
                         f"colour, so every bar draws with no fill and the chart looks empty. Give "
                         f"the series one colour (color: sage.accents[0]), or colour points one by one "
                         f"with objects in data: data: rows.map(r => ({{ y: r.value, color: r.value > 0 "
                         f"? sage.accents[0] : sage.accents[1] }}))."),
            ))
    return errors


def _python() -> str:
    import sys
    return sys.executable


#: How long one file's check may take. Two orders of magnitude under the end-of-turn check's 120s,
#: because this one runs on the build's poll loop while the turn is live: a hung `node` there is a
#: turn that stops being watched, not just a check that fails.
_PER_FILE_TIMEOUT_S = 20.0


def check_file(workspace: Path, path: str) -> FeedbackReport | None:
    """One file's own check, for the moment its write lands rather than after the turn ends (#547).

    `None` is "this stack does not check this file", and it is the answer for most writes: a
    file that is not a `.py`, a page `.js` or a react-vite `src/` `.ts`/`.tsx`, a vendored bundle,
    a path outside the workspace, a write that left nothing on disk, a react-vite file where oxlint
    is not installed. The caller says nothing then, and says nothing on a clean report either — the
    model hears from this only when the file it just wrote will not parse or breaks a lint rule.

    The commands are the ones the end-of-turn check runs over the whole workspace, each over one
    file. That is what makes running them per write affordable: a `py_compile` of one module, a
    `node --check` and an oxlint of one script are tens of milliseconds, where the workspace pass is
    every file the app has. A react-vite file gets only the lint: `tsc` is project-wide by
    construction and stays at the end of the turn. The end-of-turn check is unchanged and still runs.
    """
    workspace = Path(workspace)
    checker = stack_of(workspace).checker
    target = Path(path)
    if not target.is_absolute():
        target = workspace / target
    try:
        rel = target.resolve().relative_to(workspace.resolve())
    except (ValueError, OSError):
        # Outside the app: not this app's file to check, and not a path to hand a subprocess.
        return None
    if not target.is_file():
        return None
    parts = rel.parts
    if any(part.startswith(".") or part == "__pycache__" for part in parts):
        return None
    # Which file gets which check is the same rule the end-of-turn pass uses, read off the same
    # constants: every `.py`, and the `.js` under `static/` that the page loads, minus the bundles
    # and Sage's own scripts.
    if checker == "tsc":
        if not (parts[0] == "src" and target.suffix in (".ts", ".tsx")):
            return None
        try:
            lint = _lint(workspace, [rel], _PER_FILE_TIMEOUT_S)
        except subprocess.TimeoutExpired as e:
            return FeedbackReport(ok=False, raw=f"lint timed out after {_PER_FILE_TIMEOUT_S}s: {e}",
                                  kind="Lint")
        if lint is None:
            return None
        return FeedbackReport(ok=not lint[1], errors=lint[1], raw=lint[0], kind="Lint")
    if checker != "python":
        return None
    if target.suffix == ".py":
        command = [_python(), "-m", "py_compile", str(target)]
        parse = parse_py_compile
    elif (target.suffix == ".js" and parts[0] == "static"
            and not any(rel.as_posix().startswith(skip) for skip in _JS_SKIP)):
        node = shutil.which("node")
        if not node:
            return None
        command = [node, "--check", str(target)]
        parse = parse_node_check
    else:
        return None
    try:
        proc = subprocess.run(command, cwd=workspace, capture_output=True, text=True,
                              timeout=_PER_FILE_TIMEOUT_S, check=False)
        out = (proc.stdout or "") + (proc.stderr or "")
        errors = parse(out, workspace)
        # A script that parses is then linted, as the end-of-turn pass does.
        if parse is parse_node_check and not errors and (lint := _lint(workspace, [rel], _PER_FILE_TIMEOUT_S)):
            out += lint[0]
            errors = lint[1]
    except subprocess.TimeoutExpired as e:
        return FeedbackReport(ok=False, raw=f"syntax check timed out after {_PER_FILE_TIMEOUT_S}s: {e}",
                              kind="Syntax check")
    return FeedbackReport(ok=not errors, errors=errors, raw=out, kind="Syntax check")


class FeedbackRunner:
    def __init__(self, tsconfig: str = "tsconfig.app.json", timeout_s: float = 120.0) -> None:
        self._tsconfig = tsconfig
        self._timeout_s = timeout_s

    def check(self, workspace: Path) -> FeedbackReport:
        # Which check is the workspace's stack's to say (#490); the caller holds one runner.
        if stack_of(Path(workspace)).checker == "python":
            return check_python_stack(workspace, self._timeout_s)
        try:
            proc = subprocess.run(
                ["npx", "tsc", "--noEmit", "-p", self._tsconfig],
                cwd=workspace,
                capture_output=True,
                text=True,
                timeout=self._timeout_s,
                check=False,  # tsc exits nonzero ON type errors — that's the result being read, not a failure
            )
            out = (proc.stdout or "") + (proc.stderr or "")
            errors = parse_tsc(out)
            sources = sorted(p.relative_to(workspace) for pattern in ("*.ts", "*.tsx")
                             for p in (Path(workspace) / "src").rglob(pattern))
            if (lint := _lint(Path(workspace), sources, self._timeout_s)) is not None:
                out += lint[0]
                errors += lint[1]
            errors += _hand_written_url_state(
                Path(workspace), [Path(workspace) / p for p in sources
                                  if p.as_posix() not in REACT_VITE.owned_sources
                                  and not p.as_posix().startswith(REACT_VITE.vendored)],
                REACT_VITE.view_state, "useViewState")
        except subprocess.TimeoutExpired as e:
            return FeedbackReport(ok=False, raw=f"typecheck timed out after {self._timeout_s}s: {e}")

        # The shipped starter typechecks even when every generated component is unused. Feed
        # that exact unfinished screen into the existing repair loop before accepting the build.
        # A new app's placeholder is in the first screen the entry imports (#697).
        entry = workspace / "src" / "App.tsx"
        if proc.returncode == 0 and entry.is_file():
            for path in [entry, *_relative_imports(entry)]:
                if re.search(r'<main\s+className=[\"\']sage-placeholder[\"\']\s*>', path.read_text()):
                    errors.append(_placeholder_error(path.relative_to(workspace).as_posix()))
        # tsc exits non-zero on errors; treat clean only when exit 0 AND no parsed errors.
        return FeedbackReport(ok=(proc.returncode == 0 and not errors), errors=errors, raw=out)
