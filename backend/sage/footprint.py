"""What the workspace's memory is spent on, read from `/proc` and the cgroup (#738).

No dependency: the workspace has no shell and no `psutil`, and the two files that answer the
question are plain text. Both roots are arguments, so a test reads a fake tree on any host; the
route reads `PROC_ROOT` and `CGROUP_ROOT`.

Never raises. A process that exits between the listing and the read is skipped, a file that is not
there is a field that says so, and a host with neither tree (a laptop) gets an answer that says that.
"""
from __future__ import annotations

from pathlib import Path

PROC_ROOT = Path("/proc")
CGROUP_ROOT = Path("/sys/fs/cgroup")

# v1 reports "no limit" as the largest page-aligned 64-bit value rather than a word.
_V1_UNLIMITED = 2 ** 62

# Roles named by what is running rather than by who started it: each is a known memory weight
# wherever it sits in the tree, and tsc run by OpenCode's bash is still tsc.
_COMMAND_ROLES = (
    ("chromium", lambda base: "chrome" in base or "chromium" in base or base == "headless_shell"),
    ("tsc", lambda base: base == "tsc"),
    ("oxlint", lambda base: base.startswith("oxlint")),
)


def read(proc_root: Path, cgroup_root: Path, *, orchestrator_pid: int,
         roots: dict[int, str]) -> dict:
    """The container's memory, and every process under the orchestrator grouped by role.

    `roots` labels the top of each tree Sage started (the orchestrator, `opencode serve`, each
    preview); a process takes the label of its nearest labelled ancestor unless its command names
    one of `_COMMAND_ROLES`."""
    out: dict = {"memory": _memory(proc_root, cgroup_root, orchestrator_pid)}
    try:
        table = _processes(proc_root)
    except OSError as e:
        return {**out, "detail": f"cannot list {proc_root}: {type(e).__name__}: {e}",
                "sage_rss_bytes": 0, "groups": [], "processes": []}
    labels = {**roots, orchestrator_pid: "orchestrator"}
    rows = []
    for pid, (ppid, rss, argv) in table.items():
        role = _role(pid, table, labels, argv)
        if role is not None:
            rows.append({"pid": pid, "ppid": ppid, "rss_bytes": rss,
                         "cmd": Path(argv[0]).name if argv else "", "role": role})
    rows.sort(key=lambda r: r["rss_bytes"], reverse=True)
    groups: dict[str, dict] = {}
    for r in rows:
        g = groups.setdefault(r["role"], {"role": r["role"], "rss_bytes": 0, "processes": 0})
        g["rss_bytes"] += r["rss_bytes"]
        g["processes"] += 1
    return {**out, "sage_rss_bytes": sum(r["rss_bytes"] for r in rows),
            "groups": sorted(groups.values(), key=lambda g: g["rss_bytes"], reverse=True),
            "processes": rows}


def _processes(proc_root: Path) -> dict[int, tuple[int, int, list[str]]]:
    """pid -> (ppid, RSS bytes, argv) for every process whose status could be read."""
    table = {}
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            status = (entry / "status").read_text(errors="replace")
            argv = [a for a in (entry / "cmdline").read_bytes().decode(errors="replace").split("\0")
                    if a]
        except OSError:
            continue
        fields = dict(line.split(":", 1) for line in status.splitlines() if ":" in line)
        try:
            ppid = int(fields.get("PPid", "0").strip())
            rss = int(fields.get("VmRSS", "0 kB").split()[0]) * 1024
        except (ValueError, IndexError):
            continue
        table[int(entry.name)] = (ppid, rss, argv)
    return table


def _role(pid: int, table: dict, labels: dict[int, str], argv: list[str]) -> str | None:
    """The process's role, or None when it is not under Sage at all."""
    label, seen, at = None, set(), pid
    while at not in seen:
        seen.add(at)
        if at in labels:
            label = labels[at]
            break
        if at not in table:
            return None
        at = table[at][0]
    if label is None:
        return None
    for base in (_stem(a) for a in argv[:2]):
        for role, matches in _COMMAND_ROLES:
            if matches(base):
                return role
    return label


def _stem(arg: str) -> str:
    base = Path(arg).name
    for ext in (".js", ".cjs", ".mjs", ".exe"):
        if base.endswith(ext):
            return base[:-len(ext)]
    return base


def _memory(proc_root: Path, cgroup_root: Path, pid: int) -> dict:
    """`memory.current` / `memory.max` / `memory.events` (v2), or the v1 files that say the same."""
    v1_path, v2_path = "", ""
    try:
        for line in (proc_root / str(pid) / "cgroup").read_text().splitlines():
            _, controllers, path = line.split(":", 2)
            if controllers == "":
                v2_path = path.lstrip("/")
            elif "memory" in controllers.split(","):
                v1_path = path.lstrip("/")
    except (OSError, ValueError):
        pass
    for base in dict.fromkeys([cgroup_root / v2_path, cgroup_root]):
        if (base / "memory.current").is_file():
            return {"cgroup": "v2", "current_bytes": _int(_text(base / "memory.current")),
                    "max_bytes": _int(_text(base / "memory.max")),  # "max" is no limit: None
                    "events": _counters(base / "memory.events", ("oom", "oom_kill"))}
    for base in dict.fromkeys([cgroup_root / "memory" / v1_path, cgroup_root / "memory"]):
        if (base / "memory.usage_in_bytes").is_file():
            limit = _int(_text(base / "memory.limit_in_bytes"))
            return {"cgroup": "v1", "current_bytes": _int(_text(base / "memory.usage_in_bytes")),
                    "max_bytes": None if limit is None or limit >= _V1_UNLIMITED else limit,
                    "events": _counters(base / "memory.oom_control", ("under_oom", "oom_kill"))}
    return {"cgroup": None, "detail": f"no cgroup memory files under {cgroup_root}"}


def _text(path: Path) -> str | None:
    try:
        return path.read_text().strip()
    except OSError:
        return None


def _int(text: str | None) -> int | None:
    try:
        return int(text) if text is not None else None
    except ValueError:
        return None


def _counters(path: Path, keys: tuple[str, ...]) -> dict[str, int]:
    out = {}
    for line in (_text(path) or "").splitlines():
        name, _, value = line.partition(" ")
        if name in keys and _int(value) is not None:
            out[name] = _int(value)
    return out
