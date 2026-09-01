"""The Build composer warns before send, so the dead end never happens (#136).

WHAT WAS MISSING. An @mention of a Resource the selected Built App holds no Binding for ran the turn
anyway: the mention was dropped, the answer was prose directions to a panel, and the prompt had to
be retyped. #135 made that refusal actionable. This is the same fix moved one step earlier, to
before the turn is spent — the composer already holds both lists it takes to know, so the warning
costs no request.

WHAT THIS ASSERTS. The check reads the selected app's Bindings and Attachments and nothing else; a
chip appears under the box with one act per kind, taken from the store rather than copied; and it is
read fresh on every render, which is what makes it clear the moment the Binding or the Attachment
lands.

AND WHAT IT MUST NOT DO. It must never close the send. A mention is often incidental and the rest of
the prompt still runs, so a guard that blocked would take the turn away to save the mention. Nothing
on it binds either — every act is a click a person makes (ADR-0010). And Chat draws none of it: a
Session context chip is all Chat needs, and Chat has no Binding requirement at all.

Source assertions rather than a DOM harness, in the style of the composer's own mention suite: what
is at risk is the wiring — a list read from the wrong place, a branch that never runs, a `disabled`
that grew a third term — and every one of those is visible in the source.
"""

from __future__ import annotations

import re
from pathlib import Path

WB = Path(__file__).resolve().parents[1] / "sage" / "workbench"
UI = (WB / "js" / "components" / "composer.js").read_text()
STORE = (WB / "js" / "store.js").read_text()
UTIL = (WB / "js" / "util.js").read_text()
CSS = (WB / "css" / "chat.css").read_text()
SERVICE = (Path(__file__).resolve().parents[1] / "sage" / "orchestrator" / "service.py").read_text()


# ---- the check ---------------------------------------------------------------------------------


def test_the_check_reads_the_two_lists_the_app_already_carries_and_asks_for_nothing():
    """`bindings` and `appAttachments` are the selected app's own records, read per app and kept in
    step by the same gate (#101). A request here would make every keystroke cost a round trip."""
    guard = STORE[STORE.index("unusableMentions(text) {"):STORE.index("// Out of the selected Built App")]

    assert "const bound = new Set((state.bindings || []).map((b) => SW.util.bindingId(b)));" in guard
    assert "(state.appAttachments || []).map((a) => String(a.path || '').split('/').pop())" in guard
    assert "SW.api." not in guard, "the compose-time check must not fetch anything"


def test_the_check_reads_the_mentions_off_the_function_the_send_reads_them_off():
    """A second parser of "@name" would let the warning and the turn disagree about one prompt —
    warn about a mention the send honours, or stay quiet about one it drops."""
    assert "const refs = collectTurnRefs(text);" in STORE
    # And that is the one the send carries, unchanged.
    assert "mentions: refs.mentions, resources: refs.resources," in STORE


def test_an_unbound_resource_and_an_unattached_chat_file_are_the_two_rows():
    """The same two the refusal reports, in the same shape, so one `mentionFixes` draws both."""
    guard = STORE[STORE.index("unusableMentions(text) {"):STORE.index("// Out of the selected Built App")]

    assert "if (attached.has(name) || !path.startsWith(SCRATCH_PREFIX)) return;" in guard
    assert "entries.push({ kind: 'file', id: path, name, app: app.name, appId: app.id });" in guard
    assert "if (bound.has(key) || seen.has(key)) return;" in guard
    # The row names the app the act lands in, because a Project holds many Built Apps (ADR-0008).
    assert guard.count("app: app.name, appId: app.id });") == 2  # a file row and a Resource row


def test_the_composer_reads_the_same_two_lists_the_refusal_reads():
    """A Chat upload lives at the Project root, outside every app, and that prefix is the server's.
    Held together here because a drift makes the warning silent on exactly the drop it is for."""
    assert "_SCRATCH_PREFIX = \".sage/scratch/\"" in SERVICE
    assert "const SCRATCH_PREFIX = '.sage/scratch/';" in STORE


def test_the_same_resource_mentioned_twice_is_one_row():
    """"@Warehouse and @FCT_USAGE_DAILY" names one Data Source at one table — the server's rule, and
    it has to hold here too or the chip draws the same bind button twice."""
    assert "const key = `${ref.kind}:${ref.id}`;" in STORE
    assert "seen.add(key);" in STORE


# ---- the chip ----------------------------------------------------------------------------------


def test_the_warning_is_drawn_under_the_box_and_only_when_there_is_one():
    assert "unusable.length > 0 &&" in UI
    assert "h(MentionGuard, { entries: unusable, activeAppId: activeApp && activeApp.id })" in UI
    # Below the composer's own border, not inside it: within it, the warning reads as a field that
    # has failed validation, which says the send is blocked.
    assert ".sw-mention-guard {" in CSS
    assert "background: var(--warning-bg);" in CSS.split(".sw-mention-guard {")[1]


def test_the_sentence_names_the_app_and_only_what_a_button_below_it_can_close():
    """The invariant the refusal keeps by building both halves in one pass: the warning can never
    name something no act on it can fix."""
    assert "const offered = new Set(fixes.map((fix) => fix.key));" in UI
    assert ".filter((e) => offered.has(`${e.kind}:${e.id}`))" in UI
    assert "`Send now and ${named.join(', ')} won't reach ${entries[0].app}.`" in UI


def test_the_acts_are_the_store_s_and_the_chip_writes_no_second_copy():
    """#135 named the four acts rather than inlining them for this exact caller, and put the
    per-kind map beside them for the same reason."""
    assert "const fixes = SW.store.mentionFixes(entries, activeAppId);" in UI
    assert "FromMention" not in UI, "the chip reaches the acts through the shared map, never direct"
    # An Alias finishes in one click; a Data Source opens the Scope cascade at that Resource; a
    # Model API routes into the credential flow. All three, once, in the store.
    assert STORE.count("act: () => store.bindAliasFromMention(e)") == 1
    assert STORE.count("act: () => store.openScopeForMention(e),") == 1
    assert STORE.count("act: () => store.openCredentialForMention(e),") == 1
    assert STORE.count("act: () => store.attachFileForMention(e)") == 1


def test_the_busy_helper_is_shared_rather_than_copied_a_fourth_time():
    """Three cards in the transcript and now the composer draw a one-click act. The copies were at
    three when this surface asked for a fourth."""
    assert "useBusyAct() {" in UTIL
    assert "const [busy, run] = SW.util.useBusyAct();" in UI
    blocks = (WB / "js" / "components" / "message-blocks.js").read_text()
    assert "function useBusyAct() {" not in blocks
    assert blocks.count("const [busy, run] = SW.util.useBusyAct();") == 3


# ---- and what it must not do ---------------------------------------------------------------


def test_send_is_never_disabled_by_the_warning():
    """A mention is often incidental and the rest of the prompt still runs. A guard that closed the
    send would take the turn away to save the mention — the dead end, rebuilt facing the other way.
    """
    assert "disabled: !text.trim() || disabled," in UI
    # Two terms, an empty box and a wedged workspace, and the whole control between them.
    button = UI[UI.index("title: 'Send · ⌘⏎'"):UI.index("'aria-label': 'Send message',")]
    assert button.count("disabled") == 3  # the tooltip's guard, the prop, and the prop's value
    assert "unusable" not in button
    assert "MentionGuard" not in button


def test_nothing_binds_on_the_way_out():
    """The send is the same three lines it was: clear the box, drop the picker, hand the text over.
    A Binding is a human pick (ADR-0010), so the warning offers the act and never takes it."""
    send = UI[UI.index("const send = () => {"):UI.index("const changeText = (value, caret, inputType)")]
    assert "onSend(value);" in send
    # `setMention(null)` is in there and belongs: it closes the picker. Nothing that records
    # anything is.
    for act in ("bindToApp", "FromMention", "unusable", "MentionGuard"):
        assert act not in send, f"the send must not reach {act}"
    # And the check itself only reads: no writes, no notify, no request.
    guard = STORE[STORE.index("unusableMentions(text) {"):STORE.index("// Out of the selected Built App")]
    assert not re.search(r"state\.\w+ =", guard)
    assert "notify()" not in guard


def test_chat_draws_no_warning():
    """`showMode` is what tells the two composers apart everywhere else in this file — Chat has no
    app code to reset, no mode pill, and no Binding requirement."""
    assert "const unusable = showMode ? SW.store.unusableMentions(text) : [];" in UI
    assert UI.count("SW.store.unusableMentions(") == 1
    chat = (WB / "js" / "modes" / "chat.js").read_text()
    assert "showMode" not in chat
    builder = (WB / "js" / "modes" / "builder.js").read_text()
    assert "showMode: true," in builder


def test_the_warning_clears_itself_rather_than_being_cleared():
    """It is a function of the text and of the app's two lists, and the acts behind its buttons
    write those lists — `bindToApp` installs what the route answered, and the promote reloads the
    scope. Held in state it would need an invalidation for every door that can bind."""
    assert "const unusable = showMode ? SW.store.unusableMentions(text) : [];" in UI
    assert "setUnusable" not in UI
    assert "useState(unusable" not in UI
    # The two writes that make it clear.
    assert "applyAppScope(appScopeTicket(gen), { bindings: result.bindings || [] });" in STORE
    assert "applyAppScope(appTicket, { appAttachments: project.attached || [] });" in STORE


def test_a_file_the_app_already_holds_leaves_no_row_even_when_the_upload_survived():
    """The Attachments list has to decide something. Asked by full path it never can: an attachment
    is always under `public/data/` and a Chat upload always under `.sage/scratch/`, so the two sets
    never meet and the check would be dead. Asked by basename — which is what a mention token is —
    it goes quiet on a file that has reached the app, including when `promote_scratch_to_dataset`
    swallowed a failed unlink and left the original standing."""
    guard = STORE[STORE.index("unusableMentions(text) {"):STORE.index("// Out of the selected Built App")]

    assert "const name = path.split('/').pop();" in guard
    assert "if (attached.has(name)" in guard
    # Both halves of the reason the two sets never meet, held where they are decided.
    assert "def _attach_dest(" in SERVICE
    assert 'return PurePosix("public/data", _slug(dataset_name), *parts).as_posix()' in SERVICE


def test_the_sentence_quotes_the_token_the_picker_typed_not_the_row_s_name():
    """`mentionToken` collapses whitespace, so "Sales Warehouse" stands in the box as
    `@Sales_Warehouse`. Quoting the name would name a word the prompt does not contain."""
    assert "SW.util.mentionToken({ name: e.name, path: e.kind === 'file' ? e.id : '' })" in UI
    assert '.map((e) => `@${e.name}`)' not in UI


def test_a_synchronous_throw_does_not_wedge_the_row_it_was_clicked_in():
    """`Promise.resolve(fn())` calls `fn` before the chain exists, so a synchronous throw goes past
    the catch: `busy` stays set, every button in the row is disabled for good, and nothing says why.
    Two of the four mention acts return synchronously, so that throw has a caller."""
    assert "new Promise((resolve) => resolve(fn()))" in UTIL
    # The comment above it names the form it replaced, so the check is for the statement.
    assert "\n        Promise.resolve(fn())" not in UTIL
