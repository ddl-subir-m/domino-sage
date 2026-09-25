"""The preview picks its server by the record, and then by what is on disk (#554).

`stack.py` refuses to work the stack out from files, and its reason is sound: that answer decides
which template `ensure` SEEDS, and a wrong guess re-seeds over a real app. But the same function
also answered a second, harmless question — which server to spawn — where the record's fallback is
wrong. `LEGACY_STACK` is `react-vite` because every app born before the record existed is one.
An app that LOST its record is not that app, and measured live 2026-09-24 one got `npm run dev` run
on Python: `ENOENT ... package.json`, exit 254, "max restarts reached", a dead pane that read like
a broken build rather than a missing record.

So `preview_stack_of` is a separate reader for the harmless question. It writes nothing, it keeps
the record as the authority wherever there is one, and where there is no app at all it says so
instead of guessing — which is the case that made an empty directory look broken.
"""
import json

import pytest

from sage.preview.supervisor import UvicornSupervisor, ViteSupervisor, make_supervisor
from sage.workspace.stack import FASTAPI_ANTD, REACT_VITE, preview_stack_of


def _record(app, stack_name):
    (app / ".sage").mkdir(parents=True, exist_ok=True)
    (app / ".sage" / "settings.json").write_text(json.dumps({"stack": stack_name}))


def test_the_record_still_decides_whenever_there_is_one(tmp_path):
    """Nothing about a recorded app changes — including one whose disk disagrees."""
    _record(tmp_path, REACT_VITE.name)
    (tmp_path / "app.py").write_text("")          # disk says Python, record says react-vite
    assert preview_stack_of(tmp_path) is REACT_VITE


def test_a_pre_record_react_vite_app_still_answers_react_vite(tmp_path):
    """The population `LEGACY_STACK` was written for. It has the package.json this looks for, so
    reading the disk reaches the same answer the old fallback did — without guessing for others."""
    (tmp_path / "package.json").write_text("{}")
    assert preview_stack_of(tmp_path) is REACT_VITE


def test_a_python_app_that_lost_its_record_is_not_served_by_vite(tmp_path):
    """The measured failure. Before this, no record meant react-vite meant `npm run dev`."""
    (tmp_path / "app.py").write_text("")
    assert preview_stack_of(tmp_path) is FASTAPI_ANTD


def test_an_unreadable_record_falls_through_to_the_disk_not_to_the_legacy_stack(tmp_path):
    """A stray comma must not decide that a Python app is served by Vite."""
    (tmp_path / ".sage").mkdir()
    (tmp_path / ".sage" / "settings.json").write_text("{ not json,,, }")
    (tmp_path / "app.py").write_text("")
    assert preview_stack_of(tmp_path) is FASTAPI_ANTD


def test_a_record_naming_a_stack_nobody_ships_falls_through_rather_than_crashing(tmp_path):
    _record(tmp_path, "svelte-whatever")
    (tmp_path / "app.py").write_text("")
    assert preview_stack_of(tmp_path) is FASTAPI_ANTD


def test_an_app_directory_with_nothing_in_it_has_no_server(tmp_path):
    """Not a stack choice — there is no app. The caller must spawn nothing."""
    assert preview_stack_of(tmp_path) is None


def test_the_supervisor_follows_that_answer(tmp_path):
    py, js = tmp_path / "py", tmp_path / "js"
    py.mkdir(); js.mkdir()
    (py / "app.py").write_text("")
    (js / "package.json").write_text("{}")
    assert isinstance(make_supervisor(py, ""), UvicornSupervisor)
    assert isinstance(make_supervisor(js, ""), ViteSupervisor)


def test_nothing_is_spawned_for_an_app_that_was_never_built(tmp_path, monkeypatch):
    """`npm run dev` on an empty directory is what produced 'max restarts reached'."""
    spawned = []
    monkeypatch.setattr(ViteSupervisor, "_spawn", lambda self: spawned.append(1))
    sup = make_supervisor(tmp_path, "")
    with pytest.raises(RuntimeError) as e:
        sup.start(ready_timeout_s=0.1)
    assert spawned == [], "a server was started for an app that does not exist"
    assert "no app has been built" in str(e.value)
    assert "no app has been built" in (sup.last_error() or ""), "the pane cannot say why"
