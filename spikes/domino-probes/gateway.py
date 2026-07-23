"""Phase-0 STEP 0.4 — probe the Domino AI Gateway from inside a workspace.

Closes the gateway-questions.md open items: connectivity + auth, sovereign models present, one
real completion (with X-LLM-Tag-* labels), and the cost/usage response shape.

Set GATEWAY_BASE_URL to the gateway's OpenAI-shape base, e.g. https://<host>/apps/<id>/v1
(see gateway-questions.md / .env.example). Auth defaults to the workspace sidecar JWT; override
with GATEWAY_API_KEY (a dgw_ token) if needed.

Run (in the Domino workspace):
  cd /mnt/code/spikes/domino-probes
  GATEWAY_BASE_URL=https://<host>/apps/<id>/v1 uv run --with httpx gateway.py

Paste the whole output back.
"""
from __future__ import annotations

import os

import httpx

BASE = os.environ.get("GATEWAY_BASE_URL", "").rstrip("/")
SIDECAR = os.environ.get("DOMINO_API_PROXY", "http://localhost:8899").rstrip("/")
MODEL = os.environ.get("SAGE_MODEL", "qwen-2-5")  # sovereign tier default


def token() -> str:
    if os.environ.get("GATEWAY_API_KEY"):
        return f"Bearer {os.environ['GATEWAY_API_KEY']}"
    r = httpx.get(f"{SIDECAR}/access-token", timeout=10)
    r.raise_for_status()
    t = r.text.strip()
    return t if t.lower().startswith("bearer ") else f"Bearer {t}"


def show(label: str, r: httpx.Response) -> None:
    print(f"\n### {label} -> {r.status_code}")
    print(r.text[:1800])


def main() -> None:
    if not BASE:
        raise SystemExit("set GATEWAY_BASE_URL (e.g. https://<host>/apps/<id>/v1)")
    headers = {"Authorization": token(), "Content-Type": "application/json"}
    root = BASE.rsplit("/v1", 1)[0]
    print("gateway base:", BASE, " token prefix:", token()[:16], "…")

    # 1) models — confirms auth + that sovereign models are listed
    try:
        show(f"GET {BASE}/models", httpx.get(f"{BASE}/models", headers=headers, timeout=30))
    except Exception as e:  # noqa: BLE001
        print("models error:", type(e).__name__, e)

    # 2) route discovery — find the usage/cost endpoint from the gateway's own OpenAPI
    for spec in (f"{root}/openapi.json", f"{BASE}/openapi.json"):
        try:
            r = httpx.get(spec, headers=headers, timeout=30)
            if r.status_code == 200 and r.headers.get("content-type", "").startswith("application/json"):
                paths = sorted(r.json().get("paths", {}))
                print(f"\n### {spec} -> 200 ; {len(paths)} paths:")
                for p in paths:
                    print("   ", p)
                break
            print(f"\n### {spec} -> {r.status_code}")
        except Exception as e:  # noqa: BLE001
            print(f"openapi error {spec}:", type(e).__name__, e)

    # 3) completions across a few models to isolate the earlier 502 (provider vs gateway)
    for model in [MODEL, "sonnet", "gpt-5.4"]:
        tagged = {**headers, "X-LLM-Tag-phase": "plan", "X-LLM-Tag-project": os.environ.get("DOMINO_PROJECT_NAME", "Sage")}
        body = {"model": model, "messages": [{"role": "user", "content": "Reply with the single word: ok"}], "max_tokens": 8}
        try:
            show(f"POST {BASE}/chat/completions (model={model})", httpx.post(f"{BASE}/chat/completions", headers=tagged, json=body, timeout=60))
        except Exception as e:  # noqa: BLE001
            print(f"completion error {model}:", type(e).__name__, e)


if __name__ == "__main__":
    main()
