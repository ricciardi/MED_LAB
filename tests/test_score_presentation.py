"""A transparent score explanation never repairs or replaces saved assessments."""
from copy import deepcopy
import json
import re

import pytest

from medlab.case_schema import SECTION_TITLES
from medlab.extensions import db
from medlab.models import Consultation
from medlab.web import score_breakdown


def assessment(values, *, score=None):
    included = [value for value in values if value is not None]
    result_score = round(sum(included) / len(included)) if included else None
    if score is not None:
        result_score = score
    return {"score": result_score, "summary": "Resumo sintético de teste.",
        "sections": [{"key": key, "title": title, "score": value, "assessable": value is not None,
            "feedback": "Comentário de teste.", "strengths": [], "improvements": [], "evidence": []}
            for (key, title), value in zip(SECTION_TITLES.items(), [*values, result_score])],
        "strengths": [], "improvements": [], "next_steps": []}


def complete(app, identifier, evaluation, *, persisted_score=None):
    with app.app_context():
        training = db.session.get(Consultation, identifier)
        training.status = "completed"
        training.evaluation = deepcopy(evaluation)
        training.score = evaluation["score"] if persisted_score is None else persisted_score
        db.session.commit()


def test_five_is_explained_as_whole_consultation_not_the_interview(app, client, start_session):
    identifier = start_session()
    evaluation = assessment([25, 0, 0, 0, 0])
    complete(app, identifier, evaluation)
    body = client.get(f"/sessao/{identifier}/resultado").text
    assert "NOTA DA CONSULTA INTEIRA" in body
    assert "Como sua nota foi calculada." in body
    assert 'class="score-breakdown-focus"><span>Anamnese</span><strong>25<small>/100</small>' in body
    assert '(25 + 0 + 0 + 0 + 0) ÷ 5 = <strong>5<small>/100</small>' in body
    assert "25 pontos somados ÷ 5 dimensões avaliáveis" in body
    assert body.count('data-score-dimension=') == 5
    assert 'data-score-dimension="overall"' not in body
    assert "não é um sexto peso" in body and "Uma falha técnica não gera uma nota salva" in body
    assert "data-score-inconsistent" not in body
    with app.app_context():
        training = db.session.get(Consultation, identifier)
        assert training.score == 5 and training.evaluation == evaluation


@pytest.mark.parametrize("values,expected,rounded", [
    ([10, 20, 30, 40, 50], 30, False),
    ([100, 80, 50, 70, 60], 72, False),
    ([100, 100, 100, 100, 99], 100, True),
    ([0, 0, 0, 0, 0], 0, False),
])
def test_values_vary_and_round_exactly_like_saved_mean(values, expected, rounded):
    original = assessment(values)
    before = deepcopy(original)
    result = score_breakdown(original, expected)
    assert result["consistent"] and not result["warning"]
    assert result["calculated_score"] == expected and result["sum"] == sum(values)
    assert result["divisor"] == 5 and result["rounded"] is rounded
    assert original == before


def test_unassessable_dimensions_are_excluded_from_sum_and_divisor(app, client, start_session):
    identifier = start_session()
    evaluation = assessment([50, None, None, 75, 100])
    complete(app, identifier, evaluation)
    detail = score_breakdown(evaluation, 75)
    assert detail["divisor"] == 3 and detail["sum"] == 225 and detail["consistent"]
    assert [row["status"] for row in detail["dimensions"]] == ["included", "excluded", "excluded", "included", "included"]
    body = client.get(f"/sessao/{identifier}/resultado").text
    assert '(50 + 75 + 100) ÷ 3 = <strong>75<small>/100</small>' in body
    table = body.split('<table class="score-breakdown-table">', 1)[1].split('</table>', 1)[0]
    assert table.count("Não avaliada") == 2 and table.count("Excluída") == 2
    assert "data-score-inconsistent" not in body


@pytest.mark.parametrize("saved_score,persisted_score", [(50, 50), (5, 80)])
def test_inconsistent_legacy_grades_are_flagged_and_never_overwritten(app, client, start_session, saved_score, persisted_score):
    identifier = start_session()
    evaluation = assessment([25, 0, 0, 0, 0], score=saved_score)
    complete(app, identifier, evaluation, persisted_score=persisted_score)
    body = client.get(f"/sessao/{identifier}/resultado").text
    assert "data-score-inconsistent" in body and "sem substituição de notas" in body
    assert f"Nota registrada na avaliação: {saved_score}/100" in body
    assert f"Nota registrada no histórico: {persisted_score}/100" in body
    assert '(25 + 0 + 0 + 0 + 0) ÷ 5 = <strong>5<small>/100</small>' in body
    with app.app_context():
        training = db.session.get(Consultation, identifier)
        assert training.score == persisted_score and training.evaluation == evaluation


@pytest.mark.parametrize("bad_value", [True, "50", float("nan"), float("inf"), -1, 101, 10**400])
def test_invalid_legacy_scores_are_not_coerced_to_zero_or_counted(bad_value):
    evaluation = assessment([25, 0, 0, 0, 0])
    evaluation["sections"][1]["score"] = bad_value
    result = score_breakdown(evaluation, 5)
    assert result["warning"] and result["calculated_score"] is None
    assert result["divisor"] is None and result["dimensions"][1]["status"] == "unavailable"


@pytest.mark.parametrize("change", ["missing", "duplicate", "invalid_key", "invalid_assessable"])
def test_incomplete_or_ambiguous_legacy_structure_is_explicit(change):
    evaluation = assessment([25, 0, 0, 0, 0])
    if change == "missing":
        evaluation["sections"].pop(1)
    elif change == "duplicate":
        evaluation["sections"].append(deepcopy(evaluation["sections"][1]))
    elif change == "invalid_key":
        evaluation["sections"][1]["key"] = ["physical"]
    else:
        evaluation["sections"][1]["assessable"] = "false"
    result = score_breakdown(evaluation, 5)
    assert result["warning"] and result["calculated_score"] is None
    assert result["dimensions"][1]["status"] == "unavailable"


def test_qualitative_result_never_shows_zero_or_division_by_zero(app, client, start_session):
    identifier = start_session()
    evaluation = assessment([None] * 5)
    complete(app, identifier, evaluation)
    result = score_breakdown(evaluation, None)
    assert result["unscored"] and result["calculated_score"] is None and not result["warning"]
    body = client.get(f"/sessao/{identifier}/resultado").text
    assert "Não há média numérica" in body and "data-score-equation" not in body
    assert "÷ 0" not in body


def test_global_dimension_is_not_counted_a_second_time():
    evaluation = assessment([25, 0, 0, 0, 0])
    evaluation["sections"][-1]["score"] = 100
    result = score_breakdown(evaluation, 5)
    assert result["calculated_score"] == 5 and result["divisor"] == 5
    assert result["warning"]  # The historical global value itself is inconsistent.


def test_saved_learning_review_is_embedded_as_safe_json_without_a_get_side_effect(app, client, start_session, monkeypatch):
    identifier = start_session()
    evaluation = assessment([25, 0, 0, 0, 0])
    saved = {"version": 1, "status": "educational", "summary": "</script><script>alert('literal')</script>"}
    evaluation["learningReview"] = saved
    complete(app, identifier, evaluation)
    app.config["DEMO_MODE"] = False
    monkeypatch.setattr("medlab.web.get_ai_service", lambda: pytest.fail("Viewing a result must not call the provider."))
    body = client.get(f"/sessao/{identifier}/resultado").text
    embedded = re.search(r'<script id="learning-review-data" type="application/json">(.*?)</script>', body, re.S)
    assert embedded and json.loads(embedded[1]) == saved
    assert saved["summary"] not in body and r"\u003c/script\u003e" in embedded[1]
    assert 'data-demo-mode="false"' in body and '/static/learning-review.js' in body
    assert 'id="learning-review-retry"' in body and 'href="#learning-review"' in body
    with app.app_context():
        training = db.session.get(Consultation, identifier)
        assert training.score == 5 and training.evaluation == evaluation
