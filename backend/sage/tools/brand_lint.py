"""The lint over the marked positions, where a user-visible string is written (ADR-0014).

It scans call sites, not files. A grep over the source is the wrong test — it fails the
moment somebody writes a code comment, and a rule nobody can live with is turned off. The
three positions below are the places prose reaches a person, so a comment, a docstring, a
variable name and a URL path are all invisible here.

The marked positions:

  * ``detail=`` on an ``HTTPException`` — what the Workbench shows when a route refuses
  * ``brand.text()`` — the Python substitution helper
  * ``SW.brand.*`` — its Workbench half

Nothing below is a maintained list. The words that may not be written bare are the pack's
own values, read out of ``brand.DEFAULT``, so a name the pack learns to rename is a name
this refuses on the same day. Which glossary terms need a noun key is computed the same
way: **a ``CONTEXT.md`` term needs a key exactly when a marked position names it**, and a
marked position names a term by writing its token. So a term the pack maps is refused
written out — ``Data Source`` is answered with ``{dataSource}`` — and a token the pack
cannot resolve is refused too, by the name of the term behind it. That is the whole
mechanism: to name a glossary term in copy you write its token, and the token fails until
the key exists. A term that exists only to disambiguate — ``AI Gateway``,
``Domino Artifacts``, ``Hosted GenAI Endpoint`` — is never tokenised, so it needs no key
and this never asks for one.

A new marked position is added here. It is never added to an exclusion list.

CI runs this as `tests/test_the_lint_over_marked_positions.py`, so a bare name fails the
build. `python -m sage.tools.brand_lint` prints every finding at once, which is what to
reach for while fixing one.
"""

from __future__ import annotations

import ast
import re
import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from ..orchestrator import brand

ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "backend" / "sage"
GLOSSARY = ROOT / "CONTEXT.md"

# The helper's own token grammar, so what this calls resolvable is what actually resolves.
_TOKEN = re.compile(r"\{([A-Za-z][A-Za-z0-9]*)\}")

# A glossary entry: `**Data Source**:` on a line of its own.
_TERM = re.compile(r"^\*\*(.+?)\*\*:\s*$", re.MULTILINE)


@dataclass(frozen=True)
class Marked:
    """One string literal written at a marked position."""

    path: str
    line: int
    position: str
    text: str
    # Token names the call site fills itself — `brand.text("{name} is gone", name=…)`.
    # They resolve without a pack key, and the helper does not scan what they fill.
    filled: frozenset[str]
    # A `detail=` written as a bare literal is nobody's template: no helper reads it, so a
    # token in it is not a token, it is two braces a person reads.
    substituted: bool = True


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    rule: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.rule}: {self.message}"


# ---------------------------------------------------------------- the vocabulary


def pack_tokens(pack: dict | None = None) -> set[str]:
    """Every token the helper can resolve. Borrowed from `brand` rather than restated, so
    the set this lint calls resolvable cannot drift from the set that does resolve."""
    return set(brand._tokens(pack or brand.DEFAULT))


def forbidden_phrases(pack: dict | None = None) -> dict[str, str]:
    """Every name the pack renames, mapped to how to write it instead.

    Computed from the pack, not listed: `Sage`, `Domino`, `ML Studio` and the nouns are
    here because they are what `brand.DEFAULT` says, and a key added there is refused at a
    marked position without anybody editing this file.
    """
    pack = pack or brand.DEFAULT
    out: dict[str, str] = {}
    for key, value in pack.items():
        # A URL is an identifier, not prose (ADR-0014's third arm) — it is not a name a
        # partner reads, and `./img/domino-logo.svg` is not a word anybody writes in a
        # sentence.
        if key.endswith("Url") or not isinstance(value, str) or not value:
            continue
        out.setdefault(value, f"write {{{key}}}")
    for peer in pack.get("peerProducts") or []:
        label = (peer or {}).get("label")
        if label:
            # A peer product has no token: the switcher is drawn from the list, so a
            # sentence naming one is reading the pack, not substituting into it.
            out.setdefault(label, "read the label from the pack's peerProducts")
    for key, forms in (pack.get("nouns") or {}).items():
        out.setdefault(forms["singular"], f"write {{{key}}}")
        out.setdefault(forms["plural"], f"write {{{key}Plural}}")
    return out


def _phrase_pattern(phrases: Iterable[str]) -> re.Pattern[str]:
    # Longest first, so `Data Sources` is named before `Data Source` and `Sage Workspace`
    # before `Sage` — the advice has to name the whole thing a person wrote.
    ordered = sorted(phrases, key=len, reverse=True)
    return re.compile(
        r"(?<![A-Za-z])(" + "|".join(re.escape(p) for p in ordered) + r")(?![A-Za-z])"
    )


def glossary_terms(path: Path | None = None) -> list[str]:
    """The terms `CONTEXT.md` defines, in the order it defines them."""
    return _TERM.findall((path or GLOSSARY).read_text(encoding="utf-8"))


def key_for(term: str) -> str:
    """The noun key a glossary term would have. `Data Source` → `dataSource`,
    `LLM Alias` → `llmAlias`, `Model API` → `modelApi` — the pack's own keys, which is
    what lets this answer "does the term behind this token have a key" without a list
    pairing the two."""
    words = [w for w in re.split(r"[^A-Za-z0-9]+", term) if w]
    if not words:
        return ""
    return words[0].lower() + "".join(w.capitalize() for w in words[1:])


def terms_needing_a_key(
    strings: Iterable[Marked], glossary: Path | None = None
) -> dict[str, str]:
    """The glossary terms a marked position names, as `key → term`.

    This is the computation ADR-0014 asks for: a term needs a noun key **iff** it appears
    in a user-visible string, and a user-visible string names a term by writing its token.
    A term nothing tokenises is not in the answer, however many times the glossary
    defines it.
    """
    by_key = {key_for(term): term for term in glossary_terms(glossary)}
    used: dict[str, str] = {}
    for marked in strings:
        if not marked.substituted:
            continue
        for token in _TOKEN.findall(marked.text):
            base = token.removesuffix("Plural")
            if base in by_key and base not in marked.filled:
                used[base] = by_key[base]
    return used


# ---------------------------------------------------------------- the check


def findings(
    strings: Iterable[Marked], *, pack: dict | None = None, glossary: Path | None = None
) -> list[Finding]:
    pack = pack or brand.DEFAULT
    phrases = forbidden_phrases(pack)
    pattern = _phrase_pattern(phrases)
    resolvable = pack_tokens(pack)
    strings = list(strings)
    # The computation itself: a token the pack cannot resolve is owed a noun key when the
    # glossary is where the word comes from, and is a typo when it is not.
    wanted = terms_needing_a_key(strings, glossary)

    out: list[Finding] = []
    for marked in strings:
        for phrase in dict.fromkeys(m.group(1) for m in pattern.finditer(marked.text)):
            out.append(
                Finding(
                    marked.path,
                    marked.line,
                    "bare-name",
                    f"{marked.position} says {phrase!r} — {phrases[phrase]}.",
                )
            )
        for token in dict.fromkeys(_TOKEN.findall(marked.text)):
            if token in marked.filled:
                continue
            if not marked.substituted:
                out.append(
                    Finding(
                        marked.path,
                        marked.line,
                        "unresolved-token",
                        f"{marked.position} is a bare literal, so nothing resolves "
                        f"{{{token}}} — wrap it in brand.text().",
                    )
                )
                continue
            if token in resolvable:
                continue
            base = token.removesuffix("Plural")
            if base in wanted:
                out.append(
                    Finding(
                        marked.path,
                        marked.line,
                        "missing-noun-key",
                        f"{marked.position} names the {wanted[base]}, so the pack needs a "
                        f"{base!r} noun key in brand.DEFAULT['nouns'].",
                    )
                )
            else:
                out.append(
                    Finding(
                        marked.path,
                        marked.line,
                        "unknown-token",
                        f"{marked.position} writes {{{token}}}, which is neither a pack "
                        f"key nor filled at this call site — a person reads the braces.",
                    )
                )
    return out


def run(root: Path | None = None, *, glossary: Path | None = None) -> list[Finding]:
    return findings(marked_strings(root or SOURCE), glossary=glossary)


# ---------------------------------------------------------------- Python call sites


def marked_strings(root: Path) -> list[Marked]:
    out: list[Marked] = []
    for path in sorted(root.rglob("*.py")):
        out.extend(_python(path, _relative(path)))
    for path in sorted(root.rglob("*.js")):
        source = path.read_text(encoding="utf-8")
        # A file that never names the helper has no marked position in it, whatever else
        # it holds — which is why the vendored bundles cost nothing and need no exclusion.
        if "SW.brand." in source:
            out.extend(_javascript(source, _relative(path)))
    return out


def _relative(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _python(path: Path, name: str) -> Iterator[Marked]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    helpers, modules = _helper_names(tree)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if _is_helper(func, helpers, modules):
            filled = frozenset(kw.arg for kw in node.keywords if kw.arg)
            for line, text in _literals(node.args[0] if node.args else None, tree):
                yield Marked(name, line, "brand.text()", text, filled)
        elif _named(func) == "HTTPException":
            for kw in node.keywords:
                if kw.arg != "detail":
                    continue
                for line, text in _literals(kw.value, tree):
                    yield Marked(
                        name, line, "HTTPException(detail=)", text, frozenset(), False
                    )


def _helper_names(tree: ast.Module) -> tuple[set[str], set[str]]:
    """What `brand.text` is called in this file. Both spellings are in the tree —
    `from . import brand` then `brand.text(…)`, and `from .brand import text as
    brand_text` — and a rename is a lint that stops looking, so the imports are read
    rather than assumed."""
    helpers: set[str] = set()
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if (node.module or "").split(".")[-1] == "brand" and alias.name == "text":
                    helpers.add(alias.asname or alias.name)
                elif alias.name == "brand":
                    modules.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[-1] == "brand":
                    modules.add(alias.asname or alias.name.split(".")[0])
    return helpers, modules


def _is_helper(func: ast.expr, helpers: set[str], modules: set[str]) -> bool:
    if isinstance(func, ast.Name):
        return func.id in helpers
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "text"
        and isinstance(func.value, ast.Name)
        and func.value.id in modules
    )


def _named(func: ast.expr) -> str:
    if isinstance(func, ast.Name):
        return func.id
    return func.attr if isinstance(func, ast.Attribute) else ""


def _literals(
    node: ast.expr | None, tree: ast.Module, *, follow: bool = True
) -> list[tuple[int, str]]:
    """The string literals a template argument can turn out to be.

    A conditional writes two of them and both reach a person; a name written once and
    passed here is still a string somebody wrote at this position, so it is followed to
    where it was assigned. It does not descend into a call: `detail=brand.text(…)` is
    already found as the `brand.text` position, and finding it twice would report a
    resolved token as an unresolved one.
    """
    if node is None:
        return []
    if isinstance(node, ast.Constant):
        return [(node.lineno, node.value)] if isinstance(node.value, str) else []
    if isinstance(node, ast.IfExp):
        return _literals(node.body, tree) + _literals(node.orelse, tree)
    if isinstance(node, ast.BinOp):
        return _literals(node.left, tree) + _literals(node.right, tree)
    if isinstance(node, ast.JoinedStr):
        return [
            (part.lineno, part.value)
            for part in node.values
            if isinstance(part, ast.Constant) and isinstance(part.value, str)
        ]
    if isinstance(node, ast.Name) and follow:
        return _assigned(node.id, tree)
    return []


def _assigned(name: str, tree: ast.Module) -> list[tuple[int, str]]:
    """Every string this file assigns to that name. Hoisting a sentence into a constant is
    the one way past this lint somebody would reach for by accident, and it is a short
    walk to close."""
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        targets = (
            node.targets
            if isinstance(node, ast.Assign)
            else [node.target] if isinstance(node, ast.AnnAssign) else []
        )
        if any(isinstance(t, ast.Name) and t.id == name for t in targets):
            # One hop only: two names assigned from each other must not send this round a
            # loop, and a sentence is never two hops from where somebody wrote it.
            out.extend(_literals(getattr(node, "value", None), tree, follow=False))
    return out


# ---------------------------------------------------------------- JS call sites

_CALL = re.compile(r"\bSW\.brand\.([A-Za-z_$][\w$]*)\s*\(")
_KEY = re.compile(r"([A-Za-z_$][\w$]*)\s*:")
# `{ query }` fills `{query}` just as `{ query: q }` does — the shorthand is the
# spelling the Workbench actually reaches for, so both are read.
_SHORTHAND = re.compile(r"[{,]\s*([A-Za-z_$][\w$]*)\s*(?=[,}])")


def _javascript(source: str, name: str) -> Iterator[Marked]:
    masked, literals = _mask(source)
    for call in _CALL.finditer(masked):
        open_paren = call.end() - 1
        close = _matching(masked, open_paren)
        if close < 0:
            continue
        comma = _first_comma(masked, open_paren, close)
        position = f"SW.brand.{call.group(1)}()"
        # Everything after the first argument is the values object, whose keys the helper
        # fills — the same contract as brand.text's keywords.
        values = masked[comma:close] if comma >= 0 else ""
        filled = frozenset(_KEY.findall(values)) | frozenset(_SHORTHAND.findall(values))
        end = comma if comma >= 0 else close
        for start, _, text in literals:
            if open_paren < start < end:
                yield Marked(
                    name,
                    source.count("\n", 0, start) + 1,
                    position,
                    text,
                    filled,
                )


def _mask(source: str) -> tuple[str, list[tuple[int, int, str]]]:
    """`source` with every comment and every string's contents blanked, offsets and lines
    intact, plus the literals that were blanked.

    This is what makes a comment invisible: a brand word inside `//` or `/* */` is gone
    before anything looks for a call site, and a paren inside a string cannot close one.

    A template literal is read as text with holes rather than as one opaque run, because
    the Workbench writes `` `${peer.label} ${SW.brand.text('…')}` ``. The call inside the
    hole is a marked position; a masker that swallowed the whole template would miss it,
    and — worse — would take the nested backtick in
    `` `#/build${t ? `/${t.id}` : ''}` `` for the closing one and lose its place for the
    rest of the file. A lint that silently stops looking is worse than no lint.
    """
    out = list(source)
    literals: list[tuple[int, int, str]] = []
    # Open template literals, innermost last. Each is [start offset, the text read so far,
    # the `{` depth of the `${…}` being read — or None while reading the template's text].
    stack: list[list] = []
    i, n = 0, len(source)
    previous = ""
    while i < n:
        char = source[i]
        frame = stack[-1] if stack else None
        if frame is not None and frame[2] is None:
            if char == "\\":
                out[i] = " "
                frame[1].append(char)
                i += 1
                if i < n:
                    if source[i] != "\n":
                        out[i] = " "
                    frame[1].append(source[i])
                    i += 1
                continue
            if char == "`":
                stack.pop()
                literals.append((frame[0], i + 1, _unescape("".join(frame[1]))))
                previous = "`"
                i += 1
                continue
            if source[i : i + 2] == "${":
                frame[2] = 0  # the hole is code, and code is where a call site lives
                i += 2
                continue
            if char != "\n":
                out[i] = " "
            frame[1].append(char)
            i += 1
            continue

        two = source[i : i + 2]
        if two == "//":
            while i < n and source[i] != "\n":
                out[i] = " "
                i += 1
            continue
        if two == "/*":
            while i < n and source[i - 1 : i + 1] != "*/":
                if source[i] != "\n":
                    out[i] = " "
                i += 1
            if i < n:
                out[i - 1] = out[i] = " "
                i += 1
            continue
        if char == "`":
            stack.append([i, [], None])
            i += 1
            continue
        if char in "'\"":
            start = i
            i += 1
            while i < n and source[i] != char:
                if source[i] == "\\":
                    out[i] = " "
                    i += 1
                if i < n:
                    if source[i] != "\n":
                        out[i] = " "
                    i += 1
            body = source[start + 1 : i]
            if i < n:
                i += 1
            literals.append((start, i, _unescape(body)))
            previous = char
            continue
        if char == "/" and previous not in ")]}" and not (previous.isalnum() or previous in "_$"):
            # A regex literal, not division. Blanked like a string so `/['"]/` cannot open
            # one and `/\/\//` cannot open a comment.
            out[i] = " "
            i += 1
            klass = False
            while i < n and (klass or source[i] != "/"):
                if source[i] == "\\":
                    out[i] = " "
                    i += 1
                elif source[i] == "[":
                    klass = True
                elif source[i] == "]":
                    klass = False
                if i < n:
                    if source[i] != "\n":
                        out[i] = " "
                    i += 1
            if i < n:
                out[i] = " "
                i += 1
            previous = "/"
            continue
        if frame is not None:
            # Inside a `${…}`: the brace that closes it returns to the template's text.
            if char == "{":
                frame[2] += 1
            elif char == "}":
                if frame[2] == 0:
                    frame[2] = None
                    i += 1
                    continue
                frame[2] -= 1
        if not char.isspace():
            previous = char
        i += 1
    return "".join(out), literals


def _unescape(body: str) -> str:
    return (
        body.replace("\\n", "\n")
        .replace("\\t", "\t")
        .replace("\\'", "'")
        .replace('\\"', '"')
        .replace("\\`", "`")
        .replace("\\\\", "\\")
    )


def _matching(masked: str, open_paren: int) -> int:
    depth = 0
    for i in range(open_paren, len(masked)):
        if masked[i] in "([{":
            depth += 1
        elif masked[i] in ")]}":
            depth -= 1
            if depth == 0:
                return i
    return -1


def _first_comma(masked: str, open_paren: int, close: int) -> int:
    depth = 0
    for i in range(open_paren, close):
        if masked[i] in "([{":
            depth += 1
        elif masked[i] in ")]}":
            depth -= 1
        elif masked[i] == "," and depth == 1:
            return i
    return -1


def main() -> int:
    found = run()
    for finding in found:
        print(finding)
    print(f"{len(found)} finding(s) at marked positions.", file=sys.stderr)
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main())
