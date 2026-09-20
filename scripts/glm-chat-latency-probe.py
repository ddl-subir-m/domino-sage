#!/usr/bin/env python3
"""Measure GLM final-answer effort on fixed, previously computed aggregate statistics.

Set GATEWAY_BASE_URL (ending in /v1) and GATEWAY_API_KEY for the same deployment.
Run with the backend Python environment: python scripts/glm-chat-latency-probe.py
The real Chat prompt and live_read_query definition are sent; no tool is executed.
Output is JSONL with timing, usage and visible answers, never credentials or reasoning text.
Review answer correctness separately. This is not an end-to-end workspace benchmark.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

QUESTION = (
    "The user asked: Fit a regression predicting event count per user from their signup month, "
    "using MIXPANEL__EVENT. The query has finished and its result card is already displayed. "
    "Give the final answer; no more tool calls are needed. The computed facts are "
    "N_USERS=1377272, SLOPE=0.0325937766, INTERCEPT=74.5285970, R2=2.74587235e-10. "
    "Predictor x is months since January 1970 of the first recorded event, used as a proxy "
    "because signup dates are unavailable. Response y is lifetime events per user. Counts "
    "do not adjust for differing observation time. No standard errors or significance tests "
    "were computed."
)


def measure(base: str, key: str, effort: str) -> dict:
    from sage.liveread.mcp import TOOLS

    query = next(tool for tool in TOOLS if tool["name"] == "live_read_query")
    body = {
        "model": "GLM 5.3 OR", "stream": True, "temperature": 0.7, "max_tokens": 8192,
        # The gateway includes user in its response-cache key. Distinct IDs avoid replaying
        # an earlier completed answer; provider input-prefix caching can still apply.
        "user": "sage-472-probe-" + uuid.uuid4().hex,
        "messages": [
            {"role": "system", "content": (ROOT / "template/chat/AGENTS.md").read_text()},
            {"role": "user", "content": QUESTION},
        ],
        "tools": [{"type": "function", "function": {
            "name": query["name"], "description": query["description"],
            "parameters": query["inputSchema"],
        }}],
    }
    if effort != "default":
        body["reasoning_effort"] = effort
    req = urllib.request.Request(
        base.rstrip("/") + "/chat/completions", data=json.dumps(body).encode(),
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json",
                 "X-LLM-Tag-sage-probe": "issue-472-final-answer"},
    )
    result = {"effort": effort, "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    start = time.monotonic()
    first = None
    content = ""
    reasoning_chars = 0
    usage = {}
    finish = None
    done = False
    try:
        with urllib.request.urlopen(req, timeout=180) as response:
            result["status"] = response.status
            for raw in response:
                if time.monotonic() - start > 180:
                    raise TimeoutError("Probe exceeded 180 seconds")
                if not raw.startswith(b"data:"):
                    continue
                data = raw[5:].strip()
                if data == b"[DONE]":
                    done = True
                    continue
                chunk = json.loads(data)
                if first is None:
                    first = time.monotonic() - start
                if chunk.get("error"):
                    result["error"] = chunk["error"]
                if chunk.get("usage"):
                    usage = chunk["usage"]
                for choice in chunk.get("choices", []):
                    delta = choice.get("delta", {})
                    content += delta.get("content") or ""
                    reasoning_chars += len(delta.get("reasoning_content") or delta.get("reasoning") or "")
                    finish = choice.get("finish_reason") or finish
    except urllib.error.HTTPError as exc:
        result.update(status=exc.code, error=exc.read().decode()[:1000])
    except (OSError, ValueError) as exc:
        result["error"] = str(exc)
    result.update(wall_seconds=round(time.monotonic() - start, 3),
                  first_chunk_seconds=round(first, 3) if first is not None else None,
                  finish=finish, done=done, reasoning_chars=reasoning_chars,
                  usage=usage, content=content)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--efforts", nargs="+", choices=["default", "low", "high", "max"],
                        default=["high", "max"])
    args = parser.parse_args()
    base, key = os.environ.get("GATEWAY_BASE_URL"), os.environ.get("GATEWAY_API_KEY")
    if not base or not key:
        parser.error("Set GATEWAY_BASE_URL and GATEWAY_API_KEY for the same deployment")
    failed = False
    for effort in args.efforts:
        result = measure(base, key, effort)
        print(json.dumps(result), flush=True)
        failed |= bool(result.get("error") or not result["done"] or result["finish"] != "stop")
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
