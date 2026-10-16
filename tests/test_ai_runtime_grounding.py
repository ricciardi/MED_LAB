# Base five-dimension evaluator regressions; the version2 wrapper is covered in test_basic_questions.py.
"""Regressions discovered in real DeepSeek conversations and grading."""
from copy import deepcopy
import json
from unittest.mock import Mock

import pytest

from medlab.ai import AIError, _session_evidence
from medlab.case_schema import UNKNOWN_FACT
from test_ai import evaluation, patient_reply, reply, seed, service
from test_importer import real_case


@pytest.mark.parametrize("intent,question", [("Alergias", "Tem alergia?"), ("Transfusões", "Já recebeu sangue?"), ("Vacinação", "Quando foi sua vacina?")])
def test_missing_category_still_generates_contextual_patient_response(service, monkeypatch, intent, question):
    case = real_case()
    case["patientProfile"]["respondent"] = "Familiar que acompanha o paciente."
    case["patientProfile"]["other_medical_history"] = "Usa um medicamento informado no relato."
    post = Mock(return_value=reply(patient_reply("Sobre isso, não tenho uma informação segura para contar.", [intent])))
    monkeypatch.setattr("medlab.ai.requests.post", post)
    result = service.respond(case, question, [])
    assert result["answer"] == "Sobre isso, não tenho uma informação segura para contar."
    assert result["intent"] == intent and post.call_count == 1
    data = json.loads(post.call_args.kwargs["json"]["messages"][1]["content"])
    assert data["patientProfile"]["other_medical_history"] == case["patientProfile"]["other_medical_history"]


def test_gender_and_respondent_are_stable_full_patient_context(seed, service, monkeypatch):
    seed["patientProfile"].update(gender="Menina", respondent="A mãe responde pela criança.")
    post = Mock(return_value=reply(patient_reply("Ela está com tosse.", ["Queixa principal"])))
    monkeypatch.setattr("medlab.ai.requests.post", post)
    service.respond(seed, "O que trouxe vocês?", [])
    sent = json.loads(post.call_args.kwargs["json"]["messages"][1]["content"])
    assert sent["speaker"] == {"patientGender": "Menina", "respondent": "A mãe responde pela criança."}
    assert "past_medical_history" in sent["patientProfile"]
    assert "diagnoses" not in sent


def test_question_is_not_rewritten_or_lost_when_one_category_is_missing(seed, service, monkeypatch):
    seed["patientProfile"].setdefault("symptom_details", {}).update({
        "Início dos sintomas": UNKNOWN_FACT, "Evolução do quadro": "A falta de ar piorou depois do atendimento de ontem."})
    post = Mock(return_value=reply(patient_reply("A falta de ar piorou depois do atendimento de ontem.", ["Início dos sintomas", "Evolução do quadro"])))
    monkeypatch.setattr("medlab.ai.requests.post", post)
    result = service.respond(seed, "Quando começou e como mudou?", [])
    assert result["answer"] == "A falta de ar piorou depois do atendimento de ontem."
    sent = json.loads(post.call_args.kwargs["json"]["messages"][1]["content"])
    assert sent["question"] == "Quando começou e como mudou?"
    assert "Início dos sintomas" not in sent["patientProfile"]["symptom_details"]
    assert sent["patientProfile"]["symptom_details"]["Evolução do quadro"] == "A falta de ar piorou depois do atendimento de ontem."


def test_exam_evidence_only_proves_order_and_labels_automatic_result():
    catalog = _session_evidence({"history": [], "performedExams": [
        {"itemName": "Hemograma", "examType": "auxiliary_exam", "result": "Resultado automático do caso."}],
        "userSubmissions": [], "userTreatments": []})
    assert catalog == [{"id": "L1", "kind": "requested_auxiliary_exam", "text": "Solicitou: Hemograma",
                        "automaticCaseResult": "Resultado automático do caso."}]


def reference_grade(identifier="Q1", quote="Quando começou?"):
    grade = evaluation(0)
    grade["sections"][0].update(score=50, evidenceRefs=[{"id": identifier, "quote": quote}], evidence=["Invented provider narrative must not be retained."])
    grade["sections"][-1].update(evidenceRefs=[{"id": identifier, "quote": quote}])
    return grade


def session():
    return {"history": [{"question": "Quando começou?", "answer": "Começou ontem."}]}


@pytest.mark.parametrize("identifier,quote", [
    ("Q1", "Você teve sangue no catarro?"),  # Exists only in the reference case, not this session.
    ("Q2", "Quando começou?"),
    ("A1", "Começou ontem."),  # A patient's report is not a learner question.
])
def test_grading_rejects_invented_or_misattributed_question_evidence(seed, service, monkeypatch, identifier, quote):
    monkeypatch.setattr("medlab.ai.requests.post", Mock(return_value=reply(reference_grade(identifier, quote))))
    with pytest.raises(AIError, match="evidências que não conferem"):
        service._evaluate(seed, session())


def test_grading_preserves_only_verified_session_quotes_and_accepts_exact_provider_envelope(seed, service, monkeypatch):
    monkeypatch.setattr("medlab.ai.requests.post", Mock(return_value=reply({"type": "json_object", "format": reference_grade()})))
    graded = service._evaluate(seed, session())
    assert graded["sections"][0]["evidence"] == ['Pergunta 1: “Quando começou?”']
    assert "Invented provider narrative" not in json.dumps(graded)
    assert graded["score"] == 10  # Mean is still derived from five dimensions.


def test_only_missing_top_level_summaries_are_derived_from_valid_sections(seed, service, monkeypatch):
    grade = reference_grade()
    for section in grade["sections"]:
        section.update(strengths=[], improvements=[])
    grade["sections"][0].update(strengths=["Perguntou o início."], improvements=["Ampliar a entrevista."])
    compact = {key: grade[key] for key in ("score", "summary", "sections")}
    monkeypatch.setattr("medlab.ai.requests.post", Mock(return_value=reply(compact)))
    graded = service._evaluate(seed, session())
    assert graded["strengths"] == ["Perguntou o início."]
    assert graded["improvements"] == graded["next_steps"] == ["Ampliar a entrevista."]


def test_normalization_never_repairs_missing_dimension(seed, service, monkeypatch):
    grade = reference_grade()
    compact = {key: grade[key] for key in ("score", "summary", "sections")}
    compact["sections"].pop()
    monkeypatch.setattr("medlab.ai.requests.post", Mock(return_value=reply(compact)))
    with pytest.raises(AIError, match="dados incompletos"):
        service._evaluate(seed, session())


def test_long_literal_treatment_quote_is_verified_not_discarded(seed, service, monkeypatch):
    plan = "Conduta registrada pelo aluno. " * 15
    grade = evaluation(0)
    grade["sections"][4].update(score=25, evidenceRefs=[{"id": "T1", "quote": plan.strip()}])
    grade["sections"][-1].update(evidenceRefs=[{"id": "T1", "quote": plan.strip()}])
    monkeypatch.setattr("medlab.ai.requests.post", Mock(return_value=reply(grade)))
    graded = service._evaluate(seed, {"userTreatments": [{"data": {"treatmentPlan": plan}}]})
    assert graded["sections"][4]["score"] == 25
    assert plan.strip() in graded["sections"][4]["evidence"][0]


def test_positive_diagnosis_credit_requires_an_actual_student_submission(seed, service, monkeypatch):
    grade = reference_grade()
    grade["sections"][3].update(score=100, evidenceRefs=[{"id": "Q1", "quote": "Quando começou?"}])
    monkeypatch.setattr("medlab.ai.requests.post", Mock(return_value=reply(grade)))
    with pytest.raises(AIError, match="evidências que não conferem"):
        service._evaluate(seed, session())


def test_all_40_questions_are_numbered_and_last_turn_can_be_cited(seed, service, monkeypatch):
    history = [{"question": f"Pergunta clínica registrada número {index}?", "answer": "Não sei lhe dizer."} for index in range(1, 41)]
    grade = reference_grade("Q40", history[-1]["question"])
    post = Mock(return_value=reply(grade))
    monkeypatch.setattr("medlab.ai.requests.post", post)
    result = service._evaluate(seed, {"history": history})
    payload = json.loads(post.call_args.kwargs["json"]["messages"][1]["content"])
    assert len(payload["session"]["evidenceCatalog"]) == 80
    assert payload["session"]["evidenceCatalog"][-2]["id"] == "Q40"
    assert "número 40" in result["sections"][0]["evidence"][0]
