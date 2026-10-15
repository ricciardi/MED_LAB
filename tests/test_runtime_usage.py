"""Runtime usage remains accurate, private and independent of failed content."""
from datetime import timedelta
import json
import re
from unittest.mock import Mock

import pytest
import requests
from sqlalchemy import inspect
from sqlalchemy.dialects import mysql
from sqlalchemy.schema import CreateTable

from medlab.ai import AIService, _evaluation_claim_fields
from medlab.extensions import db
from medlab.migrations import upgrade_database
from medlab.models import AIUsage, ClinicalCase, Consultation, Turn, User, utcnow
from medlab.usage import capture_usage, usage_summary
from test_web import evaluation_payload
from test_fictional_audit import response as fictional_audit_response


USAGE = {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12,
    "prompt_cache_hit_tokens": 4, "prompt_cache_miss_tokens": 6}


def provider_response(payload, usage=USAGE, status=200):
    body = {"choices": [{"finish_reason": "stop", "message": {
        "content": payload if isinstance(payload, str) else json.dumps(payload)}}]}
    if usage is not None:
        body["usage"] = usage
    return Mock(status_code=status, content=json.dumps(body).encode(), json=Mock(return_value=body))


@pytest.fixture
def live_ai(app, monkeypatch):
    app.config.update(DEMO_MODE=False, DEEPSEEK_API_KEY="private-provider-key-for-tests")
    service = AIService(app.config)
    # Deliberately reuse one instance to verify per-operation deltas, not a
    # cumulative duplicate when conversation is followed by evaluation.
    monkeypatch.setattr("medlab.web.get_ai_service", lambda: service)
    assessment = evaluation_payload()
    refs = {"consultation": {"id": "Q1", "quote": "quando começou?"},
        "diagnosis": {"id": "D1", "quote": "PRIVATE_DIAGNOSIS_SENTINEL"},
        "treatment": {"id": "T1", "quote": "Plano sintético para o teste."},
        "overall": {"id": "Q1", "quote": "quando começou?"}}
    for section in assessment["sections"]:
        section.update(feedback="Revisão dos critérios.", strengths=[])
        section["evidenceRefs"] = [refs[section["key"]]] if section["key"] in refs else []
        if not section["evidenceRefs"]:
            section["score"] = 0
    assessment.update(summary="Revisão educacional.", strengths=[])
    audit = {"checks": [{"path": field["path"], "claims": []} for field in _evaluation_claim_fields(assessment)]}
    post = Mock(side_effect=[provider_response({"answer": "PRIVATE_RESPONSE_SENTINEL: começou ontem.",
        "intents": ["Início dos sintomas"], "newDetails": []}),
        provider_response(assessment), provider_response(audit)])
    monkeypatch.setattr("medlab.ai.requests.post", post)
    return service, post


def send_question(client, identifier, request_id="usage-request-1"):
    return client.post(f"/sessao/{identifier}/mensagem", json={
        "question": "PRIVATE_QUESTION_SENTINEL: quando começou?", "request_id": request_id})


def finish(client, identifier):
    return client.post(f"/sessao/{identifier}/finalizar", data={
        "diagnosis": "PRIVATE_DIAGNOSIS_SENTINEL", "treatment": "Plano sintético para o teste."})


def test_conversation_and_evaluation_persist_real_deltas_without_retry_duplication(app, client, start_session, live_ai):
    _, post = live_ai
    identifier = start_session()
    response = send_question(client, identifier)
    assert response.status_code == 200
    assert set(response.get_json()) == {"answer", "intent", "turn_id", "turn_count"}
    assert send_question(client, identifier).status_code == 200
    assert finish(client, identifier).status_code == 302
    assert finish(client, identifier).status_code == 302
    assert post.call_count == 3
    with app.app_context():
        rows = db.session.scalars(db.select(AIUsage).order_by(AIUsage.id)).all()
        assert [row.operation for row in rows] == ["conversation", "evaluation"]
        assert [row.requests for row in rows] == [1, 2]
        assert [row.prompt_tokens for row in rows] == [10, 20]
        assert [row.completion_tokens for row in rows] == [2, 4]
        assert [row.cache_hit_tokens for row in rows] == [4, 8]
        assert all(row.successful and row.usage_complete and row.cache_usage_complete for row in rows)
        assert all(row.consultation_id == identifier and row.user_id == db.session.get(Consultation, identifier).user_id for row in rows)
        totals = usage_summary()
        assert totals["requests"] == 3 and totals["total_tokens"] == 36 and totals["cache_rate"] == 40
        stored = json.dumps([{column.name: str(getattr(row, column.name)) for column in AIUsage.__table__.columns} for row in rows])
        for forbidden in ("PRIVATE_RESPONSE_SENTINEL", "PRIVATE_QUESTION_SENTINEL", "PRIVATE_DIAGNOSIS_SENTINEL", "private-provider-key-for-tests"):
            assert forbidden not in stored


@pytest.mark.parametrize("failure", ["partial_http_failure", "invalid_answer_with_usage", "timeout"])
def test_failed_turn_preserves_reported_usage_after_route_rollback(app, client, start_session, live_ai, failure):
    _, post = live_ai
    identifier = start_session()
    if failure == "timeout":
        post.side_effect = requests.Timeout("RAW_PRIVATE_PROVIDER_ERROR")
    else:
        second = provider_response({}, usage=None, status=503) if failure == "partial_http_failure" else provider_response("not JSON")
        # The first output lacks answer/newDetails, so a single bounded repair
        # follows. Its reported usage must survive even if that repair fails.
        post.side_effect = [provider_response({"intents": ["Início dos sintomas"]}), second]
    response = send_question(client, identifier)
    assert response.status_code == 503 and "RAW_PRIVATE_PROVIDER_ERROR" not in response.text
    with app.app_context():
        row = db.session.scalar(db.select(AIUsage))
        assert row is not None and row.successful is False
        assert db.session.scalar(db.select(db.func.count(Turn.id))) == 0
        assert db.session.get(Consultation, identifier).lock_token is None
        if failure == "timeout":
            assert row.requests == 1 and row.responses == row.usage_reports == 0
            assert row.usage_complete is row.cache_usage_complete is False
        elif failure == "partial_http_failure":
            assert row.requests == row.responses == 2 and row.usage_reports == 1
            assert row.prompt_tokens == 10 and row.cache_hit_tokens == 4
            assert usage_summary()["cache_rate"] is None
        else:
            assert row.requests == row.usage_reports == 2
            assert row.total_tokens == 24 and row.usage_complete is True


def test_failed_evaluation_records_consumption_and_preserves_submission(app, client, start_session, live_ai):
    _, post = live_ai
    identifier = start_session()
    assert send_question(client, identifier).status_code == 200
    post.side_effect = [provider_response({"score": "invalid"}) for _ in range(2)]
    assert finish(client, identifier).status_code == 302
    with app.app_context():
        row = db.session.scalar(db.select(AIUsage).where(AIUsage.operation == "evaluation"))
        assert row.requests == 2 and row.total_tokens == 24 and row.successful is False
        training = db.session.get(Consultation, identifier)
        assert training.status == "active" and training.evaluation is None
        assert training.diagnosis == "PRIVATE_DIAGNOSIS_SENTINEL"


def test_demo_never_calls_or_appears_as_api_consumption(app, client, start_session, monkeypatch):
    post = Mock(side_effect=AssertionError("Demo must not call the provider."))
    monkeypatch.setattr("medlab.ai.requests.post", post)
    identifier = start_session()
    assert send_question(client, identifier).status_code == 200
    assert finish(client, identifier).status_code == 302
    post.assert_not_called()
    with app.app_context():
        assert usage_summary()["operations"] == usage_summary()["requests"] == 0


@pytest.mark.parametrize("valid", [False, True])
def test_admin_generation_captures_success_or_rejected_output(app, client, login, monkeypatch, case_data, valid):
    app.config.update(DEMO_MODE=False, DEEPSEEK_API_KEY="private-provider-key-for-tests")
    post = Mock(side_effect=[provider_response(case_data), provider_response(fictional_audit_response(case_data))]) if valid else Mock(return_value=provider_response({}))
    monkeypatch.setattr("medlab.ai.requests.post", post)
    login("admin@example.invalid", admin=True)
    response = client.post("/gestao-teste/casos/gerar", data={"specialty": "Clínica médica", "difficulty": "Iniciante"})
    assert response.status_code == 302
    with app.app_context():
        row = db.session.scalar(db.select(AIUsage))
        assert row.operation == "generation" and row.requests == 2 and row.successful is valid
        assert row.consultation_id is None and db.session.get(User, row.user_id).is_admin is True


def test_metrics_transaction_does_not_commit_pending_case_edits(app, monkeypatch):
    with app.app_context():
        app.config.update(DEMO_MODE=False, DEEPSEEK_API_KEY="private-provider-key-for-tests")
        service = AIService(app.config)
        monkeypatch.setattr("medlab.ai.requests.post", Mock(return_value=provider_response({"ok": True})))
        case = db.session.scalar(db.select(ClinicalCase))
        identifier, title = case.id, case.title
        case.title = "Uncommitted private edit"
        with capture_usage(service, "generation"):
            service._request("json", {})
        db.session.rollback()
        assert db.session.get(ClinicalCase, identifier).title == title
        assert usage_summary()["requests"] == 1


def test_missing_metrics_table_does_not_destroy_a_valid_patient_reply(app, client, start_session, live_ai, caplog):
    identifier = start_session()
    with app.app_context():
        AIUsage.__table__.drop(db.engine)
    response = send_question(client, identifier)
    assert response.status_code == 200
    assert "Não foi possível registrar os contadores de uso da IA." in caplog.text
    assert "private-provider-key-for-tests" not in caplog.text
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Turn.id))) == 1
        upgrade_database()
        assert "ai_usage" in inspect(db.engine).get_table_names()


def test_private_usage_report_is_unavailable_to_students_and_public(app, client, login, start_session, live_ai):
    assert client.get("/gestao-teste/uso-ia").status_code == 404
    assert "/uso-ia" not in client.get("/").text
    identifier = start_session()
    assert send_question(client, identifier).status_code == 200
    assert client.get("/gestao-teste/uso-ia").status_code == 404
    assert "/uso-ia" not in client.get("/painel").text
    assert "data-usage-total" not in client.get(f"/sessao/{identifier}").text
    login("admin@example.invalid")
    assert client.get("/gestao-teste/uso-ia").status_code == 404
    login("admin@example.invalid", admin=True)
    response = client.get("/gestao-teste/uso-ia")
    assert response.status_code == 200
    assert 'name="robots" content="noindex,nofollow"' in response.text
    assert 'data-usage-total="requests">1<' in response.text
    assert 'data-usage-total="prompt_tokens">10<' in response.text
    assert 'data-usage-total="cache_rate">40,0' in response.text
    assert "PRIVATE_QUESTION_SENTINEL" not in response.text and "PRIVATE_RESPONSE_SENTINEL" not in response.text
    assert "private-provider-key-for-tests" not in response.text
    assert "aluna@example.invalid" not in response.text
    assert "Importações pelo terminal não estão incluídas." in response.text


def test_partial_admin_report_shows_known_tokens_but_no_cache_rate(app, client, start_session, login, live_ai):
    _, post = live_ai
    identifier = start_session()
    post.side_effect = [provider_response({"intents": ["Início dos sintomas"]}), provider_response({}, usage=None, status=503)]
    assert send_question(client, identifier).status_code == 503
    login("admin@example.invalid", admin=True)
    response = client.get("/gestao-teste/uso-ia")
    assert 'data-usage-total="prompt_tokens">10<' in response.text
    assert 'data-usage-total="cache_rate">—<' in response.text
    assert "Dados parciais de cache" in response.text and "Total parcial informado" in response.text
    assert "Consumo informado em 1 de 2 solicitações" in response.text


def test_usage_page_handles_empty_missing_and_reported_zero_distinctly(app, client, login):
    login("admin@example.invalid", admin=True)
    empty = client.get("/gestao-teste/uso-ia")
    assert "A primeira chamada abre este panorama." in empty.text
    assert 'data-usage-total="prompt_tokens">—<' in empty.text
    with app.app_context():
        db.session.add(AIUsage(operation="conversation", successful=False, requests=1, responses=0,
            usage_reports=0, cache_usage_reports=0, prompt_tokens=0, completion_tokens=0, total_tokens=0,
            cache_hit_tokens=0, cache_miss_tokens=0, usage_complete=False, cache_usage_complete=False))
        db.session.commit()
    missing = client.get("/gestao-teste/uso-ia")
    assert 'data-usage-total="prompt_tokens">—<' in missing.text
    with app.app_context():
        db.session.add(AIUsage(operation="evaluation", successful=True, requests=1, responses=1,
            usage_reports=1, cache_usage_reports=1, prompt_tokens=0, completion_tokens=0, total_tokens=0,
            cache_hit_tokens=0, cache_miss_tokens=0, usage_complete=True, cache_usage_complete=True))
        db.session.commit()
    zero = client.get("/gestao-teste/uso-ia?operation=evaluation")
    assert 'data-usage-total="prompt_tokens">0<' in zero.text
    assert 'data-usage-total="cache_rate">—<' in zero.text


def test_report_filters_pagination_and_additive_mysql_schema(app, client, login):
    with app.app_context():
        for index in range(27):
            db.session.add(AIUsage(operation="conversation", successful=True, requests=1, responses=1,
                usage_reports=1, cache_usage_reports=1, prompt_tokens=10, completion_tokens=2, total_tokens=12,
                cache_hit_tokens=4, cache_miss_tokens=6, usage_complete=True, cache_usage_complete=True,
                created_at=utcnow() - timedelta(days=40 if index == 26 else 1)))
        db.session.commit()
        upgrade_database()
        upgrade_database()
        ddl = str(CreateTable(AIUsage.__table__).compile(dialect=mysql.dialect()))
        assert "prompt_tokens BIGINT" in ddl and "ON DELETE SET NULL" in ddl
        assert not {"prompt", "answer", "diagnosis", "password", "api_key", "error", "history"}.intersection(AIUsage.__table__.columns.keys())
    login("admin@example.invalid", admin=True)
    first = client.get("/gestao-teste/uso-ia?period=7&operation=conversation")
    assert len(re.findall('data-usage-id=', first.text)) == 25
    assert 'data-usage-total="requests">26<' in first.text
    assert "period=7" in first.text and "operation=conversation" in first.text and "page=2" in first.text
    assert len(re.findall('data-usage-id=', client.get("/gestao-teste/uso-ia?period=7&operation=conversation&page=2").text)) == 1
    assert 'data-usage-total="requests">27<' in client.get("/gestao-teste/uso-ia?period=all").text
    assert client.get("/gestao-teste/uso-ia?page=99999").status_code == 302
