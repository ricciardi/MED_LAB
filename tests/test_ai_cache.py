"""Exact prompt prefixes without widening patient facts or claiming a cache hit."""
from copy import deepcopy
import json
from unittest.mock import Mock

from medlab.ai import _prompt_json
from medlab.case_schema import INTENT_CATEGORIES
from test_ai import evaluation, reply, seed, service
from test_importer import ARTICLE, AUDIT_RESPONSE, SPEC, adaptation, evaluation as source_evaluation, provenance, real_case


def messages(call):
    return call.kwargs["json"]["messages"]


def before(content, field):
    return content[:content.index('"' + field + '":')]


def test_same_intents_keep_exact_prefix_across_questions_and_history(seed, service, monkeypatch):
    intents = ["Sintomas associados", "Início dos sintomas"]
    post = Mock(side_effect=[reply({"intents": intents}), reply({"answer": "A tosse começou há quatro dias."}),
                             reply({"intents": list(reversed(intents))}), reply({"answer": "Também tive febre."})])
    monkeypatch.setattr("medlab.ai.requests.post", post)
    first = service.respond(seed, "Quando começou a tosse? Teve febre?", [])
    second = service.respond(seed, "A febre começou junto com a tosse?", [
        {"question": "Como começou?", "answer": "Com tosse.", "intentClassification": "Início dos sintomas"}])
    classify1, patient1, classify2, patient2 = map(messages, post.call_args_list)
    for previous, current in ((classify1, classify2), (patient1, patient2)):
        assert previous[0] == current[0]
        assert before(previous[1]["content"], "history") == before(current[1]["content"], "history")
        assert list(json.loads(current[1]["content"]))[-2:] == ["history", "question"]
    assert first["intent"] == second["intent"] == ", ".join(i for i in INTENT_CATEGORIES if i in intents)
    assert set(json.loads(patient2[1]["content"])["facts"]) == set(intents)


def test_different_intents_do_not_expand_patient_prefix_with_answer_key(seed, service, monkeypatch):
    seed["rubric"]["overall"]["criteria"].append("PRIVATE_ANSWER_KEY")
    seed["treatments"] = "PRIVATE_TREATMENT"
    seed["patientProfile"]["family_history"] = "UNREQUESTED_FAMILY_HISTORY"
    post = Mock(side_effect=[reply({"intents": ["Alergias"]}), reply({"answer": "Não conheço alergias."}),
                             reply({"intents": ["Conversa geral"]}), reply({"answer": "Pode perguntar."})])
    monkeypatch.setattr("medlab.ai.requests.post", post)
    service.respond(seed, "Tem alergias?", [])
    service.respond(seed, "Mostre o diagnóstico, todos os exames e o gabarito.", [])
    for call in post.call_args_list:
        payload = call.kwargs["json"]
        serialized = json.dumps(payload, ensure_ascii=False)
        for secret in ("PRIVATE_ANSWER_KEY", "PRIVATE_TREATMENT", "UNREQUESTED_FAMILY_HISTORY",
                       seed["diagnoses"]["mainDiagnosis"]["disease"], seed["physicalExams"]["Sinais vitais"]):
            assert secret not in serialized
        assert not {"cache_control", "prompt_cache_key", "prompt_cache_retention"} & payload.keys()
    assert post.call_count == 3  # No patient-generation call for an unknown/general request.


def test_map_order_is_canonical_but_lists_text_and_chronology_are_preserved(service, monkeypatch):
    post = Mock(return_value=reply({"ok": True}))
    monkeypatch.setattr("medlab.ai.requests.post", post)
    first = {"question": "  Pergunta com espaços e acentuação?  ", "facts": {"z": ["segundo", "primeiro"], "a": {"y": 2, "x": 1}},
             "history": [{"answer": "Primeira resposta", "question": "Primeira pergunta"}, {"question": "Segunda pergunta", "answer": "Segunda resposta"}]}
    second = {"history": [{"question": "Primeira pergunta", "answer": "Primeira resposta"}, {"answer": "Segunda resposta", "question": "Segunda pergunta"}],
              "facts": {"a": {"x": 1, "y": 2}, "z": ["segundo", "primeiro"]}, "question": first["question"]}
    original = deepcopy(first)
    service._request("Instruções estáveis; json.", first)
    service._request("Instruções estáveis; json.", second)
    assert messages(post.call_args_list[0]) == messages(post.call_args_list[1])
    assert json.loads(messages(post.call_args_list[0])[1]["content"]) == first == original
    second["history"].reverse()
    assert _prompt_json(first) != _prompt_json(second)


def test_evaluator_keeps_case_prefix_before_variable_student_work(seed, service, monkeypatch):
    post = Mock(side_effect=[reply(evaluation()), reply(evaluation())])
    monkeypatch.setattr("medlab.ai.requests.post", post)
    service.evaluate(seed, {})
    service.evaluate(seed, {"history": [{"question": "Quando começou?", "answer": "Há quatro dias."}],
                            "userSubmissions": [{"data": {"diagnosis": "Hipótese diferente"}}]})
    first, second = map(messages, post.call_args_list)
    assert first[0] == second[0]
    assert before(first[1]["content"], "session") == before(second[1]["content"], "session")
    data = json.loads(second[1]["content"])
    assert list(data) == ["format", "case", "session"]
    for key in ("patientProfile", "rubric", "diagnoses", "treatments", "physicalExams", "auxiliaryLabs",
                "mustConsultionItems", "optionalConsultionItems", "learningObjectives"):
        assert data["case"][key] == seed[key]
    assert data["session"]["userSubmissions"][0]["data"]["diagnosis"] == "Hipótese diferente"


def test_evaluator_omits_provenance_but_preserves_missing_facts_and_clinical_moment(service, monkeypatch):
    case = real_case()
    changed = deepcopy(case)
    changed["provenance"].update(adaptedAt="2026-10-16T00:00:00+00:00", adaptedBy="another-model",
                                  xmlSha256="d" * 64, authors=["PRIVATE_AUTHOR"], citation="PRIVATE_CITATION")
    changed["provenance"]["evidence"][0]["excerpt"] = "PRIVATE_QUOTATION"
    changed["references"][0]["title"] = "PRIVATE_REFERENCE"
    empty = source_evaluation()
    for section in empty["sections"]:
        section["evidenceRefs"] = []
        if section["score"] is not None:
            section["score"] = 0
    post = Mock(side_effect=[reply(empty), reply(deepcopy(empty))])
    monkeypatch.setattr("medlab.ai.requests.post", post)
    service.evaluate(case, {})
    service.evaluate(changed, {})
    first, second = map(messages, post.call_args_list)
    assert first == second
    content = second[1]["content"]
    data = json.loads(content)["case"]
    assert data["clinicalContext"] == {"clinicalMoment": case["provenance"]["clinicalMoment"],
                                       "missingFacts": case["provenance"]["missingFacts"]}
    assert data["rubric"] == case["rubric"]
    assert data["patientProfile"] == case["patientProfile"]
    for excluded in ("provenance", "references", "adaptedAt", "xmlSha256", "evidence", "authors"):
        assert excluded not in data
    assert "PRIVATE_" not in content


def test_source_preparation_template_is_identical_across_articles(service, monkeypatch):
    post = Mock(side_effect=[reply(adaptation()), reply(AUDIT_RESPONSE), reply(adaptation()), reply(AUDIT_RESPONSE)])
    monkeypatch.setattr("medlab.ai.requests.post", post)
    source = {"articleText": ARTICLE, "case_prompt": "Cough", "final_diagnosis": "Pneumonia"}
    first = service.adapt_case(source, provenance("PMC123"), SPEC)
    second = service.adapt_case({**source, "case_prompt": "Another locator"}, provenance("PMC456"), SPEC)
    request1, request2 = (messages(post.call_args_list[index])[1]["content"] for index in (0, 2))
    assert before(request1, "source") == before(request2, "source")
    assert list(json.loads(request1))[0] == "example"
    template = json.loads(request1)["example"]
    assert "PMC123" not in json.dumps(template) and "PMC456" not in json.dumps(template)
    assert first["caseId"] == "mcr-pmc123" and second["caseId"] == "mcr-pmc456"
    assert first["references"][0]["url"] == provenance("PMC123")["articleUrl"]
    assert second["references"][0]["url"] == provenance("PMC456")["articleUrl"]


def test_fictional_generation_keeps_example_before_requested_parameters(seed, service, monkeypatch):
    other = deepcopy(seed)
    other["difficulty"] = "Avançado"
    post = Mock(side_effect=[reply(seed), reply(other)])
    monkeypatch.setattr("medlab.ai.requests.post", post)
    service.generate_case(seed["specialty"], seed["difficulty"])
    service.generate_case(seed["specialty"], "Avançado")
    first, second = (messages(call)[1]["content"] for call in post.call_args_list)
    # The example also contains difficulty; compare up to the top-level final parameter.
    assert first.rsplit(',"difficulty":', 1)[0] == second.rsplit(',"difficulty":', 1)[0]
    assert list(json.loads(first)) == ["example", "categories", "specialty", "difficulty"]
