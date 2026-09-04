"""An `@` token names ONE file, or the turn says what else it matched (ADR-0030).

WHAT WAS WRONG. An `@` token was a basename. With `raw/2025/data.csv` and `raw/2026/data.csv` both
attached, `@data.csv` matched both rows and the server honored both: two descriptors inlined, one
file asked for. This was always possible and always rare, because attaching two files with one
basename took two deliberate clicks in two folders. ADR-0029 makes it routine — a partitioned folder
is what a recursive attach is FOR, and identical basenames under date partitions are its normal
shape.

The picker could not repair it either, which is the worse half. `workingSetFirst` matched on the
basename only, so typing `2026` reached nothing for `raw/2026/data.csv`; and the row drew a name and
a kind caption, so two colliding files drew two identical rows that inserted the same text. Filter
without path means typing `2026` returns two rows still indistinguishable; path without filter means
seeing the difference and being unable to narrow to it. So both change.

THE THIRD CASE, and the one this exists to prevent. Unique tokens alone leave a hole: text already
sitting in the composer keeps the token it was GIVEN. If `@data.csv` was unique when it was typed
and a later attach makes it ambiguous, both rows now answer `@2026/data.csv`, neither matches the
box, and the mention silently carries nothing. So a stale ambiguous token carries ALL its matches
and the turn reports that it did. Silence is the one outcome ruled out.

THE READ. `mention_ambiguity_harness.mjs` moves into a Project whose app holds a partitioned folder,
then reads four surfaces off that one arrival: what the query can reach, what each row says it is,
what the picker writes into the box, and what a real Build turn carries.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from sage.orchestrator.service import _ambiguous_mentions, _at_token_hits, _mentioned_in

_HARNESS = Path(__file__).resolve().parent / "js" / "mention_ambiguity_harness.mjs"
WB = Path(__file__).resolve().parents[1] / "sage" / "workbench"

needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is not on PATH (it is in the Sage image)"
)

# The app's three Attachments, as the harness fixture writes them.
P2025 = "public/data/sales/raw/2025/data.csv"
P2026 = "public/data/sales/raw/2026/data.csv"
SUMMARY = "public/data/sales/summary.csv"

# One prompt per case, in the order the harness sends them.
QUALIFIED = "chart the trend from @2026/data.csv"
STALE = "chart the trend from @data.csv"
UNIQUE = "chart the trend from @summary.csv"
PROMPTS = [QUALIFIED, STALE, UNIQUE]


def _run() -> dict:
    out = subprocess.run(
        ["node", str(_HARNESS)],
        input=json.dumps({"prompts": PROMPTS}),
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


# ---- the token is generated, so the token can be made unique ------------------------------------


@needs_node
def test_a_colliding_basename_is_inserted_as_the_shortest_distinguishing_suffix():
    """The menu INSERTS the token, so making it unique costs no rule anybody has to learn: a person
    types `@`, narrows, and picks. It is the SHORTEST distinguishing suffix and not the whole path —
    `public/data/sales/raw/` is four segments of plumbing that tell the two files nothing apart."""
    report = _run()

    assert report["inserted2026"] == "@2026/data.csv"
    assert report["inserted2025"] == "@2025/data.csv"


@needs_node
def test_a_basename_that_stands_alone_keeps_the_plain_token():
    """The fallback is for a collision, not for every file. A Project where nothing collides must
    read exactly as it did before this shipped."""
    report = _run()

    assert report["insertedUnique"] == "@summary.csv"


@needs_node
def test_uniqueness_is_computed_against_the_app_s_own_attachments():
    """`notes.csv` stands as a Project Upload and as an Attachment it crossed into (#147). That
    collision is a different question with a different answer — two scopes, two records, one file —
    and this rule is not allowed to reach it and rename one of the two."""
    report = _run()

    assert report["insertedUpload"] == "@notes.csv"


# ---- the picker has to be able to reach the file it is disambiguating ---------------------------


@needs_node
def test_the_query_matches_the_whole_relative_path():
    """Neither half of this works without the other. On the basename alone, `2026` reached nothing
    for `raw/2026/data.csv` — so the one file a person could see two rows of was the one file they
    could not narrow to."""
    report = _run()

    assert [row["name"] for row in report["menuPartition"]] == ["data.csv"]
    assert [row["title"] for row in report["menuPartition"]] == [P2026]
    # A slice of the folder path above it reaches the same row, because the match is on the path
    # rather than on any one segment of it.
    assert [row["title"] for row in report["menuFolderPath"]] == [P2026]


@needs_node
def test_the_basename_still_reaches_both_rows():
    """The widening ADDS reach. Typing the name a person knows must still find every file wearing
    it — that is how they discover there are two."""
    report = _run()

    assert [row["title"] for row in report["menuBasename"]] == [P2025, P2026]


@needs_node
def test_two_colliding_rows_say_which_folder_they_came_from():
    """Two identical rows — same icon, same label, same caption — that insert the same text is the
    half of this defect a unique token cannot reach: the right file could not be SEEN, let alone
    picked. The folder goes in the caption slot the row already has, and the whole path in `title`
    the way `LeafRow` does it in the Dataset tree."""
    report = _run()

    assert [row["captions"] for row in report["menuBasename"]] == [["2025"], ["2026"]]
    assert [row["title"] for row in report["menuBasename"]] == [P2025, P2026]


@needs_node
def test_a_row_with_nothing_to_disambiguate_keeps_its_kind_caption():
    """The folder replaces the kind rather than joining it — two captions are one more than the slot
    holds — so a row that has no twin must still say what it is."""
    report = _run()

    assert [row["captions"] for row in report["menuUnique"]] == [["file"]]
    assert [row["title"] for row in report["menuUnique"]] == [SUMMARY]


@needs_node
def test_a_row_with_no_path_at_all_is_untouched_by_either_half():
    """A Resource is not a file: it has no path to match on and no folder to be told apart by. Both
    halves of this change read a row's path, so the row that has none is the one that would break
    quietly — it keeps its own name, its kind, and the plain title it has always had."""
    report = _run()

    assert report["menuMixed"] == [{"name": "Sales", "captions": ["Dataset"], "title": "Sales"}]


@needs_node
def test_the_mount_prefix_every_attachment_shares_is_not_searched():
    """The cost of widening, and the reason a row's own `searchPath` is what the query reads. Every
    Attachment lands under `public/data/<slug>/` (`_attach_dest`), so matching the workspace path
    makes `public`, `data` and even `ta/` match EVERY row in the app — and the menu shows eight, so
    the row a person is hunting for can be pushed off the list by the one thing it has in common
    with the rest. The Dataset-relative path is the same file named from where they put it."""
    report = _run()

    assert report["menuPrefix"] == []
    # `data` still reaches the two files whose own name carries it, and nothing else.
    assert [row["title"] for row in report["menuPrefixData"]] == [P2025, P2026]


@needs_node
def test_a_name_with_a_space_stands_in_the_box_as_one_word():
    """`mentionToken` collapses whitespace, so `q1 notes.md` is `@q1_notes.md` — and every reader of
    a token has to spell it the same way or the mention names nothing (see `_mention_word`)."""
    report = _run()

    assert report["insertedSpaced"] == "@q1_notes.md"


# ---- a stale token resolves to everything it matches ---------------------------------------------


@needs_node
def test_a_qualified_token_carries_exactly_the_one_file_it_names():
    """The point of the whole change, read off the wire: `@2026/data.csv` is one file, and the
    sibling partition does not ride along with it."""
    report = _run()

    assert report["sent"][0]["prompt"] == QUALIFIED
    assert report["sent"][0]["mentions"] == [P2026]


@needs_node
def test_a_stale_ambiguous_token_carries_every_file_it_matches():
    """Text already in the composer keeps the token it was given. Both rows now answer
    `@2026/data.csv`, neither matches `@data.csv` in the box, and without this the mention carries
    NOTHING — no refusal, no warning, an answer about the wrong thing. Silence is the one outcome
    ruled out, so it carries all of them."""
    report = _run()

    assert report["sent"][1]["prompt"] == STALE
    assert report["sent"][1]["mentions"] == [P2025, P2026]


@needs_node
def test_an_unambiguous_token_is_untouched_by_the_wider_match():
    """Matching every token a row could have been given must not make a token name more rows than
    it says. `@summary.csv` is a tail of one path and of no other."""
    report = _run()

    assert report["sent"][2]["mentions"] == [SUMMARY]


@needs_node
def test_a_qualified_token_survives_its_twin_being_detached():
    """The same drift running BACKWARDS, and the half a matcher that reads only today's answer gets
    wrong. `@2026/data.csv` is in the box; then the sibling partition is detached — or the selected
    Built App changes, which is allowed mid-turn (#77) — and `mentionToken` would answer plain
    `@data.csv` for the file that is left. Reading only that, the mention in the box matches nothing
    and carries nothing: no attachment, no refusal, no note. That is the silent drop this ADR exists
    to end, mirrored, so the turn matches every token the row could have been GIVEN."""
    report = _run()

    assert report["sentAfterDetach"] == [
        {"prompt": "chart the trend from @2026/data.csv", "mentions": [P2026]}
    ]


# ---- and the turn says so ------------------------------------------------------------------------


def test_the_turn_names_every_file_an_ambiguous_mention_matched():
    """Carrying all of them is half the decision; saying so is the other half. A person who reads
    "this turn carries all of them" can narrow the mention — and the menu can now reach either one."""
    resolved = [{"name": "data.csv", "path": P2025}, {"name": "data.csv", "path": P2026}]

    said = _ambiguous_mentions(resolved, STALE)

    assert "@data.csv names 2 attached files" in said
    assert P2025 in said and P2026 in said
    assert "Pick one from the @ menu" in said


def test_two_deliberate_mentions_of_one_basename_are_not_ambiguous():
    """`@2025/data.csv` and `@2026/data.csv` also resolve to two files sharing one basename, and
    there is nothing ambiguous about them: the person named both on purpose. The BARE basename has
    to stand as a word in the prompt, which `@2026/data.csv` does not contain."""
    resolved = [{"name": "data.csv", "path": P2025}, {"name": "data.csv", "path": P2026}]

    assert _ambiguous_mentions(resolved, "compare @2025/data.csv with @2026/data.csv") == ""


def test_one_file_is_never_reported_as_ambiguous():
    resolved = [{"name": "summary.csv", "path": SUMMARY}]

    assert _ambiguous_mentions(resolved, UNIQUE) == ""
    assert _ambiguous_mentions(None, STALE) == ""


def test_a_qualified_token_that_names_two_files_is_reported_too():
    """Grouping by the TOKEN standing in the prompt rather than by the basename. `@a/data.csv` names
    two files two folders down, and a report keyed on `data.csv` alone would never see it."""
    resolved = [{"name": "data.csv", "path": "public/data/s/x/a/data.csv"},
                {"name": "data.csv", "path": "public/data/s/y/a/data.csv"}]

    said = _ambiguous_mentions(resolved, "compare @a/data.csv")

    assert said.startswith("@a/data.csv names 2 attached files")


def test_a_name_with_a_space_is_read_as_the_token_the_composer_wrote():
    """`mentionToken` collapses whitespace, so two files called `q1 notes.md` stand in the box as
    `@q1_notes.md`. Testing the raw name would find the mention that has no space and never the one
    that does — and the person and the agent would be told nothing about a turn carrying two."""
    resolved = [{"name": "q1 notes.md", "path": "public/data/a/q1 notes.md"},
                {"name": "q1 notes.md", "path": "public/data/b/q1 notes.md"}]

    said = _ambiguous_mentions(resolved, "read @q1_notes.md")

    assert said.startswith("@q1_notes.md names 2 attached files")


def test_the_server_reads_a_token_as_its_own_word():
    """The same rule as `mentionedIn` in `util.js`: punctuation may follow — people write
    "@data.csv, please" — but a longer name must not match a shorter one's prefix, and the bare
    basename must not match INSIDE the qualified token this ADR now inserts."""
    assert _mentioned_in("read @data.csv, please", "data.csv")
    assert _mentioned_in("@data.csv", "data.csv")
    assert not _mentioned_in("read @2026/data.csv", "data.csv")
    assert not _mentioned_in("read @data.csv_old", "data.csv")
    assert not _mentioned_in("read data.csv", "data.csv")
    # A "." begins a suffix as well as ending a sentence. An app holding both `report` and
    # `report.csv` must not ride a turn with the first when the person named the second.
    assert not _mentioned_in("use @report.csv please", "report")
    # A trailing "." ends a sentence far more often than it starts a suffix, so `mentionedIn` counts
    # one as punctuation and this does too. Mirrored deliberately: the value of the two rules is
    # that they are the same rule, and a server that read a token more strictly than the composer
    # wrote it would report an ambiguity nobody's prompt contains.
    assert _mentioned_in("read @data.csv.", "data.csv")


def test_an_ambiguous_mention_is_not_reported_in_the_red_a_refusal_wears():
    """A drop is a failure; this is a turn that WORKED — every file the name matched is attached and
    nothing is missing. Folded into `mentions-unresolved` it would arrive with no `entries`, take
    `MentionsUnresolved`'s `!fixes.length` branch, and draw `sw-status-line is-err`: a red failure
    line under a turn that did everything it was asked. So it is its own event and its own plain
    status block, and it carries no rows — there is no fix button for it, because the act that
    closes it is retyping the mention the menu can now reach."""
    service = (Path(__file__).resolve().parents[1] / "sage" / "orchestrator"
               / "service.py").read_text()
    store = (WB / "js" / "store.js").read_text()

    assert '"mentions-ambiguous",' in service
    assert 'yield persist({"type": "mentions-ambiguous", "message": ambiguous})' in service
    assert "ev.type === 'mentions-ambiguous' && ev.message" in store
    assert "ensureAssistant().blocks.push({ type: 'status', value: ev.message });" in store
    # A replayed transcript has to redraw it, like every other bubble a turn writes.
    persisted = service[service.index("_PERSISTED_EVENTS = frozenset({"):]
    assert "mentions-ambiguous" in persisted[:persisted.index("})")]
    # And the agent hears it too. The whole defect #130 named was the screen being told something
    # the agent never heard, and a second copy of that would be this one.
    assert "unusable_note, ambiguous_note) if p)," in service


def test_chat_honours_the_qualified_token_the_menu_gave_it():
    """Chat draws the SAME composer as Build (`modes/chat.js`), so the @ menu inserts
    `@2026/data.csv` there too — but Chat resolves its own tokens against the Thread's context, and
    `_at_token_hits` only ever compared basenames and stems. A token with a "/" in it matched
    nothing, no descriptor was attached, and Chat has no "couldn't use that" line to say so: the
    agent answers about a file it was never handed. The silent carry ADR-0030 rules out, in the half
    of the product the composer change reaches without the resolver."""
    assert _at_token_hits("2026/data.csv", "data.csv", P2026)
    assert _at_token_hits("raw/2026/data.csv", "data.csv", P2026)
    # The plain basename still hits, which is how a stale token goes on resolving.
    assert _at_token_hits("data.csv", "data.csv", P2026)
    # A folder token outlives the row the same way, including in Chat: `@2026` was a row, the
    # files are now chips, and without the parent tails the token names nothing here either.
    assert _at_token_hits("2026", "data.csv", P2026)
    # And a qualified token still names ONE partition, not its sibling.
    assert not _at_token_hits("2026/data.csv", "data.csv", P2025)
    assert not _at_token_hits("2026", "data.csv", P2025)
    # The floor: `@data` is not a token of every file under `public/data/`.
    assert not _at_token_hits("data", "summary.csv", SUMMARY)
    assert not _at_token_hits("public", "data.csv", P2026)


# ---- one derivation ------------------------------------------------------------------------------


def test_the_menu_and_the_turn_build_the_token_from_one_list():
    """A token only one of them can produce is a mention that silently carries nothing. So the
    uniqueness rule lands in `mentionToken` — used by the picker that INSERTS and the turn that
    reads back — rather than in two places that can drift."""
    util = (WB / "js" / "util.js").read_text()
    store = (WB / "js" / "store.js").read_text()
    composer = (WB / "js" / "components" / "composer.js").read_text()

    assert "mentionSuffix(path, peers) {" in util
    assert "mentionToken(resource, peers) {" in util
    assert "const fromPath = path ? SW.util.mentionSuffix(path, peers) : '';" in util
    # One spelling of a token, for every producer and every reader of one.
    assert "return SW.util.mentionWord(fromPath || fromName || 'resource');" in util
    assert util.count("mentionWord(text) {") == 1
    # The menu inserts against the app's own Attachment list; the turn reads back against every
    # token a row could have been given, because that list moves under text already typed.
    assert "const mentionPeers = SW.util.attachmentPeers(appAttachments);" in composer
    assert "SW.util.mentionToken(resource, mentionPeers)" in composer
    assert "const typed = SW.util.mentionTokensIn(text);" in store
    assert "SW.util.mentionTokens(row).some((token) => typed.has(token))" in store
    # One regex for "what does this text mention", so the predicate and the reader cannot drift —
    # and one pass over the prompt rather than one per candidate token per row per keystroke.
    assert "return SW.util.mentionTokensIn(text).has(SW.util.mentionWord(bare));" in util
    assert util.count("new RegExp") == 0
    # The row's folder comes off the SAME peer list as the token, so a row can never show a folder
    # its own click does not carry.
    assert "SW.util.mentionSuffix(resource.path, mentionPeers)" in composer


def test_widening_the_matcher_reaches_the_composer_s_picker_and_nothing_else():
    """`workingSetFirst` is shared with the Build header's picker on purpose. That caller passes NO
    query and the matcher returns true on an empty one, so the widening is contained — asserted
    rather than assumed, because a query appearing there would silently change a second menu."""
    util = (WB / "js" / "util.js").read_text()
    builder = (WB / "js" / "modes" / "builder.js").read_text()

    assert "const path = SW.util.searchablePath(row.path).toLowerCase();" in util
    assert "return name.includes(lowered) || path.includes(lowered);" in util
    # Applied in the matcher, so it reaches every group that has a path — not stamped on the app's
    # Attachment row, which would leave the Project's Uploads and the Conversation's chips
    # matching on `.sage/scratch/` and `public/data/` exactly as before.
    assert "searchablePath(path) {" in util
    assert util.count("SW.util.searchablePath(") == 1
    assert "if (!lowered) return true;" in util
    assert "SW.util.workingSetFirst({ groups: working, catalogue });" in builder
    assert builder.count("SW.util.workingSetFirst(") == 1
