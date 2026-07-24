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
