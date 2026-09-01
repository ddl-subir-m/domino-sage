"""A plan's first line was being read as its name, and it was never a name.

From a live run, visible in the right-hand rail. The Built App's row said:

    IN - THIS APP WILL BE AN AI CONSUMPTION DASHBOARD FOR EXPLORING DAILY USAGE, SPEND,

Which is the plan's opening SENTENCE — bullet marker kept because `plan_title` stripped `#` and not
`-`, trailing comma because the cut was a hard 80 characters, shouting because the panel header
capitalises what it is given. The plan card above it showed the same string, so the rail read as two
rows saying one thing twice.

Three surfaces read that line: the plan card, the plan document, and the Built App's display name.
None of them wanted a sentence, and no code can turn one into a name — so the plan shape now asks
the planner for a `# ` heading, and the cleanup below is only what is left for plans drafted before
it did.
"""
from __future__ import annotations

from sage.orchestrator.handoff import plan_title
from sage.orchestrator.service import _PLAN_SHAPE
from sage.workspace import plan_doc

# What the live planner wrote, exactly.
LIVE = ("- This app will be an AI consumption dashboard for exploring daily usage, spend, and "
        "model activity across teams and users.\n\n## Problem & outcome\n\nToday there is no app.\n")

NAMED = "# Usage Explorer\n\nThis app reads daily usage.\n\n## Problem & outcome\n\nNo app today.\n"


# ---- the plan says its own name --------------------------------------------------------------------


def test_the_shape_asks_for_a_name_before_it_asks_for_anything_else():
    assert "A '# ' heading naming the app in 2-4 words" in _PLAN_SHAPE
    assert _PLAN_SHAPE.index("'# ' heading") < _PLAN_SHAPE.index("## Problem & outcome")


def test_the_shape_says_what_a_name_is_not():
    """The planner's default is a description. It has to be told the difference."""
    assert "never a sentence" in _PLAN_SHAPE
    assert "No leading 'A' or 'The'" in _PLAN_SHAPE


def test_the_example_name_does_not_teach_the_control_rule_the_wrong_key():
    """ADR-0016's rule keys on the data's shape, never on what an app is called. A noun in an
    example is the easiest way to teach the opposite (test_a_dashboard_ships_with_a_control)."""
    assert "dashboard" not in _PLAN_SHAPE.lower()


def test_a_heading_is_the_name():
    assert plan_title(NAMED) == "Usage Explorer"


# ---- and when it does not, the sentence is at least presentable -------------------------------------


def test_the_bullet_marker_is_not_part_of_the_name():
    assert not plan_title(LIVE).startswith("-")


def test_the_name_is_whole_words_with_nothing_hanging_off_the_end():
    title = plan_title(LIVE)
    sentence = LIVE.split("\n")[0].lstrip("- ")
    assert sentence.startswith(title)
    assert not title.endswith(",")
    # The 80-character cut could land inside a word. The next character proves it did not.
    assert not sentence[len(title):len(title) + 1].isalpha()


def test_the_live_name_is_the_one_that_was_in_the_rail_minus_the_damage():
    # Still a sentence, and still not a name — that is what the `# ` heading is for. What is gone
    # is the bullet marker, the mid-clause comma and the cut landing inside a word.
    assert plan_title(LIVE) == (
        "This app will be an AI consumption dashboard for exploring daily usage, spend")


def test_a_plan_with_nothing_in_it_is_still_called_something():
    assert plan_title("") == "App"
    assert plan_title("- \n\n") == "App"


# ---- the document keeps the name it was given -------------------------------------------------------


def test_the_title_heading_is_a_title_and_not_stray_prose():
    """Unparsed, a `# ` heading fell through to `unknown` and was appended to the Plan section —
    so asking for one without teaching the parser would have put the name inside the plan."""
    parsed = plan_doc.parse_sections(NAMED)
    assert parsed["title"] == "Usage Explorer"
    assert "Usage Explorer" not in parsed["sections"]["plan"]
    assert parsed["summary"] == "This app reads daily usage."


def test_a_plan_that_opens_on_a_sentence_has_no_title_and_keeps_its_summary():
    parsed = plan_doc.parse_sections(LIVE)
    assert parsed["title"] == ""
    assert parsed["summary"].startswith("- This app will be")


def test_a_hash_heading_further_down_is_not_the_title():
    parsed = plan_doc.parse_sections("Opening line.\n\n# Later\n\nbody\n")
    assert parsed["title"] == ""


def test_a_known_section_written_at_level_one_is_still_that_section():
    parsed = plan_doc.parse_sections("# Problem & outcome\n\nToday there is no app.\n")
    assert parsed["title"] == ""
    assert parsed["sections"]["problem"] == "Today there is no app."


def test_the_name_survives_an_edit_to_some_other_section():
    """The round trip is this module's whole contract. Parsed out and not written back, the name
    would last until the first edit and the builder's copy would fall back to the first sentence."""
    parsed = plan_doc.parse_sections(NAMED)
    again = plan_doc.render(parsed["summary"], parsed["sections"], parsed["title"])
    assert plan_doc.parse_sections(again)["title"] == "Usage Explorer"
    assert plan_title(again) == "Usage Explorer"


def test_render_without_a_title_is_unchanged():
    """Every existing caller passes two arguments and must read exactly as it did."""
    sections = plan_doc.empty_sections()
    sections["problem"] = "No app today."
    assert not plan_doc.render("A summary.", sections).startswith("#")
