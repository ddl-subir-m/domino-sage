"""Workspace module (SPEC C1/C9, DESIGN Seam 3 handoff).

The single working directory (the Domino project's mounted volume) seeded from the warm
React+Vite template. Deep module, narrow interface: callers ensure the workspace and read/write
the plan artifact; how the template is materialized (seed source + symlink the warm
node_modules) is hidden.

Two record surfaces sit over that directory, because a Project holds many Built Apps
(ADR-0008). `Workspace` is one Built App — its code, Bindings, plan copy, architecture note and
build log. `ProjectRecord` is the Project — its plan documents and settings, alongside the
Threads `ThreadStore` already keeps. Both resolve the same root today: no app has moved under
`apps/<appId>/` yet, so what is separated here is which surface answers a question, not where
the answer is stored.

node_modules is symlinked from the template rather than copied so each workspace is warm
(deps already installed) without paying a multi-hundred-MB copy per project.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import threading
from collections import deque
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

from . import plan_doc

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
# Sage metadata that belongs to the APP, so it goes when the app does. queries.json is the app's SQL;
# plan.md and architecture.md both describe the code being removed, and AGENTS.md tells the agent
# plan.md is the live plan — a stale one would aim the next turn at an app that is gone.
_RESET_CLEAR = (Path(".sage") / "queries.json",
                Path(".sage") / "plan.md",
                Path(".sage") / "architecture.md")
# Same rule, but a directory: the plan documents describe the app being replaced, so a Reset
# that left them behind would hand the next build a plan for the app that just went away.
_RESET_CLEAR_DIRS = (Path(".sage") / "plan-docs",)
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

    A Project holds Threads, plan documents and its own settings. A Built App holds its code, its
    Bindings, the plan copy its builder consumes and its build log; `Workspace` is that surface,
    and this is the narrower one beside it. Both name the same directory today, because no app has
    moved under `apps/<appId>/` yet — so the separation is in the surface rather than on disk, and
    it holds only because nothing here reads an app's record and nothing on `Workspace` reads the
    Project's.

    Threads are absent because they already have a surface of their own: `ThreadStore`.
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
                        origin_thread_id: str = "", status: str = "draft") -> dict:
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
            "createdAt": now, "updatedAt": now, "originThreadId": origin_thread_id, "appId": "",
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


@dataclass(frozen=True)
class Workspace:
    """One Built App: the directory its code lives in, and the record that belongs to it.

    The Bindings manifest, the plan copy, the architecture note, the build log and the `built`
    latch are the app's. What belongs to the Project — Threads, plan documents, settings — is
    `ProjectRecord`, and a caller that wants one of those asks that surface for it.
    """

    project_id: str
    path: Path

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
    def _latch_path(self) -> Path:
        """Where this app keeps the two facts it latches: `built` and `last_turn_failed`.

        The same file `ProjectRecord.settings_path` names, because no Built App has a directory of
        its own to keep them in yet (ADR-0008). Private on purpose: sharing a file is not the same
        as sharing a record, and a caller that wants the Project's settings asks `ProjectRecord`
        for them rather than reaching in here.
        """
        return self.path / ".sage" / "settings.json"

    def has_built(self) -> bool:
        """True once a code-writing build has completed here. Drives the first-BUILD plan gate
        (not first-turn): questions asked before the first build must not consume the gate, and the
        gate must still fire on the first real build request no matter how many questions preceded it."""
        return bool(_read_settings_file(self._latch_path).get("built"))

    def mark_built(self) -> None:
        """Latch has_built() on after the first successful build. Idempotent; persisted in settings
        so it survives an orchestrator restart (a rebuilt project must not re-gate)."""
        settings = _read_settings_file(self._latch_path)
        if not settings.get("built"):
            settings["built"] = True
            _write_settings_file(self._latch_path, settings)

    def clear_built(self) -> None:
        """Un-latch has_built(), so Reset app leaves the plan gate where a fresh app has it.

        The counterpart to mark_built, and the reason it is a method rather than a caller editing
        settings: the latch is a fact about the code Reset just took away, so removing it is the
        app's own business and not an edit to the Project's settings."""
        settings = _read_settings_file(self._latch_path)
        settings.pop("built", None)
        _write_settings_file(self._latch_path, settings)

    def read_last_turn_failed(self) -> bool:
        """True when the previous build attempt on this project ended badly (see the failure-replan
        block in orchestrator.service). Drives the cross-turn failure gate: the turn after a failure
        is exactly when stopping to plan is worth the interruption.

        Lives in settings.json next to `built` rather than being derived from history.jsonl: the
        transcript is append-only and replayable, so it can't record that a signal has been CONSUMED,
        and consumption is what keeps this one-shot instead of a permanent approval wall. Fails open
        on read — missing or corrupt state reads as "didn't fail", i.e. build."""
        return bool(_read_settings_file(self._latch_path).get("last_turn_failed"))

    def set_last_turn_failed(self, failed: bool) -> None:
        """Record (or clear) the previous-turn failure signal. Best-effort by design: this runs on the
        terminal path of every turn, and a workspace we can't write to must not turn a finished build
        into a raised exception mid-stream. A lost write just means no gate next turn — the same
        behaviour as before this feature existed."""
        try:
            settings = _read_settings_file(self._latch_path)
            if bool(settings.get("last_turn_failed")) == failed:
                return
            settings["last_turn_failed"] = failed
            _write_settings_file(self._latch_path, settings)
        except OSError:
            pass

    @property
    def session_path(self) -> Path:
        """Persisted OpenCode session id, so the project re-attached after an orchestrator restart
        (see Orchestrator.project) can resume the same conversation instead of starting a fresh
        session with no memory of prior turns. The unscoped path: a build turn that names no
        conversation (CLI, tests) still gets a session that survives a restart."""
        return self.path / ".sage" / "session.json"

    def build_session_path(self, conversation: str | None = None) -> Path:
        """A Build conversation owns its own OpenCode session, the way a Chat Thread already does
        (ThreadStore.write_session_id). Both live under the same `.sage/threads/<id>/`, so a
        conversation's two halves sit together and a deleted Thread takes both with it.

        The session is what makes "New conversation" mean anything in Build: a fresh session has
        no memory of the earlier talk. It is not amnesia about the app — the session opens the
        same workspace, so the agent reads every file back."""
        if not conversation:
            return self.session_path
        return self.path / ".sage" / "threads" / conversation / "build-session.json"

    def read_session_id(self, conversation: str | None = None) -> str | None:
        p = self.build_session_path(conversation)
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text()).get("session_id")
        except (json.JSONDecodeError, OSError):
            return None

    def write_session_id(self, session_id: str, conversation: str | None = None) -> None:
        p = self.build_session_path(conversation)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"session_id": session_id}))

    @property
    def history_path(self) -> Path:
        """Append-only transcript of chat-visible build events, so the UI can replay a project's
        history after a page reload or an orchestrator restart (neither of which the in-memory
        registry survives)."""
        return self.path / ".sage" / "history.jsonl"

    def append_history(self, entry: dict, conversation: str | None = None) -> None:
        """`conversation` tags the entry with the Build conversation that produced it, so the UI
        can replay one conversation rather than the whole project. The file stays one append-only
        log: history.md renders all of it (the agent's memory is project-wide on purpose), and the
        stop-button baseline below stays positional and therefore stays correct."""
        self.history_path.parent.mkdir(parents=True, exist_ok=True)
        row = {**entry, "conversation": conversation} if conversation else entry
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
        """No conversation means the whole project: history.md and any caller that wants the log
        as written. Naming one filters to it."""
        if conversation is None:
            return list(self._iter_history())
        # The pre-filter can only over-select (the equality check below still decides), so a
        # project with several conversations parses its own turns instead of everyone's.
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
    def catalog_overrides_path(self) -> Path:
        """Per-project overrides of the plan/implement/sovereign/default model ids, layered on
        top of the deployment-wide ModelCatalog so a project can retarget which model Auto uses
        per phase without changing every other project."""
        return self.path / ".sage" / "model_overrides.json"

    def read_catalog_overrides(self) -> dict:
        if not self.catalog_overrides_path.exists():
            return {}
        return json.loads(self.catalog_overrides_path.read_text())

    def write_catalog_overrides(self, overrides: dict) -> None:
        self.catalog_overrides_path.parent.mkdir(parents=True, exist_ok=True)
        self.catalog_overrides_path.write_text(json.dumps(overrides))

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
    """Manages the single workspace bound to this builder's Domino project volume.

    Per D9 one container hosts one project, so the workspace IS the project's mounted directory
    (git-based: /mnt/code), not a per-id copy under some root. `ensure` idempotently seeds the warm
    React+Vite template into that volume the first time (when it carries no app yet) and guarantees
    the warm node_modules symlink; a volume that already holds an app is left untouched.
    """

    def __init__(self, workspace_dir: Path, template: Path) -> None:
        self._dir = Path(workspace_dir)
        self._template = Path(template)

    @property
    def template(self) -> Path:
        """The warm template this manager seeds from. Read by the orchestrator so it can ask the
        Built App's own `serve.py` what it will and will not run (#15)."""
        return self._template

    @property
    def path(self) -> Path:
        return self._dir

    def reset(self) -> None:
        """Put the app code back to the starter template, keeping everything that is the user's (#36).

        `ensure` cannot do this: it is get-or-seed and skips every entry that already exists, so on a
        built workspace it is a no-op. Starting over needed its own operation, and until it had one
        "rebuild this from scratch" was only a sentence handed to the build agent — which built the
        most literal thing those words describe, a page about rebuilding.

        What survives is what the user set up rather than what a build produced: `.sage/` (history,
        settings, the attachment and Binding manifests), `public/data/` (the files they attached), the
        project's own `.git`, and the warm `node_modules` link. What goes is the app: every other
        top-level entry, re-seeded from the template.

        `.sage/queries.json` goes with it — it is the app's SQL, written by the agent, and an app that
        no longer exists has no queries. So do `plan.md` and `architecture.md`: both describe the app
        that was just removed, and AGENTS.md tells the agent plan.md is the LIVE plan, so leaving one
        behind would point the next turn at a design for code that is gone.

        AGENTS.md is re-seeded like any other template file, so the caller is responsible for
        splicing the user's project instructions back into it (see Orchestrator.reset_app)."""
        keep = _RESET_KEEP | {p.parts[0] for p in _RESET_KEEP_NESTED}
        for item in self._dir.iterdir():
            if item.name in keep:
                continue
            if item.is_dir() and not item.is_symlink():
                shutil.rmtree(item, ignore_errors=True)
            else:
                item.unlink(missing_ok=True)
        # A kept top-level dir is emptied of everything but the nested path that earned it its place:
        # `public/` is template content, `public/data/` is the user's attachments living inside it.
        for nested in _RESET_KEEP_NESTED:
            root = self._dir / nested.parts[0]
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
            (self._dir / rel).unlink(missing_ok=True)
        for rel in _RESET_CLEAR_DIRS:
            shutil.rmtree(self._dir / rel, ignore_errors=True)
        for item in self._template.iterdir():
            if item.name in _SEED_SKIP:
                continue
            dest = self._dir / item.name
            if item.is_dir():
                # dirs_exist_ok: `public/` is still standing because it holds the attachments.
                shutil.copytree(item, dest, ignore=_IGNORE, dirs_exist_ok=True)
            else:
                shutil.copy2(item, dest)
        self.link_warm_deps()

    def ensure(self, project_id: str, seed_app: bool = True) -> Workspace:
        """Get-or-seed the bound workspace. Idempotent: seeds the template in place only when the
        volume has no app yet (no package.json), never clobbering a pre-existing app or its .git.

        `seed_app=False` is Chat: mkdir the volume, leave the React template uncopied.
        """
        self._dir.mkdir(parents=True, exist_ok=True)
        if seed_app and not (self._dir / "package.json").exists():
            # Seed the template INTO the (possibly pre-existing, e.g. a fresh git checkout)
            # directory entry by entry, so an existing .git / dotfiles are preserved.
            for item in self._template.iterdir():
                if item.name in _SEED_SKIP:
                    continue
                dest = self._dir / item.name
                if dest.exists():
                    continue
                if item.is_dir():
                    shutil.copytree(item, dest, ignore=_IGNORE)
                else:
                    shutil.copy2(item, dest)

        if seed_app:
            self.link_warm_deps()
        return Workspace(project_id, self._dir)

    def project_record(self, project_id: str) -> ProjectRecord:
        """The Project's own record over the same volume, alongside the Built App `ensure` returns.

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
        node_modules = self._dir / "node_modules"

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
            dst = self._dir / name
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
        dst = self._dir / rel
        if not src.is_file():
            return False
        if dst.is_file() and (not refresh or dst.read_bytes() == src.read_bytes()):
            return False
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        return True
