"""Boot on nonsense sentinels and prove nothing we wrote still says our name (#124, ADR-0014).

`brand_coverage.toml` is the test. It lists every surface a person reads, and this file boots the
Workbench on a pack of `ZZQQ-` sentinels and asserts that none of the forbidden words survive on
anything the list names. It then walks the code for the surfaces that can grow — an entry page, a
template AGENTS.md, a module that puts a sentence in front of a person, an OpenCode agent — and
fails when one of them is reachable and absent from the list. **Adding a surface without listing it
is itself the failure**, which is the only thing that keeps a coverage list from being aspirational.

This is the second of the two tests that block CI and it does not replace the first. The lint over
marked positions catches the string somebody writes next week; this catches a token that is never
resolved — `SW.brand.text('{gallery}')` reads clean in the file and leaks the moment `gallery` is
not a key the pack carries.

**Text Sage did not write is exempt, and the exemption is structural rather than a word list.** A
Resource the user named after the company, a line of their SQL, a platform error body passed
through: every one of those arrives as a VALUE, and no probe here reads a value. A `brand.text()`
site is resolved with a marker standing in for each keyword argument, and a passed-through error is
a runtime string with no literal to harvest. So the only thing that can ever fail this file is
Sage's own prose. `test_text_sage_did_not_write_is_never_scanned` pins that.
"""
from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sage.orchestrator import brand

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
WORKBENCH = BACKEND / "sage" / "workbench"
SAGE = BACKEND / "sage"
TEMPLATES = REPO / "template"
OPENCODE = REPO / "opencode.json"

COVERAGE = Path(__file__).with_name("brand_coverage.toml")
LIST = tomllib.loads(COVERAGE.read_text())

SENTINEL = LIST["sentinels"]["prefix"]
# Longest first, so "Data Source" is reported rather than nothing and "AI Workbench" rather than a
# half of itself. Case-sensitive and whole-word is also how identifiers stay out: `.sage/`,
# `domino_data`, `DOMINO_API_HOST` and `DatasetClient` are code, and ADR-0014 does not rename code.
_FORBIDDEN = re.compile(
    "|".join(rf"\b{re.escape(w)}\b"
             for w in sorted(LIST["forbidden"]["words"], key=len, reverse=True))
)

# Stands in for every value that arrives at runtime. Deliberately not a sentinel, so a string whose
# only `ZZQQ-` came from a substituted value cannot count as proof the pack reached the surface.
RUNTIME_VALUE = "RUNTIME-VALUE"

_BRACED = re.compile(r"\{([A-Za-z][A-Za-z0-9]*)\}")

# A full partner pack: an OEM replaces every name and image a person sees, Domino included. Values
# no real pack would carry, so a pass here cannot be an accident of the Domino defaults.
PACK = {
    "productName": f"{SENTINEL}PRODUCT",
    "assistantName": f"{SENTINEL}ASSISTANT",
    "platformName": f"{SENTINEL}PLATFORM",
    "pageTitle": f"{SENTINEL}TITLE",
    "logoAlt": f"{SENTINEL}LOGOALT",
    "logoUrl": "./brand/zzqq-logo.svg",
    "faviconUrl": "./brand/zzqq-favicon.svg",
    "peerProducts": [{"key": "peer", "label": f"{SENTINEL}PEER"}],
    "nouns": {
        key: {"singular": f"{SENTINEL}{key.upper()}", "plural": f"{SENTINEL}{key.upper()}S"}
        for key in brand.DEFAULT["nouns"]
    },
}


@dataclass(frozen=True)
class Surface:
    kind: str
    name: str | None      # the file, module, template or agent, for a kind that has many
    entry: dict

    @property
    def id(self) -> str:
        return f"{self.kind}:{self.name}" if self.name else self.kind


def _listed() -> list[Surface]:
    out = []
    for entry in LIST["surface"]:
        kind = entry["kind"]
        reads = LIST["kind"][kind].get("reads")
        out.append(Surface(kind=kind, name=entry[reads] if reads else None, entry=entry))
    return out


SURFACES = _listed()


@pytest.fixture(autouse=True)
def paranoid_pack(tmp_path, monkeypatch):
    """The whole process boots on the sentinels, exactly as a partner's install does."""
    path = tmp_path / "brand.json"
    path.write_text(json.dumps(PACK))
    monkeypatch.setattr("sage.orchestrator.brand._BAKED", tmp_path / "no-baked-brand.json")
    monkeypatch.setenv("SAGE_BRAND_FILE", str(path))
    monkeypatch.setattr("sage.orchestrator.brand._WARNED", set())


# --- reading a surface ---------------------------------------------------------------------------


def _read_api_brand(_surface, _monkeypatch) -> list[str]:
    import sage.orchestrator.app as appmod

    r = TestClient(appmod.control_app).get("/api/brand")
    assert r.status_code == 200
    return [json.dumps(r.json(), ensure_ascii=False)]


def _read_entry_page(surface, monkeypatch) -> list[str]:
    """The bytes the server sends, which is where the pack has to already be — a page patched from
    JS on boot paints our name first, and the flash lands on the door (#116)."""
    import sage.orchestrator.app as appmod

    monkeypatch.setattr(appmod, "proxy_is_app", lambda: surface.name == "door.html")
    r = TestClient(appmod.control_app).get("/")
    assert r.status_code == 200
    return [_without_comments(r.text)]


def _read_shell_dom(_surface, _monkeypatch) -> list[str]:
    if shutil.which("node") is None:
        pytest.skip("node is not on PATH (it is in the Sage image)")
    harness = Path(__file__).resolve().parent / "js" / "brand_dom_harness.mjs"
    out = subprocess.run(
        ["node", str(harness)],
        # The pack the shell gets is the pack GET /api/brand returns, so both halves of the wire
        # are being asked about one thing.
        input=json.dumps({"pack": brand.load(), "modes": ["chat", "build", "code", "manage"]}),
        check=False, capture_output=True, text=True, timeout=60,
    )
    assert out.returncode == 0, out.stderr
    drawn = json.loads(out.stdout.strip().splitlines()[-1])
    assert not drawn["unrendered"], (
        f"the harness could not render {drawn['unrendered']} — its words went unread, so this "
        "surface would pass without having been looked at. Fake what the component needs."
    )
    return drawn["words"] + [drawn["title"]]


def _read_python_prose(surface, _monkeypatch) -> list[str]:
    """Every sentence this module can put in front of a person, resolved against the pack.

    Harvested at the `brand.text()` site rather than raised, because that is where provenance still
    exists: the literal is ours and the keyword arguments are not, so the literal is resolved and
    each argument becomes a marker. A bare name and a token that never resolves both fail.
    """
    return [_resolve_site(template, keywords)
            for template, keywords in _brand_text_sites(BACKEND / surface.name)]


def _read_agents_md(surface, _monkeypatch) -> list[str]:
    """What a seeded workspace's instructions say. The repo is a surface — a partner's own customer
    can read it — so each is read through the resolver that actually writes it: `brand.text()` for
    the Built App template (`_voice_agents_md`) and `apply_voice` for Chat (`_chat_agents_md`)."""
    body = (TEMPLATES / surface.name / "AGENTS.md").read_text()
    return [brand.apply_voice(body) if surface.name == "chat" else brand.text(body)]


def _read_agent_prompt(surface, _monkeypatch) -> list[str]:
    """The system prompt as it is installed, which is the only copy OpenCode ever loads."""
    cfg = brand.apply_agent_voice(json.loads(OPENCODE.read_text()))
    return [cfg["agent"][surface.name]["prompt"]]


def _read_commit_message(_surface, _monkeypatch) -> list[str]:
    """What Sage writes into a person's git history — de-branded once rather than per-pack, so a
    change of pack cannot rewrite a commit that already happened."""
    return sorted(_commit_messages())


_READERS = {
    "api-brand": _read_api_brand,
    "entry-page": _read_entry_page,
    "shell-dom": _read_shell_dom,
    "python-prose": _read_python_prose,
    "agents-md": _read_agents_md,
    "agent-prompt": _read_agent_prompt,
    "commit-message": _read_commit_message,
}


# --- what is reachable in the code ----------------------------------------------------------------


def _discovered(kind: str) -> set[str]:
    if kind == "entry-page":
        return {p.name for p in WORKBENCH.glob("*.html")}
    if kind == "python-prose":
        return {str(p.relative_to(BACKEND)) for p in sorted(SAGE.rglob("*.py"))
                if _brand_text_sites(p)}
    if kind == "agents-md":
        return {p.parent.name for p in TEMPLATES.glob("*/AGENTS.md")}
    if kind == "agent-prompt":
        agents = json.loads(OPENCODE.read_text()).get("agent") or {}
        return {key for key, spec in agents.items() if isinstance(spec.get("prompt"), str)}
    raise AssertionError(f"{kind} has no discovery")


DISCOVERABLE = sorted(kind for kind, spec in LIST["kind"].items() if spec.get("reads"))


# --- the assertions -------------------------------------------------------------------------------


@pytest.mark.parametrize("kind", DISCOVERABLE)
def test_every_surface_reachable_in_the_code_is_on_the_coverage_list(kind):
    """A surface added without being listed is the failure, not a gap somebody notices later."""
    assert _discovered(kind) == {s.name for s in SURFACES if s.kind == kind}


@pytest.mark.parametrize("kind", DISCOVERABLE)
def test_dropping_any_one_surface_from_the_list_fails(kind):
    """The check above is an equality and not a subset, which is the difference between a coverage
    list and a wish. Proved by removing each entry in turn rather than asserted in a comment."""
    listed = {s.name for s in SURFACES if s.kind == kind}
    assert listed, f"{kind} lists nothing"
    reachable = _discovered(kind)
    for name in listed:
        assert reachable != listed - {name}


def test_the_list_and_the_probes_name_the_same_kinds():
    """A listed kind nothing can read is aspirational; a probe nothing lists is unrecorded."""
    assert set(_READERS) == set(LIST["kind"]) == {s.kind for s in SURFACES}


@pytest.mark.parametrize("surface", SURFACES, ids=lambda s: s.id)
def test_no_forbidden_word_reaches_a_person(surface, monkeypatch):
    chunks = _READERS[surface.kind](surface, monkeypatch)
    assert chunks, f"{surface.id} produced nothing to read — it cannot have been checked"

    exempt = [LIST["exemption"][name]["text"] for name in surface.entry.get("exempt", [])]
    for phrase in exempt:
        assert any(phrase in chunk for chunk in chunks), (
            f"{surface.id} is exempted for text it no longer carries: {phrase!r}. An exemption that "
            "outlives its prose hides the next leak."
        )

    hits = []
    for chunk in chunks:
        body = chunk
        for phrase in exempt:
            body = body.replace(phrase, " ")
        for m in _FORBIDDEN.finditer(body):
            hits.append(f"{m.group(0)!r} in …{body[max(0, m.start() - 70):m.end() + 50]}…")
    assert not hits, f"{surface.id} still says our words under a partner's pack:\n" + "\n".join(hits)

    strict = LIST["kind"][surface.kind].get("no_unresolved_braces", False)
    stale = {f"{{{token}}}" for chunk in chunks for token in _unresolved(chunk, strict=strict)}
    assert not stale, (
        f"{surface.id} leaves {sorted(stale)} unresolved. `text()` prints a token it does not know "
        "exactly as written, so a near miss like {assistantname} boots fine and reaches a person."
    )

    if surface.entry["shows_pack"]:
        assert any(SENTINEL in chunk for chunk in chunks), (
            f"{surface.id} carries none of the pack's words, so it cannot prove anything. Either "
            "the pack never reached it, or the surface is empty and the pass is a false one."
        )


def test_every_exemption_is_used():
    """An exemption is for prose that legitimately cannot re-brand. One nothing claims is either a
    surface that was dropped or a leak somebody talked themselves out of."""
    claimed = {name for s in SURFACES for name in s.entry.get("exempt", [])}
    assert claimed == set(LIST["exemption"])


def test_the_sentinels_are_a_pack_no_partner_would_ship():
    """A pass has to be evidence the pack reached the surface, not evidence the defaults read
    cleanly. Every value differs from the default and none of them is a word being hunted."""
    for key, value in PACK.items():
        if isinstance(value, str):
            assert value != brand.DEFAULT.get(key)
    for key, forms in PACK["nouns"].items():
        assert forms != brand.DEFAULT["nouns"][key]
    assert not _FORBIDDEN.search(json.dumps(PACK))


def test_an_identifier_is_not_a_word():
    """ADR-0014: the overlay renames prose and never identifiers, so none of these is a leak. The
    exclusion is case-sensitive whole-word matching, and it is a decision, not a lucky regex."""
    for identifier in (".sage/scratch", "sage-chat", "domino_data", "DOMINO_API_HOST",
                       "DatasetClient", "get_dataset", "./img/domino-logo.svg", "src/appQuery.ts",
                       "sage@dominodatalab.com", "sageBuilder"):
        assert not _FORBIDDEN.search(identifier), identifier


def test_prose_naming_us_is_a_word():
    """The other half of the same claim: the scan is not merely never firing."""
    for prose in ("Ask Sage about this app.", "Manage the setting in Domino.",
                  "ML Studio is the other product.", "No files in this Dataset.",
                  "Open it from the Gallery.", "the way the Workbench works today"):
        assert _FORBIDDEN.search(prose), prose


def test_text_sage_did_not_write_is_never_scanned():
    """The AC's exemption, and it holds by construction rather than by a list of allowed phrases.

    A Resource the user named after the company still reaches the screen intact — `brand.text()`
    does not rescan a substituted value — and the probe never sees it, because what the probe reads
    is the site: our literal, with a marker where each of their values goes.
    """
    assert brand.text("Couldn't open {name}.", name="domino-demo") == "Couldn't open domino-demo."
    assert brand.text("{platformName} said: {said}", said="Dataset 'Domino' not found") == (
        f"{SENTINEL}PLATFORM said: Dataset 'Domino' not found"
    )
    scanned = _resolve_site("{platformName} said: {said}", ["said"])
    assert scanned == f"{SENTINEL}PLATFORM said: {RUNTIME_VALUE}"
    assert not _FORBIDDEN.search(scanned)


# --- reading the code -----------------------------------------------------------------------------


def _unresolved(chunk: str, *, strict: bool) -> set[str]:
    """Tokens still written as tokens after the pack has been applied.

    A key spelled exactly right always resolves, so what survives is a near miss — `{assistantname}`
    for `{assistantName}` — and `text()` prints a near miss verbatim rather than raising, which is
    correct for booting and invisible to the lint. This is the half of the job only a real pack
    can do. Where a surface is nothing but our own sentences, the bar is higher: any brace left is
    a token nothing fills.
    """
    found = set(_BRACED.findall(chunk))
    if strict or not found:
        return found
    pack_keys = {key.lower() for key in _pack_tokens()}
    return {token for token in found if token.lower() in pack_keys}


def _pack_tokens() -> set[str]:
    """Every name a string may write between braces: the pack's own string keys, and both forms of
    each noun."""
    pack = brand.load()
    names = {key for key, value in pack.items() if isinstance(value, str)}
    return names | set(pack["nouns"]) | {key + "Plural" for key in pack["nouns"]}


def _resolve_site(template: str, keywords: list[str]) -> str:
    """One `brand.text()` site as the pack resolves it, with a marker for every value the caller
    fills in. The marker is what keeps their words out: a Resource name or a platform error body
    arrives through exactly those keywords."""
    return brand.text(template, **{key: RUNTIME_VALUE for key in keywords})


def _is_brand_text(call: ast.Call) -> bool:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id == "brand_text"
    return (isinstance(func, ast.Attribute) and func.attr == "text"
            and isinstance(func.value, ast.Name) and func.value.id == "brand")


def _literal(node: ast.AST | None) -> str | None:
    """A string the author wrote, or None when the argument is built at runtime. Implicit and `+`
    concatenation both count: a sentence wrapped over three lines is still one sentence."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _literal(node.left), _literal(node.right)
        return None if left is None or right is None else left + right
    return None


def _brand_text_sites(path: Path) -> list[tuple[str, list[str]]]:
    """Every `brand.text("…", …)` in one module, as (template, keyword names)."""
    sites = []
    for node in ast.walk(ast.parse(path.read_text())):
        if not (isinstance(node, ast.Call) and _is_brand_text(node)):
            continue
        template = _literal(node.args[0]) if node.args else None
        if template is not None:   # a whole document or a variable — read as its own surface
            sites.append((template, [k.arg for k in node.keywords if k.arg]))
    return sites


_FUNC = (ast.FunctionDef, ast.AsyncFunctionDef)
# The calls that write a line into somebody's git history.
_COMMIT_CALLS = {"commit_all", "commit_and_push", "finalize_merge", "seed_and_push"}


def _scope_nodes(scope: ast.AST) -> list[ast.AST]:
    """This scope's own nodes, not a nested function's. A message assembled in one function must
    not be resolved from a same-named local in another."""
    out = [scope]

    def walk(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, _FUNC):
                continue
            out.append(child)
            walk(child)

    walk(scope)
    return out


def _message_values(node: ast.AST | None, assigned: dict[str, list[str]], depth: int = 0) -> list[str]:
    """Every message this expression can be. An f-string keeps its literal halves and marks the
    interpolation, because what a person typed into the prompt is theirs and is not scanned."""
    if node is None or depth > 3:
        return []
    if isinstance(node, ast.JoinedStr):
        return ["".join(part.value if isinstance(part, ast.Constant) and isinstance(part.value, str)
                        else RUNTIME_VALUE for part in node.values)]
    if isinstance(node, ast.IfExp):
        return (_message_values(node.body, assigned, depth + 1)
                + _message_values(node.orelse, assigned, depth + 1))
    if isinstance(node, ast.Name):
        return assigned.get(node.id, [])
    literal = _literal(node)
    return [literal] if literal is not None else []


def _commit_messages() -> set[str]:
    found: set[str] = set()
    for path in sorted(SAGE.rglob("*.py")):
        tree = ast.parse(path.read_text())
        for scope in [tree] + [n for n in ast.walk(tree) if isinstance(n, _FUNC)]:
            nodes = _scope_nodes(scope)
            assigned: dict[str, list[str]] = {}
            for node in nodes:
                if not isinstance(node, ast.Assign):
                    continue
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        assigned.setdefault(target.id, []).extend(_message_values(node.value, {}, 1))
            if isinstance(scope, _FUNC):
                found |= set(_default_messages(scope, assigned))
            for node in nodes:
                if not isinstance(node, ast.Call):
                    continue
                name = (node.func.attr if isinstance(node.func, ast.Attribute)
                        else getattr(node.func, "id", None))
                if name not in _COMMIT_CALLS:
                    continue
                arg = node.args[1] if len(node.args) > 1 else None
                for keyword in node.keywords:
                    if keyword.arg == "message":
                        arg = keyword.value
                found |= set(_message_values(arg, assigned))
    return found


def _default_messages(func: ast.AST, assigned: dict[str, list[str]]) -> list[str]:
    """`message=` defaults, which is where the seed's own line lives (`seed.py`)."""
    args = func.args
    positional = args.posonlyargs + args.args
    named = [a.arg for a in positional][len(positional) - len(args.defaults):]
    out = []
    for name, default in zip(named, args.defaults):
        if name == "message":
            out += _message_values(default, assigned)
    for arg, default in zip(args.kwonlyargs, args.kw_defaults):
        if arg.arg == "message" and default is not None:
            out += _message_values(default, assigned)
    return out


_STYLE = re.compile(r"(?is)(<style\b[^>]*>)(.*?)(</style>)")
_SCRIPT = re.compile(r"(?is)(<script\b[^>]*>)(.*?)(</script>)")


def _without_comments(page: str) -> str:
    """An entry page with its HTML, CSS and JS comments removed (`rules.comments`).

    A grep over source is explicitly not this test — it breaks the moment somebody writes a code
    comment. Everything a comment is not stays in, string literals included: door.html builds its
    failure line out of one, and that line names us if nobody templated it. So the comments come
    out inside `<style>` and `<script>` only, where a comment is the one thing they can be.
    """
    assert LIST["rules"]["comments"]["applies_to"] == ["entry-page"]
    page = re.sub(r"<!--.*?-->", " ", page, flags=re.DOTALL)
    page = _STYLE.sub(lambda m: m[1] + _strip_comments(m[2], line=False) + m[3], page)
    return _SCRIPT.sub(lambda m: m[1] + _strip_comments(m[2], line=True) + m[3], page)


def _strip_comments(code: str, *, line: bool) -> str:
    """Comments out of one CSS or JS block, leaving every string exactly as written.

    Quotes and regex literals are tracked rather than pattern-matched, because both can carry the
    two slashes that would otherwise read as the start of a comment: `'https://…'` is a URL and
    `/^https?:\\/\\//` is a test, and eating the rest of either line would hide whatever came next.
    """
    out: list[str] = []
    quote: str | None = None
    in_regex = False
    previous = ""
    i = 0
    while i < len(code):
        char = code[i]
        pair = code[i:i + 2]
        if quote or in_regex:
            if char == "\\":
                out.append(code[i:i + 2])
                i += 2
                continue
            if char == quote or (in_regex and char == "/"):
                quote, in_regex = None, False
            elif in_regex and char == "\n":   # an unterminated regex was a division after all
                in_regex = False
        elif char in "'\"`":
            quote = char
        elif pair == "/*":
            end = code.find("*/", i + 2)
            i = len(code) if end == -1 else end + 2
            continue
        elif line and pair == "//":
            end = code.find("\n", i)
            i = len(code) if end == -1 else end
            continue
        elif line and char == "/" and previous in "(,=:[!&|?{};+~%<>":
            # A `/` where an operand is expected opens a regex; after one it is division.
            in_regex = True
        out.append(char)
        if not char.isspace():
            previous = char
        i += 1
    return "".join(out)
