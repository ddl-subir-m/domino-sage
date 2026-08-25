"""Asset provider — tags and snapshots (Step 6)."""
from sage.assets.provider import FakeAssetProvider, parse_tag_snapshots, parse_tags


def test_parse_tags_handles_strings_and_objects():
    assert parse_tags(["a", {"name": "b"}, {"tag": "c"}, {"nope": 1}]) == ["a", "b", "c"]
    assert parse_tags(None) == []


def test_parse_tags_handles_datasetrw_v2_tag_map():
    # datasetrw v2 returns tags as {tagName: snapshotId} — the keys are the tag names.
    tags = parse_tags({"sensitive": "62313ce67a0af0281c01a6a5", "curated": "72313ce67a0af0281c01a6a5"})
    assert set(tags) == {"sensitive", "curated"}
    assert parse_tags({}) == []


def test_parse_tag_snapshots_keeps_the_snapshot_ids():
    # The {tagName: snapshotId} map is retained so we can tag an already-tagged dataset without a fetch.
    m = parse_tag_snapshots({"sensitive": "62313ce67a0af0281c01a6a5"})
    assert m == {"sensitive": "62313ce67a0af0281c01a6a5"}
    assert parse_tag_snapshots(["sensitive"]) == {}   # list shape carries no snapshot ids
    assert parse_tag_snapshots(None) == {}


def test_fake_provider_seeds_named_datasets():
    names = {a.name for a in FakeAssetProvider().list_datasets(None)}
    assert {"sales_2026", "customer_pii", "app_logs"} <= names
