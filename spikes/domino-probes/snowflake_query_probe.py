#!/usr/bin/env python3
"""Can we actually query a Domino Data Source from inside a Sage container?

Run INSIDE a Domino workspace on cloud-dogfood. This blocks the build-time slice: if the
agent cannot query a picked data source during a build session, the whole feature fails.

Beyond pass/fail it answers a DESIGN question a spec cannot:
  Does the data source arrive pre-configured with a warehouse/database/schema, or must
  the user supply them? If they are unset, the picker needs input fields for them, and
  "pick a source" stops being one click. That changes the panel.

It also times the first query. A cold Arrow Flight connection is the latency the build
session pays, and it decides whether querying is inline or backgrounded.

SAFETY: read-only. SELECT and SHOW only, no DDL/DML, no ds.update().

Usage:
  python3 spikes/domino-probes/snowflake_query_probe.py [datasource-name]
  default name: Snowflake-Data-Warehouse
"""
import os, sys, time, traceback

NAME = sys.argv[1] if len(sys.argv) > 1 else "Snowflake-Data-Warehouse"
results = []


def step(label, fn, fatal=False):
    print(f"\n--- {label}")
    t0 = time.time()
    try:
        out = fn()
        dt = time.time() - t0
        print(f"    PASS  ({dt:.2f}s)")
        if out is not None:
            print("   ", str(out).replace("\n", "\n    "))
        results.append((label, "PASS", round(dt, 2)))
        return out
    except Exception as e:
        dt = time.time() - t0
        print(f"    FAIL  ({dt:.2f}s)  {type(e).__name__}: {e}")
        print("    --- traceback (last 6 lines) ---")
        for line in traceback.format_exc().strip().splitlines()[-6:]:
            print("    " + line)
        results.append((label, f"FAIL {type(e).__name__}", round(dt, 2)))
        if fatal:
            summary()
            sys.exit(1)
        return None


def summary():
    print("\n" + "=" * 72)
    print("SUMMARY")
    for label, status, dt in results:
        print(f"  {status:<28} {dt:>7.2f}s  {label}")
    print("=" * 72)


print("=" * 72)
print(f"Snowflake query probe -- data source: {NAME}")
print(f"DOMINO_PROJECT_NAME={os.environ.get('DOMINO_PROJECT_NAME','<unset>')}  "
      f"proxy={os.environ.get('DOMINO_DATASOURCE_PROXY_FLIGHT_HOST','<unset>')}")
print("=" * 72)

step("1. import domino_data", lambda: __import__("domino_data").__file__, fatal=True)

from domino_data.data_sources import DataSourceClient  # noqa: E402

def _client():
    # NEVER return the client itself: DataSourceClient.__repr__ includes api_key in
    # plaintext, and step() prints whatever it gets. Report a masked summary instead.
    c = DataSourceClient()
    globals()["_c"] = c
    key = getattr(c, "api_key", None) or ""
    return {"api_key": f"<set, {len(key)} chars>" if key else "<unset>",
            "token_url": getattr(c, "token_url", None),
            "token_file": getattr(c, "token_file", None)}


step("2. construct DataSourceClient() (masked)", _client, fatal=True)
client = _c  # noqa: F821
ds = step(f"3. resolve data source by name ({NAME})",
          lambda: client.get_datasource(NAME), fatal=True)

step("4. data source metadata",
     lambda: {k: getattr(ds, k, "<no attr>") for k in
              ("name", "datasource_type", "auth_type", "owner", "identifier")})

# The cheapest possible query. Proves the Arrow Flight path end to end, and the elapsed
# time here IS the cold-connection cost the build session pays.
step("5. SELECT 1 (cold Flight connection -- note the time)",
     lambda: ds.query("SELECT 1 AS ok").to_pandas().to_string(index=False))

step("6. SELECT 1 again (warm -- the difference is the connection cost)",
     lambda: ds.query("SELECT 2 AS ok").to_pandas().to_string(index=False))

# THE DESIGN QUESTION. If warehouse/database/schema come back NULL, the connector is not
# fully specified and the picker must collect them.
step("7. session context -- is the source fully configured?",
     lambda: ds.query(
         "SELECT CURRENT_USER() AS usr, CURRENT_ROLE() AS role, "
         "CURRENT_WAREHOUSE() AS warehouse, CURRENT_DATABASE() AS db, "
         "CURRENT_SCHEMA() AS schema").to_pandas().to_string(index=False))

# Schema discovery. The build agent needs to know what tables exist to write an app at all.
step("8. can the agent discover tables?",
     lambda: ds.query(
         "SELECT table_catalog, table_schema, table_name "
         "FROM information_schema.tables "
         "WHERE table_schema NOT IN ('INFORMATION_SCHEMA') "
         "ORDER BY table_schema, table_name LIMIT 20").to_pandas().to_string(index=False))

# Step 8 fails with "This session does not have a current database. Call 'USE DATABASE',
# or use a qualified name." That is a session-context gap, NOT missing introspection --
# so retry fully qualified. If this passes, the picker can cascade source -> db -> schema
# -> tables with no typing by the user.
step("8b. introspect with a QUALIFIED name (DWH.INFORMATION_SCHEMA)",
     lambda: ds.query(
         "SELECT table_catalog, table_schema, table_name "
         "FROM DWH.INFORMATION_SCHEMA.TABLES "
         "WHERE table_schema <> 'INFORMATION_SCHEMA' "
         "ORDER BY table_schema, table_name LIMIT 20").to_pandas().to_string(index=False))

step("8c. can we enumerate schemas for a cascading picker?",
     lambda: ds.query("SHOW SCHEMAS IN DATABASE DWH").to_pandas().head(15).to_string(index=False))

step("9. what else is reachable? (only useful if 7 showed no database)",
     lambda: ds.query("SHOW DATABASES").to_pandas().head(10).to_string(index=False))

summary()
print("""
WHAT THIS ESTABLISHED (live, cloud-dogfood, 2026-08-18 -- all steps PASS except 8)

  Querying works from a Sage container. domino_data is preinstalled (conda 3.12), the
  source resolves with NO project attachment, and queries run in 1-4s with no cold-Flight
  penalty -- so querying can be inline in a chat turn.

  The source is NOT fully specified: warehouse is set (DOMINO_WH) but database and schema
  are NULL. Step 8 fails for that reason alone. It is NOT missing introspection --
  qualified names work (8b), and both levels enumerate (9 -> databases, 8c -> schemas).

  So the picker CASCADES and needs no free-text input:
      source -> SHOW DATABASES -> SHOW SCHEMAS IN DATABASE <db>
             -> <db>.INFORMATION_SCHEMA.TABLES
  Each level costs 2-4s, so load lazily on expand rather than prefetching the tree.

  Snowflake identity is a SHARED service account: USR=DOMINO, ROLE=APP_ROLE_DOMINO. Every
  Domino user reads through the same principal, which is why creator-access inheritance in
  a published app adds no privilege -- WITHIN Domino. It does not make a PUBLIC app safe.
""")
