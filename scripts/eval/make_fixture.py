#!/usr/bin/env python3
"""Write the synthetic orders fixture for the #702 evaluation, and the answers it should give.

    python3 scripts/eval/make_fixture.py

Deterministic: the same seed writes the same bytes, so `fixture/orders.csv` can be regenerated and
compared rather than trusted. Every value is invented. 1,500 rows, which is more than a single
response cap shows at once, so an app that only renders the first page is visible against the
totals in `fixture/expected.json`.
"""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
import json
import pathlib
import random

SEED = 702
ROWS = 1500
REGIONS = ["North", "South", "East", "West"]
PRODUCTS = {  # product -> (category, unit price)
    "Trail Shoe": ("Footwear", 89.50),
    "Road Shoe": ("Footwear", 119.00),
    "Rain Shell": ("Apparel", 145.25),
    "Base Layer": ("Apparel", 39.99),
    "Wool Sock": ("Apparel", 14.75),
    "Day Pack": ("Gear", 64.00),
    "Head Lamp": ("Gear", 29.95),
    "Water Filter": ("Gear", 49.50),
}
START = dt.date(2026, 1, 1)
DAYS = 181  # through 2026-06-30

HERE = pathlib.Path(__file__).resolve().parent / "fixture"


def rows() -> list[dict]:
    rng = random.Random(SEED)
    names = sorted(PRODUCTS)
    out = []
    for i in range(1, ROWS + 1):
        product = rng.choice(names)
        category, price = PRODUCTS[product]
        units = rng.randint(1, 20)
        out.append({
            "order_id": f"ORD-{i:05d}",
            "order_date": (START + dt.timedelta(days=rng.randrange(DAYS))).isoformat(),
            "region": rng.choice(REGIONS),
            "product": product,
            "category": category,
            "units": units,
            "unit_price": f"{price:.2f}",
            "revenue": f"{units * price:.2f}",
        })
    return out


def main() -> None:
    data = rows()
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(data[0]), lineterminator="\n")
    writer.writeheader()
    writer.writerows(data)
    raw = buf.getvalue().encode()
    HERE.mkdir(parents=True, exist_ok=True)
    (HERE / "orders.csv").write_bytes(raw)

    def total(pred=lambda r: True) -> float:
        return round(sum(float(r["revenue"]) for r in data if pred(r)), 2)

    expected = {
        "fixture": "orders.csv",
        "sha256": hashlib.sha256(raw).hexdigest(),
        "rows": len(data),
        "columns": list(data[0]),
        "total_revenue": total(),
        "total_units": sum(r["units"] for r in data),
        "revenue_by_region": {g: total(lambda r, g=g: r["region"] == g) for g in REGIONS},
        "revenue_by_category": {c: total(lambda r, c=c: r["category"] == c)
                                for c in sorted({v[0] for v in PRODUCTS.values()})},
        "rows_by_region": {g: sum(1 for r in data if r["region"] == g) for g in REGIONS},
    }
    (HERE / "expected.json").write_text(json.dumps(expected, indent=2) + "\n")
    print(f"wrote {len(data)} rows, sha256 {expected['sha256'][:16]}")


if __name__ == "__main__":
    main()
