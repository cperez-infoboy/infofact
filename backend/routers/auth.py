"""Router de autenticación: registro + login email/password, sesión en cookie JWT.

Reglas de cookie (convención del proyecto, ver backend/deps.py):
  - HttpOnly, SameSite=Lax, path=/
  - Secure=True solo si settings.app_url empieza con 'https'
  - max_age = 7 días (alinea con TOKEN_TTL_DAYS en deps.py)

profile debe matchear ^[a-z0-9_-]{2,48}$ (clave del workspace + nombre del
container). email y profile son únicos; si colisionan, se devuelve 409.
"""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from backend.config import settings
from backend.database import AsyncSessionLocal
from backend.deps import (
    COOKIE_NAME,
    create_access_token,
    get_current_user,
    hash_password,
    verify_password,
)
from backend.models import User

router = APIRouter()

# Slug alfanumérico estable: clave del workspace y nombre del container.
PROFILE_RE = re.compile(r"^[a-z0-9_-]{2,48}$")


class RegisterBody(BaseModel):
    email: str = Field(..., min_length=3, max_length=254)
    password: str = Field(..., min_length=8, max_length=128)
    profile: str = Field(..., min_length=2, max_length=48)


class LoginBody(BaseModel):
    email: str
    password: str


class UserOut(BaseModel):
    id: int
    email: str
    profile: str


def _set_auth_cookie(response: Response, token: str) -> None:
    """Adjunta el cookie HttpOnly con el JWT según las reglas del proyecto."""
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=60 * 60 * 24 * 7,  # 7 días; alinea con TOKEN_TTL_DAYS
        httponly=True,
        secure=settings.app_url.startswith("https"),
        samesite="lax",
        path="/",
    )


def _clear_auth_cookie(response: Response) -> None:
    """Borra el cookie con los mismos flags usados al setearlo."""
    response.delete_cookie(
        key=COOKIE_NAME,
        path="/",
        secure=settings.app_url.startswith("https"),
        httponly=True,
        samesite="lax",
    )


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def register(body: RegisterBody, response: Response) -> UserOut:
    if not PROFILE_RE.match(body.profile):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid_profile")

    async with AsyncSessionLocal() as db:
        # Pre-chequeo de unicidad de email + profile para responder 409 limpio
        # en vez de integrity error crudo. La carrera sigue siendo rechazada por
        # las constraints UNIQUE a nivel DB.
        existing = (
            await db.execute(
                select(User).where(
                    (User.email == body.email) | (User.profile == body.profile)
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise HTTPException(status.HTTP_409_CONFLICT, "email_or_profile_taken")

        user = User(
            email=body.email,
            password_hash=hash_password(body.password),
            profile=body.profile,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)

    token = create_access_token(user.id)
    _set_auth_cookie(response, token)
    return UserOut(id=user.id, email=user.email, profile=user.profile)


@router.post("/login", response_model=UserOut)
async def login(body: LoginBody, response: Response) -> UserOut:
    async with AsyncSessionLocal() as db:
        user = (
            await db.execute(select(User).where(User.email == body.email))
        ).scalar_one_or_none()

    # Mismo mensaje sea usuario inexistente o password inválida: no filtrar.
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid_credentials")

    token = create_access_token(user.id)
    _set_auth_cookie(response, token)
    return UserOut(id=user.id, email=user.email, profile=user.profile)


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)) -> UserOut:
    return UserOut(id=user.id, email=user.email, profile=user.profile)


@router.post("/logout")
async def logout(response: Response) -> dict[str, bool]:
    _clear_auth_cookie(response)
    return {"ok": True}
