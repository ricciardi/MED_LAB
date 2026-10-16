"""Persistent rankings balance case variety, retain every valid attempt and protect identity."""
from copy import deepcopy
from decimal import Decimal

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.dialects import mysql
from sqlalchemy.schema import CreateTable

from medlab.extensions import db
from medlab.migrations import upgrade_database
from medlab.models import ClinicalCase, Consultation, RankingResult, RankingStanding, RankingState, Turn, User, utcnow
from medlab.ranking import BOOTSTRAP_KEY, display_name, ranking_context, rebuild_rankings, sync_consultation_ranking, validated_score
from test_score_presentation import assessment


@pytest.fixture
def rank_cases(app, case_data):
    with app.app_context():
        for index in range(6):
            data = deepcopy(case_data)
            data["caseId"] = f"ranking-case-{index}"
            db.session.add(ClinicalCase(slug=data["caseId"], title="Caso de teste", specialty="Clínica médica",
                difficulty="Iniciante", summary="Caso sintético", data=data, is_published=True))
        db.session.commit()
        return db.session.scalars(select(ClinicalCase.id).where(ClinicalCase.slug.like("ranking-case-%")).order_by(ClinicalCase.id)).all()


def add_result(user_id, case_id, score=80, track="clinical", *, save=True):
    case = db.session.get(ClinicalCase, case_id)
    snapshot = deepcopy(case.data)
    if track == "basic":
        snapshot["learningTrack"] = "basic"
    value = assessment([score] * 5)
    value["evaluationMode"] = "ai"
    training = Consultation(user_id=user_id, case_id=case_id, case_snapshot=snapshot,
        status="completed", completed_at=utcnow(), score=score, evaluation=value,
        diagnosis="Hipótese de teste", treatment="Conduta de teste")
    db.session.add(training)
    db.session.flush()
    if save:
        sync_consultation_ranking(training)
        db.session.commit()
    return training


def test_five_distinct_cases_required_repetition_does_not_unlock(app, rank_cases):
    with app.app_context():
        for _ in range(7):
            add_result(1, rank_cases[0], 95, "basic")
        for case_id in rank_cases[1:4]:
            add_result(1, case_id, 75, "basic")
        data = ranking_context(1, include_boards=True)
        personal = data["personal_rankings"]["basic"]
        assert personal["distinct_cases"] == 4 and personal["consultations"] == 10
        assert not personal["eligible"] and personal["rank"] is None and personal["remaining_cases"] == 1
        assert data["leaderboards"]["basic"]["entries"] == []
        add_result(1, rank_cases[4], 65, "basic")
        data = ranking_context(1, include_boards=True)
        assert data["personal_rankings"]["basic"]["rank"] == 1
        assert data["leaderboards"]["basic"]["entries"][0]["distinct_cases"] == 5
        assert data["personal_rankings"]["clinical"]["average_score"] is None


def test_all_attempts_count_but_each_case_has_equal_weight(app, rank_cases):
    with app.app_context():
        add_result(1, rank_cases[0], 100)
        add_result(1, rank_cases[0], 0)
        for case_id in rank_cases[1:5]:
            add_result(1, case_id, 80)
        personal = ranking_context(1)["personal_rankings"]["clinical"]
        assert personal["average_score"] == 74.0  # (mean(100,0) + 80*4) / 5
        assert personal["consultations"] == 6 and personal["distinct_cases"] == 5
        add_result(1, rank_cases[0], 100)
        assert ranking_context(1)["personal_rankings"]["clinical"]["average_score"] == 77.33
        assert db.session.scalar(select(db.func.count(RankingResult.consultation_id))) == 7


def test_categories_are_independent_and_case_snapshot_is_authoritative(app, rank_cases):
    with app.app_context():
        for case_id in rank_cases[:5]:
            add_result(1, case_id, 90, "basic")
            add_result(1, case_id, 60, "clinical")
        data = ranking_context(1, include_boards=True)
        assert data["personal_rankings"]["basic"]["average_score"] == 90
        assert data["personal_rankings"]["clinical"]["average_score"] == 60
        case = db.session.get(ClinicalCase, rank_cases[0])
        case.data = {**case.data, "learningTrack": "basic"}
        db.session.commit()
        rebuild_rankings()
        assert ranking_context(1)["personal_rankings"] == data["personal_rankings"]


def test_top_fifteen_and_personal_rank_beyond_fifteenth(app, rank_cases):
    with app.app_context():
        # User 1 is intentionally outside the public top 15.
        for case_id in rank_cases[:5]:
            add_result(1, case_id, 40)
        for index in range(16):
            user = User(name=f"Pessoa{index} Sobrenome Privado", email=f"rank{index}@example.invalid", password_hash="unused")
            db.session.add(user)
            db.session.flush()
            for case_id in rank_cases[:5]:
                add_result(user.id, case_id, 100 - index)
        data = ranking_context(1, include_boards=True)
        assert len(data["leaderboards"]["clinical"]["entries"]) == 15
        assert data["personal_rankings"]["clinical"]["rank"] == 17
        assert [row["rank"] for row in data["leaderboards"]["clinical"]["entries"]] == list(range(1, 16))
        public = repr(data["leaderboards"])
        assert "Sobrenome" not in public and "Privado" not in public and "@" not in public
        assert "user_id" not in public and "consultation_id" not in public


def test_competition_ties_use_two_decimal_score_without_attempt_tiebreaker(app, rank_cases):
    with app.app_context():
        for user_id, score in ((1, 90), (2, 90), (3, 80)):
            for case_id in rank_cases[:5]:
                add_result(user_id, case_id, score)
        add_result(2, rank_cases[0], 90)
        data = ranking_context(2, include_boards=True)
        assert [row["rank"] for row in data["leaderboards"]["clinical"]["entries"]] == [1, 1, 3]
        assert data["personal_rankings"]["clinical"]["rank"] == 1
        assert [row["is_current_user"] for row in data["leaderboards"]["clinical"]["entries"]] == [False, True, False]
        # Scores close enough to round to the same hundredth are one rank.
        assert all(isinstance(row["average_score"], float) for row in data["leaderboards"]["clinical"]["entries"])


def test_tie_uses_half_up_rounded_case_balanced_mean(app, rank_cases):
    with app.app_context():
        for _ in range(39):
            add_result(1, rank_cases[0], 100)
        add_result(1, rank_cases[0], 99)
        for case_id in rank_cases[1:5]:
            add_result(1, case_id, 100)
        for case_id in rank_cases[:5]:
            add_result(2, case_id, 100)
        # User 1: (99.975 + 100 + 100 + 100 + 100) / 5 = 99.995.
        # ROUND_HALF_UP gives 100.00, tied with User 2's exact 100.
        data = ranking_context(2, include_boards=True)
        assert [row["rank"] for row in data["leaderboards"]["clinical"]["entries"]] == [1, 1]
        assert db.session.get(RankingStanding, (1, "clinical")).average_score == Decimal("100.00")


@pytest.mark.parametrize("kind", ["pending", "demo", "demo_legacy", "unconfirmed", "null", "mismatch", "active", "nan", "malformed", "invalid_version"])
def test_only_valid_final_ai_grades_enter_rankings(app, rank_cases, kind):
    with app.app_context():
        training = add_result(1, rank_cases[0], save=False)
        value = deepcopy(training.evaluation)
        if kind == "pending":
            value["evaluationStatus"] = "pending_basic_questions"
        elif kind == "demo":
            value["evaluationMode"] = "demo"
        elif kind == "demo_legacy":
            value["summary"] = "[Demonstração sem IA] Checklist"
            value.pop("evaluationMode")
        elif kind == "unconfirmed":
            value.pop("evaluationMode")
        elif kind == "null":
            training.score = None
        elif kind == "mismatch":
            training.score = 99
        elif kind == "active":
            training.status = "active"
        elif kind == "nan":
            value["score"] = float("nan")
        elif kind == "malformed":
            value["sections"].pop()
        else:
            value["evaluationVersion"] = True
        training.evaluation = value
        assert validated_score(training) is None
        sync_consultation_ranking(training)
        db.session.commit()
        assert db.session.get(RankingResult, training.id) is None
        assert ranking_context(1)["personal_rankings"]["clinical"]["consultations"] == 0


def test_zero_and_checked_legacy_grades_are_included(app, rank_cases):
    with app.app_context():
        training = add_result(1, rank_cases[0], 0, save=False)
        training.evaluation = {key: value for key, value in training.evaluation.items() if key != "evaluationMode"}
        training.evaluation = {**training.evaluation, "verification": {"status": "limited"}}
        assert validated_score(training) == 0
        sync_consultation_ranking(training)
        db.session.commit()
        assert ranking_context(1)["personal_rankings"]["clinical"]["average_score"] == 0


def test_sync_is_atomic_and_idempotent_with_consultation(app, rank_cases):
    with app.app_context():
        training = add_result(1, rank_cases[0], 80)
        sync_consultation_ranking(training)
        sync_consultation_ranking(training)
        db.session.commit()
        assert db.session.scalar(select(db.func.count(RankingResult.consultation_id))) == 1
        before = ranking_context(1)["personal_rankings"]
        training.score = 100
        training.evaluation = {**assessment([100] * 5), "evaluationMode": "ai"}
        sync_consultation_ranking(training)
        db.session.rollback()
        assert training.score == 80 and ranking_context(1)["personal_rankings"] == before


def test_rebuild_and_migration_preserve_original_records_and_are_idempotent(app, rank_cases):
    with app.app_context():
        training = add_result(1, rank_cases[0], 80, save=False)
        db.session.commit()
        identifier, source = training.id, deepcopy(training.evaluation)
        RankingStanding.__table__.drop(db.engine)
        RankingResult.__table__.drop(db.engine)
        upgrade_database()
        assert {"ranking_result", "ranking_standing"} <= set(inspect(db.engine).get_table_names())
        assert ranking_context(1)["personal_rankings"]["clinical"]["consultations"] == 1
        standing = db.session.get(RankingStanding, (1, "clinical"))
        timestamp = standing.updated_at
        report = rebuild_rankings()
        upgrade_database()
        assert report == {"users": 3, "results": 1, "standings": 1}
        assert db.session.get(RankingStanding, (1, "clinical")).updated_at == timestamp
        assert db.session.get(Consultation, identifier).evaluation == source


def test_rebuild_removes_now_invalid_result_without_changing_source(app, rank_cases):
    with app.app_context():
        training = add_result(1, rank_cases[0], 70)
        training.evaluation = {**training.evaluation, "evaluationMode": "demo"}
        db.session.commit()
        rebuild_rankings()
        assert db.session.get(RankingResult, training.id) is None
        assert db.session.get(RankingStanding, (1, "clinical")) is None
        assert training.score == 70


def test_interrupted_initial_backfill_resumes_when_tables_already_exist(app, rank_cases, monkeypatch):
    from medlab import ranking

    with app.app_context():
        for user_id in (1, 2):
            add_result(user_id, rank_cases[0], 70, save=False)
        db.session.commit()
        original = ranking._recompute_user

        def interrupt_second(user_id):
            if user_id == 2:
                raise RuntimeError("Simulated process interruption")
            return original(user_id)

        monkeypatch.setattr(ranking, "_recompute_user", interrupt_second)
        with pytest.raises(RuntimeError, match="interruption"):
            upgrade_database()
        db.session.rollback()
        assert db.session.get(RankingState, BOOTSTRAP_KEY) is None
        assert db.session.get(RankingStanding, (1, "clinical")) is not None
        assert db.session.get(RankingStanding, (2, "clinical")) is None
        monkeypatch.setattr(ranking, "_recompute_user", original)
        upgrade_database()
        assert db.session.get(RankingState, BOOTSTRAP_KEY) is not None
        assert db.session.get(RankingStanding, (2, "clinical")) is not None
        assert db.session.scalar(select(db.func.count(RankingResult.consultation_id))) == 2


def test_inactive_users_are_removed_from_public_ranks(app, rank_cases):
    with app.app_context():
        for user_id, score in ((1, 80), (2, 100)):
            for case_id in rank_cases[:5]:
                add_result(user_id, case_id, score)
        db.session.get(User, 2).is_active = False
        db.session.commit()
        data = ranking_context(1, include_boards=True)
        assert len(data["leaderboards"]["clinical"]["entries"]) == 1
        assert data["personal_rankings"]["clinical"]["rank"] == 1


def test_display_name_and_mysql_schema(app):
    assert display_name("Beatriz Maria Ricciardi") == "Beatriz M. R."
    assert display_name("  Ana   da Silva ") == "Ana D. S."
    assert display_name("email@example.invalid") == "Estudante"
    with app.app_context():
        ddl = str(CreateTable(RankingStanding.__table__).compile(dialect=mysql.dialect()))
        assert "NUMERIC(5, 2)" in ddl and "ON DELETE CASCADE" in ddl


def test_context_without_migration_is_empty_and_read_only(app):
    with app.app_context():
        RankingStanding.__table__.drop(db.engine)
        result = ranking_context(1, include_boards=True)
        assert result["leaderboards"]["basic"]["minimum_cases"] == 5
        assert result["personal_rankings"]["clinical"]["rank"] is None
        assert not inspect(db.engine).has_table("ranking_standing")
        # Restore only the schema for fixture teardown.
        RankingStanding.__table__.create(db.engine)


def test_public_snapshot_is_read_only_private_names_and_personal_rank_is_session_only(app, client, login, rank_cases):
    with app.app_context():
        user = db.session.get(User, 1)
        user.name = "Beatriz Maria Ricciardi"
        for case_id in rank_cases[:5]:
            add_result(1, case_id, 80, "basic")
        before = db.session.scalar(select(db.func.count(RankingResult.consultation_id)))
    anonymous = client.get("/api/ranking?user_id=1")
    assert anonymous.status_code == 200 and anonymous.headers["Cache-Control"] == "no-store"
    entry = anonymous.json["leaderboards"]["basic"]["entries"][0]
    assert entry["display_name"] == "Beatriz M. R." and entry["is_current_user"] is False
    assert anonymous.json["personal_rankings"]["basic"]["rank"] is None
    assert "Ricciardi" not in anonymous.text and "aluna@example" not in anonymous.text
    login()
    personal = client.get("/api/ranking?user_id=2").json
    assert personal["personal_rankings"]["basic"]["rank"] == 1
    assert personal["leaderboards"]["basic"]["entries"][0]["is_current_user"] is True
    with app.app_context():
        assert db.session.scalar(select(db.func.count(RankingResult.consultation_id))) == before


def test_finishing_a_valid_consultation_persists_rank_in_same_transaction(app, client, start_session, monkeypatch):
    identifier = start_session()
    with app.app_context():
        db.session.add(Turn(consultation_id=identifier, request_id="ranking-finish", question="Quando começou?",
                            answer="Ontem.", intent="Início"))
        db.session.commit()

    class SyntheticAI:
        is_demo = False

        def evaluate(self, *_args, **_kwargs):
            return assessment([80] * 5)

    monkeypatch.setattr("medlab.web.get_ai_service", SyntheticAI)
    response = client.post(f"/sessao/{identifier}/finalizar", data={"diagnosis": "Hipótese sintética", "treatment": "Conduta de teste completa."})
    assert response.status_code == 302 and response.headers["Location"].endswith("/resultado")
    with app.app_context():
        training = db.session.get(Consultation, identifier)
        assert training.evaluation["evaluationMode"] == "ai"
        assert db.session.get(RankingResult, identifier).score == 80
        assert db.session.get(RankingStanding, (training.user_id, "clinical")).consultations == 1
    client.post(f"/sessao/{identifier}/finalizar", data={})
    assert client.get("/api/ranking").json["personal_rankings"]["clinical"]["consultations"] == 1


def test_demo_finish_never_creates_ranking_result(app, client, start_session):
    identifier = start_session()
    client.post(f"/sessao/{identifier}/mensagem", json={"question": "Quando começou?", "request_id": "ranking-demo-question"})
    client.post(f"/sessao/{identifier}/finalizar", data={"diagnosis": "Hipótese sintética", "treatment": "Conduta de teste completa."})
    with app.app_context():
        training = db.session.get(Consultation, identifier)
        assert training.status == "completed" and training.evaluation["evaluationMode"] == "demo"
        assert db.session.get(RankingResult, identifier) is None
