#!/usr/bin/env python3
"""Guardrail probe — what the Domino gateway will refuse, answered in about two seconds.

A gateway guardrail refuses what a turn CARRIES, and the refusal arrives buried: the gateway
answers 400, the shim wraps it in a 502, OpenCode quotes the shim. Worse, the person reasons about
their *data* while the guardrail reasons about a couple of regexes — so "there is no data that
matches" gets said about a file with an `email` column in it. This turns that argument into a loop:
send a payload, read GUARDRAIL or OK.

MEASURED on sage.gcp.cs.domino.tech, 2026-09-11. One guardrail, `Block PII`, one payload each:

    jane.doe@example.com                        GUARDRAIL    email
    555-123-4567                                GUARDRAIL    phone
    4871715921430428                            GUARDRAIL    card number
    222-33-4444                                 GUARDRAIL    SSN
    Prepared by Jane Doe, analyst               OK
    user: sales_manager                         OK
    1 Market Street, San Francisco, CA 94105    OK
    1951-11-28                                  OK           a date of birth is not a pattern
    Q3 revenue forecast was 1234567 dollars     OK
    ssn / card_number                           OK           the WORDS, alone

The last line is the one worth keeping: the VALUES trip it and the column names do not, so this is
four regexes and not a model reading a schema. Names, addresses and dates of birth pass however
obviously personal they are — a file can be full of identifiable people and pass, and a single
support email in a code comment fails. Do not assume this set on another gateway: the nest quoted in
`test_a_guardrail_refusal_says_which_guardrail.py` comes from a different one running "Block phone
numbers", whose pattern accepted a decimal point as its right boundary and so refused a file of
seven-digit sales forecasts. Probe first; the names and the patterns are the administrator's.

Three modes:

    guardrail-probe.py say  "some text"     one payload, for a hypothesis
    guardrail-probe.py file data.csv        header + 3 sample rows, then per COLUMN
    guardrail-probe.py scan ./app           prescan a tree locally, confirm candidates

`file` sends what Sage inlines for an @mentioned file — the header and three sample rows
(`describe._describe_tabular`), not the whole file. A column whose matching value sits in row 900
therefore reads OK here and still refuses a turn that reads the file. A clean verdict from this
tool is evidence, not proof.

`scan` prescans with those four shapes locally and only spends a gateway call on files that hit, so a
whole `src/` tree costs a handful of calls instead of hundreds.

Reads GATEWAY_BASE_URL and GATEWAY_API_KEY from backend/.env in-process and never prints the key.
That value ALREADY ENDS IN /v1 (see gateway/client.py) — appending another gives a bare
{"detail":"Not Found"} 404 that reads like an auth or routing fault and is not one.
"""
from __future__ import annotations

import csv
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TIMEOUT_S = 45
# The four shapes `Block PII` was measured to refuse, for the local prescan only. Deliberately
# loose: a false candidate costs one gateway call, a missed one costs the whole point of the scan.
# CARD and SSN are here because leaving them out is the mistake this file exists to stop somebody
# repeating — the first pass of this tool carried EMAIL and PHONE alone, on the belief that those
# were the whole set, and would have called a file of card numbers and SSNs clean.
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE = re.compile(r"(?<!\d)(?:\+?\d{1,2}[ .-]?)?\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}(?!\d)")
SSN = re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)")
CARD = re.compile(r"(?<![\d-])(?:\d[ -]?){13,19}(?![\d-])")
PATTERNS = (EMAIL, PHONE, SSN, CARD)
SKIP = {".git", "node_modules", "dist", "build", ".venv", "deps", "__pycache__", ".ruff_cache"}
SAMPLE_ROWS = 3


def _env() -> dict[str, str]:
    env: dict[str, str] = {}
    path = ROOT / "backend" / ".env"
    if not path.exists():
        sys.exit(f"no {path} — copy .env.example and fill in GATEWAY_BASE_URL / GATEWAY_API_KEY")
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def ask(text: str) -> str:
    """GUARDRAIL: <name> / OK / an explanation. One bounded model call."""
    env = _env()
    base = env.get("GATEWAY_BASE_URL", "").rstrip("/")
    if not base:
        return "NO-CONFIG: GATEWAY_BASE_URL is empty"
    body = json.dumps({
        "model": env.get("SAGE_MODEL_ASK") or env.get("SAGE_MODEL_DEFAULT") or "",
        "messages": [{"role": "user", "content": text}],
        "max_tokens": 16,
    }).encode()
    request = urllib.request.Request(
        f"{base}/chat/completions", data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {env.get('GATEWAY_API_KEY', '')}"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            response.read()
        return "OK"
    except urllib.error.HTTPError as err:
        raw = err.read().decode("utf-8", "replace")
        if "Blocked by guardrail" in raw:
            name = raw.split("Blocked by guardrail:", 1)[1].split('"')[0].split("\\")[0]
            return f"GUARDRAIL: {name.strip(' .;:')}"
        return f"HTTP-{err.code}: {raw[:200]}"
    except Exception as err:                    # a probe reports its failures, it never raises
        return f"ERROR: {type(err).__name__}: {err}"


def _blocked(verdict: str) -> bool:
    return verdict.startswith("GUARDRAIL")


def do_say(text: str) -> int:
    print(ask(text))
    return 0


def do_file(path: Path) -> int:
    with path.open(encoding="utf-8", errors="ignore", newline="") as handle:
        rows = list(csv.reader(handle))[:SAMPLE_ROWS + 1]
    if not rows:
        print("empty file")
        return 0
    header, sample = rows[0], rows[1:]
    verdict = ask("\n".join(",".join(r) for r in rows))
    print(f"sample of {path.name} ({len(header)} columns) -> {verdict}")
    if not _blocked(verdict):
        print("\nThis file's sample does not trip the guardrail. Remember it is the first "
              f"{SAMPLE_ROWS} rows only — a value further down still would.")
        return 0
    print("\nPer column:")
    for i, name in enumerate(header):
        column = "\n".join([name] + [r[i] for r in sample if i < len(r)])
        column_verdict = ask(column)
        print(f"  {name!r}: {column_verdict}"
              f"{'  <-- TRIPS IT' if _blocked(column_verdict) else ''}")
    return 0


def do_scan(root: Path) -> int:
    hits: dict[Path, list[str]] = {}
    for path in root.rglob("*"):
        if not path.is_file() or path.is_symlink() or any(p in SKIP for p in path.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if any(pat.search(line) for pat in PATTERNS):
                hits.setdefault(path, []).append(
                    f"    {path.relative_to(root)}:{number}: {line.strip()[:90]}")
    if not hits:
        print(f"No email, phone, SSN or card patterns under {root}")
        return 0
    print(f"{len(hits)} candidate file(s); confirming each against the gateway:\n")
    for path, lines in hits.items():
        verdict = ask("\n".join(ln.split(": ", 1)[-1] for ln in lines[:6]))
        print(f"  {path.relative_to(root)} -> "
              f"{'BLOCKS THE TURN' if _blocked(verdict) else 'ok (pattern only)'}")
        if _blocked(verdict):
            print("\n".join(lines[:6]))
    return 0


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[1] not in ("say", "file", "scan"):
        print(__doc__)
        return 2
    mode, target = argv[1], argv[2]
    if mode == "say":
        return do_say(target)
    path = Path(target)
    if not path.exists():
        sys.exit(f"no such path: {path}")
    return do_file(path) if mode == "file" else do_scan(path)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
