"""Preflight probe: one real chat completion through the gateway client.

Confirms the provider is reachable and the key/mode is valid BEFORE involving OpenCode, so a
failed live build can be diagnosed as provider-vs-loop. Reads the same env as the apps.

Run:  uv run python -m sage.tools.probe
Env:  SAGE_PROBE_MODEL overrides the model (default: SAGE_MODEL_ASK / "sonnet").
"""
from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()

from ..gateway.client import CostLabels, GatewayUpstreamError
from ..gateway.factory import build_gateway


def main() -> int:
    client, mode = build_gateway()
    model = os.environ.get("SAGE_PROBE_MODEL") or os.environ.get("SAGE_MODEL_ASK", "sonnet")
    print(f"gateway_mode={mode}  model={model}")
    if mode == "fake":
        print("NOTE: fake mode — set GATEWAY_BASE_URL (+ key) for a real probe.")

    req = {
        "model": model,
        "messages": [{"role": "user", "content": "Reply with the single word: ok"}],
        "max_tokens": 16,
        "stream": True,
    }
    labels = CostLabels(phase="plan", mode="auto", component="probe")
    try:
        data = b"".join(client.route(req, labels))
        print("OK — response bytes:", len(data))
        print(data.decode(errors="replace")[:400])
        return 0
    except GatewayUpstreamError as e:
        print(f"UPSTREAM {e.status}: {e.body[:400]}")
        return 1
    except Exception as e:  # noqa: BLE001 - surface any failure for diagnosis
        print(f"ERROR {type(e).__name__}: {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
