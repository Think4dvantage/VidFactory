"""Password hashing, session tokens, and first-boot bootstrap for the two-user auth model."""

from __future__ import annotations

import hashlib
import logging
import os
import secrets
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from vidfactory.database.models import Session, User

logger = logging.getLogger(__name__)

SESSION_COOKIE = "vf_session"
SESSION_TTL = timedelta(days=30)

_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2**14, 8, 1


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, password_hash: str) -> bool:
    try:
        scheme, salt_hex, digest_hex = password_hash.split("$")
    except ValueError:
        return False
    if scheme != "scrypt":
        return False
    salt = bytes.fromhex(salt_hex)
    expected = bytes.fromhex(digest_hex)
    actual = hashlib.scrypt(password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32)
    return secrets.compare_digest(actual, expected)


def authenticate(db: DbSession, username: str, password: str) -> User | None:
    user = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
    if user is None or not verify_password(password, user.password_hash):
        return None
    return user


def create_session(db: DbSession, user: User) -> str:
    token = secrets.token_urlsafe(32)
    db.add(Session(token=token, user_id=user.id, expires_at=datetime.utcnow() + SESSION_TTL))
    db.commit()
    return token


def get_user_for_token(db: DbSession, token: str) -> User | None:
    sess = db.get(Session, token)
    if sess is None or sess.expires_at < datetime.utcnow():
        return None
    return db.get(User, sess.user_id)


def delete_session(db: DbSession, token: str) -> None:
    sess = db.get(Session, token)
    if sess is not None:
        db.delete(sess)
        db.commit()


def ensure_bootstrap_user(db: DbSession) -> None:
    """Create the first user from env vars, and give them every pre-existing ownerless project.

    No-op once any user exists. Missing env vars on an empty `users` table just logs a warning —
    the app still starts (so `/health` stays green) but is unusable until an operator sets them
    and restarts.
    """
    if db.execute(select(User.id).limit(1)).scalar_one_or_none() is not None:
        return

    username = os.environ.get("VF_BOOTSTRAP_USERNAME")
    password = os.environ.get("VF_BOOTSTRAP_PASSWORD")
    if not username or not password:
        logger.warning(
            "No users exist and VF_BOOTSTRAP_USERNAME/VF_BOOTSTRAP_PASSWORD are not both set — "
            "the app has no way to log in until an operator sets them and restarts."
        )
        return

    user = User(username=username, password_hash=hash_password(password))
    db.add(user)
    db.flush()

    from vidfactory.database.models import Project

    db.execute(
        Project.__table__.update().where(Project.owner_id.is_(None)).values(owner_id=user.id)
    )
    db.commit()
    logger.info("Bootstrap user %r created; existing ownerless projects assigned to them.", username)
