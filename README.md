# The-polite-scraper

## Stage 3 — Extract raw book records

The crawler follows the catalogue's own Next link for at most three pages, resolves product links with `urllib.parse.urljoin`, and removes duplicates. Each product page is fetched once and cached under `cache/details/`. Cache hits do not contact the website and do not incur a delay.

The scraper uses a descriptive User-Agent, a 10-second timeout, checks for HTTP 200 before saving/parsing, and waits at least 0.5 seconds between real requests. Product fields are selected within `article.product_page` and `div.product_main`, not from the whole document.

Each raw record has all eight required fields:
`title`, `product_url`, `price_text`, `availability_text`, `rating_text`, `description`, `source_page`, and `fetched_at`. Missing descriptions are stored as JSON `null`; `fetched_at` is a UTC ISO-8601 timestamp.

Output files:
- `data/raw/raw_records.json` — extracted records
- `data/raw/errors.json` — failed fetches or extraction errors

Run:
```bash
python -m pip install -r requirements.txt
python src/main.py
```

Expected checkpoint on a complete run:
```text
catalogue_pages=3
discovered=60
unique_urls=60
detail_pages=60
raw_records=60
errors=0
```

The script prints one complete sample record. Counts are measured from the pages actually fetched or cached; they are not hardcoded. A partial run exits with a nonzero status.

## Stage 4 — Clean, check, store

- `src/schema.py` defines the `Book` Pydantic model. Required: `title`, `product_url`, `price_text`, `price_gbp` (float), `availability_text`, `rating_text`, `source_page`, `fetched_at`. Optional: `description`. URLs must start with `https://`.
- `price_text` (`"£51.77"`) is kept as-is next to the clean number `price_gbp` (`51.77`).
- `product_url` (absolute, canonical) is each record's identity; duplicates count once.
- Every record is validated before storing. Failures go to `output/errors.json` with the reason and never into `output/books.json`.
- Idempotent: output files are rewritten from scratch (never appended), records are sorted by `product_url`, and `fetched_at` is the page's cache time, so a second run produces byte-identical output with 60 records, not 120.

Output: `output/books.json`, `output/errors.json`.
