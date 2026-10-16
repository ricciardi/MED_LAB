"""General interview answers use the same saved conversation and voice flow."""
from copy import deepcopy
from unittest.mock import Mock

import pytest

from medlab.ai import AIError, AIService
from medlab.extensions import db
from medlab.models import ClinicalCase, Consultation
from test_speech import voice_assets  # noqa: F401


def test_social_answers_survive_resume_and_feed_the_saved_voice_text(app, client, start_session, monkeypatch, voice_assets):
    identifier = start_session()
    service = AIService({"DEEPSEEK_API_KEY": "isolated-test-key", "DEMO_MODE": False, "AI_TIMEOUT": 12})
    captured = []
    answers = [
        {"answer": "Não sigo uma religião, mas respeito as crenças das outras pessoas.",
         "intents": ["Informações pessoais"], "newDetails": [
             {"topic": "Informações pessoais", "detail": "Não segue uma religião."}]},
        {"answer": "Moro numa casa com minha irmã e terminei o ensino médio.",
         "intents": ["Informações pessoais"], "newDetails": [
             {"topic": "Informações pessoais", "detail": "Mora numa casa com a irmã."},
             {"topic": "Informações pessoais", "detail": "Concluiu o ensino médio."}]},
        {"answer": "Isso, não tenho religião. Não há uma preferência religiosa para o atendimento.",
         "intents": ["Informações pessoais"], "newDetails": []},
    ]
    def request(system, data, max_tokens):
        captured.append(deepcopy(data))
        return deepcopy(answers[len(captured) - 1])
    service._request = Mock(side_effect=request)
    monkeypatch.setattr("medlab.web.get_ai_service", lambda: service)
    with app.app_context():
        training = db.session.get(Consultation, identifier)
        original = deepcopy(training.case_snapshot)
        case_id = training.case_id
    for i, question in enumerate(("Tem alguma religião ou crença?", "Com quem você mora e até que ano estudou?", "Então você não segue religião, certo?")):
        payload = {"question": question, "request_id": f"social-question-{i}"}
        response = client.post(f"/sessao/{identifier}/mensagem", json=payload)
        assert response.status_code == 200 and response.json["answer"] == answers[i]["answer"]
        assert client.post(f"/sessao/{identifier}/mensagem", json=payload).json == response.json
        spoken = client.post(f"/sessao/{identifier}/voz/falar", json={"turn_id": response.json["turn_id"], "voice_id": "pf_dora"})
        assert spoken.status_code == 200 and spoken.json["text"] == answers[i]["answer"]
    assert service._request.call_count == 3
    assert captured[1]["simulationState"]["details"][0]["detail"] == "Não segue uma religião."
    assert len(captured[2]["simulationState"]["details"]) == 3
    with app.app_context():
        training = db.session.get(Consultation, identifier)
        assert len(training.turns) == 3 and training.status == "active" and training.score is None
        assert training.case_snapshot == original == db.session.get(ClinicalCase, case_id).data
        assert len(training.simulation.payload["details"]) == 3
    page = client.get(f"/sessao/{identifier}")
    assert page.status_code == 200 and answers[0]["answer"] in page.text


def test_validation_log_contains_codes_without_conversation_or_provider_prose(case_data, caplog):
    service = AIService({"DEEPSEEK_API_KEY": "isolated-test-key", "DEMO_MODE": False, "AI_TIMEOUT": 12})
    forbidden = {"answer": "Tenho diabetes e uso insulina. SEGREDO_NAO_LOGAR",
                 "intents": ["Informações pessoais"], "newDetails": []}
    service._request = Mock(return_value=forbidden)
    with caplog.at_level("WARNING", logger="medlab.ai"), pytest.raises(AIError):
        service.respond(case_data, "PERGUNTA_PRIVADA_NAO_LOGAR", [])
    assert service._request.call_count == 2
    assert "attempt=1 reason=unsupported_important_detail" in caplog.text
    assert "attempt=2 reason=unsupported_important_detail" in caplog.text
    assert "SEGREDO_NAO_LOGAR" not in caplog.text and "PERGUNTA_PRIVADA_NAO_LOGAR" not in caplog.text
    assert "isolated-test-key" not in caplog.text
