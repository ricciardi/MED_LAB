import hashlib
import hmac
import time
from functools import wraps
from urllib.parse import urlsplit

from flask import abort, current_app, jsonify, request, session
from flask_login import current_user
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from .extensions import db
from .models import RateLimit


def safe_next(target, fallback="/painel"):
    if not target or not target.startswith("/") or target.startswith("//") or "\\" in target:
        return fallback
    parsed = urlsplit(target)
    return target if not parsed.netloc and not parsed.scheme else fallback


def consume_limit(scope, identity, limit, seconds):
    """Database-backed atomic counter; works across Passenger processes."""
    if current_app.config.get("RATELIMIT_DISABLED"):
        return True
    key = hmac.new(str(current_app.secret_key).encode(), f"{scope}:{identity}".encode(), hashlib.sha256).hexdigest()
    bucket = int(time.time()) // seconds
    for _ in range(2):
        result = db.session.execute(update(RateLimit).where(
            RateLimit.key == key, RateLimit.bucket == bucket, RateLimit.hits < limit
        ).values(hits=RateLimit.hits + 1))
        if result.rowcount:
            db.session.commit()
            return True
        if db.session.get(RateLimit, (key, bucket)):
            db.session.rollback()
            return False
        try:
            db.session.add(RateLimit(key=key, bucket=bucket, hits=1))
            db.session.commit()
            return True
        except IntegrityError:
            db.session.rollback()
    return False


def login_rate_allowed(email):
    ip = request.remote_addr or "unknown"
    return (consume_limit("login-ip", ip, 30, 900)
            and consume_limit("login-account", email, 10, 900))


def api_login_required(func):
    @wraps(func)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated:
            return jsonify(error="Sua sessão expirou. Entre novamente para continuar."), 401
        return func(*args, **kwargs)
    return wrapped


def admin_required(func):
    @wraps(func)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin or not session.get("admin_access"):
            abort(404)
        return func(*args, **kwargs)
    return wrapped
