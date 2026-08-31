"""
app/operator_auth.py — Lightweight Single-Password Operator Authentication
===========================================================================
Starkville Korean Church (PCA) — Live Translation System
---------------------------------------------------------
Provides a minimal, secure session-cookie authentication boundary for
privileged operator routes without introducing database or account overhead.

Behavior:
  - SKC_OPERATOR_PASSWORD set     -> Auth ENABLED (Strict cookie session required)
  - SKC_OPERATOR_PASSWORD missing -> Auth DISABLED (Permissive with startup warning)
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time
from typing import Optional

from fastapi import Request, Response
from fastapi.responses import JSONResponse

from app.logger import server_log

COOKIE_NAME = "skc_session"
SESSION_MAX_AGE_SEC = 7 * 86400  # 7 days

# Ephemeral secret fallback if SKC_SESSION_SECRET is not provided in environment
_RUNTIME_SECRET = secrets.token_hex(32)


def get_operator_password() -> str:
    return os.environ.get("SKC_OPERATOR_PASSWORD", "").strip()


def get_session_secret() -> str:
    secret = os.environ.get("SKC_SESSION_SECRET", "").strip()
    return secret if secret else _RUNTIME_SECRET


def is_auth_enabled() -> bool:
    return bool(get_operator_password())


def log_auth_status_on_startup() -> None:
    if is_auth_enabled():
        server_log.info("[AUTH] Operator authentication is ENABLED (Protected with SKC_OPERATOR_PASSWORD)")
    else:
        server_log.warning(
            "[AUTH WARNING] Operator console authentication is DISABLED (SKC_OPERATOR_PASSWORD not set). "
            "Local admin endpoints are unauthenticated."
        )


def verify_password(plain_password: str) -> bool:
    configured = get_operator_password()
    if not configured:
        return False
    return hmac.compare_digest(plain_password.strip().encode("utf-8"), configured.encode("utf-8"))


def create_session_token() -> str:
    ts = str(int(time.time()))
    secret = get_session_secret().encode("utf-8")
    sig = hmac.new(secret, ts.encode("utf-8"), hashlib.sha256).digest()
    sig_b64 = base64.urlsafe_b64encode(sig).decode("utf-8").rstrip("=")
    return f"{ts}.{sig_b64}"


def verify_session_token(token: str) -> bool:
    if not token or "." not in token:
        return False
    try:
        ts_str, sig_b64 = token.split(".", 1)
        created_at = int(ts_str)
        if time.time() - created_at > SESSION_MAX_AGE_SEC:
            return False

        secret = get_session_secret().encode("utf-8")
        expected_sig = hmac.new(secret, ts_str.encode("utf-8"), hashlib.sha256).digest()
        pad = "=" * ((4 - len(sig_b64) % 4) % 4)
        actual_sig = base64.urlsafe_b64decode(sig_b64 + pad)
        return hmac.compare_digest(actual_sig, expected_sig)
    except Exception:
        return False


def is_authenticated(request: Request) -> bool:
    if not is_auth_enabled():
        return True

    cookie_token = request.cookies.get(COOKIE_NAME)
    if cookie_token and verify_session_token(cookie_token):
        return True

    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        bearer_token = auth_header[7:].strip()
        if verify_session_token(bearer_token):
            return True

    return False


def set_auth_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=SESSION_MAX_AGE_SEC,
        httponly=True,
        samesite="strict",
        secure=False,  # Allow local HTTP access on church LAN
        path="/",
    )


def clear_auth_cookie(response: Response) -> None:
    response.delete_cookie(
        key=COOKIE_NAME,
        httponly=True,
        samesite="strict",
        path="/",
    )
