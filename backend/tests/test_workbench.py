from pathlib import Path

from fastapi.testclient import TestClient


def test_the_published_app_serves_the_door_not_the_chat_shell(monkeypatch):
    """ADR-0004: the Workbench App is a door. It must not serve Chat from its scratch checkout —
    it sends the viewer to their own Sage Builder, where their files are in a real git Project."""
    import sage.orchestrator.app as appmod

    assert Path(appmod.ui().path) == appmod._UI  # a Sage Builder serves the shell, unchanged
    monkeypatch.setattr(appmod, "proxy_is_app", lambda: True)
    assert Path(appmod.ui().path) == appmod._DOOR_UI

    door = Path(appmod._DOOR_UI).read_text()
    assert "/door" in door and "location.replace" in door  # it opens the builder and goes there
    assert "/door/status" in door  # and waits for the session rather than landing on a dead page
    # A builder lives on the main host; the App is served from apps.<host>.
    assert "apps." in door and "slice(5)" in door


def test_workbench_is_the_default_ui():
    import sage.orchestrator.app as appmod

    client = TestClient(appmod.control_app)
    r = client.get("/")
    assert r.status_code == 200
    assert "Sage Workspace" in r.text
    assert "//fonts.googleapis.com" not in r.text
    assert "./vendor/react.production.min.js" in r.text

    gone = client.get("/builder")
    assert gone.status_code == 404

    pack = client.get("/api/brand")
    assert pack.status_code == 200
    assert pack.json()["productName"]
    assert pack.json()["assistantName"]
    assert pack.json()["colors"]["primary"]

    js = client.get("/js/api.js")
    assert js.status_code == 200
    assert b"/threads" in js.content
    assert b"/threads/save" in js.content
    assert b"/handoff/plan" in js.content
    assert b"/project/history" in js.content
    assert b"/project/build/stop" in js.content
    assert b"./api" in js.content
    assert b"chat_model" in js.content
    assert b"/project/resources" in js.content
    assert b"fetchDominoListing" in js.content
    assert b"groupsFromMembership" in js.content
    assert b"overlayListing" in js.content
    assert b"resourceListing" in js.content
    assert b"memberIds.has" in js.content
    assert b"inProject: true" not in js.content
    assert b"request('/brand')" in js.content

    store = client.get("/js/store.js")
    assert store.status_code == 200
    # A refused removal names the Built Apps that still bind it and the source that still uses
    # it, rather than a dead-end toast. The app refusing is often not the one on screen (#71); what
    # the refusal has to carry is asserted against the route, in test_project_resources.
    assert b"err.payload && err.payload.apps" in store.content
    assert b"err.payload && err.payload.refs" in store.content
    assert b"gatewayAliases" in store.content
    assert b"resourcesLoading" in store.content
    assert b"resourceListing" in store.content
    assert b"ev.type === 'done' && ev.artifacts" in store.content
    assert b"applyBrandChrome" in store.content
    assert b"SW.brand" in store.content

    build = client.get("/js/modes/builder.js")
    assert build.status_code == 200
    assert b"SW.BuildMode" in build.content
    assert b"./api/project/build/stream" in build.content or b"sendBuildPrompt" in build.content
    assert b"src: './builder'" not in build.content
    assert b'src: "./builder"' not in build.content

    panel = client.get("/js/components/resource-panel.js")
    assert panel.status_code == 200
    assert b"In context" in panel.content
    assert b"Project resources" in panel.content
    assert b"addToContext" in panel.content
    assert b"MCPs" in panel.content
    assert b"Agents" in panel.content
    assert b"Skills" in panel.content
    assert b"Extensions" not in panel.content
    assert b"resourcesLoading" in panel.content
    assert b"fromCatalog" not in panel.content
    assert b"membershipParent" in panel.content
    assert b"Files in this workspace" in panel.content
    assert b"e.target.files || []).map((f) => f.name)" not in panel.content
    assert b"SW.store.uploadFile(file)" in panel.content
    # The promote targets are every writable mount, not the curated rail: membership never gated
    # the server's copy, so it must not grey out the menu either.
    assert b"SW.store.get().datasetTargets" in panel.content
    assert b"No writable Dataset is mounted here" in panel.content

    tree = client.get("/js/components/resource-tree.js")
    assert tree.status_code == 200
    # The dead end is gone: the tree lists files for any readable Dataset, mounted or not.
    assert b"Files are not mounted in this workspace" not in tree.content
    assert b"No files in this Dataset." in tree.content
    assert b"SW.DatasetFileTree" in tree.content
    assert b"SW.DataSourceCascade" in tree.content

    catalog = client.get("/js/components/resource-catalog.js")
    assert catalog.status_code == 200
    assert b"Database tables" not in catalog.content
    assert b"setDrill" in catalog.content

    assert b"Remove from ${SW.store.get().scope.name}" in panel.content

    composer = client.get("/js/components/composer.js")
    assert composer.status_code == 200
    assert b"PROJECT_MENTION_KINDS" in composer.content
    assert b"In context" in composer.content
    assert b"BUILD_MODES" in composer.content
    assert b"setBuildMode" in composer.content
    assert b"read-only" in composer.content
    assert b"'ask'" in composer.content
    assert b"'plan'" in composer.content
    assert b"'implement'" in composer.content
    assert b"setChatModel" in composer.content
    assert b"reasoning_efforts" in composer.content
    assert b"Best for reasoning" not in composer.content
    assert b"chatAliases" in composer.content
    assert b"Sage picks from your gateway models" not in composer.content
    assert b"catalogAsk" in composer.content
    assert b"gatewayAliases" in composer.content
    assert b"resourceGroups.pin" in composer.content
    assert b"See what's in" not in composer.content
    assert b"Add a URL" not in composer.content

    assert b"Modal.confirm" in store.content
    assert b"promoteScratch" in js.content
    assert b"resource-tree.js" in client.get("/").content

    assert b"showMode: true" in build.content
    assert b"hidePhase" not in build.content
    assert b"/project/model" in js.content

    chat = client.get("/js/modes/chat.js")
    assert chat.status_code == 200
    assert b"What do you want to know" in chat.content
    assert b"What are you working on" not in chat.content
    assert b"or describe an app" not in chat.content
    assert b"or describe something you want to build" not in chat.content
    assert b"Ask about your data" in chat.content
    assert b"use @ to bring in a resource" in chat.content
def test_the_build_offer_answers_an_explicit_ask_in_its_own_words():
    """One card, two moments. The classifier notices an app taking shape in a conversation about
    something else; an explicit "build me a webapp" was already a decision, and meeting that with
    "this is starting to look like an app" reads as though nobody was listening."""
    from pathlib import Path as P

    wb = P(__file__).resolve().parents[1] / "sage" / "workbench" / "js"
    blocks = (wb / "components" / "message-blocks.js").read_text()
    store = (wb / "store.js").read_text()

    assert "reason === 'explicit'" in blocks
    assert "Let’s build that in Build." in blocks
    assert "This is starting to look like an app." in blocks  # the classifier keeps its voice
    assert "h(PlanSuggestion, { block })" in blocks           # the card can see which one it is
    # and the reason reaches it from the turn event, live and on reload
    assert store.count("{ type: 'plan_suggestion', reason: ev.reason }") == 2


def test_the_shell_asks_before_it_reuses_its_own_javascript():
    """The shell's JS and CSS carry no version in their filenames. With no Cache-Control a browser
    falls back to heuristic freshness, so an open tab can keep running code from before a deploy.
    no-cache makes it ask every time; the ETag keeps the answer a cheap 304."""
    import sage.orchestrator.app as appmod

    client = TestClient(appmod.control_app)
    assert client.get("/").headers["cache-control"] == "no-store"
    for path in ("/js/store.js", "/css/builder.css", "/vendor/react.production.min.js"):
        assert client.get(path).headers["cache-control"] == "no-cache", path

    etag = client.get("/js/store.js").headers["etag"]
    assert client.get("/js/store.js", headers={"If-None-Match": etag}).status_code == 304

    # The font is renamed when its bytes are replaced, so it keeps the long immutable cache.
    assert "immutable" in client.get("/fonts/inter-latin-var.woff2").headers["cache-control"]


def test_a_dataset_file_chip_does_not_invent_a_path_out_of_its_own_id():
    """`dsfile:<datasetId>:<relPath>` is an id, not a path with a prefix on it.

    Stripping one prefix left `<datasetId>:<relPath>`, which every `if path:` on the server read as
    a real location: it skipped attaching the file, skipped offering the Domino data library, and
    told the agent to read something that cannot be stat'd. Only a `file:<path>` id carries a path.
    """
    api = (Path(__file__).resolve().parents[1] / "sage" / "workbench" / "js" / "api.js").read_text()

    assert "kind === 'file' && kindFromPrefix(resourceId) === 'file'" in api
    assert "path: resource.path || (kind === 'file' ? rawFromPrefix(resourceId) : undefined)" not in api


def _js(*parts: str) -> str:
    return (Path(__file__).resolve().parents[1] / "sage" / "workbench" / "js" / Path(*parts)).read_text()


def test_the_answer_is_rendered_as_it_arrives_and_replaced_by_the_record_of_it():
    """Chat showed nothing until the turn ended. The server now sends `delta` while the text is
    being written, and the client has to do two things with it: paint the fragments, then let go of
    them when the authoritative text arrives. Only the authoritative text is in the transcript, so
    a client that kept its live blocks would show a Thread that changes when you reload it."""
    store = _js("store.js")

    assert "if (ev.type === 'delta')" in store
    # `final` is the whole text, not the last fragment — the stream cannot be replayed, so this is
    # what repairs a live copy that dropped a frame.
    assert "streamed = ev.text || '';" in store
    # And the record replaces what streamed rather than being appended after it.
    assert "assistant.blocks.filter((b) => !b.streaming)" in store


def test_fragments_repaint_once_a_frame_rather_than_once_each():
    """Deltas arrive faster than the screen refreshes, and each one re-renders the whole Thread —
    including earlier messages' charts, which stringify their options to decide whether to redraw.
    Painting per fragment is the difference between a Thread that scrolls and one that stutters."""
    assert "requestAnimationFrame(flush)" in _js("store.js")


def test_a_replayed_thread_has_no_live_text_to_replay():
    """`delta` never reaches history.jsonl — it is the turn happening, not the record of it. The
    replay path reads `ev.text`, so a Thread on reload is built from the transcript alone."""
    store = _js("store.js")
    head = store[:store.index("async function readSSE")]
    assert "'delta'" not in head


def test_text_still_arriving_says_so():
    """A model that pauses mid-sentence otherwise looks like a model that finished a short answer."""
    blocks = _js("components", "message-blocks.js")
    assert "block.streaming ? ' is-streaming' : ''" in blocks
    css = (Path(__file__).resolve().parents[1] / "sage" / "workbench" / "css" / "chat.css").read_text()
    assert ".sw-msg-text.is-streaming" in css
    assert "prefers-reduced-motion" in css   # a blinking caret is not for everyone


def test_the_view_follows_a_growing_answer_but_only_from_the_bottom():
    """A streamed answer grows the last message rather than adding one, so the length of the list
    never changes and the scroller stops following. It has to follow the text — and stop following
    the moment the reader scrolls up, or reading anything earlier becomes impossible mid-turn."""
    chat = _js("modes", "chat.js")
    assert "streamedChars" in chat
    assert "el.scrollHeight - el.scrollTop - el.clientHeight < 120" in chat


def test_a_build_turn_carries_what_its_mentions_name():
    """The Build composer inserts "@name" and sent only the sentence, so an @mention in Build reached
    the agent as a bare word: the file it named was never attached to the turn, and the build read
    whatever it could find instead. The menu and the sender derive the token the same way — a token
    only one of them can produce is a mention that silently carries nothing."""
    store = _js("store.js")
    assert "mentions: refs.mentions, resources: refs.resources," in store
    assert "SW.util.mentionedIn(text, SW.util.mentionToken(row))" in store
    # A Resource rides as its Binding identity, never as its name: an id is unique only within a kind.
    assert "kind: row.bindingKey[0], id: row.bindingKey[1]" in store


def test_a_mention_the_build_could_not_use_is_said_out_loud():
    """The picker offers Chat's uploads and unbound Resources; a build honors neither. Dropped in
    silence, that is a turn building from the wrong file while the right one sits in the panel."""
    assert "ev.type === 'mentions-unresolved'" in _js("store.js")
