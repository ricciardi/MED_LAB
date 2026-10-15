"""Import failures expose all actionable schema problems before an audit request."""
from copy import deepcopy
from unittest.mock import Mock

import pytest

from medlab.ai import AIService, AIValidationError, _preflight_script, _validated_audit, _import_audit_budget
from medlab.case_schema import UNKNOWN_FACT
from test_clinical_facts import factual_inputs
from test_importer import ARTICLE, AUDIT_RESPONSE, SPEC, adaptation, provenance, real_case


def test_profile_and_differentials_are_aggregated_without_changing_paid_candidate():
    result = adaptation()
    case = result["case"]
    case["patientProfile"].update(medications="Medicação documentada", social_history=UNKNOWN_FACT,
                                  vaccination_history=UNKNOWN_FACT)
    case["patientProfile"]["symptom_details"] = {"Início dos sintomas": {"value": "Ontem"}}
    case["diagnoses"]["differentialDiagnoses"] = ["Cisto dermoide"] * 11
    original = deepcopy(result)
    with pytest.raises(AIValidationError) as caught:
        _preflight_script(case, result)
    error = caught.value
    assert error.code == "schema_invalid"
    assert {"patientProfile.medications", "patientProfile.social_history", "patientProfile.symptom_details",
            "diagnoses.differentialDiagnoses", "diagnoses.differentialDiagnoses.0"} <= set(error.paths)
    assert "patientProfile.vaccination_history" not in error.paths
    assert "other_medical_history" in error.repair_feedback["expectedTypes"]["patientProfile"]["rule"]
    assert result == original  # No facts dropped, renamed, or silently inferred.


def test_null_exam_map_does_not_erase_ordered_exams_or_make_up_normal_results():
    result = adaptation()
    result["case"]["auxiliaryLabs"] = None
    result["case"]["mustAuxiliaryLabs"] = ["Hemograma completo", "Proteína C reativa"]
    original = deepcopy(result)
    with pytest.raises(AIValidationError) as caught:
        _preflight_script(result["case"], result)
    assert "auxiliaryLabs" in caught.value.paths
    assert result == original


@pytest.mark.parametrize("age", [True, -1, 121, float("inf"), float("nan"), "30"])
def test_profile_age_rejects_invalid_types_and_bounds(age):
    result = adaptation()
    result["case"]["patientProfile"]["age_value"] = age
    with pytest.raises(AIValidationError) as caught:
        _preflight_script(result["case"])
    assert "patientProfile.age_value" in caught.value.paths


def test_optional_vaccination_and_empty_real_differentials_remain_valid():
    result = adaptation()
    result["case"]["patientProfile"]["vaccination_history"] = UNKNOWN_FACT
    _preflight_script(result["case"], result)


@pytest.mark.parametrize("factual", [False, True])
def test_both_writers_and_auditors_require_accessible_history_and_correct_speaker(factual):
    config = {"DEEPSEEK_API_KEY": "test", "CASE_REUSE_SCOPE": "noncommercial", "CASE_ALLOW_CLINICAL_FACTS": True}
    ai = AIService(config)
    if factual:
        _, base, facts, script, audit = factual_inputs()
        ai._request = Mock(side_effect=[facts, script, audit])
    else:
        base = provenance()
        ai._request = Mock(side_effect=[adaptation(), deepcopy(AUDIT_RESPONSE)])
    ai.adapt_case({"articleText": ARTICLE, "case_prompt": "Cough", "final_diagnosis": "Pneumonia"}, base, SPEC)
    writer, auditor = [call.args[0] for call in ai._request.call_args_list[-2:]]
    for prompt in (writer, auditor):
        assert "Medicamentos em uso" in prompt and "other_medical_history" in prompt
        assert "respondent" in prompt and "não invente qual familiar" in prompt
        assert "pseudobolsas" in prompt and "palpação" in prompt
    assert "unknownsPreserved também exige" in auditor
    assert "patientBeforeDiagnosis=false" in auditor


def test_invalid_paid_candidate_stops_before_auditor():
    result = adaptation()
    result["case"]["patientProfile"]["medications"] = "Medicação já documentada"
    ai = AIService({"DEEPSEEK_API_KEY": "test"})
    ai._request = Mock(return_value=result)
    with pytest.raises(AIValidationError) as caught:
        ai.adapt_case({"articleText": ARTICLE}, provenance(), SPEC)
    assert "patientProfile.medications" in caught.value.paths
    assert ai._request.call_count == 1


def test_exact_final_diagnosis_in_profile_is_rejected_before_provider_audit():
    case = adaptation()['case']
    case['diagnoses']['mainDiagnosis']['disease'] = 'Crise renal esclerodérmica'
    case['patientProfile']['present_illness_history'] = 'A equipe suspeitou de CRISE RENAL ESCLERODERMICA.'
    with pytest.raises(AIValidationError) as caught:
        _preflight_script(case)
    assert caught.value.flags == ['patientBeforeDiagnosis']
    assert '/patientProfile/present_illness_history' in caught.value.paths


def test_partial_syndrome_and_professional_results_need_explicit_semantic_audit():
    case = adaptation()['case']
    case['diagnoses']['mainDiagnosis']['disease'] = 'Pan-hipopituitarismo completo com diabetes insípido central'
    case['patientProfile']['present_illness_history'] = 'Foi suspeitado diabetes insípido; os exames mostraram alterações hormonais.'
    _preflight_script(case)  # No guessing a clinical synonym/substring in deterministic code.
    audit = deepcopy(AUDIT_RESPONSE)
    audit['patientKnowledge'][1].update(knowledge='professional_only', rationale='A fonte atribui a hipótese e a interpretação hormonal à equipe, não ao conhecimento relatado do paciente.')
    with pytest.raises(AIValidationError) as caught:
        _validated_audit(audit, set(AUDIT_RESPONSE) - {'patientKnowledge', 'sourceChecklist'}, case, [{'id': 'S0001'}])
    assert 'patientBeforeDiagnosis' in caught.value.flags
    assert caught.value.repair_feedback['issues'][0]['path'] == '/patientProfile/present_illness_history'


@pytest.mark.parametrize('failure', ['missing', 'duplicate', 'invalid_span', 'missing_rationale'])
def test_patient_knowledge_requires_complete_source_backed_coverage(failure):
    audit = deepcopy(AUDIT_RESPONSE)
    if failure == 'missing':
        audit.pop('patientKnowledge')
    elif failure == 'duplicate':
        audit['patientKnowledge'][1] = deepcopy(audit['patientKnowledge'][0])
    elif failure == 'invalid_span':
        audit['patientKnowledge'][0]['sourceSpanIds'] = ['S9999']
    else:
        audit['patientKnowledge'][0]['rationale'] = ''
    with pytest.raises(AIValidationError) as caught:
        _validated_audit(audit, set(AUDIT_RESPONSE) - {'patientKnowledge', 'sourceChecklist'}, adaptation()['case'], [{'id': 'S0001'}])
    assert 'patientBeforeDiagnosis' in caught.value.flags


def test_patient_knowledge_is_private_and_budget_scales_with_profile():
    case = adaptation()['case']
    flags = set(AUDIT_RESPONSE) - {'patientKnowledge', 'sourceChecklist'}
    assert set(_validated_audit(deepcopy(AUDIT_RESPONSE), flags, case, [{'id': 'S0001'}])) == flags
    initial = _import_audit_budget(case)
    case['patientProfile']['respondent'] = 'Pais da criança.'
    assert _import_audit_budget(case) == initial + 100


@pytest.mark.parametrize('second_success', [False, True])
def test_inconsistent_audit_retries_only_auditor_once(second_success):
    ai = AIService({'DEEPSEEK_API_KEY': 'test', 'AI_IMPORT_REPAIR_ATTEMPTS': 2})
    inconsistent = {**deepcopy(AUDIT_RESPONSE), 'factsSupported': False, 'issues': []}
    ai._request = Mock(side_effect=[adaptation(), inconsistent,
        deepcopy(AUDIT_RESPONSE) if second_success else deepcopy(inconsistent)])
    if second_success:
        assert ai.adapt_case({'articleText': ARTICLE}, provenance(), SPEC)['reviewStatus'] == 'review_required'
    else:
        with pytest.raises(AIValidationError) as caught:
            ai.adapt_case({'articleText': ARTICLE}, provenance(), SPEC)
        assert caught.value.code == 'audit_inconsistent'
    assert ai._request.call_count == 3
    assert 'Audite rigorosamente' in ai._request.call_args_list[-1].args[0]
    assert ai._request.call_args_list[-1].args[1]['validationIssues']['code'] == 'audit_inconsistent'


def test_reaudit_licensed_draft_has_no_writer_request_and_changes_nothing():
    case = real_case()
    original = deepcopy(case)
    ai = AIService({'DEEPSEEK_API_KEY': 'test'})
    ai._request = Mock(return_value=deepcopy(AUDIT_RESPONSE))
    result = ai.audit_prepared_case({'articleText': ARTICLE, 'provenance': provenance()}, case)
    assert all(result.values()) and case == original
    ai._request.assert_called_once()
    assert 'Audite rigorosamente' in ai._request.call_args.args[0]


def test_reaudit_factual_draft_reuses_hash_checked_facts_without_extraction():
    old, base, _, _, audit = factual_inputs()
    case = old.adapt_case({'articleText': ARTICLE}, base, SPEC)
    ai = AIService({'DEEPSEEK_API_KEY': 'test', 'CASE_REUSE_SCOPE': 'noncommercial', 'CASE_ALLOW_CLINICAL_FACTS': True})
    ai._request = Mock(return_value=deepcopy(audit))
    assert all(ai.audit_prepared_case({'articleText': ARTICLE, 'provenance': base}, case).values())
    ai._request.assert_called_once()
    assert 'Audite um roteiro' in ai._request.call_args.args[0]
    case['provenance']['sourceFacts'][0]['sourceSha256'] = 'f' * 64
    with pytest.raises(Exception, match='snapshot'):
        ai.audit_prepared_case({'articleText': ARTICLE, 'provenance': base}, case)
    assert ai._request.call_count == 1
