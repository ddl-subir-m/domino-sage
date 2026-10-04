"""Which keys a Built App's viewer may set: `sage_keys.json`, from the app's own `secret("NAME")` (#644).

A fastapi-antd app reads a key with `secret("NAME")` (`template/fastapi-antd/sage_secrets.py`), and
its Your keys page accepts a viewer's value only for a name in `sage_keys.json` beside it. Sage writes
that file from a scan of the app's own Python at publish (`refresh_entry_script`) and when the
preview starts, with each name's note from the Project's `.sage/secrets.json`. Names and notes only:
no value is read or written here.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .stack import FASTAPI_ANTD, preview_stack_of

NAMES_FILE = "sage_keys.json"
_CALL = re.compile(r"""\bsecret\(\s*(["'])([A-Za-z_][A-Za-z0-9_]*)\1""")
# Sage's own names, which no viewer may replace (the same prefixes the Secrets panel refuses).
_RESERVED = ("SAGE_", "DOMINO_")
_SKIP_DIRS = {"node_modules", "__pycache__", "vendor"}
# Sage-owned Python: its docstrings show `secret("NAME")` and are not the app reading a key.
_OWNED = {"sage_secrets.py", "sage_mcp.py", "sage_serve.py", "sage_queries.py", "sage_domino.py"}


def scan(app_path: Path) -> list[str]:
    """Every name the app's own `.py` files pass to `secret()`, sorted, without Sage's own."""
    names: set[str] = set()
    for path in Path(app_path).rglob("*.py"):
        rel = path.relative_to(app_path)
        if rel.name in _OWNED and len(rel.parts) == 1:
            continue
        if any(p.startswith(".") or p in _SKIP_DIRS for p in rel.parts[:-1]):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        names.update(m.group(2) for m in _CALL.finditer(text))
    return sorted(n for n in names if not n.upper().startswith(_RESERVED))


def _notes(app_path: Path) -> dict:
    # A Built App lives at `<project>/apps/<id>/`; the Project's record is two levels up.
    app_path = Path(app_path)
    project = app_path.parent.parent if app_path.parent.name == "apps" else app_path
    try:
        notes = json.loads((project / ".sage" / "secrets.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return notes if isinstance(notes, dict) else {}


def write_names(app_path: Path) -> bool:
    """Bring `sage_keys.json` in line with the app's code. True if it changed.

    Only for a fastapi-antd app. An app that reads no key and has no file gets none, so the scan
    leaves nothing behind in every app that never calls `secret()`.
    """
    app_path = Path(app_path)
    if preview_stack_of(app_path) is not FASTAPI_ANTD:
        return False
    notes = _notes(app_path)
    rows = []
    for name in scan(app_path):
        entry = notes.get(name)
        note = entry.get("note") if isinstance(entry, dict) else ""
        rows.append({"name": name, "note": note if isinstance(note, str) else ""})
    target = app_path / NAMES_FILE
    text = json.dumps(rows, indent=2) + "\n"
    try:
        current = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        if not rows:
            return False
        current = None
    if current == text:
        return False
    target.write_text(text, encoding="utf-8")
    return True
