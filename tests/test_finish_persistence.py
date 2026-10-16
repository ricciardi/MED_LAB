"""Database failures must not commit grades without their ranking projection."""
from unittest.mock import Mock

import pytest
from sqlalchemy.exc import SQLAlchemyError

from medlab.extensions import db
from medlab.models import Consultation, RankingResult, RankingStanding, Turn
from medlab.ranking import sync_consultation_ranking
from test_score_presentation import assessment


FORM = {"diagnosis": "Hipótese sintética", "differential": "Diferencial sintético",
        "treatment": "Conduta sintética registrada."}


@pytest.fixture
def submitted_session(app, start_session, monkeypatch):
    identifier = start_session()
    with app.app_context():
        db.session.add(Turn(consultation_id=identifier, request_id="finish-persistence-test",
            question="Quando começou?", answer="Ontem.", intent="Início"))
        db.session.commit()
    service = Mock(is_demo=False)
    service.evaluate.return_value = assessment([80] * 5)
    monkeypatch.setattr("medlab.web.get_ai_service", lambda: service)
    return identifier, service


def assert_draft_preserved(app, identifier):
    with app.app_context():
        training = db.session.get(Consultation, identifier)
        assert training.status == "active" and training.score is None and training.evaluation is None
        assert training.completed_at is None and training.lock_token is None and training.busy_since is None
        assert training.diagnosis == FORM["diagnosis"] and training.differential == FORM["differential"]
        assert training.treatment == FORM["treatment"] and len(training.turns) == 1
        assert db.session.get(RankingResult, identifier) is None


def test_missing_ranking_table_never_commits_partial_finalization(app, client, submitted_session):
    identifier, service = submitted_session
    with app.app_context():
        RankingStanding.__table__.drop(db.engine)
    try:
        response = client.post(f"/sessao/{identifier}/finalizar", data=FORM)
        assert response.status_code == 302 and response.headers["Location"].endswith(identifier)
        assert_draft_preserved(app, identifier)
        with client.session_transaction() as browser_session:
            messages = " ".join(message for _, message in browser_session.get("_flashes", []))
            assert "Não foi possível salvar a avaliação" in messages and "foram preservadas" in messages
        assert service.evaluate.call_count == 1
    finally:
        with app.app_context():
            RankingStanding.__table__.create(db.engine)


def test_repeated_database_failure_rolls_back_score_and_all_projection_writes(app, client, submitted_session, monkeypatch):
    identifier, service = submitted_session

    def fail_after_flush(training):
        sync_consultation_ranking(training)
        db.session.flush()  # Grade, result and standing have all been written.
        raise SQLAlchemyError("Synthetic persistence failure")

    sync = Mock(side_effect=fail_after_flush)
    monkeypatch.setattr("medlab.ranking.sync_consultation_ranking", sync)
    response = client.post(f"/sessao/{identifier}/finalizar", data=FORM)
    assert response.status_code == 302 and response.headers["Location"].endswith(identifier)
    assert_draft_preserved(app, identifier)
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count()).select_from(RankingStanding)) == 0
    assert service.evaluate.call_count == 1 and sync.call_count == 2


def test_transient_save_failure_reuses_paid_evaluation_and_commits_once(app, client, submitted_session, monkeypatch):
    identifier, service = submitted_session
    attempts = []

    def fail_once(training):
        sync_consultation_ranking(training)
        db.session.flush()
        attempts.append(training.id)
        if len(attempts) == 1:
            raise SQLAlchemyError("Synthetic transient failure")

    monkeypatch.setattr("medlab.ranking.sync_consultation_ranking", fail_once)
    response = client.post(f"/sessao/{identifier}/finalizar", data=FORM)
    assert response.status_code == 302 and response.headers["Location"].endswith("/resultado")
    with app.app_context():
        training = db.session.get(Consultation, identifier)
        assert training.status == "completed" and training.score == 80 and training.lock_token is None
        assert db.session.get(RankingResult, identifier).score == 80
        standing = db.session.get(RankingStanding, (training.user_id, "clinical"))
        assert standing.consultations == 1 and standing.distinct_cases == 1 and standing.average_score == 80
    assert service.evaluate.call_count == 1 and len(attempts) == 2


def test_save_retry_rechecks_lock_before_writing(app, client, submitted_session, monkeypatch):
    identifier, service = submitted_session
    owned = Mock(side_effect=[True, False])
    sync = Mock(side_effect=SQLAlchemyError("Synthetic transient failure"))
    monkeypatch.setattr("medlab.web.lock_is_owned", owned)
    monkeypatch.setattr("medlab.ranking.sync_consultation_ranking", sync)
    response = client.post(f"/sessao/{identifier}/finalizar", data=FORM)
    assert response.status_code == 302 and response.headers["Location"].endswith(identifier)
    assert_draft_preserved(app, identifier)
    assert service.evaluate.call_count == 1 and owned.call_count == 2 and sync.call_count == 1


def test_lock_release_failure_does_not_replace_committed_result_with_500(app, client, submitted_session, monkeypatch):
    identifier, service = submitted_session
    monkeypatch.setattr("medlab.web.release_lock", Mock(side_effect=SQLAlchemyError("Synthetic release failure")))
    response = client.post(f"/sessao/{identifier}/finalizar", data=FORM)
    assert response.status_code == 302 and response.headers["Location"].endswith("/resultado")
    with app.app_context():
        training = db.session.get(Consultation, identifier)
        assert training.status == "completed" and training.score == 80
        assert db.session.get(RankingResult, identifier).score == 80
        assert db.session.get(RankingStanding, (training.user_id, "clinical")).consultations == 1
    assert service.evaluate.call_count == 1
