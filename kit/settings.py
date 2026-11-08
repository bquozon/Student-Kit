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
ADMIN_EMAIL = env("ADMIN_EMAIL").lower()

SMTP_HOST = env("SMTP_HOST")  # empty: mails are written to DATA_DIR/outbox.txt instead (local dev, tests)
SMTP_PORT = int(env("SMTP_PORT", "587"))
SMTP_USER = env("SMTP_USER")
SMTP_PASS = env("SMTP_PASS")
MAIL_FROM = env("MAIL_FROM", "Swiss Student Kit <noreply@localhost>")

TOKEN_MINUTES = 15
SESSION_DAYS = 90
MAX_PENDING = 5
KINDS = ("Web", "App", "Web + app", "Pass")
REPORT_TYPES = {"dead_link": "Dead link", "wrong_info": "Wrong info", "duplicate": "Duplicate", "other": "Other"}
