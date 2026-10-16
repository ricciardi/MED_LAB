"""A separate teaching request may add a debrief, never rewrite a saved grade.

The app fixture uses only SQLite in memory. The teaching boundary is mocked;
provider calls and the private development corpus are never used here.
"""
from copy import deepcopy
from datetime import timedelta
import re
import sys
from types import ModuleType
from unittest.mock import Mock

import pytest
from sqlalchemy.exc import SQLAlchemyError

from medlab.ai import AIError
from medlab.case_schema import SECTION_TITLES
from medlab.extensions import db
from medlab.models import (
    AIUsage, ClinicalCase, Consultation, ConsultationSimulation, PerformedExam, Turn, utcnow,
)
from medlab.usage import COUNTERS, usage_summary


REVIEW = {"version": 1, "summary": "Parecer sintético reservado para os testes da rota."}


class UsageService:
    is_demo = False

    def __init__(self):
        self.counters = {key: 0 for key in COUNTERS}

    def usage_snapshot(self):
        return deepcopy(self.counters)

    def record_request(self, *, response=True):
        self.counters["requests"] += 1
        if response:
            for key, value in {"responses": 1, "usageReports": 1, "cacheUsageReports": 1,
                "promptTokens": 100, "completionTokens": 20, "totalTokens": 120,
                "cacheHitTokens": 40, "cacheMissTokens": 60}.items():
                self.counters[key] += value


@pytest.fixture
def teacher(monkeypatch):
    service = UsageService()

    def build(actual_service, case, session, evaluation):
        assert actual_service is service
        service.record_request()
        return deepcopy(REVIEW)

    # The production helper has its own schema/provider tests. Substitution at
    # its import boundary also keeps this route suite usable during development.
    module = ModuleType("medlab.teaching_review")
    module.build_teaching_review = Mock(side_effect=build)
    monkeypatch.setitem(sys.modules, "medlab.teaching_review", module)
    factory = Mock(return_value=service)
    monkeypatch.setattr("medlab.web.get_ai_service", factory)
    return module.build_teaching_review, service, factory


@pytest.fixture
def completed(app, start_session):
    identifier = start_session()
    app.config["DEMO_MODE"] = False
    with app.app_context():
        training = db.session.get(Consultation, identifier)
        training.status, training.completed_at, training.score = "completed", utcnow(), 37
        training.diagnosis = "Hipótese salva pelo aluno."
        training.differential = "Diferencial salvo pelo aluno."
        training.treatment = "Plano salvo de investigação e acompanhamento."
        training.evaluation = {
            "score": 37, "summary": "Avaliação já concluída.",
            "sections": [{"key": key, "title": title, "score": score, "assessable": True,
                "feedback": "Feedback salvo.", "strengths": [], "improvements": [],
                "evidence": ["Evidência salva."]}
                for (key, title), score in zip(SECTION_TITLES.items(), [83, 0, 0, 61, 41, 37])],
            "strengths": [], "improvements": [], "next_steps": [],
        }
        for index, (question, answer, intent) in enumerate([
            ("Quando começou?", "Ontem.", "Início dos sintomas"),
            ("Isso afetou suas atividades?", "Parei uma caminhada.", "Impacto nas atividades"),
        ], start=1):
            db.session.add(Turn(consultation_id=identifier, question=question, answer=answer,
                intent=intent, request_id=f"saved-turn-{index}"))
        for name, exam_type, result in (
            ("Sinais vitais", "physical_exam", "Resultado físico salvo."),
            ("Exame laboratorial fictício", "auxiliary_exam", "Resultado complementar salvo."),
        ):
            db.session.add(PerformedExam(consultation_id=identifier, name=name,
                exam_type=exam_type, result=result))
        db.session.add(ConsultationSimulation(consultation_id=identifier, payload={
            "version": 1, "startedAfterTurn": 1, "details": [
                {"topic": "Impacto nas atividades", "detail": "Parei uma caminhada.", "turn": 2}]}))
        db.session.commit()
    return identifier


def endpoint(identifier):
    return f"/api/sessoes/{identifier}/parecer"


def saved_state(app, identifier):
    """Read the full persisted clinical state, excluding only the allowed addition/lock."""
    def columns(row):
        return {column.name: deepcopy(getattr(row, column.name)) for column in row.__table__.columns}

    with app.app_context():
        training = db.session.get(Consultation, identifier)
        consultation = columns(training)
        consultation.pop("busy_since")
        consultation.pop("lock_token")
        if isinstance(consultation["evaluation"], dict):
            consultation["evaluation"].pop("learningReview", None)
        return {"consultation": consultation, "case": columns(training.clinical_case),
            "turns": [columns(row) for row in training.turns],
            "exams": [columns(row) for row in training.exams],
            "simulation": columns(training.simulation) if training.simulation else None}


def assert_unlocked(app, identifier):
    with app.app_context():
        training = db.session.get(Consultation, identifier)
        assert training.busy_since is None and training.lock_token is None


def test_requires_login_and_does_not_reveal_another_students_completed_consultation(app, client, login, completed, teacher):
    build, _, factory = teacher
    before = saved_state(app, completed)
    client.post("/sair")
    assert client.post(endpoint(completed), json={}).status_code == 401
    login("colega@example.invalid")
    assert client.post(endpoint(completed), json={}).status_code == 404
    assert client.post(endpoint("missing-consultation"), json={}).status_code == 404
    build.assert_not_called()
    factory.assert_not_called()
    assert saved_state(app, completed) == before


@pytest.mark.parametrize("change", [{"status": "active"}, {"evaluation": None}, {"evaluation": []}])
def test_only_completed_consultations_with_saved_evaluation_are_eligible(app, client, completed, teacher, change):
    with app.app_context():
        training = db.session.get(Consultation, completed)
        for key, value in change.items():
            setattr(training, key, value)
        db.session.commit()
    before = saved_state(app, completed)
    assert client.post(endpoint(completed), json={}).status_code == 409
    teacher[0].assert_not_called()
    assert saved_state(app, completed) == before
    assert_unlocked(app, completed)


@pytest.mark.parametrize("request_kwargs", [
    {"json": {"score": 100}}, {"json": {"case": {}, "history": []}},
    {"json": {"evaluation": {"score": 100}, "simulationContext": {}}},
    {"json": []}, {"json": "{}"}, {"data": "null", "content_type": "application/json"},
    {"data": "{broken", "content_type": "application/json"}, {"data": {}},
])
def test_request_cannot_supply_or_replace_clinical_inputs(app, client, completed, teacher, request_kwargs):
    before = saved_state(app, completed)
    assert client.post(endpoint(completed), **request_kwargs).status_code == 400
    teacher[0].assert_not_called()
    assert saved_state(app, completed) == before
    assert_unlocked(app, completed)


def test_csrf_and_post_are_required_even_for_empty_payload(app, client, completed, teacher):
    app.config["WTF_CSRF_ENABLED"] = True
    assert client.get(endpoint(completed)).status_code == 405
    assert client.post(endpoint(completed), json={}).status_code == 400
    teacher[0].assert_not_called()
    page = client.get(f"/sessao/{completed}/resultado").get_data(as_text=True)
    match = re.search(r'name="csrf-token" content="([^"]+)"', page)
    assert match
    token = match.group(1)
    assert client.post(endpoint(completed), json={}, headers={
        "X-CSRFToken": token, "Referer": "https://example.invalid/foreign"}).status_code == 400
    assert client.post(endpoint(completed), json={}, headers={
        "X-CSRFToken": token, "Referer": f"https://localhost/sessao/{completed}/resultado"}).status_code == 200
    teacher[0].assert_called_once()


def test_success_uses_saved_snapshot_and_exact_history_context_without_mutating_grades(app, client, completed, teacher):
    build, service, _ = teacher
    with app.app_context():
        # A later catalog edit must not replace the consultation's frozen case.
        training = db.session.get(Consultation, completed)
        changed = deepcopy(training.clinical_case.data)
        changed["treatments"] = "Referência do catálogo alterada depois da consulta."
        training.clinical_case.data = changed
        db.session.commit()
    before = saved_state(app, completed)

    def mutate_arguments(actual_service, case, session, evaluation):
        assert actual_service is service
        assert case == before["consultation"]["case_snapshot"]
        assert case != before["case"]["data"]
        assert evaluation == before["consultation"]["evaluation"]
        assert session == {
            "history": [{"question": row["question"], "answer": row["answer"], "intentClassification": row["intent"]}
                        for row in before["turns"]],
            "performedExams": [{"itemName": row["name"], "result": row["result"], "examType": row["exam_type"]}
                               for row in before["exams"]],
            "userSubmissions": [{"data": {"mainDiagnoses": [{"diagnosisName": "Hipótese salva pelo aluno."}],
                "differentialDiagnoses": [{"diagnosisName": "Diferencial salvo pelo aluno."}]}}],
            "userTreatments": [{"data": {"treatmentPlan": "Plano salvo de investigação e acompanhamento."}}],
            "simulationContext": before["simulation"]["payload"],
        }
        case["patientProfile"]["name"] = "Alteração indevida do argumento."
        evaluation["sections"][0]["score"] = 100
        evaluation["score"] = 100
        session["history"][0]["question"] = "Alteração indevida do argumento."
        session["simulationContext"]["details"].clear()
        service.record_request()
        return deepcopy(REVIEW)

    build.side_effect = mutate_arguments
    response = client.post(endpoint(completed), json={})
    assert response.status_code == 200 and response.get_json() == {"learningReview": REVIEW}
    assert saved_state(app, completed) == before
    with app.app_context():
        training = db.session.get(Consultation, completed)
        assert training.evaluation["learningReview"] == REVIEW
        assert training.status == "completed" and training.score == 37
    assert_unlocked(app, completed)


def test_legacy_without_simulation_or_differential_does_not_invent_them(app, client, completed, teacher):
    with app.app_context():
        training = db.session.get(Consultation, completed)
        db.session.delete(training.simulation)
        training.differential = None
        db.session.commit()
    assert client.post(endpoint(completed), json={}).status_code == 200
    session = teacher[0].call_args.args[2]
    assert "simulationContext" not in session
    assert session["userSubmissions"][0]["data"]["differentialDiagnoses"] == []


def test_repeated_request_reuses_saved_review_without_provider_or_additional_usage(app, client, completed, teacher):
    first = client.post(endpoint(completed), json={})
    app.config.update(DEMO_MODE=True, MAX_DAILY_AI_REQUESTS=0)
    second = client.post(endpoint(completed), json={})
    assert first.status_code == second.status_code == 200
    assert first.get_json() == second.get_json() == {"learningReview": REVIEW}
    teacher[0].assert_called_once()
    teacher[2].assert_called_once()
    with app.app_context():
        assert usage_summary()["operations"] == 1


def test_demo_without_existing_review_does_not_call_provider(app, client, completed, teacher):
    app.config["DEMO_MODE"] = True
    before = saved_state(app, completed)
    assert client.post(endpoint(completed), json={}).status_code == 503
    teacher[0].assert_not_called()
    assert saved_state(app, completed) == before
    assert_unlocked(app, completed)


def test_failed_request_keeps_every_saved_record_and_retry_can_succeed(app, client, completed, teacher):
    build, service, _ = teacher
    before = saved_state(app, completed)

    def unavailable(*args):
        service.record_request(response=False)
        raise AIError("Serviço temporariamente indisponível.")

    build.side_effect = unavailable
    assert client.post(endpoint(completed), json={}).status_code == 503
    assert saved_state(app, completed) == before
    assert_unlocked(app, completed)
    with app.app_context():
        assert "learningReview" not in db.session.get(Consultation, completed).evaluation
        usage = db.session.scalar(db.select(AIUsage))
        assert usage.operation == "teaching" and not usage.successful
        assert usage.requests == 1 and usage.responses == 0 and not usage.usage_complete
    build.side_effect = lambda *args: deepcopy(REVIEW)
    assert client.post(endpoint(completed), json={}).status_code == 200
    assert build.call_count == 2 and saved_state(app, completed) == before


def test_active_lock_rejects_second_request_without_changing_its_owner(app, client, completed, teacher):
    with app.app_context():
        training = db.session.get(Consultation, completed)
        training.busy_since, training.lock_token = utcnow(), "other-review"
        db.session.commit()
    assert client.post(endpoint(completed), json={}).status_code == 409
    teacher[0].assert_not_called()
    with app.app_context():
        assert db.session.get(Consultation, completed).lock_token == "other-review"


def test_stale_lock_can_be_replaced_for_completed_session(app, client, completed, teacher):
    with app.app_context():
        training = db.session.get(Consultation, completed)
        training.busy_since, training.lock_token = utcnow() - timedelta(minutes=4), "expired-review"
        db.session.commit()
    assert client.post(endpoint(completed), json={}).status_code == 200
    teacher[0].assert_called_once()
    assert_unlocked(app, completed)


def test_lost_lock_does_not_save_result_or_release_new_owner_lock(app, client, completed, teacher):
    before = saved_state(app, completed)

    def stolen_lock(*args):
        training = db.session.get(Consultation, completed)
        training.lock_token, training.busy_since = "new-owner", utcnow()
        db.session.commit()
        return deepcopy(REVIEW)

    teacher[0].side_effect = stolen_lock
    assert client.post(endpoint(completed), json={}).status_code == 409
    assert saved_state(app, completed) == before
    with app.app_context():
        training = db.session.get(Consultation, completed)
        assert training.lock_token == "new-owner" and training.busy_since is not None
        assert "learningReview" not in training.evaluation


def test_review_saved_between_initial_read_and_lock_is_reused(app, client, completed, teacher, monkeypatch):
    from medlab import web
    acquire = web.acquire_lock

    def concurrent_completion(training, *, status):
        token = acquire(training, status=status)
        training.evaluation = {**training.evaluation, "learningReview": deepcopy(REVIEW)}
        db.session.commit()
        return token

    monkeypatch.setattr(web, "acquire_lock", concurrent_completion)
    assert client.post(endpoint(completed), json={}).get_json() == {"learningReview": REVIEW}
    teacher[0].assert_not_called()
    teacher[2].assert_not_called()
    assert_unlocked(app, completed)


@pytest.mark.parametrize("scope", ["hourly", "daily"])
def test_limits_stop_provider_calls_and_release_lock(app, client, completed, teacher, scope):
    before = saved_state(app, completed)
    teacher[0].side_effect = AIError("Indisponível.")
    allowed = 4 if scope == "hourly" else 1
    if scope == "daily":
        app.config["MAX_DAILY_AI_REQUESTS"] = 1
    for _ in range(allowed):
        assert client.post(endpoint(completed), json={}).status_code == 503
    assert client.post(endpoint(completed), json={}).status_code == 429
    assert teacher[0].call_count == allowed
    assert saved_state(app, completed) == before
    assert_unlocked(app, completed)


def test_save_failure_rolls_back_review_and_preserves_grade_for_retry(app, client, completed, teacher, monkeypatch):
    before = saved_state(app, completed)
    commit = db.session.commit
    failed = False

    def fail_review_commit():
        nonlocal failed
        for obj in db.session.dirty:
            if isinstance(obj, Consultation) and isinstance(obj.evaluation, dict) and "learningReview" in obj.evaluation:
                failed = True
                raise SQLAlchemyError("private simulated database error")
        return commit()

    with monkeypatch.context() as patch:
        patch.setattr(db.session, "commit", fail_review_commit)
        response = client.post(endpoint(completed), json={})
    assert failed and response.status_code == 503
    assert "private simulated database error" not in response.get_data(as_text=True)
    assert saved_state(app, completed) == before
    assert_unlocked(app, completed)
    with app.app_context():
        assert "learningReview" not in db.session.get(Consultation, completed).evaluation
    assert client.post(endpoint(completed), json={}).status_code == 200


def test_teaching_usage_has_own_operation_and_no_clinical_payload(app, client, completed, teacher):
    assert client.post(endpoint(completed), json={}).status_code == 200
    with app.app_context():
        rows = db.session.scalars(db.select(AIUsage)).all()
        assert len(rows) == 1
        row = rows[0]
        assert row.operation == "teaching" and row.successful
        assert row.consultation_id == completed
        assert row.user_id == db.session.get(Consultation, completed).user_id
        assert (row.requests, row.responses, row.prompt_tokens, row.completion_tokens, row.total_tokens) == (1, 1, 100, 20, 120)
        assert row.cache_hit_tokens == 40 and row.cache_miss_tokens == 60
        assert row.usage_complete and row.cache_usage_complete
        stored = str({column.name: getattr(row, column.name) for column in row.__table__.columns})
        assert "Hipótese salva" not in stored and REVIEW["summary"] not in stored
        assert usage_summary(AIUsage.operation == "teaching")["cache_rate"] == 40
