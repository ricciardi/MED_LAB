"""Student discovery must neither expose answer keys nor discard existing data."""

from copy import deepcopy
import re
import sqlite3
from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy import inspect

from medlab import create_app
from medlab.extensions import db
from medlab.models import ClinicalCase, Consultation, PerformedExam, Turn, User


def card_ids(response):
    assert response.status_code == 200
    return [int(value) for value in re.findall(r'name="case_id" value="(\d+)"', response.get_data(as_text=True))]


def add_case(payload, *, index, specialty="Cardiologia", kind="Dor torácica", difficulty="Intermediário", published=True):
    data = deepcopy(payload)
    data.update(caseId=f"catalogue-{index}", specialty=specialty, caseType=kind, difficulty=difficulty)
    case = ClinicalCase(slug=data["caseId"], title=data["caseTitle"], specialty=specialty,
                        case_type=kind, difficulty=difficulty, summary=data["summary"],
                        data=data, is_published=published, is_generated=False)
    db.session.add(case)
    db.session.flush()
    return case.id


def test_student_pages_never_render_private_titles_summaries_or_sources(app, login):
    private = ("RespostaDiagnosticaPrivada", "ResumoReveladorPrivado", "ArtigoReveladorPrivado", "AntecedenteReservadoPrivado")
    with app.app_context():
        case = db.session.scalar(db.select(ClinicalCase).where(ClinicalCase.slug == "cenario-teste-1"))
        data = deepcopy(case.data)
        case.title = data["caseTitle"] = private[0]
        case.summary = data["summary"] = private[1]
        data["references"][0]["title"] = private[2]
        data["patientProfile"]["past_medical_history"] = private[3]
        case.data = data
        identifier = case.id
        db.session.commit()
    client = login()
    started = client.post("/casos/iniciar", data={"case_id": identifier})
    assert started.status_code == 302
    for url in ("/casos", "/painel", "/historico", started.headers["Location"]):
        response = client.get(url)
        assert response.status_code == 200
        text = response.get_data(as_text=True)
        if url in ("/casos", "/painel", "/historico"):
            assert f"Encontro clínico #{identifier:05d}" in text
        for secret in private:
            assert secret not in text


@pytest.mark.parametrize("secret", ["RespostaDiagnosticaPrivada", "ResumoReveladorPrivado", "ArtigoReveladorPrivado", "%", "_"])
def test_search_cannot_be_used_to_probe_private_content_or_sql_wildcards(app, login, secret):
    with app.app_context():
        case = db.session.scalar(db.select(ClinicalCase).where(ClinicalCase.slug == "cenario-teste-1"))
        data = deepcopy(case.data)
        case.title = "RespostaDiagnosticaPrivada"
        case.summary = "ResumoReveladorPrivado"
        data["diagnoses"]["mainDiagnosis"]["disease"] = "RespostaDiagnosticaPrivada"
        data["references"][0]["title"] = "ArtigoReveladorPrivado"
        case.data = data
        db.session.commit()
    client = login()
    assert card_ids(client.get("/casos", query_string={"q": secret})) == []
    response = client.post("/casos/iniciar", data={"q": secret})
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/casos")
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Consultation.id))) == 0


def test_random_selection_respects_same_combined_filters_as_library(app, login, case_data):
    with app.app_context():
        expected = {add_case(case_data, index=1), add_case(case_data, index=2)}
        add_case(case_data, index=3, published=False)
        add_case(case_data, index=4, specialty="Pneumologia")
        add_case(case_data, index=5, kind="Sintomas respiratórios")
        add_case(case_data, index=6, difficulty="Avançado")
        db.session.commit()
    client = login()
    filters = {"specialty": "Cardiologia", "case_type": "Dor torácica", "difficulty": "Intermediário", "q": "Cardio"}
    assert set(card_ids(client.get("/casos", query_string=filters))) == expected
    selected = []
    for _ in range(2):
        response = client.post("/casos/iniciar", data=filters)
        assert response.status_code == 302
        session_id = response.headers["Location"].rsplit("/", 1)[1]
        with app.app_context():
            selected.append(db.session.get(Consultation, session_id).case_id)
    assert set(selected) == expected


def test_catalogue_pagination_preserves_filters_and_never_includes_drafts(app, login, case_data):
    with app.app_context():
        expected = {add_case(case_data, index=index) for index in range(25)}
        add_case(case_data, index=99, published=False)
        add_case(case_data, index=100, specialty="Pediatria")
        db.session.commit()
    client = login()
    filters = {"specialty": "Cardiologia", "case_type": "Dor torácica", "difficulty": "Intermediário", "q": "Cardio"}
    pages = [client.get("/casos", query_string={**filters, "page": page}) for page in (1, 2, 3)]
    identifiers = [card_ids(page) for page in pages]
    assert [len(ids) for ids in identifiers] == [12, 12, 1]
    assert len({identifier for ids in identifiers for identifier in ids}) == 25
    assert {identifier for ids in identifiers for identifier in ids} == expected
    import html
    links = re.findall(r'href="([^"]+page=2[^"]*)"', pages[0].get_data(as_text=True))
    assert links
    query = parse_qs(urlparse(html.unescape(links[0])).query)
    assert all(query[key] == [value] for key, value in filters.items())
    assert card_ids(client.get("/casos", query_string={**filters, "page": 99})) == []


def test_upgrade_sqlite_previous_schema_preserves_users_and_completed_history(tmp_path, case_data):
    """Build the old schema, then run the real CLI twice against a persistent DB."""
    path = tmp_path / "previous.sqlite3"
    app = create_app({"TESTING": True, "SECRET_KEY": "isolated-migration-test-secret-with-more-than-32-characters",
                      "SQLALCHEMY_DATABASE_URI": "sqlite:///" + str(path), "DEMO_MODE": True,
                      "DEEPSEEK_API_KEY": "", "WTF_CSRF_ENABLED": False, "SESSION_COOKIE_SECURE": False})
    snapshot = deepcopy(case_data)
    evaluation = {"score": 81, "summary": "Avaliação histórica preservada"}
    with app.app_context():
        db.create_all()
        user = User(name="Pessoa teste", email="migration@example.invalid")
        user.set_password("Senha-isolada-2026!")
        db.session.add(user)
        data = deepcopy(case_data)
        data["specialty"] = "Clínica Médica"
        case = ClinicalCase(slug="legacy-case", title="Título histórico", specialty="Clínica Médica",
                            summary="Resumo histórico", difficulty="Iniciante", data=data, is_published=True)
        db.session.add(case)
        db.session.flush()
        training = Consultation(user_id=user.id, case_id=case.id, case_snapshot=snapshot,
                                status="completed", score=81, evaluation=evaluation,
                                diagnosis="Hipótese preservada", treatment="Plano preservado")
        db.session.add(training)
        db.session.flush()
        db.session.add(Turn(consultation_id=training.id, question="Pergunta antiga?", answer="Resposta antiga.",
                            intent="Queixa principal", request_id="legacy-request-id"))
        db.session.add(PerformedExam(consultation_id=training.id, name="Sinais vitais", exam_type="physical_exam", result="Resultado antigo."))
        db.session.commit()
        identifiers = (user.id, case.id, training.id)
        old_hash = user.password_hash
        db.session.remove()
        db.engine.dispose()
    # Remove only the new field/index, retaining the old table's constraints.
    with sqlite3.connect(path) as connection:
        connection.execute("DROP INDEX ix_clinical_case_case_type")
        connection.execute("ALTER TABLE clinical_case DROP COLUMN case_type")
    for _ in range(2):
        result = app.test_cli_runner().invoke(args=["upgrade-db"])
        assert result.exit_code == 0, result.output
    with app.app_context():
        assert "case_type" in {column["name"] for column in inspect(db.engine).get_columns("clinical_case")}
        assert "ix_clinical_case_case_type" in {index["name"] for index in inspect(db.engine).get_indexes("clinical_case")}
        user = db.session.get(User, identifiers[0])
        case = db.session.get(ClinicalCase, identifiers[1])
        training = db.session.get(Consultation, identifiers[2])
        assert user.password_hash == old_hash
        assert user.check_password("Senha-isolada-2026!")
        assert case.specialty == "Clínica médica"
        assert case.case_type == case.data["caseType"] == "Primeira consulta"
        assert case.title == "Título histórico" and case.summary == "Resumo histórico"
        assert training.case_snapshot == snapshot
        assert training.score == 81 and training.evaluation == evaluation and training.status == "completed"
        assert training.diagnosis == "Hipótese preservada" and training.treatment == "Plano preservado"
        assert [(turn.question, turn.answer) for turn in training.turns] == [("Pergunta antiga?", "Resposta antiga.")]
        assert training.exams[0].result == "Resultado antigo."
        db.session.remove()
        db.engine.dispose()


def test_dashboard_aggregates_all_scores_but_loads_only_five_recent_snapshots(app, login, case_data):
    from datetime import timedelta
    from flask import template_rendered
    from sqlalchemy import event
    from medlab.models import utcnow
    with app.app_context():
        student = db.session.scalar(db.select(User).where(User.email == 'aluna@example.invalid'))
        other = db.session.scalar(db.select(User).where(User.email == 'colega@example.invalid'))
        case = db.session.scalar(db.select(ClinicalCase))
        for index in range(37):
            db.session.add(Consultation(id=f'dashboard-{index:03d}',user_id=student.id,case_id=case.id,
                case_snapshot=case_data,status='completed' if index < 13 else 'active',
                score=55 if index < 12 else None,created_at=utcnow()-timedelta(minutes=index)))
        db.session.add(Consultation(user_id=other.id,case_id=case.id,case_snapshot=case_data,status='completed',score=100))
        db.session.commit()
    client = login()
    loaded, rendered = [], []
    def record_load(training, context): loaded.append(training.id)
    def record_context(sender, template, context, **extra): rendered.append(context)
    event.listen(Consultation,'load',record_load)
    template_rendered.connect(record_context, app)
    try:
        response = client.get('/painel')
    finally:
        event.remove(Consultation,'load',record_load)
        template_rendered.disconnect(record_context, app)
    assert response.status_code == 200
    assert loaded == [f'dashboard-{index:03d}' for index in range(5)]
    # A qualitative completion still counts, but must not dilute the mean.
    assert rendered[0]['stats'] == {'completed':13,'active':24,'total':37,'average_score':55}
    assert len(rendered[0]['recent_sessions']) == 5


def test_upgrade_limits_database_results_to_pages_and_preserves_transaction(app, case_data, monkeypatch):
    from sqlalchemy import event
    import medlab.cli as commands
    monkeypatch.setattr(commands, 'restore_bundled_inventory', lambda: None)
    monkeypatch.setattr(commands, 'restore_bundled_drafts', lambda: None)
    with app.app_context():
        for index in range(205):
            data = deepcopy(case_data); data.pop('caseType', None)
            db.session.add(ClinicalCase(id=1000+index*2,slug=f'upgrade-bounded-{index}',title='Synthetic upgrade',
                specialty='Clínica médica',case_type='Outros sintomas',difficulty='Iniciante',summary='Synthetic',
                data=data,is_published=False,is_generated=False))
        db.session.commit(); engine=db.engine
    queries=[]
    def observe(connection,cursor,statement,parameters,context,executemany):
        if statement.startswith('SELECT') and 'clinical_case.data' in statement and 'FROM clinical_case' in statement:
            queries.append(statement)
    event.listen(engine,'before_cursor_execute',observe)
    try:
        result=app.test_cli_runner().invoke(args=['upgrade-db'])
    finally:
        event.remove(engine,'before_cursor_execute',observe)
    assert result.exit_code == 0, result.output
    assert len(queries)==3 and all('LIMIT' in statement for statement in queries)
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(ClinicalCase.id))) == 208
        rows=db.session.scalars(db.select(ClinicalCase).where(ClinicalCase.id>=1000)).all()
        assert all(row.data['caseType']=='Primeira consulta' and row.case_type=='Primeira consulta' for row in rows)
