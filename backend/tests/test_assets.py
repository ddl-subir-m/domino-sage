"""Asset provider + sensitivity detection (Step 6)."""
from sage.assets.provider import Asset, FakeAssetProvider, is_sensitive, parse_tags


def test_is_sensitive_case_insensitive():
    assert is_sensitive(Asset("a", "x", tags=["Sensitive"]), "sensitive")
    assert is_sensitive(Asset("a", "x", tags=["curated", "sensitive"]))
    assert not is_sensitive(Asset("a", "x", tags=["curated"]))
    assert not is_sensitive(Asset("a", "x", tags=[]))


def test_parse_tags_handles_strings_and_objects():
    assert parse_tags(["a", {"name": "b"}, {"tag": "c"}, {"nope": 1}]) == ["a", "b", "c"]
    assert parse_tags(None) == []


def test_parse_tags_handles_datasetrw_v2_tag_map():
    # datasetrw v2 returns tags as {tagName: snapshotId} — the keys are the tag names.
    tags = parse_tags({"sensitive": "62313ce67a0af0281c01a6a5", "curated": "72313ce67a0af0281c01a6a5"})
    assert set(tags) == {"sensitive", "curated"}
    assert is_sensitive(Asset("a", "x", tags=tags))
    assert parse_tags({}) == []


def test_fake_provider_has_a_sensitive_dataset():
    assets = FakeAssetProvider().list_datasets(None)
    assert any(is_sensitive(a) for a in assets)
    assert any(not is_sensitive(a) for a in assets)
