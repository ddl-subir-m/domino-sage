import pytest


@pytest.fixture(autouse=True)
def _isolate_brand_override(monkeypatch, tmp_path):
    """Keep every test off the developer's own Appearance choice (ADR-0044).

    `brand.load()` reads a writable override, and its default path is under `~/.config`. Running
    the Workbench once on this machine writes that file — so without this, a theme somebody picked
    while trying the feature out becomes a pack every test in the suite silently resolves against,
    and the failure lands in whichever test happens to assert on a colour.

    Autouse and suite-wide rather than in `test_brand.py`, because the file is read by `load()` and
    `load()` is reached from the routes, the entry pages and `apply_voice` — the brand tests are the
    ones that would notice, not the only ones affected.
    """
    monkeypatch.setenv("SAGE_BRAND_OVERRIDE", str(tmp_path / "no-brand-override.json"))
