"""CSRF protection, security headers and a small per-client rate limiter."""

from __future__ import annotations

import hmac
import re
import secrets
import threading
import time
from collections import defaultdict, deque

from flask import Flask, abort, current_app, request, session

CSRF_SESSION_KEY = "_csrf"
EMAIL_PATTERN = re.compile(r"^[a-z0-9._%+-]{1,64}@[a-z0-9.-]{1,190}\.[a-z]{2,24}$")
CSP = (
    "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
    "form-action 'self'; frame-ancestors 'none'; base-uri 'none'; object-src 'none'"
)


def csrf_token() -> str:
    token = session.get(CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        session[CSRF_SESSION_KEY] = token
    return token


def _check_csrf() -> None:
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    expected = session.get(CSRF_SESSION_KEY, "")
    submitted = request.form.get("csrf_token", "")
    if not expected or not hmac.compare_digest(expected, submitted):
        abort(400, description="Your session expired. Reload the page and try again.")


def client_key() -> str:
    """Rate-limit identity: the signed session (many users can share one proxy IP),
    falling back to the client address. A global cap backs this up."""
    return session.get(CSRF_SESSION_KEY) or request.remote_addr or "anon"


def _limit_writes():
    """Every form submission counts against a per-browser and a global limit, so a
    script cannot flood the (in-memory) store. Health checks and reads are free."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    ext = current_app.extensions
    if not (ext["write_limiter"].allow(client_key()) and ext["write_global_limiter"].allow("global")):
        abort(429, description="Too many changes in a short time. Wait a minute and try again.")


def trusted_email() -> str | None:
    """Signed-in email from the identity proxy header (Google IAP format
    'accounts.google.com:user@example.edu'). Only trust this behind a proxy
    that strips the header from client requests, such as Google IAP."""
    raw = request.headers.get(current_app.config["IDENTITY_HEADER"], "")
    email = raw.rsplit(":", 1)[-1].strip().lower()
    return email if EMAIL_PATTERN.fullmatch(email) else None


def _set_headers(response):
    response.headers.setdefault("Content-Security-Policy", CSP)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if request.is_secure:
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    if response.mimetype == "text/html":
        response.headers.setdefault("Cache-Control", "no-store")
    return response


class RateLimiter:
    """Sliding-window limiter for AI-backed endpoints (protects quota and cost)."""

    def __init__(self, limit: int, window_seconds: float) -> None:
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] > self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                return False
            hits.append(now)
            return True


def init_security(app: Flask) -> None:
    app.before_request(_check_csrf)
    app.before_request(_limit_writes)
    app.after_request(_set_headers)
    app.jinja_env.globals["csrf_token"] = csrf_token
