"""Feedback runner (SPEC C10, PLAN 5.1, AC11).

After an agent edit, typecheck the workspace and turn the result into a structured report the
agent can consume on its next turn. Typecheck (`tsc --noEmit`) is the fast, high-value signal;
it catches most of what small models get wrong (types, missing imports, bad JSX) without a full
build. Browser-console capture is a Phase-1 add (needs a headless view of the preview).

Deep module, narrow interface: `FeedbackRunner.check(workspace) -> FeedbackReport`. Parsing of
tsc's line format is hidden behind `parse_tsc` (pure, unit-tested).
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

# tsc line: "src/App.tsx(12,5): error TS2304: Cannot find name 'foo'."
_TSC_RE = re.compile(r"^(?P<file>[^(]+)\((?P<line>\d+),(?P<col>\d+)\):\s+error\s+(?P<code>TS\d+):\s+(?P<msg>.*)$")


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

    def signature(self) -> str:
        """Stable key for no-progress detection: the set of (file,line,code) sorted."""
        return "|".join(sorted(f"{e.file}:{e.line}:{e.code}" for e in self.errors))

    def as_agent_message(self, max_errors: int = 20) -> str:
        """Structured summary to inject into the agent's next turn."""
        if self.ok:
            return "Typecheck passed. No errors."
        lines = [f"Typecheck found {len(self.errors)} error(s). Fix these:"]
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


class FeedbackRunner:
    def __init__(self, tsconfig: str = "tsconfig.app.json", timeout_s: float = 120.0) -> None:
        self._tsconfig = tsconfig
        self._timeout_s = timeout_s

    def check(self, workspace: Path) -> FeedbackReport:
        try:
            proc = subprocess.run(
                ["npx", "tsc", "--noEmit", "-p", self._tsconfig],
                cwd=workspace,
                capture_output=True,
                text=True,
                timeout=self._timeout_s,
            )
        except subprocess.TimeoutExpired as e:
            return FeedbackReport(ok=False, raw=f"typecheck timed out after {self._timeout_s}s: {e}")

        out = (proc.stdout or "") + (proc.stderr or "")
        errors = parse_tsc(out)
        # tsc exits non-zero on errors; treat clean only when exit 0 AND no parsed errors.
        return FeedbackReport(ok=(proc.returncode == 0 and not errors), errors=errors, raw=out)
