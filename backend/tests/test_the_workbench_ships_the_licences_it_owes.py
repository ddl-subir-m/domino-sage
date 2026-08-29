"""#122 — the third-party licence text travels with what we distribute.

A packaging obligation, and it predates the OEM overlay. ADR-0014 settles that there is no
attribution floor: a partner may remove every Domino mark, the pack has no attribution key, and the
Workbench renders no Notices surface. What is left is ordinary — the Workbench serves Inter from our
own origin under the SIL OFL, which requires the licence to accompany the font, and it serves nine
third-party bundles beside it. A file in the Environment image satisfies all of that.

Built Apps are out of scope. The template's runtime dependencies land in a repo the user owns and
publishes, so the obligation follows that distribution and is theirs.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NOTICE = (ROOT / "NOTICE").read_text()
VENDOR = ROOT / "backend" / "sage" / "workbench" / "vendor"
FONTS = ROOT / "backend" / "sage" / "ui" / "fonts"
DOCKERFILE = (ROOT / "environment" / "Dockerfile").read_text()


def test_every_third_party_asset_the_workbench_serves_is_named():
    """From our own origin is the test, not from a CDN: what we serve, we distribute."""
    served = [p.name for p in VENDOR.iterdir() if p.is_file()]
    served.append("inter-latin-var.woff2")
    assert served
    assert [name for name in served if name not in NOTICE] == []


def test_the_inter_ofl_text_is_there_in_full():
    """A link is not enough. The OFL requires the licence itself to accompany the font."""
    assert "Copyright 2016 The Inter Project Authors" in NOTICE
    assert "SIL OPEN FONT LICENSE Version 1.1" in NOTICE
    for clause in ("PERMISSION & CONDITIONS", "TERMINATION", "DISCLAIMER"):
        assert clause in NOTICE, clause


def test_the_font_keeps_its_licence_beside_it_as_well():
    """Belt and braces: the copy that literally accompanies the font file."""
    assert (FONTS / "OFL.txt").exists()
    assert (FONTS / "inter-latin-var.woff2").exists()


def test_the_mit_notice_is_reproduced_rather_than_referred_to():
    """MIT asks for the copyright and the permission notice in all copies, so both are here."""
    assert "Permission is hereby granted, free of charge" in NOTICE
    assert 'THE SOFTWARE IS PROVIDED "AS IS"' in NOTICE
    assert "Copyright (c) Meta Platforms, Inc. and affiliates." in NOTICE


def test_highcharts_terms_are_not_described_as_open_source():
    """It is the one bundle here that is not. Saying so is the point of listing it."""
    block = NOTICE.split("Highcharts 11.4.8\n-----")[1]
    assert "highcharts.com/license" in block
    assert "Not open source" in block


def test_the_notice_travels_with_the_environment_image():
    """The image is a `git clone` of this repo into /opt/sage, so a checked-in file rides along.
    The build asserts it too, so deleting it fails the build instead of shipping quietly."""
    assert "/opt/sage/NOTICE" in DOCKERFILE


def test_the_workbench_renders_no_notices_surface():
    """ADR-0014: with no mark that has to survive, a surface defending one defends nothing.

    Asked of the running app rather than grepped out of it — the file is allowed to say the word.
    """
    from fastapi.testclient import TestClient

    import sage.orchestrator.app as appmod

    client = TestClient(appmod.control_app)
    for path in ("/NOTICE", "/notice", "/notices", "/api/notices", "/vendor/NOTICE"):
        assert client.get(path).status_code == 404, path
    for page in ("index.html", "door.html"):
        assert "Notices" not in (ROOT / "backend" / "sage" / "workbench" / page).read_text()


def test_the_brand_pack_has_no_attribution_key():
    from sage.orchestrator.brand import DEFAULT

    assert [k for k in DEFAULT if "attrib" in k.lower() or "notice" in k.lower()] == []


def test_a_new_built_app_is_given_no_licence_file():
    """The user owns and publishes that repo, so the obligation there is theirs."""
    template = ROOT / "template" / "react-vite"
    named = [p.name for p in template.rglob("*") if p.name.upper().split(".")[0]
             in {"NOTICE", "NOTICES", "LICENSE", "LICENCE", "COPYING"}
             and "node_modules" not in p.parts]
    assert named == []
