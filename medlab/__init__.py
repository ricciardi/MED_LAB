import os
import hmac
import re
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request, session
from flask_wtf.csrf import CSRFError
from sqlalchemy import event
from sqlalchemy.engine import Engine

from .extensions import csrf, db, login_manager


def env_bool(name, default=False):
    return os.getenv(name, str(default)).lower() in {"1", "true", "yes", "sim"}


@event.listens_for(Engine, "connect")
def configure_sqlite(connection, _):
    if connection.__class__.__module__.startswith("sqlite3"):
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=15000")
        cursor.close()


def create_app(config=None):
    root = Path(__file__).resolve().parent.parent
    load_dotenv(root / ".env")
    app = Flask(__name__, instance_path=str(root / "instance"))
    app.config.update(
        SECRET_KEY=os.getenv("SECRET_KEY", ""),
        SQLALCHEMY_DATABASE_URI=os.getenv("DATABASE_URL", "sqlite:///medlab.db"),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        SQLALCHEMY_ENGINE_OPTIONS={"pool_pre_ping": True},
        DEEPSEEK_API_KEY=os.getenv("DEEPSEEK_API_KEY", ""),
        AI_BASE_URL=os.getenv("AI_BASE_URL", "https://api.deepseek.com"),
        AI_MODEL=os.getenv("AI_MODEL", "deepseek-flash"),
        AI_TIMEOUT=int(os.getenv("AI_TIMEOUT", "40")),
        DEMO_MODE=env_bool("DEMO_MODE"),
        ADMIN_PATH=os.getenv("ADMIN_PATH", "/gestao-medlab").rstrip("/"),
        MAX_SESSION_TURNS=40,
        MAX_DAILY_AI_REQUESTS=int(os.getenv("MAX_DAILY_AI_REQUESTS", "120")),
        MAX_CONTENT_LENGTH=128 * 1024,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=env_bool("SESSION_COOKIE_SECURE", True),
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
        WTF_CSRF_TIME_LIMIT=28800,
        PREFERRED_URL_SCHEME="https",
    )
    if config:
        app.config.update(config)
    if len(app.config["SECRET_KEY"]) < 32:
        raise RuntimeError("Configure SECRET_KEY com pelo menos 32 caracteres aleatórios no ambiente.")
    prefix = app.config["ADMIN_PATH"]
    if not re.fullmatch(r"/[a-z0-9][a-z0-9/_-]{3,80}", prefix) or prefix in {"/painel", "/casos", "/entrar", "/cadastro", "/historico", "/sessao", "/static", "/privacidade", "/saude"}:
        raise RuntimeError("ADMIN_PATH deve ser um endereço exclusivo, por exemplo /gestao-medlab.")
    Path(app.instance_path).mkdir(mode=0o700, parents=True, exist_ok=True)
    db.init_app(app)
    csrf.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "web.login"
    login_manager.login_message = "Entre na sua conta para continuar."
    login_manager.login_message_category = "info"
    from .models import User

    @login_manager.user_loader
    def load_user(user_id):
        try:
            identifier, _ = user_id.split(":", 1)
            user = db.session.get(User, int(identifier))
            return user if user and user.is_active and hmac.compare_digest(user_id, user.get_id()) else None
        except (ValueError, TypeError):
            return None

    from .web import bp
    from .admin import bp as admin_bp
    from .cli import register_commands
    app.register_blueprint(bp)
    app.register_blueprint(admin_bp, url_prefix=prefix)
    register_commands(app)

    @app.context_processor
    def shared_context():
        return {"ai_available": bool(app.config["DEEPSEEK_API_KEY"]) or app.config["DEMO_MODE"],
                "demo_mode": app.config["DEMO_MODE"], "brand_name": "MED LAB"}

    @app.template_filter("date_br")
    def date_br(value, fmt="%d/%m/%Y"):
        return value.strftime(fmt) if value else "—"

    @app.before_request
    def session_lifetime():
        session.permanent = True

    @app.after_request
    def secure_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; "
            "frame-ancestors 'none'; base-uri 'self'; form-action 'self'; object-src 'none'"
        )
        if request.endpoint and not request.endpoint.startswith("static"):
            response.headers["Cache-Control"] = "no-store"
        if app.config["SESSION_COOKIE_SECURE"]:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response

    @app.errorhandler(CSRFError)
    def csrf_error(_):
        message = "Esta página expirou. Atualize-a e tente novamente."
        if request.is_json:
            return jsonify(error=message), 400
        return render_template("error.html", code=400, message=message), 400

    @app.errorhandler(400)
    @app.errorhandler(404)
    @app.errorhandler(403)
    @app.errorhandler(429)
    @app.errorhandler(413)
    @app.errorhandler(500)
    def http_error(error):
        code = error.code
        messages = {400: "Não foi possível processar estes dados. Confira o formulário.",
                    404: "Não encontramos esta página.", 403: "Você não tem acesso a esta página.",
                    429: "Muitas tentativas. Aguarde alguns minutos e tente novamente.",
                    413: "O conteúdo enviado excede o limite permitido.",
                    500: "Não foi possível concluir agora. Tente novamente em instantes."}
        if request.is_json:
            return jsonify(error=messages[code]), code
        return render_template("error.html", code=code, message=messages[code]), code

    return app
