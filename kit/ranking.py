"""Pure helpers with no database: card order, the "New" badge, domain normalisation, safe redirects."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit

NEW_DAYS = 14
NEW_MAX_USES = 3


def rank(items: list[dict]) -> list[dict]:
    """Most uses ("I use this" votes) first; ties: pinned by name, then newest approved."""
    items = sorted(items, key=lambda i: i["approved_at"] or "", reverse=True)
    return sorted(items, key=lambda i: (-i["uses_count"], not i["pinned"], i["name"].casefold() if i["pinned"] else ""))


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


def safe_next(url: str | None, default: str = "/") -> str:
    """A local path only: no other site, no //host, no backslash tricks."""
    if not url or not url.startswith("/") or url.startswith("//") or "\\" in url:
        return default
    return url


def with_msg(path: str, m: str) -> str:
    p = urlsplit(path)
    q = [(k, v) for k, v in parse_qsl(p.query) if k != "m"] + [("m", m)]
    return f"{p.path}?{urlencode(q)}"
