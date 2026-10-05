"""Seal a transcript before its git blob crosses GitHub's warning.

GitHub warns at 50 MB and rejects a push at 100 MB, and the check is on the blob, not the
delta. `history.jsonl` is one append-only file, so each save stores a new copy of the whole
transcript. ADR-0006 measured the delta (~500 bytes a turn) and left this cap unmeasured.
Once a copy crosses 100 MB, every later save that contains a new copy is refused, and a pull
does not shrink it.

The live file stays `history.jsonl`. When the next line would carry it past `BLOB_CAP`, it
is renamed to `history.d/NNNNN.jsonl` and a new live file starts. Sealed segments stay in
git: an untracked transcript may not survive a restart. Readers walk the sealed segments in
order, then the live file. A line that is itself over the cap is its own file — the next
line seals it — and a line over 100 MB still cannot be pushed.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

# Under the 50 MB warning, so the warning stops being the first thing a refusal prints.
# A file already past the cap (written before this existed) is sealed on the next append
# and then stops growing. 40 MiB leaves room for one large line under the warning.
BLOB_CAP = 40 * 1024 * 1024

_SEGMENT_NAME = re.compile(r"^\d{5}\.jsonl$")


def segment_dir(live: Path) -> Path:
    return live.parent / "history.d"


def segment_paths(live: Path) -> list[Path]:
    """Sealed segments in the order they were written, then the live file if it exists."""
    directory = segment_dir(live)
    sealed: list[Path] = []
    if directory.is_dir():
        sealed = sorted(
            p for p in directory.iterdir() if p.is_file() and _SEGMENT_NAME.match(p.name)
        )
    if live.exists():
        sealed.append(live)
    return sealed


def append_line(live: Path, line: str) -> None:
    """Append one JSON line, sealing the live file first if this line would cross the cap."""
    live.parent.mkdir(parents=True, exist_ok=True)
    payload = line if line.endswith("\n") else line + "\n"
    size = live.stat().st_size if live.exists() else 0
    if size and size + len(payload.encode()) > BLOB_CAP:
        try:
            _roll(live)
        except FileNotFoundError:
            # The other Builder in this Project sealed the file between the size check
            # and the rename. The line belongs on the new live file either way.
            pass
    with live.open("a") as handle:
        handle.write(payload)


def _roll(live: Path) -> None:
    dest = segment_dir(live)
    dest.mkdir(parents=True, exist_ok=True)
    existing = sorted(p for p in dest.iterdir() if p.is_file() and _SEGMENT_NAME.match(p.name))
    number = int(existing[-1].stem) + 1 if existing else 1
    target = dest / f"{number:05d}.jsonl"
    # rename replaces an existing target. A second Builder sealing in the same instant can
    # pick this number after the first has taken it; step past a name that is already there.
    while target.exists():
        number += 1
        target = dest / f"{number:05d}.jsonl"
    live.rename(target)


def count_lines(live: Path) -> int:
    """Non-blank lines across every segment. Does not parse them."""
    total = 0
    for path in segment_paths(live):
        with path.open() as handle:
            total += sum(1 for line in handle if line.strip())
    return total


def truncate_to(live: Path, n: int, write: Callable[[Path, str], None]) -> None:
    """Keep the first `n` non-blank lines of the transcript and drop the rest.

    One file keeps the historical slice: `splitlines()[:n]`. That is the stop button's
    contract with `count_lines` on a log this writer fills, which has no blank lines.
    Across segments the cut can land in a sealed file, and the files after it go away,
    so a stop still drops only the turn that was in progress when the live file rolled.
    """
    paths = segment_paths(live)
    if not paths:
        return
    if paths == [live]:
        lines = live.read_text().splitlines()[:n]
        write(live, "".join(line + "\n" for line in lines))
        return
    remaining = n
    for index, path in enumerate(paths):
        raw = path.read_text().splitlines()
        nonblank = [i for i, line in enumerate(raw) if line.strip()]
        if remaining >= len(nonblank):
            remaining -= len(nonblank)
            continue
        if remaining == 0:
            kept: list[str] = []
        else:
            kept = raw[: nonblank[remaining - 1] + 1]
        if kept:
            write(path, "".join(line + "\n" for line in kept))
        elif path == live:
            write(path, "")
        else:
            path.unlink(missing_ok=True)
        for later in paths[index + 1 :]:
            later.unlink(missing_ok=True)
        return
