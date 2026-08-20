"""The model access tokens a creator has pasted, and the check that they work (#9).

Two jobs, because they are the two halves of "ask once":

`verify_credential` calls the model before Sage keeps the token, so a bad paste is refused while the
creator is still looking at the form. Reading the result needs the one thing that cost this issue an
hour: **401 is refused at the door, 400 is the opposite result.** A 400 means the credential was
accepted and the model rejected the body — which is expected here, since the probe body is empty on
purpose. A 400 is a pass.

`CredentialStore` remembers what passed, keyed by Model API id, so the second app that uses the same
model never asks again. Kept out of git: the token a bound app calls with is committed into that
app's own source by `pinned_model_api` — that is the creator's decision, made once, for one model —
whereas this file accumulates every credential ever pasted, including for models since unbound, and
none of that belongs in the project's history.
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path

# What the store is called, and the ignore rule that keeps it out of the app's repo. The path is
# relative to the workspace root, and the rule is written in the same form `.gitignore` already uses.
STORE_PATH = Path(".sage") / "model-api-credentials.json"
IGNORE_RULE = ".sage/model-api-credentials.json"
# Empty on purpose. A model that takes no arguments answers 200 and one that takes some answers 400
# naming them — both prove the token, and the second is a better error than any guessed body would
# produce. See the module docstring in `model_api_snippet` for why the snippet's own body is dropped.
PROBE_BODY = {"data": {}}
# Long enough for a scaled-to-zero Model API to wake up, short enough that a creator staring at a
# spinner gets an answer. A Model API that needs longer than this to answer its first request will
# fail the same way for a viewer, so reporting it here is not a false alarm.
VERIFY_TIMEOUT = 30.0

_LOCK = threading.Lock()


class CredentialRequired(Exception):
    """Raised when a Model API is bound before its access token has been pasted.

    Deliberately NOT a LookupError, though something is indeed missing: bind already raises that for
    a Model API this project does not offer, and the two need opposite advice — one says the model is
    out of reach, this one says the model is fine and Sage needs its token. Sharing a base class here
    would put the wrong sentence in front of the creator the first time an except clause was widened.
    """


@dataclass(frozen=True)
class VerifyResult:
    """Whether the token opens the model, and what to tell the creator when it does not."""

    ok: bool
    status: int | None
    message: str | None = None
    #: The model's own words, when the model is what refused. Shown raw and unwrapped: this is user
    #: code output, not a Sage error, and rewording somebody's traceback helps nobody debug it.
    detail: str | None = None


def _refusal(status: int) -> str:
    """One sentence per way a Model API says no, written for whoever pasted the snippet."""
    if status in (401, 403):
        return (
            "Domino refused that access token. Tokens are regenerated from the Model API's Settings "
            "page in Domino, and an old snippet carries the old token — copy the sample request "
            "again and paste the current one."
        )
    if status == 404:
        return (
            "There is no Model API at that URL. Check the snippet came from this model's Overview "
            "page, and that the model still has a deployed version."
        )
    if status == 503:
        return (
            "That Model API is not running. Start it in Domino, wait for its status to reach "
            "Running, then try again."
        )
    return f"The Model API did not answer (error {status}). Try again in a moment."


def verify_credential(url: str, token: str, *, timeout: float = VERIFY_TIMEOUT) -> VerifyResult:
    """Call the model once, and report whether the token opened it.

    Sent the way a Model API is reached and the only way it can be: `Basic base64(token:token)`.
    Neither `Bearer` nor `X-Domino-Api-Key` works here — model invocation is a separate auth domain
    from Domino's REST API, verified against all four shapes and every credential Sage can hold.
    """
    import httpx  # local, like the resource provider's: tests never need it on the path

    try:
        r = httpx.post(
            url,
            json=PROBE_BODY,
            auth=(token, token),
            timeout=timeout,
            headers={"Content-Type": "application/json"},
        )
    except Exception as e:  # httpx raises a family; every member means the same thing here
        return VerifyResult(
            ok=False,
            status=None,
            message=(
                f"Sage could not reach that Model API ({type(e).__name__}). Check the URL, and that "
                "Domino is reachable from here, then try again."
            ),
        )
    # 400 is a PASS. The token authenticated; the empty probe body is what the model turned down.
    if r.status_code == 200 or r.status_code == 400:
        return VerifyResult(ok=True, status=r.status_code, detail=_body_text(r))
    return VerifyResult(ok=False, status=r.status_code, message=_refusal(r.status_code), detail=_body_text(r))


def _body_text(response: object) -> str | None:
    """The model's own message, trimmed to something a form can show.

    Best effort: a Model API's error body is whatever the deployed function raised, so it may be
    JSON, a plain string, or an HTML page from something in front of the model.
    """
    try:
        raw = (getattr(response, "text", "") or "").strip()
    except Exception:
        return None
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            for key in ("errors", "error", "message", "detail"):
                if parsed.get(key):
                    value = parsed[key]
                    raw = "; ".join(str(v) for v in value) if isinstance(value, list) else str(value)
                    break
    except (json.JSONDecodeError, ValueError):
        pass
    return raw[:600]


@dataclass(frozen=True)
class Credential:
    url: str
    token: str

    def to_dict(self) -> dict:
        return {"url": self.url, "token": self.token}


class CredentialStore:
    """Model access tokens by Model API id, in a gitignored file under the workspace.

    In the workspace rather than somewhere central because Sage's state all lives there — one
    container hosts one project (D9), so the project volume IS the durable place. `ensure_ignored`
    runs before every write, never after: a file that lands before its ignore rule can be staged by
    anything watching the tree in between.
    """

    def __init__(self, workspace_path: Path) -> None:
        self._root = Path(workspace_path)

    @property
    def path(self) -> Path:
        return self._root / STORE_PATH

    def _read_raw(self) -> dict:
        try:
            data = json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def get(self, model_api_id: str) -> Credential | None:
        entry = self._read_raw().get(model_api_id)
        if not isinstance(entry, dict):
            return None
        url, token = entry.get("url"), entry.get("token")
        if not isinstance(url, str) or not isinstance(token, str) or not url or not token:
            return None
        return Credential(url, token)

    def ids(self) -> set[str]:
        """Which Model APIs Sage already holds a credential for — the set the rail reads to decide
        whether clicking Use has to ask for anything."""
        return {k for k in self._read_raw() if self.get(k) is not None}

    def put(self, model_api_id: str, credential: Credential) -> None:
        self.ensure_ignored()
        with _LOCK:
            data = self._read_raw()
            data[model_api_id] = credential.to_dict()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps(data, indent=2))
            tmp.replace(self.path)
        self.path.chmod(0o600)

    def ensure_ignored(self) -> bool:
        """Put the store's ignore rule in the workspace's `.gitignore`. True if it was added.

        The template ships the rule, so projects seeded after #9 already have it. This is for the
        ones seeded before — the same reason `ensure_llm_helper` exists — and it has to hold for
        them, because the alternative is a credential file appearing in a creator's `git status`.
        """
        gitignore = self._root / ".gitignore"
        try:
            existing = gitignore.read_text() if gitignore.is_file() else ""
        except OSError:
            return False
        if IGNORE_RULE in existing.split():
            return False
        prefix = "" if not existing or existing.endswith("\n") else "\n"
        gitignore.write_text(
            f"{existing}{prefix}\n# Model access tokens a creator pasted (#9). Never committed.\n{IGNORE_RULE}\n"
        )
        return True
