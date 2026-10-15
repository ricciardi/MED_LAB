"""Elaborated patient dialogue is context, never a new rubric or learner action."""
from copy import deepcopy
from unittest.mock import Mock

import pytest

from medlab.ai import (
    AIError, AIService, _dimension_evidence_ids, _evaluation_policy,
    _evaluation_prose_issues, _session_evidence, _validate_session_evidence,
)
from medlab.case_schema import UNKNOWN_FACT
from test_ai import seed
from test_ai_runtime_grounding import reference_grade
from test_evaluation_audit import audit, grade, numeric_grade


def context():
    return {
        "history": [
            {"question": "Quando começou?", "answer": "Não sei precisar.",
             "intentClassification": "Início dos sintomas"},
            {"question": "Isso atrapalhou suas atividades?", "answer": "Precisei interromper uma caminhada.",
             "intentClassification": "Impacto nas atividades"},
        ],
        "userSubmissions": [{"data": {"diagnosis": "Hipótese registrada pelo aluno."}}],
        "simulationContext": {"version": 1, "startedAfterTurn": 1, "details": [
            {"topic": "Início dos sintomas", "detail": "Precisei interromper uma caminhada.", "turn": 2},
        ]},
    }


@pytest.mark.parametrize("value", [None, "absent"])
def test_legacy_session_shape_is_preserved(value):
    data = {"history": context()["history"]}
    if value is None:
        data["simulationContext"] = None
    session = AIService._session(data)
    assert "simulationContext" not in session
    answers = [item for item in _session_evidence(session) if item["id"].startswith("A")]
    assert all(set(item) == {"id", "kind", "text"} for item in answers)


def test_context_is_validated_and_copied_without_becoming_case_data():
    original = context()
    session = AIService._session(original)
    assert session["simulationContext"] == original["simulationContext"]
    session["simulationContext"]["details"][0]["detail"] = "Modificado apenas na cópia."
    assert original["simulationContext"]["details"][0]["detail"] == "Precisei interromper uma caminhada."


@pytest.mark.parametrize("change", [
    {"version": 2}, {"version": True}, {"startedAfterTurn": True},
    {"startedAfterTurn": -1}, {"startedAfterTurn": 3},
    {"details": "texto livre"},
    {"details": [{"topic": "Início dos sintomas", "detail": "Informação.", "turn": 1}]},
    {"details": [{"topic": "Início dos sintomas", "detail": "Informação.", "turn": 3}]},
    {"details": [{"topic": "Início dos sintomas", "detail": "", "turn": 2}]},
    {"details": [{"topic": "Início dos sintomas", "detail": "Informação.", "turn": True}]},
    {"details": [{"topic": "Início dos sintomas", "detail": "x" * 20000, "turn": 2}]},
    {"details": [{"topic": "Início dos sintomas", "detail": "Informação.", "turn": 2}] * 500},
])
def test_invalid_simulation_context_is_rejected_before_provider(seed, change):
    data = context()
    data["simulationContext"].update(change)
    service = AIService({"DEEPSEEK_API_KEY": "test"})
    service._request = Mock()
    with pytest.raises(AIError, match="contexto de simulação"):
        service.evaluate(seed, data)
    service._request.assert_not_called()


def test_only_post_activation_answers_are_marked_and_never_become_student_actions():
    session = AIService._session(context())
    catalog = {item["id"]: item for item in _session_evidence(session)}
    assert "origin" not in catalog["A1"]
    assert catalog["A2"]["origin"] == "simulated_dialogue"
    assert catalog["A2"]["sourceVerified"] is False
    assert catalog["A2"]["kind"] == "patient_report_not_student_action"
    assert catalog["A2"]["generatedDetails"] == [
        {"topic": "Início dos sintomas", "detail": "Precisei interromper uma caminhada."}]
    assert catalog["Q2"]["kind"] == "student_question"
    assert "generatedDetails" not in catalog["Q2"]
    assert "simulationContext" not in catalog["D1"]


def test_every_answer_after_boundary_is_marked_even_without_new_detail():
    data = context()
    data["simulationContext"].update(startedAfterTurn=0, details=[])
    answers = [item for item in _session_evidence(AIService._session(data)) if item["id"].startswith("A")]
    assert len(answers) == 2
    assert all(item["origin"] == "simulated_dialogue" and item["generatedDetails"] == [] for item in answers)


def test_last_allowed_turn_keeps_its_provenance_and_literal_text():
    data = context()
    data["history"] = [{"question": f"Pergunta {index}?", "answer": f"Resposta {index}."}
                       for index in range(1, 41)]
    data["simulationContext"].update(startedAfterTurn=39, details=[
        {"topic": "Início dos sintomas", "detail": "Resposta 40.", "turn": 40}])
    catalog = {item["id"]: item for item in _session_evidence(AIService._session(data))}
    assert "origin" not in catalog["A39"]
    assert catalog["A40"]["origin"] == "simulated_dialogue"
    assert catalog["A40"]["text"] == "Resposta 40."


def test_generated_patient_quote_cannot_support_a_positive_interview_score():
    value = reference_grade("A2", "Precisei interromper uma caminhada.")
    validated = AIService._validate_evaluation(value)
    with pytest.raises(AIError, match="evidências que não conferem"):
        _validate_session_evidence(validated, _session_evidence(AIService._session(context())))


def test_patient_quote_used_as_context_remains_explicitly_simulated():
    value = reference_grade()
    value["sections"][3].update(score=50, evidenceRefs=[
        {"id": "D1", "quote": "Hipótese registrada pelo aluno."},
        {"id": "A2", "quote": "Precisei interromper uma caminhada."},
    ])
    result = _validate_session_evidence(AIService._validate_evaluation(value),
                                        _session_evidence(AIService._session(context())))
    assert result["sections"][3]["evidence"][1].startswith("Fala simulada do paciente 2:")


def assert_fairness_contract(system, data, original_case):
    assert data["case"]["rubric"] == original_case["rubric"]
    assert data["case"]["diagnoses"] == original_case["diagnoses"]
    assert "simulationContext" not in data["case"]
    assert "generatedDetails" not in data["case"]
    assert "não penalize o raciocínio por esse conflito gerado" in system
    assert "Não converta esses detalhes em critérios obrigatórios" in system
    assert "nunca comprovam ação do aluno" in system


def test_rich_evaluation_and_audit_keep_simulation_separate_from_reference(seed):
    original_case = deepcopy(seed)
    service = AIService({"DEEPSEEK_API_KEY": "test"})
    service._request = Mock(return_value=reference_grade())
    service._audit_evaluation = Mock(return_value=[])
    result = service.evaluate(seed, context())
    system, data, _ = service._request.call_args.args
    assert_fairness_contract(system, data, original_case)
    assert data["session"]["simulationContext"] == context()["simulationContext"]
    audit_evidence = service._audit_evaluation.call_args.args[1]
    assert next(item for item in audit_evidence if item["id"] == "A2")["origin"] == "simulated_dialogue"
    assert result["simulationNotice"]["simulatedAnswerIds"] == ["A2"]
    assert result["simulationNotice"]["generatedDetailCount"] == 1
    assert seed == original_case


def test_semantic_auditor_receives_the_same_provenance_rules_and_no_student_credit_for_patient():
    value = grade()
    service = AIService({"DEEPSEEK_API_KEY": "test"})
    service._request = Mock(return_value=audit(value, reference="A2", quote="Precisei interromper uma caminhada."))
    issues = service._audit_evaluation(value, _session_evidence(AIService._session(context())))
    assert issues
    system, data, _ = service._request.call_args.args
    assert "não penalize o raciocínio por esse conflito gerado" in system
    assert next(item for item in data["evidenceCatalog"] if item["id"] == "A2")["sourceVerified"] is False


def test_numeric_fallback_receives_identical_context_without_fabricating_conflicts(seed):
    original_case = deepcopy(seed)
    service = AIService({"DEEPSEEK_API_KEY": "test"})
    service._request = Mock(side_effect=[AIError("Falha de formato."), numeric_grade()])
    result = service.evaluate(seed, context())
    system, data, _ = service._request.call_args.args
    assert_fairness_contract(system, data, original_case)
    assert data["simulationContext"] == context()["simulationContext"]
    assert result["verification"]["status"] == "limited"
    assert result["simulationNotice"]["simulatedAnswerIds"] == ["A2"]
    assert "sem penalizá-lo por conflitos" in result["simulationNotice"]["message"]
    assert result["sections"][0]["evidenceRefs"] == [{"id": "Q1", "quote": "Quando começou?"}]
    assert seed == original_case


def test_numeric_fallback_never_accepts_simulated_answer_as_action(seed):
    value = numeric_grade()
    value["sections"][0].update(actionIds=["A2"])
    service = AIService({"DEEPSEEK_API_KEY": "test"})
    service._request = Mock(return_value=value)
    session = AIService._session(context())
    with pytest.raises(AIError, match="numérica"):
        service._score_only_evaluation(seed, _session_evidence(session), session["simulationContext"])


def test_demo_and_empty_activation_do_not_add_requirements_or_score_credit(seed):
    service = AIService({"DEMO_MODE": True})
    data = context()
    data["simulationContext"].update(startedAfterTurn=2, details=[])
    with_simulation = service.evaluate(seed, data)
    del data["simulationContext"]
    legacy = service.evaluate(seed, data)
    assert with_simulation.pop("simulationNotice")["simulatedAnswerIds"] == []
    assert with_simulation == legacy


def policy_case(seed):
    case = deepcopy(seed)
    case["patientProfile"]["family_history"] = UNKNOWN_FACT
    case["patientProfile"].setdefault("symptom_details", {}).pop("História familiar", None)
    case["mustConsultionItems"] = ["Queixa principal", "Evolução do quadro", "Doenças crônicas"]
    case["optionalConsultionItems"] = []
    case["rubric"]["consultation"] = {"criteria": ["Investigar os fatos documentados."], "criticalActions": []}
    return case


@pytest.mark.parametrize("text", [
    "Faltou investigar antecedentes ginecológicos/obstétricos e história familiar.",
    "Faltou antecedentes ginecológicos/obstétricos e familiares",
    "Aprofundar história ginecológica e antecedentes familiares",
])
def test_unavailable_unrequired_family_history_cannot_become_a_missing_requirement(seed, text):
    case = policy_case(seed)
    result = grade(text)
    issues = _evaluation_prose_issues(result, _session_evidence(AIService._session(context())), _evaluation_policy(case))
    assert any("História familiar" in issue["correction"] for issue in issues)


def test_required_documented_history_and_explicit_optional_coaching_are_not_removed(seed):
    case = policy_case(seed)
    case["mustConsultionItems"].append("História familiar")
    case["patientProfile"]["family_history"] = "Mãe com doença cardíaca."
    evidence = _session_evidence(AIService._session(context()))
    assert _evaluation_prose_issues(grade("Faltou investigar história familiar."), evidence, _evaluation_policy(case)) == []
    case = policy_case(seed)
    assert _evaluation_prose_issues(grade("Aprofundar história familiar é opcional, sem desconto."), evidence, _evaluation_policy(case)) == []


@pytest.mark.parametrize("text", [
    "Ausência de exame físico, exames e plano terapêutico",
    "Não houve plano terapêutico.", "Nenhum plano foi registrado.", "Plano terapêutico ausente.",
])
def test_registered_plan_disproves_total_absence_even_inside_a_list(seed, text):
    data = context()
    data["userTreatments"] = [{"data": {"treatmentPlan": "Investigar antes de definir tratamento e encaminhar."}}]
    evidence = _session_evidence(AIService._session(data))
    assert any("Existe plano T" in issue["correction"] for issue in
        _evaluation_prose_issues(grade(text), evidence, _evaluation_policy(seed)))
    evidence = [item for item in evidence if item["id"] != "T1"]
    assert _evaluation_prose_issues(grade(text), evidence, _evaluation_policy(seed)) == []


def test_plan_presence_does_not_prove_quality_or_prevent_specific_criticism(seed):
    data = context()
    data["userTreatments"] = [{"data": {"treatmentPlan": "Encaminhar."}}]
    evidence = _session_evidence(AIService._session(data))
    for text in ("Faltou detalhamento do plano terapêutico.", "Não há plano terapêutico específico."):
        assert _evaluation_prose_issues(grade(text), evidence, _evaluation_policy(seed)) == []


@pytest.mark.parametrize("text", [
    "A conduta registrada é genérica, sem plano oncológico compatível com a referência (radioterapia e cirurgia).",
    "A conduta não menciona as opções da referência (radioterapia e cirurgia).",
    "Considerar radioterapia e cirurgia conforme a referência",
])
def test_historical_treatment_cannot_become_an_obligation(seed, text):
    case = policy_case(seed)
    case["catalogType"] = "published_case"
    result = grade()
    result["sections"][4]["feedback"] = text
    issues = _evaluation_prose_issues(result, [], _evaluation_policy(case))
    assert any("conduta histórica" in issue["correction"] for issue in issues)
    case["catalogType"] = "fictional"
    assert _evaluation_prose_issues(result, [], _evaluation_policy(case)) == []


def test_diagnostic_change_does_not_change_interview_evidence_or_grading_policy(seed):
    first = context()
    second = deepcopy(first)
    second["userSubmissions"][0]["data"]["diagnosis"] = "Hipótese incompatível."
    catalogs = [_session_evidence(AIService._session(data)) for data in (first, second)]
    scopes = [_dimension_evidence_ids(catalog) for catalog in catalogs]
    assert scopes[0]["consultation"] == scopes[1]["consultation"] == ["Q1", "Q2"]
    assert [[item for item in catalog if item["id"] in scopes[index]["consultation"]]
            for index, catalog in enumerate(catalogs)][0] == [item for item in catalogs[1] if item["id"] in scopes[1]["consultation"]]
    assert "não usar o acerto ou erro de D/T" in _evaluation_policy(seed)["dimensionIsolation"]["consultation"]


def test_diagnostic_reference_in_another_sentence_is_not_a_historical_treatment_demand(seed):
    case = policy_case(seed)
    case["catalogType"] = "published_case"
    value = grade()
    value["summary"] = "Falta detalhamento do raciocínio e da conduta. Diagnóstico coincide com a referência, mas sem justificativa robusta."
    assert _evaluation_prose_issues(value, [], _evaluation_policy(case)) == []


def test_local_guards_override_auditor_approval_and_do_not_retain_the_false_claim(seed):
    case = policy_case(seed)
    bad = grade("Faltou investigar história familiar.")
    service = AIService({"DEEPSEEK_API_KEY": "test"})
    service._request = Mock(side_effect=[bad, numeric_grade()])
    service._audit_evaluation = Mock(return_value=[])
    result = service.evaluate(case, context())
    assert service._request.call_count == 2
    assert result["verification"]["status"] == "limited"
    assert all(section["feedbackLimited"] for section in result["sections"])
    assert "Faltou investigar história familiar" not in str(result)
    policy = service._request.call_args.args[1]["case"]["gradingPolicy"]
    assert "História familiar" in policy["unavailableInterviewTopics"]
    assert "História familiar" not in policy["requiredInterviewTopics"]
    assert "Um diagnóstico errado em D não reduz" in service._request.call_args.args[0]


def test_auditor_receives_reference_boundaries_instead_of_guessing_availability(seed):
    case = policy_case(seed)
    service = AIService({"DEEPSEEK_API_KEY": "test"})
    service._request = Mock(return_value=audit(grade()))
    service._audit_evaluation(grade(), _session_evidence(AIService._session(context())), case)
    sent = service._request.call_args.args[1]
    assert sent["gradingPolicy"] == _evaluation_policy(case)
    assert sent["dimensionEvidenceIds"]["consultation"] == ["Q1", "Q2"]
