"""Security boundaries of a completed encounter with one pending grade stage.

SQLite-in-memory fixtures and mocked semantic output only; never provider/network.
"""
from copy import deepcopy
import re
from unittest.mock import Mock

import pytest

from medlab.ai import AIError, AIService, BasicQuestionsPending
from medlab.basic_questions import KEY, TITLE
from medlab.extensions import db
from medlab.models import Consultation, ConsultationSimulation, PerformedExam, Turn, utcnow
from test_ai import evaluation

FORM = {"diagnosis": "Hipótese original salva", "differential": "Alternativa original salva",
        "treatment": "Plano original salvo para a consulta fictícia."}
SECTION = {"key": KEY, "title": TITLE, "score": 40, "assessable": True,
           "feedback": "Julgamento semântico simulado no teste isolado.", "strengths": [],
           "improvements": [], "evidence": ["Pergunta preservada no servidor."],
           "criteria": [], "groups": []}


def retry_path(identifier):
    return f"/sessao/{identifier}/avaliacao/retomar"


def state(app, identifier):
    with app.app_context():
        row = db.session.get(Consultation, identifier)
        return {"status": row.status, "completed_at": row.completed_at, "score": row.score,
                "evaluation": deepcopy(row.evaluation), "case_snapshot": deepcopy(row.case_snapshot),
                "diagnosis": row.diagnosis, "differential": row.differential, "treatment": row.treatment,
                "turns": [(t.id, t.question, t.answer, t.intent, t.request_id) for t in row.turns],
                "exams": [(e.id, e.name, e.result, e.exam_type) for e in row.exams],
                "simulation": deepcopy(row.simulation.payload) if row.simulation else None}


@pytest.fixture
def pending(app, client, start_session, monkeypatch):
    identifier = start_session()
    app.config["DEMO_MODE"] = False
    with app.app_context():
        db.session.add(Turn(consultation_id=identifier, request_id="frozen-1",
            question="Quando começou?", answer="Ontem.", intent="Início dos sintomas"))
        db.session.add(PerformedExam(consultation_id=identifier, name="Sinais vitais",
            result="Resultado físico de teste.", exam_type="physical_exam"))
        db.session.add(ConsultationSimulation(consultation_id=identifier,
            payload={"version": 1, "startedAfterTurn": 0, "details": []}))
        db.session.commit()
    service = AIService({"DEEPSEEK_API_KEY": "test-only-no-network", "DEMO_MODE": False})
    base = evaluation(60)
    base["summary"] = "PRIVATE_BASE_SUMMARY_NOT_FINAL"
    base["privateDiagnostic"] = "PRIVATE_PROVIDER_METADATA_NOT_FOR_HTML"
    base["sections"][-1]["feedback"] = "PRIVATE_GLOBAL_SCORE_NOT_A_FINAL_GRADE"
    service._evaluate = Mock(return_value=base)
    service._request = Mock(side_effect=AssertionError("No network/provider requests allowed"))
    service.evaluate = Mock(wraps=service.evaluate)
    semantic = Mock(side_effect=[AIError("Etapa semântica indisponível no teste.")])
    monkeypatch.setattr("medlab.basic_questions.build_review", semantic)
    monkeypatch.setattr("medlab.web.get_ai_service", lambda: service)
    response = client.post(f"/sessao/{identifier}/finalizar", data=FORM)
    assert response.status_code == 302 and response.headers["Location"].endswith("/resultado")
    assert state(app, identifier)["score"] is None
    assert state(app, identifier)["status"] == "completed"
    service.evaluate.reset_mock()
    semantic.reset_mock(side_effect=True)
    semantic.return_value = deepcopy(SECTION)
    return identifier, service, semantic


def test_pending_result_has_dimension_feedback_but_no_final_grade_or_checkpoint(app, client, pending):
    identifier, service, semantic = pending
    before = state(app, identifier)
    page = client.get(f"/sessao/{identifier}/resultado")
    html = page.get_data(as_text=True)
    assert page.status_code == 200
    assert 'data-basic-evaluation-pending' in html and 'data-final-score-pending' in html
    assert "Revisar a evidência registrada." in html
    assert "Diagnóstico reservado do cenário" in html  # Encounter is frozen before reference exposure.
    for secret in ("pendingBasicQuestions", "baseEvaluation", "inputHash",
                   before["evaluation"]["pendingBasicQuestions"]["inputHash"],
                   "PRIVATE_BASE_SUMMARY_NOT_FINAL", "PRIVATE_PROVIDER_METADATA_NOT_FOR_HTML",
                   "PRIVATE_GLOBAL_SCORE_NOT_A_FINAL_GRADE"):
        assert secret not in html
    assert 'data-score-equation' not in html and 'id="learning-review"' not in html
    assert state(app, identifier) == before
    service.evaluate.assert_not_called()
    semantic.assert_not_called()


@pytest.mark.parametrize("email", ["colega@example.invalid", "admin@example.invalid"])
def test_owner_scope_applies_to_reference_and_retry_even_for_admin(app, client, login, pending, email):
    identifier, service, semantic = pending
    before = state(app, identifier)
    client.post('/sair')
    assert client.post(retry_path(identifier)).status_code == 302
    assert client.get(f'/sessao/{identifier}/resultado').status_code == 302
    login(email)
    assert client.post(retry_path(identifier)).status_code == 404
    assert client.get(f'/sessao/{identifier}/resultado').status_code == 404
    service.evaluate.assert_not_called()
    semantic.assert_not_called()
    assert state(app, identifier) == before


def test_retry_requires_post_and_csrf_then_uses_saved_inputs(app, client, pending):
    identifier, service, semantic = pending
    before = state(app, identifier)
    app.config['WTF_CSRF_ENABLED'] = True
    assert client.get(retry_path(identifier)).status_code == 405
    assert client.post(retry_path(identifier)).status_code == 400
    assert state(app, identifier) == before
    service.evaluate.assert_not_called()
    html = client.get(f'/sessao/{identifier}/resultado').get_data(as_text=True)
    token = re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)
    assert client.post(retry_path(identifier), data={'csrf_token': token},
        headers={'Referer': 'https://example.invalid/foreign'}).status_code == 400
    assert client.post(retry_path(identifier), data={'csrf_token': token},
        headers={'Referer': f'https://localhost/sessao/{identifier}/resultado'}).status_code == 302
    assert state(app, identifier)['score'] == 57
    assert service._evaluate.call_count == 1
    assert semantic.call_count == 1


@pytest.mark.parametrize('payload_kind', ['form', 'json'])
def test_forged_retry_payload_cannot_replace_snapshot_submission_or_checkpoint(app, client, pending, payload_kind):
    identifier, service, semantic = pending
    before = state(app, identifier)
    forged = {'score': 100, 'diagnosis': 'Gabarito forjado', 'treatment': 'Plano forjado',
              'history': [], 'case_snapshot': {}, 'evaluation_checkpoint': {'baseEvaluation': evaluation(100)}}
    kwargs = {'json': forged} if payload_kind == 'json' else {'data': {k: str(v) for k, v in forged.items()}}
    response = client.post(retry_path(identifier), **kwargs)
    assert response.status_code == 302
    after = state(app, identifier)
    assert after['score'] == 57
    for key in before.keys() - {'evaluation', 'score'}:
        assert after[key] == before[key]
    call = service.evaluate.call_args
    assert call.kwargs['evaluation_checkpoint'] == before['evaluation']['pendingBasicQuestions']
    assert call.args[0] == before['case_snapshot']
    assert call.args[1]['userSubmissions'][0]['data']['mainDiagnoses'][0]['diagnosisName'] == FORM['diagnosis']
    assert call.args[1]['simulationContext'] == before['simulation']
    assert service._evaluate.call_count == 1
    semantic.assert_called_once()


@pytest.mark.parametrize('mutation', ['hash', 'case', 'diagnosis', 'turn', 'exam', 'simulation'])
def test_stale_frozen_checkpoint_fails_closed_without_regrading(app, client, pending, mutation):
    identifier, service, semantic = pending
    with app.app_context():
        row = db.session.get(Consultation, identifier)
        if mutation == 'hash':
            value = deepcopy(row.evaluation)
            value['pendingBasicQuestions']['inputHash'] = '0' * 64
            row.evaluation = value
        elif mutation == 'case':
            value = deepcopy(row.case_snapshot)
            value['patientProfile']['chief_complaint'] = 'Queixa diferente.'
            row.case_snapshot = value
        elif mutation == 'diagnosis':
            row.diagnosis = 'Conclusão alterada fora deste fluxo'
        elif mutation == 'turn':
            row.turns[0].question = 'Pergunta alterada fora deste fluxo?'
        elif mutation == 'exam':
            row.exams[0].result = 'Resultado alterado fora deste fluxo.'
        else:
            row.simulation.payload = {'version': 1, 'startedAfterTurn': 1, 'details': []}
        db.session.commit()
    before = state(app, identifier)
    response = client.post(retry_path(identifier), follow_redirects=True)
    assert response.status_code == 200
    assert 'Não foi possível conferir a etapa salva' in response.get_data(as_text=True)
    assert state(app, identifier) == before
    service.evaluate.assert_not_called()
    semantic.assert_not_called()
    assert service._evaluate.call_count == 1


def test_frozen_encounter_cannot_mutate_after_reference_is_available(app, client, pending):
    identifier, service, semantic = pending
    before = state(app, identifier)
    assert client.post(f'/sessao/{identifier}/mensagem', json={'question': 'Nova pergunta?', 'request_id': 'later-request'}).status_code == 409
    assert client.post(f'/sessao/{identifier}/exame', json={'name': 'Exame laboratorial fictício', 'type': 'auxiliary_exam'}).status_code == 409
    assert client.post(f'/sessao/{identifier}/finalizar', data={**FORM, 'diagnosis': 'Outra conclusão'}).status_code == 302
    assert client.post(f'/api/sessoes/{identifier}/parecer', json={}).status_code == 409
    assert state(app, identifier) == before
    service.evaluate.assert_not_called()
    semantic.assert_not_called()


def test_busy_retry_and_lost_lock_do_not_overwrite_checkpoint_or_new_owner(app, client, pending):
    identifier, service, semantic = pending
    before = state(app, identifier)
    with app.app_context():
        row = db.session.get(Consultation, identifier)
        row.busy_since, row.lock_token = utcnow(), 'other-active-owner'
        db.session.commit()
    assert client.post(retry_path(identifier)).status_code == 302
    service.evaluate.assert_not_called()
    assert state(app, identifier) == before
    with app.app_context():
        row = db.session.get(Consultation, identifier)
        assert row.lock_token == 'other-active-owner'
        row.busy_since = row.lock_token = None
        db.session.commit()
    def steal_lock(*_args):
        row = db.session.get(Consultation, identifier)
        row.lock_token, row.busy_since = 'newer-owner', utcnow()
        db.session.commit()
        return deepcopy(SECTION)
    semantic.side_effect = steal_lock
    assert client.post(retry_path(identifier)).status_code == 302
    assert state(app, identifier) == before
    with app.app_context():
        assert db.session.get(Consultation, identifier).lock_token == 'newer-owner'
    assert service._evaluate.call_count == 1


@pytest.mark.parametrize('failure', ['transport', 'semantic'])
def test_failed_retry_keeps_base_grade_pending_and_releases_owned_lock(app, client, pending, failure):
    identifier, service, semantic = pending
    before = state(app, identifier)
    if failure == 'transport':
        semantic.side_effect = AIError('Falha sintética do provedor.')
    else:
        semantic.side_effect = BasicQuestionsPending(deepcopy(before['evaluation']['pendingBasicQuestions']))
    assert client.post(retry_path(identifier)).status_code == 302
    assert state(app, identifier) == before
    with app.app_context():
        row = db.session.get(Consultation, identifier)
        assert row.lock_token is None and row.busy_since is None
    assert service._evaluate.call_count == 1


def test_successful_retry_is_idempotent_and_preserves_five_accepted_dimensions(app, client, pending):
    identifier, service, semantic = pending
    before = state(app, identifier)
    assert client.post(retry_path(identifier)).status_code == 302
    after = state(app, identifier)
    assert after['evaluation']['evaluationVersion'] == 2 and after['score'] == 57
    assert after['evaluation']['sections'][:5] == before['evaluation']['pendingBasicQuestions']['baseEvaluation']['sections'][:5]
    assert 'pendingBasicQuestions' not in after['evaluation']
    for _ in range(2):
        assert client.post(retry_path(identifier)).status_code == 302
    assert state(app, identifier) == after
    assert service.evaluate.call_count == semantic.call_count == 1
    assert service._evaluate.call_count == 1


def test_retry_limit_stops_a_fifth_attempt_without_discarding_pending_result(app, client, pending):
    identifier, service, semantic = pending
    before = state(app, identifier)
    semantic.side_effect = AIError('Falha sintética persistente.')
    for _ in range(4):
        assert client.post(retry_path(identifier)).status_code == 302
    assert service.evaluate.call_count == 4
    response = client.post(retry_path(identifier), follow_redirects=True)
    assert 'limite de solicitações' in response.get_data(as_text=True)
    assert service.evaluate.call_count == 4
    assert service._evaluate.call_count == 1
    assert state(app, identifier) == before
    with app.app_context():
        row = db.session.get(Consultation, identifier)
        assert row.lock_token is None and row.busy_since is None


def test_global_daily_quota_also_blocks_a_retry_without_changing_accepted_results(app, client, pending):
    identifier, service, semantic = pending
    before = state(app, identifier)
    app.config['MAX_DAILY_AI_REQUESTS'] = 0
    response = client.post(retry_path(identifier), follow_redirects=True)
    assert 'limite de solicitações' in response.get_data(as_text=True)
    service.evaluate.assert_not_called()
    semantic.assert_not_called()
    assert state(app, identifier) == before
