"""No accounts. Two cookies identify a browser, nothing more:

- `mod`: set when the owner logs in at /mod with ADMIN_KEY (like polyuni's admin login). 90 days.
- `voter`: a random id for the anonymous "I use this" vote, so the same browser can't vote twice and can undo.

Only SHA-256 hashes of both are stored. Rate limits live in memory (one process, like polyuni)."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from collections import defaultdict

from flask import request

from . import settings

MOD_COOKIE = "mod"
VOTER_COOKIE = "voter"


def client_ip() -> str:
    # our only way in is the Caddy proxy, which sets X-Real-IP (deploy/install.sh); never trust X-Forwarded-For's left
    return request.headers.get("X-Real-IP") or request.remote_addr or "?"


def set_cookie(resp, name: str, value: str, days: int) -> None:
    resp.set_cookie(name, value, max_age=days * 86400, httponly=True, secure=settings.COOKIE_SECURE, samesite="Lax")


def sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


class Limiter:
    """At most `n` events per `window` seconds per key. Memory only, so a restart resets it."""

    def __init__(self):
        self.hits: dict[str, list[float]] = defaultdict(list)
        self.lock = threading.Lock()

    def allow(self, key: str, n: int, window: float) -> bool:
        now = time.monotonic()
        with self.lock:
            recent = [t for t in self.hits[key] if now - t < window]
            ok = len(recent) < n
            if ok:
                recent.append(now)
            self.hits[key] = recent
            return ok


limiter = Limiter()


def open_mod_session(con, key: str, ip: str) -> str | None:
    """The login form: a new session id for the cookie if `key` is ADMIN_KEY. Wrong guesses are rate-limited."""
    if not settings.ADMIN_KEY or not limiter.allow("modkey:" + ip, 10, 900):
        return None
    if not hmac.compare_digest(key.encode(), settings.ADMIN_KEY.encode()):
        return None
    sid = secrets.token_urlsafe(32)
    con.execute(
        "INSERT INTO sessions (id, expires_at) VALUES (?, datetime('now', ?))",
        (sha(sid), f"+{settings.SESSION_DAYS} days"),
    )
    con.execute("DELETE FROM sessions WHERE expires_at < datetime('now')")
    return sid


def is_mod(con, sid: str | None) -> bool:
    return bool(
        sid
        and settings.ADMIN_KEY
        and con.execute("SELECT 1 FROM sessions WHERE id=? AND expires_at > datetime('now')", (sha(sid),)).fetchone()
    )


def close_mod_session(con, sid: str | None) -> None:
    if sid:
        con.execute("DELETE FROM sessions WHERE id=?", (sha(sid),))
