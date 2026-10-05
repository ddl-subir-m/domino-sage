"""The resources panel's Secrets and MCP servers groups (#643), against the #641 and #642 contracts.

A secret's value goes out once, in the PUT that sets it, and nothing on screen or in the store
holds it afterwards. An MCP server's header names a secret as `{env:NAME}`. Drawn through
`tests/js/project_secrets_mcp_harness.mjs`, which records every request the panel sends, so each
act is proved by what reached the server.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "js" / "project_secrets_mcp_harness.mjs"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not on PATH (it is in the Sage image)")

SECRETS = {"available": True, "reason": None, "secrets": [
    {"name": "CRM_TOKEN", "note": "CRM API token"},
    {"name": "OPENAI_API_KEY", "note": ""},
]}
UNAVAILABLE = {"available": False, "reason": "Secrets are kept by Domino, and this is not running "
               "in a Domino Project.", "secrets": []}
SERVERS = [
    {"name": "crm", "kind": "remote", "url": "https://crm.example.com/mcp",
     "headers": {"Authorization": "Bearer {env:CRM_TOKEN}"}, "enabled": True,
     "tools": ["search", "create_lead"], "status": "connected", "warning": None},
    {"name": "docs", "kind": "domino", "url": "https://apps.domino.example.com/docs/mcp",
     "headers": {}, "enabled": False, "tools": [], "status": "failed",
     "warning": "the secret DOCS_KEY is not set"},
]
PLANTED = "sk-planted-7f3a9c1e5b"


def _run(act: str, **kw) -> dict:
    payload = {"act": act, "secrets": SECRETS, "servers": SERVERS, **kw}
    out = subprocess.run(["node", str(_HARNESS)], input=json.dumps(payload), capture_output=True,
                         text=True, timeout=60, check=False)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def _writes(calls: list) -> list:
    return [c for c in calls if c["method"] != "GET"]


def test_each_secret_is_a_row_with_its_note_and_the_group_has_no_caption():
    drawn = _run("drawn")
    assert [(r["name"], r["subtitle"]) for r in drawn["secrets"]] == [
        ("CRM_TOKEN", "CRM API token"), ("OPENAI_API_KEY", "No note")]
    heads = {h["label"]: h for h in drawn["heads"]}
    assert heads["Secrets (2)"]["hasAdd"] is True
    assert not any("kept by" in c["text"] for c in drawn["captions"])
    assert "secret" in drawn["menuKeys"] and "mcp" in drawn["menuKeys"]


def test_adding_a_secret_sends_its_value_once_and_nothing_holds_it_afterwards():
    out = _run("add-secret", value=PLANTED)
    assert out["title"] == "Add secret"
    assert out["valueField"] == {"password": True, "toggle": False}
    [put] = _writes(out["calls"])
    assert put == {"url": "./api/project/secrets/OPENAI_API_KEY", "method": "PUT",
                   "body": {"value": PLANTED, "note": "OpenAI key; use with api.openai.com"}}
    # And the list is read again, so the row comes from the server rather than from the dialog.
    assert out["calls"][-1] == {"url": "./api/project/secrets", "method": "GET", "body": None}
    assert out["dialogOpen"] is False
    assert out["leftovers"] == {"state": False, "tree": False, "toasts": False, "hooks": False}


def test_replace_value_starts_empty_and_sends_only_the_value():
    out = _run("replace-secret", value=PLANTED)
    assert out["title"] == "Replace the value of CRM_TOKEN"
    assert out["hasName"] is False and out["prefilled"] == ""
    [put] = _writes(out["calls"])
    assert put == {"url": "./api/project/secrets/CRM_TOKEN", "method": "PUT",
                   "body": {"value": PLANTED}}
    assert out["leftovers"] == {"state": False, "tree": False, "toasts": False, "hooks": False}


def test_edit_note_starts_from_the_note_and_sends_only_the_note():
    out = _run("note-secret")
    assert out["prefilled"] == "CRM API token"
    [put] = _writes(out["calls"])
    assert put == {"url": "./api/project/secrets/CRM_TOKEN", "method": "PUT",
                   "body": {"note": "Rotated monthly."}}


def test_remove_asks_first_then_deletes_the_secret():
    out = _run("remove-secret")
    assert out["confirmTitle"] == "Remove CRM_TOKEN?"
    assert _writes(out["calls"]) == [{"url": "./api/project/secrets/CRM_TOKEN", "method": "DELETE",
                                      "body": None}]


def test_where_secrets_cannot_be_kept_the_group_says_why_and_offers_no_way_in():
    drawn = _run("drawn", secrets=UNAVAILABLE)
    heads = {h["label"]: h for h in drawn["heads"]}
    assert heads["Secrets (0)"]["hasAdd"] is False
    assert {"cls": "sw-group-note", "text": UNAVAILABLE["reason"]} in drawn["captions"]
    assert "secret" not in drawn["menuKeys"]


def test_each_mcp_server_shows_its_status_tools_and_switch():
    drawn = _run("drawn")
    assert drawn["servers"] == [
        {"name": "crm", "subtitle": "Remote · Connected · 2 tools", "checked": True},
        {"name": "docs",
         "subtitle": "Domino-hosted · Failed: the secret DOCS_KEY is not set · 0 tools",
         "checked": False},
    ]
    heads = {h["label"]: h for h in drawn["heads"]}
    assert heads["MCP servers (2)"]["hasAdd"] is True
    assert {"cls": "sw-group-caption", "text": "Temporary until the MCP gateway."} in drawn["captions"]


def test_the_switch_sends_put_enabled_for_the_project():
    out = _run("toggle-mcp")
    assert _writes(out["calls"]) == [{"url": "./api/project/mcp/crm/enabled", "method": "PUT",
                                      "body": {"enabled": False}}]
    assert out["calls"][-1]["url"] == "./api/project/mcp"


def test_read_its_tools_again_reaches_its_route():
    out = _run("reread-mcp")
    assert "reread" in out["menuKeys"] and "remove" in out["menuKeys"]
    assert _writes(out["calls"]) == [{"url": "./api/project/mcp/crm/tools", "method": "POST",
                                      "body": None}]


def test_remove_asks_first_then_deletes_the_server():
    out = _run("remove-mcp")
    assert out["confirmTitle"] == "Remove crm?"
    assert _writes(out["calls"]) == [{"url": "./api/project/mcp/crm", "method": "DELETE",
                                      "body": None}]


def test_adding_a_server_with_a_secret_header_writes_its_name_not_its_value():
    out = _run("add-mcp")
    assert out["title"] == "Add MCP server"
    assert out["secretOptions"] == ["CRM_TOKEN", "OPENAI_API_KEY"]
    assert out["headerValue"] == "Bearer {env:CRM_TOKEN}"
    [post] = _writes(out["calls"])
    assert post == {"url": "./api/project/mcp", "method": "POST",
                    "body": {"name": "crm", "url": "https://crm.example.com/mcp",
                             "headers": {"Authorization": "Bearer {env:CRM_TOKEN}"}}}


def test_a_domino_hosted_server_hides_authorization_and_says_sage_signs_in():
    out = _run("add-domino-mcp")
    assert out["kinds"] == ["Remote", "Domino-hosted"]
    assert out["before"] == {"kind": "remote", "note": False}
    assert out["after"]["kind"] == "domino" and out["after"]["headerNames"] == []
    assert "signs in to it as you" in out["after"]["note"]
    [post] = _writes(out["calls"])
    assert post == {"url": "./api/project/mcp", "method": "POST",
                    "body": {"name": "crm", "url": "https://apps.domino.example.com/crm/mcp",
                             "headers": {"X-Region": "eu"}, "kind": "domino"}}
