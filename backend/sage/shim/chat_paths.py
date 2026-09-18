"""Which paths a sage-chat turn may write. Pure: no I/O, no OpenCode.

The shim and the orchestrator both call `chat_path_allowed`. OpenCode's own permission
config does not enforce this (see phase_classifier.READ_ONLY_DENIED); a denied write must
be rejected in the tool result (so the model retries the Artifact path) and reverted on
disk if it still landed. Silent-dropping the tool_call made the model retry `src/` forever.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from ..router.phase_classifier import READ_TOOLS, WRITE_TOOLS

# The phrase an @mention's descriptor is introduced by, in both of `with_attachment_listing`'s
# preambles (`driver/opencode.py`). Once a prompt has been flattened into a gateway payload this
# sentence is the only thing left that tells an inlined descriptor apart from the person's prose,
# and `withhold._carries_mention` reads it to count those rows as DATA (#290).
#
# It lives HERE, beside `content_text`, rather than in the driver that writes it. The intuitive
# home is the producer, and it is the wrong one: `withhold.py` advertises talking to nothing, and
# importing a 21-character string from `driver/opencode.py` pulls the whole driver graph and httpx
# in behind it — MEASURED 45 ms / 238 modules against 12 ms / 90 for this file. A fact about the
# TEXT SHAPE of a payload is what this module is for. One definition, both sides read it, and
# neither can drift from the other.
MENTION_MARK = "user @mentioned these"

# The line every descriptor entry carries, holding the path of the file it describes. Read WITH
# `MENTION_MARK`, never instead of it and never alone: the phrase is ordinary English and a person
# discussing Sage's own prompt types it without attaching anything, while `path:` on its own is
# more typeable still. A message has to carry both to be counted as data, and the pair is not
# something anyone produces by accident. Getting this wrong is not neutral — a message wrongly
# marked takes its whole turn off the "fetched nothing" fallback and suppresses a re-run that was
# worth the call, which is a new wrong answer in the opposite direction from the one #290 fixes.
MENTION_PATH_LINE = "\n  path: "

# Thread ids we mint (`thr_` + hex) plus anything already on disk. A path using another id
# is not "this Thread" even if it sits under examples/.
_THREAD_ID = re.compile(r"^thr_[a-zA-Z0-9_-]+$")


def normalize_write_path(path: str) -> str:
    """Workspace-relative posix path, no leading slash. Unknown prefixes (`/mnt/code/`,
    `/workspaces/`) are stripped so an absolute tool arg still classifies."""
    rel = (path or "").replace("\\", "/").strip()
    if not rel:
        return ""
    for prefix in ("/mnt/code/", "/workspaces/",):
        if prefix in rel:
            rel = rel.split(prefix, 1)[-1]
    return rel.lstrip("/")


def chat_path_allowed(path: str, thread_id: str) -> bool:
    """True only for files under this Thread's Artifact dir, its `.sage/threads/` dir, or its
    scratch dir.

    The scratch prefix is the one a turn's working files go to, and picking it is not obvious,
    so the three rejected candidates are worth more than the accepted one:

    `/tmp` is what the prompts named until #415, and it was never reachable. This function has
    always refused it, so the write tool a read-only turn is left with has always said no — the
    failure was silent, and only #407's `external_directory: "deny"` made it loud. Do not fix
    that by allowing `/tmp`: OpenCode's own server log is written to
    `tempfile.gettempdir() / "sage-opencode.log"` (`orchestrator/service.py`), which on a Domino
    container is `/tmp/sage-opencode.log` — gateway traffic, tool calls, internals. Allowing any
    part of `/tmp` hands the Chat agent that file through a tool the person cannot see it use.
    A carve-out narrow enough to exclude it cannot be written either: the rest of the filenames
    come from the person's Dataset, so the pattern would have to be wide enough to be useless.

    `.sage/threads/<threadId>/` is already permitted above and so looks like the free answer.
    It is not gitignored — deliberately, it holds Chat history that is committed — so scratch
    `.py` written there lands in the person's repository, which is the outcome the prompt line
    naming `/tmp` existed to prevent.

    `.sage/scratch/` FLAT is where the landing note on #415 pointed, and it is one level too
    wide: uploaded attachments already live there (`.sage/scratch/<name>.csv`,
    `.sage/scratch/datasets/…`, `.sage/scratch/uploads/…`). A flat allow lets one Thread's
    scratch write overwrite a file the person uploaded and can see in the rail.

    So: `.sage/scratch/<threadId>/`. Gitignored by `.gitignore`'s `.sage/scratch/`, inside the
    project so `external_directory` never applies (OpenCode re-adds a project-glob allow over
    any config, MEASURED against opencode-ai 1.18.4 — see #415), scoped like the other two
    prefixes so it cannot reach another Thread or the uploads beside it, and reachable from the
    Chat cwd because `ensure_chat_workdir` already links `.sage/scratch` in.
    """
    if not _THREAD_ID.match(thread_id or ""):
        return False
    rel = normalize_write_path(path)
    if not rel or rel.endswith("/"):
        return False
    examples = f"examples/{thread_id}/"
    meta = f".sage/threads/{thread_id}/"
    scratch = f".sage/scratch/{thread_id}/"
    return rel.startswith((examples, meta, scratch))


def tool_call_name_and_args(call: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """The tool name and arguments of a tool_call, in either shape we see.

    The OpenAI shape the shim reads, and the OpenCode `state.input` shape the orchestrator reads.
    Split out because the write side, the read side and the label side want the same parse and
    differ only in which tool names they accept.
    """
    name = ""
    args: Any = {}
    fn = call.get("function") if isinstance(call, dict) else None
    if isinstance(fn, dict):
        name = str(fn.get("name") or "")
        raw = fn.get("arguments")
        if isinstance(raw, dict):
            args = raw
        elif isinstance(raw, str) and raw.strip():
            try:
                args = json.loads(raw)
            except json.JSONDecodeError:
                args = {}
    elif isinstance(call, dict):
        name = str(call.get("tool") or call.get("name") or "")
        args = (call.get("state") or {}).get("input") or call.get("input") or {}
    return name.lower(), args if isinstance(args, dict) else {}


def _path_arg(args: dict[str, Any]) -> str | None:
    path = args.get("path") or args.get("filePath") or args.get("file_path") or ""
    return str(path) if path else None


def write_path_from_tool_call(call: dict[str, Any]) -> str | None:
    """The file a write/edit tool_call would touch, or None if this call is not a write."""
    name, args = tool_call_name_and_args(call)
    return _path_arg(args) if name in WRITE_TOOLS else None


def read_path_from_tool_call(call: dict[str, Any]) -> str | None:
    """The file a read tool_call opened, or None if this call is not a read.

    The read-side twin of `write_path_from_tool_call`, and the only reason it exists: a guardrail
    refusal has to be reported as a FILE the person recognises, and the path is nowhere else in the
    request — the tool result carries the contents and nothing else.

    A miss is cheap and safe. A read this does not recognise (a `bash cat`, a driver that renames
    the tool) still gets withheld; it is just named by its content fingerprint rather than by a
    filename, which is the fallback `apply_withheld` already needs for pasted text.
    """
    name, args = tool_call_name_and_args(call)
    return _path_arg(args) if name in READ_TOOLS else None


def denied_write_result(thread_id: str) -> str:
    """What the model sees when a Chat write missed the Artifact dir.

    Addressed to the model, not the user. Must name the path so the next call lands
    under examples/<threadId>/ instead of retrying src/.
    """
    return (
        f"Write rejected: this Chat turn cannot change the app. "
        f"Save a PNG chart at examples/{thread_id}/<slug>.png or a table at "
        f"examples/{thread_id}/<slug>.table.json. Never write under src/ "
        f"(including src/examples/)."
    )


def strip_denied_writes(messages: list[Any], thread_id: str) -> list[Any]:
    """Turn illegal Chat writes into tool errors so the model retries the Artifact path.

    Dropping the tool_call (the previous behaviour) hid the attempt, so the model
    called write on src/ again — dozens of times — and Chat filled with 'Ran write'.
    """
    if not isinstance(messages, list):
        return messages
    denied: dict[str, str] = {}
    for m in messages:
        if not isinstance(m, dict) or m.get("role") != "assistant":
            continue
        for call in m.get("tool_calls") or []:
            if not isinstance(call, dict):
                continue
            path = write_path_from_tool_call(call)
            if path and not chat_path_allowed(path, thread_id):
                cid = call.get("id")
                if cid:
                    denied[str(cid)] = path
    if not denied:
        return messages
    have_result = {
        str(m.get("tool_call_id"))
        for m in messages
        if isinstance(m, dict) and m.get("role") == "tool" and m.get("tool_call_id")
    }
    err = denied_write_result(thread_id)
    out: list[Any] = []
    for m in messages:
        if not isinstance(m, dict):
            out.append(m)
            continue
        if m.get("role") == "tool" and str(m.get("tool_call_id") or "") in denied:
            out.append({**m, "content": err})
            continue
        out.append(m)
        if m.get("role") == "assistant":
            for call in m.get("tool_calls") or []:
                if not isinstance(call, dict):
                    continue
                cid = str(call.get("id") or "")
                if cid in denied and cid not in have_result:
                    out.append({"role": "tool", "tool_call_id": cid, "content": err})
    return out


def file_key(path: str) -> str:
    """The withhold key for a file, stable across the prefixes a path can arrive with."""
    return "file:" + normalize_write_path(path)


def text_key(message: dict[str, Any]) -> str:
    """The withhold key for a message named by its CONTENT rather than by a file.

    A fingerprint, never the text. Pasted PII, an @-mention's inlined sample rows and a compaction
    summary all carry values a guardrail refuses, and none of them is a tool result with a path to
    name. Hashing means the transcript records which message to stop sending without ever writing
    down what was in it — which matters more here than anywhere else in Sage, because the thing
    being recorded is the thing a policy just refused to move.
    """
    return "text:" + hashlib.sha256(content_text(message.get("content")).encode()).hexdigest()[:16]


def content_text(content: Any) -> str:
    """A message's text, from either the plain-string or the content-parts shape.

    Public because two modules read it and ask different things of it: `text_key` fingerprints it,
    and `withhold._carries_mention` looks in it for the sentence an @mention appended (#290).
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(p["text"] for p in content
                         if isinstance(p, dict) and isinstance(p.get("text"), str))
    return ""


def withheld_result(label: str) -> str:
    """What the model gets in place of content this Conversation no longer sends.

    Addressed to the model, like `denied_write_result`, and for the same reason that one names the
    path: a placeholder that does not say what happened gets retried. This one has to stop the
    retry outright, because unlike a denied write there is no second path that would work.
    """
    return (f"[withheld: {label} is not being sent. The LLM gateway's policy refuses it, so this "
            f"conversation stopped sending it. Do not try to read it again — say you cannot see it "
            f"and answer from what is left.]")


def apply_withheld(messages: list[Any], keys: frozenset[str] | set[str]) -> list[Any]:
    """Replace withheld content in place, keeping every message where it is.

    NEVER drops a message. A `role:"tool"` message removed while its `tool_call` stays behind is an
    HTTP 400 on every provider measured (gpt-5.4, sonnet, haiku, gemini) — and replacing content is
    accepted by all four. That is also what makes a bisect sound: every probe is well-formed by
    construction, so `OK` means clean rather than "malformed in some other way".

    Matches on two keys at once. A tool result is matched through the file its `tool_call` opened;
    anything else is matched by content fingerprint. One filter, two matchers, so Chat and Build and
    files and pasted text are all the same code path.
    """
    if not keys or not isinstance(messages, list):
        return messages
    labels: dict[str, str] = {}
    for m in messages:
        if not isinstance(m, dict) or m.get("role") != "assistant":
            continue
        for call in m.get("tool_calls") or []:
            if not isinstance(call, dict):
                continue
            path = read_path_from_tool_call(call)
            cid = str(call.get("id") or "")
            if path and cid and file_key(path) in keys:
                labels[cid] = path
    out: list[Any] = []
    hit = False
    for m in messages:
        if not isinstance(m, dict):
            out.append(m)
            continue
        cid = str(m.get("tool_call_id") or "")
        if m.get("role") == "tool" and cid in labels:
            out.append({**m, "content": withheld_result(labels[cid])})
            hit = True
            continue
        if text_key(m) in keys:
            out.append({**m, "content": withheld_result("a message in this conversation")})
            hit = True
            continue
        out.append(m)
    return out if hit else messages
