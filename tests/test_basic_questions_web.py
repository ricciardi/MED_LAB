"""Persist/resume the paid base stage without finishing an incomplete review."""
from unittest.mock import Mock

import pytest

from medlab.ai import AIError, AIService
from medlab.extensions import db
from medlab.models import Consultation, Turn
from test_ai import evaluation
from test_basic_questions import scored


@pytest.fixture
def pending_session(app, start_session, monkeypatch, case_data):
    identifier = start_session()
    with app.app_context():
        db.session.add(Turn(consultation_id=identifier, request_id="basic-review-test",
            question="Isso veio de repente ou aos poucos?", answer="Aos poucos.", intent="Início dos sintomas"))
        db.session.commit()
    service = AIService({"DEEPSEEK_API_KEY": "test", "DEMO_MODE": False})
    service._evaluate = Mock(return_value=evaluation(60))
    semantic = scored(case_data)
    for row in semantic["items"]:
        if row["actionIds"]:
            row["actionIds"] = ["Q1"]  # Controlled semantic fixture, actual text stays server-owned.
    service._request = Mock(side_effect=[AIError("Indisponível"), semantic])
    monkeypatch.setattr("medlab.web.get_ai_service", lambda: service)
    return identifier, service


FORM = {"diagnosis": "Hipótese sintética", "treatment": "Conduta sintética registrada."}


def test_pending_category_is_private_saved_unfinished_and_resumes_only_category(app, client, pending_session):
    identifier, service = pending_session
    path = f"/sessao/{identifier}/finalizar"
    first = client.post(path, data=FORM)
    assert first.status_code == 302
    with app.app_context():
        training = db.session.get(Consultation, identifier)
        assert training.status == "active" and training.score is None and training.completed_at is None
        assert set(training.evaluation) == {"pendingBasicQuestions"}
        assert training.evaluation["pendingBasicQuestions"]["baseEvaluation"]["score"] == 60
        assert training.diagnosis == FORM["diagnosis"] and training.treatment == FORM["treatment"]
        assert training.lock_token is None and training.busy_since is None
    page = client.get(f"/sessao/{identifier}").get_data(as_text=True)
    assert "baseEvaluation" not in page and "inputHash" not in page
    second = client.post(path, data=FORM)
    assert second.status_code == 302 and second.headers["Location"].endswith("/resultado")
    with app.app_context():
        training = db.session.get(Consultation, identifier)
        assert training.status == "completed" and training.score == 62
        assert training.evaluation["evaluationVersion"] == 2
        assert "pendingBasicQuestions" not in training.evaluation
    assert service._evaluate.call_count == 1 and service._request.call_count == 2
    client.post(path, data=FORM)
    assert service._request.call_count == 2  # Completed evaluation remains idempotent.


def test_changed_submission_invalidates_saved_stage(app, client, pending_session):
    identifier, service = pending_session
    path = f"/sessao/{identifier}/finalizar"
    client.post(path, data=FORM)
    client.post(path, data={**FORM, "diagnosis": "Outra hipótese sintética"})
    assert service._evaluate.call_count == 2
    with app.app_context():
        assert db.session.get(Consultation, identifier).status == "completed"


def test_expired_lock_cannot_save_checkpoint(app, client, pending_session, monkeypatch):
    identifier, service = pending_session
    monkeypatch.setattr("medlab.web.lock_is_owned", lambda *_: False)
    client.post(f"/sessao/{identifier}/finalizar", data=FORM)
    with app.app_context():
        training = db.session.get(Consultation, identifier)
        assert training.status == "active" and training.evaluation is None and training.score is None
    assert service._evaluate.call_count == 1
