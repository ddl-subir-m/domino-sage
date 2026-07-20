"""AgentDriver — the harness seam (DESIGN.md Seam 3).

All OpenCode-specific detail lives behind this one adapter. The UI and feedback loop consume
the normalized AgentEvent union, never OpenCode's native shape. AgentConfig.model_base_url is
ALWAYS the enforcement shim — the only model path the driver knows.

Leak rule: nothing here leaks OpenCode concepts into the router or the shim.
"""
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol


@dataclass(frozen=True)
class AgentConfig:
    model_base_url: str          # ALWAYS the enforcement shim URL
    workspace: Path
    tool_allowlist: list[str] = field(default_factory=list)  # constrained shell for v1


@dataclass
class AgentEvent:
    kind: Literal["message", "file_edit", "tool_run", "phase", "error"]
    payload: dict


class Session(Protocol):
    id: str


class AgentDriver(Protocol):
    def start(self, workspace: Path, config: AgentConfig) -> Session: ...
    def send(self, session: Session, message: str) -> None: ...
    def events(self, session: Session) -> Iterator[AgentEvent]: ...
    def stop(self, session: Session) -> None: ...


class OpenCodeDriver:
    """Real driver. Driving contract captured 2026-07-20 (opencode-ai 1.18.4):

    Recommended: **server mode**. `opencode serve --port N` runs a headless HTTP server
    (optional basic auth via OPENCODE_SERVER_PASSWORD; --cors for our backend origin). Drive
    sessions/messages over its HTTP API (JS SDK: `@opencode-ai/sdk`) and subscribe to the
    `/event` SSE stream. Event envelope: `{"id","type","properties"}` with dotted type names
    (server.connected, session.*, message.*, ...) -> map `type` to our AgentEvent.kind.
    Point OpenCode at the shim by configuring the `sage-gateway` provider baseURL (opencode.json).

    Alternatives: `opencode run --format json` (raw JSON events per one-shot turn; --session to
    continue, --model provider/model, --dir workspace, -f file) for a subprocess-per-turn driver;
    or `opencode acp` (Agent Client Protocol).

    TODO(Phase 1): implement against server mode; capture the exact event.type list + payload
    fields live in a Domino workspace (Mac has no gateway access). Prove 100% of model calls hit
    config.model_base_url (already observed true in the 1.2 spike)."""

    def start(self, workspace: Path, config: AgentConfig) -> Session:
        raise NotImplementedError("Step 1.2: spawn OpenCode pointed at the shim base_url")

    def send(self, session: Session, message: str) -> None:
        raise NotImplementedError("Step 1.2")

    def events(self, session: Session) -> Iterator[AgentEvent]:
        raise NotImplementedError("Step 1.6: normalize OpenCode's event stream")

    def stop(self, session: Session) -> None:
        raise NotImplementedError("Step 1.2")
