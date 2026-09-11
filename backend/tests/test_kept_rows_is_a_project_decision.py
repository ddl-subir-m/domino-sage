"""**Kept rows**: the Project's standing answer to whether real data rows may be committed.

Off until somebody turns it on, and turned on beside the name of the git remote they would be
pushed to, because that name is the only part of the audience Sage can read
([ADR-0045](../../docs/adr/0045-an-artifact-commits-the-shape-and-the-rows-only-by-consent.md)).

Three layers, one claim each. `workspace.git` names the destination and refuses to name one it
cannot vouch for. `ProjectRecord` keeps the answer in the committed settings file, so the next
Builder in this Project reads the answer this one gave. The routes carry no project id — the server
already knows which Project it is.

Nothing here reaches a network: the remotes are throwaway repositories on disk, and the route tests
run the real orchestrator on a temp workspace.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from sage.workspace import git

from .test_chat_turn import _orch, _track_saves

# ---- the destination, written the way a person can weigh it ------------------------------------


def test_a_remote_reads_as_a_host_and_a_path():
    """The whole point of the sentence: `github.com/acme/analytics` is a thing a creator can weigh.
    A scheme and a `.git` are not part of that judgement, so they come out."""
    assert git.destination_name("https://github.com/acme/analytics.git") == "github.com/acme/analytics"


def test_a_credential_in_the_remote_never_reaches_the_screen():
    """Domino's helper can leave a token in the URL, and this string goes in front of a person and
    into a screenshot. Stripped server-side rather than in the browser, so there is one place it
    can be got wrong."""
    said = git.destination_name("https://sage:ghp_deadbeefdeadbeef@github.com/acme/analytics.git")
    assert said == "github.com/acme/analytics"
    assert "ghp_" not in said and "sage" not in said


def test_an_ssh_remote_names_the_same_place_as_the_https_one():
    """Two spellings of one destination. A creator who set the remote up over SSH is being asked
    about the same audience as one who set it up over HTTPS, and must read the same name."""
    assert git.destination_name("git@github.com:acme/analytics.git") == "github.com/acme/analytics"
    assert git.destination_name("ssh://git@gitlab.example.com:2222/acme/analytics.git") == (
        "gitlab.example.com/acme/analytics"
    )


def test_a_remote_on_disk_is_named_as_the_path_it_is():
    """No host to name, so none is invented. The path is the honest whole of the answer."""
    assert git.destination_name("/srv/git/analytics.git") == "/srv/git/analytics"


def test_no_remote_names_nothing_rather_than_something_vague():
    assert git.destination_name("") == ""


# ---- reading it off a real repository -----------------------------------------------------------


def _repo(path: Path, remote: str = "") -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=path, check=True)
    if remote:
        subprocess.run(["git", "remote", "add", "origin", remote], cwd=path, check=True)
    return path


def test_the_push_url_is_read_off_the_repo_rather_than_guessed(tmp_path: Path):
    _repo(tmp_path / "project", "https://github.com/acme/analytics.git")
    assert git.push_url(tmp_path / "project") == "https://github.com/acme/analytics.git"


def test_a_repo_with_no_remote_answers_nothing(tmp_path: Path):
    _repo(tmp_path / "project")
    assert git.push_url(tmp_path / "project") == ""


def test_a_directory_inside_someone_elses_repo_does_not_borrow_its_remote(tmp_path: Path):
    """The local-dev lie, and the reason `is_repo_root` exists (#20). The workspace sits inside
    Sage's own source tree there, so a bare `remote get-url` answers with SAGE's GitHub repo — and
    the dialog would name a destination this Project has never pushed to in its life."""
    root = _repo(tmp_path / "sage-source", "https://github.com/dominodatalab/sage.git")
    inside = root / "backend" / "workspaces" / "app"
    inside.mkdir(parents=True)
    assert git.push_url(inside) == ""


def test_a_directory_that_is_no_repo_at_all_answers_nothing(tmp_path: Path):
    (tmp_path / "plain").mkdir()
    assert git.push_url(tmp_path / "plain") == ""


# ---- the answer, kept where the next Builder reads it -------------------------------------------


def test_a_project_that_was_never_asked_is_off(tmp_path: Path):
    """Off by default because the default has to be the safe one when the destination is unknown,
    and it is unknown until somebody looks."""
    orch, _oc = _orch(tmp_path)
    assert orch.kept_rows()["on"] is False


def test_the_answer_given_is_the_answer_read_back(tmp_path: Path):
    """Re-opening the modal shows what this Project decided, not the default."""
    orch, _oc = _orch(tmp_path)
    orch.set_kept_rows(True)
    assert orch.kept_rows()["on"] is True
    orch.set_kept_rows(False)
    assert orch.kept_rows()["on"] is False


def test_the_answer_lives_in_the_committed_settings_file(tmp_path: Path):
    """`.sage/settings.json` is in the initial commit, so a decision written here travels to every
    other Builder in this Project rather than dying with this container."""
    orch, _oc = _orch(tmp_path)
    orch.set_kept_rows(True)
    settings = json.loads(
        (orch.project(start_preview=False).record.path / ".sage" / "settings.json").read_text()
    )
    assert settings["keptRows"] is True


def test_the_decision_is_committed_rather_than_left_in_the_working_tree(tmp_path: Path):
    """The same loss `test_a_rail_change_reaches_git` records: a file written and not saved only
    reaches git when some unrelated act happens to save afterwards. A creator who answers this and
    then deletes the Workspace would find the next one asking again."""
    orch, _oc = _orch(tmp_path)
    orch._cancel_chat_idle_save()
    orch._chat_dirty = False
    calls = _track_saves(orch)

    orch.set_kept_rows(True)

    assert calls == ["chat (kept rows)"]


def test_the_dialog_is_told_where_the_rows_would_go(tmp_path: Path):
    """The toggle is unweighable without it, which is why reading the remote is part of this ticket
    rather than a follow-on."""
    orch, _oc = _orch(tmp_path)
    path = orch.project(start_preview=False).record.path
    _repo(path, "https://github.com/acme/analytics.git")
    assert orch.kept_rows()["destination"] == "github.com/acme/analytics"


def test_a_project_whose_destination_cannot_be_read_says_so_by_naming_none(tmp_path: Path):
    """An empty destination is a fact the dialog renders as a sentence of its own. Naming a
    plausible-looking host here would be worse than naming nothing."""
    orch, _oc = _orch(tmp_path)
    assert orch.kept_rows()["destination"] == ""


# ---- the route the dialog reads and writes ------------------------------------------------------


def test_the_route_carries_no_project_id(tmp_path: Path, monkeypatch):
    """The server already knows which Project it is, the same as every collaborator route."""
    from fastapi.testclient import TestClient

    import sage.orchestrator.app as appmod

    orch, _oc = _orch(tmp_path)
    monkeypatch.setattr(appmod, "orchestrator", orch)
    client = TestClient(appmod.control_app)

    assert client.get("/api/project/kept-rows").json() == {"on": False, "destination": ""}

    written = client.post("/api/project/kept-rows", json={"on": True}).json()
    assert written["on"] is True
    assert client.get("/api/project/kept-rows").json()["on"] is True
