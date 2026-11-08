"""Private, cookie-free visit statistics (same approach as polyuni.ch's stats.py, stored in the `stat` table).

Only daily totals are kept: no IP, no cookie, no cross-day identifier. A visitor is counted once per day through a
hash of (IP, browser, a random salt thrown away at midnight). Bots are ignored."""

from __future__ import annotations

import hashlib
import re
import secrets
import threading
from datetime import date, datetime, timedelta

MAX_KEYS = 300  # distinct values per group per day (stops anyone from bloating the table)
BOT = re.compile(
    r"bot|crawl|spider|slurp|headless|lighthouse|preview|facebookexternalhit|whatsapp|telegram|"
    r"discord|slack|curl|wget|python|go-http|java/|monitor|uptime|playwright",
    re.I,
)
GROUPS = {
    "ref": "Where visitors come from",
    "city": "City pages viewed",
    "search": "What people search",
    "filter": "University filters used",
    "click": "Most opened services",
    "device": "Devices",
    "lang": "Browser language",
    "action": "Actions",
}

_lock = threading.Lock()
_salt_day, _salt, _seen = "", "", set()


def bump(con, grp: str, key: str, n: int = 1, day: str | None = None) -> None:
    key = re.sub(r"\s+", " ", str(key)).strip()[:80]
    if not key:
        return
    day = day or date.today().isoformat()
    cur = con.execute("UPDATE stat SET n = n + ? WHERE day=? AND grp=? AND key=?", (n, day, grp, key))
    if cur.rowcount == 0:
        if con.execute("SELECT count(*) FROM stat WHERE day=? AND grp=?", (day, grp)).fetchone()[0] < MAX_KEYS:
            con.execute(
                "INSERT INTO stat (day, grp, key, n) VALUES (?, ?, ?, ?)"
                " ON CONFLICT(day, grp, key) DO UPDATE SET n = n + excluded.n",
                (day, grp, key, n),
            )


def is_bot(ua: str) -> bool:
    return not ua or bool(BOT.search(ua))


def view(con, ip: str, ua: str, ref: str, lang: str, city: str, host: str) -> None:
    """A page view of a city page, counted on the server (works without JS)."""
    global _salt_day, _salt, _seen
    if is_bot(ua):
        return
    today = date.today().isoformat()
    with _lock:
        if _salt_day != today:
            _salt_day, _salt, _seen = today, secrets.token_hex(16), set()
        vid = hashlib.sha256(f"{_salt}|{ip}|{ua}".encode()).hexdigest()[:16]
        first = vid not in _seen
        _seen.add(vid)
    bump(con, "total", "views")
    bump(con, "city", city)
    if first:
        bump(con, "total", "visitors")
        bump(con, "hour", f"{datetime.now().hour:02d}")
        bump(
            con,
            "device",
            "Phone" if re.search(r"Mobi|iPhone|Android", ua) else "Tablet" if "iPad" in ua else "Computer",
        )
        bump(con, "lang", (lang[:2].lower() or "?"))
        bump(con, "ref", _ref(ref, host))


def hit(con, ua: str, h: dict, item_name) -> None:
    """A message from the page's JS: a search, a filter or a card click. `item_name(id)` looks up a click's item."""
    if is_bot(ua):
        return
    t = str(h.get("t", ""))
    if t == "search":
        bump(con, "search", str(h.get("q", "")).lower()[:60])
    elif t == "filter":
        bump(con, "filter", str(h.get("u", ""))[:10] or "All")
    elif t == "click":
        name = item_name(h.get("id"))
        if name:
            bump(con, "click", name)


def _ref(ref: str, own_host: str) -> str:
    host = re.sub(r"^https?://", "", ref or "").split("/")[0].lower().removeprefix("www.")
    if not host or host == own_host.removeprefix("www."):
        return "Direct / app"
    for pat, name in (
        (r"google\.", "Google"),
        (r"instagram", "Instagram"),
        (r"bing\.", "Bing"),
        (r"duckduckgo", "DuckDuckGo"),
        (r"reddit", "Reddit"),
        (r"whatsapp", "WhatsApp"),
        (r"polyuni\.ch", "polyuni.ch"),
        (r"chatgpt|openai", "ChatGPT"),
    ):
        if re.search(pat, host):
            return name
    return host[:60]


def summary(con, days: int) -> dict:
    """Totals for the last `days` days (today included), for the stats page."""
    start = (date.today() - timedelta(days=days - 1)).isoformat()

    def top(grp, limit=15):
        return con.execute(
            "SELECT key, SUM(n) n FROM stat WHERE grp=? AND day>=? GROUP BY key ORDER BY n DESC, key LIMIT ?",
            (grp, start, limit),
        ).fetchall()

    per_day = {
        (r["day"], r["key"]): r["n"]
        for r in con.execute("SELECT day, key, n FROM stat WHERE grp='total' AND day>=?", (start,))
    }
    rng = [(date.today() - timedelta(days=i)).isoformat() for i in range(days - 1, -1, -1)]
    series = [(d, per_day.get((d, "visitors"), 0), per_day.get((d, "views"), 0)) for d in rng]
    hours = dict(top("hour", 24))
    return {
        "start": start,
        "series": series,
        "visitors": sum(v for _, v, _ in series),
        "views": sum(w for _, _, w in series),
        "hours": [(f"{h:02d}", hours.get(f"{h:02d}", 0)) for h in range(24)],
        "groups": {g: top(g) for g in GROUPS},
    }
