"""Flask backend for Parking Price Comparison Tool."""
import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, date, timedelta

from flask import Flask, jsonify, request
from flask_cors import CORS

from scrapers import parkos
from scrapers import parkenflughafen
from scrapers import ichparkebilliger

app = Flask(__name__)
CORS(app)
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

PORTALS = {
    "parkos": {"name": "Parkos.de", "scraper": parkos},
    "parkenflughafen": {"name": "Parkenflughafen.de", "scraper": parkenflughafen},
    "ichparkebilliger": {"name": "IchParkeBilliger.de", "scraper": ichparkebilliger},
}

DAY_RANGES = list(range(1, 32))
CACHE_DIR = os.path.join(os.path.dirname(__file__), "cache")
os.makedirs(CACHE_DIR, exist_ok=True)


# ── Cache helpers ──────────────────────────────────────────────────────────────

def _cache_path(portal_key: str, location: str, arrival_date: str) -> str:
    safe_loc = location.replace("/", "_")
    return os.path.join(CACHE_DIR, f"{arrival_date}_{portal_key}_{safe_loc}.json")


def _load_cache(portal_key: str, location: str, arrival_date: str) -> list | None:
    path = _cache_path(portal_key, location, arrival_date)
    if not os.path.exists(path):
        return None
    age = datetime.now().timestamp() - os.path.getmtime(path)
    if age > 86400:  # stale after 24h
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def _save_cache(portal_key: str, location: str, arrival_date: str, results: list) -> None:
    path = _cache_path(portal_key, location, arrival_date)
    try:
        with open(path, "w") as f:
            json.dump(results, f)
    except Exception as exc:
        log.warning(f"Cache write failed for {path}: {exc}")


# ── Scraping helpers ───────────────────────────────────────────────────────────

def _scrape_one_day(portal_key, scraper, location, arrival_date, arrival_time, departure_time, days):
    try:
        result = scraper.scrape_duration(
            location=location,
            arrival_date=arrival_date,
            arrival_time=arrival_time,
            days=days,
            departure_time=departure_time,
        )
        log.info(f"[{portal_key}] days={days}: avg={result.get('avg_per_day')}€/day, count={result.get('count')}")
        return days, result
    except Exception as exc:
        log.error(f"[{portal_key}] days={days} error: {exc}")
        return days, {"days": days, "count": 0, "avg_total": None, "avg_per_day": None,
                      "min_total": None, "max_total": None, "providers": [], "error": str(exc)}


def _scrape_portal_all_days(portal_key, scraper, location, arrival_date, arrival_time, departure_time, day_ranges):
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {
            pool.submit(_scrape_one_day, portal_key, scraper, location, arrival_date, arrival_time, departure_time, days): days
            for days in day_ranges
        }
        results_map = {}
        for future in as_completed(futures):
            days, result = future.result()
            results_map[days] = result
    return portal_key, [results_map[d] for d in day_ranges]


def _fetch_portal(portal_key, location, arrival_date, arrival_time, departure_time, day_ranges, use_cache):
    """Fetch results for one portal, using cache when available."""
    cached = _load_cache(portal_key, location, arrival_date) if use_cache else None
    if cached is not None:
        log.info(f"[{portal_key}] cache hit for {arrival_date}")
        # Filter to requested day_ranges
        day_set = set(day_ranges)
        filtered = [r for r in cached if r.get("days") in day_set]
        if len(filtered) == len(day_ranges):
            return portal_key, filtered

    scraper = PORTALS[portal_key]["scraper"]
    portal_key_out, results = _scrape_portal_all_days(
        portal_key, scraper, location, arrival_date, arrival_time, departure_time, day_ranges
    )
    # Cache full 1-31 results only when we scraped all days
    if use_cache and day_ranges == DAY_RANGES:
        _save_cache(portal_key, location, arrival_date, results)
    return portal_key_out, results


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.route("/api/compare", methods=["GET"])
def compare():
    location = request.args.get("location", "parken-flughafen-frankfurt")
    arrival_date = request.args.get("arrival_date")
    arrival_time = request.args.get("arrival_time", "08:00")
    departure_time = request.args.get("departure_time", "12:00")
    portals_param = request.args.get("portals", "parkos")
    day_ranges_param = request.args.get("days")
    no_cache = request.args.get("no_cache", "").lower() in ("1", "true")

    if not arrival_date:
        arrival_date = date.today().strftime("%Y-%m-%d")
    try:
        datetime.strptime(arrival_date, "%Y-%m-%d")
    except ValueError:
        return jsonify({"error": "Invalid arrival_date format, use YYYY-MM-DD"}), 400

    requested_portals = [p.strip() for p in portals_param.split(",") if p.strip() in PORTALS]
    if not requested_portals:
        return jsonify({"error": f"No valid portals specified. Available: {list(PORTALS.keys())}"}), 400

    if day_ranges_param:
        try:
            day_ranges = sorted(set(int(d) for d in day_ranges_param.split(",") if 1 <= int(d) <= 31))
        except ValueError:
            return jsonify({"error": "Invalid days parameter"}), 400
    else:
        day_ranges = DAY_RANGES

    log.info(f"Comparing: location={location}, arrival={arrival_date} {arrival_time}, portals={requested_portals}, days={day_ranges}")

    response_data = {
        "location": location,
        "arrival_date": arrival_date,
        "arrival_time": arrival_time,
        "departure_time": departure_time,
        "day_ranges": day_ranges,
        "portals": {},
    }

    with ThreadPoolExecutor(max_workers=len(requested_portals)) as executor:
        futures = {
            executor.submit(
                _fetch_portal,
                portal_key,
                location,
                arrival_date,
                arrival_time,
                departure_time,
                day_ranges,
                not no_cache,
            ): portal_key
            for portal_key in requested_portals
        }
        for future in as_completed(futures):
            portal_key, results = future.result()
            response_data["portals"][portal_key] = {
                "name": PORTALS[portal_key]["name"],
                "results": results,
            }

    return jsonify(response_data)


@app.route("/api/providers", methods=["GET"])
def providers():
    location = request.args.get("location", "parken-flughafen-frankfurt")
    arrival_date = request.args.get("arrival_date")
    arrival_time = request.args.get("arrival_time", "08:00")
    departure_time = request.args.get("departure_time", "12:00")
    days = request.args.get("days", "1")

    if not arrival_date:
        arrival_date = date.today().strftime("%Y-%m-%d")

    try:
        days = int(days)
        if not 1 <= days <= 31:
            raise ValueError()
    except ValueError:
        return jsonify({"error": "days must be 1-30"}), 400

    try:
        result = parkos.scrape_duration(
            location=location,
            arrival_date=arrival_date,
            arrival_time=arrival_time,
            days=days,
            departure_time=departure_time,
        )
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/portals", methods=["GET"])
def list_portals():
    return jsonify({k: {"name": v["name"]} for k, v in PORTALS.items()})


@app.route("/api/cache/status", methods=["GET"])
def cache_status():
    files = os.listdir(CACHE_DIR)
    entries = []
    for f in sorted(files):
        path = os.path.join(CACHE_DIR, f)
        age = datetime.now().timestamp() - os.path.getmtime(path)
        entries.append({"file": f, "age_hours": round(age / 3600, 1)})
    return jsonify({"cache_entries": len(files), "entries": entries})


PRELOADED_TOKEN = "a16c190c84c963ef92d563898841c77889b6203b4ad0f5ca"


@app.route("/api/preloaded", methods=["GET"])
def preloaded():
    """Return all pre-calculated prices from the nightly cache."""
    if request.args.get("token") != PRELOADED_TOKEN:
        return jsonify({"error": "Unauthorized"}), 401
    if not os.path.isdir(CACHE_DIR):
        return jsonify({"error": "Cache directory not found"}), 500

    data = {}
    for filename in sorted(os.listdir(CACHE_DIR)):
        if not filename.endswith(".json"):
            continue
        parts = filename[:-5].split("_", 2)  # arrival_date, portal_key, location
        if len(parts) != 3:
            continue
        arrival_date, portal_key, location = parts
        if portal_key not in PORTALS:
            continue
        path = os.path.join(CACHE_DIR, filename)
        try:
            with open(path) as f:
                results = json.load(f)
        except Exception:
            continue
        data.setdefault(arrival_date, {})[portal_key] = {
            "name": PORTALS[portal_key]["name"],
            "location": location,
            "cached_at": datetime.fromtimestamp(os.path.getmtime(path)).isoformat(),
            "results": results,
        }

    return jsonify({"dates": sorted(data.keys()), "data": data})


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
