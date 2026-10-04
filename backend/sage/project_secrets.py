"""Project secrets: a builder's keys, stored as Domino Project environment variables (#641).

Domino holds the values. Sage reads them from the API when it needs them and keeps them in process
memory only: no value is written to disk, git, a log line or a response. What Sage writes itself
is `.sage/secrets.json` — names and the notes that tell the model what each key is for.

A Project variable added while the workspace runs is not in this process's environment until a
restart, and the workspace is never restarted for one. So every process Sage starts (OpenCode, a
preview) is given `os.environ` overlaid with the Project variables as the API answers them now.

`install()` is called once by the orchestrator. Off Domino nothing is installed, and every
function here falls back to `os.environ` alone.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import threading
from pathlib import Path

from .orchestrator import brand
from .provision.domino import NotFound

log = logging.getLogger("sage.project_secrets")

NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_RESERVED_PREFIXES = ("SAGE_", "DOMINO_")
# Read by the Built App template to encrypt viewer cookies (#644). 32 random bytes, base64url WITH
# padding, so the string is itself a valid Fernet key.
APP_KEY = "SAGE_APP_KEY"


class SecretError(ValueError):
    """A request the person can fix: a bad or reserved name, or a new secret with no value."""


def is_hidden(name: str) -> bool:
    return name.upper().startswith(_RESERVED_PREFIXES)


def check_name(name: str) -> None:
    if not NAME_RE.match(name or ""):
        raise SecretError(
            "A secret name uses letters, digits and underscores, and does not start with a digit.")
    if is_hidden(name):
        raise SecretError(brand.text(
            "Names starting with SAGE_ or DOMINO_ are reserved for {assistantName} and "
            "{platformName}. Choose another name."))


class ProjectSecrets:
    def __init__(self, control_plane, project_id: str, notes_path: Path) -> None:
        self._cp = control_plane
        self._pid = project_id
        self._notes_path = Path(notes_path)
        self._values: dict[str, str] = {}
        self._lock = threading.Lock()
        self._notes_lock = threading.Lock()  # read-modify-write of the notes file
        self._app_key_lock = threading.Lock()
        self._app_key_ok = False

    # --- values: Domino's, held in memory ---

    def refresh(self) -> dict[str, str]:
        values = self._cp.project_env_vars(self._pid)
        with self._lock:
            self._values = dict(values)
        return dict(values)

    def values(self) -> dict[str, str]:
        """The values last read from the API. Never calls it, so a model request never waits."""
        with self._lock:
            return dict(self._values)

    def env(self) -> dict[str, str]:
        """`os.environ` overlaid with the Project variables; the API wins."""
        try:
            values = self.refresh()
        except Exception as e:
            log.warning("secrets: could not read the Project variables (%s); using the last read",
                        type(e).__name__)
            values = self.values()
        return {**os.environ, **values}

    # --- notes: Sage's, on disk, names and notes only ---

    def _notes(self) -> dict[str, dict]:
        try:
            data = json.loads(self._notes_path.read_text())
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write_notes(self, notes: dict[str, dict]) -> None:
        self._notes_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._notes_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(notes, indent=2, sort_keys=True) + "\n")
        tmp.replace(self._notes_path)

    def _note_of(self, notes: dict[str, dict], name: str) -> str:
        entry = notes.get(name)
        return str(entry.get("note") or "") if isinstance(entry, dict) else ""

    # --- the three verbs the endpoints call ---

    def list(self) -> list[dict]:
        names = self.refresh()
        notes = self._notes()
        return [{"name": n, "note": self._note_of(notes, n)} for n in sorted(names) if not is_hidden(n)]

    def put(self, name: str, value: str | None, note: str | None) -> dict:
        check_name(name)
        exists = name in self.refresh()
        if value is None and not exists:
            raise SecretError("A new secret needs a value.")
        if value is not None:
            # Domino's POST on an existing name replaces its value in place.
            self._cp.set_project_env_var(self._pid, name, value)
            with self._lock:
                self._values[name] = value
        with self._notes_lock:
            notes = self._notes()
            if note is not None:
                notes[name] = {"note": note}
                self._write_notes(notes)
        return {"name": name, "note": self._note_of(notes, name)}

    def delete(self, name: str) -> None:
        check_name(name)
        try:
            self._cp.delete_project_env_var(self._pid, name)
        except NotFound:
            pass  # already gone: the outcome the caller asked for
        with self._lock:
            self._values.pop(name, None)
        with self._notes_lock:
            notes = self._notes()
            if notes.pop(name, None) is not None:
                self._write_notes(notes)

    def ensure_app_key(self) -> None:
        """Make sure the Project has SAGE_APP_KEY. Never overwrites one that is there."""
        with self._app_key_lock:
            if self._app_key_ok:
                return
            if APP_KEY not in self.refresh():
                key = base64.urlsafe_b64encode(os.urandom(32)).decode()
                self._cp.set_project_env_var(self._pid, APP_KEY, key)
                with self._lock:
                    self._values[APP_KEY] = key
            self._app_key_ok = True


_active: ProjectSecrets | None = None
_reason: str | None = None


def install(control_plane, project_id: str | None, notes_path: Path) -> ProjectSecrets | None:
    global _active, _reason
    if control_plane is None:
        _active, _reason = None, brand.text(
            "Keys are stored as {platformName} {project} variables, so they can only be added "
            "when {assistantName} runs on {platformName}.")
    elif not project_id:
        _active, _reason = None, brand.text(
            "{assistantName} can't tell which {platformName} {project} it runs in, so it has nowhere "
            "to store keys.")
    else:
        _active, _reason = ProjectSecrets(control_plane, project_id, notes_path), None
    return _active


def active() -> ProjectSecrets | None:
    return _active


def unavailable_reason() -> str | None:
    return _reason


def process_env() -> dict[str, str]:
    """The environment of an OpenCode server Sage starts."""
    s = _active
    return s.env() if s is not None else dict(os.environ)


def ensure_app_key() -> None:
    """Best-effort: a preview or a publish must not fail because this could not be written."""
    s = _active
    if s is None:
        return
    try:
        s.ensure_app_key()
    except Exception as e:
        log.warning("secrets: could not ensure %s (%s)", APP_KEY, type(e).__name__)


def preview_env() -> dict[str, str]:
    """The environment of a preview Sage starts. The Built App's cookie key exists before it runs."""
    ensure_app_key()
    return process_env()


def known_values() -> dict[str, str]:
    """name -> value, as last read. What the shim hides from the model."""
    s = _active
    return s.values() if s is not None else {}
