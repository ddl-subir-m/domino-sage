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
