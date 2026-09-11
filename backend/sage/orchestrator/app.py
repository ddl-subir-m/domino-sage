"""Orchestrator app — one API over the assembled builder (SPEC C1).

One ASGI app on one port (Phase 1): project lifecycle + model control + the /v1 shim OpenCode
targets, with the preview proxy (active project's Vite dev server, HTTP + HMR) mounted under
`/preview`. Everything is served under Domino's proxy path prefix (rewrite:false preserves it); a
tiny ASGI middleware strips that prefix so bare-registered routes match, and Vite bakes the same
prefix into its `base` so the preview round-trips through the one port. Prefix is empty locally.

Run:  uv run python -m sage.orchestrator.app
"""
from __future__ import annotations

import collections
import concurrent.futures
import contextlib
import logging
import os
import queue
import threading
import time
from mimetypes import guess_type
from pathlib import Path
from urllib.parse import quote

from dotenv import load_dotenv

load_dotenv()

from fastapi import Body, FastAPI, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    Response,
    StreamingResponse,
)
from starlette.concurrency import run_in_threadpool
from starlette.staticfiles import StaticFiles

_WB = Path(__file__).resolve().parents[1] / "workbench"
_UI = _WB / "index.html"
_DOOR_UI = _WB / "door.html"
_FONT = Path(__file__).resolve().parents[1] / "ui" / "fonts" / "inter-latin-var.woff2"

from .. import timing
from ..assets.provider import DominoAssetProvider, UnconfiguredAssetProvider
from ..feedback.runner import FeedbackRunner
from ..gateway.client import (
    DEFAULT_SIDECAR_URL,
    GatewayUpstreamError,
    bearer_from_authorization,
    bind_viewer_token,
    jwt_identity,
    sidecar_token,
    static_token,
    viewer_token,
)
from ..gateway.factory import build_gateway
from ..gateway.open_models import OPEN_WEIGHT_MODELS
from ..preview.prefix import domino_base_prefix, domino_project_label, proxy_is_app, publish_available
from ..preview.proxy import make_preview_app
from ..resources import health
from ..resources.bindings import (
    KIND_DATA_SOURCE,
    KIND_DATASET,
    KIND_LLM_ALIAS,
    KIND_MODEL_API,
)
from ..resources.model_api_credentials import CredentialRequired
from ..resources.provider import (
    DominoResourceProvider,
    FakeResourceProvider,
    ResourceUnavailable,
    data_library_ready,
)
from ..resources.publish_guard import PublishRefused
from ..resources.sensitivity import declared_turn_refusal_for_model
from ..router.models import Mode, ModelCatalog, Phase
from ..shim import keepalive as ka
from ..shim import refusal_scan
from ..workspace.threads import (
    ARTIFACT_COMMIT_MAX,
    ThreadStore,
    artifact_bytes,
    oversized_artifacts,
    safe_id,
)
from .brand import text as brand_text
from .describe import human_bytes
from .service import (
    AttachSourceMissing,
    AttachTooLarge,
    AttachWouldClobber,
    DataReferenced,
    DetachStopped,
    FolderActUnavailable,
    NotThisProjectsDataset,
    Orchestrator,
    PlanArchiveRefused,
    ResetBusy,
    ResourceNotBound,
    ResourceStillBound,
    TurnBusy,
    UploadUnavailable,
)

_feedback = FeedbackRunner()

log = logging.getLogger("sage.orchestrator")
logging.basicConfig(level=logging.INFO)

# In-memory tail of recent sage.* logs so /api/diag can surface what happened during a build (which
# port OpenCode dialed, "model call -> streaming (first byte Xs)", "gateway stream broke ...") without
# shell access in the deployed builder. Bounded; captures INFO+ from the whole sage.* hierarchy.
_LOG_RING: collections.deque[str] = collections.deque(maxlen=400)

# WARNING and above, kept a second time. The ring above holds 400 lines and a single build turn emits
# hundreds, so twice now the one line naming a failure had already scrolled out of it before anyone
# could read it — and absence from a ring that rolls proves nothing, which is how an investigation
# spends a whole reproduction learning nothing. A healthy turn warns rarely, so these 200 slots span
# many turns rather than part of one. Read it with /api/diag/log?warn=1.
_WARN_RING: collections.deque[str] = collections.deque(maxlen=200)


class _RingHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            line = self.format(record)
            _LOG_RING.append(line)
            if record.levelno >= logging.WARNING:
                _WARN_RING.append(line)
        except Exception:  # never let logging crash a request
            pass


_ring = _RingHandler()
_ring.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s", "%H:%M:%S"))
_ring.setLevel(logging.INFO)
_sage_log = logging.getLogger("sage")
_sage_log.addHandler(_ring)
_sage_log.setLevel(logging.INFO)


def _sage_rev() -> str | None:
    """Short git HEAD of the deployed Sage checkout — lets /api/diag confirm which code is running."""
    import subprocess
    home = os.environ.get("SAGE_APP_HOME", "/opt/sage")
    try:
        out = subprocess.run(["git", "-C", home, "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=3, check=False)
        return out.stdout.strip() or None
    except Exception:
        return None


_SAGE_REV = _sage_rev()

_REPO = Path(__file__).resolve().parents[3]


def _opencode_project_dir() -> Path:
    """The cwd we give the OpenCode server, holding the one config it loads as PROJECT config.

    Project config outranks both the custom source (`OPENCODE_CONFIG`) and the global one, but it
    is resolved off the git root of the SESSION directory, not off the server's cwd — measured live
    on 05fded1, see the reopened #199. Every Sage session runs under the workspace volume, so this
    dir is never read as project config and neither was `/opt/sage`. The voicing reaches OpenCode
    through the global copy instead.

    The dir is kept because two smaller claims do hold: the unvoiced `/opt/sage/opencode.json` is
    out of reach of any session that did start there, and a checked-out worktree keeps a clean
    `git status`.

    Resolved per call rather than at import, so `HOME` is read when it is used.
    """
    return Path(os.path.expanduser("~/.config/sage-opencode"))


def _slot(var: str, default: str) -> str:
    """A catalog slot from the environment, treating blank as unset.

    `.get(var, default)` isn't enough: environment/Dockerfile promotes each of these to `ENV` so a
    Domino Environment Variable can reach the container, and an ARG nobody filled in lands as the
    empty STRING, not as absent. That would send "" to the gateway as a model name.
    """
    return os.environ.get(var, "").strip() or default


def _build_catalog() -> ModelCatalog:
    return ModelCatalog(
        sovereign_plan=_slot("SAGE_MODEL_SOVEREIGN_PLAN", "qwen-2-5"),
        sovereign_implement=_slot("SAGE_MODEL_SOVEREIGN_IMPLEMENT", "qwen-2-5"),
        sovereign_ask=_slot("SAGE_MODEL_SOVEREIGN_ASK", "qwen-2-5"),
        plan=_slot("SAGE_MODEL_PLAN", "gpt-5.4"),
        implement=_slot("SAGE_MODEL_IMPLEMENT", "bedrock-qwen3-coder"),
        ask=_slot("SAGE_MODEL_ASK", "sonnet"),
    )


def _domino_api_token():
    """Bearer for the Domino API (datasets, Data Sources, Model APIs).

    Account API key if set, otherwise the workspace sidecar at :8899.
    """
    key = os.environ.get("DOMINO_API_KEY")
    return static_token(key) if key else sidecar_token(
        os.environ.get("GATEWAY_TOKEN_URL", DEFAULT_SIDECAR_URL)
    )


def _build_assets():
    """Domino datasets when DOMINO_API_HOST is set, else unlistable — never the in-memory fake."""
    api_host = os.environ.get("DOMINO_API_HOST", "").strip()
    if not api_host:
        log.info("no DOMINO_API_HOST — Datasets unlistable (set the public Domino API host to list them)")
        return UnconfiguredAssetProvider()
    return DominoAssetProvider(api_host, _domino_api_token())


def _build_control_plane():
    """A Domino control plane for Publish / Stop when this builder runs on Domino (DOMINO_API_HOST +
    the Environment/hardware ids Domino injects), else None so those endpoints report a clear
    "only on Domino" error instead of crashing local/fake runs."""
    api_host = os.environ.get("DOMINO_API_HOST")
    env_id = os.environ.get("DOMINO_ENVIRONMENT_ID")
    tier_id = os.environ.get("DOMINO_HARDWARE_TIER_ID")
    if not (api_host and env_id and tier_id):
        log.info("no DOMINO_API_HOST/ENVIRONMENT_ID/HARDWARE_TIER_ID — Publish/Stop disabled (local run)")
        return None
    from ..provision.domino import DominoControlPlane

    token = sidecar_token(os.environ.get("GATEWAY_TOKEN_URL", DEFAULT_SIDECAR_URL))
    return DominoControlPlane(
        api_host,
        token,
        environment_id=env_id,
        environment_revision_id=os.environ.get("DOMINO_ENVIRONMENT_REVISION_ID"),
        hardware_tier_id=tier_id,
        builder_tool=os.environ.get("SAGE_BUILDER_TOOL", "sageBuilder"),
        git_host=os.environ.get("SAGE_GIT_HOST", "github.com"),
    )


def _build_provision_service(control_plane):
    """A ProvisionService for this container, or None when this container can't provision.

    Both Workbench roles need one (ADR-0004). The published App provisions the viewer's Default
    Project; a Sage Builder creates further Projects and attaches this viewer's builder in them. So
    the gate is capability, not role: a Domino control plane, and a git host Sage has an adapter for.

    Repos are created with the container's own mounted git credential, the same one the seed push
    uses. If that credential belongs to the publisher while the sidecar is the viewer, the Project
    still lands correctly — the Domino project is created as the viewer, and only the GitHub repo
    carries the publisher's account. That seam is the platform's to close, not a reason to hand
    people a scratch directory.

    The credential itself is read lazily, per call: a container with no HTTPS credential still starts
    and still serves everything that doesn't provision, and says what to add when something does.
    """
    if control_plane is None:
        log.info("no Domino control plane — Sage can't create or attach Projects (local run)")
        return None
    from ..provision import credentials
    from ..provision.github import GitHubProvider
    from ..provision.service import ProvisionService

    host = os.environ.get("SAGE_GIT_HOST", "github.com").strip()
    provider = credentials.detect_provider(host)
    if provider != "github":
        log.warning("git host %s resolves to provider %r; Sage provisions GitHub only", host, provider)
        return None

    def token_provider() -> str:  # shared by repo create + seed push; in-memory, never logged
        tok = credentials.extract_token(host)
        if not tok:
            # `host` rides in as a value: it is the literal the credential is stored under,
            # and `Account Settings > Git Credentials` is the platform's own menu path, which
            # Sage renames no more than it renames the page it is sending them to.
            raise RuntimeError(brand_text(
                "no HTTPS git credential for {host} in this container "
                "(an SSH-key credential can't be extracted). Add an HTTPS Git credential under "
                "Account Settings > Git Credentials, then restart {assistantName}.",
                host=host,
            ))
        return tok

    return ProvisionService(
        control_plane,
        GitHubProvider(token_provider=token_provider),
        Path(os.environ.get("SAGE_TEMPLATE", _REPO / "template" / "react-vite")),
        push_token_provider=token_provider,
    )


def _build_door(service, control_plane):
    """The Workbench door (ADR-0004), or None when this process is not one.

    Only the published Workbench App is a door: a Sage Builder serves the Workbench chrome, and a
    laptop run has nothing to provision against — "no second local hub" is a decision, not a gap.
    """
    if not proxy_is_app():
        return None
    if service is None or control_plane is None:
        log.warning("Workbench App can't provision — the door has nothing to open")
        return None
    from ..provision.door import Door

    return Door(service, control_plane.whoami)


_gateway, GATEWAY_MODE = build_gateway()
# One builder is bound to one project volume. On Domino (git-based) that's the mounted repo at
# /mnt/code; locally it defaults to a scratch dir. The display id is the Domino project name.
_WORKSPACE_DIR = Path(os.environ.get("SAGE_WORKSPACE_DIR", _REPO / "backend" / "workspaces" / "app"))


def _gateway_ui_url(project_label: str | None) -> str | None:
    """Browser URL of the gateway's Usage & Cost dashboard, or None when there's nothing to link to.

    Sage doesn't meter spend itself — it tags calls (`sage-project`) and sends you here, because only
    the gateway can price a call correctly (per-alias custom rates live in its DB). The inference
    base URL is the same public Domino apps URL a browser can reach, minus the trailing /v1; the
    override exists for deployments where those differ.

    The dashboard deep-links, so the link arrives already filtered to this deployment rather than
    dropping you on an unfiltered page with instructions to go find yourself in it:

        /#usage?tag=sage-project%3D<owner>%2F<project>

    Without a project label we can't filter, so the link falls back to the unfiltered view.

    None in fake/openai mode: that traffic never reaches the Domino gateway, so the dashboard would
    have nothing to show and the link would read as broken rather than empty.
    """
    explicit = os.environ.get("SAGE_GATEWAY_UI_URL", "").strip()
    base = explicit or (os.environ.get("GATEWAY_BASE_URL", "").strip() if GATEWAY_MODE == "domino" else "")
    if not base:
        return None
    root = base.rstrip("/").removesuffix("/v1").rstrip("/")
    if not project_label:
        return root + "/#usage"
    # quote(safe="") so the "=" joining key to value and the "/" in "<owner>/<project>" both survive
    # as data — unescaped they'd read as a query separator and a path segment.
    tag = quote(f"sage-project={project_label}", safe="")
    return f"{root}/#usage?tag={tag}"


_MANAGE_APP_PATH = "/apps/sage-manage"


def _manage_app_url() -> str | None:
    """Where the platform bar sends a person to Manage, or None when this deployment has no Manage.

    Manage is a Domino App of its own, not a mode inside the Workbench — it reads across every
    project and detects an admin persona, neither of which the Workbench's project scope can do.
    So the platform bar links OUT to it rather than routing in.

    Host-RELATIVE, the same rule every other main-host link here follows (`app_manage_url`,
    `workspace_open_url`, `_open_url`): DOMINO_API_HOST is the INTERNAL cluster address
    (nucleus-frontend…), so a URL built from it is not one a browser can open. The browser resolves
    this path against the origin it was served from, which is by definition reachable — the UI drops
    a leading `apps.` first, since the published Workbench App is served from apps.<host> while
    Manage lives on the main host.

    SAGE_MANAGE_URL replaces it whole, for a Manage deployed somewhere this grammar does not reach.

    None off-Domino: a laptop run has no Manage App behind that path, and a link that 404s reads as
    broken rather than absent. Being under Domino's proxy — a workspace prefix, or App mode — is
    what says otherwise, because that is the same fact as "this page is served from a Domino host".
    """
    explicit = os.environ.get("SAGE_MANAGE_URL", "").strip()
    if explicit:
        return explicit.rstrip("/")
    return _MANAGE_APP_PATH if (proxy_is_app() or domino_base_prefix()) else None


def _browser_gateway_base() -> str | None:
    """The gateway URL to pin into a Built App's own source, for its browser code to call (#7).

    The very URL Sage routes through, unchanged. A published app is served from the gateway's own
    host (`apps.<domino-host>`), so that absolute URL is same-origin there and the viewer's Domino
    session cookie authenticates the call — no key in the page, no server hop, and the gateway
    attributes the spend to whoever opened the app.

    SAGE_GATEWAY_UI_URL wins when it is set, because that variable already means "where a browser
    reaches this gateway" — it exists for the dashboard link. GATEWAY_BASE_URL is where SAGE's
    server reaches it, which on cloud-dogfood is the same external URL, but a deployment that routes
    Sage over an internal cluster address would otherwise pin one no viewer's browser can resolve.

    None outside `domino` mode, the same gate _build_resources uses. In `openai` mode each model
    routes to its own vendor behind an API key, and in `fake` mode there is no gateway at all;
    pinning either into an app would ship a call the browser cannot make. The app then reports
    having no model, which is true.
    """
    if GATEWAY_MODE != "domino":
        return None
    base = (os.environ.get("SAGE_GATEWAY_UI_URL", "").strip()
            or os.environ.get("GATEWAY_BASE_URL", "").strip())
    if not base:
        return None
    # Either variable may be given with or without the API suffix; the helper appends paths to it.
    return base.rstrip("/").removesuffix("/v1").rstrip("/") + "/v1"


def _build_resources():
    """LLM Aliases from the Domino LLM Gateway when Sage is pointed at one, else an in-memory fake.

    Keyed on the gateway MODE, not on the URL alone: `openai` mode has no Domino gateway at all
    (each model routes to its own vendor), so asking a control plane there would 404.
    """
    base = os.environ.get("GATEWAY_BASE_URL", "").strip()
    if GATEWAY_MODE != "domino" or not base:
        return FakeResourceProvider()
    key = os.environ.get("GATEWAY_API_KEY", "")
    token = static_token(key) if key else sidecar_token(
        os.environ.get("GATEWAY_TOKEN_URL", DEFAULT_SIDECAR_URL)
    )
    # Model APIs come off the Domino API instead, on its own bearer — the same recipe _build_assets
    # uses, because it is the same host and the same token. Absent (Sage pointed at a Domino gateway
    # from outside Domino), the provider reports Model APIs as unlistable rather than as none.
    api_host = os.environ.get("DOMINO_API_HOST", "").strip()
    api_token = _domino_api_token()
    return DominoResourceProvider(base, token, api_host=api_host, api_token_provider=api_token)


_COST_PROJECT_LABEL = domino_project_label(fallback=_WORKSPACE_DIR.name)
_control_plane = _build_control_plane()
_provision = _build_provision_service(_control_plane)
_door = _build_door(_provision, _control_plane)
orchestrator = Orchestrator(
    workspace_dir=_WORKSPACE_DIR,
    template=Path(os.environ.get("SAGE_TEMPLATE", _REPO / "template" / "react-vite")),
    gateway=_gateway,
    catalog=_build_catalog(),
    project_id=os.environ.get("DOMINO_PROJECT_NAME", _WORKSPACE_DIR.name),
    # Not SAGE_OPENCODE_CWD: that names the checked-in *source*, and a server run there loads it as
    # project config (#199). This is the server's cwd and the dir `_opencode_base_port` reads, so
    # /api/diag reports the port of the file OpenCode really dials.
    opencode_cwd=_opencode_project_dir(),
    assets=_build_assets(),
    resources=_build_resources(),
    domino_project_id=os.environ.get("DOMINO_PROJECT_ID"),
    control_plane=_control_plane,
    domino_project_name=os.environ.get("DOMINO_PROJECT_NAME"),
    domino_run_id=os.environ.get("DOMINO_RUN_ID"),
    cost_project_label=_COST_PROJECT_LABEL,
    gateway_ui_url=_gateway_ui_url(_COST_PROJECT_LABEL),
    manage_url=_manage_app_url(),
    browser_gateway_base=_browser_gateway_base(),
    # Which authority a slot resolves against, so the turn-time slot check (#125) runs only where a
    # gateway actually holds the Alias list — the same gate `_run_slot_preflight` applies below.
    gateway_mode=GATEWAY_MODE,
)

# Preflight of Sage's own model slots (#17). Loud but not fatal: a slot resolves against the LLM
# Gateway, so a gateway blip or one de-registered Alias would otherwise be enough to stop the
# builder from starting at all — and a Domino App that exits explains nothing to the user who opened
# it. An ERROR log, /healthz, and a banner in the builder cover the maintainer and the user; the
# build that routes to the broken slot still fails, but now with a reason known before it started.
#
# On a thread so "at startup" cannot mean "after the gateway's timeout": this is two HTTP calls, and
# blocking module import on them would hold up the port Domino is waiting for. `pending` is the
# honest answer for the first moment or so after boot.
PREFLIGHT_SLOTS: dict = {"state": "pending", "error": None, "slots": []}


def _run_slot_preflight() -> None:
    global PREFLIGHT_SLOTS
    if GATEWAY_MODE == "openai":
        # Each model routes to its own vendor here, so there is no LLM Gateway holding an Alias list
        # to resolve against. Checking anyway would report every slot as missing.
        PREFLIGHT_SLOTS = {"state": "skipped", "error": None, "slots": [],
                           "reason": "openai gateway mode has no LLM Gateway to resolve slots against."}
        return
    result = orchestrator.preflight_slots()
    for problem in result["slots"]:
        log.error("preflight: %s", problem["message"])
    if result["state"] == "unreachable":
        # "could not finish", not "could not check": since #21 this state is also reached when the
        # slots themselves resolved and only the endpoint listing behind them failed, and the error
        # string is what says which of the two happened.
        log.warning("preflight: could not finish checking Sage's model slots — %s", result["error"])
    elif result["state"] == "ok":
        log.info("preflight: every configured model slot resolves on the gateway")
    PREFLIGHT_SLOTS = result


def _warm_opencode() -> None:
    """Boot the OpenCode server now rather than on the first turn.

    `_ensure_opencode` starts a Node server the first time anything asks for one, so that cost
    landed on whichever turn came first — which is the person's opening message, the one they have
    the least patience for and the least reason to expect a wait on. Nothing downstream changes:
    the same lazy start still runs if this thread lost a race or failed.

    Non-fatal by design, like the preflight above. A builder that cannot boot OpenCode has a
    bigger problem than a cold first turn, and it should report that when a turn asks, not by
    refusing to serve the port Domino is waiting on.
    """
    try:
        orchestrator._ensure_opencode()
        log.info("warm: OpenCode server is up before the first turn")
    except Exception as e:
        log.warning("warm: OpenCode did not start ahead of the first turn — %s", e)


@contextlib.asynccontextmanager
async def _lifespan(app: FastAPI):
    """Tear down the child processes however this app was launched.

    run() below wires shutdown for one launcher: `python -m sage.orchestrator.app`. Every other way
    into this module — `uvicorn sage.orchestrator.app:control_app`, one of its --reload workers,
    any embedding host — got no teardown at all. OpenCode spawns with `start_new_session=True`, so
    it survives the exit rather than dying with it, and each of those exits left one orphaned
    `opencode serve` holding ~80 MB. Hanging the teardown on the app means the ASGI server runs it,
    whichever server that is.

    Startup is the other half of the same rule: the preflight and the warm-up used to run on
    threads started at MODULE IMPORT, which is one event the teardown below can never be paired
    with. Importing this module — a test collecting, a tool reading `control_app` — spawned a real
    `opencode serve` that nothing then stopped, one per import. Booting them here means every
    launcher that starts them is a launcher that also stops them. run()'s
    `_install_opencode_config` now lands before the warm-up too, instead of racing it.
    """
    def _boot(step) -> None:
        # Neither step is worth the port Domino is waiting on, so a thread that dies logs and
        # leaves the lazy paths to report the problem when a turn asks.
        try:
            step()
        except Exception:
            log.exception("startup: %s did not finish", step.__name__)

    for step, name in ((_run_slot_preflight, "sage-preflight-slots"),
                       (_warm_opencode, "sage-warm-opencode")):
        threading.Thread(target=_boot, args=(step,), name=name, daemon=True).start()
    yield
    orchestrator.shutdown()


control_app = FastAPI(title="sage orchestrator", lifespan=_lifespan)

# The Domino proxy path prefix, single-sourced from env (empty locally). Baked into Vite's `base`
# AND stripped from incoming request paths so the bare-registered routes below keep matching.
BASE_PREFIX = domino_base_prefix()


class _PrefixMiddleware:
    """Record Domino's proxy prefix as `root_path` so bare-registered routes match.

    Domino forwards the full prefixed path (rewrite:false). Starlette routes on
    `get_route_path = path - root_path` at every level (including nested Mounts, which extend
    root_path rather than rewrite the path), so we set `root_path` and leave `path` INTACT — do NOT
    strip the path, or the /preview Mount double-counts the prefix. No-op when the prefix is empty
    (local dev). Domino also sends the prefix in `x-script-name`; a one-time mismatch is logged.
    """

    # Routes served to callers INSIDE the container over localhost, which never cross Domino's proxy
    # and so correctly carry no prefix: the shim's /v1 (every OpenCode model call), /healthz, and
    # the Live read MCP server OpenCode dials (ADR-0041).
    # They must not trip the warning — it fires once per process, so one internal call would
    # otherwise spend it seconds after boot and leave a REAL prefix misconfiguration silent forever.
    #
    # /mcp/ arrived after this list and was not added to it, which is exactly the loss the sentence
    # above predicted: OpenCode connecting spent the one warning on a correct request, and a real
    # prefix misconfiguration in that workspace could never be reported again. Nothing broke — the
    # route matches either way, since no prefix is what a loopback call is supposed to carry — so
    # the whole cost was a diagnostic that had already been fired.
    _UNPROXIED = ("/v1/", "/healthz", "/mcp/")

    def __init__(self, app, prefix: str) -> None:
        self._app = app
        self._prefix = prefix
        self._warned = False

    async def __call__(self, scope, receive, send):
        if self._prefix and scope["type"] in ("http", "websocket"):
            path = scope.get("path", "")
            if path == self._prefix or path.startswith(self._prefix + "/"):
                scope = dict(scope)
                scope["root_path"] = self._prefix
            elif not self._warned and not path.startswith(self._UNPROXIED):
                self._warned = True
                log.warning("prefix %r not found in request path %r", self._prefix, path)
        await self._app(scope, receive, send)


control_app.add_middleware(_PrefixMiddleware, prefix=BASE_PREFIX)


class _ViewerIdentityMiddleware:
    """Bind this request's own viewer JWT from Authorization, and clear it when the request ends.

    `/api/me` is the only reader. Listings and model calls use the sidecar, on the door as on a
    Sage Builder, so no path inherits a viewer from an earlier request any more (#91).

    The `finally` is the load-bearing half: a ContextVar left set on a pooled worker would hand one
    viewer's identity to the next request that arrives with no Authorization header — the leak
    ef86bdc closed, coming back by the back door.
    """

    def __init__(self, app) -> None:
        self._app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        headers = {k.decode("latin1").lower(): v.decode("latin1") for k, v in scope.get("headers", [])}
        bind_viewer_token(bearer_from_authorization(headers.get("authorization")))
        try:
            await self._app(scope, receive, send)
        finally:
            bind_viewer_token(None)


control_app.add_middleware(_ViewerIdentityMiddleware)


# Added last, so it sits OUTSIDE the two above and sees every response they let through.
#
# The JSON here is mostly prose and source: the Build log answers a single read at megabytes and
# gzips ~6x, and nothing on the Workbench was compressed before, because Domino's nginx gzips
# text/html and not application/json. SSE is what makes this a decision rather than a default —
# `gzip` holds bytes back until it has a block to write, which would stall a build stream into
# silence — and starlette excludes `text/event-stream` for exactly that reason. Every streaming
# route here declares it (`_turn_sse`, the shim's `/v1`), which is what keeps that exclusion true.
#
# Level 6 rather than the library's 9: on a megabyte of log the last three levels cost more CPU
# than the bytes they save are worth over a proxy on the same host.
control_app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=6)


@control_app.get("/")
def ui() -> HTMLResponse:
    """The Workbench shell (Chat / Build), or the door.

    In the published Workbench App this is the door (ADR-0004): it does not run Chat or Build
    against the App's scratch checkout — it sends the viewer to their own Sage Builder, where their
    files live in a real git Project. A Sage Builder serves the shell itself, unchanged.

    Both pages come through here, so the brand pack is substituted here and reaches both (#116).
    Server-side, never from JS on boot: the browser paints whatever the HTML literally said first,
    and the flash lands on the door — the first page a viewer ever sees — where it would show our
    name over a partner's product. `text()` leaves an unknown token as written, so a page keeps
    booting whatever the pack says — and that is also what protects the rest of the document, which
    is templated wholesale: a `{word}` in the CSS or the inline JS resolves only if it happens to
    name a pack key, and otherwise comes through untouched.

    no-store so the current HTML is always served.
    """
    from .brand import text as brand_text

    page = _DOOR_UI if proxy_is_app() else _UI
    return HTMLResponse(brand_text(page.read_text(encoding="utf-8")),
                        headers={"Cache-Control": "no-store"})


@control_app.post("/api/door")
async def door_open() -> JSONResponse:
    """Find or create this viewer's Default Project, open their Sage Builder, and say where to go.

    Slow on purpose the first time — creating the repo, seeding it, creating the Domino project and
    launching the builder is a minute of real work — so the door page holds a progress line rather
    than the browser holding a blank tab.
    """
    if _door is None:
        return JSONResponse(
            status_code=503,
            content={"error": brand_text(
                "{assistantName} can't reach {platformName} from this App, so it can't open your "
                "{assistantName} Builder. Check the App's Environment has the {platformName} API "
                "host and a Git credential, then restart it."
            )},
        )
    try:
        target = await run_in_threadpool(_door.ensure_default)
    except Exception as e:
        log.exception("door: couldn't open the viewer's Sage Builder")
        return JSONResponse(status_code=502, content={"error": str(e)})
    return JSONResponse(content={
        "open_url": target.open_url,
        "running": target.running,
        "launched": target.launched,
        "created": target.created,
        "project": {"id": target.project.id, "name": target.project.name},
    })


@control_app.get("/api/door/status")
async def door_status(project_id: str, workspace_id: str | None = None) -> JSONResponse:
    """Whether that builder's session is up yet, and the URL to open once it is.

    A launched or resumed workspace says `Started` before its session runs, so the door page polls
    here rather than sending the viewer to a page that isn't ready.
    """
    if _door is None:
        return JSONResponse(status_code=503, content={"error": "no door in this container"})
    try:
        return JSONResponse(content=await run_in_threadpool(_door.status, project_id, workspace_id))
    except Exception as e:
        log.exception("door: couldn't read the builder's status")
        return JSONResponse(status_code=502, content={"error": str(e)})


@control_app.get("/api/projects")
async def list_projects() -> JSONResponse:
    """The Sage Projects this viewer can open, for the scope chip (#47).

    sage-* only, and not by a filter of ours: the control plane's own list keeps a Domino project
    only when its git repo carries the `sage-` prefix, so an ordinary Domino project this viewer
    owns never appears in the chip.

    Empty off Domino, and empty is honest — a laptop run has no Projects to switch between. The
    chip still shows the project this builder is bound to; that entry comes from /api/project.
    """
    if _provision is None:
        return JSONResponse(content={"items": [], "provisioning": False})
    current = os.environ.get("DOMINO_PROJECT_ID", "")
    try:
        projects = await run_in_threadpool(_provision.list_apps)
    except Exception as e:
        log.exception("chip: couldn't list this viewer's Sage Projects")
        return JSONResponse(status_code=502, content={"error": str(e)})
    return JSONResponse(content={
        "items": [{"id": p.id, "name": p.name, "current": p.id == current} for p in projects],
        "provisioning": True,
    })


@control_app.post("/api/projects")
async def create_project(body: dict) -> JSONResponse:
    """Create a Project from a typed name and start this viewer's Sage Builder in it (#46).

    A minute of real work: a private sage-* repo, the template seeded and pushed, a git-based Domino
    project, then the builder. The typed name is not the Domino project name — it rides into the
    repo as the chip overlay and into the project's description — so the name a person picks can be
    anything without breaking the lookup Sage finds Projects by.
    """
    if _provision is None:
        return JSONResponse(
            status_code=503,
            content={"error": brand_text(
                "{assistantName} can't reach {platformName} from this container, so it can't "
                "create a {project}. This build runs against the project it is bound to."
            )},
        )
    from ..provision.service import workspace_is_running

    name = str((body or {}).get("name") or "").strip()
    if not name:
        return JSONResponse(status_code=400, content={"error": "Name the project to create it."})
    try:
        created = await run_in_threadpool(_provision.create_app, name)
    except Exception as e:
        log.exception("chip: couldn't create the Project %r", name)
        return JSONResponse(status_code=502, content={"error": str(e)})
    return JSONResponse(content={
        "open_url": created.open_url,
        "running": workspace_is_running(created.workspace),
        "launched": True,
        "workspace_id": (created.workspace or {}).get("id"),
        "project": {"id": created.project.id, "name": created.project.name},
    })


@control_app.post("/api/projects/{project_id}/open")
async def open_project(project_id: str) -> JSONResponse:
    """Attach THIS viewer's Sage Builder in that Project and say where to send the browser (#47).

    Switching Project means leaving this container for another one, so the answer is a URL, not new
    state here. Reuse if theirs is running, resume if it is stopped, create if they have none — and
    a collaborator's builder in the same Project is left alone (see ProvisionService.open_app).
    """
    if _provision is None:
        return JSONResponse(
            status_code=503,
            content={"error": brand_text(
                "{assistantName} can't reach {platformName} from this container, so it can't open "
                "another {project}. This build runs against the project it is bound to."
            )},
        )
    try:
        who = await run_in_threadpool(_control_plane.whoami)
        opened = await run_in_threadpool(_provision.open_app, project_id, owner=who.name)
    except Exception as e:
        log.exception("chip: couldn't attach a Sage Builder in project %s", project_id)
        return JSONResponse(status_code=502, content={"error": str(e)})
    return JSONResponse(content={
        "open_url": opened["open_url"],
        "running": opened["running"],
        "launched": opened["launched"],
        "workspace_id": (opened["workspace"] or {}).get("id"),
    })


@control_app.get("/api/projects/status")
async def project_status(project_id: str, workspace_id: str | None = None) -> JSONResponse:
    """Whether that builder's session is up yet, and the URL to open once it is.

    Same reason the door polls: a launched or resumed workspace says `Started` before its session
    runs, and sending the browser in then lands it on a page that isn't ready.
    """
    if _provision is None:
        return JSONResponse(status_code=503, content={"error": "this container can't open Projects"})
    try:
        who = await run_in_threadpool(_control_plane.whoami)
        content = await run_in_threadpool(
            _provision.workspace_status, project_id, workspace_id, owner=who.name
        )
    except Exception as e:
        log.exception("chip: couldn't read the builder's status in project %s", project_id)
        return JSONResponse(status_code=502, content={"error": str(e)})
    return JSONResponse(content=content)


@control_app.get("/api/gallery")
async def gallery() -> JSONResponse:
    """The Built Apps this viewer may open, published from Sage Builder sessions (#48).

    Not the chip's Project list: a sage-* Project with nothing published has nothing to show here,
    and an empty Gallery is an empty Gallery. Opening a card opens that App — it does not move the
    Workbench to another Project.
    """
    if _provision is None:
        return JSONResponse(content={"items": [], "provisioning": False})
    try:
        apps = await run_in_threadpool(_provision.list_built_apps)
    except Exception as e:
        log.exception("gallery: couldn't list this viewer's Built Apps")
        return JSONResponse(status_code=502, content={"error": str(e)})
    return JSONResponse(content={
        "items": [{"id": a.id, "name": a.name, "url": a.url, "status": a.status,
                   "project": a.project_name or a.project_id} for a in apps],
        "provisioning": True,
    })


@control_app.get("/fonts/inter-latin-var.woff2")
def font() -> FileResponse:
    """Inter, from Sage's own origin (#19). See the @font-face block in the page for the why.

    A year, immutable, where the page above it is no-store — both correct: the HTML changes on every
    deploy and these bytes never do, because replacing the font means a new filename here and in the
    page that asks for it.
    """
    return FileResponse(
        _FONT,
        media_type="font/woff2",  # pinned: mimetypes has no answer for .woff2 on a bare Linux image
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@control_app.get("/healthz")
def healthz() -> dict:
    # gateway_mode is authoritative: "openai" means the mechanism is being exercised against a
    # generic provider, NOT the real Domino sovereign gateway.
    return {
        "ok": True,
        "project": orchestrator._project_id,
        "gateway_mode": GATEWAY_MODE,
        # True when this builder can Publish/Stop through the Domino control plane (Sage Builder
        # workspace on an app repo). The Workbench App hides those controls — publishing would ship
        # Sage itself, not a Built App.
        "domino": orchestrator._control_plane is not None and publish_available(orchestrator._wm.path),
        "open_weight_models": [
            {"id": m.id, "provider": m.provider} for m in OPEN_WEIGHT_MODELS
        ] if GATEWAY_MODE == "openai" else [],
        # Whether Sage's own configured model slots resolve on the gateway (#17). Here rather than
        # on its own route because /healthz is already the one call that answers "is this builder
        # correctly wired", and the UI already makes it on load.
        "preflight_slots": PREFLIGHT_SLOTS,
    }


@control_app.get("/api/brand")
def brand() -> dict:
    """Resolved Workbench chrome + voice. OEM overlay, or the Domino default."""
    from .brand import load as load_brand
    return load_brand()


@control_app.put("/api/brand")
async def save_brand(request: Request) -> JSONResponse:
    """Appearance, from Account settings (ADR-0044): the theme and the two names.

    Only the keys `brand.WRITABLE_KEYS` names. The logo, the nouns and the peer products stay the
    OEM's to bake — they are what ADR-0014 built a lint and an image allowlist around, and a text
    field on a settings panel is not where that gets re-litigated.

    A name change re-voices OpenCode, which means restarting it, which is why this can be refused
    while a build runs. A theme change never is: it is CSS, and nothing behind the browser reads it.
    """
    from .brand import WRITABLE_KEYS, save_override
    from .brand import load as load_brand

    body = await request.json()
    if not isinstance(body, dict):
        return JSONResponse(status_code=400, content={"error": "expected an object"})
    patch = {key: body[key] for key in WRITABLE_KEYS if key in body}
    if not patch:
        return JSONResponse(status_code=400, content={"error": "nothing to change"})
    # Read BEFORE the write, so "did the voice change" is asked of what the pack resolved to and
    # not of what the form happened to send. A field submitted unchanged is not a rename, and
    # restarting OpenCode for one would make saving a theme cost somebody their warm agent.
    was = load_brand()["assistantName"]
    try:
        pack = save_override(patch)
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    if pack["assistantName"] != was:
        source = Path(os.environ.get("SAGE_OPENCODE_CWD", _REPO))
        port = int(os.environ.get("SAGE_CONTROL_PORT", "8080"))
        try:
            await run_in_threadpool(
                orchestrator.revoice, lambda: _install_opencode_config(source, port))
        except TurnBusy:
            # 409, as everywhere else the turn lock refuses — but the sentence is not the service's
            # here, because the service's would read as a rename that failed. It did not: the name
            # is on disk and the screen is already wearing it, and what waits for the build is the
            # agent's own voice.
            return JSONResponse(status_code=409, content={"error": brand_text(
                "The new name is saved. A build is running, so {assistantName} keeps its old voice "
                "until that build finishes."
            )})
    return JSONResponse(content=pack)


@control_app.get("/api/me")
def me() -> dict:
    """Who the Workbench greets. Viewer JWT when extended identity forwarded one; else the
    container's injected username (publisher on an App, the workspace user in Sage Builder)."""
    ident = jwt_identity(viewer_token())
    return {
        "id": ident.get("id") or os.environ.get("DOMINO_USER_ID") or "me",
        "name": ident.get("name")
            or os.environ.get("DOMINO_USER_NAME")
            or os.environ.get("DOMINO_STARTING_USERNAME")
            or "You",
    }


def _git_credential_diag() -> dict:
    """Which checkouts hold a git credential for the provisioning host, secrets redacted.

    "no HTTPS git credential in this container" is otherwise unfalsifiable from a Builder, which has
    no terminal: it cannot tell an account with only an SSH key from a credential we asked the wrong
    directory for. Lengths only — never the token.
    """
    from ..provision import credentials

    host = os.environ.get("SAGE_GIT_HOST", "github.com").strip() or "github.com"
    try:
        return credentials.credential_probe(host)
    except Exception as e:  # a diagnostic must never be the thing that breaks the diagnostics page
        return {"host": host, "error": str(e)}


def _git_credential_list_diag() -> dict:
    """Which of the caller's platform Git credentials the create loop would try, in order (#157).

    `_git_credential_diag` above answers the container side. This is the API-list side, which had
    nothing — so a user whose Project create was refused could not see that Sage held three
    credentials and tried them in an order they cannot influence. No secret, and no fingerprint.
    """
    if _provision is None:
        return {"error": "not connected to the platform"}
    try:
        return _provision.git_credential_diag()
    except Exception as e:  # a diagnostic must never be the thing that breaks the diagnostics page
        return {"error": str(e)}


def _mcp_diag(control_port: int) -> dict:
    """Which MCP servers OpenCode was told about, and whether each one answers right now.

    OpenCode drops an MCP server it cannot reach SILENTLY: the tools are absent from the list the
    model is offered, nothing is logged, and the agent falls back on telling the person it cannot
    see their data — which is the transcript ADR-0041 exists to end. `agents` above already reports
    what OpenCode resolved from the same file; this is the half that had nothing, so a Live read
    that never arrived looked exactly like a model that chose not to call it.

    Two questions, because they fail apart. `configured` is what the file says, read from the GLOBAL
    copy — the slot `_install_opencode_config` explains is the one that does the work. `reachable`
    is an HTTP round trip to that URL asking for its tool list, which is what OpenCode itself does
    on connect: it catches the port the checked-in config names being the wrong one (the bug that
    made the URL rewrite grow its twin), and it catches the control port not yet serving when the
    OpenCode server started, which no rewrite can fix because nothing retries.

    Never raises. A diagnostic must never be the thing that breaks the diagnostics page.
    """
    import json

    src = Path(os.path.expanduser("~/.config/opencode")) / "opencode.json"
    try:
        servers = (json.loads(src.read_text()).get("mcp") or {})
    except Exception as e:
        return {"config": str(src), "error": str(e)}

    out = []
    for name, server in servers.items():
        server = server if isinstance(server, dict) else {}
        url = str(server.get("url") or "")
        row = {"name": name, "type": server.get("type"), "enabled": server.get("enabled"),
               "url": url}
        if url:
            # The check the silent drop actually turns on. `_install_opencode_config` rewrites this
            # port to the one this process serves, so a mismatch here means the rewrite did not run
            # — the config in the image was read straight through.
            row["on_control_port"] = f":{control_port}/" in url
            row["reachable"] = _mcp_probe(url)
        out.append(row)
    reach = {}
    try:
        reach = orchestrator.live_read_reach()
    except Exception:
        # A diagnostic must never be the thing that breaks the diagnostics page.
        pass
    says = {}
    try:
        # The half that is not us. Everything above reads OUR file and probes OUR route, and all of
        # it can look right while OpenCode has quietly dropped the server — which is the shape of
        # the bug this block exists for. `GET /mcp` on the OpenCode server is the only thing that
        # answers for OpenCode. See Orchestrator.opencode_mcp_status for how to read it beside
        # `reachable`.
        #
        # Twice, because an MCP client belongs to a DIRECTORY and the two directories here are not
        # the same one. `opencode_says` answers for the server's own instance, which no turn uses.
        # `opencode_says_chat_work` answers for the instance Chat turns actually run in, and it is
        # the one worth reading — the bare call reported `connected` while three turns in a row went
        # out with no Live read in their tool list.
        says = {"opencode_says": orchestrator.opencode_mcp_status()}
        work = _chat_work_dir()
        if work:
            says["opencode_says_chat_work"] = {
                "directory": work, **orchestrator.opencode_mcp_status(work)}
    except Exception:
        pass
    return {"config": str(src), "servers": out, **reach, **says}


def _mcp_probe(url: str) -> dict:
    """One `tools/list` against a remote MCP url, naming the tools it offers.

    The tool NAMES are the point, not just a status code: OpenCode namespaces them by the config key
    before offering them to the model, so a server that answers with a renamed tool is a prompt that
    names one nothing will execute. Short timeout — this runs inside a page somebody opens because
    something is already wrong.
    """
    import httpx

    try:
        r = httpx.post(url, timeout=3.0, headers={"x-sage-diag-probe": "1"}, json={
            "jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    if r.status_code != 200:
        return {"ok": False, "status": r.status_code}
    try:
        tools = ((r.json().get("result") or {}).get("tools") or [])
        return {"ok": True, "tools": [str(t.get("name") or "") for t in tools]}
    except Exception as e:
        return {"ok": False, "status": r.status_code, "error": f"unreadable reply: {e}"}


def _chat_work_dir() -> str | None:
    """Where Chat turns run — the directory whose MCP client is the one that matters.

    Beside the diag rather than inside it because two callers need the same answer and they were
    computing it separately, which is how one of them came to ask about the wrong instance.
    """
    workspace = getattr(getattr(orchestrator, "_wm", None), "_dir", None)
    if not workspace:
        return None
    work = Path(workspace) / ".sage" / "chat-work"
    return str(work) if work.is_dir() else None


def _resolves_from(start: Path, spec: str) -> str | None:
    """Where a JS runtime would find the package `spec` imported from a file in `start`.

    It walks the `node_modules` chain upward, which is all any of them do for a bare specifier.
    A specifier that resolves NOWHERE makes the whole tool module fail to load, and OpenCode
    drops the tool without a word — the same silence as the MCP drop this was meant to escape.

    This is worth a line on the page because a laptop passes it by accident. OpenCode installs
    `@opencode-ai/plugin` into `~/.config/opencode` the first time something needs it, and it
    then sits there for months; a bench with a warm one cannot see the failure a fresh
    workspace has.
    """
    for parent in [start, *start.parents]:
        found = parent / "node_modules" / spec
        if found.exists():
            return str(found)
    return None


def _custom_tools_diag() -> dict:
    """What is actually on disk in the slot OpenCode reads custom tools from.

    The wiring log says the file was WRITTEN. It cannot say the file is still there, is the right
    size, or is the only thing in the directory — and on 2026-09-09 `live_read.ts` was written at
    boot and its tools were still absent from every turn, on a build where the identical file
    registers from this same slot on a bench. That gap is exactly where this page had nothing.

    Names and BYTES, never contents: this is a diagnostic, and the file is code we wrote, so its
    size answers "did it land whole" without putting a program on a page.
    """
    import re

    d = Path(os.path.expanduser("~/.config/opencode")) / "tools"
    row: dict = {"dir": str(d)}
    try:
        entries = sorted(d.iterdir())
    except OSError as e:
        row["exists"] = False
        row["error"] = f"{type(e).__name__}: {e}"
        return row
    row["exists"] = True
    row["files"] = [{"name": p.name, "bytes": p.stat().st_size} for p in entries if p.is_file()]
    bodies = {}
    for p in entries:
        if p.suffix != ".ts":
            continue
        try:
            bodies[p] = p.read_text()
        except OSError:
            continue
    # The names OpenCode will build out of them, so the page can be read against the shim's tool
    # list without anyone having to remember the `<file>_<export>` rule at the hour they need it.
    names = []
    for p, body in bodies.items():
        exported = re.findall(r"^export const (\w+) = (?:tool\()?\{", body, re.MULTILINE)
        names += [f"{p.stem}_{e}" for e in exported] or ([p.stem] if "export default" in body else [])
    row["tools_it_should_make"] = sorted(names)
    row["imports"] = [
        {"package": spec, "found_at": _resolves_from(d, spec)}
        for spec in sorted({m for body in bodies.values()
                            for m in re.findall(r'^\s*import .*? from "([^".][^"]*)"', body,
                                                re.MULTILINE)})
    ]
    return row


def _live_read_verdict(info: dict) -> str:
    """One sentence saying WHOSE end broke, from the fields `_mcp_diag` already gathers.

    Those fields are the right fields. The problem is that reading them means holding `reachable`,
    `opencode_says` and `turns_without_tools` against each other and knowing which combination means
    what — a bad ask at the hour somebody actually needs it, which is the hour this page gets opened.
    So this says it out loud, and still names what it read so the next question has somewhere to go.

    Three outcomes, because three things can be at fault and they used to look identical:
    our route, OpenCode's grip on it, and the model's own choice not to call a tool it was offered.
    """
    # The chat-work instance when we have it, because that is the client a turn uses. Falling back
    # to the server's own instance rather than refusing to answer: it is the weaker reading, not a
    # useless one, and before this the page had nothing else.
    says = info.get("opencode_says_chat_work") or info.get("opencode_says") or {}
    if not says.get("asked"):
        return ("VERDICT: nobody was asked — "
                + str(says.get("why") or "the OpenCode server is not up")
                + "\nOpenCode starts on the first turn, not at boot. Send one chat message, then "
                  "reload this page.")
    if not says.get("ok"):
        return ("VERDICT: could not ask OpenCode — "
                + str(says.get("error") or f"it answered HTTP {says.get('status')}")
                + f"\nAsked: {says.get('url')}. That is Sage failing to reach its own OpenCode "
                  "server, not a finding about Live read.")

    held = says.get("servers")
    held = held if isinstance(held, dict) else {}
    rows = [r for r in (info.get("servers") or []) if isinstance(r, dict)]
    lines: list[str] = []
    healthy: list[str] = []

    # The per-turn fact, which no amount of asking the server NOW can supply. OpenCode fixes a
    # turn's tool list when the turn STARTS, and the handshake for a session has been seen landing
    # after that — so "connected" read from this page says nothing about the turn the person is
    # actually asking about. Without this, a turn that went out with no tools reads here as a model
    # that chose not to call them, which is the wrong end and sends the reader to the wrong place.
    # The shim writes the list the model actually got; this reads it back. The ring ROLLS, so its
    # silence is not evidence either way and is reported as silence rather than as a pass.
    turn_line = next((ln for ln in reversed(_LOG_RING) if "chat tools: live read" in ln), "")
    last_turn = ("unknown" if not turn_line
                 else "missed" if "NOT OFFERED" in turn_line
                 else "offered")
    for row in rows or [{"name": "sage-live-read"}]:
        name = str(row.get("name") or "")
        reach = row.get("reachable") or {}
        if row.get("url") and not reach.get("ok"):
            why = reach.get("error") or f"HTTP {reach.get('status')}"
            lines.append(
                f"{name}: OURS. Our own route did not answer — {why}. OpenCode was never handed a "
                f"working server. Check the URL ({row.get('url')}) and `on_control_port` on "
                "/api/diag.")
        elif name not in held:
            lines.append(
                f"{name}: OPENCODE'S END. Our route answers, but OpenCode is not holding this "
                "server at all, so its tools were never offered to the model. OpenCode does not "
                "retry a server it dropped — a restart is the blunt fix.")
        elif str((held.get(name) or {}).get("status") or "") != "connected":
            lines.append(
                f"{name}: OPENCODE'S END. OpenCode holds it as "
                f"{(held.get(name) or {}).get('status')!r}, not connected, so its tools were not "
                "offered to the model.")
        elif last_turn == "missed" and info.get("opencode_connected") == "never":
            lines.append(
                f"{name}: OPENCODE'S END. The tools have never reached a turn. `opencode serve` has "
                "not dialled this server once on its own — every connection on record was caused by "
                "opening this page, because `GET /mcp` connects OpenCode lazily and `opencode mcp "
                "list` is a second OpenCode process that dials on startup. So 'connected' above "
                "means 'connected because you asked', and the turns went out with nothing.")
        elif last_turn == "missed":
            lines.append(
                f"{name}: LATE, NOT MISSING. OpenCode holds it connected NOW, but the last chat "
                "turn went out WITHOUT it — the shim recorded `chat tools: live read NOT OFFERED`. "
                "A turn's tool list is fixed when the turn starts, and the handshake has been seen "
                "landing after that. Nothing is misconfigured. Ask the same question again: the "
                "next turn should have the tools.")
        elif last_turn == "unknown":
            lines.append(
                f"{name}: PROBABLY NOBODY'S. Our route answers and OpenCode holds it connected. But "
                "no `chat tools: live read` line is left in the log ring, which rolls, so this "
                "could NOT be checked against the turn you are asking about — and a turn that "
                "started before the handshake goes out with no tools however healthy this reads. "
                "Ask again and reload; the fresh line will say which it was.")
        else:
            healthy.append(name)
            lines.append(
                f"{name}: NOBODY'S. Our route answers, OpenCode holds it connected, and the last "
                "turn WAS offered the tools. If Chat said it could not see the data, that was the "
                "model's choice on the turn, not the wiring — read the turn, not this page.")

    without = info.get("turns_without_tools") or 0
    if without:
        lines.append(f"{without} chat turn(s) ran before OpenCode dialled. Those turns had no Live "
                     "read tools whatever this page says now.")
    elif healthy and len(healthy) == len(rows or [1]) and info.get("opencode_connected") == "never":
        lines.append("`opencode_connected: never` is NORMAL here — the dial is lazy and happens "
                     "when a session first needs tools, not at boot.")
    return "VERDICT\n  " + "\n  ".join(lines)


def _opencode_config_diag() -> dict:
    """Every config slot OpenCode resolved, whether it exists, and whether Sage wrote it.

    `agents` says what OpenCode ended up with; `mcp` says what the file WE wrote asks for. Neither
    says which files OpenCode actually read, and that is the gap a whole evening went into: the
    global copy can be perfect, the probe green, the agents all present, and a file Sage never wrote
    can still be loaded LAST and take the MCP server away — leaving the assistant to tell somebody
    it cannot see their data.

    The project slot is the one that does it. It is resolved off the git root of the SESSION
    directory (`driver/server.py`), which is the Project volume — so it outranks `OPENCODE_CONFIG`,
    it is not ours to fill, and deleting the workspace does not clear it. `.opencode/` on that
    volume survives everything.

    Read from OpenCode's own log, which has been printing `loading path=...` for every slot the
    whole time, plus the project slot named directly in case it fell out of the log window. Top
    level KEYS only, never values: a config carries the gateway's credentials.
    """
    import json
    import re

    ours = {str(Path(os.path.expanduser("~/.config/opencode")) / name)
            for name in ("opencode.json", "opencode.jsonc")}
    ours.add(str(_opencode_project_dir() / "opencode.json"))

    paths: list[str] = []
    for line in orchestrator._opencode_log_tail(400):
        m = re.search(r"loading path=(\S+)", line)
        if m and m.group(1) not in paths:
            paths.append(m.group(1))
    # Named outright, not only where the log window happens to reach: this is the slot that wins,
    # so "not in the log" must never read as "not there".
    # BOTH project shapes. `.opencode/<name>` is the obvious one; a BARE `opencode.json` beside the
    # git root is the other, and it is not a curiosity — it is exactly how Sage's own project copy
    # works (`_install_opencode_config` writes one and `git init`s next to it so it outranks the
    # rest). A legacy file left at the workspace root has been seen surviving every rebuild, and
    # until this loop named it the page reported the project slot as absent while OpenCode was
    # reading one.
    workspace = getattr(getattr(orchestrator, "_wm", None), "_dir", None)
    for parent in (".opencode", ""):
        for name in ("opencode.json", "opencode.jsonc"):
            slot = str((Path(workspace) / parent / name) if parent else (Path(workspace) / name)) \
                if workspace else ""
            if slot and slot not in paths:
                paths.append(slot)

    slots = []
    for path in paths:
        row: dict = {"path": path, "ours": path in ours}
        try:
            stat = Path(path).stat()
        except OSError:
            # OpenCode logs a line for every slot it TRIES, existing or not — `config.json` is one
            # we never write and it is logged too. So absence is the common case, and reporting it
            # is what stops a probed path being read as a found one.
            row["exists"] = False
            slots.append(row)
            continue
        row["exists"] = True
        row["bytes"] = stat.st_size
        try:
            cfg = json.loads(Path(path).read_text())
            row["keys"] = sorted(cfg)[:20] if isinstance(cfg, dict) else type(cfg).__name__
            row["declares_mcp"] = isinstance(cfg, dict) and "mcp" in cfg
            # A `tools` map is the quiet half. It never touches the `mcp` block, so the server still
            # connects, `opencode mcp list` still prints a green tick and `opencode_says` still reads
            # connected — while `"sage-live-read*": false` takes the tools off the agent anyway. That
            # combination is indistinguishable from a healthy Sage everywhere else on this page.
            row["declares_tools"] = isinstance(cfg, dict) and (
                "tools" in cfg
                or any(isinstance(a, dict) and "tools" in a
                       for a in (cfg.get("agent") or {}).values()))
        except Exception as e:
            row["error"] = f"{type(e).__name__}: {e}"
        slots.append(row)

    # The finding, said rather than left to be spotted in a list: a file we did not write, that
    # exists, that speaks about MCP, and that OpenCode loads after ours.
    shadow = [r["path"] for r in slots
              if r.get("exists") and not r["ours"] and r.get("declares_mcp")]
    # The same finding for the quieter lever. Kept apart from `shadowing_mcp` because the symptom
    # differs: a file that takes the MCP BLOCK away shows up as a server nobody holds, and one that
    # only filters `tools` leaves every status green and the agent empty-handed.
    shadow_tools = [r["path"] for r in slots
                    if r.get("exists") and not r["ours"] and r.get("declares_tools")]
    return {"slots": slots, "shadowing_mcp": shadow, "shadowing_tools": shadow_tools}


def _artifacts_diag() -> dict:
    """What the Chat Artifacts weigh, and what is over the ceiling (#223).

    `examples/` is committed, and git history never gives a blob back, so the only lever on how big
    this Project gets is what goes in. That makes the weight worth reporting somewhere before it is
    a slow clone nobody can explain — and this is where people already look.

    `orphans` is the other half of the same question: folders no Conversation claims, which the
    attach sweep removes. A number that stays above zero across restarts means that sweep is being
    refused, and the digest guard is the likely reason."""
    try:
        project = orchestrator.project(start_preview=False, seed_app=False)
    except Exception:
        return {"ok": False, "detail": "no project attached yet"}
    try:
        root = project.record.path
        total, count, largest, biggest = artifact_bytes(root)
        heavy = oversized_artifacts(root)
        return {
            "ok": True,
            "bytes": total,
            "human": f"{total / (1024 * 1024):.1f} MB",
            "files": count,
            "largest": {"path": largest, "bytes": biggest} if largest else None,
            "ceiling_mb": ARTIFACT_COMMIT_MAX // (1024 * 1024),
            "over_ceiling": heavy,
            "orphans": ThreadStore(root).orphaned_artifact_ids(),
        }
    except Exception as e:
        return {"ok": False, "detail": f"{type(e).__name__}: {e}"}


@control_app.get("/api/diag")
def diag() -> JSONResponse:
    """Browser-openable build diagnostics (no shell needed in the deployed builder). Reads the CURRENT
    project without starting anything, so it's safe to hit mid-build. Key signals:
      - sage_rev: which code is actually running (confirm a rebuild took effect)
      - data_library: whether THIS interpreter can read inside a Data Source. The builder has no
        terminal, and the package sits in the image's system python while the orchestrator runs from
        uv's venv, so this is the only place the difference is visible
      - model_calls: how many inferences reached the shim THIS turn. 0 while a turn is live means the
        model call never got to the gateway (OpenCode stuck earlier, e.g. on a tool), not a gateway hang
      - last_gateway_error: set if a model call failed/severed
      - ports: base_port (what opencode.json tells OpenCode to dial) must equal control_port
      - agents: the agents OpenCode actually resolved. There are five — sage-chat, sage-ask, sage-plan,
        sage-architect, sage-implement — and any of them missing means that mode silently ran the
        default build agent, so its read-only permission and prompt blocks never applied (null =
        OpenCode not started yet, or the query failed). `/api/health` says the same thing to a
        creator; this stays the raw list
      - mcp: the MCP servers OpenCode was told about and whether each one answers now. OpenCode
        drops an unreachable one silently, so an absent Live read tool is otherwise indistinguishable
        from a model that chose not to call it. Read `opencode_says` (OpenCode's own per-server
        status) beside `reachable` (our probe of our own route): the pair is what says whose end
        broke. `opencode_connected: "never"` on its own is NOT a fault — the dial is lazy and
        happens when a session first needs tools, so it reads that way until the first Chat turn
      - opencode_config: every config slot OpenCode resolved and which of them Sage wrote. The
        project slot sits on the Project volume, outranks OPENCODE_CONFIG, and is not ours — a file
        there can take the MCP server away while `agents` and `mcp` both still look right
      - opencode_log_level: the level OpenCode is running at, when SAGE_OPENCODE_LOG_LEVEL set one.
        Its MCP servers are invisible at the default level — it connects, or silently does not, and
        the log reads the same either way. /api/diag/opencode?q=mcp serves the whole log
      - log_tail (see also /api/diag/mcp, which asks OpenCode itself about its MCP servers — its
        MCP client has no logger, so no amount of log level will answer that one) / opencode_log_tail: recent sage.* and OpenCode server logs
    """
    from .service import _opencode_base_port

    p = orchestrator._project  # may be None if no project bound yet; do NOT create one here
    control_port = int(os.environ.get("SAGE_CONTROL_PORT", "8080"))
    try:
        base_port = _opencode_base_port(orchestrator._opencode_cwd)
    except Exception:
        base_port = None
    library = data_library_ready()
    return JSONResponse(content={
        "sage_rev": _SAGE_REV,
        "gateway_mode": GATEWAY_MODE,
        "data_library": {"ok": not library, "detail": library or "domino_data is importable"},
        "ports": {"control_port": control_port, "base_port": base_port,
                  "match": base_port == control_port},
        "agents": orchestrator.resolved_agents(),
        "mcp": _mcp_diag(control_port),
        "opencode_config": _opencode_config_diag(),
        # Three readings, and only together do they say where a custom tool was lost: what is on
        # DISK, what OPENCODE made of it, and — on the shim's own line below — what reached the
        # gateway. With any one missing, "never installed", "installed but never loaded" and
        # "loaded but never offered" are the same blank.
        "custom_tools": {**_custom_tools_diag(),
                         "opencode_version": orchestrator.opencode_version(),
                         "opencode_holds": orchestrator.opencode_tool_registry(_chat_work_dir()),
                         "opencode_log": orchestrator.opencode_log_about_tools()},
        "project": None if p is None else {
            "model_calls": p.model_calls,
            "tool_call_responses": p.tool_call_responses,
            "last_gateway_error": p.last_gateway_error,
            "session_id": p.session_id,
        },
        "artifacts": _artifacts_diag(),
        "git_credential": _git_credential_diag(),
        "git_credential_list": _git_credential_list_diag(),
        "debug_stream": ka.debug_stream_enabled(),
        "opencode_log_level": os.environ.get("SAGE_OPENCODE_LOG_LEVEL", "") or "default (quiet)",
        "log_tail": list(_LOG_RING)[-60:],
        # Next to the tail rather than behind a query parameter nobody knows to type: this is the
        # half of the ring that survives a loud turn, and /api/diag is where someone looks first.
        "warn_tail": list(_WARN_RING)[-20:],
        "opencode_log_tail": orchestrator._opencode_log_tail(30),
    })


@control_app.get("/api/diag/log")
def diag_log(q: str = "", n: int = 400, warn: bool = False) -> PlainTextResponse:
    """The log ring as plain text, one line each — for reading in a browser.

    /api/diag is JSON, so a browser with no JSON viewer renders log_tail as one unreadable line, and
    it only carries the newest 60 entries. During a build the shim logs on every inference, so the
    line you were waiting for scrolls out of that window in under a minute. This serves the whole
    400-line ring, newest last, and `?q=` filters it: /api/diag/log?q=rescue

    `q` is a plain case-insensitive substring, not a regex — there is no shell in the workspace to
    pipe through, and a regex typo in a URL bar is a worse failure than a literal match.

    `?warn=1` reads the warnings-only ring instead. Filtering this one by `q` is the wrong reflex
    when a build has just failed: the point of that ring is that it still holds the failure after a
    loud turn, so read it whole and let it tell you which line you should have been looking for.
    An empty answer there is itself an answer — nothing warned — which the rolling ring can never
    give you.
    """
    ring = _WARN_RING if warn else _LOG_RING
    lines = [ln for ln in ring if not q or q.lower() in ln.lower()]
    if not lines:
        which = "warnings" if warn else "lines"
        return PlainTextResponse(f"(no {which} match {q!r})" if q else f"(no {which} yet)")
    return PlainTextResponse("\n".join(lines[-max(1, n):]))


@control_app.get("/api/diag/mcp")
def diag_mcp(cmd: str = "list") -> PlainTextResponse:
    """Ask OpenCode itself what it makes of its MCP servers. `?cmd=list` or `?cmd=debug`.

    The question every other surface has failed to answer. `/api/diag` proves the config declares
    the server and that the server answers a probe on the right port; `agents` proves OpenCode read
    that same file; and OpenCode's own log says nothing about MCP at DEBUG or at INFO, because its
    MCP client has no logger — the only `mcp` names in the binary are CLI commands. So "no mcp
    lines" has never meant "it did not connect", and the whole hunt has been reading silence.

    `opencode mcp list` is the one thing that will say. Run from the CHAT WORK directory, not the
    server's cwd: project config resolves off the git root of wherever OpenCode is run, so asking
    from the wrong place answers about the wrong place.

    Never raises, and does not start the server — a diagnostic must not be the thing that breaks
    the diagnostics page, and this one runs on a page somebody opened because something is wrong.
    """
    server = getattr(orchestrator, "_oc_server", None)
    if server is None:
        return PlainTextResponse(
            "OpenCode has not been started yet — it starts on the first turn, not at boot. "
            "Send one chat message, then reload this.")
    workspace = getattr(getattr(orchestrator, "_wm", None), "_dir", None)
    work = str(Path(workspace) / ".sage" / "chat-work") if workspace else None
    if work and not Path(work).is_dir():
        work = None
    # The verdict goes FIRST and in words, because the person reading this opened it knowing only
    # that Chat said it could not see their data. `opencode mcp list` below is the evidence, and it
    # is worth keeping — it asks a fresh CLI rather than the running server — but it answers in
    # OpenCode's vocabulary, and "connected" there has already been mistaken for "the tools arrived".
    try:
        verdict = _live_read_verdict(_mcp_diag(int(os.environ.get("SAGE_CONTROL_PORT", "8080"))))
    except Exception as e:
        verdict = f"VERDICT unavailable ({type(e).__name__}: {e}). The evidence below still stands."
    # `opencode mcp list` is a whole second OpenCode process and it dials the MCP server on startup.
    # Outside this window that dial was recorded as OpenCode connecting for a turn, which is how a
    # page that connects the thing it is measuring came to report the connection as the finding.
    with orchestrator.diagnostic_window():
        body = server.cli(["mcp", "list" if cmd not in ("list", "debug") else cmd], cwd=work)
    return PlainTextResponse(
        f"{verdict}\n\n"
        f"$ opencode mcp {cmd}   (cwd: {work or 'the server default'})\n\n{body}")


@control_app.get("/api/diag/opencode")
def diag_opencode(q: str = "", n: int = 400) -> PlainTextResponse:
    """OpenCode's own server log as plain text, filterable — the twin of /api/diag/log.

    `/api/diag` already carries the last 30 lines of this, which is enough to see a boot and useless
    for anything else: the config-load lines alone are six of them. The line that mattered most this
    week — whether OpenCode ever dialled the Live read MCP server — is not in it at the default log
    level at all, and turning that level up (SAGE_OPENCODE_LOG_LEVEL=DEBUG) makes 30 lines cover
    about a second.

    Same shape as the sage log for the same reason: no shell in the workspace, and `?q=` is a plain
    case-insensitive substring rather than a regex, because a regex typo in a URL bar is a worse
    failure than a literal match. Start with ?q=mcp
    """
    lines = [ln for ln in orchestrator._opencode_log_tail(max(1, n)) if not q or q.lower() in ln.lower()]
    if not lines:
        return PlainTextResponse(f"(no lines match {q!r})" if q else "(no OpenCode log yet)")
    return PlainTextResponse("\n".join(lines))


@control_app.get("/api/diag/timing")
def diag_timing(n: int = 5, format: str = "text") -> PlainTextResponse:
    """Where the last few turns actually spent their wall clock.

    Sage's latency only exists against a live gateway and a real workspace, so this is the readout
    half of the only feedback loop there is for it: the builder measures itself and a laptop reads
    the numbers back over HTTP. Text by default because the deployed builder has no shell and a
    browser is what someone has — `?format=json` for a script.

    A turn still running is the first record, with its open spans marked, so a build that feels
    stuck can be asked what it is doing rather than guessed at.
    """
    body = timing.as_json(n) if format == "json" else timing.render_all(n)
    return PlainTextResponse(body, media_type="application/json" if format == "json" else "text/plain")


@control_app.post("/api/diag/debug-stream")
async def set_debug_stream(request: Request) -> JSONResponse:
    """Turn raw gateway SSE chunk logging on/off without a restart: {"on": true}.

    The chunks land in log_tail as `sage.shim.stream`. Runtime rather than env-only because on Domino
    SAGE_DEBUG_STREAM is baked into the image, so toggling it there costs an Environment rebuild."""
    body = await request.json()
    return JSONResponse(content={"debug_stream": ka.set_debug_stream(bool(body.get("on")))})


@control_app.get("/api/project")
def get_project() -> JSONResponse:
    """Attach the bound project and return its status. Chat boots this without seeding the
    React template or starting Vite; Build's preview proxy seeds when it needs the app."""
    return JSONResponse(content=orchestrator.project(start_preview=False, seed_app=False).status())


@control_app.get("/api/project/resources")
def list_project_resources() -> JSONResponse:
    """Resources the creator added to this project. Browse Domino lists access; this is membership."""
    return JSONResponse(content={"items": orchestrator.list_project_resources()})


@control_app.post("/api/project/resources")
def add_project_resource(body: dict) -> JSONResponse:
    try:
        return JSONResponse(content=orchestrator.add_project_resource(body or {}))
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})


@control_app.delete("/api/project/resources")
def remove_project_resource(id: str = "") -> JSONResponse:
    try:
        ok = orchestrator.remove_project_resource(id)
    except ResourceStillBound as e:
        # `refs` names the app source that still uses it, so the panel can say what to change rather
        # than only that it refused — the same cleanup affordance `unbind` gives. `apps` names the
        # Built Apps that still bind it, because the one refusing is often not the one on screen.
        # `conversations` names the Chat Threads holding a context chip on it (#168), the one holder
        # with no app source behind it and so nothing in `refs` to stand in for it.
        return JSONResponse(status_code=409,
                            content={"error": str(e), "apps": e.apps, "refs": e.refs,
                                     "conversations": e.conversations})
    if not ok:
        return JSONResponse(status_code=404, content={"error": "not in this project"})
    return JSONResponse(content={"removed": True})


@control_app.post("/api/project/resources/pins")
def pin_project_resource(body: dict) -> JSONResponse:
    try:
        item = orchestrator.pin_project_resource(str((body or {}).get("id") or ""), body or {})
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    except KeyError:
        return JSONResponse(status_code=404, content={"error": "not in this project"})
    return JSONResponse(content={"item": item})


@control_app.delete("/api/project/resources/pins")
def unpin_project_resource(
    id: str = "", path: str = "", database: str = "", schema: str = "", table: str = "",
) -> JSONResponse:
    pin = {"path": path} if path else {"database": database, "schema": schema, "table": table}
    ok = orchestrator.unpin_project_resource(id, pin)
    if not ok:
        return JSONResponse(status_code=404, content={"error": "pin not in this project"})
    return JSONResponse(content={"removed": True})


@control_app.get("/api/project/history")
def project_history(conversation: str = "", detail: str = "full") -> JSONResponse:
    """The chat transcript persisted in the workspace, so the UI can replay it after a reload or
    restart (see Workspace.append_history / Orchestrator.history). Reads disk without starting the
    preview.

    `conversation` is a Thread id: Build's transcript is per conversation (ADR-0005). Naming none
    returns the selected Built App's whole log, which is what the agent's own archive renders. It
    is never another app's: the log lives in the app's directory (ADR-0008).

    `detail=off` keeps every row and drops what each tool was CALLED WITH, which on a real log is
    six bytes in seven. The Build history drawer asks that way: it names no conversation, so it
    reads the whole log to draw a list of prompts, and the tool inputs it was paying for sit two
    folds down behind a click that most of them never get. That click reads its own row through
    `/project/history/row/{index}`. The default is unchanged, because the transcript beside it
    draws those cards open."""
    return JSONResponse(content={
        "history": orchestrator.history(conversation or None, tool_detail=detail != "off"),
    })


@control_app.get("/api/project/history/row/{index}")
def project_history_row(index: int) -> JSONResponse:
    """What one tool call was called with — the row `detail=off` left out (see above).

    404 rather than an empty string when the line is gone: the stop button truncates this log, so a
    drawer read before a revert can ask for a row that no longer exists, and "" is what a tool
    nobody recorded the input of already says."""
    detail = orchestrator.history_row_detail(index)
    if detail is None:
        return JSONResponse(status_code=404, content={"error": "no such row in this app's log"})
    return JSONResponse(content={"detail": detail})


@control_app.post("/api/project/model")
async def set_model(request: Request) -> JSONResponse:
    project = orchestrator.project()
    body = await request.json()
    if "mode" in body:
        try:
            mode = Mode(body["mode"])
        except ValueError:
            return JSONResponse(status_code=400, content={"error": f"invalid mode {body['mode']!r}"})
        project.control.set_mode(mode)
    if "phase" in body:
        project.control.set_phase(Phase(body["phase"]))
    if "pick" in body:
        project.control.pick(body["pick"])
    if "chat_model" in body:
        try:
            orchestrator.set_chat_pick(body.get("chat_model"), body.get("reasoning_effort"))
        except ValueError as e:
            return JSONResponse(status_code=400, content={"error": str(e)})
    if "catalog" in body:
        try:
            orchestrator.set_catalog(**(body.get("catalog") or {}))
        except ValueError as e:
            # A slot name the catalog does not have. The panel only ever sends names this route's
            # GET returned, so if it appears the bug is in Sage — but it arrives over the wire, and
            # a 500 is the wrong way to say so.
            return JSONResponse(status_code=400, content={"error": str(e)})
        except TurnBusy as e:
            # 409, as everywhere else the turn lock refuses: the request is well formed and what is
            # in the way is a build this change would move onto another model mid-turn. The sentence
            # is the service's — only that side knows a running turn from a wedge (#97).
            return JSONResponse(status_code=409, content={"error": str(e)})
    return JSONResponse(content=project.status())


@control_app.get("/api/project/model/assignments")
def model_assignments() -> JSONResponse:
    """What the model panel draws: the three assignable slots and the Aliases they can hold (#17).

    Its own route rather than a field on `/api/project/status`, which the UI polls: this makes a
    gateway listing and an endpoint listing, and paying for those on every poll to answer a drawer
    that is almost always closed is the cost `_endpoint_listing` exists to avoid.

    It is also what re-verifies a save. The panel writes, then reads this again — so the check runs
    against the assignment that just landed, without a second cache to keep true.
    """
    return JSONResponse(content=orchestrator.model_assignments())


@control_app.post("/api/project/sync")
async def sync_project() -> JSONResponse:
    """Pull teammate changes from the repo into the workspace, resolving any merge conflicts with
    the agent, then push. Offloaded to a thread because a conflict resolution drives a model turn
    (which needs the event loop free to serve the /v1 calls that turn makes)."""
    try:
        result = await run_in_threadpool(orchestrator.sync)
    except TurnBusy as e:
        # 409, the same as publish's: the request is well formed and nothing is wrong with it — the
        # working tree is busy, and the answer is to wait or stop the build.
        return JSONResponse(status_code=409, content={"error": str(e)})
    except Exception as e:
        log.exception("sync failed")
        return JSONResponse(status_code=502, content={"error": {"message": f"{type(e).__name__}: {e}"}})
    return JSONResponse(content=result)


@control_app.post("/api/publish")
async def publish(request: Request) -> JSONResponse:
    """Publish (or republish) THIS app's project as a live Domino App. Offloaded to a thread — it
    saves work (a git push) and makes several control-plane REST calls.

    `{"new_app": true}` publishes a FRESH Domino App instead of a new version of the recorded one,
    which is what a `missing-app` refusal below asks for (#80). It does not clear the record on its
    own — the successful publish replaces it — and it is refused unless that App really is gone.
    It has to be said, and it is only ever said by somebody who read that refusal: a publish that
    took this on itself would deploy a second copy of an app that is still serving whenever Domino
    was slow to answer.

    `{"name": "..."}` is what the person accepted in the confirm's name field, and accepting writes
    it to the app (#218). A body without one publishes the app under the same default the field was
    offering and renames nothing, which is what every caller older than that field does."""
    body: object = {}
    if await request.body():
        try:
            body = await request.json()
        except ValueError:  # a body that is not JSON is a caller bug, not a 500
            return JSONResponse(status_code=400, content={
                "error": "Publish takes an optional JSON body, and this one isn't JSON."})
    # Anything that is not an object carries no `new_app`, so it means the ordinary publish rather
    # than an AttributeError on the way to a 500.
    new_app = bool(body.get("new_app")) if isinstance(body, dict) else False
    # Anything that is not a string carries no name either — the same reading as `new_app` above,
    # and for the same reason: a malformed body means the ordinary publish, not a 500.
    asked = body.get("name") if isinstance(body, dict) else None
    name = asked.strip() if isinstance(asked, str) else ""
    try:
        result = await run_in_threadpool(orchestrator.publish, new_app=new_app, name=name)
    except TurnBusy as e:
        # 409 beside the refusal below, and for the same reason: the request is well formed and the
        # app is fine, and what is in the way is a build holding the working tree this would commit.
        # The sentence is the service's — it is the only side that knows whether the lock has a turn
        # behind it or a wedge (#97).
        return JSONResponse(status_code=409, content={"error": str(e)})
    except PublishRefused as e:
        # 409, not 400: the request is well formed and the app is fine — it is the state of what it
        # reads that is in the way. `refused` carries every problem so the UI can name them all at
        # once; `error` keeps the plain-text shape every other publish failure has.
        return JSONResponse(status_code=409, content={
            "error": str(e), "refused": [p.to_dict() for p in e.problems]})
    except RuntimeError as e:  # not-on-Domino / missing app.sh — human-readable, expected failures
        return JSONResponse(status_code=400, content={"error": str(e)})
    except Exception as e:
        log.exception("publish failed")
        return JSONResponse(status_code=502, content={"error": f"{type(e).__name__}: {e}"})
    return JSONResponse(content=result)


@control_app.get("/api/publish-check")
async def publish_check() -> JSONResponse:
    """Which of this app's named queries the published app would refuse, read before publishing
    (#26): {checked, queries}. A warning the UI shows and the creator may publish past — no state
    changes here, and `POST /api/publish` neither calls this nor cares whether the UI did."""
    try:
        result = await run_in_threadpool(orchestrator.publish_check)
    except Exception as e:
        log.exception("publish-check failed")
        return JSONResponse(status_code=502, content={"error": f"{type(e).__name__}: {e}"})
    return JSONResponse(content=result)


@control_app.get("/api/publish-egress")
async def publish_egress() -> JSONResponse:
    """What of this app's data leaves Domino when it calls a model, read before publishing (#35):
    {checked, notice}. Beside `publish-check` rather than inside it — this one may reach the gateway
    for the Alias listing, and that check promises it never reaches anything. The UI asks both at
    once so a slow listing cannot hold up the query warnings.

    A notice, never a refusal (ADR-0012): `POST /api/publish` neither calls this nor cares whether
    the UI did. A 502 reads as nothing to say, which is the same thing `checked: false` means to a
    creator — the failure is loud in the log and silent on screen, on purpose."""
    try:
        result = await run_in_threadpool(orchestrator.publish_egress)
    except Exception as e:
        log.exception("publish-egress failed")
        return JSONResponse(status_code=502, content={"error": f"{type(e).__name__}: {e}"})
    return JSONResponse(content=result)


@control_app.get("/api/publish-status")
async def publish_status(app_id: str) -> JSONResponse:
    """Deploy status of a published app so the UI can poll after Publish: {phase, status, app_id}."""
    try:
        result = await run_in_threadpool(orchestrator.publish_status, app_id)
    except RuntimeError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    except Exception as e:
        log.exception("publish-status failed")
        return JSONResponse(status_code=502, content={"error": f"{type(e).__name__}: {e}"})
    return JSONResponse(content=result)


@control_app.post("/api/stop")
async def stop() -> JSONResponse:
    """Stop THIS builder's workspace (saving in-progress work first). Offloaded to a thread — it
    drives a git push and a control-plane call."""
    try:
        result = await run_in_threadpool(orchestrator.stop)
    except Exception as e:
        log.exception("stop failed")
        return JSONResponse(status_code=502, content={"error": f"{type(e).__name__}: {e}"})
    return JSONResponse(content=result)


@control_app.post("/api/preview/runtime-error")
async def preview_runtime_error(request: Request) -> Response:
    """The live preview posts here when it catches an uncaught/render error (see the template's
    reportRuntimeError). build_stream reads it after a clean typecheck to autofix runtime crashes
    that tsc can't see. Fire-and-forget: always 204, never blocks the preview."""
    try:
        body = await request.json()
    except Exception:
        return Response(status_code=204)
    orchestrator.record_runtime_error(str(body.get("message") or ""), str(body.get("stack") or ""))
    return Response(status_code=204)


@control_app.post("/api/project/check")
def check_project() -> JSONResponse:
    """Typecheck the workspace (Step 5). The server-mode driver calls the same engine after each
    agent edit and injects `message` into the next turn; exposed here for the UI + manual use."""
    project = orchestrator.project()
    report = _feedback.check(project.workspace.path)
    return JSONResponse(content={
        "ok": report.ok,
        "error_count": len(report.errors),
        "message": report.as_agent_message(),
        "signature": report.signature(),
    })


_FILE_TREE_IGNORE = {"node_modules", ".git", "dist", "dist-ssr", ".vite", "build", "__pycache__", ".turbo"}


def _build_file_tree(root: Path, current: Path) -> list[dict]:
    entries = []
    try:
        children = sorted(current.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
    except OSError:
        return entries
    for child in children:
        if child.name in _FILE_TREE_IGNORE or child.name.startswith("."):
            continue
        rel = child.relative_to(root).as_posix()
        if child.is_dir():
            entries.append({"name": child.name, "path": rel, "type": "dir", "children": _build_file_tree(root, child)})
        else:
            entries.append({"name": child.name, "path": rel, "type": "file"})
    return entries


def _resolve_workspace_file(root: Path, rel_path: str) -> Path:
    """Resolves a UI-supplied relative path against the workspace root, rejecting anything that
    escapes it (../, absolute paths) so the file API can't read/write outside the workspace."""
    root = root.resolve()
    candidate = (root / rel_path).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError("path escapes workspace")
    return candidate


# Pinned the way /fonts/inter-latin-var.woff2 is: mimetypes on a bare Linux image has no answer
# for several of these, and an <img> of /file/raw with application/octet-stream (or worse,
# Content-Disposition: attachment) is the broken-image icon over the alt text.
_RAW_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".pdf": "application/pdf",
    ".html": "text/html",
    ".htm": "text/html",
    ".json": "application/json",
}


def _raw_media_type(path: Path) -> str:
    return _RAW_MEDIA_TYPES.get(path.suffix.lower()) or guess_type(str(path))[0] or "application/octet-stream"


@control_app.get("/api/project/files")
def list_files() -> JSONResponse:
    project = orchestrator.project()
    return JSONResponse(content={"tree": _build_file_tree(project.workspace.path, project.workspace.path)})


@control_app.get("/api/project/file")
def read_file(path: str) -> JSONResponse:
    project = orchestrator.project()
    # Attached data files live as symlinks under public/data/ pointing at the dataset mount, which is
    # OUTSIDE the workspace — _resolve_workspace_file (which .resolve()s through the symlink) would
    # reject them as an escape. They're trusted (Sage created the symlink to a dataset the user owns),
    # so allow a read-only preview when the path exactly matches a known attachment; membership in the
    # manifest is the whitelist, so no traversal is possible.
    if any(e["path"] == path for e in project.attached):
        target = project.workspace.path / path  # is_file()/read_text() follow the symlink to the mount
    else:
        try:
            target = _resolve_workspace_file(project.root_for(path), path)
        except ValueError:
            return JSONResponse(status_code=400, content={"error": "invalid path"})
    if not target.is_file():
        return JSONResponse(status_code=404, content={"error": "file not found"})
    try:
        content = target.read_text()
    except UnicodeDecodeError:
        return JSONResponse(status_code=415, content={"error": "binary file"})
    return JSONResponse(content={"path": path, "content": content})


@control_app.get("/api/project/file/raw")
def read_file_raw(path: str) -> Response:
    # Serve raw file bytes with a content type so binary files (e.g. images) render in the code view.
    project = orchestrator.project()
    if any(e["path"] == path for e in project.attached):
        target = project.workspace.path / path  # follow the symlink to the dataset mount
    else:
        try:
            target = _resolve_workspace_file(project.root_for(path), path)
        except ValueError:
            return JSONResponse(status_code=400, content={"error": "invalid path"})
    if not target.is_file():
        return JSONResponse(status_code=404, content={"error": "file not found"})
    return FileResponse(
        target,
        media_type=_raw_media_type(target),
        filename=target.name,
        content_disposition_type="inline",
        headers={"Cache-Control": "no-store"},
    )


@control_app.put("/api/project/file")
async def write_file(request: Request) -> JSONResponse:
    project = orchestrator.project()
    body = await request.json()
    path = body.get("path")
    content = body.get("content")
    if not path or content is None:
        return JSONResponse(status_code=400, content={"error": "path and content required"})
    try:
        target = _resolve_workspace_file(project.workspace.path, path)
    except ValueError:
        return JSONResponse(status_code=400, content={"error": "invalid path"})
    if not target.exists() or not target.is_file():
        return JSONResponse(status_code=404, content={"error": "file not found"})
    target.write_text(content)
    return JSONResponse(content={"path": path, "saved": True})


@control_app.get("/api/project/instructions")
def read_instructions() -> JSONResponse:
    project = orchestrator.project()
    return JSONResponse(content={"content": orchestrator.read_instructions(project)})


@control_app.put("/api/project/instructions")
async def write_instructions(request: Request) -> JSONResponse:
    project = orchestrator.project()
    body = await request.json()
    content = body.get("content", "")
    if not isinstance(content, str):
        return JSONResponse(status_code=400, content={"error": "content must be a string"})
    orchestrator.write_instructions(project, content)
    return JSONResponse(content={"ok": True, "content": orchestrator.read_instructions(project)})


@control_app.post("/api/project/reset")
def reset_app() -> JSONResponse:
    """Put the selected app's code back to the starter template (#36). One Built App, not the
    Project (#75): the user's attachments, Resources, transcript, project instructions and every
    other app all survive — see Orchestrator.reset_app."""
    try:
        return JSONResponse(content=orchestrator.reset_app())
    except ResetBusy as e:
        return JSONResponse(status_code=409, content={"error": str(e)})


@control_app.get("/api/assets")
def list_assets() -> dict:
    try:
        return {
            "assets": orchestrator.list_assets(),
            "default_dataset_id": orchestrator.default_dataset_id(),
        }
    except ResourceUnavailable as e:
        return {"assets": [], "default_dataset_id": None, "error": str(e)}


@control_app.get("/api/project/sensitivity")
def sensitivity_state(conversation: str = "") -> JSONResponse:
    """The state of the sensitivity lock, for the surfaces that draw it (ADR-0043).

    `conversation` is the Conversation the surfaces are drawn beside. It carries the sticky half of
    the lock (ADR-0043): a Conversation that has already run a turn under the lock stays locked once
    the Dataset is unbound, and nothing in the Bindings says so any more. Omitting it asks about the
    Bindings alone, which is the right question for a caller that is not looking at a Conversation.

    Its own route rather than a field on `/api/project/status`, for the reason
    `/api/project/model/assignments` is: the status poll runs on a timer, and a locked Project would
    pay a gateway listing on every tick to answer a question that changes when an administrator
    edits a group. It is read on a scope load and after a Binding changes.

    Never 500s. The Workbench draws badges and a picker from this, and a read that threw would take
    the picker down rather than the lock — which still holds, in the router and at publish, whatever
    this answers.
    """
    try:
        asked = safe_id(conversation, "conversation id") if conversation else None
    except ValueError:
        # An id that could not name a Thread names no locked one either — nothing can ever have
        # written a lock under an id `safe_id` refuses. Answered as a Bindings-only read rather than
        # left to the handler below, which reports the lock OFF: that is the dangerous direction
        # (see store.js), it puts non-approved models back in the picker, and until this parameter
        # existed nothing outside this process could reach that handler at all.
        asked = None
    try:
        return JSONResponse(content=orchestrator.sensitivity_state(asked))
    except Exception:
        log.exception("sensitivity state read failed")
        return JSONResponse(content={"enabled": False, "locked": False, "group": "",
                                     "approved": [], "datasets": [], "refusal": None,
                                     "model": None, "chat_model": None, "slot_models": {},
                                     "reason": ""})


@control_app.post("/api/project/assets/{dataset_id}/sensitive")
def declare_dataset_sensitive(dataset_id: str) -> JSONResponse:
    """Declare one of this Project's own Datasets sensitive (ADR-0043).

    409 rather than 403 for a Dataset shared in from elsewhere: nothing is wrong with the caller's
    permissions — they may well be able to tag it in Domino — and the refusal is about who else the
    tag would reach. That is the state of the world, which is what the other 409s on this app mean.
    """
    try:
        return JSONResponse(content=orchestrator.declare_dataset_sensitive(dataset_id))
    except LookupError:
        return JSONResponse(status_code=404, content={"error": brand_text("{dataset} not found")})
    except NotThisProjectsDataset as e:
        return JSONResponse(status_code=409, content={"error": brand_text(
            "{name} belongs to another {project}, and the tag would mark it for everyone who reads "
            "it. Tag it in {platformName} instead, where you can see who else uses it.",
            name=e.name)})
    except ResourceUnavailable as e:
        return JSONResponse(status_code=502, content={"error": str(e)})


@control_app.get("/api/resources")
def list_resources() -> JSONResponse:
    """Domino Resources this caller can pick: LLM Aliases (#5), Model APIs (#8), Data Sources (#10).

    A service that won't answer is reported as a readable reason rather than an empty list, so the
    rail can say "the gateway is not answering" instead of "you have no models".

    That reason is carried PER KIND, and the response stays 200. The three kinds come from two
    different Domino services and fail independently, so a single failing status would let the
    Domino API being down blank out the LLM Aliases as well — reporting nothing available when only
    one third is. Each group in the rail renders its own list or its own reason.
    """
    kinds = {
        "llm_aliases": orchestrator.list_llm_aliases,
        "model_apis": orchestrator.list_model_apis,
        "data_sources": orchestrator.list_data_sources,
    }
    body: dict = {"errors": {}}
    # All three at once, not one after another (#160). Measured on a real deployment: 0.5-0.8s of
    # LLM Aliases plus 1.4-2.5s of Model APIs plus 0.3s of Data Sources, and the three summed to
    # the whole of the response's 2.5-3.3s. Two Domino services, no call depending on another's
    # answer, so the wait is now the slowest kind rather than the sum.
    #
    # Each kind still catches its own failure, off its own result: the reason stays per kind and
    # the response stays 200, exactly as when the loop was serial.
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=len(kinds), thread_name_prefix="sage-resources",
    ) as pool:
        answers = {key: pool.submit(listing) for key, listing in kinds.items()}
    for key, answer in answers.items():
        try:
            body[key] = answer.result()
        except ResourceUnavailable as e:
            body[key], body["errors"][key] = [], str(e)
    return JSONResponse(content=body)


# One route per level of the Data Source cascade (#11), rather than one route that infers the level
# from which query parameters happen to be present. The three are asked at different moments and each
# one is a query against the store that can fail on its own, so a URL that says which level it is
# keeps a failure legible in a network log — and there is no level to guess wrong.
#
# Every one answers 200 with `items`, or a reason. A reason, not an empty list: an empty schema and a
# connector that refused the statement look identical in a list of nothing, and only one of them is
# something the creator can act on.
def _cascade(level, *args) -> JSONResponse:
    """Run one cascade level and turn its failures into the right status.

    502 is the store or Domino not answering — the wording of that is already the creator's to read.
    400 is a name Sage will not send, which the panel should never produce, since it only ever sends
    names the previous level returned; if it appears, the bug is in Sage and the code says so.
    """
    try:
        return JSONResponse(content={"items": level(*args)})
    except LookupError:
        return JSONResponse(status_code=404, content={"error": brand_text(
            "That {dataSource} is not one {platformName} offers you, so {assistantName} cannot "
            "look inside it."
        )})
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    except ResourceUnavailable as e:
        return JSONResponse(status_code=502, content={"error": str(e)})


@control_app.get("/api/data-sources/{source_id}/databases")
def list_data_source_databases(source_id: str) -> JSONResponse:
    return _cascade(orchestrator.list_data_source_databases, source_id)


@control_app.get("/api/data-sources/{source_id}/schemas")
def list_data_source_schemas(source_id: str, database: str = "") -> JSONResponse:
    return _cascade(orchestrator.list_data_source_schemas, source_id, database)


@control_app.get("/api/data-sources/{source_id}/tables")
def list_data_source_tables(source_id: str, database: str = "", schema: str = "") -> JSONResponse:
    return _cascade(orchestrator.list_data_source_tables, source_id, database, schema)


# The three levels of a Scope, off a request body, in the order both writers take them (#142).
#
# `or ""` is the line that does the work: a level the control is standing on but has not answered
# arrives as `""` — the empty schema under a chosen database — and an absent key arrives as None.
# Both mean "not chosen", and flattening them here is what lets a body say either.
def _scope_levels(body: dict) -> tuple[str, str, str]:
    return tuple(str(body.get(level) or "") for level in ("database", "schema", "table"))


# Bindings are their own route, not part of /api/resources: that one has nothing to list for a kind
# whose service will not answer, and a creator auditing an app needs the dependency list precisely
# then.
@control_app.get("/api/bindings")
def list_bindings() -> JSONResponse:
    return JSONResponse(content={"bindings": orchestrator.list_bindings()})


@control_app.post("/api/bindings")
async def add_binding(request: Request) -> JSONResponse:
    body = await request.json()
    kind, resource_id = body.get("kind"), body.get("id")
    if not resource_id:
        return JSONResponse(status_code=400, content={"error": "id required"})
    # A Data Source is handled before the pair below rather than folded in with them: it is the one
    # kind whose record can carry WHERE inside the Resource the choice landed, so it binds with four
    # arguments where the others bind with one, and its 400 is a name rather than a missing Resource.
    #
    # Since #142 the three scope arguments arrive from nobody: the door on the app's own surface has
    # no cascade position to send, the Scope is set afterwards by the route below, and #144 took the
    # argument off the client's own `bind` (`workbench/js/api.js`). They stay accepted here because
    # the capability underneath them is live — `_bind_from_handoff` records a scoped Binding in one
    # call through `bind_data_source`, which it reaches in process rather than over this route, so
    # narrowing the route would leave the two ways of recording the same Binding able to say
    # different things.
    if kind == KIND_DATA_SOURCE:
        try:
            return JSONResponse(content={"bindings": orchestrator.bind_data_source(
                resource_id, *_scope_levels(body))})
        except LookupError:
            return JSONResponse(status_code=404, content={"error": brand_text(
                "That {dataSource} is not one {platformName} offers you, so the app cannot depend "
                "on it."
            )})
        except ValueError as e:
            return JSONResponse(status_code=400, content={"error": str(e)})
        except ResourceUnavailable as e:
            return JSONResponse(status_code=502, content={"error": str(e)})
    # One route, one record, two kinds — each with its own "why not" sentence, because the two are
    # refused for different reasons and "ask an admin for a grant" is the wrong advice for a Model
    # API that simply is not deployed in this project.
    if kind == KIND_LLM_ALIAS:
        # Either it is not registered on the gateway, or this caller has no grant for it. Both mean
        # the same thing to the creator: the app cannot depend on it.
        bind, missing = orchestrator.bind_llm_alias, brand_text(
            "That {llmAlias} is not one you can use, so the app cannot depend on it."
        )
    elif kind == KIND_DATASET:
        # Its own sentence for the same reason the two below have theirs: a Dataset this caller
        # cannot see is not a grant to ask an admin for and not an undeployed model — it is a
        # {dataset} that is not mounted into this project.
        bind, missing = orchestrator.bind_dataset, brand_text(
            "That {dataset} is not one {platformName} mounts into this project, so the app cannot "
            "depend on it."
        )
    elif kind == KIND_MODEL_API:
        bind, missing = orchestrator.bind_model_api, brand_text(
            "{platformName} would not describe that {modelApi} to you, and {assistantName} holds "
            "no access token for it, so the app cannot depend on it. Paste its sample request to "
            "add it."
        )
    else:
        return JSONResponse(status_code=400, content={"error": f"unknown Resource kind: {kind}"})
    try:
        return JSONResponse(content={"bindings": bind(resource_id)})
    except CredentialRequired:
        # 409, not 404: the Model API is there and the record is refused because Sage is missing
        # something the creator can supply. The rail asks for the snippet before binding, so this is
        # the invariant holding rather than the usual path — but it has to say what to do anyway,
        # because anything that binds without going through the rail lands here.
        # `Overview` is the platform's own page, named the way the platform names it; only the
        # word for the platform itself is ours to replace.
        return JSONResponse(status_code=409, content={"error": brand_text(
            "{assistantName} needs this {modelApi}'s access token before an app can call it. Open "
            "the {modelApi}'s Overview page in {platformName}, copy the sample request, and paste "
            "it into {assistantName}."
        )})
    except LookupError:
        return JSONResponse(status_code=404, content={"error": missing})
    except ResourceUnavailable as e:
        return JSONResponse(status_code=502, content={"error": str(e)})


# The second act, against a Binding that already exists (#142, ADR-0021). Its own route rather than
# a second POST to the one above, because the two are refused for opposite reasons: that one turns
# down a Resource the platform does not offer, this one turns down a Resource the app does not
# depend on. Folded together, narrowing a Scope would quietly record the Binding it was narrowing.
#
# Only a Data Source is in the path, because only a Data Source has a Scope. A `{kind}` here would
# be a parameter with one legal value and three ways to be wrong.
@control_app.post("/api/bindings/data_source/{resource_id}/scope")
async def set_binding_scope(resource_id: str, request: Request) -> JSONResponse:
    """Choose which database, schema and table of a bound Data Source the app reads.

    The body carries the levels the creator picked, and an absent level is one they did not: an
    empty `schema` under a chosen `database` means the whole database, which is a real answer and
    not a half-finished one. Answers with the Binding list, as every binding route does.
    """
    body = await request.json()
    try:
        return JSONResponse(content={"bindings": orchestrator.scope_data_source(
            resource_id, *_scope_levels(body))})
    except ResourceNotBound:
        # Its own sentence, and its own cause. This is the app not depending on the Resource, which
        # the creator fixes with the OTHER door on the same surface — so the refusal names that act
        # rather than sending them to the platform to fix a grant that is not the problem.
        return JSONResponse(status_code=404, content={"error": brand_text(
            "This app doesn't need that {dataSource} to run, so there is no {scope} to set. "
            "Use it in the app first."
        )})
    except LookupError:
        # The other cause, which reads nothing like the first: the Binding is there and the platform
        # will no longer describe the Resource, so the Scope cannot be enumerated or checked.
        return JSONResponse(status_code=404, content={"error": brand_text(
            "That {dataSource} is not one {platformName} offers you, so its {scope} cannot be set."
        )})
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    except ResourceUnavailable as e:
        return JSONResponse(status_code=502, content={"error": str(e)})


# The click on a table candidate (#183). Its own route rather than a flag on the one above, for the
# reason that one is its own route rather than a flag on `add_binding`: what separates them is a
# precondition, and a boolean that decides whether a write is checked first is a parameter nobody
# can read at the call site. The record they write is the same record, written by the same writer.
#
# What it adds is the check. The candidate came off a list that may have been read minutes ago and
# cached for the session, so the one table being recorded is looked for again before it is written —
# a stale list costs a click, and a dropped table in the record costs the first viewer of the
# published app a screen that cannot load.
@control_app.post("/api/bindings/data_source/{resource_id}/candidate")
async def confirm_table_candidate(resource_id: str, request: Request) -> JSONResponse:
    """Record the table a person picked off a candidate card, once it is proved to still be there."""
    body = await request.json()
    database, schema, table = _scope_levels(body)
    if not table:
        # A candidate is always one table. Settling for the schema is the answer this whole path
        # exists to refuse: "somewhere in PUBLIC" does not say which table holds the data, and a
        # schema-level record is still available from the panel, the surface that owns that door.
        return JSONResponse(status_code=400, content={"error": brand_text(
            "Pick one {scope}. {assistantName} does not record a schema from here."
        )})
    # Which acts this click is claiming to be (#206). The merged card is drawn before anything is
    # bound and its click answers the store and the table together, so it says so and is written
    # through the door that records both. Said by the card rather than inferred from the manifest
    # being empty: a request that did not mean to declare a Binding must still be refused below,
    # and inferring it here would turn that refusal into a silent bind.
    write = (orchestrator.confirm_source_and_table_candidate if body.get("bindFirst")
             else orchestrator.confirm_table_candidate)
    try:
        return JSONResponse(content={"bindings": write(resource_id, database, schema, table)})
    except ResourceNotBound:
        return JSONResponse(status_code=404, content={"error": brand_text(
            "This app doesn't need that {dataSource} to run, so there is no {scope} to record. "
            "Use it in the app first."
        )})
    except LookupError:
        return JSONResponse(status_code=404, content={"error": brand_text(
            "That {dataSource} is not one {platformName} offers you, so its {scope} cannot be "
            "recorded."
        )})
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    except ResourceUnavailable as e:
        # Both halves of the check land here: the table that is gone, and the store that would not
        # say either way. Neither writes the record, which is the whole point of checking.
        return JSONResponse(status_code=502, content={"error": str(e)})


# Its own route rather than a field on /api/resources: a Model API listing is what the project
# offers, and this is what Sage remembers about it. The two come from different places and one must
# not fail because the other did.
@control_app.get("/api/model-api-credentials")
def list_model_api_credentials() -> JSONResponse:
    """Which Model APIs Sage holds a token for. Ids only — a token Sage has stored is never read
    back out over HTTP, so the rail can tell which rows will prompt without the value crossing
    into a page where a devtools panel is watching."""
    return JSONResponse(content={"ids": orchestrator.model_api_credential_ids()})


@control_app.post("/api/model-api-credentials")
async def add_model_api_credential(request: Request) -> JSONResponse:
    """Take a pasted sample request for one Model API, verify it, and remember it (#9).

    Always 200 when the request was well-formed, with `ok` saying whether the paste worked. The
    failures here are the creator's to fix in the form they are looking at — a bad token, the wrong
    model's snippet, half a paste — and an HTTP error code would make the rail treat them as Sage
    breaking rather than as an answer to render beside the box.
    """
    body = await request.json()
    # No id is a legal request since #42: a Model API the rail could not list has no row to open the
    # form under, so the snippet's own URL is what says which model this is. 400 stays for a body
    # that is not shaped like a request at all.
    resource_id, snippet = str(body.get("id") or ""), body.get("snippet")
    if not isinstance(snippet, str) or not snippet.strip():
        return JSONResponse(content={"ok": False, "error": brand_text(
            "Paste the sample request from the {modelApi}'s Overview page in {platformName}."
        )})
    return JSONResponse(content=orchestrator.save_model_api_credential(resource_id, snippet))


@control_app.delete("/api/bindings/{kind}/{resource_id}")
def remove_binding(kind: str, resource_id: str) -> JSONResponse:
    """Drop a Binding. The body carries `bindings` (the list, as every binding route does) plus
    `refs` — app source that still uses what was just removed, so the rail and the composer pill can
    both offer the cleanup the way detaching a file does."""
    return JSONResponse(content=orchestrator.unbind(kind, resource_id))


@control_app.get("/api/project/samples")
def sample_candidates() -> JSONResponse:
    """Which tables the creator could show the agent, and what is already shared (#16).

    Read from the schema recorded when the Scope was bound, so opening the choice costs no query and
    no wait. `bindable: false` means this app records no Data Source, and the panel says so rather
    than offering an empty list of tables.
    """
    return JSONResponse(content=orchestrator.sample_candidates())


@control_app.post("/api/project/samples")
async def share_samples(request: Request) -> JSONResponse:
    """Show the agent real rows from the tables the creator ticked.

    The payload is the creator's decision — which tables. An empty `tables` is not an error but
    the opposite choice: stop showing any.

    502 for a store that will not answer, as the cascade's routes do: the rows are the thing being
    asked for, so unlike a Binding there is nothing to record when they do not arrive.
    """
    body = await request.json()
    tables = body.get("tables")
    try:
        return JSONResponse(content=orchestrator.share_sample_rows(
            # Which store these tables are in. Optional, so a caller that predates several bound
            # sources still means the first one; a store this app has no Binding for is a 404.
            str(body.get("source") or ""),
            [str(t) for t in tables] if isinstance(tables, list) else [],
        ))
    except LookupError as e:
        return JSONResponse(status_code=404, content={"error": str(e) or brand_text(
            "This app is not recorded as using a {dataSource}.")})
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    except ResourceUnavailable as e:
        return JSONResponse(status_code=502, content={"error": str(e)})


@control_app.delete("/api/project/samples")
def stop_sharing_samples(source: str = "") -> JSONResponse:
    """Stop showing the agent rows from one Data Source, or from all of them when none is named.
    """
    return JSONResponse(content=orchestrator.clear_sample_rows(source))


# The ids the previous Preflight found, and the whole of ADR-0027's survival rule on this side: a
# Problem is reported only when the Preflight before this one found it too. Process-wide rather than
# per-session, because the condition is the deployment's rather than any one reader's — two open
# Workbenches asking are two Preflights, not two separate counts of the same fault.
_PREFLIGHT_SEEN: set[str] = set()
# FastAPI runs a sync route on a threadpool, so two open Workbenches asking at once would otherwise
# read-modify-write that set against each other — and the interleaving that loses is the one where
# a fault sighted once by one tab is reported as a survivor to the other, which is exactly the
# self-clearing blip the rule exists to swallow.
_PREFLIGHT_LOCK = threading.Lock()


@control_app.get("/api/health")
def health_problems() -> JSONResponse:
    """Every [[Problem]] this deployment has, in the creator's own words (ADR-0027).

    One composed route rather than six raw ones. The person reading it opened a Workbench to build
    something; `/api/diag` is the maintainer's surface and stays exactly as it is.

    No probe of its own. Four of the five reads are free — a global the boot Preflight filled, two
    numbers, an import that has already happened — and the fifth is the Binding check `/api/preflight`
    used to make, which costs a listing only for an app that has a Binding at all. The slot verdict
    is the boot one: `_run_slot_preflight` writes it once, `/healthz` serves the same global, and the
    two cannot drift because there is only one.

    Always 200, never 502. "We could not check" is a state, not a failure of the request; a 502 here
    would read to the UI exactly like the Resource rail's, where it means "you have no models". Each
    read is caught on its own, so one dead dependency costs its own answer and not the other four —
    a route that reports Problems must not become one.
    """
    from .service import _opencode_base_port

    def _read(fn, fallback):
        try:
            return fn()
        except Exception:
            log.exception("health: one read did not answer")
            return fallback

    found = health.problems(
        slots=PREFLIGHT_SLOTS,
        bindings=_read(orchestrator.preflight_bindings, {}),
        # Read inside `_read` rather than above it: a port that will not parse is a deployment this
        # route still has to answer for, and the port Problem it cannot judge stays silent.
        ports={"control_port": _read(lambda: int(os.environ.get("SAGE_CONTROL_PORT", "8080")), None),
               "base_port": _read(lambda: _opencode_base_port(orchestrator._opencode_cwd), None)},
        agents=_read(orchestrator.resolved_agents, None),
        data_library=_read(data_library_ready, ""),
    )
    global _PREFLIGHT_SEEN
    with _PREFLIGHT_LOCK:
        reported = health.survivors(_PREFLIGHT_SEEN, found)
        _PREFLIGHT_SEEN = {p.id for p in found}
    return JSONResponse(content={"problems": [p.to_dict() for p in reported]})


@control_app.get("/api/project/assets/{dataset_id}/files")
def list_asset_files(dataset_id: str) -> JSONResponse:
    try:
        return JSONResponse(content=orchestrator.list_asset_files(dataset_id))
    except LookupError:
        return JSONResponse(status_code=404, content={"error": brand_text("{dataset} not found")})
    except ResourceUnavailable as e:
        return JSONResponse(status_code=502, content={"error": str(e)})


@control_app.post("/api/project/assets/{dataset_id}/files/attach")
async def attach_file(dataset_id: str, request: Request) -> JSONResponse:
    path = (await request.json()).get("path")
    if not path:
        return JSONResponse(status_code=400, content={"error": "path required"})
    try:
        return JSONResponse(content=orchestrator.attach_file(dataset_id, path))
    except LookupError:
        return JSONResponse(status_code=404, content={"error": brand_text("{dataset} not found")})
    except FileNotFoundError:
        return JSONResponse(status_code=404, content={"error": brand_text(
            "file not found in the {dataset}")})
    except ValueError:
        return JSONResponse(status_code=400, content={"error": "invalid file path"})
    except ResourceUnavailable as e:
        return JSONResponse(status_code=502, content={"error": str(e)})
    except AttachTooLarge as e:
        mb = e.cap / (1024 * 1024)
        return JSONResponse(
            status_code=413,
            content={"error": f"attaching this file would exceed the {mb:.0f} MB limit for attached data"},
        )


async def _folder_of(request: Request) -> object | None:
    """The `folder` a folder-act body names, or `None` when the body cannot carry one.

    `None` for a body that is not a JSON object, so both routes answer a malformed envelope with the
    same 400 their `folder` guard gives a malformed field, rather than the 500 an `AttributeError`
    on `[]` or a decode error on an empty body used to reach.
    """
    try:
        body = await request.json()
    except ValueError:
        return None
    return body.get("folder") if isinstance(body, dict) else None


@control_app.post("/api/project/assets/{dataset_id}/files/attach-folder")
async def attach_folder(dataset_id: str, request: Request) -> JSONResponse:
    """Attach every file below one Dataset folder, in one act (ADR-0029).

    `folder` is required and may be `""` — the Dataset root is this act at depth 0, not a second
    route with its own name. A missing key is a malformed body rather than a root attach, because
    the two are far too different to guess between.
    """
    # The ENVELOPE as well as the field. `[]`, `"raw"` and `null` have no `.get`, and an empty body
    # has no JSON at all — both raise before the guard below and left the route answering an opaque
    # framework 500 for a body it can perfectly well call malformed.
    folder = (await _folder_of(request))
    # A STRING, not merely present: `0`, `false` and `[]` all read as `""` further in, which is the
    # Dataset root — the one value this route refuses to guess its way to.
    if not isinstance(folder, str):
        return JSONResponse(status_code=400, content={"error": "folder required"})
    try:
        return JSONResponse(content=orchestrator.attach_folder(dataset_id, folder))
    except LookupError:
        return JSONResponse(status_code=404, content={"error": brand_text("{dataset} not found")})
    except AttachWouldClobber as e:
        # Nothing was attached, and nothing was overwritten either — which is the point of saying so
        # instead of writing over it. Named at the path, because that is where the person looks.
        return JSONResponse(status_code=409, content={"error": brand_text(
            "Nothing was attached. Something already sits at {path} that {assistantName} did not "
            "put there, and it is not overwritten. Move or remove it, then attach the folder.",
            path=e.path)})
    except AttachSourceMissing as e:
        # The folder is there; a file the listing named is not. Saying "folder not found" would
        # contradict the row that was just clicked, which showed the count and the size.
        return JSONResponse(status_code=404, content={"error": brand_text(
            "Nothing was attached. {assistantName} listed a file this {dataset} no longer holds "
            "({path}). Reopen this panel to list it again.", path=e.path)})
    except FileNotFoundError:
        return JSONResponse(status_code=404, content={"error": brand_text(
            "folder not found in the {dataset}")})
    except ValueError:
        return JSONResponse(status_code=400, content={"error": "invalid folder"})
    except FolderActUnavailable as e:
        # The reason the row already carried, said again by the act it withheld — one reason,
        # composed once, so the two can never disagree.
        return JSONResponse(status_code=409, content={"error": e.reason})
    except ResourceUnavailable as e:
        return JSONResponse(status_code=502, content={"error": str(e)})
    except OSError:
        # The unwind above already took back every link this act made, so nothing is half-attached.
        log.exception("attach_folder: could not write the links")
        return JSONResponse(status_code=500, content={"error": brand_text(
            "Nothing was attached. {assistantName} could not write into this app's files.")})
    except AttachTooLarge as e:
        # The three numbers ADR-0029 asks a refusal to name: what the folder weighs, what the app
        # already carries, and the cap. A refusal that names them is a decision a person can act on.
        return JSONResponse(status_code=413, content={"error": brand_text(
            "Attaching this folder ({folder}) would take this app over the {cap} limit for "
            "attached data. It already carries {current}.",
            folder=human_bytes(e.incoming), cap=human_bytes(e.cap),
            current=human_bytes(e.current),
        )})


@control_app.post("/api/project/assets/{dataset_id}/files/detach-folder")
async def detach_folder(dataset_id: str, request: Request) -> JSONResponse:
    """Remove every file the app carries below one Dataset folder, in one act (ADR-0029).

    The mirror of `attach-folder`, including the shape of its body: `folder` is required and may be
    `""` for the Dataset root, and a missing key is malformed rather than a silent whole-Dataset
    removal.
    """
    folder = (await _folder_of(request))
    if not isinstance(folder, str):
        return JSONResponse(status_code=400, content={"error": "folder required"})
    try:
        return JSONResponse(content=orchestrator.detach_folder(dataset_id, folder))
    except LookupError:
        return JSONResponse(status_code=404, content={"error": brand_text("{dataset} not found")})
    except DataReferenced as e:
        # No partial state: nothing was removed. The refusal names the FILES rather than a count,
        # because the files are the thing a person can go and act on — and it names the way out,
        # which is the per-file door, since that one reports what still uses a file instead of
        # refusing over it.
        if not e.files:
            # No file to name: the source fetches the FOLDER, building each path from a pattern, so
            # one line of code is the dependency and it stands for every file below it.
            where = ", ".join(e.refs[:3]) + (", …" if len(e.refs) > 3 else "")
            return JSONResponse(status_code=409, content={
                "error": f"Can't remove this folder — your app reads files from it ({where}). "
                         "Nothing was removed. Edit the app to stop reading from this folder first.",
                "files": [], "refs": e.refs, "copies": e.copies,
            })
        served = [f.removeprefix("public/data/") for f in e.files]
        named = ", ".join(served[:3]) + (", …" if len(served) > 3 else "")
        # The way out names the per-file door for what it is. That one REPORTS what still uses a
        # file instead of refusing over it, which is the whole reason it remains a way out — and
        # saying "remove them one at a time" without that reads like a trick for getting past this.
        tail = ("Nothing was removed. Edit the app to stop using {them}, or remove {it} from the "
                "app's own file list, which says what still uses a file rather than refusing.")
        if len(served) == 1:
            msg = (f"Can't remove this folder — your app still uses one of its files ({named}). "
                   + tail.format(them="that file", it="it"))
        else:
            msg = (f"Can't remove this folder — your app still uses {len(served)} of its files "
                   f"({named}). " + tail.format(them="them", it="them"))
        return JSONResponse(status_code=409, content={
            "error": msg, "files": e.files, "refs": e.refs, "copies": e.copies,
        })
    except ValueError:
        return JSONResponse(status_code=400, content={"error": "invalid folder"})
    except ResourceUnavailable as e:
        # Only reachable when the app carries nothing from this Dataset yet, since that is the one
        # branch that has to ask the platform for its name. The sibling route answers the same way.
        return JSONResponse(status_code=502, content={"error": str(e)})
    except DetachStopped as e:
        # The record follows the disk, so the count is real. Which SENTENCE is true depends on it:
        # the first entry can fail as easily as the hundredth, and telling someone to go looking for
        # files that all turned out to still be there is its own wrong answer.
        log.exception("detach_folder: removal stopped after %s", e.detached)
        if not e.recorded:
            # The files went and the manifest did not follow, so the file list every other sentence
            # here points at is not the record of anything.
            msg = brand_text(
                "The removal ran, but {assistantName} could not write this app's record of it. "
                "Reopen the app to see what it carries now.")
        elif e.detached:
            msg = brand_text(
                "The removal stopped part way through. Some of the folder is out of this app "
                "already — its own file list is the record of what it still carries.")
        else:
            msg = brand_text(
                "Nothing was removed — {assistantName} could not change this app's files.")
        if e.removed:
            # Copies deleted out of `src/` on the way. They were never in the manifest, so the file
            # list this points at for everything else is no record of them.
            msg += " These copies went with them: " + ", ".join(e.removed) + "."
        if e.kept:
            msg += (" These share a name and were left in place: " + ", ".join(e.kept)
                    + " — check they are yours before saving.")
        return JSONResponse(status_code=500, content={
            "error": msg, "removed_copies": e.removed, "kept_copies": e.kept})
    except OSError:
        # AFTER `DetachStopped`, which is one of these and carries what actually happened. Anything
        # left here failed OUTSIDE the removal — opening the project, reading its status — so this
        # claims nothing about what moved, because nothing did.
        log.exception("detach_folder: failed before the removal")
        return JSONResponse(status_code=500, content={"error": brand_text(
            "Nothing was removed — {assistantName} could not read this app.")})


@control_app.post("/api/project/files/detach")
async def detach_file(request: Request) -> JSONResponse:
    path = (await request.json()).get("path")
    if not path:
        return JSONResponse(status_code=400, content={"error": "path required"})
    try:
        return JSONResponse(content=orchestrator.detach_file(path))
    except ValueError:
        return JSONResponse(status_code=400, content={"error": "invalid path"})


@control_app.post("/api/project/upload")
async def upload_file(request: Request) -> JSONResponse:
    # Raw-body upload (avoids a python-multipart dependency): the file bytes are the request body;
    # the name and optional target dataset ride in query params. One file per request.
    filename = request.query_params.get("name", "")
    dataset_id = request.query_params.get("dataset") or None
    data = await request.body()
    if not data:
        return JSONResponse(status_code=400, content={"error": "empty upload"})
    try:
        if dataset_id:
            return JSONResponse(content=orchestrator.upload_file(filename, data, dataset_id))
        return JSONResponse(content=orchestrator.upload_scratch(filename, data))
    except ValueError:
        return JSONResponse(status_code=400, content={"error": "invalid filename"})
    except UploadUnavailable:
        msg = brand_text(
            "The {dataset} you picked isn't mounted and writable in this workspace."
            if dataset_id
            else "No writable {dataset} is available to store uploads in this project."
        )
        return JSONResponse(status_code=409, content={"error": msg})
    except AttachTooLarge as e:
        mb = e.cap / (1024 * 1024)
        return JSONResponse(
            status_code=413,
            content={"error": f"uploading this file would exceed the {mb:.0f} MB limit for attached data"},
        )


@control_app.post("/api/project/scratch/promote")
async def promote_scratch(request: Request) -> JSONResponse:
    body = await request.json()
    path = (body or {}).get("path") or ""
    dataset_id = (body or {}).get("dataset") or (body or {}).get("dataset_id") or ""
    try:
        return JSONResponse(content=orchestrator.promote_scratch_to_dataset(path, dataset_id))
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    except FileNotFoundError:
        return JSONResponse(status_code=404, content={"error": "scratch file not found"})
    except LookupError:
        return JSONResponse(status_code=404, content={"error": brand_text("{dataset} not found")})
    except UploadUnavailable:
        return JSONResponse(
            status_code=409,
            content={"error": brand_text(
                "The {dataset} you picked isn't mounted and writable in this workspace.")},
        )
    except AttachTooLarge as e:
        mb = e.cap / (1024 * 1024)
        return JSONResponse(
            status_code=413,
            content={"error": f"promoting this file would exceed the {mb:.0f} MB limit for attached data"},
        )


@control_app.post("/api/project/files/delete")
async def delete_file(request: Request) -> JSONResponse:
    path = (await request.json()).get("path")
    if not path:
        return JSONResponse(status_code=400, content={"error": "path required"})
    try:
        return JSONResponse(content=orchestrator.delete_file(path))
    except DataReferenced as e:
        where = sorted(set(e.copies or e.refs))
        files = ", ".join(where[:3]) + ("…" if len(where) > 3 else "")
        verb = "has a copy of" if e.copies else "uses"
        msg = (f"Can't delete — your app {verb} this file ({files}). Remove it from the app first, "
               f"or use Detach to drop it from the workspace while keeping the data.")
        return JSONResponse(status_code=409, content={"error": msg, "refs": e.refs, "copies": e.copies})
    except ValueError:
        return JSONResponse(status_code=400, content={"error": "invalid path"})


@control_app.post("/api/project/scratch/delete")
async def delete_scratch(request: Request) -> JSONResponse:
    path = (await request.json()).get("path")
    if not path:
        return JSONResponse(status_code=400, content={"error": "path required"})
    try:
        return JSONResponse(content=orchestrator.delete_scratch(path))
    except ValueError:
        return JSONResponse(status_code=400, content={"error": "invalid path"})
    except OSError as e:
        return JSONResponse(status_code=500, content={"error": f"Could not delete {path}: {e}"})


def _pump_events(events, q: queue.Queue) -> None:
    """Drain a turn's event generator into a queue on a worker thread, so the response side can
    interleave keepalives during the gaps. The bytes twin of this is `sage.shim.keepalive.pump`."""
    try:
        for evt in events:
            q.put(evt)
        q.put(ka.DONE)
    except BaseException as e:
        q.put(("error", e))


def _turn_sse(events, what: str):
    """One turn's events as SSE, with the connection kept warm through its silent gaps.

    The same treatment the shim's /v1 stream has had since it was losing OpenCode's requests to a
    "TypeError: network error", applied to the stream the BROWSER reads — the one place it was
    missing. A turn goes quiet whenever the agent is thinking rather than calling a tool, and on a
    built project a plan turn spends half a minute there routinely (30s+ gaps between tool calls,
    live on 2026-08-24). An idle response is what an intermediary times out, and when it did, the
    browser saw the stream die while the turn ran on server-side — which is the "Lost the connection
    to this build — it's still running" the UI then falls back to.

    A comment frame is the right filler: every SSE parser ignores it, so no consumer has to know it
    happened, and it resets each read timer between here and the browser.

    Running the generator on our own thread also settles what a disconnect means, and settles it the
    way the UI already assumes: the turn is not cancelled, it runs to completion, and the browser
    rejoins it by polling the transcript (see resumeRunningTurn). It also keeps the event loop free
    to serve the /v1 model calls the turn makes, which is what the threadpooled sync generator did
    before.
    """
    import json as _json

    q: queue.Queue = queue.Queue()
    threading.Thread(target=_pump_events, args=(events, q),
                     name=f"sage-{what}", daemon=True).start()
    while True:
        item = ka.get(q, ka.KEEPALIVE_INTERVAL_S)
        if item is ka.EMPTY:
            yield ka.KEEPALIVE.decode()
            continue
        if item is ka.DONE:
            return
        if ka.is_error(item):
            e = item[1]
            log.exception("%s failed", what, exc_info=e)
            yield f"data: {_json.dumps({'type': 'error', 'message': f'{type(e).__name__}: {e}'})}\n\n"
            return
        yield f"data: {_json.dumps(item)}\n\n"


@control_app.post("/api/project/build/stream")
def build_stream(body: dict) -> StreamingResponse:
    """Streaming build: SSE of progress events (agent text/tool, typecheck, done). Follow-up
    prompts reuse the session (modify/add features)."""
    import json as _json

    prompt = (body or {}).get("prompt", "")
    mentions = (body or {}).get("mentions") or None  # workspace paths of @-referenced attached files
    # @-referenced Resources (#31), as `{"kind", "id"}` Binding identities. Their own field rather
    # than more entries in `mentions`: a Resource has no path, so nothing here resolves to a file.
    resources = (body or {}).get("resources") or None
    # Which Build conversation this turn belongs to (Thread id). Absent for an unscoped caller.
    conversation = (body or {}).get("conversation") or None
    # Set only by the buttons on a reset-offer (#36), which is that offer being answered rather than
    # skipped — the confirmation the gate exists to collect has already happened by then.
    skip_reset_gate = bool((body or {}).get("skipResetGate"))
    # Set by either button on an incoming-changes offer (#78). Pulling answers that offer as much
    # as building past it does, so both arrive here and neither is asked the same question twice.
    skip_incoming_gate = bool((body or {}).get("skipIncomingGate"))
    # Set by the click on a table candidate (#183), which is that card being answered: the record is
    # written by then, and this is the original request being replayed against it as a new turn.
    skip_table_gate = bool((body or {}).get("skipTableGate"))
    # The other button on a Data Source card (#185): build with no store at all. Its own field
    # rather than `skipTableGate`, because they answer two different questions — and the pick
    # beside it sets neither, since a recorded Data Source is what stops the card coming back.
    skip_source_gate = bool((body or {}).get("skipSourceGate"))
    # And the pick itself, which the table search runs against whatever the prose named.
    chosen_source = str((body or {}).get("chosenSource") or "")
    # Set by either button on a Dataset card (#196): a file or folder was attached, or the person
    # chose to build with nothing attached. Both answer the card, so both arrive here.
    skip_dataset_gate = bool((body or {}).get("skipDatasetGate"))
    # And the way past it alone, which names the Dataset so the answer outlives this one request:
    # the gate reads the app's state rather than the request's words, so without a name to remember
    # it asks again on the next sentence, whatever the sentence is about.
    dismissed_dataset = str((body or {}).get("datasetDismissed") or "")
    # And the row that was clicked, which is the only pick on this door that travels on the request.
    # The Data Source and table picks are read back off the Binding they wrote; an attach writes one
    # manifest entry per file, so a folder pick writes many and none of them names the folder. See
    # `_picked_dataset_text`. Empty for the way past, which attached nothing to name.
    dataset_pick = str((body or {}).get("datasetPick") or "")

    def refuse_with(message: str) -> StreamingResponse:
        def refuse():
            yield f"data: {_json.dumps({'type': 'error', 'message': message})}\n\n"
        return StreamingResponse(refuse(), media_type="text/event-stream")

    if not prompt:
        return refuse_with("prompt required")
    # `conversation` becomes a directory under `.sage/threads/` and is written through
    # `mkdir(parents=True)`. `safe_id` refuses anything that is not one path segment, and it is
    # asked here as well as there so the answer is an SSE `error` the composer already renders
    # rather than a traceback halfway into a stream the browser has begun reading.
    if conversation is not None:
        try:
            safe_id(str(conversation), "conversation id")
        except ValueError:
            return refuse_with("unknown conversation")

    return StreamingResponse(
        _turn_sse(orchestrator.build_stream(prompt, mentions, resources, conversation,
                                            skip_reset_gate, skip_incoming_gate, skip_table_gate,
                                            skip_source_gate, chosen_source, skip_dataset_gate,
                                            dismissed_dataset, dataset_pick),
                  "build_stream"),
        media_type="text/event-stream")


# The Build rail's list, as the Chat rail's is /api/threads. Two lists, one per mode: a Project
# holds many Built Apps and many Threads, and neither is the other (ADR-0008).
@control_app.get("/api/apps")
def list_apps() -> JSONResponse:
    items = orchestrator.list_apps()
    return JSONResponse({"items": items,
                         "selected": next((r["id"] for r in items if r["selected"]), "")})


@control_app.post("/api/apps")
def create_app() -> JSONResponse:
    """New app in the Build rail: minted, seeded and selected, with no Thread and no plan behind
    it. The plan gate fires on its first turn because it has not been built (#74)."""
    try:
        return JSONResponse(orchestrator.create_app())
    except TurnBusy as e:
        # Only the turn-lock refusal, as in `select_app` — anything else is a real failure and must
        # not be reported to the person as a build they can wait out. The sentence is the service's:
        # it is the only side that knows whether the lock has a turn behind it or a wedge (#97).
        return JSONResponse({"error": str(e)}, status_code=409)


@control_app.post("/api/apps/{app_id}/select")
def select_app(app_id: str) -> JSONResponse:
    """Point Build at another Built App. Looking is free — this changes neither app, and it is never
    refused for a running build: the build carries on in the app it started in and the rail marks
    that row (#77)."""
    try:
        return JSONResponse({"ok": True, "app": orchestrator.select_app(app_id)})
    except KeyError:
        return JSONResponse({"error": "unknown app"}, status_code=404)


@control_app.delete("/api/apps/{app_id}")
def delete_app(app_id: str, domino_app: str = "keep") -> JSONResponse:
    """Remove a Built App from the Project (#76). `domino_app=delete` also deletes the Domino App
    this one publishes to; anything else leaves that App running — see Orchestrator.delete_app.

    The default is `keep` because it is the answer that destroys less: a request that forgot to say
    leaves a URL serving, and a URL that is still serving can still be deleted."""
    try:
        return JSONResponse(orchestrator.delete_app(app_id,
                                                    delete_domino_app=domino_app == "delete"))
    except KeyError:
        return JSONResponse({"error": "unknown app"}, status_code=404)
    except TurnBusy as e:
        # The turn-lock refusal is the one a person can wait out — or, after a wedge, the one that
        # names the restart. Either way the service wrote the sentence (#97).
        return JSONResponse({"error": str(e)}, status_code=409)
    except RuntimeError as e:
        # Anything else here is a real failure — the control plane refusing to delete the Domino
        # App, most of all, and that one has to reach them as itself, because the Built App is
        # still there.
        return JSONResponse({"error": str(e)}, status_code=502)


@control_app.patch("/api/apps/{app_id}")
async def patch_app(app_id: str, request: Request) -> JSONResponse:
    """Rename a Built App. Only the display name is writable — the id names the directory, and a
    published App's entry point is fixed at creation.

    Offloaded to a thread, as Publish is: since #219 a rename carries the name to the deployed App,
    which is a blocking control-plane call with a 30s timeout on it. Left inline on the event loop,
    one slow Domino API would stall the whole Workbench server — build streams included — for as
    long as it took to answer."""
    body = await request.json()
    try:
        return JSONResponse(await run_in_threadpool(
            orchestrator.rename_app, app_id, str((body or {}).get("name") or "")))
    except KeyError:
        return JSONResponse({"error": "unknown app"}, status_code=404)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@control_app.get("/api/threads")
def list_threads() -> JSONResponse:
    return JSONResponse(orchestrator.list_threads())


@control_app.post("/api/threads")
def create_thread() -> JSONResponse:
    return JSONResponse(orchestrator.create_thread())


@control_app.post("/api/threads/save")
def flush_chat_save() -> JSONResponse:
    """Push dirty Chat files now (leaving Chat, or switching Thread). No-op if nothing is dirty."""
    result = orchestrator.flush_chat_save()
    if result is None:
        return JSONResponse({"type": "saved", "ok": True, "pushed": False, "detail": "nothing to save"})
    return JSONResponse(result)


@control_app.get("/api/threads/{thread_id}")
def get_thread(thread_id: str) -> JSONResponse:
    try:
        return JSONResponse(orchestrator.get_thread(thread_id))
    except KeyError:
        return JSONResponse({"error": "unknown thread"}, status_code=404)


@control_app.patch("/api/threads/{thread_id}")
def patch_thread(thread_id: str, body: dict) -> JSONResponse:
    try:
        return JSONResponse(orchestrator.patch_thread(thread_id, body or {}))
    except KeyError:
        return JSONResponse({"error": "unknown thread"}, status_code=404)


@control_app.delete("/api/threads/{thread_id}")
def delete_thread(thread_id: str) -> JSONResponse:
    # Carries the save's answer, not a bare ok: the files are gone here whatever git said, and a
    # push that did not land leaves the Conversation on the remote for the next start to find
    # (ADR-0036). Only the person can judge that, so only the person can be told.
    try:
        return JSONResponse(orchestrator.delete_thread(thread_id))
    except KeyError:
        return JSONResponse({"error": "unknown thread"}, status_code=404)


@control_app.post("/api/threads/{thread_id}/handoff/plan")
def draft_handoff_plan(thread_id: str) -> JSONResponse:
    try:
        return JSONResponse(orchestrator.draft_handoff_plan(thread_id))
    except KeyError:
        return JSONResponse({"error": "unknown thread"}, status_code=404)
    except TurnBusy as e:
        return JSONResponse({"error": str(e)}, status_code=409)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=502)


@control_app.post("/api/threads/{thread_id}/handoff/confirm")
def confirm_handoff(thread_id: str, body: dict) -> JSONResponse:
    try:
        return JSONResponse(orchestrator.confirm_handoff(
            thread_id, (body or {}).get("include") or {}, (body or {}).get("target") or {}))
    except KeyError:
        return JSONResponse({"error": "unknown thread"}, status_code=404)
    except TurnBusy as e:
        return JSONResponse({"error": str(e)}, status_code=409)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@control_app.post("/api/threads/{thread_id}/handoff/recross")
def recross_handoff(thread_id: str, body: dict | None = None) -> JSONResponse:
    """Redo a confirmed handoff's crossing with different answers — Change on the plan card (#60).

    Beside the confirm rather than part of it: confirming writes a plan card, and a handoff has
    exactly one. There is no `target` here and there never will be — ADR-0008 makes which Built App
    a handoff lands in a per-handoff decision the sheet asks every time."""
    try:
        return JSONResponse(orchestrator.recross_handoff(
            thread_id, (body or {}).get("include") or {},
            str((body or {}).get("planId") or "")))
    except KeyError:
        return JSONResponse({"error": "unknown thread"}, status_code=404)
    except TurnBusy as e:
        return JSONResponse({"error": str(e)}, status_code=409)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@control_app.get("/api/threads/{thread_id}/history")
def thread_history(thread_id: str) -> JSONResponse:
    return JSONResponse(orchestrator.thread_history(thread_id))


@control_app.get("/api/threads/{thread_id}/conversation")
def thread_conversation(thread_id: str) -> JSONResponse:
    """Both halves of one Conversation, merged and labelled (#56).

    Beside `/history` rather than replacing it: that one answers "what did Chat say", which is what
    the split conversation view still asks and must keep getting the same answer to. This one
    answers "what did this Conversation do", which is a question about the Project's Built Apps as
    well — see Orchestrator.conversation_history."""
    return JSONResponse({"history": orchestrator.conversation_history(thread_id)})


@control_app.get("/api/threads/{thread_id}/context")
def thread_context(thread_id: str) -> JSONResponse:
    return JSONResponse(orchestrator.thread_context(thread_id))


@control_app.post("/mcp/live-read")
async def live_read_mcp(request: Request) -> Response:
    """The Live read tools, as an MCP server OpenCode connects to (ADR-0041).

    Loopback only, and it carries no auth of its own: what gates a read is the per-turn token
    inside the call, not who knocked. A caller with no token can reach this and read nothing.

    Answers JSON rather than an event stream. The Streamable HTTP transport allows either for a
    single response, and there is nothing here that streams — a Live read returns once.

    Verified against the pinned OpenCode 1.18.4: after initialising it also opens a `GET` here with
    `Accept: text/event-stream`, the optional server-to-client channel. There is no route for that,
    so it gets a 405 and the connection stands anyway. Do not "fix" that into a stream nothing sends
    anything down.
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}},
            status_code=400,
        )
    batch = isinstance(body, list)
    probe = request.headers.get("x-sage-diag-probe") == "1"
    out = [r for r in (orchestrator.live_read_call(m, probe=probe) for m in (body if batch else [body]))
           if r is not None]
    if not out:
        # Every message was a notification. 202 with no body is what the transport expects.
        return Response(status_code=202)
    return JSONResponse(out if batch else out[0])


@control_app.post("/api/threads/{thread_id}/context")
def add_thread_context(thread_id: str, body: dict) -> JSONResponse:
    try:
        row = orchestrator.add_thread_context(thread_id, body or {})
    except KeyError:
        return JSONResponse({"error": "unknown thread"}, status_code=404)
    return JSONResponse(row)


@control_app.delete("/api/threads/{thread_id}/context/{item_id}")
def remove_thread_context(thread_id: str, item_id: str) -> JSONResponse:
    ok = orchestrator.remove_thread_context(thread_id, item_id)
    if not ok:
        return JSONResponse({"error": "unknown context item"}, status_code=404)
    return JSONResponse({"ok": True})


@control_app.post("/api/threads/{thread_id}/chat/stream")
def chat_stream(thread_id: str, body: dict) -> StreamingResponse:
    prompt = (body or {}).get("prompt", "")
    if not prompt:
        def refuse():
            import json as _json
            yield f"data: {_json.dumps({'type': 'error', 'message': 'prompt required'})}\n\n"
        return StreamingResponse(refuse(), media_type="text/event-stream")
    # The two skip flags are a card being answered, not a gate being skipped (#188, #196): the turn
    # that drew the card wrote the question to the Thread, so a replay must not write it a second
    # time. `datasetDismissed` outlives this request — see the build route above for why.
    return StreamingResponse(
        _turn_sse(orchestrator.chat_stream(
            thread_id, prompt,
            skip_table_gate=bool((body or {}).get("skipTableGate")),
            skip_dataset_gate=bool((body or {}).get("skipDatasetGate")),
            dismissed_dataset=str((body or {}).get("datasetDismissed") or "")), "chat_stream"),
        media_type="text/event-stream")


# The Chat half of the candidate click (#188). Its own route rather than the Binding one above,
# because what it writes is a different record: Chat has no Built App to depend on anything, so the
# choice goes on the Thread's own context row and crosses into a Binding at the handoff.
@control_app.post("/api/threads/{thread_id}/context/data_source/{resource_id}/candidate")
async def confirm_thread_table_candidate(thread_id: str, resource_id: str,
                                         request: Request) -> JSONResponse:
    """Record the table a person picked off a candidate card in Chat, once it is proved still there."""
    body = await request.json()
    database, schema, table = _scope_levels(body)
    if not table:
        # A candidate is always one table, here as much as on the Binding door: "somewhere in
        # PUBLIC" does not say which table holds the data.
        return JSONResponse(status_code=400, content={"error": brand_text(
            "Pick one {scope}. {assistantName} does not record a schema from here."
        )})
    try:
        return JSONResponse(content=orchestrator.confirm_thread_table_candidate(
            thread_id, resource_id, database, schema, table))
    except KeyError:
        return JSONResponse(status_code=404, content={"error": "unknown thread"})
    except ResourceNotBound:
        return JSONResponse(status_code=404, content={"error": brand_text(
            "This {chat} isn't using that {dataSource}, so there is no {scope} to record. "
            "Add it to the {chat} first."
        )})
    except LookupError:
        return JSONResponse(status_code=404, content={"error": brand_text(
            "That {dataSource} is not one {platformName} offers you, so its {scope} cannot be "
            "recorded."
        )})
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    except ResourceUnavailable as e:
        # The table has gone since the card was drawn, which is the whole point of checking.
        return JSONResponse(status_code=502, content={"error": str(e)})


# The Dataset card's click, in Chat (#196, ADR-0039). Its own route rather than the plain context
# door beside it, because the card knows a Dataset id and a Dataset-relative path and nothing else:
# the chip's leaf id, its name and its parent are all derivable from those two, and deriving them on
# the client is the second copy that produced the fabricated `<datasetId>:<relPath>` path this
# codebase already had to hunt down (see `_dataset_pseudo_path`).
@control_app.post("/api/threads/{thread_id}/context/dataset/{dataset_id}/file")
async def pin_thread_dataset_file(thread_id: str, dataset_id: str,
                                  request: Request) -> JSONResponse:
    """Pin the file a person picked off a Dataset card to this Thread's Session context."""
    body = await request.json()
    path = str((body or {}).get("path") or "").strip()
    if not path:
        return JSONResponse(status_code=400, content={"error": brand_text(
            "Pick one file. {assistantName} does not add a whole {dataset} from here."
        )})
    try:
        return JSONResponse(content=orchestrator.confirm_thread_dataset_file(
            thread_id, dataset_id, path))
    except KeyError:
        return JSONResponse(status_code=404, content={"error": "unknown thread"})
    except LookupError:
        return JSONResponse(status_code=404, content={"error": brand_text(
            "That {dataset} is not one {platformName} offers you, so its files cannot be added."
        )})
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    except ResourceUnavailable as e:
        return JSONResponse(status_code=502, content={"error": str(e)})


@control_app.post("/api/threads/{thread_id}/handoff/decline")
def decline_handoff(thread_id: str) -> StreamingResponse:
    """`Not now` on a Build offer. Streams, because declining an offer that was made INSTEAD of an
    answer has to produce the answer — see `Orchestrator.decline_handoff_stream`."""
    return StreamingResponse(
        _turn_sse(orchestrator.decline_handoff_stream(thread_id), "decline_handoff"),
        media_type="text/event-stream")


@control_app.post("/api/threads/{thread_id}/recall/clear")
def clear_recall(thread_id: str, body: dict = Body(default={})) -> dict:
    """Start the model over on a Conversation the gateway keeps refusing (ADR-0022).

    Not a stream, unlike `Not now` on a Build offer: that one had to produce the answer it was
    offered instead of, while this one is offered AFTER a turn already failed and has no answer
    owed. The person asks their question again themselves, which is also the only way to find out
    whether clearing worked.
    """
    scope = str((body or {}).get("scope") or "")
    try:
        return orchestrator.clear_recall(thread_id, scope)
    except KeyError:
        return JSONResponse(status_code=404, content={"error": "Unknown conversation"})
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})


@control_app.post("/api/project/recall/clear")
def clear_build_recall(body: dict = Body(default={})) -> dict:
    """The Build half of the route above (ADR-0022).

    Its own route rather than a `conversation` on that one, because the two clears empty different
    sessions: a Build Conversation's session is filed per (conversation, app), and one Conversation
    can drive several Built Apps. Routing both through the Thread would make "start over" mean
    whichever of those the caller happened to be looking at.

    `conversation` is optional for the reason it is optional everywhere else in Build: an empty one
    is the unscoped Build turn, which has a session and a transcript like any other.
    """
    scope = str((body or {}).get("scope") or "")
    try:
        return orchestrator.clear_build_recall(scope, str((body or {}).get("conversation") or ""))
    except TurnBusy as e:
        return JSONResponse(status_code=409, content={"error": str(e)})
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})


@control_app.post("/api/threads/{thread_id}/recall/withhold")
def withhold_recall(thread_id: str, body: dict = Body(default={})) -> dict:
    """Stop sending one named thing the gateway's guardrail refuses (ADR-0022).

    The rung below `recall/clear`, and the one to reach for first: clearing empties Recall, while
    this takes away one file or one message the search has already proved is the reason. The
    Conversation, its transcript and the file on disk all stand.

    Body: `{keys: [...], labels: [...], prompt: bool}`, straight off the `withhold-found` row —
    fingerprints and names, never the refused text. Not a stream, for the reason the clear above is
    not: the turn it belongs to has already failed and owes no answer. Whether to re-run that turn
    is the caller's call, because only the caller knows whether anything the turn read survived the
    withhold. `prompt` rides along so the receipt still knows, after a reload, that the person's own
    question was what went — which is the one case where retyping it fails without a word.
    """
    try:
        return orchestrator.withhold_content(thread_id, (body or {}).get("keys") or [],
                                             (body or {}).get("labels") or [],
                                             bool((body or {}).get("prompt")))
    except KeyError:
        return JSONResponse(status_code=404, content={"error": "Unknown conversation"})
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})


@control_app.post("/api/project/recall/withhold")
def withhold_build_recall(body: dict = Body(default={})) -> dict:
    """The Build half of the route above — same row, the app's own transcript.

    Its own route for the reason the Build clear has one: a Build Conversation's transcript is filed
    per (conversation, app), and one Conversation can drive several Built Apps.
    """
    try:
        return orchestrator.withhold_build_content(
            (body or {}).get("keys") or [], (body or {}).get("labels") or [],
            str((body or {}).get("conversation") or ""),
            bool((body or {}).get("prompt")))
    except TurnBusy as e:
        return JSONResponse(status_code=409, content={"error": str(e)})
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})


@control_app.post("/api/project/build/approve")
def build_approve(body: dict) -> StreamingResponse:
    """Approve a gated plan (SPEC P6) and stream the build.

    Body: {answers?, plan_edits?, plan_id?, build_again?}. `build_again` is the Plan page's "Build
    this again" (ADR-0024) — an approve of a plan that has already built once, whose text a person
    has just edited. It is asked for explicitly rather than worked out from the state on disk, so
    the two side effects it carries never reach an ordinary approve."""
    answers = (body or {}).get("answers", "") or ""
    plan_edits = (body or {}).get("plan_edits")  # None = approve the plan as proposed
    conversation = (body or {}).get("conversation") or None
    plan_id = (body or {}).get("plan_id") or ""   # "" = no card sent one; fall back to the newest
    build_again = bool((body or {}).get("build_again"))

    return StreamingResponse(
        _turn_sse(orchestrator.approve_stream(answers, plan_edits, conversation, plan_id,
                                              build_again=build_again),
                  "approve_stream"),
        media_type="text/event-stream")


@control_app.get("/api/project/settings")
def get_settings() -> JSONResponse:
    """Per-project Sage settings (e.g. skip_planning — opt out of the first-build plan gate)."""
    return JSONResponse(content=orchestrator.project().record.read_settings())


@control_app.post("/api/project/settings")
async def set_settings(request: Request) -> JSONResponse:
    """Update per-project settings: skip_planning (SPEC P6 opt-out) and phased_build."""
    body = await request.json()
    record = orchestrator.project().record
    settings = record.read_settings()
    if "skip_planning" in body:
        settings["skip_planning"] = bool(body["skip_planning"])
    if "phased_build" in body:
        settings["phased_build"] = bool(body["phased_build"])
    record.write_settings(settings)
    return JSONResponse(content=settings)


@control_app.get("/api/project/kept-rows")
def get_kept_rows() -> JSONResponse:
    """Whether this Project keeps real data rows in its files, and where they would be pushed.

    A Project-level decision, so no artifact and no thread id: the audience is the Project's git
    remote, and asking per Artifact would ask the same question often enough that the answer
    becomes a reflex (ADR-0045)."""
    return JSONResponse(content=orchestrator.kept_rows())


@control_app.post("/api/project/kept-rows")
async def set_kept_rows(request: Request) -> JSONResponse:
    """Record the answer. Answers the whole read back, so the dialog re-renders from the write
    rather than from what it assumed the write did."""
    body = await request.json()
    return JSONResponse(content=orchestrator.set_kept_rows(bool((body or {}).get("on"))))


@control_app.get("/api/project/plan")
def project_plan() -> JSONResponse:
    """The plan this app is being built from, or `{}` when there is none.

    `status` is `awaiting` while `.sage/plan.md` is live and `built` once a build has archived it,
    which is the difference between the pin saying "Plan" and "Working from". The body is the plan's
    own markdown — the same text the transcript's plan card renders — because that is what Sage
    actually writes. The plan document under /api/plans holds the same text with its sections read
    out; this stays the markdown because the pin is about the build, not about the document."""
    return JSONResponse(content=orchestrator.read_plan_pin())


@control_app.post("/api/project/plan/cancel")
def cancel_plan(body: dict | None = None) -> JSONResponse:
    """Discard an un-approved plan. When the user dismisses the plan card without building, the
    plan.md the gate turn wrote is still on disk (only an approve archives it). Left there it reads
    like live intent — the exact stray-plan case archive_plan() exists to prevent — so archive it
    now (non-destructive; git keeps the history). Idempotent: no-op if there's no live plan.

    Archived as cancelled, so the rail's plan pin does not go on to describe the app as built from
    a plan the user just dismissed.

    The optional body names the Conversation whose card pressed it, which is what lets Undo on a
    handoff card still read as undone after a reload (#60). A caller that says nothing archives
    exactly as it always did."""
    return JSONResponse(content=orchestrator.cancel_plan(
        str((body or {}).get("conversation") or ""), str((body or {}).get("planId") or "")))


# ---- Plan documents ----
#
# `/api/project/plan` above answers "what is this app being built from", reading the transient
# plan.md. These answer "what does this plan say", reading the document that outlives it. Both are
# the same plan; only one of them survives the build.


@control_app.get("/api/plans")
def list_plans() -> JSONResponse:
    return JSONResponse(content={"items": orchestrator.list_plan_docs()})


@control_app.post("/api/plans")
def create_plan(body: dict | None = None) -> JSONResponse:
    return JSONResponse(content=orchestrator.create_plan_doc(body or {}))


@control_app.get("/api/plans/{plan_id}")
def get_plan(plan_id: str) -> JSONResponse:
    doc = orchestrator.read_plan_doc(plan_id)
    if doc is None:
        return JSONResponse({"error": "unknown plan"}, status_code=404)
    return JSONResponse(content=doc)


@control_app.patch("/api/plans/{plan_id}")
def patch_plan(plan_id: str, body: dict | None = None) -> JSONResponse:
    """Edit the plan. Sections are rendered back to markdown and kept as a new version, so the file
    stays the source of truth and the draft a reviewer commented on is still there."""
    doc = orchestrator.patch_plan_doc(plan_id, body or {})
    if doc is None:
        return JSONResponse({"error": "unknown plan"}, status_code=404)
    return JSONResponse(content=doc)


@control_app.post("/api/plans/{plan_id}/archive")
def archive_plan(plan_id: str, body: dict | None = None) -> JSONResponse:
    """Put a plan away, or take it back out (#167). Beside the patch rather than part of it: this
    one can be refused, and a PATCH that silently declined one of its fields would be a worse
    answer than a route with somewhere to say why."""
    try:
        doc = orchestrator.archive_plan_doc(plan_id, bool((body or {}).get("archived", True)))
    except PlanArchiveRefused as e:
        # The word the plan page already writes copy from, beside the sentence for anything that
        # shows the error raw. The page reads `reason`; the word and the sentence are composed one
        # place apart on purpose, because only the page knows which of its two buttons is asking.
        #
        # A sentence per reason word, because the two refusals send people to different doors and a
        # sentence that named both would be wrong half the time. "Waiting to be built from" used to
        # cover the busy case too (#167) on the grounds that the guard never read whether a turn
        # was running; it does now, so that case says the true thing instead.
        #
        # The busy sentence names no button on purpose. The guard takes the whole `_turn_lock`, and
        # its holder may be a build (which has a Stop) or a Publish or a sync (which have nothing
        # to press). Naming Stop would re-open the very "names a door that is not open" dead end
        # #167 and #174 exist to close. "Try again in a moment" is true of every holder.
        #
        # The waiting sentence's two acts are on the plan card in the Conversation that proposed it
        # — the only place a Cancel exists — so it says where to go rather than what to do. Two
        # cases where it named a door that was not open are gone rather than reworded: a creator
        # whose Conversation is deleted (#167), and a plan whose card lost its action row when its
        # build failed (#174); both archive instead of refusing.
        #
        # The remaining routes to that shape are closed at the source rather than answered here,
        # which is why this refusal can go on assuming the two buttons it names are on the screen
        # (#178). An answer-only turn and `plan-stale` both used to clear the card's `pending`
        # without a build ever running, and so did a GATED turn that failed — `gateway error`,
        # `stalled`, `wedged` and `empty plan` are none of them gate decisions, and `write_plan`
        # runs only once there is a plan, so `plan.md` went on holding the earlier one. The card now
        # survives all three: a `done` closes it only when the turn it ends could have written.
        if e.reason == "busy":
            return JSONResponse(status_code=409, content={"error": brand_text(
                "{assistantName} is working on something else in this project, so this plan "
                "cannot be put away yet. Try again in a moment."), "reason": e.reason})
        return JSONResponse(status_code=409, content={"error": brand_text(
            "This plan is the one this {builtApp} is waiting to be built from, so it cannot be put "
            "away. Approve it or cancel it in the conversation it came from."), "reason": e.reason})
    if doc is None:
        return JSONResponse({"error": "unknown plan"}, status_code=404)
    return JSONResponse(content=doc)


@control_app.get("/api/plans/{plan_id}/markdown")
def get_plan_markdown(plan_id: str) -> JSONResponse:
    """The raw file behind the document, for Build's Markdown tab."""
    body = orchestrator.read_plan_doc_markdown(plan_id)
    if body is None:
        return JSONResponse({"error": "unknown plan"}, status_code=404)
    return JSONResponse(content=body)


@control_app.post("/api/plans/{plan_id}/review")
def review_plan(plan_id: str, body: dict | None = None) -> JSONResponse:
    """Request a review, comment, resolve a comment, or approve. None of these writes a version —
    a comment on a plan is not a new draft of that plan."""
    doc = orchestrator.review_plan_doc(plan_id, body or {})
    if doc is None:
        return JSONResponse({"error": "unknown plan"}, status_code=404)
    return JSONResponse(content=doc)


@control_app.get("/api/members")
def members() -> JSONResponse:
    """Who is on this Project, and everyone who could be added to it.

    Two callers, deliberately one route. The plan page names a reviewer and resolves a comment's
    author out of `members`; the People modal adds and removes, and needs `directory`, `ownerId` and
    `self` beside them. A second endpoint would answer the same question twice and drift.

    Always 200. A read that failed says so in `error` rather than in a status, because one of the
    two callers must open anyway — see `list_members`, where that choice is made and stated.
    """
    return JSONResponse(content=orchestrator.list_members())


@control_app.post("/api/collaborators")
async def add_collaborators(request: Request) -> JSONResponse:
    """Add people to this Project, in the one role Sage assigns.

    The body names people and nothing else. A project id in it would be an authorization surface —
    whoever reached this route could add people to a Project this Builder is not bound to — so the
    server uses its own, and a `projectId` sent anyway is ignored rather than honoured.

    There is no permission pre-check: ADR-0018 rejected exactly this shape of Sage-side
    authorization list. Domino refuses, and the refusal is what the creator reads.

    200 with per-person outcomes, including when some of them failed. A partial failure is a normal
    answer here, not an error condition — two people added and one refused is two people added.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    # A list, checked rather than assumed: a bare string is iterable, so `{"userIds": "u-ada"}` —
    # the obvious singular-for-plural slip — would otherwise be read one CHARACTER at a time and
    # answer 200 with five refusals naming "u", "-", "a", "d", "a".
    sent = body.get("userIds")
    user_ids = [str(u) for u in sent if str(u)] if isinstance(sent, list) else []
    if not user_ids:
        return JSONResponse(status_code=400, content={"error": "userIds required"})
    try:
        return JSONResponse(content=orchestrator.add_collaborators(user_ids))
    except ResourceUnavailable as e:
        return JSONResponse(status_code=502, content={"error": str(e)})


@control_app.delete("/api/collaborators/{user_id}")
def remove_collaborator(user_id: str) -> JSONResponse:
    """Take one person off this Project, and with it their access to any App published from it."""
    try:
        return JSONResponse(content=orchestrator.remove_collaborator(user_id))
    except ResourceUnavailable as e:
        return JSONResponse(status_code=502, content={"error": str(e)})


@control_app.post("/api/project/build/stop")
async def stop_build(request: Request) -> JSONResponse:
    """Stop the in-flight turn. A Build turn is interrupted, its file changes reverted and its turn
    dropped from history, as if it never happened. A Chat turn is interrupted and keeps what it
    already wrote.

    Stop ends ONE turn and the queue behind it advances (#79): you stopped that answer, not your
    other questions. Dropping a question you have changed your mind about is Cancel's job, below.

    An optional `{kind, conversation}` body names the turn the Stop was aimed at, and it is declined
    when that turn is no longer the one running (#126) — the queue can hand the lock on between the
    press and the POST. No body means "stop whatever is running", which is what this always did and
    what a caller with one turn to mean still sends."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    stopped = orchestrator.stop_build(kind=str(body.get("kind") or ""),
                                      conversation=str(body.get("conversation") or ""),
                                      app=str(body.get("app") or ""))
    return JSONResponse(content={"stopped": stopped})


@control_app.get("/api/project/build/state")
def build_state() -> JSONResponse:
    """What the turn lock is doing right now. Cheap enough to poll: it reads the lock and does not
    attach the project. The UI calls this after its SSE stream drops, to tell "the connection broke
    but the build is still going" from "the turn is over".

    Three fields, not one. `wedged` is a prerequisite for the queue rather than a nicety: a wedged
    lock reads as "not running" here on purpose (#39), which was a fine answer while the next send
    got an immediate refusal naming the restart, and is a spinner nobody can resolve once turns wait
    in line instead (#79). `pending` is how many are waiting."""
    return JSONResponse(content=orchestrator.turn_state())


@control_app.post("/api/project/turn/cancel")
def cancel_turn(body: dict) -> JSONResponse:
    """Drop a turn that is still waiting in line (#79). Not Stop: what is running is untouched.

    The ticket comes from the `pending` event the queued turn's own stream yielded. Nothing to
    cancel is `{"cancelled": false}` and not an error — the turn it names may have started, or
    finished, between the click and this call, and neither is a mistake anybody made."""
    ticket = str((body or {}).get("ticket") or "")
    return JSONResponse(content={"cancelled": orchestrator.cancel_pending_turn(ticket)})


@control_app.post("/api/project/build")
async def build_project(request: Request) -> JSONResponse:
    """Run one agent build with the closed feedback loop (needs gateway access)."""
    body = await request.json()
    prompt = body.get("prompt")
    if not prompt:
        return JSONResponse(status_code=400, content={"error": "prompt required"})
    try:
        # Offload the blocking build (drives OpenCode, sleeps) to a thread so the event loop
        # stays free to serve the /v1 model calls that OpenCode makes DURING the build.
        # Without this the single loop deadlocks: build waits for a turn that can't be served.
        result = await run_in_threadpool(orchestrator.build, prompt, body.get("conversation") or None)
        return JSONResponse(content=result)
    except Exception as e:
        log.exception("build failed")
        return JSONResponse(status_code=502, content={"error": {"message": f"{type(e).__name__}: {e}"}})


@control_app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    import json

    project = orchestrator.project()
    # Read as bytes and parse here, rather than `await request.json()`, so the ledger can record how
    # big this step's request was without re-serialising it. The size is the whole point: a first
    # byte that grew while the payload grew is a conversation getting heavier, and one that grew on
    # a steady payload is the gateway. Seconds cannot tell those apart; this can.
    raw = await request.body()
    body = json.loads(raw)
    # Per-turn telemetry: every inference OpenCode runs this turn passes through here. Count it, and
    # (below) flag whether the model's response carried a tool call. build_stream reads these to explain
    # a no-edit turn. See Project.model_calls.
    project.model_calls += 1
    # The turn's ledger of inferences (see sage.timing). No-op outside a turn, so the shim's own
    # standalone app and a stray request from a warm OpenCode cost nothing.
    call = timing.model_call()
    call.request(len(raw))
    # The live session, so a phased build tags each phase with its OWN session id — that's what makes
    # per-phase spend separable in the gateway dashboard (group by tag:sage-session). Falling back to
    # the project session keeps normal turns tagged exactly as before.
    def _refused(model: str, messages) -> None:
        """The payload a guardrail just refused, kept for exactly as long as the search needs it.

        Held in memory on the Project and nowhere else — never a transcript row, never in this
        route's JSON, never logged. `_withhold_search` takes it and clears the slot in the same
        breath, whether or not it ends up searching. It is the content a policy has refused to move,
        so the only correct lifetime is the shortest one that still answers "which part was it".
        """
        project.last_refused = (model, messages)

    gen = project.shim.handle(body, project=project.id,
                              session=project.active_session_id or project.session_id,
                              on_resolved=call.model, on_refused=_refused)
    # The boundary between our time and the gateway's. `handle` is not a generator — it rewrites the
    # request here and now (phase classification, the read-only tool filter, routing, the signing
    # veto) and only the `route` it returns is lazy, so the HTTP call does not start until `ka.pump`
    # pulls it on the thread below. Marked because `ttfb` is measured from before `handle` and so
    # covers both halves; without this line a slow first byte names no suspect.
    call.prepared()

    # Drain the (blocking) gateway generator on a worker thread so the response side can interleave SSE
    # keepalives during silent gaps. Without this, we'd have to withhold the whole HTTP response until
    # the model's first token — minutes for a gpt-5.4 plan turn — and OpenCode's fetch (undici) aborts
    # the silent request as "TypeError: network error". See sage.shim.keepalive.
    q: queue.Queue = queue.Queue()
    started = time.monotonic()
    threading.Thread(target=ka.pump, args=(gen, q), daemon=True).start()

    # Bounded eager pull: a fast pre-stream failure (auth, bad model) inside the budget -> clean JSON
    # 502, exactly as before. If nothing arrives, the model is just thinking: commit to the stream and
    # keep it warm with keepalives below.
    first = await run_in_threadpool(ka.get, q, ka.FIRST_BYTE_BUDGET_S)
    if ka.is_error(first):
        err = first[1]
        call.done(ok=False, error=str(err))
        if isinstance(err, GatewayUpstreamError):
            log.error("gateway %s: %s", err.status, err.body)
            # A guardrail names the policy and not the payload, and the payload is recorded nowhere,
            # so the refusal alone cannot be acted on: every field Sage exposes read clean against a
            # live `Block PII` refusal on 2026-09-11 because the rule is wider than it sounds (a
            # bare ten-digit run — a seconds-precision timestamp, a row id — is a phone number).
            # Said once, here, while the request that carried it is still in hand. Masked: see
            # sage.shim.refusal_scan.
            if "guardrail" in str(err.body).lower():
                if found := refusal_scan.candidates(body):
                    log.error("guardrail refusal — what in the request matches: %s",
                              "; ".join(found))
                else:
                    log.error("guardrail refusal — nothing in the request matches a known rule; "
                              "the gateway's rules are not ours, re-probe with "
                              "scripts/guardrail-probe.py")
            project.last_gateway_error = {"message": str(err), "upstream_status": err.status}
            return JSONResponse(status_code=502, content={"error": {"message": str(err), "upstream_status": err.status}})
        log.error("shim upstream failure: %s", err)
        project.last_gateway_error = {"message": f"{type(err).__name__}: {err}"}
        return JSONResponse(status_code=502, content={"error": {"message": f"{type(err).__name__}: {err}"}})

    if first is not ka.EMPTY:
        call.first_byte()
    log.info(
        "model call -> streaming (first byte %.1fs%s)",
        time.monotonic() - started, ", pending; keepalive engaged" if first is ka.EMPTY else "",
    )

    def stream():
        # Flag once if this response carries a tool call (streamed as choices[].delta.tool_calls, or
        # finish_reason "tool_calls"). Substring sniff is enough — we only need "did the model try a
        # tool this turn", and it stays harness-agnostic (no SSE parsing).
        flagged = False

        def sniff(chunk: bytes) -> None:
            nonlocal flagged
            call.first_byte()   # no-op after the first; catches the keepalive path's real first chunk
            call.chunk()
            if not flagged and b"tool_calls" in chunk:
                flagged = True
                project.tool_call_responses += 1
            # Which tools, and not only whether there were any. The flag above answers "did this turn
            # try a tool", which is what `build_stream` needs to explain a no-edit turn; this answers
            # "what did this STEP spend its round trip on", which is what a turn of eighteen calls
            # needs before any of them can be cut. Same substring gate as the flag, so a text-only
            # chunk costs one `in` and nothing else.
            if names := ka.tool_names(chunk):
                call.tool(names)
            # What the provider says it read, when it says anything. Most models here say nothing —
            # sonnet sends no usage frame at all — so this is recorded when offered and never
            # waited for. `request_bytes` above is the signal that is always there.
            if (used := ka.usage_tokens(chunk)) is not None:
                call.usage(used[0], used[1])

        # A provider error relayed as a 200 + one `data: {"error": …}` frame. Nothing raised, so
        # without this it forwards as-is and OpenCode dies on an unparseable event with no payload.
        # Checked on EVERY chunk: it used to be checked only on the eagerly-pulled `first`, so the
        # frame was recognised only when the provider failed inside FIRST_BYTE_BUDGET_S. A model that
        # thought for longer committed the stream first, and its error frame then went out raw —
        # which is every "Invalid sage-gateway/openai-compatible-chat stream event" in the wild.
        stopped = False

        def relay(chunk: bytes):
            nonlocal stopped
            upstream_msg = ka.upstream_error(chunk)
            if upstream_msg:
                log.error("gateway returned an error frame inside a 200 stream: %s", upstream_msg)
                if ka.guardrail_frame(chunk):
                    # A TRIPWIRE. Nothing branches on it. Measured: `Block PII` refuses the
                    # REQUEST with a 400 whether or not the turn streams, so it cannot arrive
                    # here — the rows are in `ka.guardrail_frame`. An output-side guardrail added
                    # by the administrator later would have nowhere else to go, and would be
                    # silent: the capture that feeds `refusal_scan` and the withhold search hangs
                    # off a RAISED error, and nothing raises here, so `project.last_refused` would
                    # stay None and `_withhold_search` would return without a word.
                    log.error("guardrail refused inside a 200 stream — the payload was NOT captured, "
                              "so no withhold search can run for it")
                project.last_gateway_error = {"message": upstream_msg}
                stopped = True
                yield from ka.error_sse(f"\n\n⚠️ The model gateway rejected this request: {upstream_msg}")
                return
            sniff(chunk)
            yield chunk

        if first is ka.DONE:
            call.done()
            return
        if first is not ka.EMPTY:
            yield from relay(first)  # the first real chunk the eager pull already consumed
            if stopped:
                return
        while True:
            item = ka.get(q, ka.KEEPALIVE_INTERVAL_S)
            if item is ka.EMPTY:
                yield ka.KEEPALIVE  # SSE comment: ignored by the parser, resets the client's read timer
                continue
            if item is ka.DONE:
                call.done()
                return
            if ka.is_error(item):
                e = item[1]
                log.warning(
                    "gateway stream broke mid-response after %.1fs (%s): %s",
                    time.monotonic() - started, type(e).__name__, e,
                )
                project.last_gateway_error = {"message": f"{type(e).__name__}: {e}"}
                call.done(ok=False, error=f"{type(e).__name__}: {e}")
                yield from ka.error_sse(
                    f"\n\n⚠️ The model gateway closed the stream mid-response ({type(e).__name__}). "
                    "This is usually an upstream idle or duration limit — please retry."
                )
                return
            yield from relay(item)
            if stopped:
                return

    return StreamingResponse(stream(), media_type="text/event-stream")


# Preview proxy for the bound project, mounted under /preview on the one control port. Vite bakes
# base=<prefix>/preview/, so the proxy re-adds that when forwarding upstream (see make_preview_app).
# Chat may have attached an empty volume; seeding + Vite start happen here, not on Thread open.
def _preview_upstream() -> str:
    project = orchestrator._ensure_seeded()
    orchestrator._ensure_preview_running(project)
    return project.supervisor.upstream()


# The previewed app's own named queries (#24). Answered by `serve.py` on loopback rather than 404'd
# by Vite, which serves the app but has never served its data. Attaching is deliberately NOT forced
# here — `_preview_upstream` above is what seeds the project, and a query arriving before the page
# that would ask it means something is wrong rather than something to boot a project for.
def _preview_queries():
    return orchestrator._project.queries if orchestrator._project is not None else None


# The previewed app's own model calls (#7). A published app calls the gateway straight from the
# viewer's browser because both sit on `apps.<domino-host>` — same origin. The preview is served from
# here instead, so that call is cross-origin and the browser blocks it; the proxy makes it instead.
# Returns a FRESH token per call: sidecar tokens are short-lived, so one resolved at boot would work
# for the first few minutes of a session and then quietly stop.
def _preview_llm() -> tuple[str, str] | None:
    """`(gateway /v1 base, bearer)`, or None when there is no Domino gateway to forward to.

    Gated on GATEWAY_MODE exactly as `_browser_gateway_base` is, and for the same reason: in `openai`
    mode each model routes to its own vendor behind a key, and in `fake` mode there is no gateway at
    all. In both, the app was never given a gateway to call, so there is nothing to forward.
    """
    base = os.environ.get("GATEWAY_BASE_URL", "").strip()
    if GATEWAY_MODE != "domino" or not base:
        return None
    key = os.environ.get("GATEWAY_API_KEY", "")
    provider = static_token(key) if key else sidecar_token(
        os.environ.get("GATEWAY_TOKEN_URL", DEFAULT_SIDECAR_URL))
    return base.rstrip("/").removesuffix("/v1").rstrip("/") + "/v1", provider()


def _preview_approve_model(model: str) -> str | None:
    """Refuse the previewed app's model call when a declared Dataset is in scope (ADR-0043).

    The app Sage builds is gated here and at publish, and nowhere else: once published it calls the
    gateway from the viewer's browser with no Sage hop in the path. So this is the only place the
    app's OWN call can be stopped, and it is stopped while the creator is watching.

    None means allow, which is also what a Project with no declared Dataset and any deployment that
    never opted in answer — neither reads anything to say so.
    """
    project = orchestrator._project
    if project is None:
        return None
    try:
        # No conversation: the previewed app's model call carries the app's CURRENT Bindings and no
        # transcript, so the sticky lock a conversation may be carrying is not about this (ADR-0043).
        approved, refusal = orchestrator._sensitivity_for_turn(project, None)
    except Exception:
        # A preview call is not a turn, and Sage failing to read its own gate must not take the
        # preview down. The publish guard still refuses, so nothing ships on this path.
        log.exception("preview llm: couldn't resolve the sensitivity gate")
        return None
    if refusal:
        return refusal
    if approved is not None and model not in approved.names:
        return declared_turn_refusal_for_model(model, approved.names)
    return None


control_app.mount("/preview", make_preview_app(_preview_upstream, BASE_PREFIX, _preview_queries,
                                               _preview_llm, _preview_approve_model))
class _RevalidatingStatic(StaticFiles):
    """The shell's own assets carry no version in their filenames, and StaticFiles sends no
    Cache-Control at all. A browser then falls back to heuristic freshness — roughly a tenth of
    the time since Last-Modified — so a tab can keep running the JS from before a deploy without
    ever asking. `no-cache` means "ask every time", not "do not store": the ETag StaticFiles
    already sends turns that question into a 304 whenever the bytes are unchanged.

    Deliberately not `immutable`. That belongs to /fonts, where replacing the bytes means a new
    filename in the page that asks for them (see font()). Nothing here is renamed on an upgrade,
    /vendor included: a year-long immutable React would survive the upgrade that replaced it.
    """

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        response.headers.setdefault("Cache-Control", "no-cache")
        return response


control_app.mount("/css", _RevalidatingStatic(directory=_WB / "css"), name="wb-css")
control_app.mount("/js", _RevalidatingStatic(directory=_WB / "js"), name="wb-js")
control_app.mount("/img", _RevalidatingStatic(directory=_WB / "img"), name="wb-img")
control_app.mount("/vendor", _RevalidatingStatic(directory=_WB / "vendor"), name="wb-vendor")

# The partner's own logo and favicon (#117). Held on the module so a test can point it somewhere
# it is allowed to write; the boundary it enforces lives in brand.py, next to the pack that names
# the files. BRAND_DIR is `/opt/sage/brand` and never `/opt/sage`, which holds opencode.json.
from .brand import BRAND_DIR as _BRAND_DIR
from .brand import BrandImages as _BrandImages

_brand_images = _BrandImages(directory=_BRAND_DIR, check_dir=False)
control_app.mount("/brand", _brand_images, name="brand-img")


def _install_opencode_config(source_dir: Path, control_port: int) -> None:
    """Make OpenCode load Sage's provider/agents/model, in the pack's own words.

    Measured on opencode-ai@1.18.4: project config outranks both custom (`OPENCODE_CONFIG`) and
    global. But project config is resolved off the git root of the SESSION directory, not the
    server's cwd, and every Sage session runs under the workspace volume — so the winning slot is
    never ours to fill and `/opt/sage` was never in the running. The earlier reading of that
    experiment (#199) used the server's own cwd as the session directory, the one case where the
    two coincide, and it cost two misdiagnoses of a leak that turned out to be an AGENTS.md (#202).

    So what does the work here is the GLOBAL copy: it is the last-loaded of the slots we can fill,
    OpenCode demonstrably reads it, and `driver/server.py` also points OPENCODE_CONFIG at it. The
    voiced blob still goes to `_opencode_project_dir()` as well — that is where the server runs, it
    is made a git root here, and it costs nothing to have the file beside it be a voiced one rather
    than the template. Either way nothing falls back to OpenCode's free tier (HTTP 429
    FreeUsageLimitError). The sage-gateway baseURL and the Live read MCP url are aligned to the
    port this process serves first, since both dial it.

    The checked-in source is READ and never written: it is the unvoiced template, and rewriting it
    left a tracked diff in the image that `app.sh` reads as "someone hand-patched this" and skips
    self-update over. Logs to app logs."""
    import json
    import re
    import subprocess
    from copy import deepcopy

    from .brand import apply_agent_voice

    src = source_dir / "opencode.json"
    try:
        cfg = json.loads(src.read_text())
    except Exception as e:  # missing/unreadable — flag, don't crash the boot
        log.error("[wiring] cannot read %s: %s — OpenCode will stay on its free tier", src, e)
        return

    def _on_this_port(url: str) -> str:
        return re.sub(r"(://[^/:]+):\d+", rf"\g<1>:{control_port}", url)

    opts = ((cfg.get("provider") or {}).get("sage-gateway") or {}).get("options") or {}
    base = opts.get("baseURL", "")
    if base:
        opts["baseURL"] = _on_this_port(base)
    # The Live read tools are served by THIS process too (ADR-0041), so their port moves with it.
    # This is the rewrite above missing its twin: on Domino the shim serves :8888 while the
    # checked-in URL says :8080, and OpenCode drops an unreachable MCP server SILENTLY — the tools
    # are absent from the offered list with nothing logged, and the agent falls back on telling the
    # person it cannot see their data, which is the transcript ADR-0041 exists to end.
    for server in (cfg.get("mcp") or {}).values():
        url = (server or {}).get("url", "")
        if url:
            server["url"] = _on_this_port(url)
        # A `local` server is a command OpenCode SPAWNS, and it spawns it from the session's
        # directory, not from here — so a path relative to the checkout resolves against the wrong
        # place and the server dies silently, which reads exactly like the drop this file already
        # warns about. Anchored to `source_dir` because that is the checkout the config came out of.
        argv = (server or {}).get("command")
        if isinstance(argv, list):
            server["command"] = [
                str((source_dir / a).resolve()) if isinstance(a, str) and "/" in a
                and not a.startswith("/") else a
                for a in argv
            ]
    voiced = json.dumps(apply_agent_voice(deepcopy(cfg)), indent=2) + "\n"
    project_dir = _opencode_project_dir()
    try:
        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / "opencode.json").write_text(voiced)
        # A dir with no git root above it is no project at all — OpenCode reports project "global"
        # and discovers no project config. `git init` is what makes the copy beside it outrank the
        # rest. Best effort: if it fails the global copy below is voiced too, so the words hold.
        if not (project_dir / ".git").exists():
            subprocess.run(["git", "init", "-q"], cwd=project_dir, check=False,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        log.warning("[wiring] installed Sage config into %s (the OpenCode server's cwd)", project_dir)
    except OSError as e:
        log.error("[wiring] could NOT install %s (%s) — Chat may speak unresolved pack tokens", project_dir, e)
    global_dir = Path(os.path.expanduser("~/.config/opencode"))
    try:
        global_dir.mkdir(parents=True, exist_ok=True)
        for name in ("opencode.json", "opencode.jsonc"):
            (global_dir / name).write_text(voiced)
        log.warning("[wiring] installed Sage config into %s (model=%s, sage-gateway baseURL -> :%d)",
                    global_dir, cfg.get("model"), control_port)
    except OSError as e:
        log.error("[wiring] could NOT install global opencode config (%s) — OpenCode will use its free tier", e)
    _install_opencode_tools(source_dir, global_dir)


def _install_opencode_tools(source_dir: Path, global_dir: Path) -> None:
    """Put the Live read CUSTOM tools where OpenCode reads them (ADR-0041).

    `~/.config/opencode/tools/` is the global slot, and global is what Sage can fill: a Chat session
    runs under the workspace volume, so the project slot is never ours — the same reasoning that
    makes the global config the one that works. Measured on the pinned 1.18.4: a tool here reaches
    the model from ANY session directory, with no `.opencode/` in the workspace.

    Copied rather than symlinked, so a checkout that moves or a container that rebuilds cannot leave
    a dangling link that fails the way MCP already fails — silently, with the tools simply absent.

    THE FILENAME CARRIES THE TOOL NAMES. OpenCode names a multi-export tool `<file>_<export>`, so
    `live_read.ts` is what makes `live_read_table` and `live_read_files`. Copy it under any other
    name and the prompts teach tools that do not exist.

    Best effort and loud on failure: without this the agent falls back to Python, which answers but
    puts every row in the model's context — the thing ADR-0041 exists to avoid.
    """
    src_dir = source_dir / "backend" / "sage" / "liveread" / "tools"
    try:
        names = sorted(p.name for p in src_dir.glob("*.ts"))
    except OSError as e:
        log.error("[wiring] could NOT read %s (%s) — Live read tools will be absent", src_dir, e)
        return
    if not names:
        log.error("[wiring] no Live read tools at %s — they will be absent from every turn", src_dir)
        return
    dest = global_dir / "tools"
    try:
        dest.mkdir(parents=True, exist_ok=True)
        for name in names:
            (dest / name).write_text((src_dir / name).read_text())
        log.warning("[wiring] installed Live read tools into %s (%s)", dest, ", ".join(names))
    except OSError as e:
        log.error("[wiring] could NOT install Live read tools into %s (%s) — Chat will fall back "
                  "to Python, which puts rows in the model's context", dest, e)


def _release_boot_page(host: str, port: int) -> None:
    """Take the control port back from the placeholder `app.sh` left holding it.

    `sage/orchestrator/boot_page.py` answers that port from the first second of the container so a
    viewer who arrives mid-boot reads "your workspace is starting" instead of the proxy's 502. It
    has no signal handler: SIGTERM's default disposition ends it and the kernel frees the port. We
    then wait for the port rather than assume it, so uvicorn cannot lose a race with a process we
    are the one killing. A few tens of milliseconds, against the seconds the placeholder covered.

    Every failure here is a no-op on purpose. No placeholder, a pid that is already gone, a pid we
    may not signal, a port that never frees — each leaves the boot exactly as it was before this
    existed, and uvicorn's own bind error is a better report than anything we could invent.
    """
    import signal
    import socket

    pid = os.environ.get("SAGE_BOOT_PAGE_PID")
    if not pid:
        return
    try:
        os.kill(int(pid), signal.SIGTERM)
    except (ValueError, OSError) as e:
        log.info("boot page: nothing to release (pid %r, %s)", pid, type(e).__name__)
        return
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        with socket.socket() as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind((host, port))
            except OSError:
                time.sleep(0.05)
                continue
        log.info("boot page: released port %s", port)
        return
    log.warning("boot page: port %s did not free after SIGTERM to pid %s", port, pid)


def run() -> None:
    """Run the single control app (:8080, preview mounted at /preview) in one process."""
    import asyncio
    import contextlib
    import signal

    import uvicorn

    control_port = int(os.environ.get("SAGE_CONTROL_PORT", "8080"))
    _install_opencode_config(Path(os.environ.get("SAGE_OPENCODE_CWD", _REPO)), control_port)
    # Loopback locally; Domino's pluggable-tool proxy reaches the tool port from outside the
    # process, so set SAGE_CONTROL_HOST=0.0.0.0 there (matches the Phase-0 spike).
    control_host = os.environ.get("SAGE_CONTROL_HOST", "127.0.0.1")
    # Last thing before the bind, so the placeholder covers the import and the opencode config
    # write above it, and the window with nothing on the port stays as short as it can be.
    _release_boot_page(control_host, control_port)
    server = uvicorn.Server(uvicorn.Config(control_app, host=control_host, port=control_port, log_level="info"))

    # Install our own signal handler (instead of uvicorn's) so a SIGTERM reliably reaches
    # orchestrator.shutdown() to tear down Vite/OpenCode child processes.
    server.capture_signals = contextlib.nullcontext

    async def _serve() -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, lambda: setattr(server, "should_exit", True))
        try:
            await server.serve()
        finally:
            orchestrator.shutdown()

    asyncio.run(_serve())


if __name__ == "__main__":
    run()
