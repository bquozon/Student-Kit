"""Email through SMTP (settings SMTP_*), used only for the optional daily digest to ADMIN_EMAIL.
Without SMTP_HOST the mail is appended to DATA_DIR/outbox.txt instead (dev, tests)."""

from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage

from . import settings


def send(to: str | list[str], subject: str, body: str) -> None:
    """Raises on failure (the job then fails and shows in journalctl)."""
    msg = EmailMessage()
    msg["From"] = settings.MAIL_FROM
    msg["To"] = ", ".join(to) if isinstance(to, list) else to
    msg["Subject"] = subject
    msg.set_content(body)
    if not settings.SMTP_HOST:
        settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(settings.DATA_DIR / "outbox.txt", "a") as f:
            f.write(f"To: {msg['To']}\nSubject: {subject}\n\n{body}\n.\n")  # decoded, so links stay clickable
        print(f"[mail] to {msg['To']}: {subject}", flush=True)
        return
    ctx = ssl.create_default_context()
    if settings.SMTP_PORT == 465:
        smtp = smtplib.SMTP_SSL(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15, context=ctx)
    else:
        smtp = smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15)
        smtp.starttls(context=ctx)
    with smtp:
        if settings.SMTP_USER:
            smtp.login(settings.SMTP_USER, settings.SMTP_PASS)
        smtp.send_message(msg)
