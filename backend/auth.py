"""Server-side sessions and the warehouse manager permission."""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import secrets

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from backend import database, models

COOKIE = "welderp_session"
SESSION_SECONDS = 8 * 60 * 60


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def token_hash(token):
    return sha256(token.encode()).hexdigest()


def optional_user(request: Request, db: Session = Depends(database.get_db)):
    token = request.cookies.get(COOKIE)
    if not token or len(token) > 200:
        return None
    return db.scalar(select(models.User).join(models.UserSession,
        models.UserSession.user_id == models.User.id).where(
            models.UserSession.token_hash == token_hash(token), models.UserSession.expires_at > now()))


def require_user(user=Depends(optional_user)):
    if user is None:
        raise HTTPException(status_code=401, detail="Zaloguj się, aby korzystać z aplikacji")
    return user


def manager(user):
    if user is None:
        raise HTTPException(status_code=401, detail="Zaloguj się jako kierownik")
    if user.role != "manager":
        raise HTTPException(status_code=403, detail="Magazyn i planowanie materiałów są dostępne dla kierownika")
    return user


def require_manager(user=Depends(require_user)):
    return manager(user)


def lock_registration(db):
    # Serialize first-account bootstrap; a caller cannot choose their own role.
    if db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(19372501)"))
    elif db.bind.dialect.name == "sqlite":
        db.execute(text("BEGIN IMMEDIATE"))


def start_session(db, user, response, request):
    db.query(models.UserSession).filter(models.UserSession.expires_at <= now()).delete()
    old_token = request.cookies.get(COOKIE)
    if old_token:
        db.query(models.UserSession).filter(models.UserSession.token_hash == token_hash(old_token)).delete()
    token = secrets.token_urlsafe(32)
    db.add(models.UserSession(token_hash=token_hash(token), user_id=user.id,
                              expires_at=now() + timedelta(seconds=SESSION_SECONDS)))
    db.commit()
    response.set_cookie(COOKIE, token, max_age=SESSION_SECONDS, httponly=True,
                        secure=request.url.scheme == "https", samesite="lax")
    # Kept for the existing UI; this flag never grants warehouse access.
    response.set_cookie("logged_in", "true", max_age=SESSION_SECONDS, httponly=True, samesite="lax")


def public_user(user):
    return {"id": user.id, "username": user.username, "full_name": user.full_name, "role": user.role}
