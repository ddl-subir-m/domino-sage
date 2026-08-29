"""#123 — the contract half of the overlay: no bare name survives at a marked position.

ADR-0014 rejects a grep over the source, because a grep fails the moment somebody writes a
code comment and a rule nobody can live with gets turned off. What replaces it scans call
sites — `detail=` on an `HTTPException`, `brand.text()`, `SW.brand.*` — so a comment, a
docstring and a variable name are invisible to it. That property is asserted here as
hard as the failures are, because it is the one that keeps the lint switched on.

The other half is the computation: a `CONTEXT.md` term needs a noun key **iff** a marked
position names it, which a marked position does by writing its token. Nobody maintains a
list of which terms those are, so nothing can drift from what the UI actually says.
"""
from __future__ import annotations

import copy
from pathlib import Path

import pytest

from sage.orchestrator import brand
from sage.tools import brand_lint as lint


def _tree(tmp_path: Path, **files: str) -> Path:
    """A source tree of one or two files. A bare name is Python; `**{"a.js": …}` is not."""
    for name, body in files.items():
        (tmp_path / (name if "." in name else name + ".py")).write_text(body)
    return tmp_path


def _rules(findings) -> list[str]:
    return [f.rule for f in findings]


# --------------------------------------------------------------- it blocks the build


def test_the_lint_passes_on_the_tree_as_it_stands():
    """The contract step lands only once every migrate batch has. This is that check, and
    it is what blocks CI from the next string that forgets."""
    assert [str(f) for f in lint.run()] == []


def test_the_lint_reads_every_marked_position_in_the_tree():
    """A lint that found nothing would also pass. The three positions are covered, and the
    two that carry strings today carry a lot of them."""
    seen = {m.position for m in lint.marked_strings(lint.SOURCE)}
    assert "brand.text()" in seen
    assert "SW.brand.text()" in seen
    assert len(lint.marked_strings(lint.SOURCE)) > 200


@pytest.mark.parametrize("word", ["Sage", "Domino", "ML Studio"])
def test_a_bare_brand_word_fails_at_the_python_helper(tmp_path, word):
    tree = _tree(tmp_path, a=f'from x import brand\nbrand.text("{word} did it.")\n')
    found = lint.run(tree)
    assert _rules(found) == ["bare-name"]
    assert word in found[0].message


@pytest.mark.parametrize("word", ["Sage", "Domino", "ML Studio"])
def test_a_bare_brand_word_fails_in_an_http_exception_detail(tmp_path, word):
    tree = _tree(
        tmp_path,
        a=f'raise HTTPException(status_code=400, detail="{word} refused this.")\n',
    )
    found = lint.run(tree)
    assert _rules(found) == ["bare-name"]
    assert "HTTPException(detail=)" in found[0].message


@pytest.mark.parametrize("word", ["Sage", "Domino", "ML Studio"])
def test_a_bare_brand_word_fails_in_the_workbench(tmp_path, word):
    tree = _tree(tmp_path, **{"a.js": f"SW.brand.text('{word} did it.');\n"})
    found = lint.run(tree)
    assert _rules(found) == ["bare-name"]
    assert "SW.brand.text()" in found[0].message


def test_the_advice_names_the_token_to_write_instead():
    """A refusal that does not say what to write instead is a refusal people route around."""
    phrases = lint.forbidden_phrases()
    assert phrases["Sage"] == "write {assistantName}"
    assert phrases["Domino"] == "write {platformName}"
    assert phrases["Data Sources"] == "write {dataSourcePlural}"
    # A peer product is drawn from a list, so there is no token to point at.
    assert "peerProducts" in phrases["ML Studio"]


def test_the_forbidden_words_come_out_of_the_pack_not_a_list():
    """A name the pack learns to rename is refused the same day, with nothing edited here."""
    pack = copy.deepcopy(brand.DEFAULT)
    pack["nouns"]["scope"] = {"singular": "Scope", "plural": "Scopes"}
    marked = [lint.Marked("a.py", 1, "brand.text()", "Pick a Scope.", frozenset())]
    assert _rules(lint.findings(marked, pack=pack)) == ["bare-name"]
    assert lint.findings(marked) == []


def test_a_mapped_noun_written_bare_fails(tmp_path):
    """The noun map is the whole point: `Data Source` on screen is a word a partner cannot
    reach, and Domino's own whitelabel has already renamed it."""
    tree = _tree(tmp_path, a='from x import brand\nbrand.text("Pick a Data Source.")\n')
    found = lint.run(tree)
    assert _rules(found) == ["bare-name"]
    assert "{dataSource}" in found[0].message


def test_a_url_in_the_pack_is_not_a_word_anybody_writes(tmp_path):
    """`logoUrl` is an identifier, not prose (ADR-0014's third arm), so its value is not a
    phrase this refuses."""
    assert "./img/domino-logo.svg" not in lint.forbidden_phrases()


# --------------------------------------------------------------- a comment is invisible


def test_a_python_comment_and_docstring_are_invisible(tmp_path):
    tree = _tree(
        tmp_path,
        a=(
            '"""Sage talks to Domino about Data Sources and ML Studio."""\n'
            "from x import brand\n"
            "# Domino renames its own Datasets; Sage agrees with it.\n"
            'SAGE_NOTE = "Domino"  # a name nobody reads is not a marked position\n'
            'brand.text("{assistantName} asked {platformName} for the {dataSourcePlural}.")\n'
        ),
    )
    assert lint.run(tree) == []


def test_a_js_comment_is_invisible(tmp_path):
    tree = _tree(
        tmp_path,
        **{
            "a.js": (
                "// Sage and Domino and the Data Sources this renames.\n"
                "/* Domino, ML Studio, Built Apps — none of this reaches a person. */\n"
                "SW.brand.text('{assistantName} asked {platformName}.');\n"
            )
        },
    )
    assert lint.run(tree) == []


def test_a_slash_in_a_string_does_not_open_a_comment(tmp_path):
    tree = _tree(
        tmp_path,
        **{"a.js": "SW.brand.text('https://example.com/{path}', { path });\n"},
    )
    assert lint.run(tree) == []


def test_a_regex_literal_does_not_open_a_string(tmp_path):
    """`/['\"]/` would leave the masker inside a string for the rest of the file, and every
    call site after it would vanish — a lint that silently stops looking is worse than none."""
    tree = _tree(
        tmp_path,
        **{"a.js": "const q = /['\\\"]/g;\nSW.brand.text('Domino did it.');\n"},
    )
    assert _rules(lint.run(tree)) == ["bare-name"]


def test_a_call_inside_a_template_hole_is_still_a_call(tmp_path):
    """The Workbench writes `${peer.label} ${SW.brand.text('…')}` — a marked position that
    lives inside a template literal, which a masker treating the whole template as one
    opaque run would never see."""
    tree = _tree(
        tmp_path,
        **{"a.js": "const s = `${peer.label} ${SW.brand.text('Domino did it.')}`;\n"},
    )
    assert _rules(lint.run(tree)) == ["bare-name"]


def test_a_nested_template_does_not_lose_the_rest_of_the_file(tmp_path):
    """`#/build${t ? `/${t.id}` : ''}` is in the store today. Reading the inner backtick as
    the closing one puts the scan inside a string for everything after it, and every call
    site below it disappears without a word."""
    tree = _tree(
        tmp_path,
        **{
            "a.js": (
                "SW.router.go(`#/build${t ? `/${t.id}` : ''}`);\n"
                "SW.brand.text('Domino did it.');\n"
            )
        },
    )
    assert _rules(lint.run(tree)) == ["bare-name"]


def test_a_template_literal_written_at_a_marked_position_is_read(tmp_path):
    tree = _tree(tmp_path, **{"a.js": "SW.brand.text(`Domino did it.`);\n"})
    assert _rules(lint.run(tree)) == ["bare-name"]


def test_a_name_in_the_source_is_not_a_marked_position(tmp_path):
    """Identifiers stay (ADR-0014). `.sage/`, `sage-chat` and `DOMINO_API_HOST` are not
    prose, and a lint that refused them would be refusing the design."""
    tree = _tree(
        tmp_path,
        a=(
            "from x import brand\n"
            "DOMINO_API_HOST = 'x'\n"
            "path = '.sage/history.jsonl'\n"
            "agent = 'sage-chat'\n"
            'brand.text("{assistantName} wrote the file.")\n'
        ),
    )
    assert lint.run(tree) == []


# --------------------------------------------------------------- which terms need a key


def test_the_terms_needing_a_key_are_computed_from_what_the_ui_says():
    """Not a maintained list: the answer is whichever glossary terms the marked positions
    name, so it cannot drift from the copy."""
    needed = lint.terms_needing_a_key(lint.marked_strings(lint.SOURCE))
    assert set(needed.values()) == {
        "Data Source",
        "Model API",
        "LLM Alias",
        "Built App",
        "Gallery",
    }
    assert set(needed) <= set(brand.DEFAULT["nouns"])


@pytest.mark.parametrize(
    "term", ["AI Gateway", "Domino Artifacts", "Hosted GenAI Endpoint"]
)
def test_a_glossary_only_term_needs_no_key_and_does_not_fail(term):
    """These exist so the LLM Gateway and a Sage Artifact are not mistaken for something
    else. Nothing renames them, so the pack owes them nothing."""
    assert term in lint.glossary_terms()
    assert lint.key_for(term) not in lint.terms_needing_a_key(lint.marked_strings(lint.SOURCE))
    assert lint.key_for(term) not in brand.DEFAULT["nouns"]


def test_a_glossary_term_newly_used_fails_until_it_has_a_noun_key(tmp_path):
    """`Binding` is in `CONTEXT.md` and has no key. Naming it in copy is what makes the key
    owed, and the lint says so by name until the pack grows one."""
    tree = _tree(tmp_path, a='from x import brand\nbrand.text("That {binding} is gone.")\n')
    found = lint.run(tree)
    assert _rules(found) == ["missing-noun-key"]
    assert "Binding" in found[0].message

    with_key = copy.deepcopy(brand.DEFAULT)
    with_key["nouns"]["binding"] = {"singular": "Binding", "plural": "Bindings"}
    assert lint.findings(lint.marked_strings(tree), pack=with_key) == []


def test_a_token_that_is_nobodys_key_reads_as_braces(tmp_path):
    """A typo is not a glossary term, and the message says so differently — what a person
    would read is `{assistantNmae}`, literally."""
    tree = _tree(tmp_path, a='from x import brand\nbrand.text("Ask {assistantNmae}.")\n')
    found = lint.run(tree)
    assert _rules(found) == ["unknown-token"]


def test_a_token_the_call_site_fills_is_not_owed_a_key(tmp_path):
    """`{name}` is the sentence's own hole, filled from the turn. The helper does not scan
    what fills it, and neither does this."""
    tree = _tree(
        tmp_path,
        a='from x import brand\nbrand.text("{name} is gone.", name="x")\n',
        **{"b.js": "SW.brand.text('{count} left', { count: 2 });\n",
           "c.js": "const query = 'x';\nSW.brand.text('No match for {query}.', { query });\n"},
    )
    assert lint.run(tree) == []


def test_a_bare_detail_resolves_nothing(tmp_path):
    """`detail=` is not a template. A token written there is two braces on screen, which is
    the failure the paranoid pack would only catch at boot."""
    tree = _tree(
        tmp_path, a='raise HTTPException(status_code=400, detail="Ask {assistantName}.")\n'
    )
    found = lint.run(tree)
    assert _rules(found) == ["unresolved-token"]
    assert "brand.text()" in found[0].message


def test_a_detail_that_goes_through_the_helper_is_read_once(tmp_path):
    tree = _tree(
        tmp_path,
        a=(
            "from x import brand\n"
            'raise HTTPException(status_code=400, detail=brand.text("Ask {assistantName}."))\n'
        ),
    )
    assert lint.run(tree) == []


# --------------------------------------------------------------- it finds the call sites


def test_the_helper_is_found_under_either_spelling(tmp_path):
    """Both are in the tree — `from . import brand` and `from .brand import text as
    brand_text` — so the imports are read rather than assumed."""
    tree = _tree(
        tmp_path,
        a="from .brand import text as brand_text\nbrand_text('Domino did it.')\n",
        b="from . import brand as pack\npack.text('Domino did it.')\n",
    )
    assert _rules(lint.run(tree)) == ["bare-name", "bare-name"]


def test_a_text_call_on_something_else_is_not_a_marked_position(tmp_path):
    """`r.text()` is an HTTP response body, not a sentence anybody wrote."""
    tree = _tree(tmp_path, a='r.text("Domino answered")\n')
    assert lint.run(tree) == []


def test_a_sentence_hoisted_into_a_constant_is_still_read(tmp_path):
    """The one way past this that somebody would reach for by accident."""
    tree = _tree(
        tmp_path,
        a='from x import brand\nNUDGE = "Domino refused."\nbrand.text(NUDGE)\n',
    )
    assert _rules(lint.run(tree)) == ["bare-name"]


def test_both_arms_of_a_conditional_template_are_read(tmp_path):
    tree = _tree(
        tmp_path,
        a=(
            "from x import brand\n"
            'brand.text("{assistantName} is fine." if ok else "Domino refused.")\n'
        ),
    )
    assert _rules(lint.run(tree)) == ["bare-name"]
