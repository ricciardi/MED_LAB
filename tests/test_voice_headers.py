"""Origin isolation and existing security policies, without network or real data."""
from html.parser import HTMLParser

import pytest

import medlab


CSP = (
    "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; worker-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self' https://huggingface.co https://*.hf.co https://*.huggingface.co; media-src 'self' blob:; "
    "frame-ancestors 'none'; base-uri 'self'; form-action 'self'; object-src 'none'"
)


def assert_headers(response, enabled, *, microphone=False):
    assert response.headers.get("Cross-Origin-Opener-Policy") == ("same-origin" if enabled else None)
    assert response.headers.get("Cross-Origin-Embedder-Policy") == ("require-corp" if enabled else None)
    assert response.headers["Content-Security-Policy"] == CSP
    assert response.headers["Permissions-Policy"] == f"camera=(), microphone={'(self)' if microphone else '()'}, geolocation=()"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"


@pytest.mark.parametrize("setting,expected", [(None, True), ("true", True), ("false", False)])
def test_isolation_default_and_environment_switch(monkeypatch, setting, expected):
    # Do not read .env, keys, runtime assets, or an existing database.
    monkeypatch.setattr(medlab, "load_dotenv", lambda *_: None)
    if setting is None:
        monkeypatch.delenv("LOCAL_VOICE_ISOLATION", raising=False)
    else:
        monkeypatch.setenv("LOCAL_VOICE_ISOLATION", setting)
    app = medlab.create_app({
        "TESTING": True, "SECRET_KEY": "isolated-header-fixture-secret-with-more-than-32-chars",
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:", "DEEPSEEK_API_KEY": "",
        "CASE_REUSE_SCOPE": "noncommercial", "CASE_ALLOW_CLINICAL_FACTS": False,
        "ADMIN_PATH": "/gestao-teste", "SESSION_COOKIE_SECURE": False,
    })
    assert app.config["LOCAL_VOICE_ISOLATION"] is expected
    response = app.test_client().get("/saude")
    assert response.status_code == 200
    assert_headers(response, expected)


@pytest.mark.parametrize("enabled", [True, False])
def test_pages_redirects_errors_and_voice_api_share_isolation_without_broadening_permissions(app, client, start_session, enabled):
    app.config["LOCAL_VOICE_ISOLATION"] = enabled
    session_id = start_session()
    consultation_url = f"/sessao/{session_id}"
    for url, status in (("/", 200), ("/casos", 200), (consultation_url, 200),
                        (f"{consultation_url}/voz/config", 200), ("/nao-existe", 404)):
        response = client.get(url)
        assert response.status_code == status
        assert_headers(response, enabled, microphone=url == consultation_url)
        assert response.headers.get("Cache-Control") == (None if status == 404 else "no-store")
    client.post("/sair")
    redirected = client.get(consultation_url)
    assert redirected.status_code == 302
    assert_headers(redirected, enabled, microphone=True)
    forbidden = client.get(f"{consultation_url}/voz/config")
    assert forbidden.status_code == 401
    assert_headers(forbidden, enabled)


@pytest.mark.parametrize("enabled", [True, False])
def test_local_worker_wasm_image_and_stylesheet_keep_bytes_mime_and_conditional_headers(app, client, tmp_path, enabled):
    app.config["LOCAL_VOICE_ISOLATION"] = enabled
    app.static_folder = str(tmp_path)
    resources = {
        "voice-local/worker.js": (b"self.onmessage = () => {};", "javascript"),
        "voice-local/runtime.wasm": (b"\x00asm\x01\x00\x00\x00", "application/wasm"),
        "fixture.svg": (b'<svg xmlns="http://www.w3.org/2000/svg"/>', "image/svg+xml"),
        "fixture.css": (b"body { color: black; }", "text/css"),
    }
    for name, (raw, mime) in resources.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        response = client.get(f"/static/{name}")
        assert response.status_code == 200 and response.data == raw
        assert mime in response.mimetype
        assert_headers(response, enabled)
        cached = client.get(f"/static/{name}", headers={"If-None-Match": response.headers["ETag"]})
        assert cached.status_code == 304 and not cached.data
        assert_headers(cached, enabled)


class StylesheetParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.styles = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "link" and attrs.get("rel") == "stylesheet":
            self.styles.append(attrs)


def test_base_styles_load_same_origin_or_explicit_cors_and_hsts_is_preserved(app, client):
    app.config.update(LOCAL_VOICE_ISOLATION=True, SESSION_COOKIE_SECURE=True)
    response = client.get("/")
    parser = StylesheetParser()
    parser.feed(response.text)
    assert parser.styles
    for style in parser.styles:
        if style["href"].startswith(("https://", "http://", "//")):
            assert style.get("crossorigin") == "anonymous"
    assert response.headers["Strict-Transport-Security"] == "max-age=31536000"
    assert_headers(response, True)
