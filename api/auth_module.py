"""
Модуль авторизации — Auth0 + RS256 JWT верификация
Заменяет самописную JWT-аутентификацию с passlib/bcrypt.

Зависимости (добавить в requirements.txt):
    python-jose[cryptography]
    httpx

Убрать из requirements.txt:
    passlib[bcrypt]
    bcrypt

Переменные окружения (.env):
    AUTH0_DOMAIN    = your-tenant.auth0.com
    AUTH0_AUDIENCE  = https://grass-crm/api
"""

import os
from functools import lru_cache
from typing import Optional

import httpx
from jose import jwt, JWTError
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

# ── Настройки ─────────────────────────────────────────────────
AUTH0_DOMAIN   = os.getenv("AUTH0_DOMAIN", "YOUR_TENANT.auth0.com")
AUTH0_AUDIENCE = os.getenv("AUTH0_AUDIENCE", "https://grass-crm/api")

# Кастомный claim с ролью, который добавляет Post-Login Action в Auth0.
# Имя клейма обязано быть URL-образным (требование Auth0).
ROLE_CLAIM = "https://grass-crm/role"

bearer = HTTPBearer(auto_error=False)


# ── JWKS-кеш ──────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _fetch_jwks() -> dict:
    """
    Загружает публичные ключи Auth0 один раз и кеширует их
    на время жизни процесса.

    Для принудительного обновления (например, после ротации ключей):
        _fetch_jwks.cache_clear()
    """
    url = f"https://{AUTH0_DOMAIN}/.well-known/jwks.json"
    try:
        resp = httpx.get(url, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPError as e:
        raise RuntimeError(f"Не удалось загрузить JWKS с Auth0: {e}") from e


def _get_signing_key(token: str) -> dict:
    """
    Извлекает из JWKS публичный ключ, соответствующий kid в заголовке токена.
    Если ключ не найден — сбрасывает кеш и пробует ещё раз
    (на случай ротации ключей Auth0).
    """
    unverified_header = jwt.get_unverified_header(token)
    kid = unverified_header.get("kid")

    for attempt in range(2):
        jwks = _fetch_jwks()
        for key in jwks.get("keys", []):
            if key.get("kid") == kid:
                return key
        # Ключ не найден — возможно, Auth0 ротировал ключи. Сбросить кеш.
        if attempt == 0:
            _fetch_jwks.cache_clear()

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Подходящий публичный ключ не найден. Попробуйте войти снова.",
        headers={"WWW-Authenticate": "Bearer"},
    )


# ── Верификация токена ─────────────────────────────────────────

def verify_token(token: str) -> dict:
    """
    Верифицирует Auth0 access token (RS256) и возвращает payload.

    Проверяет:
    - подпись (через публичный ключ из JWKS)
    - audience (AUTH0_AUDIENCE)
    - issuer (https://{AUTH0_DOMAIN}/)
    - срок действия (exp)
    """
    signing_key = _get_signing_key(token)
    try:
        payload = jwt.decode(
            token,
            signing_key,
            algorithms=["RS256"],
            audience=AUTH0_AUDIENCE,
            issuer=f"https://{AUTH0_DOMAIN}/",
        )
        return payload
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Токен недействителен или истёк: {exc}",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


# ── FastAPI зависимости ────────────────────────────────────────

def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer),
) -> dict:
    """
    Зависимость FastAPI. Извлекает и верифицирует Bearer-токен.

    Использование (без изменений по сравнению со старым кодом):
        @app.get("/api/deals")
        def get_deals(user: dict = Depends(get_current_user)):
            ...

    Возвращает словарь:
        {
            "username": "oauth2|yandex|1234567",   # sub из токена
            "email":    "ivanov@yandex.ru",
            "role":     "admin" | "manager" | "user"
        }
    """
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Необходима авторизация. Войдите через Яндекс.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = verify_token(credentials.credentials)

    return {
        "username": payload.get("sub", ""),
        "email":    payload.get("email", ""),
        "role":     payload.get(ROLE_CLAIM, "user"),
    }


def require_admin(current_user: dict = Depends(get_current_user)) -> dict:
    """
    Зависимость FastAPI. Пропускает только пользователей с ролью admin.

    Использование (без изменений):
        @app.delete("/api/users/{id}")
        def delete_user(user: dict = Depends(require_admin)):
            ...
    """
    if current_user["role"] != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Требуется роль admin.",
        )
    return current_user
