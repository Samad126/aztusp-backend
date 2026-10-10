import hashlib
import logging
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import psycopg
from cryptography.fernet import Fernet, InvalidToken
from psycopg.types.json import Jsonb

log = logging.getLogger(__name__)

LINK_CODE_MINUTES = 15
TOKEN_DAYS = 1

SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS users (
        id BIGSERIAL PRIMARY KEY,
        site_username TEXT NOT NULL UNIQUE,
        token_hash TEXT NOT NULL UNIQUE,
        token_expires_at TIMESTAMPTZ,
        cookies_enc BYTEA,
        password_enc BYTEA,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    # Tokens used to last forever. Databases created before that get the column here; rows without an expiry are
    # treated as expired, so those students log in once.
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS token_expires_at TIMESTAMPTZ",
    # Databases created before the password was stored at login get the column here.
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS password_enc BYTEA",
    # Students who turned change notifications on. The watcher signs in with the password saved on the user.
    # Everything here is deleted by turning notifications off. Logging out keeps it, so checks keep running.
    """
    CREATE TABLE IF NOT EXISTS notification_settings (
        user_id BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
        email TEXT,
        fields TEXT[] NOT NULL,
        status TEXT NOT NULL DEFAULT 'ok',
        last_checked_at TIMESTAMPTZ,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    # Passwords used to be kept per notification setting. Copy them to the user and drop the old column. Runs on every
    # start; does nothing once the column is gone.
    """
    DO $$
    BEGIN
        IF EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = current_schema() AND table_name = 'notification_settings' AND column_name = 'password_enc'
        ) THEN
            UPDATE users u SET password_enc = n.password_enc FROM notification_settings n WHERE n.user_id = u.id;
            ALTER TABLE notification_settings DROP COLUMN password_enc;
        END IF;
    END $$
    """,
    # Last seen results of each student, used to spot changes.
    """
    CREATE TABLE IF NOT EXISTS grade_snapshots (
        user_id BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
        data JSONB NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    # The Telegram chat of a student. A chat belongs to one account at a time.
    """
    CREATE TABLE IF NOT EXISTS telegram_chats (
        user_id BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
        chat_id TEXT NOT NULL UNIQUE,
        linked_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    # One-time codes in the link students open from the frontend (t.me/<bot>?start=<code>).
    """
    CREATE TABLE IF NOT EXISTS telegram_link_codes (
        code TEXT PRIMARY KEY,
        user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        expires_at TIMESTAMPTZ NOT NULL
    )
    """,
    # The profile photo a student uploaded to this service (not to the university site). One per user; it is kept across logouts.
    """
    CREATE TABLE IF NOT EXISTS profile_photos (
        user_id BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
        content_type TEXT NOT NULL,
        data BYTEA NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
)


@dataclass(frozen=True)
class User:
    id: int
    site_username: str
    cookies: str | None  # decrypted JSON, None if absent or unreadable


@dataclass(frozen=True)
class Notifications:
    email: str | None
    fields: list[str]  # result fields to watch
    status: str = "ok"  # how the last check went: ok, wrong_password or error
    last_checked_at: datetime | None = None
    telegram_linked: bool = False


@dataclass(frozen=True)
class Subscription:
    user_id: int
    site_username: str
    password: str = field(repr=False)
    notifications: Notifications
    telegram_chat_id: str | None = field(default=None, repr=False)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class UserStore:
    """Users, their API tokens (stored hashed, with an expiry), their site session cookies and site password (both stored encrypted),
    the change notifications of students who turned them on, their Telegram chats, and their profile photos."""

    def __init__(self, database_url: str, secret_key: str):
        self.database_url = database_url
        self.fernet = Fernet(secret_key.encode())
        self._create_schema()

    def _create_schema(self, attempts: int = 15, delay: float = 2.0) -> None:
        """Create tables, waiting for the database to accept connections (it may still be starting)."""
        for attempt in range(1, attempts + 1):
            try:
                with self._connect() as db:
                    for statement in SCHEMA:
                        db.execute(statement)
                return
            except psycopg.OperationalError as exc:
                if attempt == attempts:
                    raise
                log.warning("Database not ready (%s), retry %s/%s", str(exc).strip(), attempt, attempts)
                time.sleep(delay)

    def _connect(self) -> psycopg.Connection:
        # `with` commits (or rolls back) and closes the connection.
        return psycopg.connect(self.database_url)

    def upsert_login(self, site_username: str, cookies: str, password: str) -> str:
        """Create or update the user after a successful site login; returns a fresh API token that lasts TOKEN_DAYS.

        The password is kept encrypted, so change notifications can sign in later without asking for it again.
        """
        token = secrets.token_urlsafe(32)
        expires_at = datetime.now(timezone.utc) + timedelta(days=TOKEN_DAYS)
        encrypted_cookies = self.fernet.encrypt(cookies.encode())
        encrypted_password = self.fernet.encrypt(password.encode())
        with self._connect() as db:
            db.execute(
                "INSERT INTO users (site_username, token_hash, token_expires_at, cookies_enc, password_enc) "
                "VALUES (%s, %s, %s, %s, %s) "
                "ON CONFLICT(site_username) DO UPDATE SET token_hash = excluded.token_hash, "
                "token_expires_at = excluded.token_expires_at, cookies_enc = excluded.cookies_enc, "
                "password_enc = excluded.password_enc",
                (site_username, _hash(token), expires_at, encrypted_cookies, encrypted_password),
            )
            # The login proves this password works, so a wrong-password status from an older one is cleared.
            db.execute(
                "UPDATE notification_settings SET status = 'ok', last_checked_at = NULL, updated_at = now() "
                "WHERE user_id = (SELECT id FROM users WHERE site_username = %s)",
                (site_username,),
            )
        return token

    def get_by_token(self, token: str) -> User | None:
        """The user a token belongs to, or None if the token is unknown or has expired."""
        return self._get_user("token_hash = %s AND token_expires_at > now()", _hash(token))

    def _get_user(self, condition: str, value: object) -> User | None:
        with self._connect() as db:
            row = db.execute(
                f"SELECT id, site_username, cookies_enc FROM users WHERE {condition}", (value,)
            ).fetchone()
        return self._user(row) if row else None

    def _user(self, row: tuple) -> User:
        """A user from (id, site_username, cookies_enc), with the cookies decrypted."""
        cookies = None
        if row[2]:
            try:
                cookies = self.fernet.decrypt(row[2]).decode()
            except InvalidToken:
                log.warning("Stored cookies for user %s could not be decrypted", row[0])
        return User(id=row[0], site_username=row[1], cookies=cookies)

    def save_cookies(self, user_id: int, cookies: str) -> None:
        with self._connect() as db:
            db.execute("UPDATE users SET cookies_enc = %s WHERE id = %s", (self.fernet.encrypt(cookies.encode()), user_id))

    def end_session(self, user_id: int) -> None:
        """Log out: the API token and the site session cookies stop working. Everything else stays, so change notifications,
        the saved password, the Telegram link and the profile photo keep working."""
        # The token is replaced by a random one nobody has, so the old token can no longer match.
        with self._connect() as db:
            db.execute(
                "UPDATE users SET token_hash = %s, token_expires_at = NULL, cookies_enc = NULL WHERE id = %s",
                (_hash(secrets.token_urlsafe(32)), user_id),
            )

    def save_password(self, user_id: int, password: str) -> None:
        """Replace the saved password after a site password change. Clears a wrong-password status, so the watcher signs
        in again."""
        encrypted = self.fernet.encrypt(password.encode())
        with self._connect() as db:
            db.execute("UPDATE users SET password_enc = %s WHERE id = %s", (encrypted, user_id))
            db.execute(
                "UPDATE notification_settings SET status = 'ok', last_checked_at = NULL, updated_at = now() WHERE user_id = %s",
                (user_id,),
            )

    def saved_password(self, user_id: int) -> str | None:
        """The password saved at login (or by the last change), or None if there is none."""
        with self._connect() as db:
            row = db.execute("SELECT password_enc FROM users WHERE id = %s", (user_id,)).fetchone()
        return self._decrypt_password(user_id, row[0]) if row else None

    def set_notifications(self, user_id: int, settings: Notifications) -> None:
        """Turn notifications on or replace them. Saving them again clears a wrong-password status."""
        with self._connect() as db:
            db.execute(
                "INSERT INTO notification_settings (user_id, email, fields) VALUES (%s, %s, %s) "
                "ON CONFLICT (user_id) DO UPDATE SET email = excluded.email, fields = excluded.fields, "
                "status = 'ok', last_checked_at = NULL, updated_at = now()",
                (user_id, settings.email, settings.fields),
            )

    def _decrypt_password(self, user_id: int, encrypted: bytes | None) -> str | None:
        if encrypted is None:
            return None
        try:
            return self.fernet.decrypt(encrypted).decode()
        except InvalidToken:
            log.warning("Saved password of user %s could not be decrypted", user_id)
            return None

    def get_notifications(self, user_id: int) -> Notifications | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT n.email, n.fields, n.status, n.last_checked_at, t.user_id IS NOT NULL "
                "FROM notification_settings n LEFT JOIN telegram_chats t ON t.user_id = n.user_id WHERE n.user_id = %s",
                (user_id,),
            ).fetchone()
        return Notifications(*row) if row else None

    def record_check(self, user_id: int, status: str) -> None:
        with self._connect() as db:
            db.execute(
                "UPDATE notification_settings SET status = %s, last_checked_at = now() WHERE user_id = %s",
                (status, user_id),
            )

    def delete_notifications(self, user_id: int) -> None:
        """Turn notifications off: removes the email and the saved results. The saved password and the Telegram link stay."""
        with self._connect() as db:
            db.execute("DELETE FROM notification_settings WHERE user_id = %s", (user_id,))
            db.execute("DELETE FROM grade_snapshots WHERE user_id = %s", (user_id,))

    def subscribers(self) -> list[Subscription]:
        """Every student with notifications on, with the password the watcher signs in with and their Telegram chat."""
        with self._connect() as db:
            rows = db.execute(
                "SELECT u.id, u.site_username, u.password_enc, n.email, n.fields, n.status, n.last_checked_at, t.chat_id "
                "FROM notification_settings n JOIN users u ON u.id = n.user_id "
                "LEFT JOIN telegram_chats t ON t.user_id = u.id ORDER BY u.id"
            ).fetchall()
        subscriptions = []
        for user_id, site_username, password_enc, email, fields, status, checked_at, chat_id in rows:
            password = self._decrypt_password(user_id, password_enc)
            if password is None:
                log.warning("User %s has no usable saved password, skipping", user_id)
                continue
            notifications = Notifications(email, fields, status, checked_at, telegram_linked=chat_id is not None)
            subscriptions.append(Subscription(user_id, site_username, password, notifications, chat_id))
        return subscriptions

    def load_snapshot(self, user_id: int) -> dict | None:
        with self._connect() as db:
            row = db.execute("SELECT data FROM grade_snapshots WHERE user_id = %s", (user_id,)).fetchone()
        return row[0] if row else None

    def save_snapshot(self, user_id: int, data: dict) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT INTO grade_snapshots (user_id, data) VALUES (%s, %s) "
                "ON CONFLICT (user_id) DO UPDATE SET data = excluded.data, updated_at = now()",
                (user_id, Jsonb(data)),
            )

    def create_link_code(self, user_id: int) -> tuple[str, datetime]:
        """A new one-time code for connecting a Telegram chat to this account. It replaces any earlier code."""
        code = secrets.token_urlsafe(16)
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=LINK_CODE_MINUTES)
        with self._connect() as db:
            db.execute("DELETE FROM telegram_link_codes WHERE user_id = %s", (user_id,))
            db.execute(
                "INSERT INTO telegram_link_codes (code, user_id, expires_at) VALUES (%s, %s, %s)",
                (code, user_id, expires_at),
            )
        return code, expires_at

    def redeem_link_code(self, code: str) -> int | None:
        """Uses a code once. Returns the account it belongs to, or None if the code is unknown or expired."""
        with self._connect() as db:
            row = db.execute(
                "DELETE FROM telegram_link_codes WHERE code = %s AND expires_at > now() RETURNING user_id", (code,)
            ).fetchone()
        return row[0] if row else None

    def set_telegram_chat(self, user_id: int, chat_id: str) -> None:
        """Connect a Telegram chat to an account. If another account had that chat, it moves here."""
        with self._connect() as db:
            db.execute("DELETE FROM telegram_chats WHERE chat_id = %s", (chat_id,))
            db.execute(
                "INSERT INTO telegram_chats (user_id, chat_id) VALUES (%s, %s) "
                "ON CONFLICT (user_id) DO UPDATE SET chat_id = excluded.chat_id, linked_at = now()",
                (user_id, chat_id),
            )

    def get_telegram_chat(self, user_id: int) -> str | None:
        with self._connect() as db:
            row = db.execute("SELECT chat_id FROM telegram_chats WHERE user_id = %s", (user_id,)).fetchone()
        return row[0] if row else None

    def delete_telegram_chat(self, user_id: int) -> None:
        with self._connect() as db:
            db.execute("DELETE FROM telegram_chats WHERE user_id = %s", (user_id,))

    def save_photo(self, user_id: int, content_type: str, data: bytes) -> None:
        """Set the profile photo, replacing any earlier one."""
        with self._connect() as db:
            db.execute(
                "INSERT INTO profile_photos (user_id, content_type, data) VALUES (%s, %s, %s) "
                "ON CONFLICT (user_id) DO UPDATE SET content_type = excluded.content_type, data = excluded.data, updated_at = now()",
                (user_id, content_type, data),
            )

    def get_photo(self, user_id: int) -> tuple[str, bytes] | None:
        """The profile photo as (content type, bytes), or None if there is none."""
        with self._connect() as db:
            row = db.execute("SELECT content_type, data FROM profile_photos WHERE user_id = %s", (user_id,)).fetchone()
        return (row[0], bytes(row[1])) if row else None

    def delete_photo(self, user_id: int) -> None:
        with self._connect() as db:
            db.execute("DELETE FROM profile_photos WHERE user_id = %s", (user_id,))
