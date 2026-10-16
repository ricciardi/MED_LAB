"""Recovery is isolated, authenticated and never publishes a five-part total."""
from copy import deepcopy

from medlab.ai import AIError
from medlab.extensions import db
from medlab.models import Consultation, utcnow
from test_basic_questions_web import FORM, pending_session, semantic_response  # noqa: F401


def pending(app, client, pending_session):
    identifier, service = pending_session
    client.post(f"/sessao/{identifier}/finalizar", data=FORM)
    with app.app_context():
        saved = deepcopy(db.session.get(Consultation, identifier).evaluation)
    return identifier, service, saved


def test_failed_retry_keeps_base_pending_and_releases_lock(app, client, pending_session):
    identifier, service, saved = pending(app, client, pending_session)
    service._request.side_effect = AIError("Indisponível")
    response = client.post(f"/sessao/{identifier}/avaliacao/retomar", follow_redirects=True)
    assert response.status_code == 200 and 'data-final-score-pending' in response.text
    assert "ainda não puderam ser validadas" in response.text
    with app.app_context():
        training = db.session.get(Consultation, identifier)
        assert training.evaluation == saved and training.score is None
        assert training.status == "completed" and training.lock_token is None
    assert service._evaluate.call_count == 1


def test_retry_uses_saved_submission_and_completion_date(app, client, pending_session):
    identifier, service, _ = pending(app, client, pending_session)
    with app.app_context():
        ended = db.session.get(Consultation, identifier).completed_at
    client.post(f"/sessao/{identifier}/avaliacao/retomar", data={"diagnosis": "Resposta adulterada", "treatment": "Outro plano"})
    with app.app_context():
        training = db.session.get(Consultation, identifier)
        assert training.diagnosis == FORM["diagnosis"] and training.treatment == FORM["treatment"]
        assert training.completed_at == ended
        assert training.score == 62 and "pendingBasicQuestions" not in training.evaluation
    assert service._evaluate.call_count == 1
    assert service._request.call_count == 2
    client.post(f"/sessao/{identifier}/avaliacao/retomar")
    assert service._request.call_count == 2


def test_pending_history_and_dashboard_do_not_call_it_qualitative(app, client, pending_session):
    identifier, _, _ = pending(app, client, pending_session)
    history = client.get("/historico").text
    assert "Avaliação pendente" in history and "Nota final<br>pendente" in history
    assert "Retomar avaliação" in history and "Avaliação<br>qualitativa" not in history
    dashboard = client.get("/painel").text
    assert "Avaliação pendente" in dashboard and "Nota pendente" in dashboard
    assert client.get(f"/sessao/{identifier}").headers["Location"].endswith("/resultado")


def test_teaching_waits_without_calling_provider(app, client, pending_session):
    identifier, service, _ = pending(app, client, pending_session)
    app.config["DEMO_MODE"] = False
    result = client.post(f"/api/sessoes/{identifier}/parecer", json={})
    assert result.status_code == 409 and "Retome as Perguntas Básicas" in result.json["error"]
    assert service._request.call_count == 1


def test_retry_requires_owner_and_csrf(app, client, login, pending_session):
    identifier, service, _ = pending(app, client, pending_session)
    client.post("/sair")
    login("colega@example.invalid")
    assert client.get(f"/sessao/{identifier}/resultado").status_code == 404
    assert client.post(f"/sessao/{identifier}/avaliacao/retomar").status_code == 404
    client.post("/sair")
    login()
    app.config["WTF_CSRF_ENABLED"] = True
    assert client.post(f"/sessao/{identifier}/avaliacao/retomar").status_code == 400
    assert service._request.call_count == 1


def test_lock_prevents_parallel_retry(app, client, pending_session):
    identifier, service, saved = pending(app, client, pending_session)
    with app.app_context():
        training = db.session.get(Consultation, identifier)
        training.busy_since, training.lock_token = utcnow(), "other-worker"
        db.session.commit()
    response = client.post(f"/sessao/{identifier}/avaliacao/retomar", follow_redirects=True)
    assert "está sendo retomada" in response.text and service._request.call_count == 1
    with app.app_context():
        training = db.session.get(Consultation, identifier)
        assert training.evaluation == saved and training.lock_token == "other-worker"


def test_changed_saved_context_cannot_regrade_after_reference(app, client, pending_session):
    identifier, service, saved = pending(app, client, pending_session)
    with app.app_context():
        training = db.session.get(Consultation, identifier)
        training.diagnosis = "Alteração administrativa posterior"
        db.session.commit()
    response = client.post(f"/sessao/{identifier}/avaliacao/retomar", follow_redirects=True)
    assert "Não foi possível conferir a etapa salva" in response.text
    assert service._request.call_count == 1 and service._evaluate.call_count == 1
    with app.app_context():
        assert db.session.get(Consultation, identifier).evaluation == saved


def test_lost_retry_lock_cannot_overwrite_pending_result(app, client, pending_session, monkeypatch):
    identifier, service, saved = pending(app, client, pending_session)
    monkeypatch.setattr("medlab.web.lock_is_owned", lambda *args, **kwargs: False)
    response = client.post(f"/sessao/{identifier}/avaliacao/retomar", follow_redirects=True)
    assert "A retomada expirou" in response.text
    with app.app_context():
        training = db.session.get(Consultation, identifier)
        assert training.evaluation == saved and training.score is None
    assert service._evaluate.call_count == 1


def test_quota_does_not_discard_valid_feedback(app, client, pending_session, monkeypatch):
    identifier, service, saved = pending(app, client, pending_session)
    monkeypatch.setattr("medlab.web.ai_quota", lambda: False)
    response = client.post(f"/sessao/{identifier}/avaliacao/retomar", follow_redirects=True)
    assert "limite de solicitações" in response.text and 'data-final-score-pending' in response.text
    with app.app_context():
        assert db.session.get(Consultation, identifier).evaluation == saved
    assert service._request.call_count == 1


def test_existing_active_checkpoint_can_finish_without_repeating_base(app, client, pending_session):
    identifier, service, saved = pending(app, client, pending_session)
    # This is the persisted shape written by the version before the recovery fix.
    with app.app_context():
        training = db.session.get(Consultation, identifier)
        training.status, training.completed_at = "active", None
        training.evaluation = {"pendingBasicQuestions": saved["pendingBasicQuestions"]}
        db.session.commit()
    response = client.post(f"/sessao/{identifier}/finalizar", data=FORM)
    assert response.headers["Location"].endswith("/resultado")
    with app.app_context():
        training = db.session.get(Consultation, identifier)
        assert training.status == "completed" and training.score == 62
    assert service._evaluate.call_count == 1


def test_result_get_is_read_only_and_does_not_emit_private_checkpoint(app, client, pending_session):
    identifier, service, saved = pending(app, client, pending_session)
    for _ in range(2):
        body = client.get(f"/sessao/{identifier}/resultado").text
        assert "baseEvaluation" not in body and "inputHash" not in body
        assert "pendingBasicQuestions" not in body and "evaluationStatus" not in body
        assert 'class="score-circle"' not in body and 'data-score-equation' not in body
    with app.app_context():
        assert db.session.get(Consultation, identifier).evaluation == saved
    assert service._request.call_count == 1


def test_failed_selective_repair_preserves_base_and_retry_publishes_only_valid_review(app, client, pending_session, case_data):
    identifier, service = pending_session
    valid = semantic_response(case_data)
    invalid = deepcopy(valid)
    next(row for row in invalid["items"] if row["id"] == "H01")["actionIds"] = ["A1"]
    attempts = []
    repair_available = False

    def provider(_prompt, data, max_tokens):
        attempts.append(deepcopy(data))
        if "repair" not in data:
            assert len(data["criteriaCatalog"]) == 47
            return deepcopy(invalid)
        # Only the invalid authorship is sent back; all other items stay saved
        # inside this review attempt and cannot be replaced by the repair.
        assert [row["id"] for row in data["criteriaCatalog"]] == ["H01"]
        assert data["format"]["items"]["requiredCount"] == 1
        assert [row["id"] for row in data["repair"]["previousResponse"]["items"]] == ["H01"]
        assert data["repair"]["diagnostic"]["code"] == "reference_or_authorship"
        assert max_tokens < 10000
        if not repair_available:
            raise AIError("Reparo indisponível")
        return {"version": 1, "items": [deepcopy(next(row for row in valid["items"] if row["id"] == "H01"))]}

    service._request.side_effect = provider
    response = client.post(f"/sessao/{identifier}/finalizar", data=FORM, follow_redirects=True)
    assert response.status_code == 200 and 'data-final-score-pending' in response.text
    with app.app_context():
        training = db.session.get(Consultation, identifier)
        base = deepcopy(training.evaluation["pendingBasicQuestions"]["baseEvaluation"])
        assert training.status == "completed" and training.score is None
        assert training.lock_token is None and training.busy_since is None
        assert base["score"] == 60
        assert not any(row["key"] == "basic_questions" for row in base["sections"])
    assert service._request.call_count == 2 and service._evaluate.call_count == 1

    repair_available = True
    response = client.post(f"/sessao/{identifier}/avaliacao/retomar", follow_redirects=True)
    assert response.status_code == 200 and 'data-final-score-pending' not in response.text
    with app.app_context():
        training = db.session.get(Consultation, identifier)
        assert training.score == 62 and "pendingBasicQuestions" not in training.evaluation
        assert training.evaluation["sections"][:5] == base["sections"][:5]
        review = next(row for row in training.evaluation["sections"] if row["key"] == "basic_questions")
        assert len(review["criteria"]) == 47
        repaired = next(row for row in review["criteria"] if row["id"] == "H01")
        retained = next(row for row in review["criteria"] if row["id"] == "H07")
        assert repaired["evidenceRefs"] == ["Q1"]
        assert retained["reason"] == next(row for row in valid["items"] if row["id"] == "H07")["reason"]
        saved = deepcopy(training.evaluation)
    assert service._request.call_count == 4 and service._evaluate.call_count == 1
    assert ["repair" in data for data in attempts] == [False, True, False, True]
    client.post(f"/sessao/{identifier}/avaliacao/retomar")
    assert service._request.call_count == 4
    with app.app_context():
        assert db.session.get(Consultation, identifier).evaluation == saved
