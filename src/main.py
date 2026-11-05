"""Stages 3-4: discover, extract, clean, validate and store book records."""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parent))
from schema import Book, clean_record  # noqa: E402

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
OUTPUT_DIR = PROJECT_DIR / "output"
_last_network_request_at: float | None = None


def is_allowed_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "https" and parsed.hostname == ALLOWED_HOST


STATS = {"pages_fetched": 0, "cache_hits": 0, "retries": 0}
LAST_ERROR: str | None = None
RETRY_DELAY_SECONDS = 2.0


def _wait_for_rate_limit() -> None:
    global _last_network_request_at
    if _last_network_request_at is not None:
        elapsed = time.monotonic() - _last_network_request_at
        if elapsed < MIN_REQUEST_INTERVAL:
            time.sleep(MIN_REQUEST_INTERVAL - elapsed)
    _last_network_request_at = time.monotonic()


def fetch_or_cache(url: str, cache_file: Path) -> bytes | None:
    """Use cached bytes first; otherwise fetch (one retry on timeout/5xx), then cache.

    Returns None on failure and records the reason in LAST_ERROR. Never raises.
    404/403 (and other 4xx) are NOT retried: asking again will not change the answer.
    """
    global LAST_ERROR
    LAST_ERROR = None

    if cache_file.exists():
        content = cache_file.read_bytes()
        STATS["cache_hits"] += 1
        print(f"CACHE HIT {cache_file.relative_to(PROJECT_DIR)} ({len(content):,} bytes)")
        return content

    if not is_allowed_url(url):
        LAST_ERROR = "blocked URL outside approved host"
        print(f"FETCH FAILED: {LAST_ERROR}: {url}")
        return None

    for attempt in (1, 2):
        _wait_for_rate_limit()
        print(f"FETCH {url}" + (" (retry)" if attempt == 2 else ""))
        retryable = False
        try:
            response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT_SECONDS)
            if response.status_code == 200:
                content = response.content
                cache_file.parent.mkdir(parents=True, exist_ok=True)
                cache_file.write_bytes(content)
                STATS["pages_fetched"] += 1
                print(f"FETCHED {cache_file.relative_to(PROJECT_DIR)} ({len(content):,} bytes)")
                return content
            LAST_ERROR = f"HTTP {response.status_code}"
            retryable = 500 <= response.status_code < 600
        except (requests.Timeout, requests.ConnectionError) as exc:
            LAST_ERROR = f"{type(exc).__name__}: {exc}"
            retryable = True
        except requests.RequestException as exc:
            LAST_ERROR = f"{type(exc).__name__}: {exc}"

        print(f"FETCH FAILED: {LAST_ERROR} for {url}")
        if attempt == 1 and retryable:
            STATS["retries"] += 1
            time.sleep(RETRY_DELAY_SECONDS)
            continue
        break
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


def extract_raw_record(html: bytes, product_url: str, source_page: str, fetched_at: str) -> dict:
    """Extract the required eight fields from the book's product area."""
    soup = BeautifulSoup(html, "html.parser", from_encoding="utf-8")
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
        "fetched_at": fetched_at,
    }


def fetched_at_for(cache_file: Path) -> str:
    """Stable timestamp = when the page was cached, so re-runs give identical output."""
    ts = datetime.fromtimestamp(cache_file.stat().st_mtime, tz=timezone.utc)
    return ts.isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="The polite scraper")
    parser.add_argument(
        "--extra-url", action="append", default=[],
        help="Add an extra product URL to the list (repeatable); used to test failure handling.",
    )
    return parser.parse_args(argv)


def write_report(started: datetime, t0: float, valid: int, invalid: int,
                 failed: int, catalogue_pages: int, crashed: str | None) -> None:
    report = {
        "start_time": started.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "duration_seconds": round(time.monotonic() - t0, 2),
        "catalogue_pages": catalogue_pages,
        "pages_fetched": STATS["pages_fetched"],
        "cache_hits": STATS["cache_hits"],
        "retries": STATS["retries"],
        "valid_records": valid,
        "invalid_records": invalid,
        "failed_pages": failed,
        "crashed": crashed,
    }
    (OUTPUT_DIR / "run-report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("RUN REPORT:", json.dumps(report))


def process_books(book_pairs: list[tuple[str, str]]):
    """Handle every book page on its own: one bad page is logged and skipped."""
    records: list[dict] = []
    failures: list[dict] = []
    for index, (product_url, source_page) in enumerate(book_pairs, start=1):
        detail_cache = CACHE_DIR / "details" / f"book-{index:03d}.html"
        try:
            html = fetch_or_cache(product_url, detail_cache)
            if html is None:
                failures.append({"product_url": product_url, "stage": "fetch",
                                 "error": LAST_ERROR or "Detail page fetch failed"})
                continue
            records.append(extract_raw_record(html, product_url, source_page, fetched_at_for(detail_cache)))
        except Exception as exc:  # noqa: BLE001 - isolate every page, whatever goes wrong
            failures.append({"product_url": product_url, "stage": "extract",
                             "error": f"{type(exc).__name__}: {exc}"})
            print(f"PAGE FAILED {product_url}: {exc}")
    return records, failures


def run(extra_urls: list[str], state: dict) -> int:
    catalogue_pages, book_pairs = discover_catalogue(max_pages=3)
    state["catalogue_pages"] = catalogue_pages
    seen = {u for u, _ in book_pairs}
    for url in extra_urls:
        if url not in seen:
            seen.add(url)
            book_pairs.append((url, "manual"))
            print(f"EXTRA URL ADDED: {url}")

    records, failures = process_books(book_pairs)
    state["failed"] = len(failures)

    # Clean + validate; identity is the canonical product_url, so a repeat counts once.
    good: dict[str, dict] = {}
    invalid: list[dict] = []
    for raw in records:
        try:
            book = Book(**clean_record(raw))
        except (ValidationError, ValueError) as exc:
            reason = (
                "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
                if isinstance(exc, ValidationError) else str(exc)
            )
            invalid.append({"product_url": raw.get("product_url"), "stage": "validate",
                            "error": reason, "record": raw})
            continue
        good.setdefault(book.product_url, book.model_dump())

    books = sorted(good.values(), key=lambda b: b["product_url"])
    state["valid"], state["invalid"] = len(books), len(invalid)

    # Files are rewritten from scratch each run (never appended) -> idempotent.
    (RAW_DIR / "raw_records.json").write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")
    (OUTPUT_DIR / "books.json").write_text(
        json.dumps(books, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (OUTPUT_DIR / "errors.json").write_text(
        json.dumps(failures + invalid, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"raw_records={len(records)}")
    print(f"valid_books={len(books)}")
    print(f"invalid_records={len(invalid)}")
    print(f"failed_pages={len(failures)}")

    # Non-zero only if the real job fell short; known-bad extras don't fail the run.
    return 0 if catalogue_pages == 3 and len(books) == 60 else 1


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    started = datetime.now(timezone.utc)
    t0 = time.monotonic()
    state = {"catalogue_pages": 0, "valid": 0, "invalid": 0, "failed": 0}
    crashed: str | None = None
    code = 1
    try:
        code = run(args.extra_url, state)
    except Exception as exc:  # noqa: BLE001 - still report, then exit non-zero
        crashed = f"{type(exc).__name__}: {exc}"
        print(f"RUN CRASHED: {crashed}")
    finally:
        write_report(started, t0, state["valid"], state["invalid"], state["failed"],
                     state["catalogue_pages"], crashed)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
