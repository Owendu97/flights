#!/usr/bin/env python3
"""Production scraper for Ctrip flight prices.

Runs as `python3 -m backend.scraper`. Designed to work both:
  - locally (any Python 3.9+ with playwright + chromium installed)
  - in GitHub Actions (uses `actions/setup-python` + `playwright install`)

Pipeline:
  1. For each (route, depdate) pair, load Ctrip SPA in headless Chromium.
  2. Run extract.js to lift calendar + flight list out of the DOM.
  3. Insert rows into SQLite (idempotent via UNIQUE constraints).
  4. Print a per-route summary; exit 0 even on partial failures.

Examples:
  python3 -m backend.scraper                           # default 6 routes, T+0..T+6
  python3 -m backend.scraper --routes PEK-PVG --lookahead 0
  python3 -m backend.scraper --only-today
"""
import argparse
import json
import os
import sys
import time as _time
from datetime import datetime, timedelta
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).parent
EXTRACT_JS = (HERE / "extract.js").read_text(encoding="utf-8")

DEFAULT_ROUTES = "PEK-PVG,PKX-SHA,SHA-KMG,SHA-SZX,SHA-CTU,SHA-XMN"
URL_TEMPLATE = (
    "https://flights.ctrip.com/online/list/oneway-{orig}-{dest}"
    "?_=1&depdate={date}&cabin=Y_S_C_F&adult=1&child=0&infant=0"
)

# Import after path setup so `python3 -m backend.scraper` works from repo root.
if __package__ in (None, ""):
    sys.path.insert(0, str(HERE.parent))
    from backend import db
    from backend.seed import (
        label_to_iso, discount_to_rate, parse_price_text, infer_full_price,
    )
else:
    from . import db
    from .seed import (
        label_to_iso, discount_to_rate, parse_price_text, infer_full_price,
    )


def warmup_context(browser) -> tuple:
    """Open a Ctrip homepage warmup page in a NEW context, then transfer its
    cookies into a SECOND context for actual flight fetches.

    Why two contexts? Empirically, the SAME context that did the warmup
    will then be intercepted on flights.ctrip.com (whaleguard's secondary
    check rejects a context that's "just visited homepage"). Copying
    cookies into a fresh context sidesteps this.

    Returns (flight_ctx, list_of_dicts_cookies) so the caller can re-seed
    a fresh per-route context if cookies need refreshing.
    """
    warmup_ctx = browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        ),
        locale="zh-CN",
        timezone_id="Asia/Shanghai",
        extra_http_headers={
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "sec-ch-ua": '"Chromium";v="126", "Not.A/Brand";v="8"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"macOS"',
        },
    )
    p = warmup_ctx.new_page()
    try:
        p.goto("https://www.ctrip.com/", wait_until="domcontentloaded", timeout=15000)
        p.wait_for_timeout(3000)
        # Touching flights domain also seeds specific anti-bot cookies
        try:
            p.goto("https://flights.ctrip.com/", wait_until="domcontentloaded", timeout=10000)
            p.wait_for_timeout(1500)
        except Exception:
            pass
    except Exception:
        pass
    finally:
        try: p.close()
        except Exception: pass

    cookies = list(warmup_ctx.cookies())
    warmup_ctx.close()

    flight_ctx = browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        ),
        locale="zh-CN",
        timezone_id="Asia/Shanghai",
    )
    if cookies:
        try:
            flight_ctx.add_cookies(cookies)
        except Exception as e:
            print(f"  [warmup] add_cookies failed: {e}", flush=True)
    return flight_ctx


def scrape_route(browser, flight_ctx, orig, dest, depdate: str,
                 retries: int = 2) -> dict:
    """Fetch one (orig, dest, depdate) snapshot from Ctrip, returning the
    extracted {calendar, flights} dict."""
    url = URL_TEMPLATE.format(orig=orig.lower(), dest=dest.lower(), date=depdate)
    last_err = None
    for attempt in range(retries + 1):
        page = flight_ctx.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=25000)
            # SPA hydration: Ctrip's price widgets hydrate late. We sleep a
            # fixed 15s — empirically the sweet spot where calendar AND flight
            # cards are both rendered (wait_for_function is unreliable here
            # because the same-page SPA rewrites the DOM repeatedly).
            page.wait_for_timeout(15000)
            data = page.evaluate(EXTRACT_JS)
            if isinstance(data, str):
                data = json.loads(data)
            return data
        except Exception as e:
            last_err = e
            print(f"  [retry {attempt+1}/{retries}] {orig}-{dest} @ {depdate}: "
                  f"{type(e).__name__}: {e}", flush=True)
            _time.sleep(2 + attempt * 2)
        finally:
            try: page.close()
            except Exception: pass
    return {"calendar": [], "flights": [], "_error": str(last_err)}


def write_route(
    conn, route: str, orig: str, dest: str, depdate: str,
    data: dict, scrape_time: str,
) -> tuple[int, int]:
    """Insert calendar + flight rows for one (route, depdate) snapshot.

    Per user spec, scraper stores:
      - Every non-shared flight in the CHEAPEST bucket (rank 1, 2) — two slots
        when the cheapest bucket is ≥30% below the second-cheapest.
      - Exactly ONE non-shared flight in every other bucket (rank 1).

    Returns (calendar_rows_added, flight_rows_added). Idempotent.
    """
    from collections import defaultdict

    cal_count = 0
    flight_count = 0

    conn.execute(
        """INSERT INTO meta_routes (route, orig, dest, depdate, is_real)
           VALUES (?, ?, ?, ?, 1)
           ON CONFLICT(route) DO UPDATE SET
             orig=excluded.orig, dest=excluded.dest,
             depdate=excluded.depdate, updated_at=CURRENT_TIMESTAMP""",
        (route, orig, dest, depdate),
    )

    base_year = int(depdate[:4])
    for c in data.get("calendar", []) or []:
        m_iso = label_to_iso(c.get("date", ""), base_year)
        if not m_iso:
            continue
        price = parse_price_text(c.get("price", ""))
        is_low = 1 if "低" in c.get("price", "") else 0
        days_before = (
            datetime.fromisoformat(m_iso).date()
            - datetime.fromisoformat(depdate).date()
        ).days
        conn.execute(
            """INSERT INTO price_calendar
               (route, flight_date, depdate_anchor, days_before_departure,
                price, is_lowest, raw_text)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(route, flight_date, depdate_anchor) DO UPDATE SET
                 price=excluded.price, is_lowest=excluded.is_lowest,
                 raw_text=excluded.raw_text, updated_at=CURRENT_TIMESTAMP""",
            (route, m_iso, depdate, days_before, price, is_low, c.get("price")),
        )
        cal_count += 1

    # Bucket-pruning logic (per user spec):
    #   1. Bucket all non-shared flights by time_bucket, sorted by price.
    #   2. Find the bucket with the lowest min price.
    #   3. If that bucket is ≥30% cheaper than the next-cheapest bucket,
    #      keep the top 2 flights from it (rank=1 and rank=2).
    #   4. From every other populated bucket, keep only the top 1.
    #   5. Drop shared flights entirely (per PROJECT_BRIEF §5.1).
    by_bucket: dict = defaultdict(list)
    sanity_drops = 0
    for f in data.get("flights", []) or []:
        if f.get("is_shared"):
            continue
        try:
            p = int(f.get("price", 0) or 0)
        except (TypeError, ValueError):
            p = 0
        if p <= 0:
            continue
        # Sanity check: ¥50 is below any realistic domestic Y-class fare.
        # If extract.js mis-parsed a "立减¥30" coupon as the price, drop it.
        if p < 50:
            sanity_drops += 1
            continue
        # Mutate the dict with parsed price so the bucket sort uses int.
        f2 = dict(f)
        f2["price"] = p
        by_bucket[f2.get("time_bucket", "") or "unknown"].append(f2)

    picked: list = []
    if by_bucket:
        # Determine per-bucket min price and rank
        bucket_min = {k: min(g["price"] for g in arr) for k, arr in by_bucket.items()}
        sorted_buckets = sorted(bucket_min.items(), key=lambda kv: kv[1])
        super_cheap_bucket = None
        if len(sorted_buckets) >= 2:
            top_min = sorted_buckets[0][1]
            second_min = sorted_buckets[1][1]
            # "≥30% cheaper than second-cheapest" → top_min ≤ second_min × 0.7
            if second_min > 0 and top_min <= second_min * 0.7:
                super_cheap_bucket = sorted_buckets[0][0]

        for bucket_key, arr in by_bucket.items():
            arr_sorted = sorted(arr, key=lambda x: x["price"])
            if bucket_key == super_cheap_bucket:
                picked.extend([(arr_sorted[0], 1), (arr_sorted[1], 2)])
            else:
                picked.append((arr_sorted[0], 1))

    for f, bucket_rank in picked:
        discount = f.get("discount", "") or ""
        rate = discount_to_rate(discount)
        full = infer_full_price(int(f.get("price", 0) or 0), discount) if f.get("price") else None
        price_int = int(f.get("price", 0) or 0)
        is_shared = 0  # already filtered above
        is_main = 1
        conn.execute(
            """INSERT INTO flight_snapshot
               (route, flight_date, flight_no, airline, aircraft,
                dep_airport, arr_airport, dep_time, arr_time,
                stops, time_bucket, price,
                discount_rate, inferred_full_price,
                is_main_flight, is_shared, actual_operator, bucket_rank)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(route, flight_date, flight_no) DO UPDATE SET
                 airline=excluded.airline, aircraft=excluded.aircraft,
                 dep_time=excluded.dep_time, arr_time=excluded.arr_time,
                 stops=excluded.stops, time_bucket=excluded.time_bucket,
                 price=excluded.price, discount_rate=excluded.discount_rate,
                 inferred_full_price=excluded.inferred_full_price,
                 is_main_flight=excluded.is_main_flight,
                 is_shared=excluded.is_shared,
                 actual_operator=excluded.actual_operator,
                 bucket_rank=excluded.bucket_rank,
                 updated_at=CURRENT_TIMESTAMP""",
            (route, depdate,
             f.get("flight_no", "") or "",
             f.get("airline", "") or "",
             f.get("aircraft", "") or "",
             orig, dest,
             f.get("dep_time", "") or "",
             f.get("arr_time", "") or "",
             1 if f.get("has_stops") else 0,
             f.get("time_bucket", "") or "",
             price_int, rate, full,
             is_main, is_shared, f.get("actual_operator", "") or "",
             bucket_rank),
        )
        flight_count += 1

    status = "ok" if not data.get("_error") and flight_count > 0 else (
        "partial" if flight_count > 0 else "fail"
    )
    conn.execute(
        """INSERT INTO scrape_log
           (route, depdate_anchor, finished_at, flights_count, calendar_count, status)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (route, depdate, scrape_time, flight_count, cal_count, status),
    )
    if sanity_drops:
        print(f"  [sanity] {route} {depdate}: dropped {sanity_drops} "
              f"flights with price < 50 (likely '立减¥XX' coupon mis-parse)",
              flush=True)
    return cal_count, flight_count


def main() -> int:
    ap = argparse.ArgumentParser(description="Ctrip flight price scraper")
    ap.add_argument("--routes", default=DEFAULT_ROUTES,
                    help=f"Comma-separated route codes (default: {DEFAULT_ROUTES})")
    ap.add_argument("--lookahead", type=int, default=7,
                    help="Days from today to scrape (default 7 → today..today+6)")
    ap.add_argument("--delay", type=float, default=3.0,
                    help="Seconds to wait between routes (anti-bot budget)")
    ap.add_argument("--anchor-start", default=None,
                    help="Override starting date YYYY-MM-DD (default: today UTC)")
    ap.add_argument("--no-headless", action="store_true",
                    help="Run browser with UI (debug only)")
    ap.add_argument("--chromium-channel", default=None,
                    help="Override chromium channel (e.g. 'chrome'); default uses bundled headless-shell")
    args = ap.parse_args()

    # 1) Ensure DB exists.
    db.init_db()
    conn = db.get_conn()

    # 2) Build the work list.
    if args.anchor_start:
        start = datetime.fromisoformat(args.anchor_start).date()
    else:
        start = datetime.utcnow().date()
    lookahead = max(args.lookahead, 0)
    depdates = [(start + timedelta(days=d)).isoformat()
                for d in range(lookahead + 1)]

    routes = []
    for pair in args.routes.split(","):
        pair = pair.strip()
        if not pair or "-" not in pair:
            print(f"  [skip] bad route spec: {pair!r}")
            continue
        o, d = pair.split("-", 1)
        routes.append((f"{o}-{d}", o.strip().upper(), d.strip().upper()))

    if not routes:
        print("[scraper] no routes to process, exiting")
        return 1

    print(f"[scraper] {len(routes)} routes × {len(depdates)} depdates = "
          f"{len(routes)*len(depdates)} combos, delay={args.delay}s")

    total = {"calendar": 0, "flights": 0, "errors": 0}

    with sync_playwright() as pw:
        launch_kwargs = {"headless": not args.no_headless}
        if args.chromium_channel:
            launch_kwargs["channel"] = args.chromium_channel
        browser = pw.chromium.launch(**launch_kwargs)

        ctx = None
        try:
            ctx = warmup_context(browser)
            for ri, (route, orig, dest) in enumerate(routes):
                for di, depdate in enumerate(depdates):
                    label = f"  [{ri+1}/{len(routes)} route={route} {di+1}/{len(depdates)} d={depdate}]"
                    print(f"{label} fetching …", flush=True)
                    scrape_time = datetime.utcnow().isoformat(timespec="seconds") + "Z"
                    data = scrape_route(browser, ctx, orig, dest, depdate)
                    cal_n, fl_n = write_route(conn, route, orig, dest,
                                               depdate, data, scrape_time)
                    conn.commit()
                    total["calendar"] += cal_n
                    total["flights"] += fl_n
                    if data.get("_error") or (cal_n == 0 and fl_n == 0):
                        total["errors"] += 1
                    print(f"           → cal={cal_n} flights={fl_n}  "
                          f"{'ERR' if data.get('_error') else 'ok'}", flush=True)

                    if (ri, di) != (len(routes) - 1, len(depdates) - 1):
                        _time.sleep(args.delay)
        finally:
            try:
                if ctx: ctx.close()
            except Exception:
                pass
            browser.close()

    conn.close()
    print(f"\n[scraper] DONE. rows: cal={total['calendar']} flights={total['flights']} "
          f"errors={total['errors']}")
    return 0 if total["errors"] == 0 else 0  # partial failures don't fail the cron


if __name__ == "__main__":
    sys.exit(main())
