"""Scraper for ich-parke-billiger.de via their internal loadoffers AJAX endpoint."""
import re
import requests
from datetime import datetime, timedelta

BASE_URL = "https://www.ich-parke-billiger.de"
OFFERS_URL = f"{BASE_URL}/fe_ajax/loadoffers"

# Frankfurt FRA slug
FRANKFURT_SLUG = "parken-flughafen-frankfurt-main-fra"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "de-DE,de;q=0.9",
}

AJAX_HEADERS = {
    **HEADERS,
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": f"{BASE_URL}/",
}


def _fmt_date(date_str: str) -> str:
    """Convert YYYY-MM-DD to DD.MM.YYYY for the API."""
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    return dt.strftime("%d.%m.%Y")


def _get_session(slug: str, timeout: int = 15) -> requests.Session:
    """Open a session by loading the listing page to get a valid cookie."""
    session = requests.Session()
    session.get(
        f"{BASE_URL}/{slug}",
        headers=HEADERS,
        timeout=timeout,
    )
    return session


def fetch_offers(
    start_date: str,
    end_date: str,
    slug: str = FRANKFURT_SLUG,
    timeout: int = 30,
) -> list[dict]:
    """
    Fetch parking offers from ich-parke-billiger.de.
    Returns list of dicts with name, price, service_type, rating, slug.
    """
    session = _get_session(slug)

    headers = {
        **AJAX_HEADERS,
        "Referer": f"{BASE_URL}/{slug}",
    }
    data = {
        "slug": slug,
        "von": _fmt_date(start_date),
        "bis": _fmt_date(end_date),
        "sortierung": "preis",
    }

    resp = session.post(OFFERS_URL, headers=headers, data=data, timeout=timeout)
    resp.raise_for_status()
    return _parse_html(resp.text)


def _parse_displayed_price(block: str) -> float | None:
    m = re.search(r'([0-9]{1,3}(?:\.[0-9]{3})*,[0-9]{2})\s*(?:&nbsp;)?€', block)
    if not m:
        return None
    return float(m.group(1).replace(".", "").replace(",", "."))


def _parse_html(html: str) -> list[dict]:
    """Parse the HTML response and return offer list."""
    blocks = re.split(r"(?=<div class=\"offerblock )", html)
    offers = []

    for block in blocks[1:]:
        if "bookable" not in block:
            continue

        # Displayed price ("1.234,56 €") – data-sort-price switched from cents to euros in Oct 2026
        price = _parse_displayed_price(block)
        if price is None:
            price_m = re.search(r'data-sort-price="([0-9.]+)"', block)
            if not price_m:
                continue
            price = round(float(price_m.group(1)), 2)

        # Provider name from first image alt
        name_m = re.search(r'class="[^"]*responsive-offer-img[^"]*"[^>]*alt="([^"]+)"', block)
        name = name_m.group(1).strip() if name_m else ""
        if not name:
            # Fallback: data-sort-provider (title-cased)
            prov_m = re.search(r'data-sort-provider="([^"]+)"', block)
            name = prov_m.group(1).title() if prov_m else "Unknown"

        # Service type from CSS classes
        service_type = "Unbekannt"
        if "compvalet" in block:
            service_type = "Valet Service"
        elif "compshuttle" in block:
            service_type = "Shuttle Service"

        # Rating from aria-label "4,9 von 5 Sternen (208)"
        rating, num_reviews = None, None
        rating_m = re.search(r'aria-label="([0-9]+(?:,[0-9]+)?) von 5 Sternen(?: \(([0-9.]+)\))?"', block)
        if rating_m:
            rating = float(rating_m.group(1).replace(",", "."))
            num_reviews = int(rating_m.group(2).replace(".", "")) if rating_m.group(2) else None

        # Booking slug
        link_m = re.search(r'/parken-reservieren/([a-z0-9-]+)', block)
        book_slug = link_m.group(1) if link_m else ""

        offers.append({
            "name": name,
            "type": service_type,
            "price": price,
            "slug": book_slug,
            "avg_score": rating,
            "num_reviews": num_reviews,
        })

    return offers


def scrape_duration(
    location: str,
    arrival_date: str,
    arrival_time: str,
    days: int,
    departure_time: str = "12:00",
) -> dict:
    """
    Scrape prices for a given number of parking days.
    Returns aggregated price statistics matching the parkos/parkenflughafen interface.
    """
    arrival_dt = datetime.strptime(arrival_date, "%Y-%m-%d")
    departure_dt = arrival_dt + timedelta(days=days)
    departure_date = departure_dt.strftime("%Y-%m-%d")

    providers = fetch_offers(start_date=arrival_date, end_date=departure_date)

    if not providers:
        return {
            "days": days,
            "count": 0,
            "avg_total": None,
            "avg_per_day": None,
            "min_total": None,
            "max_total": None,
            "providers": [],
        }

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
