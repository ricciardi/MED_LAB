"""Outages checkpoint one case, stop AI work, and leave the remainder untouched."""
import csv
import json
from unittest.mock import Mock

import pytest

from medlab.ai import AIError, AIUnavailableError
from medlab.importer import CaseImporter


def source_file(tmp_path, count=12):
    path = tmp_path / "source.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["pmcid", "case_prompt", "final_diagnosis"])
        writer.writeheader()
        for index in range(1, count + 1):
            writer.writerow({"pmcid": f"PMC{index}", "case_prompt": "Cough for three weeks.", "final_diagnosis": "Pneumonia"})
    return path


def runner(tmp_path, service):
    pmc = Mock()
    pmc.verify.side_effect = lambda pmcid, refresh=False: {
        "provenance": {"pmcid": pmcid, "license": {"code": "CC BY", "url": "https://creativecommons.org/licenses/by/4.0/"}, "checkedAt": "2026-10-15T12:00:00+00:00"},
        "articleText": "Original clinical source for testing only.",
    }
    return CaseImporter({"DEEPSEEK_API_KEY": "test-key"}, tmp_path / "work", ai_service=service, pmc_client=pmc)


@pytest.mark.parametrize("workers", [1, 4])
@pytest.mark.parametrize("code", ["authentication_required", "quota_exhausted", "rate_limited", "service_unavailable", "timeout", "connection_error"])
def test_classification_outage_stops_first_case_and_preserves_full_inventory(tmp_path, workers, code):
    path = source_file(tmp_path)
    service = Mock(is_demo=False)
    service.classify_case.side_effect = AIUnavailableError(code)
    importer = runner(tmp_path, service)
    progress = []
    report = importer.run(path, adapt=True, all_records=True, source_workers=workers, limit=100, on_progress=progress.append)
    assert report["stopReason"] == code and report["stoppedEarly"] is True
    assert report["processedThisRun"] == 1
    assert report["sourceRecords"] == 12 and report["uniquePmcids"] == 12
    assert report["statusCounts"] == {"classification_error": 1}
    assert report["pendingVerification"] == 11
    assert service.classify_case.call_count == 1
    service.adapt_case.assert_not_called()
    # Existing bounded source prefetch can finish, but no new work is scheduled.
    assert importer.pmc.verify.call_count <= workers
    assert len(progress) == 1
    assert len(list((tmp_path / "work/records").glob("*.json"))) == 1
    checkpoint = json.loads((tmp_path / "work/records/PMC1.json").read_text())
    assert checkpoint["availabilityError"] == code and checkpoint["attempts"] == 1
    assert not list((tmp_path / "work/drafts").glob("*.json"))
    assert importer.report()["stopReason"] == code


def test_adaptation_outage_keeps_classification_and_requires_explicit_retry(tmp_path):
    path = source_file(tmp_path, 3)
    service = Mock(is_demo=False)
    service.classify_case.return_value = {"specialty": "Pneumologia", "reason": "Sintomas respiratórios."}
    service.adapt_case.side_effect = AIUnavailableError("quota_exhausted")
    importer = runner(tmp_path, service)
    first = importer.run(path, adapt=True, all_records=True)
    assert first["statusCounts"] == {"adaptation_error": 1}
    first_checkpoint = json.loads((tmp_path / "work/records/PMC1.json").read_text())
    assert first_checkpoint["specialty"] == "Pneumologia"
    # Another invocation without retry_errors skips the failed record.
    second = importer.run(path, adapt=True, all_records=True)
    assert second["processedThisRun"] == 1 and second["pendingVerification"] == 1
    assert json.loads((tmp_path / "work/records/PMC1.json").read_text()) == first_checkpoint
    service.adapt_case.side_effect = AIError("Rascunho incompleto para este caso.")
    final = importer.run(path, adapt=True, all_records=True, retry_errors=True)
    assert final["processedThisRun"] == 3 and final["stopReason"] is None
    assert final["stoppedEarly"] is False
    assert final["statusCounts"] == {"adaptation_error": 3}
    checkpoint = json.loads((tmp_path / "work/records/PMC1.json").read_text())
    assert checkpoint["attempts"] == 2 and "availabilityError" not in checkpoint


def test_record_specific_errors_continue_and_source_only_run_never_calls_ai(tmp_path):
    path = source_file(tmp_path, 3)
    service = Mock(is_demo=False)
    service.classify_case.side_effect = AIError("A classificação deste caso está inválida.")
    importer = runner(tmp_path, service)
    report = importer.run(path, adapt=True, all_records=True)
    assert report["processedThisRun"] == 3 and report["stopReason"] is None
    assert service.classify_case.call_count == 3
    service.reset_mock()
    source_only = importer.run(path, adapt=False, retry_errors=True, source_workers=3)
    assert source_only["sourceVerified"] == 3 and source_only["stopReason"] is None
    service.classify_case.assert_not_called()
    service.adapt_case.assert_not_called()
