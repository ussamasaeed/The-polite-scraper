"""Schema for a finished book record, plus cleaning of raw scraped strings."""
from __future__ import annotations

import re
from typing import Optional

from pydantic import BaseModel, ConfigDict, field_validator

_PRICE_RE = re.compile(r"(\d+(?:[.,]\d+)?)")


def parse_price_gbp(price_text: str) -> float:
    """'£51.77' (or mojibake 'Â£51.77') -> 51.77. Raises ValueError if no number."""
    match = _PRICE_RE.search(price_text or "")
    if not match:
        raise ValueError(f"Cannot parse price from {price_text!r}")
    return float(match.group(1).replace(",", ""))


class Book(BaseModel):
    """A finished, validated record. Raw text and clean value live side by side."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str
    product_url: str            # canonical absolute URL = the record's identity
    price_text: str             # raw, e.g. "£51.77"
    price_gbp: float            # clean, e.g. 51.77
    availability_text: str
    rating_text: str
    description: Optional[str] = None   # the only optional field
    source_page: str
    fetched_at: str

    @field_validator("title", "price_text", "availability_text", "rating_text", "fetched_at")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v:
            raise ValueError("must not be empty")
        return v

    @field_validator("product_url", "source_page")
    @classmethod
    def _https(cls, v: str) -> str:
        if not v.startswith("https://"):
            raise ValueError("URL must start with https://")
        return v

    @field_validator("price_gbp")
    @classmethod
    def _positive(cls, v: float) -> float:
        if v < 0:
            raise ValueError("price must be >= 0")
        return v


def clean_record(raw: dict) -> dict:
    """Add the clean fields derived from raw strings (raw values are kept)."""
    cleaned = dict(raw)
    cleaned["price_gbp"] = parse_price_gbp(raw.get("price_text", ""))
    return cleaned
