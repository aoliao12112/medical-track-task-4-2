"""Minimal local authentication primitives for the competition prototype."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import base64
import hashlib
import hmac
import os
import secrets
import threading


PASSWORD_SCHEME = "pbkdf2_sha256"
PASSWORD_ITERATIONS = 260_000


def hash_password(password: str, *, iterations: int = PASSWORD_ITERATIONS) -> str:
    if len(password) < 8:
        raise ValueError("密码至少需要 8 个字符")
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return "$".join(
        (
            PASSWORD_SCHEME,
            str(iterations),
            base64.urlsafe_b64encode(salt).decode("ascii"),
            base64.urlsafe_b64encode(digest).decode("ascii"),
        )
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, iteration_text, salt_text, digest_text = encoded.split("$", 3)
        if scheme != PASSWORD_SCHEME:
            return False
        iterations = int(iteration_text)
        salt = base64.urlsafe_b64decode(salt_text.encode("ascii"))
        expected = base64.urlsafe_b64decode(digest_text.encode("ascii"))
    except (ValueError, TypeError):
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(actual, expected)


@dataclass(frozen=True)
class AuthSession:
    user_id: str
    expires_at: datetime


class SessionStore:
    """Process-local sessions suitable for the single-process local prototype."""

    def __init__(self, ttl_hours: int = 12):
        self.ttl = timedelta(hours=ttl_hours)
        self._sessions: dict[str, AuthSession] = {}
        self._lock = threading.Lock()

    def create(self, user_id: str) -> str:
        token = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)
        with self._lock:
            self._purge(now)
            self._sessions[token] = AuthSession(user_id=user_id, expires_at=now + self.ttl)
        return token

    def resolve(self, token: str | None) -> str | None:
        if not token:
            return None
        now = datetime.now(timezone.utc)
        with self._lock:
            self._purge(now)
            session = self._sessions.get(token)
            return session.user_id if session else None

    def revoke(self, token: str | None) -> None:
        if not token:
            return
        with self._lock:
            self._sessions.pop(token, None)

    def clear(self) -> None:
        with self._lock:
            self._sessions.clear()

    def _purge(self, now: datetime) -> None:
        expired = [token for token, item in self._sessions.items() if item.expires_at <= now]
        for token in expired:
            self._sessions.pop(token, None)
