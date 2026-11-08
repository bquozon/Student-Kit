"""Email through SMTP (settings SMTP_*). Without SMTP_HOST the mail is appended to DATA_DIR/outbox.txt (dev, tests)."""

from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage

from . import settings


def send(to: str | list[str], subject: str, body: str) -> None:
    """Raises on failure: the caller decides whether that matters (login: yes, a notification: no)."""
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


def try_send(to, subject: str, body: str) -> bool:
    """For notifications: a mail problem is logged, never breaks the action that triggered it."""
    try:
        send(to, subject, body)
        return True
    except (OSError, smtplib.SMTPException) as e:
        print(f"[mail] failed to {to}: {e}", flush=True)
        return False
