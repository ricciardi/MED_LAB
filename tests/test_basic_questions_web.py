"""Preserve valid feedback and freeze the encounter while its last grade waits."""
from copy import deepcopy
from unittest.mock import Mock

import pytest

from medlab.ai import AIError, AIService
from medlab.extensions import db
from medlab.models import Consultation, Turn
from test_ai import evaluation
from test_basic_questions import provider_response, scored


def semantic_response(case_data):
    semantic = provider_response(case_data, scored(case_data))
    for row in semantic["items"]:
        if row["actionIds"]:
            row["actionIds"] = ["Q1"]  # Controlled fixture; evidence text stays server-owned.
    return semantic


@pytest.fixture
def pending_session(app, start_session, monkeypatch, case_data):
    identifier = start_session()
    with app.app_context():
        db.session.add(Turn(consultation_id=identifier, request_id="basic-review-test",
            question="Isso veio de repente ou aos poucos? E o que ajuda a aliviar?", answer="Aos poucos; repouso ajuda.", intent="Início dos sintomas"))
        db.session.commit()
    service = AIService({"DEEPSEEK_API_KEY": "test", "DEMO_MODE": False})
    service._evaluate = Mock(return_value=evaluation(60))
    attempts = []

    def provider(_prompt, data, _max_tokens):
        attempts.append(data)
        if len(attempts) == 1:
            raise AIError("Indisponível")
        return semantic_response(case_data)

    service._request = Mock(side_effect=provider)
    monkeypatch.setattr("medlab.web.get_ai_service", lambda: service)
    return identifier, service


FORM = {"diagnosis": "Hipótese sintética", "treatment": "Conduta sintética registrada."}


def test_pending_category_shows_valid_feedback_and_resumes_only_category(app, client, pending_session):
    identifier, service = pending_session
    path = f"/sessao/{identifier}/finalizar"
    first = client.post(path, data=FORM)
    assert first.status_code == 302 and first.headers["Location"].endswith("/resultado")
    with app.app_context():
        training = db.session.get(Consultation, identifier)
        assert training.status == "completed" and training.score is None and training.completed_at is not None
        assert set(training.evaluation) == {"evaluationStatus", "pendingBasicQuestions"}
        assert training.evaluation["evaluationStatus"] == "pending_basic_questions"
        assert training.evaluation["pendingBasicQuestions"]["baseEvaluation"]["score"] == 60
        assert training.diagnosis == FORM["diagnosis"] and training.treatment == FORM["treatment"]
        assert training.lock_token is None and training.busy_since is None
    page = client.get(f"/sessao/{identifier}/resultado").get_data(as_text=True)
    assert "baseEvaluation" not in page and "inputHash" not in page
    assert 'data-basic-evaluation-pending' in page and 'data-final-score-pending' in page
    assert page.count('class="evaluation-card"') == 5
    assert 'id="result-calculation"' not in page and 'class="score-circle"' not in page
    assert 'id="learning-review"' not in page and 'learning-review.js' not in page
    assert "Diagnóstico reservado do cenário" in page
    assert "Feedback<br>qualitativo" not in page
    second = client.post(f"/sessao/{identifier}/avaliacao/retomar")
    assert second.status_code == 302 and second.headers["Location"].endswith("/resultado")
    with app.app_context():
        training = db.session.get(Consultation, identifier)
        assert training.status == "completed" and training.score == 62
        assert training.evaluation["evaluationVersion"] == 2
        assert "pendingBasicQuestions" not in training.evaluation
    assert service._evaluate.call_count == 1 and service._request.call_count == 2
    assert "criteriaCatalog" in service._request.call_args.args[1]
    client.post(path, data=FORM)
    assert service._request.call_count == 2  # Neither stage repeats after completion.


def test_finished_interview_cannot_be_changed_after_reference_is_visible(app, client, pending_session):
    identifier, service = pending_session
    path = f"/sessao/{identifier}/finalizar"
    client.post(path, data=FORM)
    client.post(path, data={**FORM, "diagnosis": "Outra hipótese sintética"})
    assert service._evaluate.call_count == 1 and service._request.call_count == 1
    with app.app_context():
        training = db.session.get(Consultation, identifier)
        assert training.status == "completed" and training.diagnosis == FORM["diagnosis"]
        before = deepcopy(training.evaluation)
    assert client.post(f"/sessao/{identifier}/mensagem", json={"question": "Nova pergunta", "request_id": "should-not-change"}).status_code == 409
    assert client.post(f"/sessao/{identifier}/exame", json={"name": "Sinais vitais", "type": "physical_exam"}).status_code == 409
    with app.app_context():
        assert db.session.get(Consultation, identifier).evaluation == before


def test_expired_lock_cannot_save_checkpoint(app, client, pending_session, monkeypatch):
    identifier, service = pending_session
    monkeypatch.setattr("medlab.web.lock_is_owned", lambda *_: False)
    client.post(f"/sessao/{identifier}/finalizar", data=FORM)
    with app.app_context():
        training = db.session.get(Consultation, identifier)
        assert training.status == "active" and training.evaluation is None and training.score is None
    assert service._evaluate.call_count == 1
