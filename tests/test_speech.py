"""Privacy and authorization boundaries for on-device speech."""
import io

import pytest

from medlab.extensions import db
from medlab.models import Turn
from medlab.speech import SpeechService, voice_options


@pytest.fixture
def voice_assets(tmp_path, app):
    for name in ("whisper-worker.js", "kokoro-worker.js", "vendor/transformers.js",
                 "vendor/transformers-tts.js", "ort-tts/ort-wasm-simd-threaded.jsep.mjs",
                 "ort-tts/ort-wasm-simd-threaded.jsep.wasm",
                 "kokoro-pt.js", "espeak/piper_phonemize.js", "espeak/piper_phonemize.wasm",
                 "espeak/piper_phonemize.data", "ort/ort-wasm-simd-threaded.asyncify.mjs",
                 "ort/ort-wasm-simd-threaded.asyncify.wasm",
                 "ort/ort-wasm-simd-threaded.jsep.mjs", "ort/ort-wasm-simd-threaded.jsep.wasm"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixture")
    app.config.update(LOCAL_VOICE_ASSET_ROOT=str(tmp_path), LOCAL_VOICE_ENABLED=True)
    return tmp_path


def test_known_gender_is_locked_but_unknown_allows_both(case_data):
    female = voice_options(case_data)
    assert female["gender_locked"] and {v["gender"] for v in female["voices"]} == {"female"}
    case_data["patientProfile"]["gender"] = "Masculino"
    assert voice_options(case_data)["default_voice"] == "pm_alex"
    case_data["patientProfile"]["gender"] = "Menina"
    assert voice_options(case_data)["gender"] == "female"
    case_data["patientProfile"]["gender"] = "Não informado na fonte."
    unknown = voice_options(case_data)
    assert not unknown["gender_locked"]
    assert {v["gender"] for v in unknown["voices"]} == {"female", "male"}


def test_voice_requires_installed_assets_but_no_cloud_credentials(app, voice_assets, case_data):
    config = SpeechService(app.config).configuration(case_data)
    assert config["available"] and config["provider"] == "local"
    assert app.config["DEMO_MODE"] and not app.config["DEEPSEEK_API_KEY"]
    assert config["models_asr"]["worker_url"].startswith("/static/")
    (voice_assets / "kokoro-worker.js").unlink()
    assert not SpeechService(app.config).available


def test_operator_can_disable_voice(app, voice_assets):
    app.config["LOCAL_VOICE_ENABLED"] = False
    assert not SpeechService(app.config).available


def test_voice_config_is_private_and_has_no_credentials(client, start_session, login, voice_assets):
    sid = start_session()
    config = client.get(f"/sessao/{sid}/voz/config")
    assert config.status_code == 200 and config.json["available"] is True
    assert "key" not in config.get_data(as_text=True).lower()
    assert config.headers["Cache-Control"] == "no-store"
    client.post("/sair")
    assert client.get(f"/sessao/{sid}/voz/config").status_code == 401
    login("colega@example.invalid")
    assert client.get(f"/sessao/{sid}/voz/config").status_code == 404
    assert client.post(f"/sessao/{sid}/voz/falar", json={"turn_id": 1, "voice_id": "pf_dora"}).status_code == 404


def test_speech_only_reads_saved_turn_from_owned_consultation(app, client, start_session, voice_assets):
    sid = start_session()
    other_sid = start_session(2)
    with app.app_context():
        first = Turn(consultation_id=sid, question="Quando?", answer="Resposta salva.", intent="Conversa geral", request_id="speech-test-1")
        second = Turn(consultation_id=other_sid, question="Quando?", answer="Outro caso.", intent="Conversa geral", request_id="speech-test-2")
        db.session.add_all([first, second])
        db.session.commit()
        tid, other_tid = first.id, second.id
    endpoint = f"/sessao/{sid}/voz/falar"
    assert client.post(endpoint, json={"turn_id": other_tid, "voice_id": "pf_dora"}).status_code == 404
    assert client.post(endpoint, json={"turn_id": tid, "voice_id": "pm_alex"}).status_code == 400
    response = client.post(endpoint, json={"turn_id": tid, "voice_id": "pf_dora", "text": "Conteúdo injetado"})
    assert response.status_code == 200 and response.is_json
    assert response.headers["Cache-Control"] == "no-store"
    assert response.json == {"text": "Resposta salva.", "voice_id": "pf_dora"}
    app.config["WTF_CSRF_ENABLED"] = True
    assert client.post(endpoint, json={"turn_id": tid, "voice_id": "pf_dora"}).status_code == 400


def test_audio_upload_endpoint_does_not_exist(app, client, start_session):
    sid = start_session()
    response = client.post(f"/sessao/{sid}/voz/transcrever", data={"audio": (io.BytesIO(b"audio"), "test.wav")})
    assert response.status_code == 404
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Turn.id))) == 0
    assert "web.transcribe_audio" not in app.view_functions


def test_microphone_permission_only_on_consultation(client, start_session):
    sid = start_session()
    page = client.get(f"/sessao/{sid}")
    assert "microphone=(self)" in page.headers["Permissions-Policy"]
    assert "microphone=()" in client.get("/casos").headers["Permissions-Policy"]
    csp = page.headers["Content-Security-Policy"]
    assert "'wasm-unsafe-eval'" in csp and "worker-src 'self'" in csp
    assert "'unsafe-eval'" not in csp
