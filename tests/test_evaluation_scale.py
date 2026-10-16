"""Grading prompts allow proportional scores without changing the arithmetic.

Provider responses are controlled: these tests verify contracts and preservation
of grades, not the clinical calibration of a language model.
"""
from copy import deepcopy
from unittest.mock import Mock

import pytest

from medlab.ai import AIError, AIService, _session_evidence
from medlab.case_schema import SECTION_TITLES
from test_ai import evaluation, seed


def recorded_session(case):
    return {
        "history": [{"question": "Quando começou?", "answer": "Há quatro dias.",
                     "intentClassification": "Início dos sintomas"}],
        "performedExams": [
            {"itemName": next(iter(case["physicalExams"])), "examType": "physical_exam", "result": "Registro."},
            {"itemName": next(iter(case["auxiliaryLabs"])), "examType": "auxiliary_exam", "result": "Registro."},
        ],
        "userSubmissions": [{"data": {"diagnosis": "Hipótese sindrômica registrada e justificada."}}],
        "userTreatments": [{"data": {"treatmentPlan": "Plano registrado para investigação e acompanhamento."}}],
    }


def provider_grade(scores, evidence, path):
    identifiers = ["Q1", "P1", "L1", "D1", "T1", "Q1"]
    if path == "numeric":
        return {"sections": [
            {"key": key, "score": score, "actionIds": [identifier]}
            for key, score, identifier in zip(SECTION_TITLES, [*scores, 0], identifiers)]}
    result = evaluation()
    result["score"] = 5  # The provider's global number must never replace the mean.
    texts = {item["id"]: item["text"] for item in evidence}
    for section, score, identifier in zip(result["sections"], [*scores, 0], identifiers):
        section.update(score=score, improvements=[], evidenceRefs=[
            {"id": identifier, "quote": texts[identifier][:180]}])
    return result


def run_grade(case, scores, path):
    session = recorded_session(case)
    evidence = _session_evidence(AIService._session(session))
    service = AIService({"DEEPSEEK_API_KEY": "test"})
    response = provider_grade(scores, evidence, path)
    service._request = Mock(side_effect=[AIError("Formato inválido."), response]
                            if path == "numeric" else [response])
    service._audit_evaluation = Mock(return_value=[])
    return service, service.evaluate(case, session)


@pytest.mark.parametrize("path", ["rich", "numeric"])
@pytest.mark.parametrize("scores,expected", [
    ([17, 38, 64, 73, 91], 57),
    ([1, 7, 42, 68, 99], 43),
    ([5, 5, 5, 5, 5], 5),  # Five means five/100; never silently multiply by ten.
    ([25, 0, 0, 0, 0], 5),  # The observed low-grade pattern still has this mean.
    ([0, 0, 0, 0, 0], 0),
    ([100, 100, 100, 100, 100], 100),
])
def test_both_paths_preserve_intermediate_scores_and_compute_the_same_mean(seed, path, scores, expected):
    service, result = run_grade(seed, scores, path)
    assert [section["score"] for section in result["sections"][:5]] == scores
    assert result["score"] == result["sections"][-1]["score"] == expected
    assert service._request.call_count == (2 if path == "numeric" else 1)


@pytest.mark.parametrize("path", ["rich", "numeric"])
def test_both_grading_prompts_require_partial_credit_and_preserve_reference_boundaries(seed, path):
    original = deepcopy(seed)
    service, _ = run_grade(seed, [17, 38, 64, 73, 91], path)
    system, data, _ = service._request.call_args.args
    for requirement in (
        "escala de 0 a 100 pontos, nunca de 0 a 10",
        "Qualquer inteiro de 0 a 100 é permitido",
        "não as únicas notas possíveis",
        "não arredonde a nota para múltiplos de 25",
        "peso igual por critério",
        "ação ausente, inadequada, parcialmente adequada ou completa",
        "não da quantidade de perguntas, exames ou palavras",
        "Não dê pontos por palavras soltas",
        "score=null somente nas dimensões já marcadas rubric.assessable=false",
        "não converta essa dimensão em não avaliável",
        "Sem ação documentada pertinente numa dimensão avaliável, score=0",
        "limita a dimensão afetada a 50",
        "hipótese sindrômica, diagnóstico diferencial ou hipótese parcial",
        "Não exija a nomenclatura final de uma doença rara antes dos exames",
        "não altera os pesos das dimensões nem a média global",
    ):
        assert requirement in system
    assert data["case"]["rubric"] == original["rubric"]
    assert data["case"]["diagnoses"] == original["diagnoses"]
    assert seed == original


def test_unassessable_dimension_is_excluded_but_legitimate_omission_remains_zero():
    result = evaluation()
    for section, score in zip(result["sections"], [17, None, 0, 83, 42, 0]):
        section["score"] = score
    assessable = {key: key != "physical" for key in SECTION_TITLES}
    checked = AIService._validate_evaluation(result, assessable)
    assert checked["sections"][1]["score"] is None
    assert checked["sections"][2]["score"] == 0
    assert checked["score"] == 36  # (17 + 0 + 83 + 42) / 4 = 35.5, rounded.


def test_provider_cannot_remove_a_required_dimension_from_the_mean():
    result = evaluation()
    result["sections"][1].update(score=None, assessable=False)
    with pytest.raises(AIError, match="avaliação retornou dados incompletos"):
        AIService._validate_evaluation(result, {key: True for key in SECTION_TITLES})
