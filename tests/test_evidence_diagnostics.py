import pytest

from medlab.ai import AIValidationError, _verify_source_evidence, _verify_factual_references
from test_importer import adaptation, ARTICLE


def test_source_feedback_lists_all_required_leaf_paths_without_weakening_validation():
    data = adaptation()
    data["evidence"][0]["path"] = "/patientProfile"
    data["evidence"][1]["excerpt"] = "Invented source quote"
    with pytest.raises(AIValidationError) as failure:
        _verify_source_evidence(data["case"], data["evidence"], data["missingFacts"], ARTICLE)
    error = failure.value
    assert "/evidence/0/path" in error.paths and "/evidence/1/excerpt" in error.paths
    coverage = error.repair_feedback["coverage"]
    assert "/patientProfile/chief_complaint" in coverage["requiredEvidencePaths"]
    assert "/patientProfile" not in coverage["requiredEvidencePaths"]
    assert "/physicalExams" not in coverage["requiredEvidencePaths"]
    assert coverage["requiredMissingFacts"] == sorted(data["missingFacts"])


def test_factual_feedback_distinguishes_bad_paths_from_unknown_fact_ids():
    data = adaptation()
    evidence = [{"path": item["path"], "factIds": ["F001"]} for item in data["evidence"]]
    evidence[0]["path"] = "/patientProfile"
    evidence[1]["factIds"] = ["F999"]
    with pytest.raises(AIValidationError) as failure:
        _verify_factual_references(data["case"], evidence, data["missingFacts"], [{"factId": "F001"}])
    error = failure.value
    assert "/factualEvidence/0/path" in error.paths
    assert "/factualEvidence/1/factIds" in error.paths
    assert error.repair_feedback["coverage"]["allowedFactIds"] == ["F001"]


@pytest.mark.parametrize("factual", [False, True])
def test_wrapper_pointers_are_canonicalized_only_for_existing_leaves(factual):
    data = adaptation()
    original_paths = [item["path"] for item in data["evidence"]]
    original_missing = list(data["missingFacts"])
    evidence = [{"path": "/case" + item["path"], **(
        {"factIds": ["F001"]} if factual else {"excerpt": item["excerpt"]})}
        for item in data["evidence"]]
    missing = ["/case" + path for path in original_missing]
    if factual:
        _verify_factual_references(data["case"], evidence, missing, [{"factId": "F001"}])
    else:
        _verify_source_evidence(data["case"], evidence, missing, ARTICLE)
    assert [item["path"] for item in evidence] == original_paths
    assert missing == original_missing
    evidence[0]["path"] = "/case/patientProfile"
    with pytest.raises(AIValidationError):
        if factual:
            _verify_factual_references(data["case"], evidence, missing, [{"factId": "F001"}])
        else:
            _verify_source_evidence(data["case"], evidence, missing, ARTICLE)


def test_wrapper_normalization_does_not_accept_missing_or_invented_source_support():
    data = adaptation()
    data["evidence"][0] = {"path": "/case" + data["evidence"][0]["path"], "excerpt": "Invented quote"}
    with pytest.raises(AIValidationError) as failure:
        _verify_source_evidence(data["case"], data["evidence"], data["missingFacts"], ARTICLE)
    assert "/evidence/0/excerpt" in failure.value.paths


def test_unescaped_exam_name_requires_an_exact_unique_existing_leaf():
    from medlab.ai import _normalize_evidence_paths
    evidence = [{"path": "/case/auxiliaryLabs/Relação albumina/creatinina"}, {"path": "/auxiliaryLabs/Inexistente"}]
    leaves = {"/auxiliaryLabs/Relação albumina~1creatinina": "Resultado"}
    _normalize_evidence_paths(evidence, [], leaves)
    assert evidence[0]["path"] == next(iter(leaves))
    assert evidence[1]["path"] == "/auxiliaryLabs/Inexistente"
    ambiguous = [{"path": "/auxiliaryLabs/A/B/C"}]
    _normalize_evidence_paths(ambiguous, [], {"/auxiliaryLabs/A~1B/C": "1", "/auxiliaryLabs/A/B~1C": "2"})
    assert ambiguous[0]["path"] == "/auxiliaryLabs/A/B/C"
