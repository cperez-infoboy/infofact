"""Auth helpers compartidos: hashing de password, JWT y current-user dependency.

El cookie se llama 'infofact_session' y lleva un JWT HS256 firmado con
settings.secret_key. El payload usa 'sub' = str(user.id) y 'exp' (~7 días).

Reglas de cookie (convención del proyecto):
  - HttpOnly, SameSite=Lax, path=/
  - Secure=True solo si settings.app_url empieza con 'https'

TRAMPA CONOCIDA (CLAUDE.md decisión 7): NUNCA uses Depends(get_current_user)
en el endpoint de streaming del chat. Resuelve el usuario leyendo la cookie
manualmente con decode_access_token + AsyncSessionLocal. Ver backend/routers/chat.py.

Hashing: bcrypt directo (NO passlib). passlib 1.7.x se rompe con bcrypt 4.x
porque su self-test interno (detect_wrap_bug) hashea un secreto >72 bytes y
bcrypt 4.x ya no trunca silenciosamente → ValueError en cada hash. bcrypt es
el límite duro de 72 bytes, por eso truncamos antes de hashear/verificar.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import bcrypt
from fastapi import HTTPException, Request, status
from jose import JWTError, jwt

from backend.config import settings
from backend.database import AsyncSessionLocal
from backend.models import User

COOKIE_NAME = "infofact_session"
ALGORITHM = "HS256"
TOKEN_TTL_DAYS = 7
# bcrypt solo procesa los primeros 72 bytes del password; truncar es seguro
# porque la parte extra jamás entró al hash de todos modos.
_BCRYPT_MAX_BYTES = 72


def hash_password(plain: str) -> str:
    """Hash bcrypt con salt aleatorio. Devuelve el hash como str ascii."""
    raw = plain.encode("utf-8")[:_BCRYPT_MAX_BYTES]
    return bcrypt.hashpw(raw, bcrypt.gensalt()).decode("ascii")


def verify_password(plain: str, hashed: str) -> bool:
    """Check constant-time de password contra hash bcrypt. False si no matchea."""
    raw = plain.encode("utf-8")[:_BCRYPT_MAX_BYTES]
    try:
        return bcrypt.checkpw(raw, hashed.encode("ascii"))
    except (ValueError, TypeError):
        # Hash malformado o tipo inesperado → no es válido.
        return False


def create_access_token(user_id: int | str) -> str:
    """Construye un JWT HS256 con {sub, iat, exp}. Firma con settings.secret_key."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(days=TOKEN_TTL_DAYS)).timestamp()),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict | None:
    """Decodifica el JWT o devuelve None si la firma/expiración falla."""
    try:
        return jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
    except JWTError:
        return None


async def get_current_user(request: Request) -> User:
    """Dependency de FastAPI: lee cookie, decodifica JWT, carga User desde la DB.

    Úsala SOLO en endpoints no-streaming. Para endpoints de streaming (SSE del
    chat) replica la lógica con decode_access_token + AsyncSessionLocal directo
    para evitar el conflicto documentado en CLAUDE.md decisión 7.
    """
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "no_session")
    payload = decode_access_token(token)
    if not payload or "sub" not in payload:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid_session")
    try:
        user_id = int(payload["sub"])
    except (TypeError, ValueError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid_session")
    async with AsyncSessionLocal() as db:
        user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "user_not_found")
    return user
