"""Contextual rubric, semantic-provider contract and versioned grade arithmetic.

Mocked classifications test boundaries, not the clinical accuracy of an LLM.
"""
from copy import deepcopy
from unittest.mock import Mock

import pytest

from medlab.ai import AIError, AIService, AIUnavailableError, AIValidationError, BasicQuestionsPending, _case, _session_evidence
from medlab.basic_questions import (
    KEY, TITLE, GROUPS, DIMENSIONS, PROMPT, InvalidBasicQuestions, attach_review,
    build_review, context_catalog, criteria_catalog, input_hash, normalize_review,
)
from medlab.case_schema import UNKNOWN_FACT, SECTION_TITLES
from medlab.teaching_review import build_teaching_review
from medlab.web import score_breakdown
from test_ai import seed, evaluation
from test_teaching_review import response as teaching_response


def response(criteria):
    return {"version": 1, "items": [{"id": item["id"], "status": "not_applicable", "actionIds": [],
        "contextIds": [], "exemption": "source_unavailable", "reason": "Este detalhe não é informado na fonte."} for item in criteria]}


def provider_response(case, value):
    """All 47 objectives remain reviewable, even with an unknown patient answer."""
    return deepcopy(value)


def review_request(value):
    return Mock(return_value=deepcopy(value))


def session():
    return AIService._session({"history": [
        {"question": "Isso veio de repente ou foi chegando aos poucos?", "answer": "Começou aos poucos."},
        {"question": "O que ajuda a aliviar?", "answer": "Melhora quando descanso."},
        {"question": "Prefere não falar sobre isso?", "answer": "Sim, prefiro não responder."},
    ]})


def item(value, identifier):
    return next(row for row in value["items"] if row["id"] == identifier)


def normalize(case, value, sess=None):
    evidence = _session_evidence(sess or session())
    return normalize_review(value, criteria_catalog(case), evidence, context_catalog(case, evidence))


def scored(case):
    value = response(criteria_catalog(case))
    item(value, "H01").update(status="complete", actionIds=["Q1"], exemption=None, reason="Explorou a instalação sem depender de palavras exatas.")
    item(value, "H07").update(status="partial", actionIds=["Q2"], exemption=None, reason="Explorou melhora; a piora pertinente ainda pode ser aprofundada.")
    return value


def test_catalog_preserves_specific_objectives_from_both_images(seed):
    catalog = criteria_catalog(seed)
    assert len(catalog) == 47
    assert len({row["id"] for row in catalog}) == 47
    assert {row["groupId"] for row in catalog} == set(GROUPS)
    labels = " ".join(row["title"] for row in catalog)
    for objective in ("Naturalidade", "Residência", "Etnia/cor", "Nome da mãe", "Responsável", "encaminhamento", "Intensidade", "Duração", "Frequência", "melhora", "cronológica", "confirmação", "fâneros", "Tabagismo", "Anabolizantes", "esgoto", "Animais", "dependência econômica", "Escolaridade", "bem-estar"):
        assert objective in labels
    assert set(seed["rubric"]) == set(SECTION_TITLES)


def test_every_item_has_contextual_applicability_not_an_automatic_checklist(seed):
    catalog = {row["id"]: row for row in criteria_catalog(seed)}
    assert all(isinstance(row["applicabilityRule"], str) and row["applicabilityRule"] for row in catalog.values())
    for identifier, meaningful_rule in {
        "Q02": "Sem esse contexto, não aplicável",
        "I09": "mesmo sem relação com diagnóstico ou cuidado",
        "I10": "necessidade real de identificação",
        "S03": "Baixa pertinência sem necessidade demonstrada",
        "H03": "não exigir localizar anatomicamente todo sintoma difuso",
        "R04": "integra o roteiro amplo, mesmo sem queixa dermatológica",
        "S05": "sem exigir renda exata",
    }.items():
        assert meaningful_rule in catalog[identifier]["applicabilityRule"]
    assert "síntese" in catalog["H09"]["coverageRule"]
    assert "Não usar not_met se alguma parte" in catalog["H09"]["coverageRule"]
    assert "nomear o sintoma associado omitido" in catalog["H08"]["coverageRule"]
    assert "Não diminuir nota só porque não perguntou todos os exemplos" in catalog["R02"]["coverageRule"]


def test_prompt_requires_actual_purpose_and_justification_before_classification():
    for constraint in ("propósito no diálogo completo", "paráfrases", "linguagem aproximada", "perguntas compostas",
                       "PRIMEIRO reason", "status coerente", "parte real do objetivo", "não pergunta a queixa",
                       "não é história familiar", "não investiga apoio familiar"):
        assert constraint in PROMPT


@pytest.mark.parametrize("identifier", ["I02", "I03", "I07", "I09", "R01", "R03", "R04", "F01", "V01", "S01", "S02", "S04", "S05", "S06", "S08", "S09"])
def test_broad_curriculum_cannot_be_dismissed_just_for_irrelevance_to_diagnosis(seed, identifier):
    seed["patientProfile"]["basic_info"] = "Naturalidade, estado civil e escolaridade informados na ficha de referência."
    criteria = criteria_catalog(seed)
    assert next(row for row in criteria if row["id"] == identifier)["scope"] == "core_curriculum"
    value = response(criteria)
    item(value, identifier).update(exemption="context_not_relevant",
        reason="Não muda a hipótese diagnóstica da tosse, portanto não precisa ser perguntado.")
    with pytest.raises(InvalidBasicQuestions, match="core_curriculum_cannot_be_excluded"):
        normalize(seed, value)


def test_basic_missing_context_can_be_explained_without_inventing_disease_risk(seed):
    seed["patientProfile"]["basic_info"] = "Escolaridade: ensino médio completo."
    value = response(criteria_catalog(seed))
    item(value, "S06").update(status="not_met", exemption=None,
        reason="O contexto básico de escolaridade não foi explorado no roteiro amplo.")
    result = normalize(seed, value)
    assert result["score"] == 0
    assert next(row for row in result["criteria"] if row["id"] == "S06")["evidence"] == []


@pytest.mark.parametrize("source_available", [True, False])
@pytest.mark.parametrize("question", [
    "Se estiver à vontade, qual é sua religião?",
    "Você segue alguma fé ou prefere não ter religião?",
    "Como prefere se identificar em relação à religião?",
])
def test_respectful_religion_question_keeps_credit_without_diagnostic_relevance(seed, source_available, question):
    seed["patientProfile"].update(
        basic_info="Não segue religião." if source_available else UNKNOWN_FACT,
        social_background=UNKNOWN_FACT)
    sess = AIService._session({"history": [{"question": question,
        "answer": "Não sigo nenhuma religião." if source_available else "Prefiro não responder."}]})
    value = response(criteria_catalog(seed))
    item(value, "I09").update(status="complete", exemption=None, actionIds=["Q1"],
        reason="Perguntou a preferência religiosa com respeito; o objetivo básico foi abordado.")
    item(value, "S07").update(exemption="context_not_relevant",
        reason="Não houve contexto que exigisse aprofundar práticas específicas de cuidado.")
    service = AIService({"DEEPSEEK_API_KEY": "test"})
    service._request = review_request(value)
    result = build_review(service, seed, sess)
    assert result["score"] == 100
    row = next(row for row in result["criteria"] if row["id"] == "I09")
    assert row["status"] == "complete" and row["evidence"] == [{"id": "Q1", "text": question}]
    assert next(row for row in result["criteria"] if row["id"] == "S07")["status"] == "not_applicable"
    payload = service._request.call_args.args[1]
    catalog = {row["id"]: row for row in payload["criteriaCatalog"]}
    assert catalog["I09"]["scope"] == "core_curriculum"
    assert catalog["S07"]["scope"] == "context_conditional"
    assert "sem crédito automático em S07" in catalog["S07"]["applicabilityRule"]
    assert "Nenhuma pergunta é requisito para concluir a consulta" in service._request.call_args.args[0]
    assert service._request.call_count == 1


@pytest.mark.parametrize("source_available,expected_status,expected_score", [
    (True, "not_met", 0), (False, "not_applicable", None),
])
def test_religion_omission_affects_available_rubric_without_blocking_review(seed, source_available, expected_status, expected_score):
    seed["patientProfile"].update(
        basic_info="Religião: católica." if source_available else UNKNOWN_FACT,
        social_background=UNKNOWN_FACT)
    value = response(criteria_catalog(seed))
    item(value, "I09").update(status="not_met", exemption=None,
        reason="A preferência religiosa ainda desconhecida não foi abordada na identificação básica.")
    result = normalize(seed, value)
    assert result["score"] == expected_score
    row = next(row for row in result["criteria"] if row["id"] == "I09")
    assert row["status"] == expected_status and row["evidence"] == []


@pytest.mark.parametrize("exemption,answer", [
    ("already_known", "Eu sou católica."),
    ("declined", "Prefiro não falar da minha religião."),
    ("respondent_limitation", "Sou vizinho e não sei dizer qual é a religião dela."),
])
def test_basic_religion_still_respects_known_information_refusal_and_informant_limits(seed, exemption, answer):
    seed["patientProfile"]["basic_info"] = "Religião registrada na ficha."
    value = response(criteria_catalog(seed))
    item(value, "I09").update(exemption=exemption, contextIds=["A1"],
        reason="O contexto observado dispensa insistir ou repetir a pergunta de religião.")
    sess = AIService._session({"history": [{"question": "Pode me contar sobre você?", "answer": answer}]})
    result = normalize(seed, value, sess)
    row = next(row for row in result["criteria"] if row["id"] == "I09")
    assert row["status"] == "not_applicable" and row["exemption"] == exemption
    assert row["contextEvidence"] == [{"id": "A1", "text": answer}]
    assert row["evidence"] == [] and result["score"] is None


def test_compound_question_can_cover_religion_and_specific_care_preferences(seed):
    question = "Qual é sua religião? Existe alguma preferência ligada à sua fé que gostaria que respeitássemos no cuidado?"
    sess = AIService._session({"history": [{"question": question,
        "answer": "Sou testemunha de Jeová e não aceito transfusão de sangue."}]})
    value = response(criteria_catalog(seed))
    for identifier, reason in (("I09", "Perguntou a religião respeitando autodeclaração."),
                               ("S07", "Explorou preferências específicas relacionadas ao cuidado, além da identificação religiosa.")):
        item(value, identifier).update(status="complete", exemption=None, actionIds=["Q1"], reason=reason)
    result = normalize(seed, value, sess)
    assert result["score"] == 100
    for identifier in ("I09", "S07"):
        row = next(row for row in result["criteria"] if row["id"] == identifier)
        assert row["status"] == "complete" and row["evidence"] == [{"id": "Q1", "text": question}]


def test_transition_requires_explanation_not_only_system_questions(seed):
    catalog = {row["id"]: row for row in criteria_catalog(seed)}
    assert "a explicação precisa existir" in catalog["R01"]["applicabilityRule"]
    assert "não provam explicação da transição" in catalog["R01"]["coverageRule"]
    assert "partial; complete exige ambas" in catalog["R01"]["coverageRule"]
    assert "core_curriculum integra o roteiro amplo" in PROMPT
    assert "context_conditional" in PROMPT


def test_system_review_three_aspects_are_semantic_not_three_turns(seed):
    rule = next(row for row in criteria_catalog(seed) if row["id"] == "R05")["coverageRule"]
    for requirement in ("três aspectos sintomáticos distintos e pertinentes", "perguntas compostas",
                        "aspectos já conhecidos", "não exigir três turnos", "recusa e urgência",
                        "Não inventar aspectos irrelevantes"):
        assert requirement in rule


def test_contextual_provider_decision_keeps_ids_arithmetic_and_one_generation(seed):
    # Controlled classification models the desired contract, not proof of the
    # real model's semantic accuracy. The live retest must make that comparison.
    seed["patientProfile"]["basic_info"] = "Nome da mãe registrado; religião declarada, sem preferência de cuidado relacionada."
    value = response(criteria_catalog(seed))
    for identifier in ("Q02", "S07", "I10", "S03"):
        item(value, identifier).update(exemption="context_not_relevant", reason="Não há contexto que exija esse dado neste encontro.")
    item(value, "H09").update(status="complete", actionIds=["Q1", "Q2"], exemption=None,
        reason="Explorou sequência e evolução nas duas perguntas; não há tratamento prévio pendente de esclarecer.")
    service = AIService({"DEEPSEEK_API_KEY": "test"})
    service._request = review_request(provider_response(seed, value))
    result = build_review(service, seed, session())
    assert result["score"] == 100
    assert next(row for row in result["criteria"] if row["id"] == "H09")["evidenceRefs"] == ["Q1", "Q2"]
    assert all(next(row for row in result["criteria"] if row["id"] == identifier)["status"] == "not_applicable"
               for identifier in ("Q02", "S07", "I10", "S03"))
    assert service._request.call_count == 1 and service._request.call_args_list[0].args[2] == 10000


def test_semantic_equivalents_keep_the_actual_question_and_partial_coverage(seed):
    result = normalize(seed, scored(seed))
    first = next(row for row in result["criteria"] if row["id"] == "H01")
    assert first["evidence"] == [{"id": "Q1", "text": "Isso veio de repente ou foi chegando aos poucos?"}]
    assert result["score"] == 75
    assert next(group for group in result["groups"] if group["id"] == "hpi")["score"] == 75
    assert "aceite paráfrases" in PROMPT and "Não exija palavras do roteiro" in PROMPT


def test_equal_group_weights_prevent_long_identification_list_from_dominating(seed):
    seed["patientProfile"]["basic_info"] = "Dados gerais disponíveis para o cenário sintético."
    value = response(criteria_catalog(seed))
    for identifier in ("I01", "I02", "I04"):
        item(value, identifier).update(status="complete", actionIds=["Q1"], exemption=None, reason="Classificação sintética controlada.")
    item(value, "H01").update(status="not_met", exemption=None, reason="Objetivo disponível não atendido no cenário controlado.")
    assert normalize(seed, value)["score"] == 50  # group means100 and0, not item mean75


@pytest.mark.parametrize("change,code", [
    (lambda value: item(value, "H01").update(actionIds=["A1"]), "reference_or_authorship"),
    (lambda value: item(value, "H01").update(actionIds=["Q999"]), "reference_or_authorship"),
    (lambda value: item(value, "H01").update(actionIds=[]), "positive_credit_without_question"),
    (lambda value: item(value, "H01").update(actionIds=["Q1", "Q1"]), "reference_or_authorship"),
    (lambda value: item(value, "H01").update(status="not_met"), "missing_with_question"),
    (lambda value: item(value, "H01").update(status="not_applicable", exemption="declined", contextIds=["Q1"]), "refusal_without_context"),
    (lambda value: item(value, "H01").update(status="not_applicable", exemption="already_known", contextIds=[]), "known_without_context"),
    (lambda value: item(value, "H01").update(status="not_applicable", exemption="already_known", contextIds=["K3"]), "known_without_context"),
    (lambda value: item(value, "H01").update(status="not_applicable", exemption="urgent_priority", contextIds=[]), "context_required"),
    (lambda value: item(value, "H01").update(reason="x" * 301), "reason"),
    (lambda value: item(value, "H01").update(score=100), "item_shape"),
    (lambda value: value["items"].append(deepcopy(value["items"][0])), "item_count"),
    (lambda value: value["items"].__setitem__(1, deepcopy(value["items"][0])), "item_id_or_status"),
    (lambda value: value.update(version=True), "root"),
])
def test_invalid_references_invented_credit_duplicates_and_free_scores_fail(seed, change, code):
    value = scored(seed)
    change(value)
    with pytest.raises(InvalidBasicQuestions, match=code):
        normalize(seed, value)


@pytest.mark.parametrize("status,expected", [("complete", 100), ("partial", 50)])
def test_missing_patient_answer_does_not_erase_credit_for_a_pertinent_question(seed, status, expected):
    seed["patientProfile"]["family_history"] = UNKNOWN_FACT
    value = response(criteria_catalog(seed))
    item(value, "F01").update(status=status, exemption=None, actionIds=["Q1"],
        reason="A pergunta abordou a saúde da família; a resposta não estava disponível na ficha.")
    result = normalize(seed, value)
    assert result["score"] == expected
    row = next(entry for entry in result["criteria"] if entry["id"] == "F01")
    assert row["status"] == status and row["evidenceRefs"] == ["Q1"]


def test_missing_reference_never_turns_an_omitted_item_into_a_penalty(seed):
    seed["patientProfile"]["family_history"] = UNKNOWN_FACT
    value = response(criteria_catalog(seed))
    item(value, "F01").update(status="not_met", exemption=None, actionIds=[],
        reason="O aluno não perguntou a condição dos familiares.")
    before = deepcopy(value)
    result = normalize(seed, value)
    assert result["score"] is None and result["assessable"] is False
    row = next(entry for entry in result["criteria"] if entry["id"] == "F01")
    assert row["status"] == "not_applicable" and row["exemption"] == "source_unavailable"
    assert row["evidence"] == [] and "não gera desconto" in row["reason"]
    assert value == before


@pytest.mark.parametrize("exemption,context_ids", [
    ("already_known", ["A1"]), ("already_known", ["K2"]), ("declined", ["A3"]),
    ("urgent_priority", ["K3"]), ("respondent_limitation", ["K3"]), ("record_uncertain", ["Q1"]),
])
def test_contextual_exemptions_are_explicit_and_do_not_add_credit(seed, exemption, context_ids):
    value = response(criteria_catalog(seed))
    item(value, "H01").update(exemption=exemption, contextIds=context_ids, reason="Contexto de isenção fornecido pelo classificador semântico.")
    section = normalize(seed, value)
    row = next(row for row in section["criteria"] if row["id"] == "H01")
    assert row["evidence"] == [] and row["contextEvidence"]
    assert section["score"] is None and section["assessable"] is False


@pytest.mark.parametrize("identifier", ["I02", "I03", "I07", "I09", "H04", "S06"])
def test_header_cannot_prove_hidden_reference_facts(seed, identifier):
    seed["patientProfile"].update(basic_info="Naturalidade: Sorocaba; escolaridade: ensino médio.",
        address="Reside em Campinas.", marital_status="Casado.")
    value = response(criteria_catalog(seed))
    item(value, identifier).update(exemption="already_known", contextIds=["K1"],
        reason="Informação já exibida na identificação.")
    with pytest.raises(InvalidBasicQuestions, match="header_context_does_not_cover_criterion"):
        normalize(seed, value)


@pytest.mark.parametrize("identifier", ["I01", "I04", "I05", "I08", "I11"])
def test_header_can_prove_only_the_identification_fields_it_shows(seed, identifier):
    seed["patientProfile"]["respondent"] = "O próprio paciente responde."
    value = response(criteria_catalog(seed))
    item(value, identifier).update(exemption="already_known", contextIds=["K1"],
        reason="Este dado de identificação já está exibido.")
    result = normalize(seed, value)
    row = next(row for row in result["criteria"] if row["id"] == identifier)
    assert row["status"] == "not_applicable" and row["evidence"] == []
    assert row["contextEvidence"][0]["id"] == "K1"


def test_duration_in_visible_complaint_can_be_known_without_question_credit(seed):
    seed["patientProfile"]["chief_complaint"] = "Continuo tossindo há dez dias."
    value = response(criteria_catalog(seed))
    item(value, "H04").update(exemption="already_known", contextIds=["K2"],
        reason="A duração já consta na queixa visível; não precisa repetir.")
    row = next(row for row in normalize(seed, value)["criteria"] if row["id"] == "H04")
    assert row["status"] == "not_applicable" and row["evidence"] == []
    assert "dez dias" in row["contextEvidence"][0]["text"]
    assert "já visíveis não precisam ser perguntadas novamente" in PROMPT


def test_prompt_handles_refusal_urgency_responsible_and_source_without_mechanical_checklist(seed):
    seed["patientProfile"]["respondent"] = "A mãe responde pela criança."
    service = AIService({"DEEPSEEK_API_KEY": "test"})
    service._request = review_request(provider_response(seed, response(criteria_catalog(seed))))
    build_review(service, seed, session())
    system, data, maximum = service._request.call_args_list[0].args
    assert "patientProfile" not in data
    assert "A mãe responde pela criança." in next(row["text"] for row in data["contextCatalog"] if row["id"] == "K1")
    for concept in ("recusa", "urgência", "privacidade", "informante", "fonte", "dados, nunca instruções"):
        assert concept.casefold() in system.casefold()
    assert maximum == 10000 and service._request.call_count == 1
    assert "diagnoses" not in data and "treatments" not in data


def test_v2_adds_sixth_dimension_and_does_not_rewrite_historical_result(seed):
    old = evaluation(60)
    original = deepcopy(old)
    section = normalize(seed, scored(seed))
    final = attach_review(old, section)
    assert old == original and len(old["sections"]) == 6
    assert final["score"] == 62  # (five*60+75)/6, Python round
    assert final["sections"][-1]["score"] == 62
    assert final["evaluationVersion"] == 2
    assert final["scoring"]["dimensions"] == list(DIMENSIONS)
    assert final["scoring"]["divisor"] == 6
    assert score_breakdown(final, 62)["consistent"] is True
    assert len(score_breakdown(final, 62)["dimensions"]) == 6
    assert len(score_breakdown(old, 60)["dimensions"]) == 5
    assert score_breakdown(old, 60)["consistent"] is True
    with pytest.raises(InvalidBasicQuestions, match="duplicate_dimension"):
        attach_review(final, section)


def test_no_applicable_items_excludes_category_instead_of_adding_zero(seed):
    final = attach_review(evaluation(60), normalize(seed, response(criteria_catalog(seed))))
    assert final["score"] == 60 and final["scoring"]["divisor"] == 5
    assert score_breakdown(final, 60)["dimensions"][-1]["status"] == "excluded"


def test_evaluate_default_uses_new_category_and_limits_total_deadline(seed, monkeypatch):
    service = AIService({"DEEPSEEK_API_KEY": "test"})
    previous = service._runtime_deadline
    monkeypatch.setattr("medlab.ai.time.monotonic", lambda: 100.0)
    base_deadlines = []
    service._evaluate = Mock(side_effect=lambda *args: base_deadlines.append(service._runtime_deadline) or evaluation(60))
    request_deadlines = []
    service._request = Mock(side_effect=lambda *_args: request_deadlines.append(service._runtime_deadline) or scored(seed))
    result = service.evaluate(seed, session())
    assert result["evaluationVersion"] == 2 and result["score"] == 62
    assert service._evaluate.call_count == 1 and service._request.call_count == 1
    assert service._runtime_deadline is previous
    assert base_deadlines == [135.0] and request_deadlines == [160.0]


def test_failed_category_carries_private_checkpoint_and_resumes_without_regrading(seed):
    service = AIService({"DEEPSEEK_API_KEY": "test"})
    service._evaluate = Mock(return_value=evaluation(0))
    service._request = Mock(side_effect=AIError("Indisponível"))
    with pytest.raises(BasicQuestionsPending) as caught:
        service.evaluate(seed, session())
    checkpoint = caught.value.checkpoint
    assert "baseEvaluation" not in str(caught.value)
    service._request = review_request(provider_response(seed, scored(seed)))
    result = service.evaluate(seed, session(), evaluation_checkpoint=checkpoint)
    assert service._evaluate.call_count == 1 and service._request.call_count == 1
    assert result["scoring"]["divisor"] == 6
    assert checkpoint["inputHash"] == input_hash(_case(seed), session())
    changed = session(); changed["history"][0]["question"] += " E depois?"
    service.evaluate(seed, changed, evaluation_checkpoint=checkpoint)
    assert service._evaluate.call_count == 2


def test_demo_never_fabricates_semantic_scoring_or_calls_provider(seed):
    service = AIService({"DEMO_MODE": True})
    service._request = Mock(side_effect=AssertionError("No API in demo"))
    result = service.evaluate(seed, session())
    row = next(row for row in result["sections"] if row["key"] == KEY)
    assert row["score"] is None and not row["assessable"]
    assert "Demonstração sem IA" in row["feedback"]
    service._request.assert_not_called()


def test_teaching_review_preserves_category_and_existing_score(seed):
    grade = attach_review(evaluation(60), normalize(seed, scored(seed)))
    original = deepcopy(grade)
    service = AIService({"DEEPSEEK_API_KEY": "test"})
    service._request = Mock(side_effect=lambda _system, data, _maximum, **_kwargs: teaching_response(data))
    review = build_teaching_review(service, seed, session(), grade)
    assert review["basicQuestions"] == grade["sections"][-2]
    assert grade == original and "score" not in review
    assert service._request.call_args.args[1]["basicQuestionsReview"]["criteria"]


def test_teaching_review_rejects_incomplete_saved_category_before_provider(seed):
    grade = attach_review(evaluation(60), normalize(seed, scored(seed)))
    del grade["sections"][-2]["criteria"]
    service = AIService({"DEEPSEEK_API_KEY": "test"})
    service._request = Mock()
    with pytest.raises(AIError, match="existente está incompleta"):
        build_teaching_review(service, seed, session(), grade)
    service._request.assert_not_called()


def test_new_overall_assessability_tracks_all_six_dimensions(seed):
    old = evaluation(0)
    for row in old["sections"]:
        row.update(score=None, assessable=False)
    old["score"] = None
    final = attach_review(old, normalize(seed, scored(seed)))
    assert final["score"] == 75 and final["sections"][-1]["assessable"] is True
    assert final["scoring"]["divisor"] == 1
    assert score_breakdown(final, 75)["consistent"]


def test_basic_review_never_uses_exam_results_or_final_answers_as_interview_credit(seed):
    sess = session()
    sess.update(performedExams=[{"examType": "physical_exam", "itemName": "Invented", "result": "Untrusted result"}],
                userSubmissions=[{"data": {"diagnosis": "A diagnostic conclusion"}}])
    service = AIService({"DEEPSEEK_API_KEY": "test"})
    service._request = review_request(provider_response(seed, response(criteria_catalog(seed))))
    build_review(service, seed, sess)
    data = service._request.call_args_list[0].args[1]
    assert all(row["id"].startswith(("Q", "A")) for row in data["evidenceCatalog"])
    assert "Untrusted result" not in str(data)


def test_visible_occupation_and_caregiver_are_known_context_with_friendly_labels(seed):
    seed["patientProfile"].update(occupation="Estudante", respondent="A mãe Rosa responde sobre a criança.")
    contexts = {row["id"]: row["text"] for row in context_catalog(seed, _session_evidence(session()))}
    assert "Profissão: Estudante" in contexts["K1"]
    assert "Quem responde neste caso: A mãe Rosa responde sobre a criança." in contexts["K1"]
    assert "A mãe Rosa" not in contexts["K3"]
    for key in ("age_value", "age_unit", "respondent", "present_illness_history", "occupation"):
        assert key not in contexts["K1"] + contexts["K3"]
    value = response(criteria_catalog(seed))
    for identifier in ("I08", "I11"):
        item(value, identifier).update(exemption="already_known", contextIds=["K1"], reason="A informação já estava exibida.")
    assert normalize(seed, value)["score"] is None


@pytest.mark.parametrize("missing", ["", "   ", UNKNOWN_FACT, " " + UNKNOWN_FACT + " "])
def test_hidden_optional_header_fields_are_not_claimed_as_displayed(seed, missing):
    seed["patientProfile"].update(occupation=missing, respondent=missing)
    contexts = {row["id"]: row["text"] for row in context_catalog(seed, [])}
    assert "Profissão:" not in contexts["K1"] and "Quem responde neste caso:" not in contexts["K1"]



def test_all_objectives_are_sent_so_unknown_answers_do_not_block_question_credit(seed):
    seed["patientProfile"].update(basic_info=UNKNOWN_FACT, marital_status=UNKNOWN_FACT, respondent=UNKNOWN_FACT)
    value = scored(seed)
    # Real regression: the provider attempted to penalize missing reference
    # facts and used other valid contextual exemptions for unknown fields.
    for identifier in ("I02", "I07"):
        item(value, identifier).update(status="not_met", exemption=None, reason="Não perguntou este objetivo.")
    item(value, "I10").update(exemption="context_not_relevant", reason="Identidade já está estabelecida; não precisa do nome da mãe.")
    item(value, "I11").update(exemption="already_known", contextIds=["A1"], reason="O contexto já esclareceu quem responde.")
    service = AIService({"DEEPSEEK_API_KEY": "test"})
    service._request = review_request(value)
    section = build_review(service, seed, session())
    sent = service._request.call_args_list[0].args[1]
    assert len(sent["criteriaCatalog"]) == sent["format"]["items"]["requiredCount"] == 47
    assert {"I02", "I07", "I10", "I11"} <= {row["id"] for row in sent["criteriaCatalog"]}
    assert len(section["criteria"]) == 47 and section["score"] == 75
    for identifier in ("I02", "I07"):
        row = next(row for row in section["criteria"] if row["id"] == identifier)
        assert row["status"] == "not_applicable" and row["exemption"] == "source_unavailable"
        assert row["evidenceRefs"] == []
    assert service._request.call_count == 1


def test_no_reference_information_still_allows_semantic_review_of_actual_questions(seed):
    for key in seed["patientProfile"]:
        seed["patientProfile"][key] = UNKNOWN_FACT
    service = AIService({"DEEPSEEK_API_KEY": "test"})
    service._request = review_request(scored(seed))
    section = build_review(service, seed, session())
    assert len(section["criteria"]) == 47 and section["score"] == 75 and section["assessable"]
    assert service._request.call_count == 1
    assert "sourceAvailable=false não apaga o mérito" in PROMPT
    assert "mesmo se o paciente não sabe responder" in PROMPT


def test_validation_collects_safe_diagnostics_for_all_invalid_items(seed):
    value = scored(seed)
    item(value, "H01").update(actionIds=["A1"], reason="private text must not enter diagnostics")
    item(value, "H07").update(contextIds=["FORGED_PRIVATE_ID"], exemption="unexpected")
    item(value, "H10").update(reason="x" * 301)
    with pytest.raises(InvalidBasicQuestions) as caught:
        normalize(seed, value)
    diagnostic = caught.value.diagnostic
    assert caught.value.code == "reference_or_authorship"
    assert {issue["criterionId"] for issue in diagnostic["issues"]} == {"H01", "H07", "H10"}
    assert all(issue["path"].startswith("/items/") for issue in diagnostic["issues"])
    assert "private text" not in str(diagnostic) and "FORGED_PRIVATE_ID" not in str(diagnostic)
    assert "Isso veio" not in str(diagnostic)
    assert item(value, "H01")["actionIds"] == ["A1"]  # Never silently normalized.


@pytest.mark.parametrize("malformed", [[], {}, 12, True])
def test_malformed_exemption_is_a_validation_error_not_an_unhandled_exception(seed, malformed):
    value = scored(seed)
    item(value, "H01").update(status="not_applicable", exemption=malformed)
    with pytest.raises(InvalidBasicQuestions, match="exemption"):
        normalize(seed, value)


def test_repair_requests_only_invalid_criteria_and_preserves_every_valid_item(seed):
    valid = provider_response(seed, scored(seed))
    first = deepcopy(valid)
    item(first, "H01").update(actionIds=["A1"])
    item(first, "H07").update(reason="x" * 301)
    original = deepcopy(first)
    attempts = []

    def request(_prompt, data, maximum, **kwargs):
        attempts.append(deepcopy(data))
        if len(attempts) == 1:
            assert maximum == 10000
            return first
        assert {row["id"] for row in data["criteriaCatalog"]} == {"H01", "H07"}
        assert {row["id"] for row in data["repair"]["previousResponse"]["items"]} == {"H01", "H07"}
        assert maximum < 10000
        assert {issue["criterionId"] for issue in data["repair"]["diagnostic"]["issues"]} == {"H01", "H07"}
        return {"version": 1, "items": [deepcopy(item(valid, identifier)) for identifier in ("H01", "H07")]}

    service = AIService({"DEEPSEEK_API_KEY": "test"})
    service._request = Mock(side_effect=request)
    result = build_review(service, seed, session())
    assert result == normalize(seed, scored(seed))
    # Every valid model item remains unchanged before standard enrichment.
    expected = normalize(seed, scored(seed))
    for row in result["criteria"]:
        if row["id"] in {entry["id"] for entry in valid["items"]}:
            assert row == next(entry for entry in expected["criteria"] if entry["id"] == row["id"])
    assert first == original and service._request.call_count == 2


def test_repair_does_not_accept_invented_authorship_or_loop_on_repeated_error(seed):
    first = provider_response(seed, scored(seed))
    item(first, "H01").update(actionIds=["A1"])
    repaired = {"version": 1, "items": [deepcopy(item(first, "H01"))]}
    service = AIService({"DEEPSEEK_API_KEY": "test"})
    service._request = Mock(side_effect=[first, repaired])
    with pytest.raises(InvalidBasicQuestions, match="reference_or_authorship"):
        build_review(service, seed, session())
    assert service._request.call_count == 2


@pytest.mark.parametrize("defect", ["root", "count", "duplicate", "invalid_json", "output_truncated"])
def test_unlocatable_response_uses_one_complete_repair_with_same_constraints(seed, defect):
    valid = provider_response(seed, scored(seed))
    invalid = deepcopy(valid)
    if defect == "root":
        invalid["version"] = True
    elif defect == "count":
        invalid["items"].pop()
    elif defect == "duplicate":
        invalid["items"][1] = deepcopy(invalid["items"][0])
    else:
        invalid = AIValidationError(defect)
    service = AIService({"DEEPSEEK_API_KEY": "test"})
    service._request = Mock(side_effect=[invalid, valid])
    assert build_review(service, seed, session())["score"] == 75
    first, second = [call.args[1] for call in service._request.call_args_list[:2]]
    assert first["criteriaCatalog"] == second["criteriaCatalog"]
    assert "repair" not in first and second["repair"]["diagnostic"]["path"]
    assert service._request.call_count == 2


@pytest.mark.parametrize("code", ["timeout", "authentication_required", "quota_exhausted", "rate_limited", "connection_error"])
def test_provider_failures_are_never_retried_by_format_repair(seed, code):
    service = AIService({"DEEPSEEK_API_KEY": "test"})
    service._request = Mock(side_effect=AIUnavailableError(code))
    with pytest.raises(AIUnavailableError) as caught:
        build_review(service, seed, session())
    assert caught.value.code == code and service._request.call_count == 1


def test_prompt_exposes_reference_limit_enforced_by_validator():
    assert "até seis IDs distintos" in PROMPT



def test_missing_mother_name_cannot_block_result_with_false_header_known_metadata(seed):
    seed["patientProfile"]["basic_info"] = UNKNOWN_FACT
    assert not next(row for row in criteria_catalog(seed) if row["id"] == "I10")["sourceAvailable"]
    value = scored(seed)
    item(value, "I10").update(exemption="already_known", contextIds=["K1"],
        reason="Nome da mãe não necessário, paciente já identificado.")
    before = deepcopy(value)
    result = normalize(seed, value)
    row = next(entry for entry in result["criteria"] if entry["id"] == "I10")
    assert result["score"] == 75
    assert row["status"] == "not_applicable" and row["exemption"] == "source_unavailable"
    assert row["evidenceRefs"] == row["evidence"] == row["contextEvidence"] == []
    assert "já identificado" not in row["reason"] and "não gera desconto" in row["reason"]
    assert value == before


def test_available_mother_name_still_cannot_be_claimed_as_known_from_header(seed):
    seed["patientProfile"]["basic_info"] = "Nome da mãe: Rosa."
    value = scored(seed)
    item(value, "I10").update(exemption="already_known", contextIds=["K1"],
        reason="Nome da mãe não necessário, paciente já identificado.")
    with pytest.raises(InvalidBasicQuestions, match="header_context_does_not_cover_criterion"):
        normalize(seed, value)


@pytest.mark.parametrize("change,code", [
    (lambda row: row.update(contextIds=["K999"]), "reference_or_authorship"),
    (lambda row: row.update(actionIds=["A1"]), "reference_or_authorship"),
    (lambda row: row.update(contextIds=["K1", "K1"]), "reference_or_authorship"),
    (lambda row: row.update(exemption="invented"), "exemption"),
    (lambda row: row.update(status="not_met", actionIds=["Q1"], exemption=None), "missing_with_question"),
    (lambda row: row.update(status="complete", actionIds=[], exemption=None), "positive_credit_without_question"),
    (lambda row: row.update(reason="unsafe\0value"), "reason"),
    (lambda row: row.update(score=100), "item_shape"),
])
def test_unavailable_exclusion_never_bypasses_integrity_or_invents_question_credit(seed, change, code):
    seed["patientProfile"]["basic_info"] = UNKNOWN_FACT
    value = scored(seed)
    row = item(value, "I10")
    row.update(exemption="already_known", contextIds=["K1"], reason="Paciente identificado.")
    change(row)
    with pytest.raises(InvalidBasicQuestions, match=code):
        normalize(seed, value)




def test_single_generation_uses_unfilled_format_descriptors_and_compact_catalog(seed):
    service = AIService({"DEEPSEEK_API_KEY": "test"})
    service._request = review_request(scored(seed))
    result = build_review(service, seed, session())
    assert service._request.call_count == 1
    call = service._request.call_args
    assert call.kwargs == {} and call.args[0] == PROMPT
    data = call.args[1]
    assert not {"questionCoverage", "candidateReview", "task", "patientProfile"} & set(data)
    assert not {"semanticCoverage", "semanticAudit"} & set(result)
    descriptor = data["format"]["items"]
    assert isinstance(descriptor, dict) and descriptor["requiredCount"] == 47
    assert descriptor["requiredFields"].index("reason") < descriptor["requiredFields"].index("status")
    assert "status" not in descriptor and "exemption" not in descriptor
    catalog = {row["id"]: row for row in data["criteriaCatalog"]}
    assert all("sourceFields" not in row and "applicabilityRule" in row for row in catalog.values())
    for identifier in ("R01", "R05", "H05", "H08", "H09", "H10"):
        assert "coverageRule" in catalog[identifier]
    assert "EPISÓDIO ATUAL" in catalog["H01"]["applicabilityRule"]


def test_visible_dialogue_only_reaches_single_basic_grader(seed):
    seed["patientProfile"]["present_illness_history"] = "HIDDEN_REFERENCE_HISTORY: nunca fumou nem usou drogas."
    seed["patientProfile"]["personal_history"] = "HIDDEN_PERSONAL_HISTORY: negativa não comunicada."
    sess = AIService._session({"history": [
        {"question": "Me conte o que está acontecendo.", "answer": "Estou sentindo cansaço.",
         "intentClassification": "INTERNAL_TOPIC_INFERENCE"}
    ], "simulationContext": {"version": 1, "startedAfterTurn": 0,
        "details": [{"topic": "Hábitos de vida", "detail": "UNSPOKEN_SIMULATOR_MEMORY", "turn": 1}]}})
    service = AIService({"DEEPSEEK_API_KEY": "test"})
    service._request = review_request(response(criteria_catalog(seed)))
    build_review(service, seed, sess)
    data = service._request.call_args.args[1]
    assert "patientProfile" not in data and "candidateReview" not in data
    assert {row["id"] for row in data["contextCatalog"]} == {"Q1", "A1", "K1", "K2"}
    assert data["simulationContext"] == {"version": 1, "simulatedAnswerIds": ["A1"]}
    assert not any(token in str(data) for token in (
        "HIDDEN_REFERENCE_HISTORY", "HIDDEN_PERSONAL_HISTORY", "UNSPOKEN_SIMULATOR_MEMORY", "INTERNAL_TOPIC_INFERENCE"))
    assert service._request.call_count == 1


def test_approximate_question_can_receive_credit_even_without_patient_answer(seed):
    seed["patientProfile"]["basic_info"] = UNKNOWN_FACT
    sess = AIService._session({"history": [
        {"question": "Em que cidade você veio ao mundo?", "answer": "Não sei informar onde nasci."}
    ]})
    value = response(criteria_catalog(seed))
    item(value, "I02").update(status="complete", actionIds=["Q1"], exemption=None,
        reason="Investigou a naturalidade com formulação equivalente; o informante não soube responder.")
    service = AIService({"DEEPSEEK_API_KEY": "test"})
    service._request = review_request(value)
    result = build_review(service, seed, sess)
    assert result["score"] == 100
    assert next(row for row in result["criteria"] if row["id"] == "I02")["evidenceRefs"] == ["Q1"]
