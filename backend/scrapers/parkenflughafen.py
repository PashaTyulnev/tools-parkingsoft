"""Scraper for parkenflughafen.de via their internal GraphQL proxy API."""
import requests
from datetime import datetime, timedelta

GRAPHQL_URL = "https://www.parkenflughafen.de/api/graphql-proxy"

# Frankfurt (FRA) — the only airport needed
AIRPORT_ID = "airport_d51bb18974434640a6cf94d1adb81557"
SITE_ID = "site_b29f57d4e9c94e6a8b2c4a5d1e3f0b7a"
CAR_TYPE_ID = "car_type_e52e0d094ad34a049a270fbf0573e7dd"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Content-Type": "application/json",
    "Referer": "https://www.parkenflughafen.de/",
    "Origin": "https://www.parkenflughafen.de",
}

QUERY = """
query GetAirportAvailability($input: AirportAvailabilityInput!, $language: String) {
  getAirportAvailability(input: $input, queryOptions: {languageTag: $language}) {
    availability {
      id
      parking { id displayName __typename }
      serviceType { id name __typename }
      product {
        id
        reviewStatistics { numberOfReviews averageScore __typename }
        __typename
      }
      productPage { id name slug __typename }
      services {
        id
        name
        quote { currencyIso price __typename }
        __typename
      }
      __typename
    }
    __typename
  }
}
"""


def _fmt_iso(date_str: str, time_str: str) -> str:
    """Format date + time as ISO 8601 UTC string for the API."""
    dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def fetch_offers(
    start_date: str,
    start_time: str,
    end_date: str,
    end_time: str,
    lang: str = "de-DE",
    timeout: int = 30,
) -> list:
    """
    Fetch availability from parkenflughafen.de GraphQL API.
    Returns the raw availability list.
    """
    payload = {
        "operationName": "GetAirportAvailability",
        "query": QUERY,
        "variables": {
            "input": {
                "airportId": AIRPORT_ID,
                "carTypeId": CAR_TYPE_ID,
                "discountCode": "",
                "siteId": SITE_ID,
                "start": _fmt_iso(start_date, start_time),
                "end": _fmt_iso(end_date, end_time),
            },
            "language": lang,
        },
    }

    resp = requests.post(GRAPHQL_URL, json=payload, headers=HEADERS, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, dict):
        return []
    return (data.get("data") or {}).get("getAirportAvailability", {}).get("availability", []) or []


def scrape_duration(
    location: str,
    arrival_date: str,
    arrival_time: str,
    days: int,
    departure_time: str = "12:00",
    lang: str = "de-DE",
) -> dict:
    """
    Scrape prices for a given number of parking days.
    Returns aggregated price statistics matching the parkos scraper interface.
    """
    arrival_dt = datetime.strptime(f"{arrival_date} {arrival_time}", "%Y-%m-%d %H:%M")
    departure_dt = arrival_dt + timedelta(days=days)
    departure_date = departure_dt.strftime("%Y-%m-%d")

    availability = fetch_offers(
        start_date=arrival_date,
        start_time=arrival_time,
        end_date=departure_date,
        end_time=departure_time,
        lang=lang,
    )

    if not availability:
        return {
            "days": days,
            "count": 0,
            "avg_total": None,
            "avg_per_day": None,
            "min_total": None,
            "max_total": None,
            "providers": [],
        }

    # Collect cheapest service per product page
    providers = []
    for item in availability:
        name = item.get("productPage", {}).get("name") or item.get("parking", {}).get("displayName", "Unknown")
        service_type = item.get("serviceType", {}).get("name", "")
        slug = item.get("productPage", {}).get("slug", "")
        review_stats = (item.get("product", {}).get("reviewStatistics") or [{}])[0]
        avg_score = review_stats.get("averageScore")
        num_reviews = review_stats.get("numberOfReviews")

        services = item.get("services", [])
        prices = [float(s["quote"]["price"]) for s in services if s.get("quote") and s["quote"].get("price")]
        if not prices:
            continue

        cheapest = min(prices)
        providers.append({
            "name": name,
            "type": service_type,
            "price": cheapest,
            "slug": slug,
            "avg_score": float(avg_score) if avg_score else None,
            "num_reviews": num_reviews,
        })

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
