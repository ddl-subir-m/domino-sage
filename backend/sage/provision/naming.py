"""App naming (Phase 4.1).

A new app's GitHub repo is `sage-<slug>` where the slug is a host-safe, lowercased form of the
display name the user typed. Repo/project name collisions are resolved by suffixing `-2`, `-3`, …
(the caller retries the next candidate when the provider reports the name is taken).

The caller's one Untitled project is a Sage overlay: the chip says Untitled, but the Domino
project and git repo are `sage-<user-slug>-<id>` so they never collide with a literal "Untitled".
"""
from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")
_IDENT_KEEP = re.compile(r"[^a-z0-9]+")
_USER_SLUG_MAX = 20
_IDENT_LEN = 8
UNTITLED_DISPLAY = "Untitled"


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


def untitled_project_name(username: str, user_id: str) -> str:
    """Stable Domino + git name for this caller's Untitled project: sage-<user>-<id>.

    The id is a short token from the Domino user id when it has usable characters, otherwise a
    hash of username+id so local/fake hubs still get a deterministic slug.
    """
    user = slugify(username)[:_USER_SLUG_MAX]
    ident = _ident_token(user_id, username)
    return f"sage-{user}-{ident}"


def is_scratch_name(name: str | None, expected: str) -> bool:
    """True if `name` is this caller's scratch project (exact or a -N collision suffix)."""
    if not name or not expected:
        return False
    if name == expected:
        return True
    prefix = expected + "-"
    return name.startswith(prefix) and name[len(prefix):].isdigit()


def _ident_token(user_id: str, username: str) -> str:
    kept = _IDENT_KEEP.sub("", (user_id or "").lower())
    if len(kept) >= 6:
        return kept[:_IDENT_LEN]
    return hashlib.sha256(f"{username}:{user_id}".encode()).hexdigest()[:_IDENT_LEN]
