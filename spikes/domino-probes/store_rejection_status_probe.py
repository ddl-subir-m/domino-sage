#!/usr/bin/env python3
"""Which Flight status does Domino put on a STORE-SIDE rejection? (#399)

Run INSIDE a Domino workspace on cloud-dogfood. Read-only: every statement here is a SELECT
written to fail, and nothing is created, altered or dropped.

WHY THIS EXISTS
---------------
`failure_kind()` in `backend/sage/resources/provider.py` sorts a Data Source failure into three
populations and says a different sentence for each. It decides using the pyarrow exception class
first, then the gRPC status word in `Flight returned <status> error, with message: ...`.

Three strings were captured live on 2026-09-17 and all three are category 1 or 2:

    unavailable      / failed to connect ... Connection refused   -> never_delivered
    not found        / no credentials for user <user>             -> setup_fault
    invalid argument / Type: configObjectError, Subtype: ...      -> setup_fault

NO category-3 string — the store receiving a statement and objecting to it — has ever been
captured from the real proxy. That `not found` and `invalid argument` also carry genuine store
objections is INFERRED, not read. This probe reads it.

WHAT THE ANSWER CHANGES
-----------------------
`answered` is the default in `failure_kind`, so an unclassified message is shown rather than
swallowed and the inference is safe either way. What this settles is whether any status is in the
WRONG tuple:

  * A store objection arriving as `unauthenticated`, `permission denied`, `unauthorized`,
    `unavailable`, `deadline exceeded` or `cancelled` means that status must come OUT of its tuple,
    because a real store answer would be misreported as plumbing.
  * Anything else confirms the inference, and the comment above those tuples can drop the word
    INFERRED.

USAGE
-----
    python3 spikes/domino-probes/store_rejection_status_probe.py

Paste the whole output back. It carries no credential: the statements are nonsense table names, and
a store objection quotes the name you sent, not your token. Read it before you paste anyway — a
transport failure (which is not what this provokes) can quote a peer address.
"""
import sys
import traceback

# Sources known reachable on 2026-09-17. `fpoblete-postgres-service-account` answered 200 in the
# six-source sweep; Snowflake answers whenever it is given a database and a schema (#404).
CASES = [
    ("fpoblete-postgres-service-account", "SELECT * FROM public.__sage_no_such_table_9z7"),
    ("fpoblete-postgres-service-account", "SELECT __sage_no_such_column_9z7 FROM information_schema.tables"),
    ("fpoblete-postgres-service-account", "SELEKT 1"),
    ("Snowflake-Data-Warehouse", 'SELECT * FROM "DWH"."MARTS"."__SAGE_NO_SUCH_TABLE_9Z7"'),
    ("Snowflake-Data-Warehouse", 'SELECT __sage_no_such_column_9z7 FROM "DWH"."MARTS"."DIM_DATE"'),
    ("Snowflake-Data-Warehouse", "SELEKT 1"),
]


def _classifier():
    """`failure_kind` if Sage is importable here, else None. The raw strings are the point; this
    only says what Sage would have done with them."""
    for path in ("/opt/sage/backend", "backend"):
        if path not in sys.path:
            sys.path.insert(0, path)
    try:
        from sage.resources.provider import failure_kind
        return failure_kind
    except Exception as e:
        print(f"(sage not importable here: {type(e).__name__}: {e} — raw strings only)\n")
        return None


def main() -> int:
    try:
        from domino_data.data_sources import DataSourceClient
    except ImportError:
        print("domino_data is not importable. Run this inside the workspace, with the Sage venv:")
        print("  /opt/sage/backend/.venv/bin/python spikes/domino-probes/store_rejection_status_probe.py")
        return 2

    failure_kind = _classifier()
    seen = []
    for name, sql in CASES:
        print("=" * 100)
        print(f"SOURCE : {name}")
        print(f"SQL    : {sql}")
        try:
            DataSourceClient().get_datasource(name).query(sql).to_pandas()
        except Exception as e:
            ctx = e.__context__
            raw = str(e)
            print(f"  exception class   : {type(e).__name__}")
            print(f"  __context__ class : {type(ctx).__name__ if ctx is not None else None}")
            print(f"  raw str(e)        : {raw[:500]}")
            if failure_kind:
                kind, payload = failure_kind(e)
                print(f"  Sage would say    : {kind}")
                print(f"  payload shown     : {payload[:200]}")
                seen.append((name, kind, raw[:90]))
            print()
        else:
            # A statement written to fail that did not fail is a broken probe, not a finding.
            print("  NO ERROR — this statement was supposed to be rejected. Probe case is stale.\n")

    print("=" * 100)
    print("SUMMARY — every one of these SHOULD be `answered`:")
    for name, kind, raw in seen:
        flag = "  OK" if kind == "answered" else "  <-- WRONG TUPLE, see the docstring"
        print(f"  {kind:16s} {name:38s} {raw}{flag}")
    wrong = [s for s in seen if s[1] != "answered"]
    print()
    print(f"  {len(seen)} rejections captured, {len(wrong)} misclassified.")
    if wrong:
        print("  A status carrying a real store objection is in the wrong tuple in provider.py.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(1)
