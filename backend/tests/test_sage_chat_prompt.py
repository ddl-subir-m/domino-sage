import json
from pathlib import Path


def test_sage_chat_prompt_is_the_agents_md_file():
    root = Path(__file__).resolve().parents[2]
    prompt = (root / "template" / "chat" / "AGENTS.md").read_text()
    cfg = json.loads((root / "opencode.json").read_text())
    assert cfg["agent"]["sage-chat"]["prompt"] == prompt
    assert cfg["agent"]["sage-chat"]["permission"]["edit"] == "allow"
    assert cfg["agent"]["sage-chat"]["permission"]["bash"] == "allow"
