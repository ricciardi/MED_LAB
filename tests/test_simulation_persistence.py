"""Session elaborations are private, append-only and atomic with each saved turn."""
from copy import deepcopy
from unittest.mock import Mock

import pytest
from sqlalchemy import event, inspect
from sqlalchemy.dialects import mysql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.schema import CreateTable

from medlab.ai import AIError
from medlab.extensions import db
from medlab.migrations import upgrade_database
from medlab.models import ClinicalCase, Consultation, ConsultationSimulation, Turn
from test_web import evaluation_payload


DETAIL = {"topic": "Fatores de melhora ou piora", "detail": "Prefiro descansar um pouco quando incomoda.", "turn": 1}


def state(*details, started_after=0):
    return {"version": 1, "startedAfterTurn": started_after, "details": deepcopy(list(details))}


def reply(payload):
    return {"answer": "Prefiro descansar um pouco quando incomoda.", "intent": "Fatores de melhora ou piora",
            "simulation_state": deepcopy(payload)}


@pytest.fixture
def service(monkeypatch):
    ai = Mock()
    ai.respond.return_value = reply(state(DETAIL))
    ai.evaluate.return_value = evaluation_payload()
    monkeypatch.setattr("medlab.web.get_ai_service", lambda: ai)
    return ai


def send(client, identifier, suffix="first", **extra):
    return client.post(f"/sessao/{identifier}/mensagem", json={
        "question": "Como isso afeta sua rotina?", "request_id": f"simulation-{suffix}", **extra})


def stored_state(app, identifier):
    with app.app_context():
        row = db.session.get(ConsultationSimulation, identifier)
        return deepcopy(row.payload) if row else None


def submit(client, identifier, **extra):
    return client.post(f"/sessao/{identifier}/finalizar", data={
        "diagnosis": "Hipótese sintética", "treatment": "Conduta sintética para teste.", **extra})


def test_server_memory_is_atomic_private_and_does_not_change_case(app, client, start_session, service):
    identifier = start_session()
    with app.app_context():
        training = db.session.get(Consultation, identifier)
        original = deepcopy(training.case_snapshot)
        case_id = training.case_id
    first = send(client, identifier, simulation_state={"details": ["CLIENT_INJECTION"]})
    assert first.status_code == 200
    assert set(first.json) == {"answer", "intent", "turn_id", "turn_count"}
    assert service.respond.call_args.kwargs == {"simulation_state": None}
    assert stored_state(app, identifier) == state(DETAIL)
    assert send(client, identifier).json == first.json
    assert service.respond.call_count == 1

    added = {"topic": "Preocupações do paciente", "detail": "Tenho receio de atrapalhar minha rotina.", "turn": 2}
    service.respond.return_value = reply(state(DETAIL, added))
    second = send(client, identifier, "second", simulation_state=state())
    assert second.status_code == 200 and second.json["turn_count"] == 2
    assert service.respond.call_args.kwargs["simulation_state"] == state(DETAIL)
    assert len(service.respond.call_args.args[2]) == 1
    with app.app_context():
        training = db.session.get(Consultation, identifier)
        assert training.case_snapshot == original
        assert db.session.get(ClinicalCase, case_id).data == original
        assert len(training.turns) == 2 and training.simulation.payload == state(DETAIL, added)
        assert training.simulation.updated_at is not None
    assert "CLIENT_INJECTION" not in client.get(f"/sessao/{identifier}").text


def test_resuming_legacy_session_records_start_without_reclassifying_old_turn(app, client, start_session, service):
    identifier = start_session()
    with app.app_context():
        db.session.add(Turn(consultation_id=identifier, question="Pergunta anterior?", answer="Resposta legada.",
                            intent="Queixa principal", request_id="legacy-question"))
        db.session.commit()
    detail = {**DETAIL, "turn": 2}
    service.respond.return_value = reply(state(detail, started_after=1))
    assert send(client, identifier).status_code == 200
    assert service.respond.call_args.kwargs["simulation_state"] is None
    assert service.respond.call_args.args[2][0]["answer"] == "Resposta legada."
    assert stored_state(app, identifier) == state(detail, started_after=1)
    assert submit(client, identifier).status_code == 302
    body = client.get(f"/sessao/{identifier}/resultado").text
    assert "começou após a resposta 1" in body and 'data-simulation-turn="2"' in body
    assert 'data-simulation-turn="1"' not in body and "Resposta legada." in body


@pytest.mark.parametrize("invalid", [
    None,
    {"version": 1, "startedAfterTurn": 0, "details": [{**DETAIL, "turn": 3}]},
    {"version": 1, "startedAfterTurn": 0, "details": [{**DETAIL, "detail": "Anterior alterado."}]},
    {"version": 1, "startedAfterTurn": 1, "details": []},
    {"version": 1, "startedAfterTurn": 0, "details": []},
    {"version": 1, "startedAfterTurn": 0, "details": [DETAIL, {**DETAIL, "detail": "Acréscimo retroativo."}]},
])
def test_invalid_or_rewritten_state_saves_neither_answer_nor_memory(app, client, start_session, service, invalid):
    identifier = start_session()
    assert send(client, identifier).status_code == 200
    service.respond.return_value = reply(invalid)
    response = send(client, identifier, "invalid")
    assert response.status_code == 503 and "error" in response.json
    assert stored_state(app, identifier) == state(DETAIL)
    with app.app_context():
        training = db.session.get(Consultation, identifier)
        assert len(training.turns) == 1 and training.lock_token is None


def test_provider_failure_preserves_memory_and_request_can_be_retried(app, client, start_session, service):
    identifier = start_session()
    assert send(client, identifier).status_code == 200
    service.respond.side_effect = AIError("Resposta indisponível.")
    assert send(client, identifier, "retry").status_code == 503
    assert stored_state(app, identifier) == state(DETAIL)
    service.respond.side_effect = None
    service.respond.return_value = reply(state(DETAIL))
    assert send(client, identifier, "retry").status_code == 200
    with app.app_context():
        assert len(db.session.get(Consultation, identifier).turns) == 2


def test_lost_lock_discards_new_answer_and_memory_without_unlocking_new_owner(app, client, start_session, service):
    identifier = start_session()
    assert send(client, identifier).status_code == 200

    def expired_reply(*args, **kwargs):
        training = db.session.get(Consultation, identifier)
        training.lock_token = "replacement-owner"
        db.session.commit()
        return reply(state(DETAIL, {**DETAIL, "detail": "Novo detalhe perdido.", "turn": 2}))

    service.respond.side_effect = expired_reply
    assert send(client, identifier, "expired").status_code == 409
    assert stored_state(app, identifier) == state(DETAIL)
    with app.app_context():
        training = db.session.get(Consultation, identifier)
        assert len(training.turns) == 1 and training.lock_token == "replacement-owner"


def test_memory_insert_failure_rolls_back_already_flushed_turn(app, client, start_session, service):
    identifier = start_session()
    observed = []

    def reject_insert(mapper, connection, target):
        observed.append(connection.execute(db.select(db.func.count(Turn.id))).scalar_one())
        raise IntegrityError("test-memory-insert", {}, Exception("synthetic write failure"))

    event.listen(ConsultationSimulation, "before_insert", reject_insert)
    try:
        response = send(client, identifier)
    finally:
        event.remove(ConsultationSimulation, "before_insert", reject_insert)
    assert response.status_code == 503 and "synthetic write failure" not in response.text
    assert observed == [1], "The injected failure must happen after the Turn has reached this transaction."
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Turn.id))) == 0
        assert db.session.get(ConsultationSimulation, identifier) is None
        assert db.session.get(Consultation, identifier).lock_token is None
    assert send(client, identifier).status_code == 200


def test_evaluation_receives_separate_memory_and_result_escapes_it(app, client, start_session, service):
    identifier = start_session()
    detail = {**DETAIL, "detail": "Um detalhe <em>somente desta simulação</em>."}
    service.respond.return_value = reply(state(detail))
    assert send(client, identifier).status_code == 200
    service.evaluate.return_value["simulationNotice"] = {"message": "Elaborações consideradas separadamente."}
    assert submit(client, identifier, simulationContext="CLIENT_INJECTION").status_code == 302
    case, session = service.evaluate.call_args.args
    assert session["simulationContext"] == state(detail)
    assert "simulationContext" not in case and detail["detail"] not in str(case)
    body = client.get(f"/sessao/{identifier}/resultado").text
    assert "Elaborações consideradas separadamente." in body and "data-session-simulation" in body
    assert 'data-simulation-turn="1"' in body and 'id="review-turn-1"' in body
    assert "&lt;em&gt;somente desta simulação&lt;/em&gt;" in body
    assert detail["detail"] not in body
    reference = body.split('<div class="reference-review">', 1)[1].split('</div></div></details>', 1)[0]
    assert "somente desta simulação" not in reference and "Diagnóstico reservado do cenário" in reference


def test_legacy_finish_has_no_simulation_context_or_retrospective_claim(app, client, start_session, service):
    identifier = start_session()
    with app.app_context():
        db.session.add(Turn(consultation_id=identifier, question="Pergunta anterior?", answer="Resposta legada.",
                            intent="Queixa principal", request_id="legacy-question"))
        db.session.commit()
    assert submit(client, identifier).status_code == 302
    assert "simulationContext" not in service.evaluate.call_args.args[1]
    assert stored_state(app, identifier) is None
    assert "data-session-simulation" not in client.get(f"/sessao/{identifier}/resultado").text


def test_failed_evaluation_cannot_mutate_saved_memory_and_keeps_learner_draft(app, client, start_session, service):
    identifier = start_session()
    assert send(client, identifier).status_code == 200

    def reject_evaluation(case, session):
        session["simulationContext"]["details"].clear()
        case["patientProfile"]["name"] = "Mutação que não deve persistir"
        raise AIError("Avaliação temporariamente indisponível.")

    service.evaluate.side_effect = reject_evaluation
    response = submit(client, identifier)
    assert response.status_code == 302 and not response.location.endswith("/resultado")
    assert stored_state(app, identifier) == state(DETAIL)
    with app.app_context():
        training = db.session.get(Consultation, identifier)
        assert training.status == "active" and training.evaluation is None
        assert training.diagnosis == "Hipótese sintética" and training.lock_token is None
        assert training.case_snapshot["patientProfile"]["name"] == "Paciente Fictício"


def test_details_belong_only_to_owner_and_current_consultation(app, client, start_session, login, service):
    first = start_session()
    assert send(client, first).status_code == 200
    assert submit(client, first).status_code == 302
    second = start_session()
    assert send(client, second).status_code == 200
    assert service.respond.call_args.kwargs["simulation_state"] is None
    assert stored_state(app, first) == stored_state(app, second) == state(DETAIL)
    client.post("/sair")
    login("colega@example.invalid")
    assert client.get(f"/sessao/{first}/resultado").status_code == 404
    assert send(client, second, "intrusion").status_code == 404


def test_create_all_upgrade_preserves_legacy_rows_and_is_idempotent(app, client, start_session):
    identifier = start_session()
    with app.app_context():
        training = db.session.get(Consultation, identifier)
        snapshot = deepcopy(training.case_snapshot)
        db.session.add(Turn(consultation_id=identifier, question="Pergunta?", answer="Resposta antiga.",
                            intent="Queixa principal", request_id="legacy-upgrade"))
        db.session.commit()
        ConsultationSimulation.__table__.drop(db.engine)
        upgrade_database()
        upgrade_database()
        assert "consultation_simulation" in inspect(db.engine).get_table_names()
        assert db.session.get(Consultation, identifier).case_snapshot == snapshot
        assert db.session.scalar(db.select(Turn.answer)) == "Resposta antiga."
        assert db.session.get(ConsultationSimulation, identifier) is None
        db.session.add(ConsultationSimulation(consultation_id=identifier, payload=state()))
        db.session.commit()
        upgrade_database()
        assert db.session.get(ConsultationSimulation, identifier).payload == state()
        ddl = str(CreateTable(ConsultationSimulation.__table__).compile(dialect=mysql.dialect()))
        assert "payload JSON NOT NULL" in ddl and "ON DELETE CASCADE" in ddl
        db.session.delete(db.session.get(Consultation, identifier))
        db.session.commit()
        assert db.session.get(ConsultationSimulation, identifier) is None
