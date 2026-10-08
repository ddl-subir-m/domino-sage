"""A request naming `DB.SCHEMA.TABLE` and no store word is a request about a store (#705).

Live: "the Snowflake table DWH.MARTS.MIXPANEL__EVENT" got the data-source card and a working app;
the same table without the word "Snowflake" got none, so the app recorded no store and its code
reached for one on its own. The three-part name is the store word the person did not say.
"""
from __future__ import annotations

import pytest

from sage.resources.table_search import offer_sources

SOURCE = {"id": "69d5685b8115e1376d36b4ae", "name": "Snowflake-Data-Warehouse",
          "connector": "snowflake"}


@pytest.mark.parametrize("prompt", [
    ("Build an app showing the total number of events in DWH.MARTS.MIXPANEL__EVENT as a KPI, plus "
     "a paginated table of recent events, 50 per page."),
    "Build a dashboard over the Snowflake table DWH.MARTS.MIXPANEL__EVENT with a daily count.",
    "chart signups per week from analytics.public.signups",
])
def test_a_request_about_a_store_gets_an_offer(prompt):
    offer = offer_sources(prompt, [], [SOURCE])

    assert offer is not None
    assert [s["id"] for s in offer.sources] == [SOURCE["id"]]


@pytest.mark.parametrize("prompt", [
    "add a page to app.config.json",
    "move the chart into static/app.js",
    "fix the import in sage_queries.py",
    "rename src.components.chart.tsx",
    "bump the version to 1.2.3",
    "make the header blue and add a dark mode toggle",
])
def test_an_ordinary_request_gets_none(prompt):
    assert offer_sources(prompt, [], [SOURCE]) is None
