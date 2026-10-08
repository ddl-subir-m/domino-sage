#!/usr/bin/env python3
"""Drive one condition of the #702 evaluation pilot through Sage's own HTTP API, and record it.

    python3 scripts/eval/drive.py --tree <checkout> --condition treatment --task task1 --label t1-treat
    python3 scripts/eval/drive.py ... --task task1 --repeat-followup          # the `already done` repeat
    python3 scripts/eval/drive.py ... --task task8 --source-map on \\
        --restore <snapshot dir> --workspace <the snapshot's original workspace path>

One run boots that checkout's Sage (`serve.py`) on a fresh workspace and a private HOME, makes the
calls the Workbench makes -- set the Build models, attach the fixture, open a Conversation, send the
request, approve the plan, send the follow-up -- downloads each turn's Build diagnostics the way the
Workbench's download button does, and stops every process it started.

THE BOUND. Every Build turn this driver STARTS is appended to `runs/ledger.jsonl` before the request
goes out, retries included. At `BOUND` it refuses to start another, so the bound holds across runs.

WHAT IS KEPT. Diagnostics downloads (content-free by contract) and a record of each turn: revision,
condition, models and efforts, prompt digest, outcome, verification, repairs, discovery reads,
tokens and wall time. Not the plan, not the agent's prose, not a single streamed text delta.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
RUNS = HERE / "runs"
LEDGER = RUNS / "ledger.jsonl"
SCRATCH = pathlib.Path("/private/tmp/702-runs")
BOUND = 24
PORT = 18702
DATASET_ID = "ds_eval-orders"
MODELS = {"plan": {"model": "sonnet", "effort": "low"}, "implement": {"model": "haiku", "effort": None}}
DISCOVERY_TOOLS = {"read", "glob", "grep", "list", "codesearch", "sage_source_map"}
EDIT_TOOLS = {"edit", "write", "apply_patch"}
# Streamed events whose payload is model or plan text: counted, never kept.
TEXT_EVENTS = {"agent", "delta", "narration", "user", "plan-proposed", "iterate"}
TURN_CAP_S = 45 * 60


class BoundReached(RuntimeError):
    pass


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def turns_used() -> int:
    return sum(1 for line in LEDGER.read_text().splitlines() if line.strip()) if LEDGER.exists() else 0


class Sage:
    def __init__(self, base: str) -> None:
        self.base = base
        self.app = ""

    def _req(self, method: str, path: str, body=None, timeout=60.0):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(f"{self.base}{path}", data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        if self.app:
            req.add_header("X-Sage-App", self.app)
        return urllib.request.urlopen(req, timeout=timeout)

    def get(self, path: str, timeout=60.0):
        with self._req("GET", path, timeout=timeout) as r:
            return json.loads(r.read() or b"null")

    def post(self, path: str, body=None, timeout=120.0):
        with self._req("POST", path, body if body is not None else {}, timeout=timeout) as r:
            return json.loads(r.read() or b"null")

    def stream(self, path: str, body: dict) -> tuple[dict, list[dict]]:
        """POST a turn and read its SSE to the end. Answers (response headers, events)."""
        events: list[dict] = []
        deadline = time.monotonic() + TURN_CAP_S
        with self._req("POST", path, body, timeout=900.0) as r:
            headers = {k: r.headers.get(k, "") for k in
                       ("X-Sage-Turn-Id", "X-Sage-Turn-State", "X-Sage-Turn-Sequence")}
            for raw in r:
                line = raw.decode("utf-8", "replace").rstrip("\n")
                if line.startswith("data:"):
                    try:
                        events.append(json.loads(line[5:].strip()))
                    except ValueError:
                        events.append({"type": "_unparsed"})
                if time.monotonic() > deadline:
                    events.append({"type": "_driver_timeout"})
                    break
        return headers, events


def kept(event: dict) -> dict | None:
    """The part of a streamed event that is Sage's own vocabulary rather than model text."""
    kind = event.get("type")
    if kind in TEXT_EVENTS:
        if kind == "plan-proposed":
            return {"type": kind, "planId": event.get("planId"), "steps": event.get("steps"),
                    "kind": event.get("kind")}
        return None
    out = {}
    for key, value in event.items():
        if key in ("prompt", "text", "content", "plan"):
            continue
        if isinstance(value, str) and len(value) > 300:
            value = value[:300] + "…"
        if isinstance(value, (dict, list)) and len(json.dumps(value)) > 2000:
            value = f"<{type(value).__name__} omitted>"
        out[key] = value
    return out


def summarise(doc: dict) -> dict:
    """The per-turn numbers the report needs, read off one diagnostics download."""
    timing = doc.get("timing") or {}
    calls = [c for c in timing.get("calls", []) if isinstance(c, dict)]
    tools = [t for t in timing.get("tools", []) if isinstance(t, dict)]
    spans = [s for s in timing.get("spans", []) if isinstance(s, dict)]
    routes = {}
    for c in calls:
        key = "|".join(str(c.get(k)) for k in ("phase", "model", "requestedAlias", "effectiveEffort",
                                                "effortSource", "effortStatus"))
        routes[key] = routes.get(key, 0) + 1
    first_edit = next((i for i, t in enumerate(tools)
                       if t.get("tool") in EDIT_TOOLS and t.get("status") == "completed"), len(tools))
    by_tool: dict[str, int] = {}
    for t in tools:
        by_tool[str(t.get("tool"))] = by_tool.get(str(t.get("tool")), 0) + 1

    def tok(key):
        seen = [c[key] for c in calls if isinstance(c.get(key), (int, float))]
        return {"sum": sum(seen) if seen else None, "callsReporting": len(seen), "calls": len(calls)}

    outcome = doc.get("buildOutcome") or {}
    capture = doc.get("capture") or {}
    return {
        "sourceRevision": doc.get("sourceRevision"),
        "kind": f"{(doc.get('turn') or {}).get('kind')}/{(doc.get('turn') or {}).get('phase')}",
        "status": outcome.get("status"),
        "decision": outcome.get("decision"),
        "verification": outcome.get("verification"),
        "captureComplete": capture.get("complete"),
        "captureStatus": capture.get("status"),
        "ms": timing.get("ms"),
        "modelCalls": len(calls),
        "routes(phase|model|alias|effectiveEffort|effortSource|effortStatus)": routes,
        "inTokens": tok("inTokens"),
        "outTokens": tok("outTokens"),
        "cachedTokens": tok("cachedTokens"),
        "repairs": [s.get("retry_reason") for s in spans
                    if str(s.get("name", "")).startswith("agent-turn.") and "retry_reason" in s],
        "typechecks": [s.get("errors") for s in spans if s.get("name") == "typecheck"],
        "toolsByName": by_tool,
        "discoveryReads": sum(1 for t in tools if t.get("tool") in DISCOVERY_TOOLS),
        "discoveryReadsBeforeFirstEdit": sum(1 for t in tools[:first_edit]
                                             if t.get("tool") in DISCOVERY_TOOLS),
        "sourceMapCalls": by_tool.get("sage_source_map", 0),
        "landedEdits": sum(1 for t in tools if t.get("tool") in EDIT_TOOLS
                           and t.get("status") == "completed"),
    }


class Run:
    def __init__(self, args) -> None:
        self.args = args
        self.tree = pathlib.Path(args.tree).resolve()
        self.dir = RUNS / args.label
        self.scratch = SCRATCH / args.label
        self.workspace = pathlib.Path(args.workspace) if args.workspace else self.scratch / "ws"
        self.home = self.scratch / "home"
        self.mount = SCRATCH / "fixture-mount" / "eval-orders"
        self.sage = Sage(f"http://127.0.0.1:{PORT}/api")
        self.proc: subprocess.Popen | None = None
        self.prompts = json.loads((HERE / "prompts.json").read_text())[args.task]
        self.record: dict = {
            "label": args.label, "task": args.task, "condition": args.condition,
            "sageRevision": subprocess.check_output(["git", "-C", str(self.tree), "rev-parse", "HEAD"],
                                                    text=True).strip(),
            "sourceMap": args.source_map, "requestedModels": MODELS,
            "fixtureSha256": hashlib.sha256((HERE / "fixture/orders.csv").read_bytes()).hexdigest(),
            "turns": [], "notes": [],
        }

    # ---- processes -------------------------------------------------------------------------
    def prepare(self) -> None:
        if self.dir.exists():
            raise SystemExit(f"{self.dir} exists; every run gets a fresh label")
        self.dir.mkdir(parents=True)
        if self.scratch.exists() and not self.args.workspace:
            shutil.rmtree(self.scratch)
        self.home.mkdir(parents=True, exist_ok=True)
        (self.home / "package.json").write_text('{"private": true}\n')
        link = self.home / "node_modules"
        if not link.exists():
            link.symlink_to(self.tree / "node_modules")
        self.mount.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(HERE / "fixture/orders.csv", self.mount / "orders.csv")
        if self.args.restore:
            if self.workspace.exists():
                shutil.rmtree(self.workspace)
            shutil.copytree(self.args.restore, self.workspace, symlinks=True)
            self.record["restoredFrom"] = str(self.args.restore)
        self.workspace.mkdir(parents=True, exist_ok=True)
        seed = SCRATCH / "reasoning-evidence.json"
        if seed.exists() and not self.args.measure:
            (self.workspace / ".sage").mkdir(exist_ok=True)
            shutil.copyfile(seed, self.workspace / ".sage" / "reasoning-evidence.json")
            self.record["reasoningEvidenceSha256"] = hashlib.sha256(seed.read_bytes()).hexdigest()

    def start(self) -> None:
        real = pathlib.Path.home()
        env = {**os.environ, "HOME": str(self.home), "SAGE_WORKSPACE_DIR": str(self.workspace),
               "SAGE_CONTROL_PORT": str(PORT), "EVAL_FIXTURE_MOUNT": str(self.mount),
               "npm_config_cache": str(real / ".npm"), "GIT_CONFIG_GLOBAL": str(real / ".gitconfig"),
               "UV_CACHE_DIR": str(real / ".cache/uv")}
        log = open(self.dir / "orchestrator.log", "w")  # noqa: SIM115 - closed in stop()
        self._log = log
        self.proc = subprocess.Popen([str(self.tree / "backend/.venv/bin/python"), str(HERE / "serve.py")],
                                     cwd=self.tree / "backend", env=env, stdout=log,
                                     stderr=subprocess.STDOUT, start_new_session=True)
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"orchestrator exited {self.proc.returncode}; see orchestrator.log")
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/healthz", timeout=5) as r:
                    if r.status == 200:
                        return
            except (urllib.error.URLError, OSError):
                pass
            time.sleep(2)
        raise RuntimeError("orchestrator never answered /healthz")

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.send_signal(signal.SIGTERM)
            try:
                self.proc.wait(timeout=60)
            except subprocess.TimeoutExpired:
                os.killpg(self.proc.pid, signal.SIGKILL)
                self.proc.wait(timeout=10)
                self.record["notes"].append("orchestrator needed SIGKILL after 60s")
        if getattr(self, "_log", None):
            self._log.close()
        self.record["leftoverProcesses"] = leftovers((str(self.scratch), str(self.workspace)))

    # ---- turns -----------------------------------------------------------------------------
    def turn(self, step: str, path: str, body: dict, prompt: str | None) -> dict:
        used = turns_used()
        if used >= BOUND:
            raise BoundReached(f"{used}/{BOUND} Build turns already started")
        with LEDGER.open("a") as f:
            f.write(json.dumps({"n": used + 1, "label": self.args.label, "step": step,
                                "startedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z")}) + "\n")
        began = time.monotonic()
        error = None
        try:
            headers, events = self.sage.stream(path, body)
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            headers, events, error = {}, [], f"{type(e).__name__}: {e}"
        wall = time.monotonic() - began
        done = next((e for e in reversed(events) if e.get("type") == "done"), None)
        counts: dict[str, int] = {}
        for e in events:
            counts[str(e.get("type"))] = counts.get(str(e.get("type")), 0) + 1
        row = {
            "n": used + 1, "step": step, "turnId": headers.get("X-Sage-Turn-Id"),
            "turnState": headers.get("X-Sage-Turn-State"),
            "promptSha256": sha(prompt) if prompt is not None else None,
            "wallSeconds": round(wall, 1), "driverError": error,
            "done": kept(done) if done else None, "eventCounts": counts,
            "events": [k for k in (kept(e) for e in events if e.get("type") not in ("agent", "delta"))
                       if k is not None][:200],
        }
        row["diagnostics"] = self.download(row["turnId"])
        self.record["turns"].append(row)
        self.save()
        print(f"  turn {row['n']}/{BOUND} {step}: decision={(done or {}).get('decision')!r} "
              f"ok={(done or {}).get('ok')} {wall:.0f}s", flush=True)
        return row

    def download(self, turn_id: str | None) -> dict | None:
        if not turn_id:
            return None
        conv = self.record.get("conversation", "")
        path = (f"/project/build-diagnostics/{turn_id}?app_id={self.sage.app}"
                f"&conversation_id={conv}")
        for _ in range(10):
            try:
                doc = self.sage.get(path)
            except urllib.error.HTTPError as e:
                if e.code != 404:
                    return {"error": f"HTTP {e.code}"}
                time.sleep(3)
                continue
            out = self.dir / f"build-{turn_id}.json"
            out.write_text(json.dumps(doc, indent=2) + "\n")
            return {"file": out.name, **summarise(doc)}
        return {"error": "not captured (404 after 30s)"}

    def failed(self, row: dict) -> bool:
        done = row.get("done") or {}
        return not done or done.get("ok") is False

    def run_step(self, step: str, path: str, body: dict, prompt: str | None) -> dict:
        row = self.turn(step, path, body, prompt)
        if self.failed(row):
            self.record["notes"].append(f"{step} failed; retrying once")
            row = self.turn(f"{step} (retry)", path, body, prompt)
        return row

    def ask(self, step: str, prompt: str) -> dict:
        body = {"prompt": prompt, "conversation": self.record["conversation"]}
        row = self.run_step(step, "/project/build/stream", body, prompt)
        if (row.get("done") or {}).get("decision") == "awaiting approval":
            plan = next((e for e in row["events"] if e.get("type") == "plan-proposed"), {})
            approve = {"answers": "", "conversation": self.record["conversation"],
                       "plan_id": plan.get("planId") or ""}
            row = self.run_step(f"{step}: approve", "/project/build/approve", approve, None)
        return row

    # ---- the run ---------------------------------------------------------------------------
    def configure(self) -> None:
        apps = self.sage.get("/apps")
        if not apps.get("items"):
            self.sage.post("/apps", {})
            apps = self.sage.get("/apps")
        self.sage.app = apps.get("selected") or (apps["items"][0]["id"] if apps.get("items") else "")
        self.record["app"] = {"id": self.sage.app, "items": [
            {k: i.get(k) for k in ("id", "stack", "selected", "built")} for i in apps.get("items", [])]}
        if self.args.measure:
            self.measure("sonnet")
        status = self.sage.post("/project/model", {"catalog": MODELS})
        self.record["modelStatus"] = {k: v for k, v in status.items()
                                      if "mode" in k.lower() and not isinstance(v, (dict, list))}
        assignments = self.sage.get("/project/model/assignments")
        self.record["assignments"] = [
            {k: s.get(k) for k in ("slot", "model", "effort", "default", "default_effort")}
            for s in (assignments.get("slots") or assignments.get("assignments") or [])]
        if self.args.source_map is not None:
            self.record["settings"] = self.sage.post(
                "/project/settings", {"source_map": self.args.source_map == "on"})
        else:
            self.record["settings"] = self.sage.get("/project/settings")

    def alias(self, name: str) -> dict:
        rows = self.sage.get("/project/model/assignments").get("aliases") or []
        row = next((a for a in rows if a.get("name") == name), {})
        return {k: row.get(k) for k in ("name", "reasoning_status", "reasoning_efforts_with_tools",
                                        "reasoning_note")}

    def measure(self, name: str) -> None:
        """The assignments drawer's Re-check: measure one alias on this deployment, and keep it."""
        self.record["measuredBefore"] = self.alias(name)
        self.sage.post("/project/model/recheck", {"model": name})
        deadline = time.monotonic() + 600
        row = {}
        while time.monotonic() < deadline:
            time.sleep(10)
            row = self.alias(name)
            if row.get("reasoning_status") not in ("unmeasured", "measuring"):
                break
        self.record["measuredAfter"] = row
        evidence = self.workspace / ".sage" / "reasoning-evidence.json"
        if evidence.exists():
            shutil.copyfile(evidence, SCRATCH / "reasoning-evidence.json")
            shutil.copyfile(evidence, self.dir / "reasoning-evidence.json")

    def go(self) -> None:
        self.configure()
        if not self.args.restore:
            self.record["attach"] = {k: v for k, v in self.sage.post(
                f"/project/assets/{DATASET_ID}/files/attach", {"path": "orders.csv"}).items()
                if k in ("attached", "dataset", "path", "size")}
        thread = self.sage.post("/threads", {})
        self.record["conversation"] = (thread.get("id") or (thread.get("thread") or {}).get("id"))
        if self.args.dry:
            return
        if self.args.task in ("task1", "task2"):
            self.ask("build", self.prompts["build"])
            self.ask("followup", self.prompts["followup"])
            if self.args.repeat_followup:
                self.ask("followup repeated", self.prompts["followup"])
        else:
            self.ask("followup", self.prompts["followup"])

    def save(self) -> None:
        self.record["turnsUsedAfter"] = turns_used()
        (self.dir / "record.json").write_text(json.dumps(self.record, indent=2) + "\n")


def leftovers(roots: tuple[str, ...]) -> list[str]:
    """Processes still running whose cwd or command line sits under this run's directories."""
    found = []
    out = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True,
                         check=False).stdout
    for line in out.splitlines():
        pid, _, cmd = line.strip().partition(" ")
        if not pid.isdigit() or int(pid) == os.getpid():
            continue
        cwd = subprocess.run(["lsof", "-a", "-p", pid, "-d", "cwd", "-Fn"], capture_output=True,
                             text=True, check=False).stdout
        cwd = next((x[1:] for x in cwd.splitlines() if x.startswith("n")), "")
        if any(r in cmd or cwd.startswith(r) for r in roots):
            found.append(f"{pid} cwd={cwd} {cmd[:120]}")
    return found


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--tree", required=True)
    p.add_argument("--condition", required=True, choices=["control", "treatment"])
    p.add_argument("--task", required=True, choices=["task1", "task2", "task8"])
    p.add_argument("--label", required=True)
    p.add_argument("--source-map", choices=["on", "off"])
    p.add_argument("--repeat-followup", action="store_true")
    p.add_argument("--restore", help="copy this workspace snapshot in before booting")
    p.add_argument("--workspace", help="workspace path (task 8: the snapshot's original path)")
    p.add_argument("--dry", action="store_true", help="boot, configure and attach; start no turn")
    p.add_argument("--measure", action="store_true",
                   help="Re-check sonnet's reasoning settings first and keep the measurement")
    args = p.parse_args()
    RUNS.mkdir(parents=True, exist_ok=True)
    if turns_used() >= BOUND:
        print(f"bound reached: {turns_used()}/{BOUND}", file=sys.stderr)
        return 3
    run = Run(args)
    run.prepare()
    code = 0
    try:
        run.start()
        run.go()
    except BoundReached as e:
        run.record["notes"].append(f"stopped at the bound: {e}")
        code = 3
    except Exception as e:  # noqa: BLE001 - recorded, then the processes are still stopped
        body = e.read().decode("utf-8", "replace")[:500] if isinstance(e, urllib.error.HTTPError) else ""
        run.record["notes"].append(f"driver stopped: {type(e).__name__}: {e} {body}".strip())
        code = 1
    finally:
        run.stop()
        run.save()
    print(f"{args.label}: turns used {turns_used()}/{BOUND}; leftovers {run.record['leftoverProcesses']}")
    return code


if __name__ == "__main__":
    sys.exit(main())
