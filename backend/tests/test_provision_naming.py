from sage.provision import naming


def test_slugify_basic():
    assert naming.slugify("My App") == "my-app"
    assert naming.slugify("  Sales Dashboard!!  ") == "sales-dashboard"
    assert naming.slugify("a__b--c") == "a-b-c"


def test_slugify_empty_falls_back():
    assert naming.slugify("") == "app"
    assert naming.slugify("💥🎉") == "app"
    assert naming.slugify("---") == "app"


def test_repo_base():
    assert naming.repo_base("My App") == "sage-my-app"
    assert naming.repo_base("🎉") == "sage-app"


def test_suffixed_and_candidates():
    assert naming.suffixed("sage-x", 1) == "sage-x"
    assert naming.suffixed("sage-x", 3) == "sage-x-3"
    assert list(naming.candidates("sage-x", 3)) == ["sage-x", "sage-x-2", "sage-x-3"]


def test_untitled_project_name_is_sage_prefixed_and_stable():
    a = naming.untitled_project_name("Alice Smith", "507f1f77bcf86cd799439011")
    b = naming.untitled_project_name("Alice Smith", "507f1f77bcf86cd799439011")
    assert a == b
    assert a.startswith("sage-alice-smith-")
    assert a == "sage-alice-smith-507f1f77"


def test_untitled_project_name_differs_per_user():
    alice = naming.untitled_project_name("alice", "aaaaaaa1")
    bob = naming.untitled_project_name("bob", "bbbbbbb2")
    assert alice != bob
    assert alice.startswith("sage-alice-")
    assert bob.startswith("sage-bob-")


def test_untitled_project_name_hashes_when_id_is_thin():
    name = naming.untitled_project_name("alice", "")
    assert name.startswith("sage-alice-")
    assert len(name.split("-")[-1]) == 8
    assert naming.untitled_project_name("alice", "") == name
    assert naming.untitled_project_name("bob", "") != name


def test_is_scratch_name_allows_collision_suffix():
    expected = "sage-alice-507f1f77"
    assert naming.is_scratch_name(expected, expected)
    assert naming.is_scratch_name(expected + "-2", expected)
    assert not naming.is_scratch_name("sage-alice-other", expected)
    assert not naming.is_scratch_name("Untitled", expected)
    assert not naming.is_scratch_name(None, expected)
