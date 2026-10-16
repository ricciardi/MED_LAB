"""ND sources have a distinct opt-in factual workflow, never text adaptation."""
from copy import deepcopy
import hashlib
import json
from unittest.mock import Mock

import pytest

from medlab.ai import AIError, AIService
from medlab.case_schema import (CaseValidationError, allowed_license_codes, source_license_allowed,
                               source_reuse_basis, validate_case)
from medlab.importer import CaseImporter, PMCClient, SourceRejected
from test_importer import ARTICLE, AUDIT, AUDIT_RESPONSE, SPEC, adaptation, cloud_session, fake_ai, provenance, source_file
from test_importer_license_formats import license_session


@pytest.mark.parametrize("code,slug", [("CC BY-ND", "by-nd"), ("CC BY-NC-ND", "by-nc-nd")])
def test_nd_opt_in_verifies_source_but_never_grants_licensed_adaptation(tmp_path, code, slug):
    session = cloud_session(code=code, url=f"https://creativecommons.org/licenses/{slug}/4.0/")
    client = PMCClient(tmp_path, session=session, reuse_scope="noncommercial", allow_clinical_facts=True)
    result = client.verify("PMC123")
    assert result["provenance"]["reuseBasis"] == "clinical_facts"
    assert code not in allowed_license_codes("noncommercial")
    assert "adaptationLicenseUrl" not in result["provenance"]["license"]
    assert source_license_allowed(result["provenance"], "noncommercial", True)
    assert not source_license_allowed(result["provenance"], "noncommercial", False)
    with pytest.raises(SourceRejected):
        PMCClient(tmp_path, session=session, reuse_scope="noncommercial", allow_clinical_facts=False).verify("PMC123")
    assert session.get.call_count == 3  # flag-disabled validation uses metadata, no new requests


def test_retracted_nd_source_remains_rejected_even_with_opt_in(tmp_path):
    session = cloud_session(code="CC BY-ND", url="https://creativecommons.org/licenses/by-nd/4.0/", retracted=True)
    with pytest.raises(SourceRejected, match="retratado"):
        PMCClient(tmp_path, session=session, allow_clinical_facts=True).verify("PMC123")


def test_activating_factual_mode_revisits_previous_rejections_without_refresh(tmp_path, source_file):
    pmc = Mock()
    pmc.verify.side_effect = SourceRejected("ND exige fluxo factual.")
    before = CaseImporter({"CASE_REUSE_SCOPE": "noncommercial", "CASE_ALLOW_CLINICAL_FACTS": False},
                          tmp_path / "work", ai_service=fake_ai(), pmc_client=pmc)
    assert before.run(source_file)["licenseRejected"] == 3
    def verified(pmcid, refresh=False):
        assert refresh is False
        base = provenance(pmcid, reuseBasis="clinical_facts")
        base["license"].update(code="CC BY-ND", url="https://creativecommons.org/licenses/by-nd/4.0/")
        return {"provenance": base, "articleText": ARTICLE}
    pmc.verify.side_effect = verified
    after = CaseImporter({"CASE_REUSE_SCOPE": "noncommercial", "CASE_ALLOW_CLINICAL_FACTS": True},
                         tmp_path / "work", ai_service=fake_ai(), pmc_client=pmc)
    report = after.run(source_file, source_workers=3)
    assert report["processedThisRun"] == 3 and report["sourceVerified"] == 3
    assert report["allowClinicalFacts"] is True
    assert set(report["factualSourceLicenses"]) == {"CC BY-ND", "CC BY-NC-ND"}
    assert after.run(source_file)["processedThisRun"] == 0
    assert json.loads((tmp_path / "work/records/PMC123.json").read_text())["allowClinicalFacts"] is True


def test_factual_mode_does_not_relax_commercial_nc_or_unknown_terms():
    assert source_reuse_basis("CC BY-NC-ND", "commercial", True) is None
    assert source_reuse_basis("Custom license", "noncommercial", True) is None
    assert source_reuse_basis(["CC BY-ND"], "noncommercial", True) is None


def test_ported_nd_license_retains_us_jurisdiction_and_actual_version(tmp_path):
    url = "https://creativecommons.org/licenses/by-nc-nd/3.0/us/"
    session = cloud_session(code="CC BY-NC-ND", url=url)
    result = PMCClient(tmp_path, session=session, reuse_scope="noncommercial", allow_clinical_facts=True).verify("PMC123")
    assert result["provenance"]["license"]["url"] == url
    assert result["provenance"]["license"]["version"] == "3.0"
    assert source_license_allowed(result["provenance"], "noncommercial", True)


@pytest.mark.parametrize("explicit,accepted", [(True, True), (False, False)])
def test_legacy_copyright_statement_requires_explicit_license_declaration(tmp_path, explicit, accepted):
    declaration = "This is an open-access article distributed under the terms of the Creative Commons License" if explicit else "A link mentioned without any license declaration"
    xml = '<copyright-statement>' + declaration + ' (http://creativecommons.org/licenses/by-nc-nd/3.0/). Reproduction is permitted for personal, noncommercial use, provided the article is whole and unmodified.</copyright-statement>'
    client = PMCClient(tmp_path, session=license_session(xml, "CC BY-NC-ND"), reuse_scope="noncommercial", allow_clinical_facts=True)
    if accepted:
        result = client.verify("PMC123")
        assert result["provenance"]["reuseBasis"] == "clinical_facts"
        assert result["provenance"]["license"]["version"] == "3.0"
        assert "whole and unmodified" in result["provenance"]["license"]["text"]
    else:
        with pytest.raises(SourceRejected):
            client.verify("PMC123")


def factual_inputs():
    base = provenance(reuseBasis="clinical_facts")
    base["license"].update(code="CC BY-NC-ND", url="https://creativecommons.org/licenses/by-nc-nd/4.0/")
    facts = {"facts": [
        {"factId": "F001", "text": "Tosse com duração de três semanas.", "excerpt": "cough for three weeks"},
        {"factId": "F002", "text": "Diagnóstico confirmado: pneumonia.", "excerpt": "The final diagnosis was pneumonia."},
    ]}
    script = adaptation()
    script["changes"] = ["Roteiro original composto somente a partir de fatos clínicos."]
    script["factualEvidence"] = [{"path": item["path"], "factIds": ["F001" if item["path"].startswith("/patientProfile") else "F002"]}
                                 for item in script.pop("evidence")]
    audit = {**deepcopy(AUDIT_RESPONSE), "atomicFactsOnly": True, "originalWording": True}
    service = AIService({"DEEPSEEK_API_KEY": "test-key", "CASE_REUSE_SCOPE": "noncommercial", "CASE_ALLOW_CLINICAL_FACTS": True})
    service._request = Mock(side_effect=[facts, script, audit])
    return service, base, facts, script, audit


def test_factual_writer_receives_only_facts_and_final_case_keeps_no_source_excerpts():
    service, base, facts, _, _ = factual_inputs()
    source = {"articleText": ARTICLE, "case_prompt": "locator-private", "diagnostic_reasoning": "private-reasoning"}
    case = service.adapt_case(source, base, SPEC)
    assert service._request.call_count == 3
    writer = service._request.call_args_list[1].args[1]
    assert set(writer) == {"facts", "specialty", "caseTypes", "categories", "template"}
    serialized = json.dumps(writer, ensure_ascii=False)
    assert ARTICLE not in serialized and "locator-private" not in serialized and "private-reasoning" not in serialized
    assert "excerpt" not in serialized and "articleText" not in serialized
    assert writer["facts"] == [{"factId": item["factId"], "text": item["text"]} for item in facts["facts"]]
    assert case["reviewStatus"] == "review_required" and case["provenance"]["reuseBasis"] == "clinical_facts"
    assert case["provenance"]["evidence"] == []
    saved = json.dumps(case, ensure_ascii=False)
    assert "cough for three weeks" not in saved and "The final diagnosis was pneumonia." not in saved
    for item in case["provenance"]["sourceFacts"]:
        start, end = item["sourceLocation"]["start"], item["sourceLocation"]["end"]
        assert hashlib.sha256(ARTICLE[start:end].encode()).hexdigest() == item["sourceSha256"]
    assert validate_case(case) == case


@pytest.mark.parametrize("failure", ["missing_excerpt", "repeated_id", "narrative_fact", "unknown_reference", "uncovered_field", "copied_prose", "not_original", "not_atomic"])
def test_factual_pipeline_fails_closed_on_unsupported_or_expressive_outputs(failure):
    service, base, facts, script, audit = factual_inputs()
    if failure == "missing_excerpt":
        facts["facts"][0]["excerpt"] = "Invented quotation."
    elif failure == "repeated_id":
        facts["facts"][1]["factId"] = "F001"
    elif failure == "narrative_fact":
        facts["facts"][0]["text"] = ARTICLE
    elif failure == "unknown_reference":
        script["factualEvidence"][0]["factIds"] = ["F999"]
    elif failure == "uncovered_field":
        script["factualEvidence"].pop()
    elif failure == "copied_prose":
        script["case"]["summary"] = ARTICLE
    elif failure == "not_original":
        audit["originalWording"] = False
        audit["issues"] = [{"flag": "originalWording", "path": "/patientProfile", "reason": "A redação preserva expressão narrativa do artigo."}]
    elif failure == "not_atomic":
        audit["atomicFactsOnly"] = False
        audit["issues"] = [{"flag": "atomicFactsOnly", "path": "/facts", "reason": "Há raciocínio editorial entre os fatos."}]
    with pytest.raises(AIError):
        service.adapt_case({"articleText": ARTICLE}, base, SPEC)


@pytest.mark.parametrize("failure", ["no_basis", "with_quote", "no_hash", "unknown_id", "no_coverage", "originality_false", "mislabels_new_script_license"])
def test_factual_schema_rejects_missing_proof_and_article_license_applied_to_script(failure):
    service, base, _, _, _ = factual_inputs()
    case = service.adapt_case({"articleText": ARTICLE}, base, SPEC)
    proof = case["provenance"]
    if failure == "no_basis":
        proof.pop("reuseBasis")
    elif failure == "with_quote":
        proof["evidence"] = [{"path": "/patientProfile/chief_complaint", "excerpt": "cough for three weeks"}]
    elif failure == "no_hash":
        proof["sourceFacts"][0].pop("sourceSha256")
    elif failure == "unknown_id":
        proof["factualEvidence"][0]["factIds"] = ["F999"]
    elif failure == "no_coverage":
        proof["factualEvidence"].pop()
    elif failure == "originality_false":
        proof["audit"]["originalWording"] = False
    elif failure == "mislabels_new_script_license":
        proof["license"]["adaptationLicenseUrl"] = proof["license"]["url"]
    with pytest.raises(CaseValidationError):
        validate_case(case)


def test_missing_or_disabled_opt_in_blocks_nd_before_provider_and_textual_route():
    _, base, _, _, _ = factual_inputs()
    for config in ({}, {"CASE_ALLOW_CLINICAL_FACTS": False}, {"CASE_ALLOW_CLINICAL_FACTS": "true"}):
        service = AIService({"DEEPSEEK_API_KEY": "test-key", "CASE_REUSE_SCOPE": "noncommercial", **config})
        service._request = Mock()
        with pytest.raises(AIError, match="licença"):
            service.adapt_case({"articleText": ARTICLE}, base, SPEC)
        service._request.assert_not_called()


def test_thread_local_sources_preserve_factual_opt_in(tmp_path, source_file, monkeypatch):
    flags = []
    def verify(client, pmcid, refresh=False):
        flags.append(client.allow_clinical_facts)
        return {"provenance": provenance(pmcid), "articleText": ARTICLE}
    monkeypatch.setattr(PMCClient, "verify", verify)
    importer = CaseImporter({"CASE_ALLOW_CLINICAL_FACTS": True}, tmp_path / "work", ai_service=fake_ai())
    assert importer.run(source_file, source_workers=3)["sourceVerified"] == 3
    assert flags == [True] * 3
