"""Throwaway repro, not a permanent test — delete after use."""
from pathlib import Path

from .fake_opencode import FakeOpenCode, Turn
from .test_chat_turn import OkFeedback, ScriptedGateway, _catalog
from sage.orchestrator.service import Orchestrator


def test_repro(tmp_path, capsys):
    template = tmp_path / "template"
    (template / "static").mkdir(parents=True)
    (template / "static" / "app.js").write_text("// placeholder\n")
    (template / "app.py").write_text("# app\n")
    ws = tmp_path / "mnt" / "code"
    oc = FakeOpenCode(ws, [Turn(text="hello"), Turn(text="built it")])
    orch = Orchestrator(workspace_dir=ws, template=template, gateway=ScriptedGateway(),
                        catalog=_catalog(), project_id="Sage", feedback=OkFeedback(),
                        opencode_client=oc)
    tid = orch.create_thread()["id"]
    list(orch.chat_stream(tid, "hi"))
    with capsys.disabled():
        print("apps dir exists:", (ws / "apps").exists())
        if (ws / "apps").exists():
            print("apps:", list((ws / "apps").iterdir()))
        proj = orch.project(start_preview=False, seed_app=False)
        app = proj.app_for_turn()
        print("app path:", app.path, "exists:", app.exists())
        events = list(orch.build_stream("build me a todo app"))
        for e in events[:8]:
            print("EVENT:", e)
        print("AFTER build_stream: apps dir exists:", (ws / "apps").exists())
        if (ws / "apps").exists():
            for d in (ws / "apps").iterdir():
                print(" app dir:", d, "app.py exists:", (d / "app.py").exists())
                settings = d / ".sage" / "settings.json"
                print(" settings:", settings.read_text() if settings.exists() else "(missing)")
        print("orch._project is proj (same object)?", orch._project is proj)
        print("proj.workspace is app_for_turn?", proj.workspace is proj.app_for_turn())
        print("proj.workspace.path:", proj.workspace.path, "exists:", proj.workspace.exists())
