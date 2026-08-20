"""The two facts Sage needs out of a Model API's sample snippet (#9).

Domino's Overview page for a Model API shows a ready-to-run call in several languages, and every one
of them carries the same two things: the invocation URL and the model access token. Sage cannot get
either any other way — probed exhaustively, no API vends the token and no listing carries the URL, so
the snippet a creator copies out of that page is the only source (see DOMINO-PRIMITIVES.md).

Parsing rather than asking for two fields separately: the creator already has the snippet on their
clipboard, and a form with a URL box and a token box invites pasting the whole thing into one of them.

Deliberately NOT parsed: the snippet's sample body. It is boilerplate — Domino prints
`{"data":{"start":1,"stop":100}}` for every Model API regardless of what the deployed function takes
— so keeping it would preserve a wrong example under the authority of having come from Domino. The
verify call sends `{"data":{}}` instead, and lets the model say what it actually wants.

Pure functions over text, like `pinned_model`: no I/O here, so the shapes stay testable without a
Domino to paste from.
"""
from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass

# The invocation URL, in any of Domino's language tabs. The port is optional because the page prints
# an explicit `:443` while a hand-shortened URL usually drops it, and both reach the same endpoint.
# The version segment is `latest` on the page but a pinned version number is equally valid.
_URL = re.compile(
    r"https?://[A-Za-z0-9.\-]+(?::\d+)?/models/(?P<id>[0-9a-fA-F]{24})/[A-Za-z0-9._\-]+/model",
)
# The access token: 64 characters of base62. Distinctive enough to find unanchored, which is what
# lets one pattern serve the jQuery tab (`var accessToken = "…"`), the Python tab (`auth=("…", "…")`)
# and a curl `-u …:…` without a rule per language.
_TOKEN = re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9]{64}(?![A-Za-z0-9])")
# Some curl snippets carry the credential pre-encoded instead, as `Authorization: Basic <base64>`.
_BASIC = re.compile(r"Basic\s+([A-Za-z0-9+/=]{40,})")


@dataclass(frozen=True)
class ParsedSnippet:
    """What a paste yielded. Either field may be None; the form says which one is missing."""

    url: str | None
    token: str | None
    model_id: str | None

    @property
    def complete(self) -> bool:
        return bool(self.url and self.token)

    def missing(self) -> str | None:
        """One sentence naming what the paste lacked, or None when it lacked nothing.

        Says what to paste rather than what failed to match: a creator who pasted the wrong half of
        the page needs to know which page and which button, not that a regex did not fire.
        """
        if self.complete:
            return None
        if not self.url and not self.token:
            return (
                "That paste has neither the model's URL nor its access token. Copy the whole sample "
                "request from the Model API's Overview page in Domino, and paste all of it."
            )
        if not self.url:
            return (
                "That paste has the access token but not the model's URL. Copy the whole sample "
                "request, including the line with https:// in it."
            )
        return (
            "That paste has the model's URL but not its access token. On the Overview page the "
            "token is in the sample request itself — copy all of it, not just the URL."
        )


def _token_from_basic(text: str) -> str | None:
    """The token out of a pre-encoded `Authorization: Basic` value.

    Domino encodes the token as both halves of the pair, so a value that decodes to `x:y` with
    x != y is somebody else's credential in a pasted-over snippet, not this model's, and is left
    alone rather than half-recovered.
    """
    for encoded in _BASIC.findall(text):
        try:
            decoded = base64.b64decode(encoded, validate=True).decode("ascii")
        except (binascii.Error, UnicodeDecodeError, ValueError):
            continue
        user, sep, password = decoded.partition(":")
        if sep and user and user == password:
            return user
    return None


def parse_snippet(text: str) -> ParsedSnippet:
    """The URL and token in a pasted sample request.

    The token search runs on the text with the URL cut out of it. A Model API id is 24 hex
    characters and a host name is base62 with dots, so no URL can be mistaken for a 64-character
    token today — but the snippet is Domino's to change, and finding the credential inside the
    endpoint it authenticates would be a hard failure to read.
    """
    text = text or ""
    m = _URL.search(text)
    url = m.group(0) if m else None
    model_id = m.group("id") if m else None

    rest = text[: m.start()] + text[m.end() :] if m else text
    found = _TOKEN.search(rest)
    token = found.group(0) if found else _token_from_basic(rest)

    return ParsedSnippet(url=url, token=token, model_id=model_id)
