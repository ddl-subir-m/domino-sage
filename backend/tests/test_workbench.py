from fastapi.testclient import TestClient


def test_workbench_is_the_default_ui():
    import sage.orchestrator.app as appmod

    client = TestClient(appmod.control_app)
    r = client.get("/")
    assert r.status_code == 200
    assert "Sage Workspace" in r.text
    assert "//fonts.googleapis.com" not in r.text
    assert "./vendor/react.production.min.js" in r.text

    builder = client.get("/builder")
    assert builder.status_code == 200
    assert "function " in builder.text

    js = client.get("/js/api.js")
    assert js.status_code == 200
    assert b"/threads" in js.content
    assert b"/threads/save" in js.content
    assert b"/handoff/plan" in js.content
    assert b"/project/history" in js.content
    assert b"/project/build/stop" in js.content
    assert b"./api" in js.content

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
