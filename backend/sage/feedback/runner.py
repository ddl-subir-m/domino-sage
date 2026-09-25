"""Feedback runner (SPEC C10, PLAN 5.1, AC11).

After an agent edit, check the workspace and turn the result into a structured report the agent
can consume on its next turn. For a react-vite app the check is the typecheck (`tsc --noEmit`): the
fast, high-value signal that catches most of what small models get wrong (types, missing imports,
bad JSX) without a full build. For a fastapi-antd app (#490) there is nothing to type: the check
compiles every `.py` and syntax-checks every `.js` the page loads, which catches the file that
would have failed to import or to parse before a viewer does. Browser-console capture is a
Phase-1 add (needs a headless view of the preview).

Deep module, narrow interface: `FeedbackRunner.check(workspace) -> FeedbackReport`. Which check
runs is the workspace's stack's to say; the parsers are pure and unit-tested.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from ..workspace.stack import stack_of

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
    except subprocess.TimeoutExpired as e:
        return FeedbackReport(ok=False, raw=f"syntax check timed out after {timeout_s}s: {e}",
                              kind="Syntax check")
    entry = workspace / "static" / "app.js"
    if not errors and entry.is_file() and re.search(r"""className:\s*["']sage-placeholder["']""",
                                                    entry.read_text(errors="ignore")):
        errors.append(FeedbackError(
            file="static/app.js", line=1, col=1, code="SAGE001",
            message="The starter placeholder is still the app's screen. Replace it with "
                    "the requested app and connect the components you wrote.",
        ))
    return FeedbackReport(ok=not errors, errors=errors, raw="\n".join(raw_parts), kind="Syntax check")


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
    react-vite app (whose check is `tsc`, project-wide by construction — see `FeedbackRunner.check`),
    a file that is not a `.py` or a page `.js`, a vendored bundle, a path outside the workspace, a
    write that left nothing on disk. The caller says nothing then, and says nothing on a clean
    report either — the model hears from this only when the file it just wrote will not parse.

    The two commands are the ones `check_python_stack` runs over the whole workspace, each over one
    file. That is what makes running them per write affordable: a `py_compile` of one module and a
    `node --check` of one script are tens of milliseconds, where the workspace pass is every file
    the app has. The end-of-turn check is unchanged and still runs.
    """
    workspace = Path(workspace)
    if stack_of(workspace).checker != "python":
        return None
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
    except subprocess.TimeoutExpired as e:
        return FeedbackReport(ok=False, raw=f"syntax check timed out after {_PER_FILE_TIMEOUT_S}s: {e}",
                              kind="Syntax check")
    out = (proc.stdout or "") + (proc.stderr or "")
    errors = parse(out, workspace)
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
        except subprocess.TimeoutExpired as e:
            return FeedbackReport(ok=False, raw=f"typecheck timed out after {self._timeout_s}s: {e}")

        out = (proc.stdout or "") + (proc.stderr or "")
        errors = parse_tsc(out)
        # The shipped starter typechecks even when every generated component is unused. Feed
        # that exact unfinished screen into the existing repair loop before accepting the build.
        entry = workspace / "src" / "App.tsx"
        if proc.returncode == 0 and entry.is_file():
            source = entry.read_text()
            if re.search(r'<main\s+className=[\"\']sage-placeholder[\"\']\s*>', source):
                errors.append(FeedbackError(
                    file="src/App.tsx", line=1, col=1, code="SAGE001",
                    message="The starter placeholder is still the app's screen. Replace it with "
                            "the requested app and connect the components you wrote.",
                ))
        # tsc exits non-zero on errors; treat clean only when exit 0 AND no parsed errors.
        return FeedbackReport(ok=(proc.returncode == 0 and not errors), errors=errors, raw=out)
