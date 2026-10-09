"""What a Chat answer does not say about how Sage works (#733).

The pinned prompt says never to mention tools, and a model that was refused by one opened its answer
with the refusal anyway: "I cannot write to the findings file through artifact_write, but I can
provide a summary." So the rule is held where the answer ships, not where it is asked for. A sentence
that names one of Sage's tools, or the findings file, is about Sage and not about the data, and is
dropped. The rest of the answer is kept as written.
"""

from __future__ import annotations

import re

from ..liveread import mcp

# Identifiers, never words: `read`, `grep` and `skill` are English, and a sentence using them is not
# about a tool. Every name here has an underscore, and OpenCode's `<server>_` prefix still ends in it.
TOOL_NAMES = frozenset({
    *(t["name"] for t in mcp.TOOLS), "live_read", "artifact_write", "delegated_model_call",
    "analyze_text", "sage_source_map", "apply_patch",
})
_FINDINGS = r"findings(?:\.md|\s+file)\b"
_SENTENCE = re.compile(r"(?<=[.!?])[ \t]+")


def unnarrated(text: str, prompt: str) -> str:
    """`text` without the sentences that narrate Sage's plumbing. A name the person typed is theirs
    to ask about and stays; code in a fence is left as it was written."""
    names = [n for n in TOOL_NAMES if n not in prompt]
    terms = [rf"(?<![A-Za-z0-9]){re.escape(n)}(?![A-Za-z0-9_])" for n in sorted(names)]
    if not re.search(_FINDINGS, prompt, re.IGNORECASE):
        terms.append(_FINDINGS)
    if not terms:
        return text
    plumbing = re.compile("|".join(terms), re.IGNORECASE)
    if not plumbing.search(text):
        return text

    out: list[str] = []
    fenced = False
    for line in text.split("\n"):
        if line.lstrip().startswith("```"):
            fenced = not fenced
        if fenced or line.lstrip().startswith("```") or not plumbing.search(line):
            out.append(line)
            continue
        kept = " ".join(s for s in _SENTENCE.split(line) if not plumbing.search(s))
        if kept.strip():
            out.append(kept)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()
