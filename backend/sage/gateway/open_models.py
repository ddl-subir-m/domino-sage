"""Static catalog of popular open-weight models for openai gateway mode.

Domino gateway mode has one endpoint/one key — the gateway owns model routing. openai mode has
no such intermediary, so each model here carries its own base_url + api-key env var; the client
looks these up per request.model instead of a single global GATEWAY_BASE_URL/GATEWAY_API_KEY.

Confirmed via MODELS.md / gateway-questions.md: no provider here exposes a queryable /v1/models
list we could fetch instead — this list is maintained by hand.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OpenModel:
    id: str
    provider: str
    base_url: str
    api_key_env: str


OPEN_WEIGHT_MODELS: list[OpenModel] = [
    OpenModel("deepseek-v4-flash", "DeepSeek", "https://api.deepseek.com/v1", "DEEPSEEK_API_KEY"),
    OpenModel("deepseek-v4-pro", "DeepSeek", "https://api.deepseek.com/v1", "DEEPSEEK_API_KEY"),
    OpenModel("qwen3.7-max", "Qwen", "https://dashscope.aliyuncs.com/compatible-mode/v1", "QWEN_API_KEY"),
    OpenModel("qwen3.7-plus", "Qwen", "https://dashscope.aliyuncs.com/compatible-mode/v1", "QWEN_API_KEY"),
    OpenModel("qwen3.6-flash", "Qwen", "https://dashscope.aliyuncs.com/compatible-mode/v1", "QWEN_API_KEY"),
    OpenModel("kimi-k3", "Moonshot Kimi", "https://api.moonshot.ai/v1", "MOONSHOT_API_KEY"),
    OpenModel("kimi-k2.7-code", "Moonshot Kimi", "https://api.moonshot.ai/v1", "MOONSHOT_API_KEY"),
    OpenModel("kimi-k2.7-code-highspeed", "Moonshot Kimi", "https://api.moonshot.ai/v1", "MOONSHOT_API_KEY"),
]


def by_id(model_id: str) -> OpenModel | None:
    return next((m for m in OPEN_WEIGHT_MODELS if m.id == model_id), None)
