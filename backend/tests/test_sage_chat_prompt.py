import json
from pathlib import Path

from sage.driver.opencode import with_attachment_listing


def test_sage_chat_prompt_is_the_agents_md_file():
    root = Path(__file__).resolve().parents[2]
    prompt = (root / "template" / "chat" / "AGENTS.md").read_text()
    cfg = json.loads((root / "opencode.json").read_text())
    assert cfg["agent"]["sage-chat"]["prompt"] == prompt
    assert cfg["agent"]["sage-chat"]["permission"]["edit"] == "allow"
    assert cfg["agent"]["sage-chat"]["permission"]["bash"] == "allow"


def test_chat_attachment_listing_tells_the_agent_to_read_the_file():
    out = with_attachment_listing(
        "what data is there in @desk.csv",
        [{"name": "desk.csv", "path": ".sage/scratch/desk.csv",
          "summary": "2 rows", "detail": ""}],
        chat=True,
    )
    assert "what data is there in @desk.csv" in out
    assert ".sage/scratch/desk.csv" in out
    assert "read the file at the path shown" in out
    assert "built app MUST" not in out


def test_build_attachment_listing_still_warns_not_to_copy_into_src():
    out = with_attachment_listing(
        "use this csv",
        [{"name": "desk.csv", "path": "public/data/x/uploads/desk.csv",
          "summary": "2 rows", "detail": ""}],
    )
    assert "built app MUST" in out
