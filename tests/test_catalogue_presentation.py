"""Student discovery labels availability without disclosing examination results."""
from html import unescape
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse
import re

from flask import render_template
from flask_login import login_user
import pytest

from medlab.extensions import db
from medlab.models import User


def public_card(**changes):
    return {"id": 123, "title": "Encontro clínico #00123", "specialty": "Clínica médica",
            "summary": "Conduza a entrevista e construa sua hipótese.", "case_type": "Primeira consulta",
            "case_types": ["Primeira consulta"], "difficulty": "Intermediário", "estimated_minutes": 15,
            "exam_result_count": 0, "exam_image_count": 0, **changes}


def render_catalogue(app, **changes):
    context = {"cases": [public_card()], "selected_specialty": "Clínica médica",
        "selected_case_type": "Primeira consulta", "selected_difficulty": "Não classificada",
        "selected_exams": "results", "search_query": "clínica", "specialties": ["Clínica médica"],
        "case_types": ["Primeira consulta"], "difficulties": ["Não classificada"],
        "pagination": SimpleNamespace(total=13, page=1, per_page=12, pages=2,
                                      has_prev=False, has_next=True, next_num=2), **changes}
    with app.test_request_context("/casos"):
        login_user(db.session.scalar(db.select(User).where(User.email == "aluna@example.invalid")))
        return render_template("cases.html", **context)


@pytest.mark.parametrize("selection", ["all", "results", "images"])
def test_exam_filter_survives_random_direct_start_and_pagination(app, selection):
    body = render_catalogue(app, selected_exams=selection)
    assert re.search(rf'<option value="{selection}"\s+selected', body)
    forms = re.findall(r'<form method="post" action="/casos/iniciar">(.*?)</form>', body, re.S)
    assert len(forms) == 2
    for form in forms:
        inputs = dict(re.findall(r'name="([^"]+)" value="([^"]*)"', form))
        assert inputs["exams"] == selection
        assert inputs["specialty"] == "Clínica médica"
        assert inputs["case_type"] == "Primeira consulta"
        assert inputs["difficulty"] == "Não classificada"
        assert inputs["q"] == "clínica"
    links = re.findall(r'href="([^"]+page=2[^"]*)"', body)
    assert links and parse_qs(urlparse(unescape(links[0])).query)["exams"] == [selection]
    assert 'aria-describedby="catalog-exam-help"' in body
    assert "Imagens em preparação não entram no filtro" in body


def test_card_shows_only_public_availability_and_estimates(app):
    card = public_card(exam_result_count=3, exam_image_count=0, difficulty_estimated=True,
        case_types=["Dor torácica", "Sintomas respiratórios", "Febre e sintomas gerais", "Quarto tipo oculto"],
        auxiliaryLabs={"Exame reservado": "Resultado reservado"},
        diagnoses={"mainDiagnosis": {"disease": "Diagnóstico reservado"}})
    body = render_catalogue(app, cases=[card])
    assert "3 exames complementares com resultados" in body and "data-case-exam-results" in body
    assert "data-case-exam-images" not in body
    assert "Dificuldade estimada" in body and "não a gravidade clínica" in body
    assert all(item in body for item in card["case_types"][:3])
    assert all(secret not in body for secret in ("Quarto tipo oculto", "Exame reservado", "Resultado reservado", "Diagnóstico reservado"))
    difficulty = body.split('id="difficulty"', 1)[1].split("</select>", 1)[0]
    assert 'value="Não classificada" selected' in difficulty
    assert 'value="Iniciante"' not in difficulty  # Options come from the server context.


def test_card_accepts_older_metadata_without_claiming_images(app):
    card = public_card(difficulty="")
    for key in ("case_types", "exam_result_count", "exam_image_count"):
        card.pop(key)
    body = render_catalogue(app, cases=[card])
    assert "Não classificada" in body and "Primeira consulta" in body
    assert "data-case-exam-results" not in body and "data-case-exam-images" not in body
    assert "Dificuldade estimada</small>" not in body


def test_verified_image_availability_is_distinct_from_result_count(app):
    body = render_catalogue(app, cases=[public_card(exam_result_count=1, exam_image_count=1)])
    assert "1 exame complementar com resultado" in body and "1 imagem disponível" in body
    assert "data-case-exam-results" in body and "data-case-exam-images" in body


def test_facets_show_real_counts_including_zero_and_apply_selects_immediately(app):
    body = render_catalogue(app, case_types=["Primeira consulta", "Dor torácica"],
        type_counts={"Primeira consulta": 0, "Dor torácica": 23},
        difficulty_counts={"Não classificada": 7})
    assert '<option value="Primeira consulta" selected>Primeira consulta (0)</option>' in body
    assert '>Dor torácica (23)</option>' in body
    assert '>Não classificada (7)</option>' in body
    selects = re.findall(r'<select ([^>]+)>', body)
    assert len(selects) == 4 and all('data-auto-submit' in attrs for attrs in selects)
    assert 'type="submit" class="button button-primary">Aplicar filtros' in body


@pytest.mark.parametrize("counts", [{}, None])
def test_absent_facet_counts_do_not_invent_zero(app, counts):
    body = render_catalogue(app, type_counts=counts, difficulty_counts=counts)
    assert '>Primeira consulta</option>' in body and '>Não classificada</option>' in body
    assert 'Primeira consulta (0)' not in body and 'Não classificada (0)' not in body


def test_consultation_separates_available_from_requested_and_reveals_only_requested_result(app, client, start_session):
    session_id = start_session()
    body = client.get(f"/sessao/{session_id}").text
    tab = body.split('id="exams-tab"', 1)[1].split("</button>", 1)[0]
    assert '<span class="tab-count">2</span>' in tab and "disponíveis" in tab
    assert '<strong id="exam-count">0</strong> solicitados' in body
    assert body.count('id="exam-count"') == 1
    assert "Clique em <strong>Solicitar</strong>" in body
    assert "Mencionar um exame no chat não registra o pedido." in body
    assert "Resultado físico de teste." not in body and "Resultado laboratorial de teste." not in body
    response = client.post(f"/sessao/{session_id}/exame", json={"name": "Sinais vitais", "type": "physical_exam"})
    assert response.status_code == 200
    body = client.get(f"/sessao/{session_id}").text
    assert '<strong id="exam-count">1</strong> solicitados' in body
    assert "Resultado físico de teste." in body and "Resultado laboratorial de teste." not in body
    tab = body.split('id="exams-tab"', 1)[1].split("</button>", 1)[0]
    assert '<span class="tab-count">2</span>' in tab
