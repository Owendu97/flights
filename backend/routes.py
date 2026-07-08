"""FastAPI API endpoints.

Two endpoints:
  GET /api/routes              → list seeded (orig, dest) pairs
  GET /api/snapshot?..&date=.. → one 7-day calendar + same-day flights

If (orig, dest, date) has no real data, the endpoint returns
{"error": "no-route"} with HTTP 200 (so the front-end can decide
silently to fall back to its own synthesis or show a banner).
"""
from __future__ import annotations
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from . import db
from .seed import iso_to_label

router = APIRouter()


def _calendar_window(center_iso: str, n: int = 3) -> list[str]:
    """Returns [center-n ... center ... center+n] as ISO dates."""
    center = datetime.fromisoformat(center_iso).date()
    return [(center + timedelta(days=delta)).isoformat()
            for delta in range(-n, n + 1)]


@router.get("/api/health")
def health() -> dict:
    return {"status": "ok", "db_counts": db.table_counts()}


@router.get("/api/routes")
def list_routes() -> list[dict]:
    conn = db.get_conn()
    rows = conn.execute("""
        SELECT route, orig, dest, note, depdate
          FROM meta_routes
         WHERE is_real = 1
         ORDER BY route
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@router.get("/api/snapshot")
def get_snapshot(
    orig: str = Query(..., min_length=3, max_length=3),
    dest: str = Query(..., min_length=3, max_length=3),
    date: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$"),
) -> dict:
    orig = orig.upper()
    dest = dest.upper()
    try:
        datetime.fromisoformat(date)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid date")

    conn = db.get_conn()

    # Find a real route, allowing reverse lookup
    route_row = conn.execute("""
        SELECT * FROM meta_routes
         WHERE ((orig = ? AND dest = ?) OR (orig = ? AND dest = ?))
           AND is_real = 1
         ORDER BY CASE WHEN orig = ? AND dest = ? THEN 0 ELSE 1 END
         LIMIT 1
    """, (orig, dest, dest, orig, orig, dest)).fetchone()

    if not route_row:
        conn.close()
        return {"error": "no-route", "orig": orig, "dest": dest, "date": date,
                "message": "该航线未导入真实数据，前端可选择合成或重新抓取"}

    route = route_row["route"]
    depdate = route_row["depdate"]

    cal_dates = _calendar_window(date, n=3)
    placeholders = ",".join("?" * len(cal_dates))

    cal_rows = conn.execute(f"""
        SELECT flight_date, raw_text, price, is_lowest
          FROM price_calendar
         WHERE route = ? AND depdate_anchor = ?
           AND flight_date IN ({placeholders})
         ORDER BY flight_date
    """, (route, depdate, *cal_dates)).fetchall()

    flight_rows = conn.execute("""
        SELECT flight_no, airline, aircraft,
               dep_airport, arr_airport, dep_time, arr_time,
               stops, time_bucket, price,
               discount_rate, inferred_full_price
          FROM flight_snapshot
         WHERE route = ? AND flight_date = ?
           AND is_main_flight = 1
         ORDER BY price ASC
    """, (route, date)).fetchall()
    conn.close()

    # Calendar: re-shape to front-end's expected format
    calendar = []
    for r in cal_rows:
        calendar.append({
            "date": iso_to_label(r["flight_date"]),
            "price": r["raw_text"] or (f"¥{r['price']}" if r["price"] else "--"),
        })

    flights = []
    for r in flight_rows:
        flights.append({
            "flight_no": r["flight_no"],
            "airline": r["airline"] or "",
            "aircraft": r["aircraft"] or "",
            "dep_time": r["dep_time"] or "",
            "arr_time": r["arr_time"] or "",
            "discount": f"{r['discount_rate'] * 10:.1f}折" if r["discount_rate"] else "",
            "has_stops": bool(r["stops"]),
            "time_bucket": r["time_bucket"] or "",
            "price": int(r["price"]),
            "inferred_full_price": (
                round(r["inferred_full_price"]) if r["inferred_full_price"] else None
            ),
        })

    return {
        "route": route,
        "orig": route_row["orig"],
        "dest": route_row["dest"],
        "note": route_row["note"] or "",
        "depdate": date,
        "is_real_date": (date == depdate),
        "real_depdate": depdate,
        "calendar_count": len(calendar),
        "flight_count": len(flights),
        "calendar": calendar,
        "flights": flights,
    }
