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

    assert b"showMode: true" in build.content
    assert b"hidePhase" not in build.content
    assert b"/project/model" in js.content
