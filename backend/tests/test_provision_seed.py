"""seed_and_push auth wiring: the token rides the child git env via a one-shot credential helper,
never argv. The temp seed repo inherits no ambient helper, so this is the only auth path."""
import subprocess
import types

from sage.provision import seed


def _template(tmp_path):
    t = tmp_path / "template"
    t.mkdir()
    (t / "index.html").write_text("<h1>hi</h1>")
    return t


def _record_runs(monkeypatch):
    calls = []

    def fake_run(args, **kw):
        calls.append((args, kw))
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    return calls


def _push_call(calls):
    return next(c for c in calls if "push" in c[0])


def test_push_injects_token_via_env_not_argv(tmp_path, monkeypatch):
    calls = _record_runs(monkeypatch)
    secret = "ghp_TESTTOKEN"

    seed.seed_and_push("https://github.com/o/r.git", _template(tmp_path), token_provider=lambda: secret)

    argv, kw = _push_call(calls)
    # Our one-shot helper is configured, and the empty helper clears inherited ones first.
    assert "credential.helper=" in argv
    assert any(seed._ONESHOT_HELPER in a for a in argv)
    # Token is passed only through the child env, never on the command line.
    assert secret not in " ".join(argv)
    assert kw["env"][seed._PUSH_TOKEN_ENV] == secret


def test_push_without_token_uses_ambient_env(tmp_path, monkeypatch):
    calls = _record_runs(monkeypatch)

    seed.seed_and_push("https://github.com/o/r.git", _template(tmp_path))

    argv, kw = _push_call(calls)
    assert "credential.helper=" not in argv
    assert kw["env"] is None  # inherit the parent env unchanged


def test_git_failure_surfaces_stderr_not_token(tmp_path, monkeypatch):
    def fail_run(args, **kw):
        rc = 128 if "push" in args else 0
        return types.SimpleNamespace(returncode=rc, stdout="", stderr="fatal: repository not found")

    monkeypatch.setattr(subprocess, "run", fail_run)

    try:
        seed.seed_and_push("https://github.com/o/r.git", _template(tmp_path), token_provider=lambda: "ghp_SECRET")
    except RuntimeError as e:
        assert "repository not found" in str(e)
        assert "ghp_SECRET" not in str(e)
    else:
        raise AssertionError("expected RuntimeError on push failure")


# --- dest (registry.create, Phase 3 step 1) -------------------------------------------------


def test_seed_and_push_with_dest_seeds_in_place_not_a_tempdir(tmp_path):
    template = _template(tmp_path)
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    dest = tmp_path / "projects" / "sage-alpha"

    seed.seed_and_push(str(bare), template, dest=dest)

    assert (dest / "index.html").exists()
    assert (dest / ".git").is_dir()


# --- clone (registry.clone, Phase 3 step 2) -------------------------------------------------


def test_clone_injects_token_via_env_not_argv(tmp_path, monkeypatch):
    calls = _record_runs(monkeypatch)
    secret = "ghp_TESTTOKEN"

    seed.clone("https://github.com/o/r.git", tmp_path / "dest", token_provider=lambda: secret)

    argv, kw = next(c for c in calls if "clone" in c[0])
    assert "credential.helper=" in argv
    assert any(seed._ONESHOT_HELPER in a for a in argv)
    assert secret not in " ".join(argv)
    assert kw["env"][seed._PUSH_TOKEN_ENV] == secret
    assert str(tmp_path / "dest") in argv


def test_clone_without_token_uses_ambient_env(tmp_path, monkeypatch):
    calls = _record_runs(monkeypatch)

    seed.clone("https://github.com/o/r.git", tmp_path / "dest")

    argv, kw = next(c for c in calls if "clone" in c[0])
    assert "credential.helper=" not in argv
    assert kw["env"] is None


def test_clone_failure_surfaces_stderr_not_token(monkeypatch, tmp_path):
    def fail_run(args, **kw):
        rc = 128 if "clone" in args else 0
        return types.SimpleNamespace(returncode=rc, stdout="", stderr="fatal: repository not found")

    monkeypatch.setattr(subprocess, "run", fail_run)

    try:
        seed.clone("https://github.com/o/r.git", tmp_path / "dest", token_provider=lambda: "ghp_SECRET")
    except RuntimeError as e:
        assert "repository not found" in str(e)
        assert "ghp_SECRET" not in str(e)
    else:
        raise AssertionError("expected RuntimeError on clone failure")
