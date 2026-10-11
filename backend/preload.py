#!/opt/parkingsoft-venv/bin/python3
"""
Nightly preload script: scrapes all 3 portals for the next 14 days and caches results.
Run via cron at midnight: 0 0 * * * /opt/parkingsoft-tools/backend/preload.py
"""
import json
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(__file__))
from scrapers import parkos, parkenflughafen, ichparkebilliger

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler("/var/log/parkingsoft-preload.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)

PORTALS = {
    "parkos": parkos,
    "parkenflughafen": parkenflughafen,
    "ichparkebilliger": ichparkebilliger,
}

LOCATION = "parken-flughafen-frankfurt"
ARRIVAL_TIME = "08:00"
DEPARTURE_TIME = "12:00"
DAY_RANGES = list(range(1, 32))
PRELOAD_DAYS_AHEAD = 14  # preload for the next 14 arrival dates

CACHE_DIR = os.path.join(os.path.dirname(__file__), "cache")
os.makedirs(CACHE_DIR, exist_ok=True)


def _cache_path(portal_key: str, arrival_date: str) -> str:
    return os.path.join(CACHE_DIR, f"{arrival_date}_{portal_key}_{LOCATION}.json")


def _scrape_one_day(portal_key, scraper, arrival_date, days):
    try:
        result = scraper.scrape_duration(
            location=LOCATION,
            arrival_date=arrival_date,
            arrival_time=ARRIVAL_TIME,
            days=days,
            departure_time=DEPARTURE_TIME,
        )
        return days, result
    except Exception as exc:
        log.error(f"[{portal_key}] {arrival_date} days={days} error: {exc}")
        return days, {"days": days, "count": 0, "avg_total": None, "avg_per_day": None,
                      "min_total": None, "max_total": None, "providers": [], "error": str(exc)}


def preload_portal(portal_key: str, scraper, arrival_date: str):
    log.info(f"[{portal_key}] Preloading {arrival_date} ...")
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {
            pool.submit(_scrape_one_day, portal_key, scraper, arrival_date, days): days
            for days in DAY_RANGES
        }
        results_map = {}
        for future in as_completed(futures):
            days, result = future.result()
            results_map[days] = result

    results = [results_map[d] for d in DAY_RANGES]
    path = _cache_path(portal_key, arrival_date)
    with open(path, "w") as f:
        json.dump(results, f)
    counts = [r.get("count", 0) for r in results]
    log.info(f"[{portal_key}] {arrival_date} done — avg providers/day: {sum(counts)/len(counts):.1f}, saved to {path}")


def main():
    log.info("=== Nightly preload started ===")
    today = date.today()
    arrival_dates = [(today + timedelta(days=i)).isoformat() for i in range(PRELOAD_DAYS_AHEAD)]

    # Run portals × dates in parallel (but limit concurrency to avoid overloading)
    tasks = [(pk, sc, ad) for ad in arrival_dates for pk, sc in PORTALS.items()]
    log.info(f"Total tasks: {len(tasks)} ({len(arrival_dates)} dates × {len(PORTALS)} portals)")

    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {executor.submit(preload_portal, pk, sc, ad): (pk, ad) for pk, sc, ad in tasks}
        for future in as_completed(futures):
            pk, ad = futures[future]
            try:
                future.result()
            except Exception as exc:
                log.error(f"[{pk}] {ad} failed: {exc}")

    log.info("=== Nightly preload completed ===")


if __name__ == "__main__":
    main()
