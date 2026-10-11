"""Scraper for Parkos.de via their internal SSE API."""
import requests
import json
from datetime import datetime, timedelta
from typing import Optional


HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/event-stream",
    "Referer": "https://parkos.de/",
    "Origin": "https://parkos.de",
}

BFF_URL = "https://mpl-bff.parkos.io/v1/offers"


def _fmt_dt(date_str: str, time_str: str) -> str:
    """Format date + time for the Parkos API (YYYY-MM-DDTHH:MM:SS, no TZ suffix)."""
    dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
    return dt.strftime("%Y-%m-%dT%H:%M:00")


def fetch_offers(
    location: str,
    arrival_date: str,
    arrival_time: str,
    departure_date: str,
    departure_time: str,
    lang: str = "de",
    timeout: int = 30,
) -> dict:
    """
    Fetch parking offers from Parkos SSE API.
    Returns dict with 'available', 'unavailable', 'pending' lists.
    """
    parking_start = _fmt_dt(arrival_date, arrival_time)
    parking_end = _fmt_dt(departure_date, departure_time)

    params = {
        "location": location,
        "parkingStart": parking_start,
        "parkingEnd": parking_end,
        "lang": lang,
        "x-refer": "mpl-listing-page",
    }

    response = requests.get(BFF_URL, headers=HEADERS, params=params, stream=True, timeout=timeout)
    response.raise_for_status()

    # The SSE stream delivers one main event with all data
    current_event = {}
    for line in response.iter_lines():
        if not line:
            continue
        decoded = line.decode("utf-8") if isinstance(line, bytes) else line
        if decoded.startswith("event:"):
            current_event["event"] = decoded[6:].strip()
        elif decoded.startswith("data:"):
            current_event["data"] = decoded[5:].strip()
            if current_event.get("data"):
                try:
                    return json.loads(current_event["data"])
                except json.JSONDecodeError:
                    pass
            current_event = {}

    return {"available": [], "unavailable": [], "pending": []}


def scrape_duration(
    location: str,
    arrival_date: str,
    arrival_time: str,
    days: int,
    departure_time: str = "12:00",
    lang: str = "de",
) -> dict:
    """
    Scrape prices for a given number of parking days.
    Returns aggregated price statistics.
    """
    arrival_dt = datetime.strptime(f"{arrival_date} {arrival_time}", "%Y-%m-%d %H:%M")
    departure_dt = arrival_dt + timedelta(days=days)
    departure_date = departure_dt.strftime("%Y-%m-%d")

    data = fetch_offers(
        location=location,
        arrival_date=arrival_date,
        arrival_time=arrival_time,
        departure_date=departure_date,
        departure_time=departure_time,
        lang=lang,
    )

    available = data.get("available", [])
    if not available:
        return {
            "days": days,
            "count": 0,
            "avg_total": None,
            "avg_per_day": None,
            "min_total": None,
            "max_total": None,
            "providers": [],
        }

    # Deduplicate by merchant+type, keep cheapest per merchant
    seen = {}
    for offer in available:
        merchant_name = offer.get("merchant", {}).get("name", "Unknown")
        ptype = offer.get("parking_type", "")
        key = f"{merchant_name}_{ptype}"
        price = offer.get("price")
        if price is None:
            continue
        price = float(price)
        if key not in seen or price < seen[key]["price"]:
            seen[key] = {
                "name": merchant_name,
                "type": ptype,
                "price": price,
                "slug": offer.get("merchant", {}).get("slug", ""),
            }

    providers = list(seen.values())
    if not providers:
        return {"days": days, "count": 0, "avg_total": None, "avg_per_day": None, "min_total": None, "max_total": None, "providers": []}

    prices = [p["price"] for p in providers]
    avg_total = sum(prices) / len(prices)

    return {
        "days": days,
        "count": len(providers),
        "avg_total": round(avg_total, 2),
        "avg_per_day": round(avg_total / days, 2),
        "min_total": round(min(prices), 2),
        "max_total": round(max(prices), 2),
        "providers": sorted(providers, key=lambda x: x["price"]),
    }
