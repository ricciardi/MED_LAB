"""Ranking presentation: explicit synthetic payloads and an isolated test app."""

from copy import deepcopy
from types import SimpleNamespace

from flask import render_template, render_template_string


def boards(minimum=5):
    return {
        key: {"title": title, "minimum_cases": minimum, "entries": []}
        for key, title in (("basic", "Casos Básicos"), ("clinical", "Casos Clínicos"))
    }


def positions(minimum=5):
    return {
        key: {"rank": None, "average_score": None, "distinct_cases": 0,
              "consultations": 0, "eligible": False, "remaining_cases": minimum,
              "minimum_cases": minimum}
        for key in ("basic", "clinical")
    }


def participant(rank, name, average=82.3, count=5):
    return {"rank": rank, "display_name": name, "average_score": average,
            "distinct_cases": count, "consultations": count + 3, "is_current_user": False}


def landing(app, value):
    with app.test_request_context("/"):
        return render_template("landing.html", leaderboards=value)


def personal(app, value, authenticated=True):
    with app.test_request_context("/painel"):
        return render_template_string(
            "{% from 'icons.html' import icon %}{% include 'personal_rankings.html' %}",
            personal_rankings=value,
            current_user=SimpleNamespace(is_authenticated=authenticated),
        )


def test_empty_boards_explain_threshold_without_inventing_people_or_scores(app):
    html = landing(app, boards())
    assert 'id="ranking-basic"' in html and 'id="ranking-clinical"' in html
    assert html.count("Esta trajetória está começando.") == 2
    assert html.count("pelo menos 5 casos distintos avaliados") == 2
    assert 'class="ranking-table"' not in html
    assert "média de todas as tentativas válidas de cada caso" in html
    assert "Avaliações pendentes e consultas de demonstração ficam de fora" in html
    assert "1º, 1º e 3º" in html


def test_public_rows_use_competition_ranks_and_only_abbreviated_names(app):
    payload = boards()
    payload["basic"]["entries"] = [participant(1, "Ana S."), participant(1, "Bia M."), participant(3, "Caio P.", 71.25)]
    payload["basic"]["entries"][0].update(full_name="PRIVATE FULL NAME", email="secret@example.invalid")
    html = landing(app, payload)
    assert html.count('ranking-place-leading">1º</span>') == 2
    assert 'ranking-place-leading">3º</span>' in html
    assert "82,30" in html and "71,25" in html
    assert "PRIVATE FULL NAME" not in html and "secret@example.invalid" not in html
    assert '<th scope="row" class="ranking-name">Ana S.' in html
    assert '<th scope="col"' in html and '<caption class="ranking-sr-only">' in html


def test_public_ranking_escapes_names_limits_to_15_and_marks_current_participant(app):
    payload = boards()
    payload["clinical"]["entries"] = [participant(index, f"Pessoa {index}.") for index in range(1, 17)]
    payload["clinical"]["entries"][0].update(display_name='<img src=x onerror="alert(1)">', is_current_user=True)
    html = landing(app, payload)
    assert "&lt;img" in html and '<img src=x' not in html
    assert 'class="ranking-current"' in html and 'class="ranking-you">Você' in html
    assert "Pessoa 15." in html and "Pessoa 16." not in html


def test_personal_summary_has_two_independent_states_and_global_position(app):
    payload = positions()
    payload["basic"].update(distinct_cases=4, consultations=18, average_score=93.0, remaining_cases=1)
    payload["clinical"].update(rank=27, distinct_cases=7, consultations=9, average_score=83.25, eligible=True, remaining_cases=0)
    html = personal(app, payload)
    assert "Em formação" in html and "Falta 1 caso distinto" in html
    assert "4 casos distintos com nota válida" in html
    assert "27º <span>lugar</span>" in html and "Média 83,25/100" in html
    assert "93,00" not in html  # An ineligible result is not a ranked score.
    assert 'href="/#ranking"' in html


def test_minimum_is_read_from_backend_and_anonymous_users_get_no_personal_summary(app):
    html = landing(app, boards(3))
    assert "pelo menos 3 casos distintos" in html
    assert "pelo menos 5 casos distintos" not in html
    assert "Faltam 3 casos distintos" in personal(app, positions(3))
    assert "Sua posição" not in personal(app, positions(), authenticated=False)


def test_refresh_endpoint_and_asset_honor_application_mount_path(app):
    with app.test_request_context("/", environ_overrides={"SCRIPT_NAME": "/medrounds"}):
        html = render_template("landing.html", leaderboards=boards())
    assert 'data-ranking-url="/medrounds/api/ranking"' in html
    assert 'src="/medrounds/static/ranking.js"' in html


def test_dashboard_keeps_pending_evaluation_without_fictitious_final_score(app):
    pending = {
        "status": "completed", "result_url": "/resultado-pendente", "url": "/consulta-encerrada",
        "patient_name": "Paciente", "title": "Consulta de teste", "specialty": "Clínica médica",
        "evaluation_pending": True, "status_label": "Avaliação em conclusão", "score": None,
    }
    with app.test_request_context("/painel"):
        html = render_template(
            "dashboard.html", current_user=SimpleNamespace(is_authenticated=True, name="Aluna Teste"),
            personal_rankings=positions(), stats={"completed": 1, "active": 0, "average_score": None},
            recent_sessions=[deepcopy(pending)], recommended_cases=[],
        )
    assert html.index("Sua posição") < html.index("Últimas consultas")
    assert 'href="/resultado-pendente"' in html
    assert "Nota pendente" in html and "Avaliação em conclusão" in html
    assert 'class="pill pill-success"' not in html
    assert "ranking.js" in html
