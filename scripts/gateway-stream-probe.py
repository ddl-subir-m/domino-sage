#!/usr/bin/env python3
"""Gateway cut probe v6 — max inter-chunk GAP, under concurrency, with a tool call.

The model, after v5:
  The gateway's upstream connection has a ~60s READ timeout that every chunk resets.
    non-streaming 2400 words -> one silent 87s read -> 3 attempts x 60s -> 502 at 180.5s
    streaming     2400 words -> same 87s of work, chunks throughout  -> ok
  So a stream dies only when ONE GAP between chunks passes the timeout. That needs load.

v6 measures the gap (v3-v5 forgot to), generates the load itself, and flags the exact
205 signature: a stream that ends with no finish_reason and no [DONE].

Known limitation: GAP is the silence BETWEEN chunks. The silence between the LAST chunk
and the socket closing is never measured, so a stream that stalls and then dies reports a
small GAP. `sonnet` read GAP=0.4s on a 302.8s wall; its real stall was ~297s. Read GAP
together with `wall` and `chunks`, never alone.

Usage:
  python gw_probe6.py                      # ramp 1 -> 4 -> 8 concurrent, with tool call
  python gw_probe6.py --levels 8 --rounds 3
  python gw_probe6.py --no-tools           # plain text generation instead
"""
import argparse, json, os, queue, random, string, sys, threading, time
import urllib.request, urllib.error

BASE = os.environ.get("GATEWAY_BASE_URL",
                      "https://apps.cloud-dogfood.domino.tech/apps/llm_gateway/v1")
TOKEN_URL = os.environ.get("GATEWAY_TOKEN_URL", "http://localhost:8899/access-token")
CLIENT_TIMEOUT = 600

WRITE_TOOL = [{"type": "function", "function": {
    "name": "write_file",
    "description": "Write a file to disk.",
    "parameters": {"type": "object", "required": ["path", "content"], "properties": {
        "path": {"type": "string"}, "content": {"type": "string"}}}}}]


def token() -> str:
    key = os.environ.get("GATEWAY_API_KEY", "").strip()
    if key:
        return key
    with urllib.request.urlopen(TOKEN_URL, timeout=10) as r:
        return r.read().decode().strip().removeprefix("Bearer ")


def filler(n_words: int) -> str:
    rnd = random.Random()
    return "\n".join(
        f"{i:05d} " + " ".join("".join(rnd.choices(string.ascii_lowercase, k=rnd.randint(3, 8)))
                               for _ in range(8))
        for i in range(1, n_words // 8 + 1))


def one(model, n_words, max_out, use_tools, out: queue.Queue, idx: int) -> None:
    """One streaming request. Records the max gap between chunks — the number that decides
    whether the gateway's ~60s upstream read timer ever gets close to firing."""
    body = {"model": model, "stream": True, "max_tokens": max_out}
    text = filler(n_words)
    if use_tools:
        body["tools"] = WRITE_TOOL
        body["tool_choice"] = "auto"
        body["messages"] = [{"role": "user", "content":
                             "Call write_file once. Set path to notes.txt. Set content to the "
                             "following lines, verbatim and complete, every line.\n\n" + text}]
    else:
        body["messages"] = [{"role": "user", "content":
                             "Repeat the following lines verbatim and in full.\n\n" + text}]

    req = urllib.request.Request(
        f"{BASE}/chat/completions", data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {token()}", "Content-Type": "application/json",
                 "Accept": "text/event-stream", "X-LLM-Tag-sage-probe": "gw-cut-205-v6"})

    t0 = time.monotonic()
    ttfb = None; last = t0; gap = 0.0; chunks = 0
    done = False; fin = ""; nbytes = 0
    try:
        with urllib.request.urlopen(req, timeout=CLIENT_TIMEOUT) as resp:
            status = resp.status
            for raw in resp:                       # line-wise: one SSE frame at a time
                now = time.monotonic()
                if ttfb is None:
                    ttfb = now - t0
                else:
                    gap = max(gap, now - last)     # <-- THE number
                last = now
                if not raw.strip():
                    continue
                chunks += 1; nbytes += len(raw)
                if b"[DONE]" in raw:
                    done = True
                elif b'"finish_reason"' in raw:
                    i = raw.rfind(b'"finish_reason"')
                    v = raw[i + 16:i + 40].lstrip(b': ').split(b',')[0].strip(b'" }]')
                    if v and v != b"null":
                        fin = v.decode(errors="replace")
        # The 205 signature, straight out of shim/keepalive.py: chunks arrived, then the
        # gateway simply stopped. No terminal finish_reason, no [DONE], nothing raised.
        if chunks and not done and not fin:
            outcome = "*** 205 SIGNATURE: stream ended, no finish_reason, no [DONE] ***"
        elif not chunks:
            outcome = "empty stream (pre-stream failure wearing a 200)"
        elif not done:
            outcome = f"no [DONE] but finish_reason={fin}"
        else:
            outcome = "ok"
    except urllib.error.HTTPError as e:
        status, outcome = e.code, f"HTTP {e.code}: " + e.read()[:90].decode(errors="replace")
    except Exception as e:
        status, outcome = None, f"BROKE: {type(e).__name__}: {e}"

    out.put({"i": idx, "wall": round(time.monotonic() - t0, 1),
             "ttfb": round(ttfb, 1) if ttfb else None, "gap": round(gap, 1),
             "chunks": chunks, "kb": nbytes // 1024, "fin": fin or "-",
             "status": status, "outcome": outcome})


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=os.environ.get("PROBE_MODEL", "bedrock-qwen3-coder"))
    ap.add_argument("--words", type=int, default=2400)
    ap.add_argument("--max-out", type=int, default=32000)
    ap.add_argument("--levels", type=int, nargs="+", default=[1, 4, 8])
    ap.add_argument("--rounds", type=int, default=1)
    ap.add_argument("--no-tools", action="store_true")
    args = ap.parse_args()

    print(f"base={BASE}\nmodel={args.model}  words={args.words}  "
          f"tools={'off' if args.no_tools else 'ON'}\n")
    print("GAP = longest silence BETWEEN chunks. The upstream read timer is ~60s and every")
    print("chunk resets it. A GAP near 60 is the fault about to happen.\n")

    all_rows, hits = [], []
    for rnd_i in range(1, args.rounds + 1):
        for n in args.levels:
            print(f"=== round {rnd_i}  concurrency {n} ===")
            print(f"{'req':>4}{'wall':>8}{'ttfb':>7}{'GAP':>7}{'chunks':>8}"
                  f"{'KB':>6}{'fin':>12}{'stat':>6}  outcome")
            q: queue.Queue = queue.Queue()
            ts = [threading.Thread(target=one, args=(args.model, args.words, args.max_out,
                                                     not args.no_tools, q, i), daemon=True)
                  for i in range(n)]
            for t in ts:
                t.start()
            for t in ts:
                t.join()
            rows = sorted((q.get() for _ in range(n)), key=lambda r: r["i"])
            for r in rows:
                all_rows.append(r)
                if "205 SIGNATURE" in r["outcome"]:
                    hits.append(r)
                print(f"{r['i']:>4}{r['wall']:>8}{str(r['ttfb']):>7}{r['gap']:>7}"
                      f"{r['chunks']:>8}{r['kb']:>6}{r['fin']:>12}{str(r['status']):>6}"
                      f"  {r['outcome']}")
            print()

    gaps = sorted(r["gap"] for r in all_rows if r["gap"])
    print("--- read this ---")
    print(f"requests={len(all_rows)}  ok={sum(1 for r in all_rows if r['outcome'] == 'ok')}  "
          f"205-signature={len(hits)}")
    if gaps:
        print(f"GAP  max={gaps[-1]}s  median={gaps[len(gaps)//2]}s")
        if gaps[-1] >= 45:
            print("!! A gap is within striking distance of the 60s upstream read timeout.")
            print("   Raise --levels to push it over and reproduce on demand.")
        else:
            print(f"   Max gap {gaps[-1]}s is far from 60s. This load is not enough.")
            print("   Raise --levels (try 16, 24) or run while a real build is going.")
    if hits:
        print(f"\nREPRODUCED {len(hits)}x. Gaps on those: "
              f"{[h['gap'] for h in hits]}, chunks: {[h['chunks'] for h in hits]}")
        print("That is the exact fault shim/keepalive.py warns about.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
