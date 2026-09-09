"""A Chat Artifact over the ceiling stays on disk and out of git (#223).

Committing `examples/` is what makes a restart keep the charts it made, and it is also permanent:
git history never gives a blob back. The orphan sweep beside this reclaims folders nothing claims,
but nothing reclaims a blob already committed — so the only lever on how big a Project gets is
what is allowed IN. That lever is `ARTIFACT_COMMIT_MAX`.

A matplotlib PNG at the dpi the Chat prompt asks for is 50-500 KB, so nobody writing a chart meets
this. It is the ceiling on a runaway.

Over it the file is NOT deleted. Too big to keep forever is not a reason to destroy someone's work,
and `commit_all(exclude=...)` already draws exactly this line for attached-data copies leaked into
`src/` — the bytes stay on the volume and never reach the repo. The honest consequence, and the
last test here, is that such a file cannot outlive the container.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from sage.workspace import git
from sage.workspace.threads import ARTIFACT_COMMIT_MAX, artifact_bytes, oversized_artifacts

SMALL = b"\x89PNG a chart" * 10


def _repo(path: Path) -> None:
    for args in (("init", "-q"), ("config", "user.email", "d@e.com"), ("config", "user.name", "d")):
        subprocess.run(["git", *args], cwd=str(path), capture_output=True, check=True)


def _tracked(path: Path) -> set[str]:
    out = subprocess.run(["git", "ls-files"], cwd=str(path), capture_output=True, text=True,
                         check=True).stdout
    return set(out.split())


def _art(root: Path, name: str, size: int) -> Path:
    p = root / "examples" / "thr_a" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"\0" * size)
    return p


def test_an_ordinary_chart_is_nowhere_near_the_ceiling(tmp_path: Path):
    """The claim that keeps this from being a limit anyone meets."""
    _art(tmp_path, "chart1.png", 500 * 1024)
    assert oversized_artifacts(tmp_path) == []
    assert ARTIFACT_COMMIT_MAX >= 10 * 1024 * 1024


def test_a_runaway_is_named(tmp_path: Path):
    big = _art(tmp_path, "runaway.png", ARTIFACT_COMMIT_MAX + 1)
    _art(tmp_path, "fine.png", 1024)
    assert oversized_artifacts(tmp_path) == ["examples/thr_a/runaway.png"]
    assert big.exists()          # named, not removed


def test_the_ceiling_keeps_it_out_of_the_commit_and_on_disk(tmp_path: Path):
    """The whole behaviour, through the real `commit_all(exclude=...)` path."""
    _repo(tmp_path)
    small = _art(tmp_path, "chart1.png", len(SMALL))
    small.write_bytes(SMALL)
    big = _art(tmp_path, "runaway.png", ARTIFACT_COMMIT_MAX + 1)

    git.commit_all(tmp_path, "a Chat turn", exclude=oversized_artifacts(tmp_path))

    tracked = _tracked(tmp_path)
    assert "examples/thr_a/chart1.png" in tracked        # the chart still rides in
    assert "examples/thr_a/runaway.png" not in tracked   # the runaway does not
    assert big.exists()                                  # and is still there to look at


def test_what_the_size_report_says(tmp_path: Path):
    _art(tmp_path, "chart1.png", 1000)
    _art(tmp_path, "chart2.png", 3000)
    total, count, largest, biggest = artifact_bytes(tmp_path)
    assert (total, count) == (4000, 2)
    assert largest == "examples/thr_a/chart2.png" and biggest == 3000


def test_the_report_is_zero_before_any_conversation(tmp_path: Path):
    assert artifact_bytes(tmp_path) == (0, 0, "", 0)
    assert oversized_artifacts(tmp_path) == []
