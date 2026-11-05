"""Authentication (JWT, PBKDF2 password hashes), role-based access, rate limiting and security headers.

Roles (ascending privilege): VIEWER < OPERATOR < METALLURGIST < PROCESS_ENGINEER < ADMIN.
Service identity: SERVICE_INGEST (edge gateway pushing read-only telemetry; cannot call any
other endpoint). All checks are enforced server-side. For real deployments integrate an OIDC
provider (see docs/OT_SECURITY.md); the built-in user store is intended for demo/pilot use.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time
import uuid
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import OAuth2PasswordBearer
from starlette.middleware.base import BaseHTTPMiddleware

from .config import get_settings

ROLES = ["VIEWER", "OPERATOR", "METALLURGIST", "PROCESS_ENGINEER", "ADMIN"]
RANK = {r: i for i, r in enumerate(ROLES)}

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/token", auto_error=False)


def hash_password(pw: str, salt: Optional[bytes] = None, iterations: int = 210_000) -> str:
    salt = salt or secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${dk.hex()}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        algo, it, salt, dk = stored.split("$")
        calc = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), int(it))
        return hmac.compare_digest(calc.hex(), dk)
    except Exception:
        return False


def create_token(sub: str, role: str, minutes: Optional[int] = None) -> str:
    s = get_settings()
    now = datetime.now(timezone.utc)
    payload = {"sub": sub, "role": role, "iat": int(now.timestamp()),
               "exp": int((now + timedelta(minutes=minutes or s.jwt_expire_min)).timestamp()), "jti": uuid.uuid4().hex}
    return jwt.encode(payload, s.jwt_secret, algorithm="HS256")


def decode_token(token: str) -> dict:
    s = get_settings()
    return jwt.decode(token, s.jwt_secret, algorithms=["HS256"])


class Principal:
    def __init__(self, sub: str, role: str, anonymous: bool = False):
        self.sub, self.role, self.anonymous = sub, role, anonymous

    def has(self, role: str) -> bool:
        if self.role == "SERVICE_INGEST":
            return role == "SERVICE_INGEST"
        return RANK.get(self.role, -1) >= RANK.get(role, 99)

    def as_dict(self):
        return {"sub": self.sub, "role": self.role, "anonymous": self.anonymous}


async def current_principal(request: Request, token: Optional[str] = Depends(oauth2_scheme)) -> Principal:
    s = get_settings()
    if token:
        try:
            p = decode_token(token)
            return Principal(p["sub"], p["role"])
        except jwt.PyJWTError:
            raise HTTPException(401, "Invalid or expired token", headers={"WWW-Authenticate": "Bearer"})
    if s.allow_anonymous_viewer:
        return Principal("anonymous", "VIEWER", anonymous=True)
    raise HTTPException(401, "Authentication required", headers={"WWW-Authenticate": "Bearer"})


def require(role: str):
    async def dep(p: Principal = Depends(current_principal)) -> Principal:
        if not p.has(role):
            raise HTTPException(403, f"Requires role {role} (you are {p.role})")
        return p
    dep.required_role = role          # read by scripts/gen_api_reference.py
    return dep


class RateLimiter(BaseHTTPMiddleware):
    """Per-client sliding-window limiter (in-memory; use a gateway/WAF in production)."""

    def __init__(self, app, per_min: int):
        super().__init__(app)
        self.per_min = per_min
        self.hits: Dict[str, deque] = defaultdict(deque)

    async def dispatch(self, request, call_next):
        if request.url.path in ("/health", "/ready"):
            return await call_next(request)
        key = request.client.host if request.client else "unknown"
        now = time.monotonic()
        dq = self.hits[key]
        while dq and now - dq[0] > 60.0:
            dq.popleft()
        if len(dq) >= self.per_min:
            from starlette.responses import JSONResponse
            return JSONResponse({"error": {"code": "RATE_LIMITED", "message": "Too many requests"}}, status_code=429,
                                headers={"Retry-After": "10"})
        dq.append(now)
        return await call_next(request)


class SecurityHeaders(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:16]
        request.state.request_id = rid
        resp = await call_next(request)
        resp.headers["X-Request-ID"] = rid
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        resp.headers["Cache-Control"] = resp.headers.get("Cache-Control", "no-store")
        if get_settings().is_production:
            resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return resp
