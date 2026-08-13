"""
Scrapes BazaarPulse listing pages (not the 1,134 product detail pages -- the
listing cards already carry retailer, price, MRP, pack size, category and
stock status; a detail-page crawl buys us historical price points we don't
need for a "today's gap" view, at ~5x the request count). See DECISIONS.md.

The site is deliberately inconsistent (per 03_External_Sources.md's "fair
warning"), confirmed by inspection:
  - Pagination: Mumbai/Delhi use /city/{slug}/page/{n}.html. Bengaluru/
    Chennai use /city/{slug}/index.html for page 1 and
    /city/{slug}/index_p{n}.html for subsequent pages -- NOT the
    ?p={n} query string the on-page links suggest (a static file server
    can't route query strings; each city ships a PAGINATION.txt that says
    so explicitly, so we read that rather than hardcode per-city rules).
  - Price markup: four different variants appear across cards, sometimes
    within the same city: <span class="price">, <div class="amt">,
    <span class="pricing-block" data-price-paise="...">, and Chennai-only
    <b class="sellingPrice">INR ...</b>. All four are handled per-card
    rather than assuming one applies per page or per city.

Robots.txt is fetched and parsed at the start of every run via Python's
stdlib urllib.robotparser -- not hardcoded from a one-time manual read (an
earlier version of this docstring claimed robots.txt compliance but the
code never actually fetched it at runtime; found and fixed during an audit
pass, see DECISIONS.md). Every URL this scraper requests is checked against
the live policy with can_fetch() before the request is made, and the
crawl-delay is read from the policy too (falling back to the hardcoded
default only if robots.txt can't be fetched at all).

Usage:
    python3 etl/scrape_bazaarpulse.py [--refresh]

Output:
    cache/bazaarpulse/listings.csv -- one row per listing card, all cities.
"""
import argparse
import csv
import re
import sys
import time
import urllib.request
import urllib.error
from html import unescape
from pathlib import Path
from urllib.robotparser import RobotFileParser

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from etl import config

USER_AGENT = "kestrel-control-tower-scraper/1.0"
DEFAULT_CRAWL_DELAY_SECONDS = 1.0  # used only if robots.txt is unreachable
CITIES = ["mumbai", "delhi", "bengaluru", "chennai"]

# Set once by main() via load_robots_policy(), read by fetch()/try_fetch().
# Module-level rather than threaded through every call site, since it's a
# single run-wide policy, not per-request state.
_ROBOTS: RobotFileParser | None = None
CRAWL_DELAY_SECONDS = DEFAULT_CRAWL_DELAY_SECONDS


def load_robots_policy() -> RobotFileParser | None:
    """Fetches and parses robots.txt from the scrape target. Returns None
    if it can't be fetched at all (network error, not just a 404 -- a 404
    is a valid "no restrictions" answer that RobotFileParser already
    handles correctly), in which case callers fall back to the documented
    disallow list as a conservative default rather than assuming
    everything is fair game."""
    rp = RobotFileParser()
    rp.set_url(f"{config.BAZAARPULSE_BASE_URL}/robots.txt")
    try:
        rp.read()
    except Exception as e:
        print(f"  Could not fetch robots.txt ({e}) -- falling back to the last-known "
              f"policy (Crawl-delay: {DEFAULT_CRAWL_DELAY_SECONDS}, Disallow: /internal/, /admin/).")
        return None
    return rp


def _robots_allowed(url: str) -> bool:
    if _ROBOTS is not None:
        return _ROBOTS.can_fetch(USER_AGENT, url)
    # No policy fetched -- fall back to the specific rules this scraper has
    # always been built around (see module docstring), rather than either
    # blocking everything or allowing everything by default.
    from urllib.parse import urlparse
    path = urlparse(url).path
    return not (path.startswith("/internal/") or path.startswith("/admin/"))

CARD_START_RE = re.compile(r'<div class="card product-item" data-listing-id="(\d+)">')
PAGE_COUNT_RE = re.compile(r"page \d+ of (\d+)", re.IGNORECASE)
RUPEE_RE = re.compile(r"[\d,]+\.?\d*")

HREF_TITLE_RE = re.compile(r'<a href="(/product/\d+\.html)"><strong>(.*?)</strong></a>')
FIRST_MUTED_RE = re.compile(r'</a>\s*<div class="muted">(.*?)</div>')
PRICE_SPAN_RE = re.compile(r'<span class="price">(.*?)</span>')
PRICE_AMT_RE = re.compile(r'<div class="amt">.*?([\d,]+\.?\d*)\s*<small>')
PRICE_BLOCK_RE = re.compile(r'<span class="pricing-block" data-price-paise="(\d+)"')
PRICE_SELLING_RE = re.compile(r'<b class="sellingPrice">INR\s*([\d,]+\.?\d*)</b>')
MRP_STOCK_RE = re.compile(r'<div class="muted">(MRP.*?)</div>')
LAST_SEEN_RE = re.compile(r'Last seen: ([\d-]+)')


class RobotsDisallowedError(Exception):
    pass


def fetch(url: str) -> str:
    if not _robots_allowed(url):
        raise RobotsDisallowedError(f"robots.txt disallows fetching {url}")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.read().decode("utf-8")


def try_fetch(url: str) -> str | None:
    try:
        return fetch(url)
    except (urllib.error.HTTPError, urllib.error.URLError):
        return None
    except RobotsDisallowedError as e:
        print(f"    skipping (robots.txt): {e}")
        return None


def parse_money(raw: str) -> float | None:
    m = RUPEE_RE.search(unescape(raw))
    if not m:
        return None
    return float(m.group(0).replace(",", ""))


def guess_brand(title: str) -> str | None:
    upper = title.upper()
    for brand in config.KNOWN_BRANDS:
        if brand.upper() in upper:
            return brand
    return None


def split_cards(html: str) -> list[str]:
    starts = [m.start() for m in CARD_START_RE.finditer(html)]
    if not starts:
        return []
    starts.append(len(html))
    return [html[starts[i]:starts[i + 1]] for i in range(len(starts) - 1)]


def parse_card(card_html: str, city: str, source_url: str) -> dict | None:
    listing_id_m = CARD_START_RE.match(card_html)
    href_title_m = HREF_TITLE_RE.search(card_html)
    if not listing_id_m or not href_title_m:
        return None

    title = unescape(href_title_m.group(2)).strip()

    meta1_m = FIRST_MUTED_RE.search(card_html)
    retailer = pack = category = None
    if meta1_m:
        parts = [unescape(p).strip() for p in meta1_m.group(1).split("&middot;")]
        retailer = parts[0] if len(parts) > 0 else None
        pack = parts[1] if len(parts) > 1 else None
        category = parts[2] if len(parts) > 2 else None

    # Price: try each known markup variant in turn.
    price_inr = None
    price_source = None
    m = PRICE_SPAN_RE.search(card_html)
    if m:
        price_inr = parse_money(m.group(1))
        price_source = "span.price"
    if price_inr is None:
        m = PRICE_BLOCK_RE.search(card_html)
        if m:
            price_inr = int(m.group(1)) / 100.0
            price_source = "pricing-block(paise)"
    if price_inr is None:
        m = PRICE_AMT_RE.search(card_html)
        if m:
            price_inr = parse_money(m.group(1))
            price_source = "div.amt"
    if price_inr is None:
        m = PRICE_SELLING_RE.search(card_html)
        if m:
            price_inr = parse_money(m.group(1))
            price_source = "b.sellingPrice"

    mrp = stock_status = rating = rating_count = None
    m = MRP_STOCK_RE.search(card_html)
    if m:
        for part in [unescape(p).strip() for p in m.group(1).split("&middot;")]:
            if part.startswith("MRP"):
                mrp = parse_money(part)
            elif part.lower().startswith("in stock"):
                stock_status = "IN_STOCK"
            elif part.lower().startswith("currently unavailable"):
                stock_status = "UNAVAILABLE"
            elif part.startswith("rated"):
                rm = re.match(r"rated ([\d.]+) \((\d+)\)", part)
                if rm:
                    rating = float(rm.group(1))
                    rating_count = int(rm.group(2))

    last_seen_m = LAST_SEEN_RE.search(card_html)

    pack_value, pack_uom = None, None
    if pack:
        pm = re.match(r"([\d.]+)\s*(g|kg|ml|l)\b", pack, re.IGNORECASE)
        if pm:
            pack_value = float(pm.group(1))
            pack_uom = pm.group(2).upper()

    return {
        "listing_id": listing_id_m.group(1),
        "city": city,
        "product_detail_url": href_title_m.group(1),
        "title": title,
        "brand_guess": guess_brand(title),
        "retailer": retailer,
        "pack_raw": pack,
        "pack_value": pack_value,
        "pack_uom": pack_uom,
        "category": category,
        "price_inr": price_inr,
        "price_source": price_source,
        "mrp_inr": mrp,
        "stock_status": stock_status,
        "rating": rating,
        "rating_count": rating_count,
        "last_seen": last_seen_m.group(1) if last_seen_m else None,
        "source_url": source_url,
    }


def parse_listing_page(html: str, city: str, source_url: str) -> list[dict]:
    rows = []
    for card_html in split_cards(html):
        row = parse_card(card_html, city, source_url)
        if row:
            rows.append(row)
    return rows


def discover_page_count(html: str) -> int:
    m = PAGE_COUNT_RE.search(html)
    return int(m.group(1)) if m else 1


def scrape_city(city: str) -> list[dict]:
    rows = []

    # Page 1: try /page/1.html first (Mumbai/Delhi convention), fall back to
    # /index.html (Bengaluru/Chennai convention).
    page1_url = f"{config.BAZAARPULSE_BASE_URL}/city/{city}/page/1.html"
    html = try_fetch(page1_url)
    uses_page_dir = html is not None
    if html is None:
        page1_url = f"{config.BAZAARPULSE_BASE_URL}/city/{city}/index.html"
        html = fetch(page1_url)

    rows += parse_listing_page(html, city, page1_url)
    total_pages = discover_page_count(html)

    # Ask the site itself how subsequent pages are addressed rather than
    # assuming -- PAGINATION.txt is exactly there to tell us this.
    pagination_note = try_fetch(f"{config.BAZAARPULSE_BASE_URL}/city/{city}/PAGINATION.txt")
    uses_indexed_files = pagination_note is not None and "index_p" in pagination_note

    print(f"  {city}: {total_pages} pages (dir={'page/' if uses_page_dir else 'index.html'}, "
          f"pagination={'index_pN' if uses_indexed_files else 'page/N' if uses_page_dir else 'unknown'})")

    for page in range(2, total_pages + 1):
        time.sleep(CRAWL_DELAY_SECONDS)
        if uses_page_dir:
            url = f"{config.BAZAARPULSE_BASE_URL}/city/{city}/page/{page}.html"
        elif uses_indexed_files:
            url = f"{config.BAZAARPULSE_BASE_URL}/city/{city}/index_p{page}.html"
        else:
            # Neither convention detected -- nothing safe to try; stop here
            # rather than guess and silently under-scrape.
            print(f"    page {page}: no known pagination convention, stopping city")
            break
        html = try_fetch(url)
        if html is None:
            print(f"    page {page}: unreachable, skipping (site warns some pages are unreachable)")
            continue
        page_rows = parse_listing_page(html, city, url)
        if not page_rows:
            print(f"    page {page}: no cards found, stopping city early")
            break
        rows += page_rows
    return rows


def main():
    global _ROBOTS, CRAWL_DELAY_SECONDS

    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true", help="Re-scrape even if a cache file exists")
    args = parser.parse_args()

    config.BAZAARPULSE_CACHE.mkdir(parents=True, exist_ok=True)
    out_path = config.BAZAARPULSE_CACHE / "listings.csv"

    if out_path.exists() and not args.refresh:
        print(f"Cache exists at {out_path}, skipping (use --refresh to re-scrape).")
        return

    _ROBOTS = load_robots_policy()
    if _ROBOTS is not None:
        policy_delay = _ROBOTS.crawl_delay(USER_AGENT)
        if policy_delay:
            CRAWL_DELAY_SECONDS = float(policy_delay)
        print(f"  robots.txt loaded: crawl-delay {CRAWL_DELAY_SECONDS}s, "
              f"can_fetch a /city/ URL = {_ROBOTS.can_fetch(USER_AGENT, f'{config.BAZAARPULSE_BASE_URL}/city/mumbai/page/1.html')}")

    all_rows = []
    for city in CITIES:
        print(f"Scraping {city}...")
        try:
            all_rows += scrape_city(city)
        except Exception as e:
            print(f"  FAILED to scrape {city}: {e}. Continuing with other cities.")
        time.sleep(CRAWL_DELAY_SECONDS)

    if not all_rows:
        print("No listings scraped -- is the site running? (cd bazaarpulse_site && python3 -m http.server 8080)")
        sys.exit(1)

    fieldnames = list(all_rows[0].keys())
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_rows)

    n_no_price = sum(1 for r in all_rows if r["price_inr"] is None)
    print(f"Wrote {len(all_rows)} listings to {out_path} ({n_no_price} with unparsed price)")


if __name__ == "__main__":
    main()
