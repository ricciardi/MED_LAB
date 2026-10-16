"""Private import stages resume without paying twice or accepting rejected results."""
from copy import deepcopy
import json
from unittest.mock import Mock

import pytest

from medlab.ai import (AIService, AIValidationError, AIUnavailableError, _article_spans,
                      _literal_excerpt, _resolve_atomic_spans, _resolve_evidence_spans, _validated_audit)
from test_ai import reply
from test_clinical_facts import factual_inputs
from test_importer import ARTICLE, AUDIT, AUDIT_RESPONSE, SPEC, adaptation, provenance


def service(tmp_path, repairs=1):
    return AIService({"DEEPSEEK_API_KEY": "private-test-key", "AI_IMPORT_REPAIR_ATTEMPTS": repairs},
                     stage_cache_dir=tmp_path / "stages")


def source():
    return {"articleText": ARTICLE, "case_prompt": "Cough", "final_diagnosis": "Pneumonia"}


def entries(tmp_path):
    return [json.loads(path.read_text()) for path in (tmp_path / "stages").glob("*.json")]


def test_missing_leaf_is_diagnosed_and_one_repair_is_fully_audited(tmp_path):
    ai = service(tmp_path)
    bad = adaptation()
    missing_path = bad["evidence"].pop()["path"]
    ai._request = Mock(side_effect=[bad, adaptation(), deepcopy(AUDIT_RESPONSE)])
    case = ai.adapt_case(source(), provenance(), SPEC)
    assert case["reviewStatus"] == "review_required"
    assert ai._request.call_count == 3
    repair = ai._request.call_args_list[1].args[1]
    assert repair["validationIssues"]["code"] == "evidence_missing"
    assert missing_path in repair["validationIssues"]["paths"]
    assert repair["previousResult"] == bad
    assert "180 caracteres" in ai._request.call_args_list[0].args[0]
    saved = entries(tmp_path)
    assert len(saved) == 3 and sum(e["state"] == "rejected" for e in saved) == 1
    resumed = service(tmp_path)
    resumed._request = Mock(side_effect=AssertionError("No repeat provider call"))
    assert resumed.adapt_case(source(), provenance(), SPEC)["caseId"] == case["caseId"]
    resumed._request.assert_not_called()
    for path in (tmp_path / "stages").glob("*.json"):
        assert path.stat().st_mode & 0o777 == 0o600
        assert "private-test-key" not in path.read_text()


def test_bad_cached_repair_stays_rejected_without_new_calls(tmp_path):
    ai = service(tmp_path)
    bad = adaptation()
    bad["missingFacts"] = []
    ai._request = Mock(side_effect=[deepcopy(bad), deepcopy(bad)])
    with pytest.raises(AIValidationError) as error:
        ai.adapt_case(source(), provenance(), SPEC)
    assert error.value.code == "evidence_missing" and error.value.paths
    assert ai._request.call_count == 2
    resumed = service(tmp_path)
    resumed._request = Mock(side_effect=AssertionError("Do not repeat a cached rejection"))
    with pytest.raises(AIValidationError, match="evidências"):
        resumed.adapt_case(source(), provenance(), SPEC)
    resumed._request.assert_not_called()


def test_audit_outage_resumes_candidate_and_only_calls_audit_again(tmp_path):
    ai = service(tmp_path)
    ai._request = Mock(side_effect=[adaptation(), AIUnavailableError("service_unavailable")])
    with pytest.raises(AIUnavailableError):
        ai.adapt_case(source(), provenance(), SPEC)
    assert len(entries(tmp_path)) == 1
    resumed = service(tmp_path)
    resumed._request = Mock(return_value=deepcopy(AUDIT_RESPONSE))
    assert resumed.adapt_case(source(), provenance(), SPEC)["reviewStatus"] == "review_required"
    resumed._request.assert_called_once()
    assert "Audite rigorosamente" in resumed._request.call_args.args[0]


def test_changed_source_hash_does_not_reuse_other_source_stage(tmp_path):
    ai = service(tmp_path)
    ai._request = Mock(side_effect=[adaptation(), deepcopy(AUDIT_RESPONSE), adaptation(), deepcopy(AUDIT_RESPONSE)])
    ai.adapt_case(source(), provenance(), SPEC)
    ai.adapt_case(source(), provenance(xmlSha256="d" * 64), SPEC)
    assert ai._request.call_count == 4
    assert len(entries(tmp_path)) == 4


def test_schema_field_and_audit_flags_are_safe_and_actionable(tmp_path):
    ai = service(tmp_path, repairs=0)
    bad = adaptation()
    bad["case"]["summary"] = "x" * 801
    ai._request = Mock(side_effect=[bad, deepcopy(AUDIT_RESPONSE)])
    with pytest.raises(AIValidationError) as error:
        ai.adapt_case(source(), provenance(), SPEC)
    assert error.value.code == "schema_invalid" and error.value.paths == ["summary"]
    assert "x" * 30 not in str(error.value)
    ai2 = service(tmp_path / "other", repairs=0)
    ai2._request = Mock(side_effect=[adaptation(), {**AUDIT_RESPONSE, "patientBeforeDiagnosis": False,
        "issues": [{"flag": "patientBeforeDiagnosis", "path": "/patientProfile", "reason": "O perfil contém uma interpretação profissional da fonte."}]}])
    with pytest.raises(AIValidationError) as error:
        ai2.adapt_case(source(), provenance(), SPEC)
    assert error.value.flags == ["patientBeforeDiagnosis"]
    assert error.value.code == "audit_failed"


def test_nd_cache_retains_atomic_facts_not_literal_excerpts(tmp_path):
    _, base, facts, script, audit = factual_inputs()
    ai = AIService({"DEEPSEEK_API_KEY": "test-key", "CASE_REUSE_SCOPE": "noncommercial",
                    "CASE_ALLOW_CLINICAL_FACTS": True}, stage_cache_dir=tmp_path / "stages")
    ai._request = Mock(side_effect=[facts, script, audit])
    case = ai.adapt_case(source(), base, SPEC)
    stored = entries(tmp_path)
    assert len(stored) == 3
    serialized = json.dumps(stored, ensure_ascii=False)
    assert "cough for three weeks" not in serialized and "The final diagnosis was pneumonia." not in serialized
    assert "sourceLocation" in serialized and "sourceSha256" in serialized
    ai._request = Mock(side_effect=AssertionError("No provider repeat"))
    resumed = ai.adapt_case(source(), base, SPEC)
    assert resumed["provenance"]["sourceFacts"] == case["provenance"]["sourceFacts"]
    ai._request.assert_not_called()


def test_truncated_output_is_kept_private_and_repaired_with_bounded_output(tmp_path, monkeypatch):
    ai = service(tmp_path)
    truncated = reply({"case": "partial"}, finish="length")
    post = Mock(side_effect=[truncated, reply(adaptation()), reply(AUDIT_RESPONSE)])
    monkeypatch.setattr("medlab.ai.requests.post", post)
    assert ai.adapt_case(source(), provenance(), SPEC)["reviewStatus"] == "review_required"
    assert [call.kwargs["json"]["max_tokens"] for call in post.call_args_list] == [12000, 24000, 2700]
    assert ai.usage_snapshot()["requests"] == 3
    cached = [e for e in entries(tmp_path) if e["state"] == "rejected"]
    assert cached[0]["diagnostic"]["code"] == "output_truncated"
    assert cached[0]["result"]["rawText"] == '{"case": "partial"}'


def test_diagnostics_never_accept_arbitrary_provider_error_text():
    error = AIValidationError("SECRET_PROVIDER_BODY", paths=["SECRET_BODY", "patientProfile / secret value"],
                              flags=["SECRET_PROVIDER_FLAG"])
    assert error.code == "schema_invalid" and error.paths == [] and error.flags == []
    assert "SECRET" not in str(error)


def test_source_spans_are_exact_contiguous_and_unknown_ids_fail_closed():
    article = ("The patient described a documented symptom. " * 80).strip()
    spans = _article_spans(article)
    assert spans == _article_spans(article)
    assert all(span["text"] in article and len(span["text"]) <= 1200 for span in spans)
    assert len({span["id"] for span in spans}) == len(spans)
    evidence = _resolve_evidence_spans([{"path": "/patientProfile/chief_complaint", "spanIds": [spans[0]["id"]]}], spans)
    assert evidence[0]["excerpt"] == spans[0]["text"]
    with pytest.raises(AIValidationError):
        _resolve_evidence_spans([{"path": "/patientProfile/chief_complaint", "spanIds": ["S9999"]}], spans)
    resolved = _resolve_atomic_spans({"facts": [{"factId": "F001", "text": "Um sintoma documentado.", "spanId": spans[0]["id"]}]}, spans)
    assert resolved["facts"][0]["excerpt"] == spans[0]["text"]


def test_whitespace_normalization_recovers_original_quote_without_changing_words():
    original = "A lesion (Fig. 1a ) was seen [ 7 ]."
    assert _literal_excerpt(original, "A lesion (Fig. 1a) was seen [7].") == original
    assert _literal_excerpt("The patient is now here.", "The patient is nowhere.") is None
    assert _literal_excerpt(original, "A lesion was seen.") is None
    assert _literal_excerpt(original, "A lesion ... was seen [7].") is None


def test_paid_initial_candidate_is_hashed_and_repaired_without_first_generation(tmp_path):
    ai = service(tmp_path)
    bad = adaptation()
    bad["case"]["patientProfile"]["symptom_details"] = {"início": "Há três semanas."}
    ai._request = Mock(side_effect=[adaptation(), deepcopy(AUDIT_RESPONSE)])
    result = ai.adapt_case(source(), provenance(), SPEC, initial_candidate=bad)
    assert result["reviewStatus"] == "review_required"
    assert ai._request.call_count == 2
    diagnostic = ai._request.call_args_list[0].args[1]["validationIssues"]
    assert diagnostic["paths"] == ["patientProfile.symptom_details"]
    assert "Objeto" in diagnostic["expectedTypes"]["patientProfile.symptom_details"]
    seeded = [entry for entry in entries(tmp_path) if "initialCandidateSha256" in entry]
    assert len(seeded) == 1 and seeded[0]["state"] == "rejected"


def test_audit_issues_reach_repair_privately_but_not_saved_provenance(tmp_path):
    ai = service(tmp_path)
    issue = {"flag": "factsSupported", "path": "/patientProfile/present_illness_history",
             "reason": "Uma afirmação deve ser conferida no momento inicial."}
    repaired = adaptation()
    repaired["case"]["patientProfile"]["present_illness_history"] = "Tenho tosse com duração de três semanas."
    ai._request = Mock(side_effect=[adaptation(), {**AUDIT_RESPONSE, "factsSupported": False, "issues": [issue]},
                                   repaired, {**AUDIT_RESPONSE, "issues": []}])
    case = ai.adapt_case(source(), provenance(), SPEC)
    assert ai._request.call_count == 4
    assert ai._request.call_args_list[2].args[1]["validationIssues"]["issues"] == [issue]
    assert case["provenance"]["audit"] == AUDIT and "issues" not in case["provenance"]["audit"]


def test_audit_truncation_retries_audit_without_rewriting_candidate(tmp_path, monkeypatch):
    ai = service(tmp_path)
    post = Mock(side_effect=[reply(adaptation()), reply({"part": "audit"}, finish="length"), reply(AUDIT_RESPONSE)])
    monkeypatch.setattr("medlab.ai.requests.post", post)
    assert ai.adapt_case(source(), provenance(), SPEC)["reviewStatus"] == "review_required"
    assert post.call_count == 3
    assert [call.kwargs["json"]["max_tokens"] for call in post.call_args_list] == [12000, 2700, 5400]
    assert "Audite rigorosamente" in post.call_args.kwargs["json"]["messages"][0]["content"]


def test_preflight_aggregates_exam_name_mismatch_and_long_moment_before_audit(tmp_path):
    ai = service(tmp_path)
    bad = adaptation()
    bad["case"]["mustPhysicalExams"] = ["Exame sem resultado documentado"]
    bad["clinicalMoment"] = "x" * 1201
    ai._request = Mock(side_effect=[adaptation(), deepcopy(AUDIT_RESPONSE)])
    assert ai.adapt_case(source(), provenance(), SPEC, initial_candidate=bad)["reviewStatus"] == "review_required"
    assert ai._request.call_count == 2
    diagnostic = ai._request.call_args_list[0].args[1]["validationIssues"]
    assert set(diagnostic["paths"]) == {"mustPhysicalExams", "optionalPhysicalExams", "clinicalMoment"}
    assert "1200" in diagnostic["expectedTypes"]["clinicalMoment"]


def test_final_diagnosis_cannot_be_replaced_by_unresolved_presentation(tmp_path):
    ai = service(tmp_path)
    bad = adaptation()
    bad["case"]["diagnoses"]["mainDiagnosis"]["disease"] = "Massa pélvica a esclarecer"
    ai._request = Mock(side_effect=[adaptation(), deepcopy(AUDIT_RESPONSE)])
    ai.adapt_case(source(), provenance(), SPEC, initial_candidate=bad)
    diagnostic = ai._request.call_args_list[0].args[1]["validationIssues"]
    assert diagnostic["flags"] == ["factsSupported"]
    assert diagnostic["paths"] == ["/diagnoses/mainDiagnosis/disease"]
    assert "diagnóstico FINAL" in ai._request.call_args_list[-1].args[0]


@pytest.mark.parametrize("missing", ["confirmatoryFindings", "definitiveTreatment"])
def test_audit_checklist_catches_missing_confirmation_or_surgery_even_if_flags_pass(missing):
    audit = deepcopy(AUDIT_RESPONSE)
    audit["sourceChecklist"][missing] = {"sourceFact": "Cirurgia documentada" if missing == "definitiveTreatment" else "Biópsia confirmatória documentada",
                                        "casePaths": [], "covered": True}
    with pytest.raises(AIValidationError) as error:
        _validated_audit(audit, set(AUDIT), adaptation()["case"])
    assert error.value.flags == ["factsSupported"]
    assert "A fonte documenta" in error.value.repair_feedback["issues"][0]["reason"]


def test_audit_checklist_requires_result_and_treatment_fields_not_rubric_mentions():
    case = adaptation()["case"]
    case["auxiliaryLabs"] = {"Histologia": "Pneumonia confirmada"}
    case["treatments"] = "Conduta histórica documentada."
    audit = deepcopy(AUDIT_RESPONSE)
    audit["sourceChecklist"]["confirmatoryFindings"] = {"sourceFact": "Pneumonia confirmada", "casePaths": ["/auxiliaryLabs/Histologia"], "covered": True}
    audit["sourceChecklist"]["definitiveTreatment"] = {"sourceFact": "Conduta histórica documentada.", "casePaths": ["/treatments"], "covered": True}
    assert _validated_audit(audit, set(AUDIT), case) == AUDIT
    audit["sourceChecklist"]["definitiveTreatment"]["casePaths"] = ["/rubric/treatment/criteria/0"]
    with pytest.raises(AIValidationError):
        _validated_audit(audit, set(AUDIT), case)
    with pytest.raises(AIValidationError):
        _validated_audit(AUDIT, set(AUDIT), case)


def test_known_review_issues_repair_paid_candidate_without_reauditing_bad_version(tmp_path):
    ai = service(tmp_path)
    issue = {"code": "audit_failed", "flags": ["factsSupported"], "paths": ["/treatments"],
             "issues": [{"flag": "factsSupported", "path": "/treatments", "reason": "Conferir a conduta final documentada."}]}
    ai._request = Mock(side_effect=[adaptation(), deepcopy(AUDIT_RESPONSE)])
    ai.adapt_case(source(), provenance(), SPEC, initial_candidate=adaptation(), initial_issues=issue)
    assert ai._request.call_count == 2
    assert ai._request.call_args_list[0].args[1]["validationIssues"] == issue
    assert "Adapte UM relato" in ai._request.call_args_list[0].args[0]


def test_checklist_array_recovered_only_when_paths_identify_categories():
    case = adaptation()["case"]
    case["auxiliaryLabs"] = {"Histologia": "Resultado documentado."}
    case["treatments"] = "Cirurgia documentada."
    audit = deepcopy(AUDIT_RESPONSE)
    audit["sourceChecklist"] = [
        {"sourceFact": "Cirurgia documentada.", "casePaths": ["/treatments"], "covered": True},
        {"sourceFact": "Resultado documentado.", "casePaths": ["/auxiliaryLabs/Histologia"], "covered": True},
        audit["sourceChecklist"]["finalDiagnosis"],
    ]
    assert _validated_audit(audit, set(AUDIT), case) == AUDIT
    audit["sourceChecklist"][0]["casePaths"] = []
    with pytest.raises(AIValidationError):
        _validated_audit(audit, set(AUDIT), case)
