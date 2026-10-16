"""Contextual rubric, semantic-provider contract and versioned grade arithmetic.

Mocked classifications test boundaries, not the clinical accuracy of an LLM.
"""
from copy import deepcopy
from unittest.mock import Mock

import pytest

from medlab.ai import AIError, AIService, BasicQuestionsPending, _case, _session_evidence
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
        "I09": "possibilidade teórica não basta",
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


def test_prompt_requires_a_specific_grounded_gap_and_cross_turn_coverage():
    for constraint in (
        "disponibilidade não é pertinência", "possibilidade teórica não é obrigação",
        "Antes de not_met, releia TODAS as perguntas", "incluindo sínteses e confirmações",
        "se a parte restante é dispensável/já conhecida", "Reason de partial/not_met deve nomear EXATAMENTE",
        "Nunca combine not_met com uma justificativa de que o item não se aplica",
    ):
        assert constraint in PROMPT


@pytest.mark.parametrize("identifier", ["I02", "I03", "I07", "R01", "R03", "R04", "F01", "V01", "S01", "S02", "S04", "S05", "S06", "S08", "S09"])
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


def test_transition_requires_explanation_not_only_system_questions(seed):
    catalog = {row["id"]: row for row in criteria_catalog(seed)}
    assert "a explicação precisa existir" in catalog["R01"]["applicabilityRule"]
    assert "não provam explicação da transição" in catalog["R01"]["coverageRule"]
    assert "partial; complete exige ambas" in catalog["R01"]["coverageRule"]
    assert "ANAMNESE AMPLA DE GRADUAÇÃO" in PROMPT
    assert "finalidade PEDAGÓGICA já justifica" in PROMPT
    assert "context_not_relevant é permitido SOMENTE para context_conditional" in PROMPT


def test_system_review_three_aspects_are_semantic_not_three_turns(seed):
    rule = next(row for row in criteria_catalog(seed) if row["id"] == "R05")["coverageRule"]
    for requirement in ("três aspectos sintomáticos distintos e pertinentes", "perguntas compostas",
                        "aspectos já conhecidos", "não exigir três turnos", "recusa e urgência",
                        "Não inventar aspectos irrelevantes"):
        assert requirement in rule


def test_contextual_provider_decision_keeps_ids_arithmetic_and_one_call(seed):
    # Controlled classification models the desired contract, not proof of the
    # real model's semantic accuracy. The live retest must make that comparison.
    seed["patientProfile"]["basic_info"] = "Nome da mãe registrado; religião declarada, sem preferência de cuidado relacionada."
    value = response(criteria_catalog(seed))
    for identifier in ("Q02", "I09", "I10", "S03"):
        item(value, identifier).update(exemption="context_not_relevant", reason="Não há contexto que exija esse dado neste encontro.")
    item(value, "H09").update(status="complete", actionIds=["Q1", "Q2"], exemption=None,
        reason="Explorou sequência e evolução nas duas perguntas; não há tratamento prévio pendente de esclarecer.")
    service = AIService({"DEEPSEEK_API_KEY": "test"})
    service._request = Mock(return_value=value)
    result = build_review(service, seed, session())
    assert result["score"] == 100
    assert next(row for row in result["criteria"] if row["id"] == "H09")["evidenceRefs"] == ["Q1", "Q2"]
    assert all(next(row for row in result["criteria"] if row["id"] == identifier)["status"] == "not_applicable"
               for identifier in ("Q02", "I09", "I10", "S03"))
    assert service._request.call_count == 1 and service._request.call_args.args[2] == 10000


def test_semantic_equivalents_keep_the_actual_question_and_partial_coverage(seed):
    result = normalize(seed, scored(seed))
    first = next(row for row in result["criteria"] if row["id"] == "H01")
    assert first["evidence"] == [{"id": "Q1", "text": "Isso veio de repente ou foi chegando aos poucos?"}]
    assert result["score"] == 75
    assert next(group for group in result["groups"] if group["id"] == "hpi")["score"] == 75
    assert "aceite paráfrases" in PROMPT and "nem use classifiedTopics como prova suficiente" in PROMPT


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


@pytest.mark.parametrize("status", ["complete", "partial", "not_met"])
def test_missing_source_never_becomes_negative_or_required_topic(seed, status):
    seed["patientProfile"]["family_history"] = UNKNOWN_FACT
    value = response(criteria_catalog(seed))
    item(value, "F01").update(status=status, exemption=None, actionIds=[] if status == "not_met" else ["Q1"])
    with pytest.raises(InvalidBasicQuestions, match="source_unavailable"):
        normalize(seed, value)


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


@pytest.mark.parametrize("identifier", ["I02", "I03", "I07", "H04", "S06"])
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
    assert "inclusive duração quando informada" in PROMPT


def test_prompt_handles_refusal_urgency_responsible_and_source_without_mechanical_checklist(seed):
    seed["patientProfile"]["respondent"] = "A mãe responde pela criança."
    service = AIService({"DEEPSEEK_API_KEY": "test"})
    service._request = Mock(return_value=response(criteria_catalog(seed)))
    build_review(service, seed, session())
    system, data, maximum = service._request.call_args.args
    assert data["patientProfile"]["respondent"] == "A mãe responde pela criança."
    for concept in ("bebê", "recusa", "urgência", "três perguntas irrelevantes", "repetir negativas", "não exija nome da mãe", "Elaborações", "não duplique"):
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
    service._request = Mock(side_effect=lambda *_: request_deadlines.append(service._runtime_deadline) or scored(seed))
    result = service.evaluate(seed, session())
    assert result["evaluationVersion"] == 2 and result["score"] == 62
    assert service._evaluate.call_count == service._request.call_count == 1
    assert service._runtime_deadline is previous
    assert base_deadlines == [140.0] and request_deadlines == [160.0]


def test_failed_category_carries_private_checkpoint_and_resumes_without_regrading(seed):
    service = AIService({"DEEPSEEK_API_KEY": "test"})
    service._evaluate = Mock(return_value=evaluation(0))
    service._request = Mock(side_effect=AIError("Indisponível"))
    with pytest.raises(BasicQuestionsPending) as caught:
        service.evaluate(seed, session())
    checkpoint = caught.value.checkpoint
    assert "baseEvaluation" not in str(caught.value)
    service._request = Mock(return_value=scored(seed))
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
    service._request = Mock(side_effect=lambda _system, data, _maximum: teaching_response(data))
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
    service._request = Mock(return_value=response(criteria_catalog(seed)))
    build_review(service, seed, sess)
    data = service._request.call_args.args[1]
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
