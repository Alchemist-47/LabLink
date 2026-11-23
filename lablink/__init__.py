"""LabLink: GenAI-powered campus laboratory resource sharing."""

from __future__ import annotations

import logging
import os
import secrets
from collections.abc import Callable
from datetime import datetime
from zoneinfo import ZoneInfo

from flask import Flask, render_template
from werkzeug.middleware.proxy_fix import ProxyFix

from .ai import LabAssistant
from .repository import MemoryRepository, Repository
from .security import RateLimiter, init_security
from .seed import seed_if_empty
from .services import LabService

CAMPUS_TZ = ZoneInfo(os.environ.get("CAMPUS_TZ", "Asia/Kolkata"))
log = logging.getLogger(__name__)


def campus_now() -> datetime:
    """Current campus time as a naive datetime (all stored times are campus-local)."""
    return datetime.now(CAMPUS_TZ).replace(tzinfo=None, second=0, microsecond=0)


def _build_repository() -> Repository:
    if os.environ.get("LABLINK_STORAGE", "memory").lower() == "firestore":
        from .firestore_repo import FirestoreRepository

        return FirestoreRepository(
            project=os.environ.get("GOOGLE_CLOUD_PROJECT"),
            database=os.environ.get("FIRESTORE_DATABASE"),
        )
    return MemoryRepository()


def create_app(
    repo: Repository | None = None,
    assistant: LabAssistant | None = None,
    clock: Callable[[], datetime] = campus_now,
    config: dict | None = None,
) -> Flask:
    logging.basicConfig(level=logging.INFO)
    app = Flask(__name__)

    secret = os.environ.get("SECRET_KEY")
    if not secret:
        log.warning("SECRET_KEY not set; using a random key (sessions reset on restart)")
        secret = secrets.token_hex(32)
    on_cloud_run = bool(os.environ.get("K_SERVICE"))
    app.config.update(
        SECRET_KEY=secret,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=on_cloud_run,
        MAX_CONTENT_LENGTH=32 * 1024,  # forms only; reject large bodies early
        AI_RATE_LIMIT=int(os.environ.get("AI_RATE_LIMIT", "20")),
    )
    if config:
        app.config.update(config)
    if on_cloud_run:
        # Trust exactly one proxy hop (Google's front end) for client IP and https scheme.
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

    repo = repo or _build_repository()
    assistant = assistant or LabAssistant.from_env(
        api_key=os.environ.get("GEMINI_API_KEY"),
        project=os.environ.get("GOOGLE_CLOUD_PROJECT") if os.environ.get("GEMINI_USE_VERTEX") == "1" else None,
        location=os.environ.get("GOOGLE_CLOUD_LOCATION", "global"),
        model=os.environ.get("GEMINI_MODEL", "gemini-flash-latest"),
    )
    if seed_if_empty(repo, clock()):
        log.info("Seeded demo campus data")

    app.extensions["lablink"] = LabService(repo, assistant, clock)
    app.extensions["ai_limiter"] = RateLimiter(app.config["AI_RATE_LIMIT"], 60)

    init_security(app)

    from .routes import bp

    app.register_blueprint(bp)

    @app.errorhandler(400)
    @app.errorhandler(403)
    @app.errorhandler(404)
    @app.errorhandler(405)
    @app.errorhandler(413)
    @app.errorhandler(429)
    def _http_error(err):
        return render_template("error.html", error=err), err.code

    @app.errorhandler(500)
    def _server_error(err):
        log.exception("Unhandled error")
        return render_template("error.html", error=None), 500

    return app
