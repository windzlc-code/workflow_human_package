import base64
import hashlib
import hmac
import secrets
import time
from typing import Any

from fastapi import Cookie, HTTPException

from .db import db

SESSION_COOKIE = "session_token"


def _now() -> int:
    return int(time.time())


def _pbkdf2_hash(password: str, *, salt: bytes, iterations: int = 200_000) -> str:
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(iterations))
    return base64.b64encode(dk).decode("utf-8")


def hash_password(password: str) -> str:
    pwd = str(password or "")
    if len(pwd) < 6:
        raise ValueError("password must be at least 6 characters")
    salt = secrets.token_bytes(16)
    iterations = 200_000
    digest = _pbkdf2_hash(pwd, salt=salt, iterations=iterations)
    return f"pbkdf2_sha256${iterations}${base64.b64encode(salt).decode('utf-8')}${digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, it_text, salt_b64, digest_b64 = str(stored or "").split("$", 3)
    except Exception:
        return False
    if algo != "pbkdf2_sha256":
        return False
    try:
        iterations = int(it_text)
        salt = base64.b64decode(salt_b64.encode("utf-8"))
    except Exception:
        return False
    computed = _pbkdf2_hash(str(password or ""), salt=salt, iterations=iterations)
    return hmac.compare_digest(computed, digest_b64)


def create_session(conn, user_id: int, *, ttl_seconds: int = 14 * 24 * 3600) -> str:
    token = secrets.token_urlsafe(32)
    now_ts = _now()
    expires_at = now_ts + int(ttl_seconds)
    conn.execute(
        "INSERT INTO sessions(token, user_id, expires_at, created_at) VALUES (?, ?, ?, ?)",
        (token, int(user_id), int(expires_at), int(now_ts)),
    )
    return token


def delete_session(conn, token: str) -> None:
    conn.execute("DELETE FROM sessions WHERE token = ?", (str(token),))


def _get_user_by_id(conn, user_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM users WHERE id = ?", (int(user_id),)).fetchone()
    if row is None:
        return None
    return dict(row)


def get_current_user(session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE)) -> dict[str, Any]:
    token = str(session_token or "").strip()
    if not token:
        raise HTTPException(status_code=401, detail="not logged in")
    now_ts = _now()
    with db() as conn:
        row = conn.execute(
            "SELECT user_id, expires_at FROM sessions WHERE token = ?",
            (token,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=401, detail="login expired")
        if int(row["expires_at"]) <= now_ts:
            conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
            raise HTTPException(status_code=401, detail="login expired")
        user = _get_user_by_id(conn, int(row["user_id"]))
        if user is None:
            raise HTTPException(status_code=401, detail="login invalid")
        if int(user.get("is_disabled") or 0) == 1:
            raise HTTPException(status_code=403, detail="account disabled")
        return user


def public_admin_user() -> dict[str, Any]:
    return {
        "id": 0,
        "username": "admin",
        "is_admin": 1,
        "is_disabled": 0,
        "balance_cents": 0,
        "created_at": 0,
    }


def require_admin(session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE)) -> dict[str, Any]:
    token = str(session_token or "").strip()
    if token:
        try:
            user = get_current_user(session_token=token)
            if int(user.get("is_admin") or 0) == 1:
                return user
        except HTTPException:
            pass
    return public_admin_user()
