"""All settings come from the environment; a .env file in the working directory is read first (never overrides)."""

from __future__ import annotations

import os
from pathlib import Path

if Path(".env").exists():
    for line in Path(".env").read_text().splitlines():
        k, sep, v = line.partition("=")
        if sep and not k.strip().startswith("#"):
            os.environ.setdefault(k.strip(), v.strip())


def env(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


DATA_DIR = Path(env("DATA_DIR", "data"))
DB_PATH = DATA_DIR / "kit.sqlite"
SEED_FILE = Path(env("SEED_FILE", "data/seed.json"))
SITE_URL = env("SITE_URL", "http://localhost:8800").rstrip("/")
COOKIE_SECURE = SITE_URL.startswith("https://")
ADMIN_KEY = env("ADMIN_KEY")  # the mod password, typed at /mod; empty = mod panel off
if len(ADMIN_KEY) < 20 or ADMIN_KEY.startswith("change-me"):  # a short or example key would be guessable
    ADMIN_KEY = ""
ADMIN_EMAIL = env("ADMIN_EMAIL").lower()  # optional: where the daily digest goes

SMTP_HOST = env("SMTP_HOST")  # empty: the digest is written to DATA_DIR/outbox.txt instead (local dev, tests)
SMTP_PORT = int(env("SMTP_PORT", "587"))
SMTP_USER = env("SMTP_USER")
SMTP_PASS = env("SMTP_PASS")
MAIL_FROM = env("MAIL_FROM", "Swiss Student Kit <noreply@localhost>")

SESSION_DAYS = 90
MAX_PENDING = 100  # whole queue: past this the form says "come back later" (stops a flood from burying real ones)
SUGGEST_PER_IP = (5, 3600)  # suggestions per IP per hour
KINDS = ("Web", "App", "Web + app", "Pass")
REPORT_TYPES = {"dead_link": "Dead link", "wrong_info": "Wrong info", "duplicate": "Duplicate", "other": "Other"}
