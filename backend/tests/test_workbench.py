from fastapi.testclient import TestClient


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

    store = client.get("/js/store.js")
    assert store.status_code == 200
    assert b"gatewayAliases" in store.content
    assert b"resourcesLoading" in store.content
    assert b"resourceListing" in store.content
    assert b"ev.type === 'done' && ev.artifacts" in store.content

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
    assert b"Loading this project" in panel.content

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
    assert b"See what's in" not in composer.content
    assert b"Add a URL" not in composer.content

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
