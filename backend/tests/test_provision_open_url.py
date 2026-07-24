from sage.provision.service import workspace_open_url


def test_assembles_host_relative_path_from_workspace_dto():
    ws = {
        "id": "ws-1",
        "ownerName": "subir_mansukhani",
        "project": {"name": "probe one"},
        "mostRecentSession": {"executionId": "6a639ccdce6de705f76ea3d3"},
    }
    # Host-relative (browser resolves against the external origin), project name URL-encoded, runId
    # is the session executionId — not the workspace id.
    assert workspace_open_url(ws) == "/subir_mansukhani/probe%20one/notebookSession/6a639ccdce6de705f76ea3d3/"


def test_none_when_pieces_missing():
    assert workspace_open_url({"id": "ws-1"}) is None
    assert workspace_open_url({"ownerName": "x", "project": {"name": "p"}}) is None  # no session/runId
    assert workspace_open_url(None) is None


def test_falls_back_to_session_id_without_execution_id():
    ws = {"ownerName": "u", "project": {"name": "p"}, "mostRecentSession": {"id": "sess-9"}}
    assert workspace_open_url(ws) == "/u/p/notebookSession/sess-9/"


def test_uses_passed_project_name_when_dto_project_is_null():
    # The live v4 WorkspaceDto: ownerName + mostRecentSession present, but project is null. The
    # caller supplies the project name (the URL slug).
    ws = {
        "ownerName": "subir_mansukhani",
        "project": None,
        "mostRecentSession": {"executionId": "6a63a2d2242fc543ed246d9a"},
    }
    assert workspace_open_url(ws, "probe one") == (
        "/subir_mansukhani/probe%20one/notebookSession/6a63a2d2242fc543ed246d9a/"
    )
    # Without the name there's nothing to build the slug from.
    assert workspace_open_url(ws) is None
