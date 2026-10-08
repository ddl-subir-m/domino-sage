"""Which store refusals are a fault in the app's own SQL (#708).

A query that fails to compile fails the same way every time, and the store's message names what to
fix, so the build sends it back for a repair. Anything else a store says — it is down, it timed
out, it refused this person — is outside the app, and editing correct SQL to chase it would be
wrong; those keep #203's notice.

Judged from the message text alone, because that is all the preview keeps of a failure. Snowflake's
codes, as its own error text carries them:

- `000904` invalid identifier: a column the table does not have.
- `001003` syntax error: the statement does not parse.
- `002003` object does not exist or not authorized. Snowflake says the SAME thing for a table that
  is not there and for one this person may not read, so on its own it is ambiguous. It counts as
  the app's fault only when the object it names is not one of the Binding's recorded tables — a name
  the app invented. On a recorded table it is a permission refusal, and with no tables recorded
  there is nothing to tell the two apart by, so it is not judged a typo.
"""
from __future__ import annotations

import re
from collections.abc import Collection

_ALWAYS = re.compile(r"\b(000904|001003)\b")
_MISSING = re.compile(r"\b002003\b.*?Object '([^']+)' does not exist", re.DOTALL)


def is_compile_fault(message: str, bound_tables: Collection[str]) -> bool:
    """True when `message` is the store refusing to compile the app's own SQL."""
    if _ALWAYS.search(message):
        return True
    missing = _MISSING.search(message)
    if missing is None or not bound_tables:
        return False
    name = missing.group(1).rsplit(".", 1)[-1].strip('"').upper()
    return name not in {t.upper() for t in bound_tables}
