"""Autenticación: hashing, sesiones server-side, CSRF y dependency current_user."""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from datetime import UTC, datetime, timedelta
from typing import Optional

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHashError
from fastapi import Cookie, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import User, UserSession

# Argon2id con parámetros recomendados por OWASP 2024.
_hasher = PasswordHasher(time_cost=3, memory_cost=64 * 1024, parallelism=4)

# Hash precalculado de una contraseña ficticia: lo usamos en el endpoint de
# login cuando el email no existe para igualar el tiempo de respuesta y evitar
# un side-channel de timing.
_DUMMY_HASH = _hasher.hash("dummy-password-for-timing-equalization-only")

SESSION_COOKIE = "mendi_session"
SESSION_DAYS = 30
SESSION_DAYS_REMEMBER = 90


def hash_password(plain: str) -> str:
    return _hasher.hash(plain)


def verify_password(stored_hash: str, plain: str) -> bool:
    try:
        _hasher.verify(stored_hash, plain)
        return True
    except (VerifyMismatchError, InvalidHashError):
        return False


def verify_dummy(plain: str) -> None:
    """Verifica contra un hash dummy para igualar timings cuando no hay usuario."""
    try:
        _hasher.verify(_DUMMY_HASH, plain)
    except (VerifyMismatchError, InvalidHashError):
        pass


def create_session(db: Session, user_id: int, *, remember: bool, request: Request) -> str:
    token = secrets.token_urlsafe(32)
    expires = datetime.now(UTC) + timedelta(
        days=SESSION_DAYS_REMEMBER if remember else SESSION_DAYS
    )
    sess = UserSession(
        id=token,
        user_id=user_id,
        created_at=datetime.now(UTC),
        expires_at=expires,
        ip=(request.client.host if request.client else "")[:64],
        user_agent=(request.headers.get("user-agent", ""))[:256],
        revoked=0,
    )
    db.add(sess)
    db.commit()
    return token


def revoke_session(db: Session, token: str) -> None:
    sess = db.get(UserSession, token)
    if sess:
        sess.revoked = 1
        db.commit()


def revoke_user_sessions(db: Session, user_id: int) -> int:
    """Revoca todas las sesiones del usuario (H10: tras reset de password).

    Devuelve el número de sesiones revocadas. Hace commit para que los
    llamadores offline (scripts/create_user.py) no necesiten gestionarlo.
    """
    rows = db.query(UserSession).filter(UserSession.user_id == user_id).all()
    n = 0
    for sess in rows:
        if not sess.revoked:
            sess.revoked = 1
            n += 1
    db.commit()
    return n


def set_session_cookie(response: Response, token: str, *, remember: bool, request: Request) -> None:
    max_age = (SESSION_DAYS_REMEMBER if remember else SESSION_DAYS) * 24 * 3600
    secure = request.url.scheme == "https" or os.environ.get("MENDI_REQUIRE_HTTPS") == "1"
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        max_age=max_age,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")


def _now() -> datetime:
    return datetime.now(UTC)


def _coerce_aware(dt: datetime) -> datetime:
    """SQLite devuelve datetimes naive; los tratamos como UTC."""
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def resolve_session(db: Session, token: Optional[str]) -> Optional[UserSession]:
    """Devuelve la sesión si es válida y no está revocada/expirada."""
    if not token:
        return None
    sess = db.get(UserSession, token)
    if not sess or sess.revoked:
        return None
    if _coerce_aware(sess.expires_at) < _now():
        return None
    return sess


def get_current_user(
    request: Request,
    db: Session = Depends(get_session),
    mendi_session: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE),
) -> User:
    """Dependency: devuelve el User actual o lanza 401 (HTMX-aware)."""
    sess = resolve_session(db, mendi_session)
    if not sess:
        raise _unauthenticated(request)

    user = db.get(User, sess.user_id)
    if not user or not user.is_active:
        raise _unauthenticated(request)

    user.last_login_at = _now()
    db.commit()
    return user


def _unauthenticated(request: Request) -> HTTPException:
    """Excepción 401 con manejo HTMX-aware.

    HTMX: 401 + HX-Redirect: /login (el cliente hace window.location).
    Navegación normal: 401 con Location — un handler global lo convierte en 303.
    """
    hx = request.headers.get("hx-request", "").lower() == "true"
    if hx:
        return HTTPException(status_code=401, headers={"HX-Redirect": "/login"})
    return HTTPException(status_code=401, headers={"Location": "/login"})


# ===== CSRF =====

def _secret_key() -> bytes:
    """Devuelve la clave secreta para HMAC. Loguea warning si usa default."""
    raw = os.environ.get("MENDI_SECRET_KEY")
    if not raw:
        return b"dev-only-change-me-please-32-chars-minimum-length-x"
    return raw.encode("utf-8")


def csrf_token_for(session_token: str) -> str:
    """Deriva un CSRF token determinista por sesión con HMAC-SHA256."""
    return hmac.new(_secret_key(), session_token.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_csrf(session_token: Optional[str], provided: Optional[str]) -> bool:
    if not session_token or not provided:
        return False
    expected = csrf_token_for(session_token)
    return hmac.compare_digest(expected, provided)


# ===== RBAC =====

def require_role(*allowed: str):
    """Factory: dependency que exige que el rol del usuario esté en `allowed`.

    Por encima de `get_current_user` (que ya verifica sesión válida). Si el
    rol no encaja devolvemos 403 — el usuario está autenticado pero no
    autorizado.
    """
    def _dep(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in allowed:
            raise HTTPException(
                status_code=403,
                detail=f"Acceso denegado: rol '{current_user.role}' no autorizado.",
            )
        return current_user
    return _dep


# Atajos semánticos: nombres legibles en los endpoints.
require_admin = require_role("admin")
require_writer = require_role("admin", "user")


async def require_csrf(
    request: Request,
    mendi_session: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE),
) -> None:
    """Dependency que exige CSRF token válido en headers o form body."""
    if not mendi_session:
        raise HTTPException(status_code=401, headers={"Location": "/login"})

    provided = request.headers.get("x-csrf-token")
    if not provided:
        # Para POST/PATCH/DELETE con formularios, leer el body. Solo si el
        # content-type es form-urlencoded o multipart, intentamos parsear.
        ctype = request.headers.get("content-type", "").lower()
        if "application/x-www-form-urlencoded" in ctype or "multipart/form-data" in ctype:
            try:
                form = await request.form()
                provided = form.get("csrf_token")
            except Exception:  # noqa: BLE001 — parsear el body es best-effort
                provided = None

    if not verify_csrf(mendi_session, provided):
        raise HTTPException(status_code=403, detail="CSRF inválido")
