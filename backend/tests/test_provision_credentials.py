import subprocess
import types

from sage.provision import credentials


def test_parse_remote_https():
    r = credentials.parse_remote("https://github.com/ddl-subir-m/sage-foo.git")
    assert r is not None
    assert r.provider == "github"
    assert r.host == "github.com"
    assert r.owner == "ddl-subir-m"
    assert r.protocol == "https"


def test_parse_remote_scp_like():
    r = credentials.parse_remote("git@github.com:owner/repo.git")
    assert r is not None
    assert r.host == "github.com"
    assert r.owner == "owner"
    assert r.protocol == "ssh"


def test_parse_remote_unparseable():
    assert credentials.parse_remote("not a url") is None


def test_detect_provider():
    assert credentials.detect_provider("github.com") == "github"
    assert credentials.detect_provider("gitlab.com") == "gitlab"
    assert credentials.detect_provider("git.acme-github.internal") == "github-enterprise"
    assert credentials.detect_provider("scm.example.com") == "unknown"


def test_extract_token_reads_password(monkeypatch):
    def fake_run(cmd, **kw):
        assert cmd == ["git", "credential", "fill"]
        assert "host=github.com" in kw["input"]
        return types.SimpleNamespace(returncode=0, stdout="protocol=https\nhost=github.com\nusername=x\npassword=ghp_secret\n")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert credentials.extract_token("github.com") == "ghp_secret"


def test_extract_token_none_when_helper_fails(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: types.SimpleNamespace(returncode=1, stdout=""))
    assert credentials.extract_token("github.com") is None


def _run_stub(*, origin=None, fill=None):
    """A subprocess.run stand-in for the two git calls extraction makes: `remote get-url origin`
    and `credential fill`. `fill(cwd, query)` returns stdout or None for "no credential"."""
    calls = []

    def fake_run(cmd, **kw):
        calls.append((tuple(cmd), kw.get("cwd"), kw.get("input")))
        if cmd[:2] == ["git", "remote"]:
            if origin is None:
                return types.SimpleNamespace(returncode=1, stdout="")
            return types.SimpleNamespace(returncode=0, stdout=origin + "\n")
        out = fill(kw.get("cwd"), kw.get("input")) if fill else None
        if out is None:
            return types.SimpleNamespace(returncode=1, stdout="")
        return types.SimpleNamespace(returncode=0, stdout=out)

    return fake_run, calls


def test_extract_token_asks_from_the_mounted_checkout_not_the_process_cwd(tmp_path, monkeypatch):
    """The orchestrator's cwd is Sage's baked code, not the user's repo. Domino authorizes a
    credential per repository, so the answer that matters comes from inside the checkout."""
    monkeypatch.setenv("SAGE_WORKSPACE_DIR", str(tmp_path))

    def fill(cwd, query):
        if cwd != str(tmp_path):
            return None
        return "protocol=https\nhost=github.com\npassword=ghp_from_checkout\n"

    fake_run, calls = _run_stub(origin="https://github.com/owner/repo.git", fill=fill)
    monkeypatch.setattr(subprocess, "run", fake_run)

    assert credentials.extract_token("github.com") == "ghp_from_checkout"
    # The first ask carries the repo path, so a path-scoped credential config can match it.
    fills = [c for c in calls if c[0][:2] == ("git", "credential")]
    assert "path=owner/repo\n" in fills[0][2]


def test_extract_token_reads_a_credential_domino_embedded_in_the_origin_url(tmp_path, monkeypatch):
    """`git credential fill` cannot see this one — an URL-embedded credential is part of the remote,
    not a helper — so a container wired this way pushes fine and still answers "no credential"."""
    monkeypatch.setenv("SAGE_WORKSPACE_DIR", str(tmp_path))
    fake_run, _ = _run_stub(origin="https://x-access-token:ghp_in_url@github.com/owner/repo.git")
    monkeypatch.setattr(subprocess, "run", fake_run)

    assert credentials.extract_token("github.com") == "ghp_in_url"


def test_extract_token_ignores_an_origin_credential_for_another_host(tmp_path, monkeypatch):
    monkeypatch.setenv("SAGE_WORKSPACE_DIR", str(tmp_path))
    fake_run, _ = _run_stub(origin="https://user:glpat_x@gitlab.com/owner/repo.git")
    monkeypatch.setattr(subprocess, "run", fake_run)

    assert credentials.extract_token("github.com") is None


def test_the_credential_probe_reports_lengths_and_never_the_secret(tmp_path, monkeypatch):
    """A Builder has no terminal, so /api/diag is the only place the credential question can be
    asked twice — which it can only be if the answer carries no live token."""
    monkeypatch.setenv("SAGE_WORKSPACE_DIR", str(tmp_path))
    fake_run, _ = _run_stub(
        origin="https://github.com/owner/repo.git",
        fill=lambda cwd, q: "password=ghp_secret\n" if cwd == str(tmp_path) else None,
    )
    monkeypatch.setattr(subprocess, "run", fake_run)

    report = credentials.credential_probe("github.com")

    assert report["found"] is True
    assert "ghp_secret" not in str(report)
    mounted = next(a for a in report["asked"] if a["cwd"] == str(tmp_path))
    assert mounted["fill_len"] == len("ghp_secret")
