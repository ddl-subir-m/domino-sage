#!/usr/bin/env python3
"""Compare gateway response speed with a checked, synthetic text or tool result.

No tool is executed. Each round uses a new nonce to avoid exact response-cache hits.
Provider prompt caching can still occur; its token counts are kept in the results.
The first argument fragment can be punctuation, so first output is not task completion.
This checks transport and copying, not coding quality. A timeout is not a slow success.

Example, from a worktree (the JWT is read in-process and never printed):
  python3 scripts/latency-probe.py --env /path/to/backend/.env \
    --models sonnet gpt-5.4 haiku 'Gemma 4 31B' --context-lines 1800 \
    --shape tool --rounds 3 --output /tmp/latency.json
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path


def probe(base: str, key: str, body: dict, expected: str) -> dict:
    row = {"model": body["model"], "request_bytes": len(json.dumps(body).encode()),
           "status": None, "first_frame_s": None, "first_output_s": None,
           "first_100_output_chars_s": None,
           "max_gap_s": 0, "chunks": 0, "finish": None, "done": False,
           "usage": None, "error": None}
    started = time.monotonic()
    last = None
    content = arguments = ""
    request = urllib.request.Request(
        base + "/chat/completions", data=json.dumps(body).encode(),
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json",
                 "X-LLM-Tag-sage-probe": "latency-comparison"})
    try:
        with urllib.request.urlopen(request, timeout=55) as response:
            row["status"] = response.status
            for raw in response:
                if not raw.startswith(b"data:"):
                    continue
                now = time.monotonic() - started
                if row["first_frame_s"] is None:
                    row["first_frame_s"] = now
                if last is not None:
                    row["max_gap_s"] = max(row["max_gap_s"], now - last)
                last = now
                row["chunks"] += 1
                data = raw[5:].strip()
                if data == b"[DONE]":
                    row["done"] = True
                    continue
                event = json.loads(data)
                if event.get("error"):
                    raise ValueError("gateway error frame")
                row["usage"] = event.get("usage") or row["usage"]
                for choice in event.get("choices", []):
                    row["finish"] = choice.get("finish_reason") or row["finish"]
                    delta = choice.get("delta") or {}
                    text = delta.get("content") or ""
                    args = "".join((call.get("function") or {}).get("arguments") or ""
                                   for call in delta.get("tool_calls", []))
                    content += text
                    arguments += args
                    if (text or args) and row["first_output_s"] is None:
                        row["first_output_s"] = now
                    if len(content) + len(arguments) >= 100 and row["first_100_output_chars_s"] is None:
                        row["first_100_output_chars_s"] = now
    except (OSError, ValueError) as error:
        row["error"] = type(error).__name__
        row["status"] = getattr(error, "code", row["status"])
    row["total_s"] = time.monotonic() - started
    row["output_chars"] = len(content) + len(arguments)
    row["correct"] = False
    if "tools" in body:
        try:
            result = json.loads(arguments)
            row["correct"] = result.get("path") == "notes.txt" and result.get("content", "").strip() == expected
        except (ValueError, AttributeError):
            pass
    else:
        row["correct"] = content.strip() == expected
    row["ok"] = row["correct"] and row["finish"] in ("stop", "tool_calls") and not row["error"]
    return row


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", type=Path, default=Path(__file__).resolve().parents[1] / "backend/.env")
    parser.add_argument("--models", nargs="+", required=True)
    parser.add_argument("--shape", choices=("text", "tool"), default="tool")
    parser.add_argument("--context-lines", type=int, default=0)
    parser.add_argument("--output-lines", type=int, default=40)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--provider-sort", choices=("latency", "throughput", "price"),
                        help="OpenRouter provider preference; use only with an OpenRouter alias")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.rounds < 1 or args.context_lines < 0 or args.output_lines < 1:
        parser.error("rounds and output-lines must be positive; context-lines must be non-negative")
    env = {}
    for line in args.env.read_text().splitlines():
        if line.strip() and not line.lstrip().startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip().strip('"').strip("'")
    base = env["GATEWAY_BASE_URL"].rstrip("/")
    rows = []
    run_id = uuid.uuid4().hex
    payload = "\n".join(f"Item {i}: north south east west." for i in range(1, args.output_lines + 1))
    for repetition in range(args.rounds):
        for model in args.models:
            prompt = ("Repeat the following lines exactly. No introduction or code fences.\n"
                      if args.shape == "text" else
                      "Call write_file exactly once with path notes.txt and content equal to the "
                      "following lines. No introduction.\n")
            messages = [{"role": "user", "content": prompt + payload +
                         f"\nTest id {run_id}-{repetition}. Do not copy the test id."}]
            if args.context_lines:
                catalog = "\n".join(f"row_{i}: zone=west; amount={i*13}; category=office; active=true;"
                                    for i in range(args.context_lines))
                messages.insert(0, {"role": "system", "content":
                                    "Reference catalog; ignore it for the copy task.\n" + catalog})
            body = {"model": model, "stream": True, "max_tokens": 2000, "messages": messages}
            if args.provider_sort:
                body["provider"] = {"sort": args.provider_sort}
            if args.shape == "tool":
                body["tools"] = [{"type": "function", "function": {
                    "name": "write_file", "description": "Save a text file", "parameters": {
                        "type": "object", "properties": {"path": {"type": "string"},
                        "content": {"type": "string"}}, "required": ["path", "content"]}}}]
            row = {"round": repetition, "shape": args.shape, "provider_sort": args.provider_sort,
                   **probe(base, env["GATEWAY_API_KEY"], body, payload)}
            rows.append(row)
            args.output.write_text(json.dumps({"run_id": run_id, "gateway": base,
                "context_lines": args.context_lines, "output_lines": args.output_lines,
                "rows": rows}, indent=2) + "\n")
            print(json.dumps(row), flush=True)
    return 0 if all(row["ok"] for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
