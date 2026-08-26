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
    # A refused removal names the app source that still uses it, rather than a dead-end toast.
    assert b"is still used by this app" in store.content
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
    assert b"Add to a Dataset" in panel.content

    tree = client.get("/js/components/resource-tree.js")
    assert tree.status_code == 200
    assert b"Files are not mounted in this workspace" in tree.content
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
