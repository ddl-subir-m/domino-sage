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


def test_sage_chat_prompt_teaches_visuals_without_being_asked():
    prompt = (Path(__file__).resolve().parents[2] / "template" / "chat" / "AGENTS.md").read_text()
    assert "They do not have to ask for a visual" in prompt
    assert "heatmap PNG" in prompt
    assert "ax.imshow" in prompt
    assert "greeting, thanks" in prompt


def test_sage_chat_prompt_names_the_pandas_shape_that_breaks_a_table():
    """Stating the `{title, columns, rows}` schema was not enough on its own: a turn asked what was
    in an uploaded forecast JSON reached for the pandas one-liner, wrote a bare record array, and
    the card showed "No data" beside a chart that had plotted the same rows. Naming the two idioms
    that produce that file, and the one that does not, is the part that was missing."""
    prompt = (Path(__file__).resolve().parents[2] / "template" / "chat" / "AGENTS.md").read_text()
    assert 'df.to_json(orient="records")' in prompt
    assert 'json.dump(df.to_dict("records"), f)' in prompt
    assert "df.values.tolist()" in prompt
    assert "positional array" in prompt


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
