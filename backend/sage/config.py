"""Sage's own settings: one `Settings` object per process (ONE-APP-PLAN.md §2.7, Phase 1).

`$SAGE_HOME/settings.json`, with the platform's own injected env vars overriding whatever a
person saved — those are facts about where this process is running, not a preference a stale file
should be able to override. Model routing defaults (`SAGE_MODEL_*`) stay in
`gateway/factory.py::_build_catalog`; this module is the connection half only: Domino host, token,
gateway, and the publish environment/tier ids.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path


def resolve_sage_home(env: dict[str, str] | None = None) -> Path:
    """Where Sage keeps `settings.json`, `brand.json` and (from Phase 2 on) project clones.

    `SAGE_HOME` wins outright — the explicit override tests and any deployment that mounts its own
    path need. Off Domino (no `DOMINO_API_HOST`) the default is `~/.sage`, a laptop's usual one-dir-
    per-tool convention. On Domino, ONE-APP-PLAN.md §2.1 wants a per-user path under a mounted
    `sage-home` Dataset when the App has one, else an ephemeral scratch dir — but resolving "does
    this App have that Dataset mounted, and as which user" is Phase 7 packaging work with a real
    App to test against. Until that lands, the safe default here is the ephemeral scratch dir on
    every Domino run: projects/settings re-clone or get re-entered rather than a session silently
    writing into a path nothing actually mounted.
    """
    env = env if env is not None else os.environ
    override = env.get("SAGE_HOME", "").strip()
    if override:
        return Path(override).expanduser()
    if env.get("DOMINO_API_HOST", "").strip():
        return Path("/tmp/sage-home")
    return Path.home() / ".sage"


def derive_gateway_url(host: str) -> str:
    """The Domino LLM Gateway's default base URL for a Domino API host (§2.1's "derived" row).

    Same "swap the internal host for the public apps.<host> one" move every other browser-facing
    link in this codebase already makes off `DOMINO_API_HOST` (`_manage_app_url`, `_gateway_ui_url`
    in `orchestrator/app.py`) — `host` here is normally the internal cluster address, not something
    a browser or an external caller can resolve on its own.
    """
    host = host.strip().rstrip("/")
    host = host.removeprefix("https://").removeprefix("http://")
    host = host.removeprefix("apps.")
    # The internal cluster address carries its own port (verified live: `:80`); the public
    # apps.<host> address never does, so a literal port copied across would be wrong rather than
    # merely superfluous.
    host = host.split(":", 1)[0]
    return f"https://apps.{host}/apps/llm_gateway/v1"


# Field name -> the env var that overrides it. Deliberately the platform's OWN injected names
# (verified present in a live Domino workspace, 2026-09-23) rather than a Sage-invented alias —
# DOMINO_USER_API_KEY is the account API key Domino itself puts in the environment, and the other
# three are the Environment/hardware ids Domino injects for the App this process runs as.
_ENV_OVERRIDES = {
    "domino_host": "DOMINO_API_HOST",
    "domino_token": "DOMINO_USER_API_KEY",
    "gateway_base_url": "GATEWAY_BASE_URL",
    "gateway_api_key": "GATEWAY_API_KEY",
    "publish_environment_id": "DOMINO_ENVIRONMENT_ID",
    "publish_hardware_tier_id": "DOMINO_HARDWARE_TIER_ID",
}

_SECRET_FIELDS = ("domino_token", "gateway_api_key", "git_token")


@dataclass(frozen=True)
class Settings:
    """The Connection section of the settings drawer, and the only place a Domino host/token/
    gateway config is read from outside an App's own injected env.

    Every field's unset value is `""`, never `None`, so the file round-trips as plain JSON with no
    null-handling anywhere else and an absent key in an old settings.json is indistinguishable from
    an unset one.
    """

    domino_host: str = ""
    domino_token: str = ""              # account API key (laptop PAT, or DOMINO_USER_API_KEY)
    gateway_base_url: str = ""
    gateway_api_key: str = ""           # dgw_ key — only when the gateway won't take domino_token
    git_token: str = ""                 # settings.git.token fallback (§2.1) for the git credential
    publish_environment_id: str = ""
    publish_hardware_tier_id: str = ""

    def gateway_url(self) -> str:
        """The gateway base URL this process calls: the explicit setting first, else derived from
        the Domino host (§2.1's "default derived from host")."""
        if self.gateway_base_url:
            return self.gateway_base_url
        return derive_gateway_url(self.domino_host) if self.domino_host else ""

    def as_dict(self) -> dict:
        return asdict(self)

    def redacted(self) -> dict:
        """What `GET /api/settings` answers: a secret field reports only whether it's set, never
        the value — the settings drawer's own "write-only, shown as set/unset" contract."""
        d = self.as_dict()
        for name in _SECRET_FIELDS:
            d[name] = bool(d[name])
        return d


def _settings_path(home: Path) -> Path:
    return home / "settings.json"


def load(home: Path, env: dict[str, str] | None = None) -> Settings:
    """`$SAGE_HOME/settings.json`, with env vars overriding per field (`_ENV_OVERRIDES`).

    A missing or unreadable file is the same as an empty one — first run, or a laptop that has
    never saved settings — not an error; env vars (and then the drawer, via `save`) are what fill
    it in.
    """
    env = env if env is not None else os.environ
    data: dict = {}
    path = _settings_path(home)
    if path.is_file():
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            data = {}
    valid_fields = {f.name for f in fields(Settings)}
    kwargs = {k: v for k, v in data.items() if k in valid_fields and isinstance(v, str)}
    settings = Settings(**kwargs)
    overrides = {}
    for field_name, var in _ENV_OVERRIDES.items():
        value = env.get(var, "").strip()
        if value:
            overrides[field_name] = value
    return replace(settings, **overrides) if overrides else settings


def save(home: Path, settings: Settings) -> None:
    """Write `settings.json`, creating `$SAGE_HOME` on first save."""
    home.mkdir(parents=True, exist_ok=True)
    _settings_path(home).write_text(json.dumps(settings.as_dict(), indent=2) + "\n")
