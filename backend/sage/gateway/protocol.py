"""Wire protocols at the configured LLM Gateway, never vendor destinations."""
from enum import StrEnum


class Protocol(StrEnum):
    CHAT = "chat"
    MESSAGES = "messages"
    RESPONSES = "responses"


def endpoint(base_url: str, protocol: Protocol) -> str:
    root = base_url.rstrip("/").removesuffix("/v1")
    path = {
        Protocol.CHAT: "/v1/chat/completions",
        Protocol.MESSAGES: "/anthropic/v1/messages",
        Protocol.RESPONSES: "/v1/responses",
    }[protocol]
    return root + path
