#!/usr/bin/env python3
"""Run the demo script (#715) against the Sage this Domino workspace serves, and record it.

    python3 scripts/eval/demo.py --label base-1
    python3 scripts/eval/demo.py --label sonnet-impl --models implement=sonnet

Each run writes `runs/demo/<label>/record.json` and `summary.json`, and prints the summary.

Run it INSIDE the workspace, from a checkout of this repo whose root has `node_modules` (for the
probe's playwright-core) and with Chromium where the page check finds it. Unlike drive.py it boots
nothing: it talks to the workspace's own Sage on 127.0.0.1 (`SAGE_CONTROL_PORT`, default 8080,
under the Domino path prefix), which needs no auth there, and so uses that workspace's real Data
Source, Dataset, skills, secret and MCP servers. It never publishes: the client refuses the route.

THE RUN, in `demo.json`'s order, making the calls the Workbench makes. Set the Chat, plan and
implement models (`--models`, default haiku / sonnet at medium / haiku) and Auto mode; open a
Conversation; send Chat prompts 1-6, picking each @mention the way the @ menu does and answering a
Dataset file card the way the script does; send prompt 7, write the plan, confirm the handoff into
a new app, then edit the plan and approve it as the script does; send Build follow-ups 8, 9 and
9b, approving any plan they draft, and after 9 click Add them as the bar under it offers. After
every finished Build turn the probe (`probe.mjs`) opens the preview headless and clicks every
top-level tab. The run stops at the first failure it cannot step past.

THE BOUND. Every model-spending request this harness starts -- each Chat and Build turn and the
plan draft -- is appended to `runs/demo-ledger.jsonl` first. At `BOUND` it refuses to start another,
so the bound holds across runs.

WHAT IS KEPT, by drive.py's rules and through its filters: per turn, the prompt's digest, Sage's
own event vocabulary, Build diagnostics downloads, the checks and the probe's counts. Not a prompt,
not the plan, not a word of model prose. The probe process is killed and reaped on every path.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
from collections import Counter
from urllib.parse import quote

HERE = pathlib.Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import drive
from drive import BoundReached, kept, sha, summarise

DEMO = HERE / "demo.json"
PROBE = HERE / "probe.mjs"
RUNS = HERE / "runs" / "demo"
LEDGER = HERE / "runs" / "demo-ledger.jsonl"
# About 16 turns a run, so four full runs.
BOUND = 64
PROBE_TIMEOUT_S = 180
SETTLE_MS = 1500
DEFAULT_MODELS = "chat=haiku,plan=sonnet@medium,implement=haiku"
# What the handoff sheet sends when nobody has changed Account settings (`prefs.js`).
CROSSINGS = {"resources": True, "artifacts": True, "transcript": False}
# The groups the @ menu offers (`emptyResourceGroups`), and the ones a pick adds no chip for.
GROUPS = {"dataset", "table", "datasource", "model_llm", "model_predictive", "tool", "agent", "skill",
          "file"}
TEXT_ONLY = {"skill", "secret", "mcp", "folder"}
UI_KIND = {"data_source": "datasource", "llm_alias": "model_llm", "model_api": "model_predictive"}
POST_KIND = {"table": "data_source", "datasource": "data_source", "model_llm": "llm_alias",
             "model_predictive": "model_api"}
CARDS = {"dataset-files", "table-candidates", "reset-offer", "incoming-changes", "mentions-unresolved"}
FINDINGS = ("runtimeErrors", "crashScreen", "missingColumns", "invisibleCharts",
            "blankFirstColumnTables", "clickFailed")


def load_script(path: pathlib.Path = DEMO) -> dict:
    doc = json.loads(path.read_text())
    for item in [*doc["chat"], doc["handoff"], *doc["build"]]:
        if sha(item["prompt"]) != item["sha256"]:
            raise ValueError(f"prompt {item['id']} does not match its recorded sha256")
    return doc


def parse_models(spec: str) -> dict:
    """`chat=haiku,plan=sonnet@medium,...` over the defaults. No effort is the slot's default."""
    out: dict = {}
    for source in (DEFAULT_MODELS, spec):
        for part in filter(None, (p.strip() for p in source.split(","))):
            slot, _, value = part.partition("=")
            model, _, effort = value.partition("@")
            if slot not in ("chat", "plan", "implement") or not model:
                raise ValueError(f"--models: {part!r} is not slot=model[@effort] for chat, plan or "
                                 "implement")
            out[slot] = {"model": model, "effort": effort or None}
    return out


def turns_used() -> int:
    return sum(1 for line in LEDGER.read_text().splitlines() if line.strip()) if LEDGER.exists() else 0


class Workspace(drive.Sage):
    """drive.py's client, pointed at the workspace's own Sage, and unable to publish."""

    def _req(self, method: str, path: str, body=None, timeout=60.0):
        if "publish" in path:
            raise PermissionError(f"the demo harness never publishes ({method} {path})")
        return super()._req(method, path, body, timeout)


# ---- @mentions, read and picked the way the composer does (`util.js`, `api.js`, `store.js`) ---------

def mention_word(text: str) -> str:
    return "@" + re.sub(r"\s+", "_", str(text or "")).lstrip("@")


def mention_tokens_in(text: str) -> set[str]:
    pattern = r"""(?:^|\s)@([^\s]+?)(?=[\s,;:!?)\]}'"]|\.(?!\w)|\Z)"""
    return {"@" + m for m in re.findall(pattern, text)}


def row_tokens(row: dict) -> set[str]:
    path = str(row.get("path") or "")
    if not path:
        return {mention_word(str(row.get("name") or "").split("/")[-1] or "resource")}
    parts = path.split("/")
    return {mention_word("/".join(parts[-n:])) for n in range(1, len(parts) + 1)}


def project_rows(members: list[dict], extensions: list[dict]) -> list[dict]:
    """The Project's rows as the @ menu holds them: members, their pins, and the skills.

    The selected app's Attachments are left out: no prompt in the script names a file."""
    rows = []
    for item in members:
        kind = UI_KIND.get(item.get("kind"), item.get("kind") or "file")
        if kind not in GROUPS:
            continue
        rows.append({**{k: item[k] for k in ("id", "name", "path", "project", "bindingKey", "alias",
                                             "description", "capabilities", "reasoning_efforts")
                        if item.get(k) is not None}, "kind": kind})
        bare = str(item.get("id") or "").split(":", 1)[-1]
        for pin in item.get("pins") or []:
            if kind == "dataset" and pin.get("path"):
                rows.append({"id": f"dsfile:{bare}:{pin['path']}", "kind": "file",
                             "name": pin.get("name") or pin["path"].split("/")[-1],
                             "datasetId": bare, "datasetRelPath": pin["path"],
                             "datasetName": item.get("name"), "parentId": item.get("id"),
                             "subtitle": item.get("name")})
            elif kind == "datasource" and pin.get("table"):
                dotted = ".".join(p for p in (pin.get("database"), pin.get("schema"), pin["table"]) if p)
                rows.append({"id": f"table:{bare}:{dotted}", "kind": "table",
                             "name": pin.get("name") or pin["table"],
                             "bindingKey": item.get("bindingKey") or ["data_source", bare],
                             "scope": {"database": pin.get("database") or "",
                                       "schema": pin.get("schema") or "", "table": pin["table"]},
                             "parentId": item.get("id"), "subtitle": item.get("name")})
    rows += [{"id": e["id"], "name": e["name"], "kind": "skill"} for e in extensions
             if e.get("kind") == "skill" and not e.get("shadowed")]
    return rows


def context_body(row: dict, in_build: bool) -> dict:
    """What `addToConversation` posts for a picked row."""
    body = {"kind": POST_KIND.get(row["kind"], row["kind"]), "addedBy": "user", "resourceId": row["id"],
            "inBuild": in_build}
    for key in ("name", "path", "project", "bindingKey", "description", "alias", "capabilities",
                "reasoning_efforts", "parentId", "datasetId", "datasetRelPath", "datasetName", "scope"):
        if row.get(key) is not None:
            body[key] = row[key]
    return body


def turn_refs(rows: list[dict]) -> tuple[list[str], list[dict]]:
    """A Build turn's `mentions` and `resources`, as `collectTurnRefs` carries them."""
    mentions: list[str] = []
    resources: list[dict] = []
    for row in rows:
        key = row.get("bindingKey")
        if key and len(key) == 2:
            ref = {"kind": key[0], "id": key[1], "name": row.get("name") or ""}
            if (row.get("scope") or {}).get("table"):
                ref["table"] = row["scope"]["table"]
                if row.get("subtitle"):
                    ref["sourceName"] = row["subtitle"]
            if ref not in resources:
                resources.append(ref)
            continue
        path = row.get("path") or row.get("datasetRelPath") or row.get("name")
        if path and path not in mentions:
            mentions.append(path)
    return mentions, resources


# ---- checks, and the summary ------------------------------------------------------------------------

def tool_counts(events: list[dict]) -> dict[str, int]:
    """Tool events by tool name. A call can send more than one, so this counts events, not calls."""
    return dict(Counter(str(e["tool"]) for e in events
                        if e.get("type") == "agent" and e.get("kind") == "tool" and e.get("tool")))


def chat_checks(events: list[dict], expect: dict) -> dict:
    artifacts = Counter(str(item.get("kind")) for e in events if e.get("type") == "artifacts"
                        for item in e.get("items") or [] if item.get("role", "answer") == "answer")
    out: dict = {"artifacts": dict(artifacts)}
    if expect.get("artifact"):
        out["expected"] = expect["artifact"]
        out["artifactWritten"] = artifacts[expect["artifact"]] > 0
    if expect.get("tool"):
        want = re.compile(expect["tool"]["pattern"], re.IGNORECASE)
        n = sum(c for name, c in tool_counts(events).items() if want.search(name))
        out["tool"] = {"label": expect["tool"]["label"], "events": n, "called": n > 0}
    return out


def add_not_doing(markdown: str, line: str) -> str | None:
    """The plan with `line` added as the last item under its Not doing heading, or None."""
    lines = markdown.split("\n")
    head = next((i for i, x in enumerate(lines) if re.match(r"#+\s*not doing\b", x.strip(), re.IGNORECASE)), None)
    if head is None:
        return None
    end = next((i for i in range(head + 1, len(lines)) if lines[i].lstrip().startswith("#")), len(lines))
    last = max((i for i in range(head + 1, end) if lines[i].strip()), default=head)
    bullet = re.match(r"\s*[-*+]\s", lines[last])
    lines.insert(last + 1, f"{bullet.group(0) if bullet else '- '}{line}")
    return "\n".join(lines)


def findings(probe: dict | None) -> list[dict]:
    """Every tab where the probe found something, with what it found."""
    out = []
    for tab in (probe or {}).get("tabs") or []:
        found = {k: tab[k] for k in FINDINGS if tab.get(k)}
        if found:
            out.append({"tab": tab.get("tab"), **found})
    return out


def build_outcome(turn: dict) -> dict:
    """Clean is `ok` with a verification that did not fail."""
    done = turn.get("done") or {}
    diag = turn.get("diagnostics") or {}
    ver = done.get("verification") or diag.get("verification")
    overall = ver.get("overall") if isinstance(ver, dict) else ver
    repairs = diag.get("repairs")
    return {"decision": done.get("decision"), "ok": done.get("ok"), "verification": overall,
            "stages": ver.get("stages") if isinstance(ver, dict) else None,
            "repairs": len(repairs) if isinstance(repairs, list) else None,
            "repairKinds": dict(Counter(map(str, repairs or []))),
            "clean": done.get("ok") is True and overall != "failed"}


def summary(record: dict) -> dict:
    rows, failed, found = [], [], []
    for t in record.get("turns", []):
        done = t.get("done") or {}
        row = {"n": t["n"], "step": t["step"], "kind": t["kind"]}
        if t["kind"] == "chat":
            row.update(decision=done.get("decision"), ok=done.get("ok"))
            checks = t.get("checks")
            if checks:
                row["checks"] = checks
                if checks.get("artifactWritten") is False:
                    failed.append(f"{t['step']}: no {checks['expected']} written")
                if (checks.get("tool") or {}).get("called") is False:
                    failed.append(f"{t['step']}: no {checks['tool']['label']} tool called")
        elif done.get("decision") == "awaiting approval":
            row.update(kind="plan", decision=done.get("decision"))
        else:
            row.update(build_outcome(t), probe=findings(t.get("probe")))
            if (t.get("probe") or {}).get("error"):
                row["probeError"] = t["probe"]["error"]
            if row["clean"] and row["probe"]:
                found.append({"step": t["step"], "findings": row["probe"]})
        rows.append(row)
    builds = [r for r in rows if r["kind"] == "build"]
    return {"turns": rows, "builds": len(builds), "cleanBuilds": sum(1 for r in builds if r["clean"]),
            "chatChecksFailed": failed, "cleanButProbeFound": found}


def render(s: dict) -> str:
    out = ["| # | step | outcome | verification | stages | repairs | probe |", "|" + "---|" * 7]
    for r in s["turns"]:
        if r["kind"] == "chat":
            checks = r.get("checks") or {}
            note = ", ".join(f"{k}={v}" for k, v in checks.items() if k in ("artifactWritten",)) or ""
            if checks.get("tool"):
                note = (note + f" {checks['tool']['label']}={checks['tool']['called']}").strip()
            out.append(f"| {r['n']} | {r['step']} | {r.get('decision')} {note} | | | | |")
        elif r["kind"] == "plan":
            out.append(f"| {r['n']} | {r['step']} | {r['decision']} | | | | |")
        else:
            stages = "/".join(f"{k}={v}" for k, v in (r.get("stages") or {}).items())
            probe = "; ".join(json.dumps(f) for f in r["probe"]) or r.get("probeError") or "-"
            out.append(f"| {r['n']} | {r['step']} | {r['decision']} ok={r['ok']} | {r['verification']} "
                       f"| {stages} | {r['repairs']} {r['repairKinds'] or ''} | {probe} |")
    out.append(f"\nBuilds that ended clean but the probe found something: "
               f"{len(s['cleanButProbeFound'])} of {s['cleanBuilds']} clean ({s['builds']} builds)")
    out += [f"- {f['step']}: {json.dumps(f['findings'])}" for f in s["cleanButProbeFound"]]
    out += [f"Chat check failed: {f}" for f in s["chatChecksFailed"]]
    return "\n".join(out)


# ---- the probe process ------------------------------------------------------------------------------

def stop_group(proc: subprocess.Popen) -> None:
    """Stop the probe and the Chromium it started, and reap it. Safe after a normal exit."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            proc.wait(5 if sig == signal.SIGTERM else None)
            return
        except subprocess.TimeoutExpired:
            continue


def run_probe(command: list[str], timeout: float, popen=subprocess.Popen) -> dict:
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        proc = popen(command, stdin=subprocess.DEVNULL, stdout=out, stderr=err, start_new_session=True)
        result = None
        try:
            proc.wait(timeout)
        except subprocess.TimeoutExpired:
            result = {"error": f"probe timed out after {timeout:g}s"}
        finally:
            stop_group(proc)
        if result is None:
            out.seek(0)
            try:
                result = json.loads(out.read().decode("utf-8", "replace") or "null")
            except ValueError:
                result = None
            if not isinstance(result, dict):
                err.seek(0)
                said = err.read().decode("utf-8", "replace").strip()[-300:]
                result = {"error": f"probe exited {proc.returncode} without a report: {said}"}
    result["reaped"] = proc.returncode is not None
    return result


# ---- the run ----------------------------------------------------------------------------------------

class Demo:
    def __init__(self, label: str, models: dict, sage, base: str, probe, sleep=time.sleep) -> None:
        self.label, self.models, self.sage, self.base = label, models, sage, base
        self.probe, self.sleep = probe, sleep
        self.script = load_script()
        self.dir = RUNS / label
        self.tid = ""
        self.posted: set[str] = set()
        self.record: dict = {
            "label": label, "kind": "demo", "requestedModels": models,
            "promptSha256": {t["id"]: t["sha256"] for t in
                             [*self.script["chat"], self.script["handoff"], *self.script["build"]]},
            "turns": [], "crossings": [], "notes": [],
        }

    def prepare(self) -> None:
        if self.dir.exists():
            raise SystemExit(f"{self.dir} exists; every run gets a fresh label")
        self.dir.mkdir(parents=True)
        LEDGER.parent.mkdir(parents=True, exist_ok=True)

    def spend(self, step: str) -> int:
        used = turns_used()
        if used >= BOUND:
            raise BoundReached(f"{used}/{BOUND} demo turns already started")
        with LEDGER.open("a") as f:
            f.write(json.dumps({"n": used + 1, "label": self.label, "step": step,
                                "startedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z")}) + "\n")
        return used + 1

    def turn(self, step: str, path: str, body: dict, prompt: str | None, kind: str):
        n = self.spend(step)
        began = time.monotonic()
        error = None
        try:
            headers, events = self.sage.stream(path, body)
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            headers, events, error = {}, [], f"{type(e).__name__}: {e}"
        done = next((e for e in reversed(events) if e.get("type") == "done"), None)
        row = {"n": n, "step": step, "kind": kind, "turnId": headers.get("X-Sage-Turn-Id"),
               "turnState": headers.get("X-Sage-Turn-State"),
               "promptSha256": sha(prompt) if prompt is not None else None,
               "wallSeconds": round(time.monotonic() - began, 1), "driverError": error,
               "done": kept(done) if done else None,
               "eventCounts": dict(Counter(str(e.get("type")) for e in events)),
               "toolEvents": tool_counts(events),
               "cards": sorted({str(e["type"]) for e in events if e.get("type") in CARDS}),
               "events": [k for k in (kept(e) for e in events) if k is not None][:200]}
        if kind == "build":
            row["diagnostics"] = self.download(row["turnId"])
        self.record["turns"].append(row)
        self.save()
        print(f"  turn {n}/{BOUND} {step}: decision={(done or {}).get('decision')!r} "
              f"ok={(done or {}).get('ok')} {row['wallSeconds']:.0f}s", flush=True)
        return row, events

    def download(self, turn_id: str | None) -> dict | None:
        if not turn_id:
            return None
        path = f"/project/build-diagnostics/{turn_id}?app_id={self.sage.app}&conversation_id={self.tid}"
        for _ in range(10):
            try:
                doc = self.sage.get(path)
            except urllib.error.HTTPError as e:
                if e.code != 404:
                    return {"error": f"HTTP {e.code}"}
                self.sleep(3)
                continue
            out = self.dir / f"build-{turn_id}.json"
            out.write_text(json.dumps(doc, indent=2) + "\n")
            return {"file": out.name, **summarise(doc)}
        return {"error": "not captured (404 after 30s)"}

    def pick(self, prompt: str, in_build: bool) -> list[dict]:
        """The @ menu's picks for every token typed: a chip for each row a pick adds one for."""
        rows = project_rows(self.sage.get("/project/resources").get("items") or [],
                            self.sage.get("/project/extensions").get("items") or [])
        typed = mention_tokens_in(prompt)
        matched = [r for r in rows if row_tokens(r) & typed]
        unmatched = sorted(typed - set().union(*(row_tokens(r) for r in rows)))
        if unmatched:
            self.record["notes"].append(f"no @ menu row for {', '.join(unmatched)}")
        for row in matched:
            if row["kind"] in TEXT_ONLY or row["id"] in self.posted:
                continue
            try:
                self.sage.post(f"/threads/{self.tid}/context", context_body(row, in_build))
            except urllib.error.HTTPError as e:
                self.record["notes"].append(f"the pick of {row['id']} was refused: HTTP {e.code}")
                continue
            self.posted.add(row["id"])
        return matched

    def chat(self, item: dict) -> dict:
        prompt, step = item["prompt"], f"chat {item['id']}"
        self.pick(prompt, in_build=False)
        path = f"/threads/{self.tid}/chat/stream"
        row, events = self.turn(step, path, {"prompt": prompt}, prompt, "chat")
        card = next((e for e in events if e.get("type") == "dataset-files"), None)
        if card and item.get("datasetPick"):
            pick = next((r["path"] for r in (card.get("allRows") or card.get("rows") or [])
                         if str(r.get("path", "")).split("/")[-1] == item["datasetPick"]), None)
            if pick is None:
                self.record["notes"].append(f"{step}: the file card offered no {item['datasetPick']}")
            else:
                self.sage.post(f"/threads/{self.tid}/context/dataset/{quote(card['datasetId'], safe='')}"
                               "/file", {"path": pick})
                row, more = self.turn(f"{step}: after the file card", path,
                                      {"prompt": prompt, "skipDatasetGate": True}, prompt, "chat")
                events = events + more
        if item.get("expect"):
            row["checks"] = chat_checks(events, item["expect"])
            self.save()
        return row

    def build_turn(self, step: str, path: str, body: dict, prompt: str | None):
        """One Build turn, its plan approved if it drafts one, then the probe."""
        row, events = self.turn(step, path, body, prompt, "build")
        if (row.get("done") or {}).get("decision") == "awaiting approval":
            plan = next((e for e in events if e.get("type") == "plan-proposed"), {})
            row, more = self.turn(f"{step}: approve", "/project/build/approve",
                                  {"answers": "", "conversation": self.tid,
                                   "plan_id": plan.get("planId") or ""}, None, "build")
            events = events + more
        if row.get("done"):
            row["probe"] = self.probe(f"{self.base}/preview/{quote(self.sage.app, safe='')}/")
            self.save()
        return row, events

    def handoff(self, item: dict) -> None:
        row = self.chat(item)
        row["handoffOffered"] = "handoff-suggest" in row["eventCounts"]
        self.spend("handoff: write a plan")
        began = time.monotonic()
        draft = self.sage.post(f"/threads/{self.tid}/handoff/plan", {}, timeout=1800.0)
        name = draft.get("appName") or "Signal Room"
        bound = self.sage.post(f"/threads/{self.tid}/handoff/confirm",
                               {"include": CROSSINGS, "target": {"appId": "", "name": name}})
        handoff = bound.get("handoff") or {}
        self.sage.app = handoff.get("appId") or self.sage.app
        plan_id = handoff.get("planId") or (draft.get("handoff") or {}).get("planId") or ""
        try:
            plan = (self.sage.get(f"/plans/{quote(plan_id, safe='')}/markdown") if plan_id else {}) or {}
        except urllib.error.HTTPError:
            plan = {}
        edits = add_not_doing(plan.get("content") or "", item["plan"]["notDoing"])
        self.record["handoff"] = {"draftSeconds": round(time.monotonic() - began, 1),
                                  "status": handoff.get("status"), "appId": self.sage.app,
                                  "planId": plan_id, "appNamedSignalRoom": name == "Signal Room",
                                  "notDoingEdited": edits is not None}
        body = {"answers": item["plan"]["answers"], "conversation": self.tid, "plan_id": plan_id}
        if edits is not None:
            body["plan_edits"] = edits
        self.build_turn("handoff: approve", "/project/build/approve", body, None)

    def build(self, item: dict) -> None:
        prompt, step = item["prompt"], f"build {item['id']}"
        mentions, resources = turn_refs(self.pick(prompt, in_build=True))
        body = {"prompt": prompt, "conversation": self.tid}
        if mentions:
            body["mentions"] = mentions
        if resources:
            body["resources"] = resources
        _, events = self.build_turn(step, "/project/build/stream", body, prompt)
        if not item.get("addThem"):
            return
        # The bar's Add them: cross this Conversation's chips into the app. When a live refusal
        # card is under the turn and nothing was refused, the button is "Add them and build" and
        # sends the card's request again.
        crossed = self.sage.post(f"/threads/{self.tid}/crossing")
        refused = crossed.get("refused") or []
        card = next((e for e in events if e.get("type") == "mentions-unresolved" and e.get("prompt")), None)
        replay = bool(card) and not refused
        self.record["crossings"].append({"step": step, "refused": len(refused), "replayed": replay})
        if replay:
            again = {**body, "prompt": card["prompt"]}
            self.build_turn(f"{step}: Add them and build", "/project/build/stream", again, card["prompt"])

    def configure(self) -> None:
        apps = self.sage.get("/apps")
        self.sage.app = apps.get("selected") or ""
        self.record["appsBefore"] = len(apps.get("items") or [])
        chat = self.models["chat"]
        body = {"mode": "auto", "chat_model": chat["model"],
                "catalog": {"plan": self.models["plan"], "implement": self.models["implement"]}}
        if chat["effort"]:
            body["reasoning_effort"] = chat["effort"]
        status = self.sage.post("/project/model", body) or {}
        self.record["modelStatus"] = {k: v for k, v in status.items()
                                      if "mode" in k.lower() and not isinstance(v, (dict, list))}
        assignments = self.sage.get("/project/model/assignments") or {}
        self.record["assignments"] = [
            {k: s.get(k) for k in ("slot", "model", "effort", "default", "default_effort")}
            for s in (assignments.get("slots") or assignments.get("assignments") or [])]

    def go(self) -> None:
        self.configure()
        thread = self.sage.post("/threads", {})
        self.tid = thread.get("id") or (thread.get("thread") or {}).get("id") or ""
        self.record["conversation"] = self.tid
        for item in self.script["chat"]:
            self.chat(item)
        self.handoff(self.script["handoff"])
        for item in self.script["build"]:
            self.build(item)

    def save(self) -> None:
        self.record["turnsUsedAfter"] = turns_used()
        (self.dir / "record.json").write_text(json.dumps(self.record, indent=2) + "\n")

    def finish(self) -> str:
        self.record["probesReaped"] = all((t.get("probe") or {}).get("reaped", True)
                                          for t in self.record["turns"])
        self.save()
        s = summary(self.record)
        (self.dir / "summary.json").write_text(json.dumps(s, indent=2) + "\n")
        return render(s)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--label", required=True)
    p.add_argument("--models", default=DEFAULT_MODELS,
                   help=f"slot=model[@effort] for chat, plan, implement (default {DEFAULT_MODELS})")
    p.add_argument("--port", default=os.environ.get("SAGE_CONTROL_PORT", "8080"))
    args = p.parse_args()
    try:
        models = parse_models(args.models)
    except ValueError as e:
        p.error(str(e))
    sys.path.insert(0, str(HERE.parents[1] / "backend"))
    from sage.preview import page_check
    from sage.preview.prefix import domino_base_prefix

    why = page_check.unavailable()
    if why is not None:
        print(f"the probe cannot run here: {why}", file=sys.stderr)
        return 2
    if turns_used() >= BOUND:
        print(f"bound reached: {turns_used()}/{BOUND}", file=sys.stderr)
        return 3
    base = f"http://127.0.0.1:{args.port}{domino_base_prefix()}"
    command = [shutil.which("node") or "node", str(PROBE)]
    chromium = str(page_check.find_chromium())
    run = Demo(args.label, models, Workspace(f"{base}/api"), base=base,
               probe=lambda url: run_probe([*command, url, chromium, str(SETTLE_MS)], PROBE_TIMEOUT_S))
    run.prepare()
    code = 0
    try:
        run.go()
    except BoundReached as e:
        run.record["notes"].append(f"stopped at the bound: {e}")
        code = 3
    except Exception as e:  # noqa: BLE001 - recorded, then the run is still summarised
        body = e.read().decode("utf-8", "replace")[:500] if isinstance(e, urllib.error.HTTPError) else ""
        run.record["notes"].append(f"driver stopped: {type(e).__name__}: {e} {body}".strip())
        code = 1
    finally:
        print(run.finish())
    print(f"{args.label}: turns used {turns_used()}/{BOUND}; record in {run.dir}")
    return code


if __name__ == "__main__":
    sys.exit(main())
