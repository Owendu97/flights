"""Import 6routes_v4.json (real flight price snapshots) into SQLite.

Idempotent: running multiple times is safe — UNIQUE constraints cause
existing rows to be ignored / replaced. Use --replace to wipe first.
"""
from __future__ import annotations
import json
import re
import argparse
from datetime import datetime
from pathlib import Path
from . import db

DATA_FILE = Path(__file__).parent.parent / "data" / "6routes_v4.json"

DISCOUNT_RE = re.compile(r"([\d.]+)折")
PRICE_RE = re.compile(r"¥(\d+)")

WD_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def discount_to_rate(s: str) -> float | None:
    m = DISCOUNT_RE.match(s or "")
    return float(m.group(1)) / 10 if m else None


def parse_price_text(s: str) -> int | None:
    m = PRICE_RE.search(s or "")
    return int(m.group(1)) if m else None


def iso_to_label(iso_date: str) -> str:
    """'2026-08-08' → '08-08周六' (so the front-end parses unchanged)."""
    d = datetime.fromisoformat(iso_date).date()
    return f"{d.month:02d}-{d.day:02d}{WD_CN[d.weekday()]}"


def infer_full_price(price: int, discount: str) -> float | None:
    r = discount_to_rate(discount)
    if not r or r == 0:
        return None
    return round(price / r, 2)


def label_to_iso(label: str, year: int) -> str | None:
    """'08-08周六' → '2026-08-08' (year resolved from base depdate)."""
    m = re.match(r"(\d{2})-(\d{2})", label)
    if not m:
        return None
    return f"{year}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"


def seed(replace: bool = False) -> dict:
    db.init_db()
    conn = db.get_conn()
    cur = conn.cursor()

    if replace:
        cur.execute("DELETE FROM price_calendar")
        cur.execute("DELETE FROM flight_snapshot")
        cur.execute("DELETE FROM meta_routes")
        cur.execute("DELETE FROM scrape_log")

    with open(DATA_FILE, encoding="utf-8") as f:
        data = json.load(f)

    routes_imported = 0
    cal_imported = 0
    flight_imported = 0
    scrape_time = datetime.utcnow().isoformat(timespec="seconds") + "Z"

    for route_data in data:
        route = route_data["route"]
        orig = route_data["orig"]
        dest = route_data["dest"]
        note = route_data.get("note", "")
        depdate = route_data["depdate"]
        base_year = int(depdate[:4])

        cur.execute(
            """INSERT INTO meta_routes (route, orig, dest, note, depdate, is_real)
               VALUES (?, ?, ?, ?, ?, 1)
               ON CONFLICT(route) DO UPDATE SET
                 orig=excluded.orig, dest=excluded.dest,
                 note=excluded.note, depdate=excluded.depdate,
                 updated_at=CURRENT_TIMESTAMP""",
            (route, orig, dest, note, depdate),
        )
        routes_imported += 1

        for c in route_data["calendar"]:
            flight_iso = label_to_iso(c["date"], base_year)
            if not flight_iso:
                continue
            price = parse_price_text(c["price"])
            days_before = (
                datetime.fromisoformat(flight_iso).date()
                - datetime.fromisoformat(depdate).date()
            ).days
            is_lowest = 1 if "低" in c["price"] else 0
            cur.execute(
                """INSERT INTO price_calendar
                   (route, flight_date, depdate_anchor, days_before_departure,
                    price, is_lowest, raw_text)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(route, flight_date, depdate_anchor) DO UPDATE SET
                     price=excluded.price,
                     is_lowest=excluded.is_lowest,
                     raw_text=excluded.raw_text,
                     updated_at=CURRENT_TIMESTAMP""",
                (route, flight_iso, depdate, days_before,
                 price, is_lowest, c["price"]),
            )
            cal_imported += 1

        for f in route_data["flights"]:
            discount = f["discount"]
            rate = discount_to_rate(discount)
            full = infer_full_price(f["price"], discount)
            cur.execute(
                """INSERT INTO flight_snapshot
                   (route, flight_date, flight_no, airline, aircraft,
                    dep_airport, arr_airport, dep_time, arr_time,
                    stops, time_bucket, price,
                    discount_rate, inferred_full_price, is_main_flight)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                   ON CONFLICT(route, flight_date, flight_no) DO UPDATE SET
                     airline=excluded.airline,
                     aircraft=excluded.aircraft,
                     dep_time=excluded.dep_time, arr_time=excluded.arr_time,
                     stops=excluded.stops, time_bucket=excluded.time_bucket,
                     price=excluded.price, discount_rate=excluded.discount_rate,
                     inferred_full_price=excluded.inferred_full_price,
                     updated_at=CURRENT_TIMESTAMP""",
                (route, depdate, f["flight_no"], f.get("airline", ""),
                 f.get("aircraft", ""), orig, dest,
                 f["dep_time"], f["arr_time"],
                 0 if not f.get("has_stops") else 1,
                 f["time_bucket"], f["price"], rate, full),
            )
            flight_imported += 1

        # scrape_log
        cur.execute(
            """INSERT INTO scrape_log
               (route, depdate_anchor, finished_at, flights_count, calendar_count, status)
               VALUES (?, ?, ?, ?, ?, 'seeded')""",
            (route, depdate, scrape_time,
             len(route_data["flights"]), len(route_data["calendar"])),
        )

    conn.commit()
    conn.close()
    return {
        "routes": routes_imported,
        "calendar_rows": cal_imported,
        "flight_rows": flight_imported,
        "source": str(DATA_FILE),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--replace", action="store_true",
                        help="wipe existing rows before import")
    args = parser.parse_args()
    res = seed(replace=args.replace)
    print(f"[seed] routes={res['routes']}  calendar={res['calendar_rows']}  "
          f"flights={res['flight_rows']}  source={res['source']}")
    print(f"[seed] table counts: {db.table_counts()}")
