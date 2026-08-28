"""Workspace module (SPEC C1/C9, DESIGN Seam 3 handoff).

The single working directory (the Domino project's mounted volume) seeded from the warm
React+Vite template. Deep module, narrow interface: callers ensure the workspace and read/write
the plan artifact; how the template is materialized (seed source + symlink the warm
node_modules) is hidden.

Two record surfaces sit over that directory, because a Project holds many Built Apps
(ADR-0008). `Workspace` is one Built App — its code, Bindings, plan copy, architecture note and
build log — and it lives in `apps/<appId>/`, seeded from the template. `ProjectRecord` is the
Project — its plan documents, settings and OpenCode sessions, alongside the Threads
`ThreadStore` already keeps — and it stays at the volume root.

The app directory is the build agent's working directory, so from the agent's side nothing has
moved: `AGENTS.md`, `.sage/plan.md`, `.sage/bindings.json` and the rest keep the paths they
always had, one level down. `appId` comes from `new_id("app")` and never changes, because
Domino fixes a published App's `entryPoint` at creation and a renamed directory would strand
the deployment.

node_modules is symlinked from the template rather than copied so each workspace is warm
(deps already installed) without paying a multi-hundred-MB copy per project.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import threading
import time
from collections import deque
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

from . import plan_doc
from .threads import CHAT_WORK, new_id

log = logging.getLogger(__name__)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def ensure_ignore_line(path: Path, line: str) -> None:
    """Append one rule to an ignore file, once. Shared because both surfaces have one: the app
    carries the template's .gitignore, the Project keeps its own at the volume root, and the
    orchestrator adds lines to both plus the `.ignore` ripgrep reads."""
    existing = path.read_text() if path.exists() else ""
    if line in existing.split():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(existing + ("" if existing.endswith("\n") or not existing else "\n") + line + "\n")

# Source dirs never copied into a workspace (heavy / regenerated / linked separately). __pycache__
# appears in a dev checkout of the template as soon as anything imports serve.py, and a workspace
# that receives it commits it to the user's app repo.
_IGNORE = shutil.ignore_patterns("node_modules", "dist", ".git", ".DS_Store", "__pycache__")
# Top-level template entries skipped when seeding (linked or repo-owned, not template content).
_SEED_SKIP = {"node_modules", "dist", ".git", ".DS_Store", "__pycache__"}
# Reset (#36) keeps what the user set up and replaces what a build produced. Top-level entries that
# are not the app: Sage's own record, the project's real git history, and the warm dependency link.
_RESET_KEEP = {".git", ".sage", "node_modules"}
# Kept by path rather than by top-level name: `public/` is template content, but `public/data/` holds
# the files the user attached, and taking those would put them back in the builder attaching again.
_RESET_KEEP_NESTED = (Path("public") / "data",)
# Every Built App lives one level down, in a directory named for its id (ADR-0008).
_APPS = "apps"
# What the PROJECT keeps out of git, at the volume root. The app carries its own .gitignore from
# the template; these are the three trees that stayed behind when the app moved down a level, and
# nothing seeds a file at the root to carry their rules. Chat's scratch and its OpenCode workdir
# are builder-local, and a half-written Thread record is a file `git add -A` would otherwise
# commit (ThreadStore._write_json renames the real one into place, so a .tmp is never read).
_PROJECT_IGNORE = (".sage/scratch/", f"{CHAT_WORK.as_posix()}/", ".sage/threads/*/.*.tmp")
# Sage metadata that belongs to the APP, so it goes when the app does. queries.json is the app's SQL;
# plan.md and architecture.md both describe the code being removed, and AGENTS.md tells the agent
# plan.md is the live plan — a stale one would aim the next turn at an app that is gone.
_RESET_CLEAR = (Path(".sage") / "queries.json",
                Path(".sage") / "plan.md",
                Path(".sage") / "architecture.md")
# Proof that a node_modules is usable: the binary both `npm run dev` and `npm run build` invoke.
_DEPS_SENTINEL = Path(".bin") / "vite"
# What Domino runs to serve a published App: the entry script, and the Python server it execs
# (ADR-0002). Both Sage-owned — see refresh_entry_script. serve.py comes FIRST: a refreshed app.sh
# without it is an app that crash-loops, whereas a stale app.sh with a spare serve.py still serves.
_DEPLOY_FILES = (
    "serve.py",
    "scripts/rehydrate-data.mjs",
    "scripts/rehydrate_data.py",
    "app.sh",
)
# One binding writer at a time. Workspace is a frozen value object that callers re-create freely, so
# the lock cannot live on the instance; a process-wide one is enough because a Sage process serves a
# single project (D9) and every binding write goes through update_bindings.
_BINDINGS_LOCK = threading.Lock()
# Same shape for the project's working set of Domino Resources (Browse → Add). Not Bindings: those
# are the Built App's recorded uses. This list is what the Resource Browser rail shows.
_PROJECT_RESOURCES_LOCK = threading.Lock()
# The helper a Built App calls its model through (#7). Sage-owned like the deploy files above,
# and committed to the app's repo for the same reason: a published app has no Sage around it.
_LLM_HELPER = str(Path("src") / "sageLlm.ts")
# The same, for the Model API a Built App calls (#9). Separate file rather than more of sageLlm.ts:
# the two call different hosts with different credentials, and only this one carries a secret.
_MODEL_API_HELPER = str(Path("src") / "sageModelApi.ts")
# And for the Data Source a Built App queries (#15). Separate again, and for a sharper reason than
# the other two: this one calls the app's OWN server, which is the only Resource path that does.
_QUERY_HELPER = str(Path("src") / "sageQuery.ts")


# settings.json is read and written by two surfaces. ProjectRecord owns the file — skip_planning,
# phased_build, the display name — and Workspace keeps its own latches in it until a Built App has
# a directory of its own to keep them in (ADR-0008). Neither reads the other's keys, and both are
# forgiving of a missing or corrupt file: a settings read that raised would turn every caller's
# question into an error page over a file that is optional by design.


def _read_settings_file(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_settings_file(path: Path, settings: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2))


@dataclass(frozen=True)
class ProjectRecord:
    """What the Project owns, as against what one Built App owns (ADR-0008).

    A Project holds Threads, plan documents, its own settings and the OpenCode session each
    conversation runs in. A Built App holds its code, its Bindings, the plan copy its builder
    consumes and its build log; `Workspace` is that surface, and this is the one beside it. This
    one names the volume root; the app names `apps/<appId>/` inside it.

    A session is here rather than on the app because a session belongs to a conversation: it is
    filed under `.sage/threads/<id>/` next to that Thread's chat session, so a deleted Thread
    takes both halves with it.

    Threads themselves are absent because they already have a surface of their own: `ThreadStore`.
    """

    project_id: str
    path: Path

    # ---- The plan document (the durable artifact) ----
    #
    # Kept apart from plan.md on purpose. `.sage/plan.md` is the one-shot handoff archive_plan()
    # moves aside the moment a build consumes it; the document is what people read, edit and review,
    # and it has to survive that build. Same split as architecture.md, one level up: the document is
    # the source, plan.md is the copy handed to the builder.
    #
    # Under .sage/plan-docs/, NOT .sage/plans/ — that directory is the plan.md archive, and
    # archive_plan()/read_archived_plan() glob "[0-9]*.md" in it. A doc directory sitting there
    # would be one rename away from colliding with an archived plan.

    @property
    def plan_docs_dir(self) -> Path:
        return self.path / ".sage" / "plan-docs"

    def _plan_doc_dir(self, plan_id: str) -> Path:
        # Ids are allocated below and never come from the caller's body, but they do arrive off the
        # wire in a URL, so refuse anything that could climb out of plan-docs/.
        if not re.fullmatch(r"[0-9a-zA-Z_-]{1,64}", plan_id or ""):
            raise ValueError(f"bad plan id: {plan_id!r}")
        return self.plan_docs_dir / plan_id

    def _plan_doc_versions(self, plan_id: str) -> list[Path]:
        d = self._plan_doc_dir(plan_id)
        if not d.is_dir():
            return []
        return sorted((p for p in d.glob("v[0-9]*.md") if p.is_file()),
                      key=lambda p: int(p.stem[1:]))

    def _read_plan_doc_meta(self, plan_id: str) -> dict | None:
        meta_path = self._plan_doc_dir(plan_id) / "meta.json"
        if not meta_path.is_file():
            return None
        try:
            data = json.loads(meta_path.read_text())
        except (json.JSONDecodeError, OSError):
            return None
        return data if isinstance(data, dict) else None

    def _write_plan_doc_meta(self, plan_id: str, meta: dict) -> None:
        d = self._plan_doc_dir(plan_id)
        d.mkdir(parents=True, exist_ok=True)
        (d / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")

    def create_plan_doc(self, markdown: str, *, title: str, author: str = "",
                        origin_thread_id: str = "", status: str = "draft",
                        app_id: str = "") -> dict:
        """Store a plan's markdown as version 1 of a new document, and return the whole document."""
        self.plan_docs_dir.mkdir(parents=True, exist_ok=True)
        n = len([p for p in self.plan_docs_dir.iterdir() if p.is_dir()]) + 1
        plan_id = f"{n:03d}"
        while self._plan_doc_dir(plan_id).exists():   # never reuse a document's id
            n += 1
            plan_id = f"{n:03d}"

        now = plan_doc.now()
        self._plan_doc_dir(plan_id).mkdir(parents=True, exist_ok=True)
        (self._plan_doc_dir(plan_id) / "v001.md").write_text(markdown)
        self._write_plan_doc_meta(plan_id, {
            "id": plan_id, "title": title, "version": 1, "status": status, "author": author,
            # `appId` is empty for a plan drafted in Chat: the app it will build does not exist
            # yet, and the reference is stamped on when the handoff confirms. A plan the BUILD gate
            # writes already stands in an app, and names it here.
            "createdAt": now, "updatedAt": now, "originThreadId": origin_thread_id, "appId": app_id,
            "reviewers": [], "approvals": [], "comments": [],
        })
        return self.read_plan_doc(plan_id)

    def read_plan_doc(self, plan_id: str) -> dict | None:
        """The document: its metadata, plus the sections parsed out of its newest version."""
        try:
            meta = self._read_plan_doc_meta(plan_id)
        except ValueError:
            return None
        if meta is None:
            return None
        versions = self._plan_doc_versions(plan_id)
        markdown = versions[-1].read_text() if versions else ""
        parsed = plan_doc.parse_sections(markdown)
        return {**meta, "summary": parsed["summary"], "sections": parsed["sections"],
                "markdown": markdown}

    def list_plan_docs(self) -> list[dict]:
        """Newest first, which is the order the panel lists them in, and which the plan pin trusts
        to name the document plan.md belongs to.

        Sorted on the id as well as the timestamp. Two documents drafted in the same second tie on
        createdAt, and a tie there would leave the order to whatever the directory listing happened
        to give — the ids are allocated in order, so they settle it.
        """
        if not self.plan_docs_dir.is_dir():
            return []
        docs = [self.read_plan_doc(p.name) for p in self.plan_docs_dir.iterdir() if p.is_dir()]
        return sorted((d for d in docs if d),
                      key=lambda d: (d.get("createdAt", ""), d.get("id", "")), reverse=True)

    def write_plan_doc_version(self, plan_id: str, markdown: str, **meta_updates) -> dict | None:
        """Add a version rather than overwrite one. Editing a section people are reviewing must not
        rewrite the thing they commented on, and git alone can't show that inside one build."""
        try:
            meta = self._read_plan_doc_meta(plan_id)
        except ValueError:
            return None
        if meta is None:
            return None
        versions = self._plan_doc_versions(plan_id)
        n = (int(versions[-1].stem[1:]) if versions else 0) + 1
        (self._plan_doc_dir(plan_id) / f"v{n:03d}.md").write_text(markdown)
        meta.update(meta_updates)
        meta["version"] = n
        meta["updatedAt"] = plan_doc.now()
        self._write_plan_doc_meta(plan_id, meta)
        return self.read_plan_doc(plan_id)

    def patch_plan_doc_meta(self, plan_id: str, **meta_updates) -> dict | None:
        """Change the document's state (status, reviewers, approvals, comments) without adding a
        version — a comment is not a new draft of the plan."""
        try:
            meta = self._read_plan_doc_meta(plan_id)
        except ValueError:
            return None
        if meta is None:
            return None
        meta.update(meta_updates)
        meta["updatedAt"] = plan_doc.now()
        self._write_plan_doc_meta(plan_id, meta)
        return self.read_plan_doc(plan_id)

    def read_plan_doc_markdown(self, plan_id: str) -> dict | None:
        """The raw file behind the document, for Build's Markdown tab."""
        try:
            versions = self._plan_doc_versions(plan_id)
        except ValueError:
            return None
        if not versions:
            return None
        return {"path": str(versions[-1].relative_to(self.path)),
                "content": versions[-1].read_text()}

    @property
    def settings_path(self) -> Path:
        """Per-project Sage settings (e.g. skip_planning to opt out of the first-build plan gate).
        Same committed-.sage pattern as model_overrides.json."""
        return self.path / ".sage" / "settings.json"

    def read_settings(self) -> dict:
        return _read_settings_file(self.settings_path)

    def write_settings(self, settings: dict) -> None:
        _write_settings_file(self.settings_path, settings)

    def is_untitled(self) -> bool:
        return bool(self.read_settings().get("untitled"))

    def mark_untitled(self, untitled: bool = True) -> None:
        settings = self.read_settings()
        if bool(settings.get("untitled")) == untitled:
            return
        settings["untitled"] = untitled
        self.write_settings(settings)

    def display_name(self) -> str:
        """Sage overlay name for the scope chip. Domino's project name is the URL slug and does
        not change; Default and the plan title live here (ADR-0004)."""
        settings = self.read_settings()
        stored = settings.get("displayName")
        if isinstance(stored, str) and stored.strip():
            return stored.strip()
        if settings.get("untitled"):
            return "Default"
        return ""

    def set_display_name(self, name: str) -> None:
        settings = self.read_settings()
        settings["displayName"] = name.strip()
        self.write_settings(settings)

    @property
    def project_resources_path(self) -> Path:
        """Domino Resources the creator added to this project (the Resource Browser working set).

        Browse Domino lists everything this caller can access. Add writes a row here. The rail
        shows only these rows — listing access is not membership.
        """
        return self.path / ".sage" / "project-resources.json"

    def read_project_resources(self) -> list[dict]:
        if not self.project_resources_path.exists():
            return []
        try:
            data = json.loads(self.project_resources_path.read_text())
        except (json.JSONDecodeError, OSError):
            return []
        if isinstance(data, dict):
            items = data.get("items")
            return items if isinstance(items, list) else []
        return data if isinstance(data, list) else []

    def update_project_resources(self, change: Callable[[list[dict]], list[dict]]) -> list[dict]:
        """Read, change and republish the project-resource working set as one step."""
        with _PROJECT_RESOURCES_LOCK:
            entries = change(self.read_project_resources())
            self.project_resources_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.project_resources_path.with_name(self.project_resources_path.name + ".tmp")
            try:
                with open(tmp, "w") as f:
                    json.dump({"items": entries}, f, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, self.project_resources_path)
            finally:
                tmp.unlink(missing_ok=True)
            return entries

    def build_session_path(self, conversation: str | None = None, app_id: str = "") -> Path:
        """Where a Build conversation's persisted OpenCode session id lives, so a project
        re-attached after an orchestrator restart resumes the conversation it was having instead of
        starting a fresh session with no memory of the prior turns.

        A Build conversation owns its own session, the way a Chat Thread already does
        (ThreadStore.write_session_id). Both live under the same `.sage/threads/<id>/`, so a
        conversation's two halves sit together and a deleted Thread takes both with it.

        The session is what makes "New conversation" mean anything in Build: a fresh session has
        no memory of the earlier talk. It is not amnesia about the app — the session opens the
        app's directory, so the agent reads every file back.

        `app_id` is in the NAME because a session is opened on one directory and a conversation can
        build into several Built Apps (ADR-0008): a session recovered for the app the person just
        left would stand the agent in the wrong tree.

        Naming neither gives `.sage/session.json`, which is what an unscoped caller (CLI, tests)
        still gets: a build turn that names no conversation keeps a session across a restart."""
        suffix = f"-{app_id}" if app_id else ""
        if not conversation:
            return self.path / ".sage" / f"session{suffix}.json"
        return self.path / ".sage" / "threads" / conversation / f"build-session{suffix}.json"

    def read_session_id(self, conversation: str | None = None, app_id: str = "") -> str | None:
        p = self.build_session_path(conversation, app_id)
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text()).get("session_id")
        except (json.JSONDecodeError, OSError):
            return None

    def write_session_id(self, session_id: str, conversation: str | None = None,
                         app_id: str = "") -> None:
        p = self.build_session_path(conversation, app_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"session_id": session_id}))

    @property
    def catalog_overrides_path(self) -> Path:
        """Per-project overrides of the plan/implement/sovereign/default model ids, layered on
        top of the deployment-wide ModelCatalog so a project can retarget which model Auto uses
        per phase without changing every other project.

        The Project's, not the app's: the rail that sets these sits in both modes, and Chat has no
        app to keep them in."""
        return self.path / ".sage" / "model_overrides.json"

    def read_catalog_overrides(self) -> dict:
        if not self.catalog_overrides_path.exists():
            return {}
        return json.loads(self.catalog_overrides_path.read_text())

    def write_catalog_overrides(self, overrides: dict) -> None:
        self.catalog_overrides_path.parent.mkdir(parents=True, exist_ok=True)
        self.catalog_overrides_path.write_text(json.dumps(overrides))

    @property
    def instructions_path(self) -> Path:
        """The user's standing guidance for what gets built in this Project.

        The Project's, not the app's, and this is where it is KEPT. The agent reads it as a managed
        block in the app's `AGENTS.md`, but that block is a rendering: the app is one of possibly
        several, it is re-seeded from the template by Reset app, and it does not exist at all until
        a handoff is confirmed — none of which should be able to lose a sentence the person wrote
        about their Project (ADR-0008).
        """
        return self.path / ".sage" / "instructions.md"

    def read_instructions(self) -> str:
        try:
            return self.instructions_path.read_text().strip()
        except OSError:
            return ""

    def write_instructions(self, text: str) -> None:
        text = text.strip()
        if not text:
            self.instructions_path.unlink(missing_ok=True)
            return
        self.instructions_path.parent.mkdir(parents=True, exist_ok=True)
        self.instructions_path.write_text(text + "\n")

    def clear_plan_docs(self, app_id: str) -> None:
        """Drop the plan documents that name one Built App. Reset app's half of the Project's
        record: they describe the app that was just taken away, so leaving them behind would hand
        the next build a plan for code that no longer exists.

        One app's, not every one (#75). The documents live with the Project and a Project holds
        many apps, so "every document" stopped being an answer to "this app's". A document naming
        NO app is not one either: it is a plan drafted in Chat and not yet handed off, so a reset
        that took it would reach outside the app in front of the person, which is the thing that
        narrowed. Nothing is lost by leaving it — an unbound document is already the FALLBACK a
        build reads when an app has none of its own (see Orchestrator._app_plan_docs), and after a
        reset the app really is new again."""
        for doc in self.list_plan_docs():
            # Read from the document's side rather than the argument's: "names an app, and that app
            # is this one" is the rule above, and it is also what keeps an id that named nothing
            # from matching every unbound draft at once.
            named = str(doc.get("appId") or "")
            if named and named == app_id:
                shutil.rmtree(self._plan_doc_dir(doc["id"]), ignore_errors=True)


@dataclass(frozen=True)
class Workspace:
    """One Built App: `apps/<appId>/`, and the record that belongs to it.

    The Bindings manifest, the plan copy, the architecture note, the build log and the `built`
    latch are the app's, and they sit in the app's own `.sage/`. What belongs to the Project —
    Threads, plan documents, settings, sessions — is `ProjectRecord`, and a caller that wants one
    of those asks that surface for it.

    `app_id` is the directory's name and never changes: Domino fixes a published App's
    `entryPoint` when the App is created, so a rename would strand the deployment (ADR-0008).
    """

    project_id: str
    path: Path
    app_id: str

    def exists(self) -> bool:
        """True once this app has a directory. False is the ordinary state of a Project that has
        only ever been talked to in Chat: an app is born when a handoff is confirmed, and until
        then there is nothing here to read, link or ignore."""
        return self.path.is_dir()

    @property
    def app_entry(self) -> Path:
        return self.path / "src" / "App.tsx"

    @property
    def plan_path(self) -> Path:
        """The plan→implement handoff artifact (auto mode). Lives in the workspace so the
        implement session and IDE mode can both see it."""
        return self.path / ".sage" / "plan.md"

    def write_plan(self, text: str) -> None:
        self.plan_path.parent.mkdir(parents=True, exist_ok=True)
        self.plan_path.write_text(text)

    def read_plan(self) -> str | None:
        return self.plan_path.read_text() if self.plan_path.exists() else None

    @property
    def architecture_path(self) -> Path:
        """A design document the user asked for ("give me an architecture for…"). Deliberately NOT
        plan.md: a plan is a one-shot handoff that archive_plan() moves aside as soon as a build
        consumes it, and an architecture is a reference the user keeps coming back to."""
        return self.path / ".sage" / "architecture.md"

    def write_architecture(self, text: str) -> None:
        self.architecture_path.parent.mkdir(parents=True, exist_ok=True)
        self.architecture_path.write_text(text)

    def read_architecture(self) -> str | None:
        p = self.architecture_path
        return p.read_text() if p.exists() else None

    def archive_plan(self, cancelled: bool = False) -> Path | None:
        """Move the consumed plan out of the agent's live view (SPEC P6). The plan artifact is a
        one-shot handoff, not a living spec: once the Implement turn has built from it, a leftover
        `.sage/plan.md` reads like *current* intent/state and can mislead a later turn — it's the
        one .sage/ file that looks like instructions. Archived copies stay under .sage/plans/ so git
        retains the history. Returns the archive path, or None if there was no live plan.

        `cancelled` marks a plan the user dismissed without building. Same move, different filename,
        because the two are not the same fact: `read_archived_plan` answers "what is this app built
        from", and a plan nobody built is not an answer to that.
        """
        if not self.plan_path.exists():
            return None
        archive_dir = self.path / ".sage" / "plans"
        archive_dir.mkdir(parents=True, exist_ok=True)
        suffix = "-cancelled" if cancelled else ""
        n = len(list(archive_dir.glob("[0-9]*.md"))) + 1
        dest = archive_dir / f"{n:03d}{suffix}.md"
        while dest.exists():  # never clobber a prior archived plan
            n += 1
            dest = archive_dir / f"{n:03d}{suffix}.md"
        self.plan_path.rename(dest)
        return dest

    def read_archived_plan(self) -> str | None:
        """The most recently BUILT plan, or None if no build has consumed one.

        The counterpart to `archive_plan`: once a build consumes a plan there is no live `plan.md`
        left, and the app in the preview is nonetheless the thing that plan describes. The rail's
        plan pin reads this so it can say "Working from" instead of falling back to "No plan yet"
        the moment the first build finishes. Cancelled archives are skipped — the pin would
        otherwise claim the app was built from a plan the user had just dismissed. Sorted
        numerically, not lexically: `archive_plan` zero-pads to three digits and would run out at
        1000.
        """
        archive_dir = self.path / ".sage" / "plans"
        built = [p for p in archive_dir.glob("[0-9]*.md") if p.is_file() and p.stem.isdigit()]
        if not built:
            return None
        try:
            return max(built, key=lambda p: int(p.stem)).read_text()
        except OSError:
            return None

    @property
    def _settings_path(self) -> Path:
        """Where this app keeps what it knows about itself: the `built` and `last_turn_failed`
        latches, and the display name a person gave it.

        The app's own settings file, inside the app's directory. It shares a NAME with
        `ProjectRecord.settings_path` and nothing else: a caller that wants the Project's settings
        asks `ProjectRecord` for them rather than reaching in here.
        """
        return self.path / ".sage" / "settings.json"

    def display_name(self) -> str:
        """What a person calls this app in the rail, or "" if nobody has named it.

        The mutable half of the app's identity: `app_id` is the directory's name and can never
        change, so this is the half a rename moves (ADR-0008). Kept in the app's own settings, which
        makes it one writer per app — the same reason the app list is a scan and not an index.
        """
        stored = _read_settings_file(self._settings_path).get("displayName")
        return stored.strip() if isinstance(stored, str) else ""

    def set_display_name(self, name: str) -> None:
        settings = _read_settings_file(self._settings_path)
        settings["displayName"] = name.strip()
        _write_settings_file(self._settings_path, settings)

    def domino_app_id(self) -> str:
        """The Domino App this Built App deploys to, or "" before its first publish.

        Recorded here rather than asked of the Domino project, which now holds one App per Built
        App and so cannot answer "the app": the lookup returned the first of them, which meant
        publishing the second app shipped its code as a new version of the first (ADR-0008, #70).
        The id is also what keeps the URL stable, because a re-publish posts a version to it.
        """
        stored = _read_settings_file(self._settings_path).get("dominoAppId")
        return stored.strip() if isinstance(stored, str) else ""

    def record_domino_app(self, app_id: str) -> None:
        """Remember which Domino App this app's first publish created, so every later publish is a
        new version of that one. Written in the app's own settings, which makes it one writer per
        app — the same reason the app list is a scan and not an index.

        settings.json is committed, so the record travels to the other Sage Builders in this
        Project — but only from the next save, because the id does not exist until after the
        pre-publish one has run."""
        settings = _read_settings_file(self._settings_path)
        settings["dominoAppId"] = app_id
        _write_settings_file(self._settings_path, settings)

    def has_built(self) -> bool:
        """True once a code-writing build has completed here. Drives the first-BUILD plan gate
        (not first-turn): questions asked before the first build must not consume the gate, and the
        gate must still fire on the first real build request no matter how many questions preceded it."""
        return bool(_read_settings_file(self._settings_path).get("built"))

    def built_at(self) -> str:
        """When this app was last built, or "" if it never was — and "" for an app built before the
        stamp existed, which reads as the honest "no date" rather than a wrong one.

        The handoff sheet lists the Project's apps by this, because "which app do I build into" is
        a question about which one is still alive: a name alone leaves two dashboards from March
        and yesterday looking identical (ADR-0008, #73)."""
        stored = _read_settings_file(self._settings_path).get("builtAt")
        return stored.strip() if isinstance(stored, str) else ""

    def mark_built(self) -> None:
        """Latch has_built() on after the first successful build, and stamp the time of THIS one.
        Persisted in settings so it survives an orchestrator restart (a rebuilt project must not
        re-gate). The latch is idempotent; the stamp is not, because it is the LAST build's."""
        settings = _read_settings_file(self._settings_path)
        settings["built"] = True
        settings["builtAt"] = _now()
        _write_settings_file(self._settings_path, settings)

    def clear_built(self) -> None:
        """Un-latch has_built(), so Reset app leaves the plan gate where a fresh app has it.

        The counterpart to mark_built, and the reason it is a method rather than a caller editing
        settings: the latch is a fact about the code Reset just took away, so removing it is the
        app's own business and not an edit to the Project's settings."""
        settings = _read_settings_file(self._settings_path)
        settings.pop("built", None)
        settings.pop("builtAt", None)
        _write_settings_file(self._settings_path, settings)

    def read_last_turn_failed(self) -> bool:
        """True when the previous build attempt on this project ended badly (see the failure-replan
        block in orchestrator.service). Drives the cross-turn failure gate: the turn after a failure
        is exactly when stopping to plan is worth the interruption.

        Lives in settings.json next to `built` rather than being derived from history.jsonl: the
        transcript is append-only and replayable, so it can't record that a signal has been CONSUMED,
        and consumption is what keeps this one-shot instead of a permanent approval wall. Fails open
        on read — missing or corrupt state reads as "didn't fail", i.e. build."""
        return bool(_read_settings_file(self._settings_path).get("last_turn_failed"))

    def set_last_turn_failed(self, failed: bool) -> None:
        """Record (or clear) the previous-turn failure signal. Best-effort by design: this runs on the
        terminal path of every turn, and a workspace we can't write to must not turn a finished build
        into a raised exception mid-stream. A lost write just means no gate next turn — the same
        behaviour as before this feature existed."""
        try:
            settings = _read_settings_file(self._settings_path)
            if bool(settings.get("last_turn_failed")) == failed:
                return
            settings["last_turn_failed"] = failed
            _write_settings_file(self._settings_path, settings)
        except OSError:
            pass

    @property
    def history_path(self) -> Path:
        """Append-only transcript of this app's chat-visible build events, so the UI can replay
        them after a page reload or an orchestrator restart (neither of which the in-memory
        registry survives).

        One log per Built App, not per Project (ADR-0008). The stop-button baseline below is a
        POSITION in this file, so a log two apps shared would let one viewer's stop rewind past the
        other's turns — and two viewers in one Project are two Sage Builders, which is to say two
        processes with no lock between them."""
        return self.path / ".sage" / "history.jsonl"

    def append_history(self, entry: dict, conversation: str | None = None) -> None:
        """Write one event, stamped with the app and the conversation it belongs to.

        `conversation` tags the entry with the Build conversation that produced it, so the UI can
        replay one conversation rather than the app's whole log. `app` is stamped here rather than
        passed in, because the file being written IS the app's: one Thread can hand off more than
        once, so a conversation no longer says which app its turn built, and an entry read out of
        the log should not need the path it came from to answer that.

        The file stays one append-only log: history.md renders all of it (the agent's memory is
        per app on purpose), and the stop-button baseline below stays positional and therefore
        stays correct."""
        self.history_path.parent.mkdir(parents=True, exist_ok=True)
        row = {**entry, "app": self.app_id}
        if conversation:
            row["conversation"] = conversation
        with self.history_path.open("a") as f:
            f.write(json.dumps(row) + "\n")

    def _iter_history(self, only: str | None = None) -> Iterator[dict]:
        """One line at a time. The log reaches megabytes on a long-lived project (~68KB per user
        turn), and every caller below used to pay a whole-file read plus a parse of every line to
        answer a question most of them could answer from a fraction of it.

        `only` skips the parse for any line that does not contain that raw text."""
        if not self.history_path.exists():
            return
        with self.history_path.open() as f:
            for line in f:
                if line.strip() and (only is None or only in line):
                    yield json.loads(line)

    @staticmethod
    def _tag_text(conversation: str | None = None) -> str:
        """The tag as append_history() writes it, so the two cannot drift: same json.dumps, same
        separator. Naming a conversation gives the whole `"conversation": "thr_a"` pair; naming
        none gives the key alone. JSON escapes every quote inside a string value, so neither can
        appear in a line except as the tag itself — which makes a raw substring test on the line
        the same answer as parsing it, and the log is megabytes."""
        if conversation is None:
            return json.dumps("conversation") + ":"
        return json.dumps({"conversation": conversation})[1:-1]

    def read_history(self, conversation: str | None = None) -> list[dict]:
        """No conversation means this app's whole log: history.md and any caller that wants the
        log as written. Naming one filters to it. Every row is this app's either way — the file is
        the app's — so there is nothing to filter on that side."""
        if conversation is None:
            return list(self._iter_history())
        # The pre-filter can only over-select (the equality check below still decides), so an app
        # with several conversations parses its own turns instead of everyone's.
        tag = self._tag_text(conversation)
        return [r for r in self._iter_history(only=tag) if r.get("conversation") == conversation]

    def has_untagged_history(self) -> bool:
        """True while entries written before conversation tagging are still unclaimed.

        Runs on every conversation switch, and the answer is no for the whole life of a project
        after the one adoption that makes it no. append_history() is the only writer of the tag,
        and it writes the key only for a conversation it has, so a line missing the key is an
        untagged entry and no line has to be parsed to see that."""
        if not self.history_path.exists():
            return False
        key = self._tag_text()
        with self.history_path.open() as f:
            return any(key not in line for line in f if line.strip())

    def adopt_history(self, conversation: str) -> None:
        """Give every untagged entry to `conversation`. Build history predates tagging, so an
        upgrade would otherwise blank a project's transcript. Rewrites in place and keeps order,
        so the positional stop-button baseline survives. Idempotent."""
        rows = self.read_history()
        if not rows:
            return
        adopted = [r if r.get("conversation") else {**r, "conversation": conversation} for r in rows]
        self.history_path.write_text("".join(json.dumps(r) + "\n" for r in adopted))

    def history_len(self) -> int:
        """Counts the lines truncate_history() would keep. Deliberately does not parse them: this
        runs twice a turn, only to take the stop-button baseline, and the baseline is a position."""
        if not self.history_path.exists():
            return 0
        with self.history_path.open() as f:
            return sum(1 for line in f if line.strip())

    def truncate_history(self, n: int) -> None:
        """Drop everything appended after the first `n` entries (stop-button revert:
        removes the in-progress turn's user prompt and any partial response)."""
        if not self.history_path.exists():
            return
        lines = self.history_path.read_text().splitlines()[:n]
        self.history_path.write_text("".join(line + "\n" for line in lines))

    @property
    def history_md_path(self) -> Path:
        """Grep-able rendering of history.jsonl, for the AGENT rather than the UI. OpenCode compacts
        a long build's context and the dropped detail is gone from the model's view; this gives it a
        way back to what was already asked, planned, or rejected. Not committed (#65): it is
        regenerated from the log on every turn, so two Builders in one Project conflicted on it
        every turn over data either could rebuild. The workspace `.ignore` rule is what keeps it
        greppable once it is gitignored — see Orchestrator._refresh_history_archive."""
        return self.path / ".sage" / "history.md"

    # Entries worth archiving: the ones that carry a decision. Tool calls, typechecks and dividers
    # are replay noise — they'd bury the user's actual words in the model's grep results.
    _ARCHIVED_EVENTS = frozenset({"user", "agent", "plan-proposed", "step-done"})

    # Turns kept in the archive. Bounds a file we fully rewrite once per turn.
    _MAX_ARCHIVED_TURNS = 40

    def render_history_md(self) -> None:
        """Regenerate history.md from history.jsonl. Full rewrite, never incremental: truncate_history()
        rewinds the JSONL on stop, so a from-scratch render self-heals instead of needing its own
        rollback path. Call BEFORE a turn's tree baseline is taken — writing it mid-turn would read
        as an agent edit and fail the read-only gate."""
        # Streams, and keeps only the turns it will actually write. The log outgrows the archive
        # early — at 100 turns it is ~6.8MB — and holding all of it to then throw 60% away is the
        # one part of this rewrite that grew without bound.
        turns: deque[list[dict]] = deque(maxlen=self._MAX_ARCHIVED_TURNS)
        total = 0
        for entry in self._iter_history():
            if entry.get("type") not in self._ARCHIVED_EVENTS:
                continue
            if entry.get("type") == "user" or not total:
                turns.append([])
                total += 1
            turns[-1].append(entry)

        if not total:
            self.history_md_path.unlink(missing_ok=True)
            return

        dropped = max(0, total - self._MAX_ARCHIVED_TURNS)
        out = ["<!-- Generated by Sage from .sage/history.jsonl. Overwritten each turn — do not edit. -->",
               "", "# Earlier turns", ""]
        if dropped:
            # Say so explicitly: a model that greps and misses would otherwise conclude the user never
            # said it, which is worse than knowing the record is partial.
            out += [f"_Turns 1–{dropped} are older than this archive keeps and were dropped._", ""]
        for i, turn in enumerate(turns, start=dropped + 1):
            out += [f"## Turn {i}", ""]
            for entry in turn:
                out += self._render_entry(entry)
        self.history_md_path.parent.mkdir(parents=True, exist_ok=True)
        self.history_md_path.write_text("\n".join(out).rstrip() + "\n")

    @staticmethod
    def _render_entry(entry: dict) -> list[str]:
        kind = entry.get("type")
        if kind == "user":
            return [f"**User:** {entry.get('text', '')}".rstrip(), ""]
        if kind == "agent":
            # Only the prose. kind == "tool" is a per-call trace with no decision in it.
            if entry.get("kind") != "text":
                return []
            return [f"**Sage:** {entry.get('text', '')}".rstrip(), ""]
        if kind == "plan-proposed":
            return ["**Plan proposed:**", "", (entry.get("plan") or "").strip(), ""]
        if kind == "step-done":
            outcome = "ok" if entry.get("ok") else "failed"
            return [f"- step {entry.get('n')}/{entry.get('total')}: {outcome}", ""]
        return []

    @property
    def attachments_path(self) -> Path:
        """Committed manifest of attached/uploaded data files. `public/data/` itself is gitignored
        (data never enters git), so this manifest is the source of truth that lets the PUBLISHED app
        rebuild public/data/ from the project's dataset mounts at startup — see the template's
        scripts/rehydrate-data.mjs. Lives under committed .sage/ (like plan.md / history.jsonl)."""
        return self.path / ".sage" / "attachments.json"

    def read_attachments(self) -> list[dict]:
        if not self.attachments_path.exists():
            return []
        try:
            data = json.loads(self.attachments_path.read_text())
        except (json.JSONDecodeError, OSError):
            return []
        return data if isinstance(data, list) else []

    def write_attachments(self, entries: list[dict]) -> None:
        self.attachments_path.parent.mkdir(parents=True, exist_ok=True)
        self.attachments_path.write_text(json.dumps(entries, indent=2))

    @property
    def bindings_path(self) -> Path:
        """Committed manifest of the Resources this app uses (#6).

        Its own file rather than another entry kind in attachments.json: that manifest's consumer,
        the template's scripts/rehydrate-data.mjs, skips any entry it does not recognise without a
        word, so a Binding stored there would vanish silently.
        """
        return self.path / ".sage" / "bindings.json"

    def read_bindings(self) -> list[dict]:
        if not self.bindings_path.exists():
            return []
        try:
            data = json.loads(self.bindings_path.read_text())
        except (json.JSONDecodeError, OSError):
            return []
        return data if isinstance(data, list) else []

    def update_bindings(self, change: Callable[[list[dict]], list[dict]]) -> list[dict]:
        """Read, change and republish the bindings manifest as one step, and return the new list.

        Read-modify-write under a lock, then os.replace, so two requests that arrive together cannot
        drop one of the two edits and a reader never sees a half-written file. write_attachments
        does neither — it truncates in place, last writer wins — which is why this is not built on
        it.
        """
        with _BINDINGS_LOCK:
            entries = change(self.read_bindings())
            self.bindings_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.bindings_path.with_name(self.bindings_path.name + ".tmp")
            try:
                with open(tmp, "w") as f:
                    json.dump(entries, f, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, self.bindings_path)
            finally:
                # A leftover .tmp inside committed .sage/ would land in the user's app repo.
                tmp.unlink(missing_ok=True)
            return entries


class WorkspaceManager:
    """Manages the Built App inside this builder's Domino project volume.

    Per D9 one container hosts one project, so the volume IS the project's mounted directory
    (git-based: /mnt/code), not a per-id copy under some root. The app lives one level down, in
    `apps/<appId>/` (ADR-0008): `ensure` idempotently seeds the warm React+Vite template into that
    directory the first time and guarantees the warm node_modules symlink; a directory that
    already holds an app is left untouched.

    A Project holds several, and this manager points at one of them at a time. The list is found by
    scanning `apps/` rather than read from an index file: an index is one file with many writers,
    which is the thing two Sage Builders in one Project keep losing work to.
    """

    def __init__(self, workspace_dir: Path, template: Path) -> None:
        self._dir = Path(workspace_dir)
        self._template = Path(template)
        # The app every caller in this process means by "the app": the one selected, or the id
        # minted for an app that has no directory yet. Chat opens a volume with no app on it, and
        # the app a handoff goes on to seed must be the one every caller in between was already
        # naming — otherwise `_ensure_seeded` seeds a second directory beside the one the attached
        # Project is pointing at.
        #
        # Process state rather than a file. One Sage Builder shows one app at a time and the
        # selection is that view, not a fact about the Project — writing it down would put a second
        # writer on a shared file to record something only this browser tab cares about. A restart
        # lands on the newest app, which is the one a confirmed handoff just made.
        self._selected: str | None = None

    @property
    def template(self) -> Path:
        """The warm template this manager seeds from. Read by the orchestrator so it can ask the
        Built App's own `serve.py` what it will and will not run (#15)."""
        return self._template

    @property
    def path(self) -> Path:
        """The Project's volume: the git repo root, and where the Project's own record lives."""
        return self._dir

    @property
    def apps_dir(self) -> Path:
        return self._dir / _APPS

    def app_ids(self) -> list[str]:
        """Every Built App on this volume, by directory scan, oldest first.

        Sorted by name, which sorts by age: `new_id` leads with epoch-ms, so the directory name
        carries the order a list would otherwise have to remember.
        """
        if not self.apps_dir.is_dir():
            return []
        return sorted(p.name for p in self.apps_dir.iterdir() if p.is_dir() and not p.is_symlink())

    def selected_app_id(self) -> str:
        """The Built App this manager is pointed at: the selection, else the newest on disk, else
        one minted for the app that does not exist yet.

        The minting is why this is asked rather than read: a Project with no app is the ordinary
        state, and every caller in between has to name the same not-yet-seeded app. Once an id is
        settled it never changes — the directory IS the id, and Domino fixes a published App's
        `entryPoint` at creation.
        """
        if self._selected is None:
            existing = self.app_ids()
            self._selected = existing[-1] if existing else new_id("app")
        return self._selected

    def select(self, app_id: str) -> str:
        """Point this manager at another Built App. Raises KeyError for one that is not there."""
        if app_id not in self.app_ids():
            raise KeyError(app_id)
        self._selected = app_id
        return app_id

    def create_app(self, project_id: str) -> Workspace:
        """Mint a Built App, seed it from the template, and select it.

        The one path that ADDS to `apps/`. `ensure` is get-or-seed and answers for whichever app is
        already selected, which is what every build turn wants; this is what a confirmed handoff
        wants, because that is where a Built App is born (ADR-0008).
        """
        self._selected = new_id("app")
        return self.ensure(project_id, seed_app=True)

    @property
    def app_path(self) -> Path:
        return self.apps_dir / self.selected_app_id()

    def app_workspace(self, project_id: str, app_id: str | None = None) -> Workspace:
        """One Built App's record, without seeding or starting anything. For readers that want the
        transcript, the name or the Bindings off the volume without attaching the project. Names no
        app to get the selected one."""
        app_id = app_id or self.selected_app_id()
        return Workspace(project_id, self.apps_dir / app_id, app_id)

    def reset(self) -> None:
        """Put the app code back to the starter template, keeping everything that is the user's (#36).

        `ensure` cannot do this: it is get-or-seed and skips every entry that already exists, so on a
        built workspace it is a no-op. Starting over needed its own operation, and until it had one
        "rebuild this from scratch" was only a sentence handed to the build agent — which built the
        most literal thing those words describe, a page about rebuilding.

        What survives is what the user set up rather than what a build produced: the app's `.sage/`
        (history, settings, the attachment and Binding manifests), `public/data/` (the files they
        attached), and the warm `node_modules` link. What goes is the app: every other top-level
        entry in the app directory, re-seeded from the template.

        `.sage/queries.json` goes with it — it is the app's SQL, written by the agent, and an app that
        no longer exists has no queries. So do `plan.md` and `architecture.md`: both describe the app
        that was just removed, and AGENTS.md tells the agent plan.md is the LIVE plan, so leaving one
        behind would point the next turn at a design for code that is gone.

        Only the app is reset. The Project's record — Threads, settings — is not this operation's
        to take, and the plan documents that describe the gone app are cleared through the surface
        that owns them (`ProjectRecord.clear_plan_docs`, called by Orchestrator.reset_app).

        AGENTS.md is re-seeded like any other template file, so the caller is responsible for
        splicing the user's project instructions back into it (see Orchestrator.reset_app)."""
        app = self.app_path
        keep = _RESET_KEEP | {p.parts[0] for p in _RESET_KEEP_NESTED}
        for item in app.iterdir():
            if item.name in keep:
                continue
            if item.is_dir() and not item.is_symlink():
                shutil.rmtree(item, ignore_errors=True)
            else:
                item.unlink(missing_ok=True)
        # A kept top-level dir is emptied of everything but the nested path that earned it its place:
        # `public/` is template content, `public/data/` is the user's attachments living inside it.
        for nested in _RESET_KEEP_NESTED:
            root = app / nested.parts[0]
            if not root.is_dir():
                continue
            for item in root.iterdir():
                if item.name == nested.parts[1]:
                    continue
                if item.is_dir() and not item.is_symlink():
                    shutil.rmtree(item, ignore_errors=True)
                else:
                    item.unlink(missing_ok=True)
        for rel in _RESET_CLEAR:
            (app / rel).unlink(missing_ok=True)
        for item in self._template.iterdir():
            if item.name in _SEED_SKIP:
                continue
            dest = app / item.name
            if item.is_dir():
                # dirs_exist_ok: `public/` is still standing because it holds the attachments.
                shutil.copytree(item, dest, ignore=_IGNORE, dirs_exist_ok=True)
            else:
                shutil.copy2(item, dest)
        self.link_warm_deps()

    def ensure(self, project_id: str, seed_app: bool = True) -> Workspace:
        """Get-or-seed this Project's Built App. Idempotent: seeds the template into
        `apps/<appId>/` only when that directory has no app yet (no package.json), never
        clobbering an app already there.

        `seed_app=False` is Chat: attach the volume, and leave the app directory uncreated. A
        Project with no app on it is the ordinary state — an app is born when a handoff is
        confirmed — so this must not be the thing that creates one.
        """
        self._dir.mkdir(parents=True, exist_ok=True)
        self._ensure_project_ignores()
        app = self.app_path
        if seed_app:
            app.mkdir(parents=True, exist_ok=True)
            if not (app / "package.json").exists():
                # Seed the template INTO the (possibly pre-existing) directory entry by entry, so
                # anything already there is preserved.
                for item in self._template.iterdir():
                    if item.name in _SEED_SKIP:
                        continue
                    dest = app / item.name
                    if dest.exists():
                        continue
                    if item.is_dir():
                        shutil.copytree(item, dest, ignore=_IGNORE)
                    else:
                        shutil.copy2(item, dest)
            self.link_warm_deps()
        return Workspace(project_id, app, self.selected_app_id())

    def _ensure_project_ignores(self) -> None:
        """Put the Project's own ignore rules at the volume root, and keep them there.

        Both modes, because Chat is what writes two of the three trees and Chat never seeds an app.
        Append-only and idempotent: the file belongs to the Project's repo and may hold rules a
        person put there.
        """
        try:
            for line in _PROJECT_IGNORE:
                ensure_ignore_line(self._dir / ".gitignore", line)
        except OSError:
            log.warning("workspace: could not write the Project's .gitignore")

    def project_record(self, project_id: str) -> ProjectRecord:
        """The Project's own record over the volume, alongside the Built App `ensure` returns.

        Seeds nothing and starts nothing: the Project's Threads, plan documents and settings are
        readable on a volume that carries no app at all, which is what Chat opens on.
        """
        return ProjectRecord(project_id, self._dir)

    def link_warm_deps(self) -> bool:
        """Point node_modules at the baked template copy, repairing a wrecked one. True if changed.

        Repair, not just create. An agent-run `npm install` refuses to write into a symlinked
        node_modules: it deletes the link ("npm warn reify Removing non-directory …") and puts a
        real directory there — and it does that during reify, BEFORE it knows whether the install
        resolves. Install a package that 404s and npm aborts having already destroyed the link, so
        the volume is left with no deps at all: every later build fails, and the preview can't start
        because `npm run dev` has no node_modules/.bin/vite. Verified live 2026-08-13.

        The sentinel is that vite binary — it's what both `npm run dev` and `npm run build` invoke.
        Present means the deps are usable and we keep our hands off, whether they're ours or an
        agent's successful install. Absent means wreckage, and the warm copy is strictly better than
        what's there. A template without the sentinel isn't one we can lend from, so we do nothing.
        """
        tmpl = self._template / "node_modules"
        if not tmpl.exists():
            return False
        node_modules = self.app_path / "node_modules"

        # Nothing usable there: never linked, or a link left dangling. exists() follows the link and
        # reports False while the link itself is still present, which is why the unlink comes first.
        if not node_modules.exists():
            if node_modules.is_symlink():
                node_modules.unlink()
            os.symlink(tmpl, node_modules)
            return True

        # Something IS there, so only replace it where we can prove it's wreckage — which needs a
        # template carrying the sentinel to compare against. Without one we leave the volume alone
        # rather than guess, and this stays exactly the create-if-absent link it has always been.
        if (tmpl / _DEPS_SENTINEL).exists() and not (node_modules / _DEPS_SENTINEL).exists():
            if node_modules.is_symlink() or node_modules.is_file():
                node_modules.unlink()
            else:
                shutil.rmtree(node_modules, ignore_errors=True)
            os.symlink(tmpl, node_modules)
            return True
        return False

    def refresh_entry_script(self) -> bool:
        """Bring the workspace's deploy files back in line with the template. True if any changed.

        app.sh and the serve.py it execs are Sage infrastructure, not app content: they encode how a
        published App installs, builds and serves itself, and the agent has no reason to touch them.
        But they're COMMITTED to the app's repo when the project is seeded, so an app keeps whatever
        copies it was born with — which meant a fix to the template only ever reached NEW apps, while
        every existing app went on crash-looping on the bug we'd already fixed (the Node-18 PATH
        order, 2026-08-07). Callers refresh at publish time so the fix travels to every app that
        deploys.

        Order is load-bearing (_DEPLOY_FILES): app.sh lands LAST, after everything it calls, so a
        refresh that fails partway leaves an app that still serves rather than one that can't boot.
        The rehydrate pair is on that list for the same reason app.sh is — an app born before a
        rehydrate step existed would otherwise deploy an app.sh that calls a script it doesn't have.
        """
        changed = False
        for name in _DEPLOY_FILES:
            src = self._template / name
            if not src.is_file():
                continue
            dst = self.app_path / name
            if dst.is_file() and dst.read_bytes() == src.read_bytes():
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)  # scripts/ may predate this app
            shutil.copy2(src, dst)  # copy2 keeps the +x bit Domino needs to run app.sh
            changed = True
        return changed

    def refresh_preview_config(self) -> bool:
        """Bring `vite.config.ts` back in line with the template. True if it changed.

        The preview twin of refresh_entry_script, and it exists for the same reason that one does: the
        file is committed when the project is seeded, so an app keeps whatever copy it was born with,
        and a fix to the template reaches only new apps while every existing one goes on hitting the
        bug we already fixed. AGENTS.md already tells the agent not to touch this file, so there is
        nothing of theirs in it to lose.

        Refreshed at ATTACH rather than at publish, unlike the deploy files: what it configures is the
        preview, so by publish time the damage it prevents has already been done. It has to land
        before ViteSupervisor.start(), because the dev server reads this file once at boot.
        """
        return self._ensure_helper("vite.config.ts", refresh=True)

    def ensure_llm_helper(self) -> bool:
        """Put the Sage-owned model helper in the workspace, replacing a stale copy. True if written.

        `src/sageLlm.ts` ships in the template, so every project seeded after #7 already has it. The
        absent case is for the ones seeded before: their repo has no helper, and writing the config
        file next to a missing module would leave an app that cannot build.

        This one REFRESHES where the other two only fill a gap, and the difference earned itself.
        Missing-only meant a fix to the helper reached new projects and never existing ones: an app
        that could not call its model in the preview (#7) stayed that way through the whole session
        that reported it, and the only routes to the fix were Reset app — which throws the app
        away — or a new project. The stated risk of refreshing was replacing a copy the app's code
        imports, and it is bounded by what this file actually is: Sage owns it, AGENTS.md forbids the
        agent to edit or re-create it, and its exported surface (`askModel`, `checkModel`, the types)
        is what apps import and does not change. A helper that reads an older config keeps working
        for the same reason it always did — `render_config`'s two shapes are both handled here.
        """
        return self._ensure_helper(_LLM_HELPER, refresh=True)

    def ensure_model_api_helper(self) -> bool:
        """The same, for `src/sageModelApi.ts` (#9), and for projects seeded before it shipped."""
        return self._ensure_helper(_MODEL_API_HELPER)

    def ensure_query_helper(self) -> bool:
        """The same, for `src/sageQuery.ts` (#15)."""
        return self._ensure_helper(_QUERY_HELPER)

    def _ensure_helper(self, rel: str, *, refresh: bool = False) -> bool:
        """Copy one Sage-owned helper in. `refresh` also replaces a copy that differs from the
        template's; without it an existing file is left alone whatever it holds."""
        src = self._template / rel
        dst = self.app_path / rel
        if not src.is_file():
            return False
        if dst.is_file() and (not refresh or dst.read_bytes() == src.read_bytes()):
            return False
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        return True
