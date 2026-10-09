import hashlib
import logging
import secrets
import time
from dataclasses import dataclass

import psycopg
from cryptography.fernet import Fernet, InvalidToken

log = logging.getLogger(__name__)

SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS users (
        id BIGSERIAL PRIMARY KEY,
        site_username TEXT NOT NULL UNIQUE,
        token_hash TEXT NOT NULL UNIQUE,
        cookies_enc BYTEA,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
)


@dataclass(frozen=True)
class User:
    id: int
    site_username: str
    cookies: str | None  # decrypted JSON, None if absent or unreadable


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class UserStore:
    """Users, their API tokens (stored hashed) and their site session cookies (stored encrypted)."""

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

    def upsert_login(self, site_username: str, cookies: str) -> str:
        """Create or update the user after a successful site login; returns a fresh API token."""
        token = secrets.token_urlsafe(32)
        encrypted = self.fernet.encrypt(cookies.encode())
        with self._connect() as db:
            db.execute(
                "INSERT INTO users (site_username, token_hash, cookies_enc) VALUES (%s, %s, %s) "
                "ON CONFLICT(site_username) DO UPDATE SET token_hash = excluded.token_hash, cookies_enc = excluded.cookies_enc",
                (site_username, _hash(token), encrypted),
            )
        return token

    def get_by_token(self, token: str) -> User | None:
        return self._get_user("token_hash = %s", _hash(token))

    def _get_user(self, condition: str, value: object) -> User | None:
        with self._connect() as db:
            row = db.execute(
                f"SELECT id, site_username, cookies_enc FROM users WHERE {condition}", (value,)
            ).fetchone()
        if row is None:
            return None
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

    def delete(self, user_id: int) -> None:
        with self._connect() as db:
            db.execute("DELETE FROM users WHERE id = %s", (user_id,))
