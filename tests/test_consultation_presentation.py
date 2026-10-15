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


def test_finish_path_is_explicit_and_does_not_reveal_reference(app, client, start_session, monkeypatch):
    session_id = start_session()
    monkeypatch.setattr("medlab.web.get_ai_service", lambda: pytest.fail("Navigation must not evaluate"))
    body = client.get(f"/sessao/{session_id}").get_data(as_text=True)
    chat_panel = body.split('id="chat-panel"', 1)[1].split('id="exams-panel"', 1)[0]
    assert 'id="conclude-consultation" type="button"' in chat_panel
    assert 'data-conclude-consultation aria-controls="diagnosis-panel"' in chat_panel
    assert "Concluir consulta e receber avaliação" in chat_panel
    assert "Não há encerramento automático por tempo ou número de perguntas." in body
    assert "A avaliação começa após o envio dessas conclusões." in body
    assert 'action="/sessao/' + session_id + '/finalizar" method="post"' in body
    assert "Enviar conclusões e receber avaliação" in body
    assert "Voltar à conversa antes de enviar" in body
    assert "avaliação de demonstração por regras, sem IA" in body
    assert "Diagnóstico reservado do cenário" not in body
    with app.app_context():
        training = db.session.get(Consultation, session_id)
        assert training.status == "active"
        assert training.evaluation is None


def test_live_finish_path_identifies_ai_without_calling_it(app, client, start_session, monkeypatch):
    session_id = start_session()
    app.config["DEMO_MODE"] = False
    monkeypatch.setattr("medlab.web.get_ai_service", lambda: pytest.fail("Rendering must not call AI"))
    body = client.get(f"/sessao/{session_id}").get_data(as_text=True)
    assert "o avaliador por IA analisa a conversa, os exames solicitados e suas conclusões" in body
    assert "avaliação de demonstração por regras" not in body
    assert "médico experiente" not in body


def test_completed_feedback_exposes_scores_and_reference_as_distinct_sections(app, client, start_session, monkeypatch):
    session_id = start_session()
    with app.app_context():
        training = db.session.get(Consultation, session_id)
        training.status = "completed"
        training.diagnosis = "Hipótese escrita pela estudante"
        training.treatment = "Conduta escrita pela estudante"
        training.score = 73
        training.evaluation = {
            "score": 73, "summary": "Resumo educacional de teste.",
            "sections": [{"title": "Anamnese", "score": 73, "feedback": "Comentário de teste.",
                          "strengths": ["Ponto forte registrado"], "improvements": ["Ajuste indicado"], "evidence": []}],
            "strengths": ["Ponto forte registrado"], "improvements": ["Ajuste indicado"], "next_steps": [],
        }
        db.session.commit()
    monkeypatch.setattr("medlab.web.get_ai_service", lambda: pytest.fail("Results must not trigger another evaluation"))
    body = client.get(f"/sessao/{session_id}/resultado").get_data(as_text=True)
    for anchor in ("result-dimensions", "result-reflections", "result-reference"):
        assert f'href="#{anchor}"' in body
        assert f'id="{anchor}"' in body
    assert 'id="result-reference"><details class="review-details" open>' in body
    assert "Hipótese escrita pela estudante" in body
    assert "Diagnóstico reservado do cenário" in body
    assert "Plano reservado do cenário de teste." in body
    assert "Ponto forte registrado" in body and "Ajuste indicado" in body
    assert "AVALIAÇÃO DE DEMONSTRAÇÃO" in body
    assert "73<small>/100</small>" in body
