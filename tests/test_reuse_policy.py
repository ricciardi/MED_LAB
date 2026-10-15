"""Explicit noncommercial permission, exact licenses, and policy-aware resume."""
import hashlib
import json
from unittest.mock import Mock

import pytest

from medlab.ai import AIError, AIService
from medlab.case_schema import CaseValidationError, license_obligations, validate_case
from medlab.importer import CaseImporter, PMCClient, SourceRejected
from medlab.source_registry import register_sources, source_counts, sync_import_records
from test_importer import cloud_session, fake_ai, provenance, real_case, source_file
from test_source_registry import csv_source, registry_app, work_files, write_json


@pytest.mark.parametrize("code,slug", [("CC BY-NC", "by-nc"), ("CC BY-SA", "by-sa"), ("CC BY-NC-SA", "by-nc-sa")])
@pytest.mark.parametrize("version", ["1.0", "2.0", "2.5", "3.0", "4.0"])
def test_noncommercial_accepts_exact_versioned_license_and_preserves_obligations(tmp_path, code, slug, version):
    url = f"https://creativecommons.org/licenses/{slug}/{version}/"
    result = PMCClient(tmp_path, session=cloud_session(code=code, url=url), reuse_scope="noncommercial").verify("PMC123")
    license_data = result["provenance"]["license"]
    assert license_data["code"] == code and license_data["url"] == url and license_data["version"] == version
    for key, value in license_obligations(code, url).items():
        assert license_data[key] == value
    assert validate_case(real_case(base=result["provenance"]))["provenance"]["license"] == license_data


@pytest.mark.parametrize("code,slug", [("CC BY-ND", "by-nd"), ("CC BY-NC-ND", "by-nc-nd")])
def test_nd_remains_excluded_even_in_noncommercial_scope(tmp_path, code, slug):
    with pytest.raises(SourceRejected):
        PMCClient(tmp_path, session=cloud_session(code=code, url=f"https://creativecommons.org/licenses/{slug}/4.0/"), reuse_scope="noncommercial").verify("PMC123")
    case = real_case()
    case["provenance"]["license"].update(code=code, url=f"https://creativecommons.org/licenses/{slug}/4.0/")
    with pytest.raises(CaseValidationError):
        validate_case(case)


def test_metadata_code_cannot_be_mislabeled_as_a_more_permissive_url(tmp_path):
    with pytest.raises(SourceRejected):
        PMCClient(tmp_path, session=cloud_session(code="CC BY-NC", url="https://creativecommons.org/licenses/by/4.0/"), reuse_scope="noncommercial").verify("PMC123")
    case = real_case()
    case["provenance"]["license"]["code"] = "CC BY-NC"
    with pytest.raises(CaseValidationError):
        validate_case(case)


def test_original_copyright_notices_are_retained_with_license_statement(tmp_path):
    session = cloud_session()
    responses = list(session.get.side_effect)
    raw = responses[2].iter_content.return_value[0].replace(b"<permissions>", b"<permissions><copyright-statement>Copyright Authors 2024</copyright-statement><copyright-year>2024</copyright-year><copyright-holder>Original Authors</copyright-holder>")
    metadata = json.loads(responses[1].iter_content.return_value[0])
    metadata["xml_url"] = "s3://pmc-oa-opendata/PMC123.2/PMC123.2.xml?md5=" + hashlib.md5(raw).hexdigest()
    responses[1].iter_content.return_value = [json.dumps(metadata).encode()]
    responses[2].iter_content.return_value = [raw]
    session.get.side_effect = responses
    result = PMCClient(tmp_path, session=session, reuse_scope="noncommercial").verify("PMC123")
    assert "Copyright Authors 2024" in result["provenance"]["license"]["text"]
    assert "Original Authors" in result["provenance"]["license"]["text"]


def test_legacy_rejected_metadata_is_reused_without_reloading_listing_or_metadata(tmp_path):
    session = cloud_session(code="CC BY-NC", url="https://creativecommons.org/licenses/by-nc/4.0/")
    with pytest.raises(SourceRejected):
        PMCClient(tmp_path, session=session).verify("PMC123")
    assert session.get.call_count == 2
    result = PMCClient(tmp_path, session=session, reuse_scope="noncommercial").verify("PMC123")
    assert result["provenance"]["license"]["code"] == "CC BY-NC"
    assert session.get.call_count == 3  # only the XML was missing
    # A permissive verified cache must not bypass a later restrictive policy.
    with pytest.raises(SourceRejected):
        PMCClient(tmp_path, session=session, reuse_scope="commercial").verify("PMC123")
    assert session.get.call_count == 3


def test_scope_change_revisits_old_rejections_once_and_keeps_existing_by_verification(tmp_path, source_file):
    client = Mock()
    def commercial(pmcid, refresh=False):
        if pmcid != "PMC456":
            raise SourceRejected("Licença fora da política anterior.")
        return {"provenance": provenance(pmcid), "articleText": "Original source"}
    client.verify.side_effect = commercial
    first = CaseImporter({}, tmp_path / "work", ai_service=fake_ai(), pmc_client=client)
    first.run(source_file, adapt=False)
    # Historical checkpoints did not contain policy metadata.
    for path in (tmp_path / "work/records").glob("*.json"):
        data = json.loads(path.read_text())
        data.pop("reuseScope")
        data.pop("policyVersion")
        path.write_text(json.dumps(data))
    client.reset_mock()
    def noncommercial(pmcid, refresh=False):
        assert refresh is False
        if pmcid == "PMC789":
            raise SourceRejected("ND continua excluído.")
        data = provenance(pmcid)
        data["license"].update(code="CC BY-NC", url="https://creativecommons.org/licenses/by-nc/4.0/")
        return {"provenance": data, "articleText": "Original source"}
    client.verify.side_effect = noncommercial
    second = CaseImporter({"CASE_REUSE_SCOPE": "noncommercial"}, tmp_path / "work", ai_service=fake_ai(), pmc_client=client)
    report = second.run(source_file, adapt=False)
    assert report["processedThisRun"] == 2 and report["sourceVerified"] == 2
    assert report["reuseScope"] == "noncommercial" and report["policyVersion"] == 2
    assert {call.args[0] for call in client.verify.call_args_list} == {"PMC123", "PMC789"}
    client.reset_mock()
    assert second.run(source_file, adapt=False)["processedThisRun"] == 0
    client.verify.assert_not_called()


def test_thread_local_source_clients_keep_noncommercial_scope(tmp_path, source_file, monkeypatch):
    scopes = []
    def verify(client, pmcid, refresh=False):
        scopes.append(client.reuse_scope)
        return {"provenance": provenance(pmcid), "articleText": "Original source"}
    monkeypatch.setattr(PMCClient, "verify", verify)
    importer = CaseImporter({"CASE_REUSE_SCOPE": "noncommercial"}, tmp_path / "work", ai_service=fake_ai())
    assert importer.run(source_file, source_workers=3)["sourceVerified"] == 3
    assert scopes == ["noncommercial"] * 3


@pytest.mark.parametrize("scope,expected", [("commercial", 0), ("noncommercial", 1)])
def test_registry_validates_license_against_active_application_policy(registry_app, tmp_path, scope, expected):
    registry_app.config["CASE_REUSE_SCOPE"] = scope
    path = csv_source(tmp_path / "source.csv")
    register_sources(path)
    work, _ = work_files(tmp_path, path)
    cache = work / "pmc-cache/PMC123.verified.json"
    data = json.loads(cache.read_text())
    license_data = data["provenance"]["license"]
    license_data.update(code="CC BY-NC-SA", url="https://creativecommons.org/licenses/by-nc-sa/4.0/")
    license_data.update(license_obligations(license_data["code"], license_data["url"]))
    metadata = {"pmcid": "PMC123", "version": 1, "license_code": "CC BY-NC-SA", "is_retracted": False, "is_manuscript": False}
    raw = json.dumps(metadata).encode()
    digest = hashlib.sha256(raw).hexdigest()
    data["provenance"]["metadataSha256"] = digest
    (work / "pmc-cache" / f"PMC123.1.{digest}.metadata.json").write_bytes(raw)
    write_json(cache, data)
    checkpoint_path = work / "records/PMC123.json"
    checkpoint = json.loads(checkpoint_path.read_text())
    checkpoint["license"] = "CC BY-NC-SA"
    write_json(checkpoint_path, checkpoint)
    report = sync_import_records(work)
    assert report["database"]["verified"] == expected
    assert report["invalid"] == 1 - expected
    registry_app.config["CASE_REUSE_SCOPE"] = "commercial"
    assert source_counts()["verified"] == 0


def test_sharealike_obligations_cannot_be_removed_or_contradicted_and_ai_checks_scope(monkeypatch):
    case = real_case()
    license_data = case["provenance"]["license"]
    license_data.update(code="CC BY-SA", url="https://creativecommons.org/licenses/by-sa/4.0/", shareAlike=False)
    with pytest.raises(CaseValidationError):
        validate_case(case)
    post = Mock()
    monkeypatch.setattr("medlab.ai.requests.post", post)
    with pytest.raises(AIError, match="licença"):
        AIService({"DEEPSEEK_API_KEY": "test-key"}).adapt_case({}, case["provenance"], "Pneumologia")
    post.assert_not_called()
