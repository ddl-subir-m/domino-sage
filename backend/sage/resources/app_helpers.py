"""Which name a Built App's Sage-owned `src/` helpers go by (#119).

The template ships neutral names — `appLlm.ts`, `appQuery.ts` — because the Built App repo is a
surface a partner's own customer reads, and these names are prose that cannot be per-pack: they are
imported by code Sage does not own. So they are de-branded once, permanently, for every pack
including Domino's (ADR-0014).

Apps seeded before that change keep the `sage*` names they were born with. Migrating them was
rejected: the imports live in code the agent wrote, in files Sage does not own, and rewriting a
user's source to fix a cosmetic name is the worst trade available.

That makes the name a property of the app in hand rather than a constant, and this is the ONE place
that answers it. Every caller takes a `HelperNames` and asks it; nobody works the scheme out again.

ONE TOKEN PER HELPER, not one per file. `appLlm` is the file's stem, the module specifier its
sibling imports, and the prefix of the generated config's export (`appLlmConfig`) — so a single
substitution over the template's text says all three at once, which is what `localize` is.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class HelperNames:
    """The stem each Sage-owned helper goes by in one app. Every path derives from a stem."""

    base: str
    query: str
    llm: str
    model_api: str
    dir: str = "static/sage"
    ext: str = "js"

    @property
    def stems(self) -> tuple[str, ...]:
        return (self.base, self.query, self.llm, self.model_api)

    def _path(self, stem: str, suffix: str = "") -> str:
        return f"{self.dir}/{stem}{suffix}.{self.ext}"

    @property
    def base_path(self) -> str:
        return self._path(self.base)

    @property
    def query_path(self) -> str:
        return self._path(self.query)

    @property
    def llm_path(self) -> str:
        return self._path(self.llm)

    @property
    def llm_config_path(self) -> str:
        return self._path(self.llm, ".config")

    @property
    def model_api_path(self) -> str:
        return self._path(self.model_api)

    @property
    def model_api_config_path(self) -> str:
        return self._path(self.model_api, ".config")

    @property
    def paths(self) -> tuple[str, ...]:
        """Every file this scheme names, the generated configs included."""
        return (self.base_path, self.query_path, self.llm_path, self.llm_config_path,
                self.model_api_path, self.model_api_config_path)

    @property
    def owned(self) -> frozenset[str]:
        """The app sources Sage writes and rewrites itself, for the scans that must skip them.

        `base_path` is not among them: it is seeded once and never rewritten, so a Binding found in
        it would be a real reference. This is `service.py`'s `_SAGE_OWNED_SOURCES`, resolved.
        """
        return frozenset({self.query_path, self.llm_path, self.llm_config_path,
                          self.model_api_path, self.model_api_config_path})

    def localize(self, text: str) -> str:
        """Text written in the TEMPLATE's names, said in this app's names instead.

        Used two ways, and it is the same substitution both times: to turn a template PATH into the
        one this app has, and to turn the template file's own CONTENTS — its sibling imports, its
        config export, its comments — into what an app that kept the old names can compile.

        A plain replace over the whole text, because the stem is the only form the name takes. No
        stem is a substring of another, so the order of the loop does not matter.
        """
        for template_stem, ours in zip(TEMPLATE.stems, self.stems):
            text = text.replace(template_stem, ours)
        return text


#: What the template ships, and what every app seeded after #119 has.
TEMPLATE = HelperNames(base="appBase", query="appQuery", llm="appLlm", model_api="appModelApi")


def helpers_for(app_path: Path, default: HelperNames = TEMPLATE) -> HelperNames:
    """The names THIS app's helpers go by. `default` is what the app's STACK ships (#490).

    There used to be a second answer here — `sageBase`/`sageQuery`, kept by an app seeded before
    #119 renamed the helpers — but that vintage only ever belongs to a `react-vite` app, and Build
    refuses one of those before this is ever asked (`workspace/stack.py`). One name now.
    """
    return default
