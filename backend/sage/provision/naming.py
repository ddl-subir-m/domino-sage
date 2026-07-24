"""App naming (Phase 4.1).

A new app's GitHub repo is `sage-<slug>` where the slug is a host-safe, lowercased form of the
display name the user typed. Repo/project name collisions are resolved by suffixing `-2`, `-3`, …
(the caller retries the next candidate when the provider reports the name is taken).
"""
from __future__ import annotations

import re
from collections.abc import Iterator

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def slugify(display_name: str) -> str:
    """Lowercase, collapse any run of non-alphanumerics to a single hyphen, trim hyphens.

    Falls back to "app" when the input has no usable characters (e.g. all emoji/punctuation), so we
    never produce the invalid repo name "sage-".
    """
    slug = _SLUG_STRIP.sub("-", display_name.strip().lower()).strip("-")
    return slug or "app"


def repo_base(display_name: str) -> str:
    """The un-suffixed repo name for a display name, e.g. "My App!" -> "sage-my-app"."""
    return f"sage-{slugify(display_name)}"


def suffixed(base: str, n: int) -> str:
    """The n-th collision candidate: n=1 -> base, n=2 -> base-2, n=3 -> base-3, …."""
    return base if n <= 1 else f"{base}-{n}"


def candidates(base: str, limit: int = 50) -> Iterator[str]:
    """Yield base, base-2, … base-<limit> — the names to try in order on a collision."""
    for n in range(1, limit + 1):
        yield suffixed(base, n)
