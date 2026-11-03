"""Stage 3: discover catalogue pages and extract provenance-aware raw book records."""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://books.toscrape.com/"
ALLOWED_HOST = "books.toscrape.com"
TIMEOUT_SECONDS = 10
MIN_REQUEST_INTERVAL = 0.5
USER_AGENT = (
    "FlyRankInternshipA9/1.0 "
    "(educational scraper; replace with your public repository URL when available)"
)

PROJECT_DIR = Path(__file__).resolve().parents[1]
CACHE_DIR = PROJECT_DIR / "cache"
RAW_DIR = PROJECT_DIR / "data" / "raw"
_last_network_request_at: float | None = None


def is_allowed_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "https" and parsed.hostname == ALLOWED_HOST


def fetch_or_cache(url: str, cache_file: Path) -> bytes | None:
    """Use cached bytes first; otherwise fetch, check HTTP 200, then cache."""
    global _last_network_request_at

    if cache_file.exists():
        content = cache_file.read_bytes()
        print(f"CACHE HIT {cache_file.relative_to(PROJECT_DIR)} ({len(content):,} bytes)")
        return content

    if not is_allowed_url(url):
        print(f"FETCH FAILED: blocked URL outside approved host: {url}")
        return None

    if _last_network_request_at is not None:
        elapsed = time.monotonic() - _last_network_request_at
        if elapsed < MIN_REQUEST_INTERVAL:
            time.sleep(MIN_REQUEST_INTERVAL - elapsed)

    print(f"FETCH {url}")
    _last_network_request_at = time.monotonic()

    try:
        response = requests.get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=TIMEOUT_SECONDS,
        )
        if response.status_code != 200:
            print(f"FETCH FAILED: HTTP {response.status_code} for {url}")
            return None

        content = response.content
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_bytes(content)
        print(f"FETCHED {cache_file.relative_to(PROJECT_DIR)} ({len(content):,} bytes)")
        return content
    except requests.RequestException as exc:
        print(f"FETCH FAILED: {type(exc).__name__}: {exc}")
        return None


def get_book_links(html: bytes, page_url: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    urls = []
    for anchor in soup.select("article.product_pod h3 a"):
        href = anchor.get("href")
        if href:
            absolute_url = urljoin(page_url, href)
            if is_allowed_url(absolute_url):
                urls.append(absolute_url)
    return urls


def get_next_page(html: bytes, page_url: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    anchor = soup.select_one("li.next a")
    if not anchor or not anchor.get("href"):
        return None
    next_url = urljoin(page_url, anchor["href"])
    return next_url if is_allowed_url(next_url) else None


def discover_catalogue(max_pages: int = 3) -> tuple[int, list[tuple[str, str]]]:
    """Discover links by following Next; retain the catalogue page as provenance."""
    current_url: str | None = BASE_URL
    page_count = 0
    pairs: list[tuple[str, str]] = []

    while current_url and page_count < max_pages:
        page_number = page_count + 1
        html = fetch_or_cache(current_url, CACHE_DIR / f"catalogue-page-{page_number}.html")
        if html is None:
            break

        page_count += 1
        pairs.extend((book_url, current_url) for book_url in get_book_links(html, current_url))
        current_url = get_next_page(html, current_url)

    unique_pairs: list[tuple[str, str]] = []
    seen: set[str] = set()
    for product_url, source_page in pairs:
        if product_url not in seen:
            seen.add(product_url)
            unique_pairs.append((product_url, source_page))

    print(f"catalogue_pages={page_count}")
    print(f"discovered={len(pairs)}")
    print(f"unique_urls={len(unique_pairs)}")
    return page_count, unique_pairs


def extract_raw_record(html: bytes, product_url: str, source_page: str) -> dict:
    """Extract the required eight fields from the book's product area."""
    soup = BeautifulSoup(html, "html.parser")
    product = soup.select_one("article.product_page")
    if product is None:
        raise ValueError("Product area article.product_page was not found")

    product_main = product.select_one("div.row div.product_main")
    if product_main is None:
        raise ValueError("Product details section was not found")

    title_node = product_main.select_one("h1")
    price_node = product_main.select_one(".price_color")
    availability_node = product_main.select_one(".availability")
    rating_node = product_main.select_one("p.star-rating")

    description_heading = product.select_one("#product_description")
    description_node = (
        description_heading.find_next_sibling("p")
        if description_heading is not None else None
    )
    description = description_node.get_text(" ", strip=True) if description_node else None
    if description == "":
        description = None

    rating_words = (
        [name for name in rating_node.get("class", []) if name != "star-rating"]
        if rating_node else []
    )

    if not all((title_node, price_node, availability_node, rating_words)):
        raise ValueError("One or more required product fields are missing")

    return {
        "title": title_node.get_text(" ", strip=True),
        "product_url": product_url,
        "price_text": price_node.get_text(" ", strip=True),
        "availability_text": availability_node.get_text(" ", strip=True),
        "rating_text": rating_words[0],
        "description": description,
        "source_page": source_page,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
    }


def main() -> int:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    catalogue_pages, book_pairs = discover_catalogue(max_pages=3)
    records: list[dict] = []
    errors: list[dict[str, str]] = []

    for index, (product_url, source_page) in enumerate(book_pairs, start=1):
        detail_cache = CACHE_DIR / "details" / f"book-{index:03d}.html"
        html = fetch_or_cache(product_url, detail_cache)
        if html is None:
            errors.append({"product_url": product_url, "error": "Detail page fetch failed"})
            continue
        try:
            records.append(extract_raw_record(html, product_url, source_page))
        except (ValueError, AttributeError, IndexError) as exc:
            errors.append({"product_url": product_url, "error": str(exc)})
            print(f"EXTRACT FAILED {product_url}: {exc}")

    output_path = RAW_DIR / "raw_records.json"
    output_path.write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")
    (RAW_DIR / "errors.json").write_text(json.dumps(errors, indent=2), encoding="utf-8")

    print(f"detail_pages={len(records)}")
    print(f"raw_records={len(records)}")
    print(f"errors={len(errors)}")
    if records:
        print("SAMPLE RAW RECORD:")
        print(json.dumps(records[0], indent=2, ensure_ascii=False))

    # Exit nonzero when the checkpoint was not actually achieved.
    return 0 if catalogue_pages == 3 and len(records) == 60 and not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
