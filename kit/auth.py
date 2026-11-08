"""Passwordless login for verified students: a magic link to a university email, then a 90-day session cookie.

Only SHA-256 hashes of tokens and session ids are stored. Rate limits live in memory (one process, like polyuni)."""

from __future__ import annotations

import hashlib
import secrets
import threading
import time
from collections import defaultdict
from urllib.parse import urlencode

from . import db, mail, settings
from .ranking import university_for

COOKIE = "sid"


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


def start_login(con, email: str, ip: str, next_url: str = "/") -> str | None:
    """Send a magic link. Returns an error message for the form, or None when the mail went out."""
    email = email.strip().lower()
    domains = db.email_domains(con)
    if not university_for(email, domains):
        return "Please use your university email (" + ", ".join("@" + d for d in sorted(domains)) + ")."
    if not limiter.allow("ip:" + ip, 20, 3600) or not limiter.allow("email:" + email, 3, 900):
        return "Too many login emails. Please wait a few minutes and try again."
    token = secrets.token_urlsafe(32)
    con.execute(
        "INSERT INTO login_tokens (token_hash, email, expires_at) VALUES (?, ?, datetime('now', ?))",
        (sha(token), email, f"+{settings.TOKEN_MINUTES} minutes"),
    )
    con.execute("DELETE FROM login_tokens WHERE expires_at < datetime('now', '-1 day')")
    link = f"{settings.SITE_URL}/login/verify?" + urlencode({"t": token, "next": next_url})
    try:
        mail.send(
            email,
            "Your Swiss Student Kit login link",
            f"Hi!\n\nOpen this link to log in (valid {settings.TOKEN_MINUTES} minutes, works once):\n\n{link}\n\n"
            "If you didn't ask for it, ignore this email.\n",
        )
    except Exception as e:  # noqa: BLE001 — any SMTP/network problem: tell the user, keep the server up
        print(f"[mail] login link to {email} failed: {e}", flush=True)
        return "We couldn't send the email right now. Please try again in a moment."
    return None


def finish_login(con, token: str) -> str | None:
    """Use a magic-link token once. Returns a new session id for the cookie, or None if invalid/expired/used."""
    with db.tx(con):
        row = con.execute(
            "SELECT email FROM login_tokens WHERE token_hash=? AND used_at IS NULL AND expires_at > datetime('now')",
            (sha(token),),
        ).fetchone()
        if not row:
            return None
        con.execute("UPDATE login_tokens SET used_at=datetime('now') WHERE token_hash=?", (sha(token),))
        uni = university_for(row["email"], db.email_domains(con))
        con.execute(
            "INSERT INTO users (email, university_code) VALUES (?, ?) ON CONFLICT(email) DO NOTHING",
            (row["email"], uni),
        )
        user_id = con.execute("SELECT id FROM users WHERE email=?", (row["email"],)).fetchone()[0]
        sid = secrets.token_urlsafe(32)
        con.execute(
            "INSERT INTO sessions (id, user_id, expires_at) VALUES (?, ?, datetime('now', ?))",
            (sha(sid), user_id, f"+{settings.SESSION_DAYS} days"),
        )
    return sid


def current_user(con, sid: str | None):
    if not sid:
        return None
    return con.execute(
        "SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.id=? AND s.expires_at > datetime('now')",
        (sha(sid),),
    ).fetchone()


def logout(con, sid: str | None) -> None:
    if sid:
        con.execute("DELETE FROM sessions WHERE id=?", (sha(sid),))
