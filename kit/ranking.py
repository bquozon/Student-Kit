"""Pure helpers with no database: card order, the "New" badge, domain normalisation, email → university."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

NEW_DAYS = 14
NEW_MAX_USES = 3
EMAIL = re.compile(r"^[^@\s]+@([a-z0-9-]+(?:\.[a-z0-9-]+)+)$")


def rank(items: list[dict]) -> list[dict]:
    """Pinned first (by name), then by uses_count desc, ties by approved_at desc (newest first)."""
    pinned = sorted((i for i in items if i["pinned"]), key=lambda i: i["name"].casefold())
    rest = sorted((i for i in items if not i["pinned"]), key=lambda i: i["approved_at"] or "", reverse=True)
    rest.sort(key=lambda i: -i["uses_count"])  # stable: keeps the approved_at order inside equal counts
    return pinned + rest


def is_new(item: dict, now: datetime | None = None) -> bool:
    """Fewer than 3 uses and approved in the last 14 days. Pinned (curated/seeded) items are never "New"."""
    if item["pinned"] or not item["approved_at"] or item["uses_count"] >= NEW_MAX_USES:
        return False
    now = now or datetime.now(timezone.utc)
    approved = datetime.fromisoformat(item["approved_at"]).replace(tzinfo=timezone.utc)
    return now - approved < timedelta(days=NEW_DAYS)


def domain(url: str) -> str:
    """'https://www.WOKO.ch/rooms/' → 'woko.ch' (for duplicate checks)."""
    url = url.strip()
    host = urlsplit(url if "://" in url else "https://" + url).hostname or ""
    return host.lower().removeprefix("www.").rstrip(".")


def pretty_url(url: str) -> str:
    """What a card shows: no scheme, no www., no trailing slash."""
    return re.sub(r"^https?://(www\.)?", "", url.strip()).rstrip("/")


def valid_https(url: str) -> bool:
    p = urlsplit(url.strip())
    return p.scheme == "https" and bool(p.hostname) and "." in p.hostname and not p.username and len(url) <= 500


def university_for(email: str, domains: dict[str, str]) -> str | None:
    """The university code for a student email, or None. Only exact listed domains count (no subdomains)."""
    m = EMAIL.match(email.strip().lower())
    return domains.get(m.group(1)) if m else None
