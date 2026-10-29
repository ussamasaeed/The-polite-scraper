
from pathlib import Path

import requests

BASE_URL = "https://books.toscrape.com/"
TIMEOUT_SECONDS = 10

# Replace this with your real repository URL once available.
USER_AGENT = (
    "FlyRankInternshipA9/1.0 "
    "(educational scraper; repository URL not yet published)"
)

PROJECT_DIR = Path(__file__).resolve().parents[1]
CACHE_DIR = PROJECT_DIR / "cache"
CACHE_FILE = CACHE_DIR / "catalogue-page-1.html"


def fetch_or_cache():
    """Fetch the page once, then use the local cache on later runs."""

    # Development runs should not repeatedly contact the website.
    if CACHE_FILE.exists():
        content = CACHE_FILE.read_bytes()
        print("CACHE HIT")
        print(f"Response size: {len(content):,} bytes")
        return content

    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    print("FETCH")

    try:
        response = requests.get(
            BASE_URL,
            headers={"User-Agent": USER_AGENT},
            timeout=TIMEOUT_SECONDS,
        )

        # Only HTTP 200 is accepted.
        if response.status_code != 200:
            print(f"FETCH FAILED: HTTP {response.status_code}")
            return None

        content = response.content
        CACHE_FILE.write_bytes(content)

        print(f"Response size: {len(content):,} bytes")
        print(f"Cached at: {CACHE_FILE}")

        return content

    except requests.RequestException as error:
        print(f"FETCH FAILED: {type(error).__name__}: {error}")
        return None


if __name__ == "__main__":
    fetch_or_cache()
