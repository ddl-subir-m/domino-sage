"""#700, slice E step 1: the source note moved into `sage/source_map.py` and says exactly what it said.

The note rides every Build turn's first send and every recovery packet, and
`ContextContinuation.source_map_digest` is the sha256 of it. So a move that changed one byte would
change what every model is told AND every continuation's identity, with nothing visible to show for
it. The golden strings below were captured from `Orchestrator._build_source_note` BEFORE the move,
one fixture per stack, and each fixture walks every branch the note has: the 60-path cap, the
per-file name cap, the size cap, a vendored bundle, a hidden directory, a file the patterns do not
know, and a file with no names at all.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest


def _stack(root: Path, name: str) -> Path:
    (root / ".sage").mkdir(parents=True, exist_ok=True)
    (root / ".sage" / "settings.json").write_text(json.dumps({"stack": name}))
    return root


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _fastapi(root: Path) -> Path:
    _stack(root, "fastapi-antd")
    _write(root, "app.py",
           "import sage_serve\nfrom fastapi import FastAPI\n\napp = FastAPI()\n\n"
           "@app.get(\"/api/summary\")\ndef summary() -> dict:\n    return {}\n\n"
           "async def load(rows):\n    pass\n\nclass Table:\n    def method(self):\n        pass\n")
    _write(root, "helpers.py", "".join(f"def helper_{i:02}():\n    pass\n" for i in range(12)))
    _write(root, ".env", "SECRET=1\n")
    _write(root, "static/index.html", "<html><body><script src=\"static/app.js\"></script></body></html>\n")
    _write(root, "static/app.js", "(function () {\n  const { createElement: h } = React;\n})();\n")
    _write(root, "static/components/MainScreen.js",
           "(function () {\n  window.app = window.app || {};\n  function MainScreen() { return null }\n"
           "  window.app.MainScreen = MainScreen;\n})();\n")
    _write(root, "static/components/Detail.js",
           "const PRIVATE_VALUE = 'do-not-send'\nfunction Detail() { return null }\nwindow.app.Detail = Detail\n")
    _write(root, "static/app.css", "body { margin: 0 }\n")
    _write(root, "static/big.js", "function huge() {}\n" + "// x\n" * 60_000)
    _write(root, "static/vendor/react.production.min.js", "function vendored() {}\n")
    _write(root, "static/.hidden/secret.js", "function hidden() {}\n")
    for i in range(55):
        _write(root, f"static/gen/f{i:02}.js", f"export const gen{i:02} = 1\n")
    return root


def _react(root: Path) -> Path:
    _stack(root, "react-vite")
    _write(root, "src/App.tsx",
           "import \"./App.css\";\nimport MainScreen from \"./screens/MainScreen\";\n\n"
           "function App() {\n  return <MainScreen />;\n}\n\nexport default App;\n")
    _write(root, "src/screens/MainScreen.tsx",
           "export default function MainScreen() {\n  return <main className=\"x\" />;\n}\n"
           "export const TOTAL_LABEL = 'Total'\n  const indented = 1\n")
    _write(root, "src/calc.py", "def count_by_soc(rows):\n    pass\n")
    _write(root, "src/rows.csv", "usubjid,ssn\nABC-001,123-45-6789\n")
    _write(root, "src/notes.ts", "// nothing here\n")
    _write(root, "src/vendor/lib.ts", "export function vendored() {}\n")
    _write(root, "src/.cache/x.ts", "export function hidden() {}\n")
    _write(root, "public/data.csv", "a,b\n1,2\n")
    return root


_FASTAPI_NOTE = (
    'Existing source paths (JSON array, relative to the app directory):\n'
    '["app.py", "helpers.py", "static/app.css", "static/app.js", "static/big.js", '
    '"static/components/Detail.js", "static/components/MainScreen.js", '
    + ", ".join(f'"static/gen/f{i:02}.js"' for i in range(53))
    + ']\nListing limited to the first 60 paths.\n'
    'Top-level names in each of those files (JSON object, path -> names):\n'
    '{"app.py": ["summary", "load", "Table"], "helpers.py": ["helper_00", "helper_01", '
    '"helper_02", "helper_03", "helper_04", "helper_05", "helper_06", "helper_07"], '
    '"static/components/Detail.js": ["PRIVATE_VALUE", "Detail"], '
    + ", ".join(f'"static/gen/f{i:02}.js": ["gen{i:02}"]' for i in range(53))
    + '}\nOpen the relevant files together before editing — the ones whose names the request '
    'touches, in ONE message. Search only if the needed path is not listed.'
)

_REACT_NOTE = (
    'Existing source paths (JSON array, relative to the app directory):\n'
    '["src/App.tsx", "src/calc.py", "src/notes.ts", "src/rows.csv", "src/screens/MainScreen.tsx"]\n'
    'Top-level names in each of those files (JSON object, path -> names):\n'
    '{"src/App.tsx": ["App"], "src/calc.py": ["count_by_soc"], '
    '"src/screens/MainScreen.tsx": ["MainScreen", "TOTAL_LABEL"]}\n'
    'Open the relevant files together before editing — the ones whose names the request '
    'touches, in ONE message. Search only if the needed path is not listed.'
)

# sha256 of each golden note: what `ContextContinuation.source_map_digest` records for that app.
_FASTAPI_DIGEST = "d5a33c1d8f0ffb65b909092d90b97c11d5f03593986792ff23ce2010aeb6674a"
_REACT_DIGEST = "1e8106337872e5b51a6107926f1917cc9f70677a43236c3850266d0240fbbb58"

_STACKS = [pytest.param(_fastapi, _FASTAPI_NOTE, _FASTAPI_DIGEST, id="fastapi-antd"),
           pytest.param(_react, _REACT_NOTE, _REACT_DIGEST, id="react-vite")]


@pytest.mark.parametrize("build,golden,digest", _STACKS)
def test_the_moved_note_is_byte_identical_to_the_old_one(tmp_path, build, golden, digest):
    from sage import source_map

    root = build(tmp_path)

    assert source_map.build_source_note(root) == golden


@pytest.mark.parametrize("build,golden,digest", _STACKS)
def test_the_continuation_digest_of_the_note_is_unchanged(tmp_path, build, golden, digest):
    from sage import source_map

    note = source_map.build_source_note(build(tmp_path))

    assert hashlib.sha256(note.encode()).hexdigest() == digest


@pytest.mark.parametrize("build,golden,digest", _STACKS)
def test_the_orchestrator_and_its_path_rule_read_the_moved_implementation(tmp_path, build, golden,
                                                                          digest):
    """`_scope_gate_applies` decides whether a prompt NAMED a source file off `_source_paths`, and
    the prompt's listing is built off the same call. Two implementations would be two answers."""
    from sage import source_map
    from sage.orchestrator.service import Orchestrator

    root = build(tmp_path)

    assert Orchestrator._build_source_note(root) == golden
    assert Orchestrator._source_paths is source_map.source_paths


def test_an_app_with_no_stack_has_no_note(tmp_path):
    from sage import source_map

    assert source_map.build_source_note(tmp_path) == ""
