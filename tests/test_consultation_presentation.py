"""Documented summaries stay separate from actual interview turns."""

from copy import deepcopy
from html import escape

import pytest

from medlab.case_schema import UNKNOWN_FACT
from medlab.extensions import db
from medlab.models import Consultation, Turn


def set_profile(app, session_id, **fields):
    with app.app_context():
        training = db.session.get(Consultation, session_id)
        snapshot = deepcopy(training.case_snapshot)
        snapshot["patientProfile"].update(fields)
        training.case_snapshot = snapshot
        db.session.commit()
        return snapshot


def test_opening_is_a_summary_without_fabricated_turn(app, client, start_session, monkeypatch):
    session_id = start_session()
    complaint = "Relata Uma história de 4 dias de dor no peito central e falta de ar."
    snapshot = set_profile(app, session_id, chief_complaint=complaint, occupation=UNKNOWN_FACT)
    monkeypatch.setattr("medlab.web.get_ai_service", lambda: pytest.fail("Rendering must not call AI"))

    response = client.get(f"/sessao/{session_id}")
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "QUEIXA REGISTRADA" in body
    assert body.count(complaint) == 1
    assert f"<p>{complaint}</p>" in body
    assert 'class="message-bubble"' not in body
    assert "Comece pela sua primeira pergunta." in body
    assert UNKNOWN_FACT not in body
    assert "Diagnóstico reservado do cenário" not in body
    with app.app_context():
        training = db.session.get(Consultation, session_id)
        assert training.case_snapshot == snapshot
        assert list(training.turns) == []


def test_saved_questions_and_answers_render_unchanged(app, client, start_session, monkeypatch):
    session_id = start_session()
    complaint = "Resumo clínico em terceira pessoa, sem atribuição de fala."
    respondent = "A mãe fornece o relato da criança."
    snapshot = set_profile(app, session_id, chief_complaint=complaint, respondent=respondent)
    question = "Quando começou? Você disse <ontem>?"
    answer = "Ela me disse: hoje de manhã, não ontem."
    with app.app_context():
        db.session.add(Turn(consultation_id=session_id, question=question, answer=answer,
                            intent="Início dos sintomas", request_id="presentation-saved-turn"))
        db.session.commit()
    monkeypatch.setattr("medlab.web.get_ai_service", lambda: pytest.fail("Rendering must not call AI"))

    body = client.get(f"/sessao/{session_id}").get_data(as_text=True)
    assert body.count('class="message-bubble"') == 2
    assert f'<div class="message-bubble">{escape(question)}</div>' in body
    assert f'<div class="message-bubble">{escape(answer)}</div>' in body
    assert body.count(complaint) == 1
    assert "Comece pela sua primeira pergunta." not in body
    assert "QUEM RESPONDE NESTE CASO" in body and respondent in body
    with app.app_context():
        training = db.session.get(Consultation, session_id)
        assert training.case_snapshot == snapshot
        assert [(t.question, t.answer) for t in training.turns] == [(question, answer)]


@pytest.mark.parametrize("occupation,visible", [
    ("", False), ("   ", False), (UNKNOWN_FACT, False),
    ("Professora", True), (UNKNOWN_FACT + " Observação do registro.", True),
])
def test_occupation_hides_only_missing_values(app, client, start_session, occupation, visible):
    session_id = start_session()
    snapshot = set_profile(app, session_id, occupation=occupation)
    body = client.get(f"/sessao/{session_id}").get_data(as_text=True)
    header = body.split('<div class="patient-header">', 1)[1].split('<div class="chief-complaint">', 1)[0]
    if visible:
        assert occupation in header
    else:
        assert UNKNOWN_FACT not in header
        assert header.count("<span>·</span>") == 1
    with app.app_context():
        assert db.session.get(Consultation, session_id).case_snapshot == snapshot
