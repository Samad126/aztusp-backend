"""Deliver a result change: email through the SMTP relay and Telegram through the shared bot."""

import logging
import smtplib
import ssl
from email.message import EmailMessage

import requests

from .config import SmtpSettings, WatcherSettings
from .results import Change

log = logging.getLogger(__name__)

TIMEOUT = 20
FIELD_LABELS = {
    "course_type": "Type",
    "credits": "Credits",
    "final_score": "Final score",
    "grade": "Grade",
    "retake": "Retake",
}


class NotifyError(RuntimeError):
    pass


def format_message(changes: list[Change]) -> tuple[str, str]:
    """Subject and body naming each course and the result that changed."""
    courses: dict[tuple[str | None, str], list[Change]] = {}
    for change in changes:
        courses.setdefault((change.semester, change.course), []).append(change)

    if len(courses) == 1:
        subject = f"AZTUSP: result updated for {next(iter(courses))[1]}"
    else:
        subject = f"AZTUSP: results updated for {len(courses)} courses"

    blocks = []
    for (semester, course), items in courses.items():
        lines = [f"Course: {course}"]
        if semester:
            lines.append(f"Semester: {semester}")
        for change in items:
            line = f"{FIELD_LABELS.get(change.field, change.field)}: {change.new}"
            if change.old is not None:
                line += f" (was {change.old})"
            lines.append(line)
        blocks.append("\n".join(lines))
    return subject, "\n\n".join(blocks)


def notify(settings: WatcherSettings, email: str | None, telegram_chat_id: str | None, subject: str, body: str) -> bool:
    """Send to the given addresses over the configured channels. Failures are logged, not raised; True only if every send worked."""
    ok = True
    if email:
        if settings.smtp is None:
            log.error("Email is requested but SMTP is not configured")
            ok = False
        else:
            try:
                send_email(settings.smtp, email, subject, body)
                log.info("Sent by email")
            except NotifyError as exc:
                log.error("%s", exc)
                ok = False
    if telegram_chat_id:
        if settings.telegram_bot_token is None:
            log.error("Telegram is requested but TELEGRAM_BOT_TOKEN is not set")
            ok = False
        else:
            try:
                send_telegram(settings.telegram_bot_token, telegram_chat_id, f"{subject}\n\n{body}")
                log.info("Sent by Telegram")
            except NotifyError as exc:
                log.error("%s", exc)
                ok = False
    return ok


def send_email(settings: SmtpSettings, to: str, subject: str, body: str) -> None:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = settings.sender
    message["To"] = to
    message.set_content(body)

    # smtplib's own default context skips certificate checks, and this sends the account password.
    context = ssl.create_default_context()
    try:
        if settings.port == 465:
            smtp = smtplib.SMTP_SSL(settings.host, settings.port, timeout=TIMEOUT, context=context)
        else:
            smtp = smtplib.SMTP(settings.host, settings.port, timeout=TIMEOUT)
        with smtp:
            if settings.port != 465:
                smtp.starttls(context=context)
            smtp.login(settings.username, settings.password)
            smtp.send_message(message)
    except (smtplib.SMTPException, OSError) as exc:
        raise NotifyError(f"Email failed: {exc}") from None


def send_telegram(token: str, chat_id: str, text: str) -> None:
    # The request URL holds the bot token, so errors are re-raised without the original exception text.
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=TIMEOUT,
        )
    except requests.RequestException as exc:
        raise NotifyError(f"Telegram request failed: {type(exc).__name__}") from None
    if not response.ok:
        raise NotifyError(f"Telegram rejected the message: HTTP {response.status_code} {response.text[:200]}")
