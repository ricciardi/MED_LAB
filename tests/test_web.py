"""Exercise authenticated workflows and persistence across HTTP requests."""

from copy import deepcopy
from datetime import datetime, timezone
import re
from unittest.mock import Mock

import pytest

from medlab.ai import AIError
from medlab.case_schema import SECTION_TITLES
from medlab.extensions import db
from medlab.models import ClinicalCase, Consultation, PerformedExam, Turn, User


def stored(app, model, identifier):
    with app.app_context():
        record = db.session.get(model, identifier)
        db.session.expunge(record)
        return record


def evaluation_payload():
    return {
        "score": 73,
        "summary": "Resumo sintético da avaliação de teste.",
        "sections": [
            {"key": key, "title": title, "score": 73,
             "feedback": "Feedback de teste.", "strengths": ["Ação observada."],
             "improvements": ["Melhoria de teste."], "evidence": ["Pergunta registrada."]}
            for key, title in SECTION_TITLES.items()
        ],
        "strengths": ["Ação observada."],
        "improvements": ["Melhoria de teste."],
        "next_steps": ["Revisar a consulta."],
    }


@pytest.fixture
def ai(monkeypatch):
    service = Mock()
    service.respond.return_value = {"answer": "O desconforto começou ontem.", "intent": "Início dos sintomas",
                                    "simulation_state": {"version": 1, "startedAfterTurn": 0, "details": []}}
    service.evaluate.return_value = evaluation_payload()
    monkeypatch.setattr("medlab.web.get_ai_service", lambda: service)
    return service


def test_registration_hashes_password_and_never_grants_admin(app, client):
    password = "Senha-somente-de-teste-2026!"
    response = client.post("/cadastro", data={
        "name": "Nova Pessoa", "email": "NOVA@example.invalid", "password": password,
        "password_confirm": password, "is_admin": "true",
    })
    assert response.status_code in (302, 303)
    with app.app_context():
        user = db.session.scalar(db.select(User).where(User.email == "nova@example.invalid"))
        assert user is not None and not user.is_admin
        assert user.password_hash != password and user.check_password(password)
    assert client.get("/painel").status_code == 302
    assert client.post("/entrar", data={"email": "nova@example.invalid", "password": password}).status_code == 302
    assert client.get("/painel").status_code == 200


def test_registration_rejects_mismatch_and_duplicate_email(app, client):
    bad = {"name": "Teste", "email": "nova@example.invalid", "password": "Teste-local-2026!", "password_confirm": "Diferente-2026!"}
    assert client.post("/cadastro", data=bad).status_code == 400
    bad.update(email="aluna@example.invalid", password_confirm=bad["password"])
    assert client.post("/cadastro", data=bad).status_code == 400
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(User.id))) == 3


def test_login_rejects_wrong_password_and_external_redirect(client):
    response = client.post("/entrar", data={"email": "aluna@example.invalid", "password": "incorreta"})
    assert response.status_code in (400, 401)
    assert client.get("/painel").status_code == 302
    response = client.post("/entrar?next=https://example.invalid/fora", data={
        "email": "aluna@example.invalid", "password": "Teste-local-2026!",
    })
    assert response.status_code in (302, 303)
    assert not response.headers["Location"].startswith("https://example.invalid")


def test_disabled_user_cannot_login(app, client):
    with app.app_context():
        user = db.session.scalar(db.select(User).where(User.email == "aluna@example.invalid"))
        user.is_active = False
        db.session.commit()
    response = client.post("/entrar", data={"email": "aluna@example.invalid", "password": "Teste-local-2026!"})
    assert response.status_code in (400, 401, 403)
    assert client.get("/painel").status_code == 302


def test_logout_is_post_only_and_removes_authenticated_session(client, login):
    login()
    assert client.get("/sair").status_code == 405
    assert client.post("/sair").status_code in (302, 303)
    assert client.get("/painel").status_code == 302


def test_admin_has_no_public_link_and_requires_role(client, login):
    assert "/gestao-teste" not in client.get("/").get_data(as_text=True)
    assert client.get("/gestao-medlab/login").status_code == 404
    assert client.get("/admin").status_code == 404
    assert client.get("/gestao-teste/").status_code in (302, 303, 404)
    login()
    assert "/gestao-teste" not in client.get("/painel").get_data(as_text=True)
    assert client.get("/gestao-teste/").status_code in (403, 404)
    assert client.post("/gestao-teste/casos/3/publicar").status_code in (403, 404)
    client.post("/sair")
    login("admin@example.invalid", admin=True)
    assert client.get("/gestao-teste/").status_code == 200


def test_case_catalog_does_not_reveal_drafts_or_answers(client, login):
    login()
    response = client.get("/casos")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Encontro clínico #00001" in body and "Encontro clínico #00002" in body
    assert "Cenário de teste 3" not in body
    assert "Diagnóstico reservado" not in body
    assert "Plano reservado" not in body
    assert "Critério reservado" not in body


def test_admin_publication_requires_review_and_can_be_repeated(app, client, login):
    login("admin@example.invalid", admin=True)
    with app.app_context():
        draft_id = db.session.scalar(db.select(ClinicalCase.id).where(ClinicalCase.is_published.is_(False)))
    endpoint = f"/gestao-teste/casos/{draft_id}/publicar"
    assert client.post(endpoint, data={}).status_code == 302
    assert not stored(app, ClinicalCase, draft_id).is_published
    assert client.post(endpoint, data={"review_confirmed": "yes"}).status_code == 302
    assert stored(app, ClinicalCase, draft_id).is_published
    assert client.post(endpoint, data={}).status_code == 302
    assert not stored(app, ClinicalCase, draft_id).is_published
    assert client.post(endpoint, data={"review_confirmed": "yes"}).status_code == 302
    assert stored(app, ClinicalCase, draft_id).is_published


def test_generated_case_is_saved_as_a_private_draft(app, client, login, case_data, monkeypatch):
    service = Mock()
    service.generate_case.return_value = case_data
    monkeypatch.setattr("medlab.ai.AIService", lambda config: service)
    login("admin@example.invalid", admin=True)
    response = client.post("/gestao-teste/casos/gerar", data={"specialty": "Clínica médica", "difficulty": "Iniciante"})
    assert response.status_code == 302
    with app.app_context():
        generated = db.session.scalar(db.select(ClinicalCase).where(ClinicalCase.slug.startswith("ia-")))
        assert generated is not None and generated.is_generated and not generated.is_published
        assert generated.data["reviewStatus"] == "review_required"
    service.generate_case.assert_called_once_with("Clínica médica", "Iniciante")


def test_generation_failure_saves_no_partial_case(app, client, login, monkeypatch):
    service = Mock()
    service.generate_case.side_effect = AIError("Falha temporária de teste.")
    monkeypatch.setattr("medlab.ai.AIService", lambda config: service)
    login("admin@example.invalid", admin=True)
    response = client.post("/gestao-teste/casos/gerar", data={"specialty": "Clínica médica", "difficulty": "Iniciante"})
    assert response.status_code == 302
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(ClinicalCase.id))) == 3


def test_start_keeps_private_case_snapshot_and_excludes_unpublished(app, client, start_session):
    session_id = start_session()
    consultation = stored(app, Consultation, session_id)
    with app.app_context():
        case = db.session.get(ClinicalCase, consultation.case_id)
        changed = deepcopy(case.data)
        changed["patientProfile"]["chief_complaint"] = "Conteúdo alterado após abrir."
        case.data = changed
        db.session.commit()
    assert stored(app, Consultation, session_id).case_snapshot["patientProfile"]["chief_complaint"] == "Estou com um desconforto desde ontem."
    body = client.get(f"/sessao/{session_id}").get_data(as_text=True)
    assert "Diagnóstico reservado" not in body and "Critério reservado" not in body
    with app.app_context():
        unpublished = db.session.scalar(db.select(ClinicalCase).where(ClinicalCase.is_published.is_(False)))
        unpublished_id = unpublished.id
    response = client.post("/casos/iniciar", data={"case_id": unpublished_id})
    assert response.status_code in (302, 303)
    assert response.headers["Location"].endswith("/casos")
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Consultation.id))) == 1


def test_random_case_only_uses_published_cases(app, client, login):
    login()
    selected = []
    for _ in range(4):
        response = client.post("/casos/iniciar", data={})
        assert response.status_code in (302, 303)
        consultation_id = response.headers["Location"].rstrip("/").split("/")[-1]
        consultation = stored(app, Consultation, consultation_id)
        assert stored(app, ClinicalCase, consultation.case_id).is_published
        selected.append(consultation.case_id)
    assert selected[0] != selected[1], "An unvisited case must be preferred while one is available."


@pytest.mark.parametrize("path,method,payload", [
    ("", "get", None),
    ("/resultado", "get", None),
    ("/mensagem", "post", {"question": "Quando começou?", "request_id": "invasao-1"}),
    ("/exame", "post", {"name": "Sinais vitais", "type": "physical_exam"}),
    ("/finalizar", "post", {"diagnosis": "Outra", "differential": "Outra", "treatment": "Outro"}),
])
def test_consultation_is_private_to_owner(app, client, login, start_session, ai, path, method, payload):
    consultation_id = start_session()
    client.post("/sair")
    login("colega@example.invalid")
    kwargs = {"json": payload} if payload and path != "/finalizar" else ({"data": payload} if payload else {})
    response = getattr(client, method)(f"/sessao/{consultation_id}{path}", **kwargs)
    assert response.status_code == 404
    ai.respond.assert_not_called()
    ai.evaluate.assert_not_called()
    assert stored(app, Consultation, consultation_id).status == "active"


def test_messages_persist_and_request_retry_is_idempotent(app, client, start_session, ai):
    consultation_id = start_session()
    payload = {"question": "Quando começou?", "request_id": "mensagem-teste-1"}
    endpoint = f"/sessao/{consultation_id}/mensagem"
    first = client.post(endpoint, json=payload)
    assert first.status_code == 200
    assert "ontem" in first.get_json()["answer"]
    repeated = client.post(endpoint, json=payload)
    assert repeated.status_code == 200
    assert repeated.get_json()["answer"] == first.get_json()["answer"]
    assert client.post(endpoint, json={**payload, "question": "Outra pergunta?"}).status_code == 409
    ai.respond.assert_called_once()
    with app.app_context():
        rows = db.session.scalars(db.select(Turn).where(Turn.consultation_id == consultation_id)).all()
        assert len(rows) == 1
        assert rows[0].question == payload["question"]
        assert rows[0].intent == "Início dos sintomas"
    client.post("/sair")
    client.post("/entrar", data={"email": "aluna@example.invalid", "password": "Teste-local-2026!"})
    assert "O desconforto começou ontem." in client.get(f"/sessao/{consultation_id}").get_data(as_text=True)


def test_ai_failure_keeps_turn_empty_and_allows_same_request_retry(app, client, start_session, ai):
    consultation_id = start_session()
    ai.respond.side_effect = [AIError("Serviço temporariamente indisponível."), ai.respond.return_value]
    endpoint = f"/sessao/{consultation_id}/mensagem"
    payload = {"question": "Quando começou?", "request_id": "tentativa-repetivel"}
    failed = client.post(endpoint, json=payload)
    assert failed.status_code == 503 and failed.get_json()["error"]
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Turn.id))) == 0
    failed_session = stored(app, Consultation, consultation_id)
    assert failed_session.busy_since is None and failed_session.lock_token is None
    assert client.post(endpoint, json=payload).status_code == 200
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Turn.id))) == 1


def test_busy_consultation_rejects_concurrent_message(app, client, start_session, ai):
    consultation_id = start_session()
    with app.app_context():
        consultation = db.session.get(Consultation, consultation_id)
        consultation.busy_since = datetime.now(timezone.utc).replace(tzinfo=None)
        consultation.lock_token = "another-operation"
        db.session.commit()
    response = client.post(f"/sessao/{consultation_id}/mensagem", json={"question": "Quando começou?", "request_id": "concorrente"})
    assert response.status_code == 409
    ai.respond.assert_not_called()


def test_exams_are_case_grounded_and_not_duplicated(app, client, start_session):
    consultation_id = start_session()
    endpoint = f"/sessao/{consultation_id}/exame"
    payload = {"name": "Sinais vitais", "type": "physical_exam"}
    response = client.post(endpoint, json=payload)
    assert response.status_code == 200
    assert response.get_json()["result"] == "Resultado físico de teste."
    assert client.post(endpoint, json=payload).status_code == 200
    assert client.post(endpoint, json={"name": "Exame inexistente", "type": "physical_exam"}).status_code in (400, 404)
    with app.app_context():
        rows = db.session.scalars(db.select(PerformedExam)).all()
        assert len(rows) == 1 and rows[0].result == "Resultado físico de teste."


def test_evaluation_saves_and_reopening_does_not_call_ai(app, client, start_session, ai):
    consultation_id = start_session()
    client.post(f"/sessao/{consultation_id}/mensagem", json={"question": "Quando começou?", "request_id": "pre-avaliacao"})
    response = client.post(f"/sessao/{consultation_id}/finalizar", data={
        "diagnosis": "Hipótese fictícia de teste", "differential": "Alternativa fictícia", "treatment": "Plano fictício de teste",
    })
    assert response.status_code in (302, 303)
    consultation = stored(app, Consultation, consultation_id)
    assert consultation.status == "completed" and consultation.completed_at is not None
    assert consultation.score == 73 and consultation.evaluation == evaluation_payload()
    assert consultation.diagnosis == "Hipótese fictícia de teste"
    assert consultation.differential == "Alternativa fictícia"
    assert consultation.treatment == "Plano fictício de teste"
    for _ in range(2):
        result = client.get(f"/sessao/{consultation_id}/resultado")
        assert result.status_code == 200
        assert "Resumo sintético" in result.get_data(as_text=True)
    assert client.post(f"/sessao/{consultation_id}/finalizar", data={
        "diagnosis": "Uma alteração tardia", "treatment": "Não deve reavaliar a consulta",
    }).status_code in (302, 303)
    ai.evaluate.assert_called_once()
    assert client.get("/historico").status_code == 200
    assert client.post(f"/sessao/{consultation_id}/mensagem", json={"question": "Mais?", "request_id": "depois-da-avaliacao"}).status_code == 409


def test_limited_feedback_is_visible_with_rubric_and_reference(client, start_session, ai):
    consultation_id = start_session()
    client.post(f"/sessao/{consultation_id}/mensagem", json={"question": "Quando começou?", "request_id": "limited-feedback"})
    payload = evaluation_payload()
    payload["verification"] = {"status": "limited", "message": "Comentários não confirmados foram retirados."}
    payload["sections"][0].update(feedbackLimited=True, reviewCriteria=["Critério reservado de teste."],
        feedback="Feedback limitado nesta dimensão.", strengths=[], improvements=[])
    ai.evaluate.return_value = payload
    client.post(f"/sessao/{consultation_id}/finalizar", data={
        "diagnosis": "Hipótese fictícia", "treatment": "Plano fictício de teste",
    })
    result = client.get(f"/sessao/{consultation_id}/resultado")
    html = result.get_data(as_text=True)
    assert result.status_code == 200
    assert html.count("data-feedback-limited") == 1
    assert "Critérios da rubrica para revisar" in html and "Critério reservado de teste." in html
    assert "Diagnóstico reservado do cenário" in html and "Comentários não confirmados foram retirados." in html


def test_failed_evaluation_does_not_complete_or_save_a_fake_score(app, client, start_session, ai):
    consultation_id = start_session()
    client.post(f"/sessao/{consultation_id}/mensagem", json={"question": "Quando começou?", "request_id": "pre-falha"})
    ai.evaluate.side_effect = AIError("Não foi possível avaliar agora.")
    response = client.post(f"/sessao/{consultation_id}/finalizar", data={
        "diagnosis": "Hipótese fictícia", "differential": "Alternativa fictícia", "treatment": "Plano fictício",
    })
    assert response.status_code in (302, 303, 503)
    consultation = stored(app, Consultation, consultation_id)
    assert consultation.status == "active"
    assert consultation.evaluation is None and consultation.score is None
    assert consultation.diagnosis == "Hipótese fictícia" and consultation.treatment == "Plano fictício"
    assert consultation.busy_since is None and consultation.lock_token is None
    ai.evaluate.side_effect = None
    assert client.post(f"/sessao/{consultation_id}/finalizar", data={
        "diagnosis": "Hipótese fictícia", "differential": "Alternativa fictícia", "treatment": "Plano fictício",
    }).status_code in (302, 303)
    assert stored(app, Consultation, consultation_id).status == "completed"


def test_complete_demo_workflow_uses_real_ai_service_contract(app, client, login, start_session):
    """Keep this flow free of AIService mocks so route/service schemas are tested together."""
    assert app.config["DEMO_MODE"] is True and not app.config["DEEPSEEK_API_KEY"]
    consultation_id = start_session()
    response = client.post(f"/sessao/{consultation_id}/mensagem", json={
        "question": "Quando começou o desconforto?", "request_id": "contrato-demo-real",
    })
    assert response.status_code == 200, response.get_data(as_text=True)
    assert response.get_json()["answer"] and response.get_json()["intent"]
    response = client.post(f"/sessao/{consultation_id}/exame", json={
        "name": "Sinais vitais", "type": "physical_exam",
    })
    assert response.status_code == 200
    assert response.get_json()["result"] == "Resultado físico de teste."
    submitted = {
        "diagnosis": "Diagnóstico reservado do cenário",
        "differential": "Diferencial fictício",
        "treatment": "Plano reservado do cenário de teste.",
    }
    completed = client.post(f"/sessao/{consultation_id}/finalizar", data=submitted)
    result_path = f"/sessao/{consultation_id}/resultado"
    assert completed.status_code == 302 and completed.headers["Location"].endswith(result_path)
    consultation = stored(app, Consultation, consultation_id)
    assert consultation.status == "completed" and consultation.completed_at is not None
    assert consultation.differential == submitted["differential"]
    assert len(consultation.evaluation["sections"]) == 6
    assert {section["key"] for section in consultation.evaluation["sections"]} == set(SECTION_TITLES)
    assert "demonstra" in consultation.evaluation["summary"].casefold()
    result = client.get(result_path)
    assert result.status_code == 200
    assert all(title in result.get_data(as_text=True) for title in SECTION_TITLES.values())
    client.post("/sair")
    login()
    history = client.get("/historico")
    assert history.status_code == 200 and result_path in history.get_data(as_text=True)
    assert client.get(result_path).status_code == 200


def test_csrf_protects_forms_and_json(app, client, start_session, ai):
    consultation_id = start_session()
    app.config["WTF_CSRF_ENABLED"] = True
    assert client.post("/sair").status_code == 400
    endpoint = f"/sessao/{consultation_id}/mensagem"
    payload = {"question": "Quando começou?", "request_id": "csrf-teste"}
    assert client.post(endpoint, json=payload).status_code == 400
    ai.respond.assert_not_called()
    page = client.get(f"/sessao/{consultation_id}").get_data(as_text=True)
    match = re.search(r'name="csrf-token" content="([^"]+)"', page)
    if not match:
        match = re.search(r'name="csrf_token" value="([^"]+)"', page)
    assert match, "The page must provide a CSRF token to legitimate form/JSON clients."
    assert client.post(endpoint, json=payload, headers={
        "X-CSRFToken": match.group(1), "Referer": "https://example.invalid/fora",
    }).status_code == 400
    assert client.post(endpoint, json=payload, headers={
        "X-CSRFToken": match.group(1), "Referer": f"https://localhost/sessao/{consultation_id}",
    }).status_code == 200


def test_production_without_key_does_not_create_demo_consultation(app, client, login, ai):
    app.config.update(DEMO_MODE=False, DEEPSEEK_API_KEY="")
    login()
    response = client.post("/casos/iniciar", data={})
    assert response.status_code == 302 and response.headers["Location"].endswith("/casos")
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Consultation.id))) == 0
    ai.respond.assert_not_called()


def test_daily_ai_limit_releases_lock_and_does_not_call_provider(app, client, start_session, ai):
    app.config["MAX_DAILY_AI_REQUESTS"] = 1
    consultation_id = start_session()
    endpoint = f"/sessao/{consultation_id}/mensagem"
    assert client.post(endpoint, json={"question": "Quando começou?", "request_id": "quota-primeira"}).status_code == 200
    assert client.post(endpoint, json={"question": "Como se sente?", "request_id": "quota-segunda"}).status_code == 429
    assert stored(app, Consultation, consultation_id).busy_since is None
    ai.respond.assert_called_once()


def test_init_db_is_repeatable_and_preserves_existing_data(app):
    runner = app.test_cli_runner()
    first = runner.invoke(args=["init-db"])
    assert first.exit_code == 0, first.output
    with app.app_context():
        count = db.session.scalar(db.select(db.func.count(ClinicalCase.id)))
        original = db.session.scalar(db.select(ClinicalCase).where(ClinicalCase.slug == "cenario-teste-1"))
        original.title = "Título local preservado"
        db.session.commit()
    second = runner.invoke(args=["init-db"])
    assert second.exit_code == 0, second.output
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(ClinicalCase.id))) == count == 11
        assert db.session.scalar(db.select(ClinicalCase.title).where(ClinicalCase.slug == "cenario-teste-1")) == "Título local preservado"


def test_admin_cli_uses_password_prompt_and_can_reset_it(app):
    runner = app.test_cli_runner()
    password, replacement = "Senha-admin-de-teste!", "Outra-senha-de-teste!"
    created = runner.invoke(args=["create-admin", "--email", "operacao@example.invalid", "--name", "Operação Teste"], input=f"{password}\n{password}\n")
    assert created.exit_code == 0, created.output
    assert password not in created.output
    with app.app_context():
        admin = db.session.scalar(db.select(User).where(User.email == "operacao@example.invalid"))
        assert admin.is_admin and admin.check_password(password)
    reset = runner.invoke(args=["reset-password", "operacao@example.invalid"], input=f"{replacement}\n{replacement}\n")
    assert reset.exit_code == 0, reset.output
    assert replacement not in reset.output
    with app.app_context():
        admin = db.session.scalar(db.select(User).where(User.email == "operacao@example.invalid"))
        assert admin.check_password(replacement) and not admin.check_password(password)


@pytest.mark.parametrize("email,admin,protected,denied", [
    ("aluna@example.invalid", False, "/painel", 302),
    ("admin@example.invalid", True, "/gestao-teste/", 404),
])
def test_password_reset_revokes_existing_and_replayed_sessions(app, client, login, email, admin, protected, denied):
    login(email, admin=admin)
    assert client.get(protected).status_code == 200
    original_cookie = client.get_cookie(app.config["SESSION_COOKIE_NAME"]).value
    new_password = "Senha-alterada-para-teste!"
    reset = app.test_cli_runner().invoke(
        args=["reset-password", email], input=f"{new_password}\n{new_password}\n",
    )
    assert reset.exit_code == 0, reset.output
    assert client.get(protected).status_code == denied
    login_url = "/gestao-teste/login" if admin else "/entrar"
    assert client.post(login_url, data={"email": email, "password": "Teste-local-2026!"}).status_code == 400
    assert client.post(login_url, data={"email": email, "password": new_password}).status_code == 302
    assert client.get(protected).status_code == 200
    replay = app.test_client()
    replay.set_cookie(app.config["SESSION_COOKIE_NAME"], original_cookie)
    assert replay.get(protected).status_code == denied
