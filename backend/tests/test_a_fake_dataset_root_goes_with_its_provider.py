"""`FakeAssetProvider()` seeds its datasets under a fresh temp directory, and used to leave it there.

Every Orchestrator built without `assets` makes one, so the suite made one per test and removed
none. Measured 2026-09-20: 1,657,863 `sage-fake-datasets-*` directories in the macOS temp dir, and
every process that listed that directory paid for it — a real-OpenCode boot took 167s against 5s
with an empty temp dir, and the full suite 13 minutes against 4.
"""

import gc

from sage.assets.provider import FakeAssetProvider


def test_the_root_it_made_is_removed_when_the_provider_dies():
    provider = FakeAssetProvider()
    root = provider.root
    assert root.is_dir() and any(root.iterdir()), "the provider seeds its datasets under root"
    del provider
    gc.collect()
    assert not root.exists()


def test_a_root_it_was_given_is_left_alone(tmp_path):
    """The caller's directory is the caller's to remove — tests hand in `tmp_path`, and the
    dataclass must not delete a directory it did not create."""
    provider = FakeAssetProvider(root=tmp_path)
    del provider
    gc.collect()
    assert tmp_path.is_dir()
