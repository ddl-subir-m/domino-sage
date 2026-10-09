"""The @ menu offers a Project's skills on a page where the resources panel was never opened (#736).

The list the menu reads used to be filled only by the panel's mount, and the panel is closed by
default. Driven through `tests/js/mention_skill_load_harness.mjs`, which boots through the real
`init()` against a stub server and never mounts the panel, so nothing seeds the list but the
store's own reads.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "js" / "mention_skill_load_harness.mjs"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not on PATH (it is in the Sage image)")


@pytest.fixture(scope="module")
def stages() -> dict:
    out = subprocess.run(["node", str(_HARNESS)], capture_output=True, text=True, timeout=60,
                         check=False)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_a_fresh_load_offers_the_projects_skills(stages):
    assert stages["boot"]["rows"] == ["revops-conventions"]


def test_a_project_switch_offers_the_new_projects_skills(stages):
    assert sorted(stages["project"]["rows"]) == ["drift-metrics", "meddpicc"]
    assert len(stages["project"]["reads"]) == 1


def test_opening_a_conversation_reads_the_list_for_it(stages):
    assert stages["thread"]["reads"] == ["thread=conv_1&app="]


def test_moving_to_build_reads_the_list_for_the_app(stages):
    assert stages["mode"]["reads"] == ["thread=&app=app_a"]


def test_an_uploaded_skill_is_offered(stages):
    assert "deal-brief" in stages["upload"]["rows"]


def test_switching_a_skill_on_reaches_the_list(stages):
    assert stages["enable"]["reads"] and stages["enable"]["meddpiccEnabled"] is True
