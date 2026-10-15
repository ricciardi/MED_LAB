"""CLI orchestration checks with isolated databases, synthetic data and no network."""
from copy import deepcopy
import csv
import hashlib
import json
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests

from medlab import create_app
from medlab.extensions import db
from medlab.importer import CaseImporter, DATASET_REVISION, PMCClient
from medlab.models import ClinicalCase, SourceExclusion, SourceRecord


@pytest.fixture
def corpus_apps(tmp_path, monkeypatch):
    """Factory for truly empty databases; never use the working installation."""
    applications = []

    def network_forbidden(*args, **kwargs):
        pytest.fail("A corpus CLI test attempted a real HTTP request.")

    monkeypatch.setattr(requests.sessions.Session, "request", network_forbidden)

    def make_app():
        application = create_app({
            "TESTING": True,
            "SECRET_KEY": "corpus-cli-test-only-secret-not-for-deployment",
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / f'corpus-{len(applications)}.sqlite'}",
            "WTF_CSRF_ENABLED": False,
            "SESSION_COOKIE_SECURE": False,
            "DEMO_MODE": False,
            "CASE_REUSE_SCOPE": "noncommercial",
            "CASE_ALLOW_CLINICAL_FACTS": False,
            "DEEPSEEK_API_KEY": "fake-key-no-network-test",
            "AI_BASE_URL": "https://api.deepseek.com",
            "AI_MODEL": "test-model",
            "ADMIN_PATH": "/gestao-corpus-teste",
        })
        applications.append(application)
        return application

    yield make_app
    for application in applications:
        with application.app_context():
            db.session.remove()
            db.engine.dispose()


def write_source(path, *, duplicates=False):
    rows = [
        {"pmcid": "PMC123", "title": "Original English report: cough", "final_diagnosis": "Original diagnosis one"},
        {"pmcid": "PMC456", "title": "Original English report: fatigue", "final_diagnosis": "Original diagnosis two"},
    ]
    if duplicates:
        rows.extend([dict(rows[0]), {"pmcid": "INVALID", "final_diagnosis": "Invalid record"}])
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["", "pmcid", "title", "journal", "article_link", "publication_date", "text", "case_prompt", "diagnostic_reasoning", "final_diagnosis", "split"])
        writer.writeheader()
        for index, row in enumerate(rows):
            writer.writerow({"": index, "journal": "Synthetic journal", "article_link": "https://example.invalid/article",
                "publication_date": "2024-01-01", "text": "PRIVATE_FULL_ARTICLE_SENTINEL",
                "case_prompt": "PRIVATE_CASE_PROMPT,\nwith a second line", "diagnostic_reasoning": "PRIVATE_REASONING_SENTINEL",
                "split": "train", **row})
    return path


def command_json(application, *arguments):
    result = application.test_cli_runner().invoke(args=list(arguments))
    assert result.exit_code == 0, result.output
    # import-real-cases prints progress before its final JSON report.
    return json.loads(result.output[result.output.index("{"):])


def case_count(application):
    with application.app_context():
        return db.session.scalar(db.select(db.func.count(ClinicalCase.id)))


def source_snapshot(application):
    with application.app_context():
        return [(row.pmcid, row.original_title, row.original_diagnosis, row.record_sha256, row.dataset_revision)
            for row in db.session.scalars(db.select(SourceRecord).order_by(SourceRecord.pmcid))]


def test_cli_register_report_export_restore_is_idempotent_and_does_not_publish(corpus_apps, tmp_path, case_data):
    original = corpus_apps()
    source = write_source(tmp_path / "original.csv", duplicates=True)
    work = tmp_path / "no-checkpoints"
    registered = command_json(original, "register-sources", "--source", str(source), "--work-dir", str(work))
    assert {key: registered[key] for key in ("read", "new", "duplicates", "invalid")} == {"read": 4, "new": 2, "duplicates": 1, "invalid": 1}
    assert registered["database"] == {"registered": 2, "verified": 0, "translated": 0, "drafts": 0, "published": 0, "statusCounts": {"registered": 2}}
    assert case_count(original) == 0
    assert command_json(original, "source-report") == registered["database"]
    repeated = command_json(original, "register-sources", "--source", str(source), "--work-dir", str(work))
    assert repeated["new"] == 0 and repeated["unchanged"] == 2
    assert repeated["duplicates"] == 1 and case_count(original) == 0

    # Simulate local preparation already completed before portability. Its
    # workflow claims and case contents must not cross into the clean database.
    with original.app_context():
        data = deepcopy(case_data)
        data["caseId"] = "existing-local-draft"
        draft = ClinicalCase(slug=data["caseId"], title=data["caseTitle"], specialty=data["specialty"],
            difficulty=data["difficulty"], summary=data["summary"], data=data, is_published=False)
        db.session.add(draft)
        db.session.flush()
        record = db.session.scalar(db.select(SourceRecord).where(SourceRecord.pmcid == "PMC123"))
        record.verified, record.translated, record.import_status = True, True, "draft"
        record.license_code = "CC BY"
        record.provenance = {"license": {"code": "CC BY", "url": "https://creativecommons.org/licenses/by/4.0/"}}
        record.clinical_case_id = draft.id
        db.session.commit()
    prepared = command_json(original, "source-report")
    assert [prepared[key] for key in ("registered", "verified", "translated", "drafts", "published")] == [2, 1, 1, 1, 0]
    expected_inventory = source_snapshot(original)
    exported_path = tmp_path / "private-export" / "inventory.jsonl"
    exported = command_json(original, "export-sources", "--output", str(exported_path))
    assert exported["exported"] == 2 and exported["metadataOnly"] is True
    assert exported["sha256"] == hashlib.sha256(exported_path.read_bytes()).hexdigest()
    content = exported_path.read_text(encoding="utf-8")
    lines = [json.loads(line) for line in content.splitlines()]
    assert lines[0]["metadataOnly"] is True and lines[0]["pipelineStateIncluded"] is False
    for forbidden in ("PRIVATE_FULL_ARTICLE_SENTINEL", "PRIVATE_CASE_PROMPT", "PRIVATE_REASONING_SENTINEL", "existing-local-draft"):
        assert forbidden not in content
    for row in lines[1:]:
        assert not {"verified", "translated", "clinical_case_id", "is_published", "import_status"}.intersection(row)

    restored_app = corpus_apps()
    restored = command_json(restored_app, "restore-sources", "--source", str(exported_path))
    assert restored["new"] == 2 and restored["invalid"] == 0 and restored["conflicts"] == 0
    assert restored["database"] == registered["database"]
    assert source_snapshot(restored_app) == expected_inventory
    assert case_count(restored_app) == 0
    restored_twice = command_json(restored_app, "restore-sources", "--source", str(exported_path))
    assert restored_twice["new"] == 0 and restored_twice["unchanged"] == 2
    assert restored_twice["database"] == registered["database"]
    assert case_count(restored_app) == 0
    assert command_json(restored_app, "source-report") == registered["database"]


@pytest.mark.parametrize("failure", ["changed-source", "work-directory-locked"])
def test_import_failure_before_source_validation_never_recovers_cached_drafts(corpus_apps, tmp_path, case_data, monkeypatch, failure):
    application = corpus_apps()
    source = write_source(tmp_path / "source.csv")
    work = tmp_path / "private-work"
    work.mkdir()
    original_fingerprint = hashlib.sha256(source.read_bytes()).hexdigest()
    (work / "manifest.json").write_text(json.dumps({"dataset": "MedCaseReasoning", "datasetRevision": DATASET_REVISION,
        "sourceSha256": original_fingerprint}), encoding="utf-8")
    cached = deepcopy(case_data)
    cached["caseId"] = "cached-draft-that-must-not-be-imported"
    # This spy would offer a usable cached draft if CLI tried recovery before
    # run() checked the CSV identity and acquired its exclusive work lock.
    cached_drafts = Mock(side_effect=lambda *args, **kwargs: iter([deepcopy(cached)]))
    verify = Mock(side_effect=AssertionError("Source verification must not start after this preflight failure."))
    monkeypatch.setattr(CaseImporter, "iter_drafts", cached_drafts)
    monkeypatch.setattr(PMCClient, "verify", verify)
    lock_handle = None
    if failure == "changed-source":
        source.write_bytes(source.read_bytes() + b"\n")
        expected_error = "A fonte mudou"
    else:
        import fcntl
        lock_handle = (work / ".import.lock").open("a")
        fcntl.flock(lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        expected_error = "Já existe uma importação usando este diretório"
    try:
        result = application.test_cli_runner().invoke(args=["import-real-cases", "--source", str(source), "--work-dir", str(work), "--adapt"])
    finally:
        if lock_handle is not None:
            fcntl.flock(lock_handle, fcntl.LOCK_UN)
            lock_handle.close()
    assert result.exit_code != 0
    assert expected_error in result.output
    cached_drafts.assert_not_called()
    verify.assert_not_called()
    assert case_count(application) == 0
    report = command_json(application, "source-report")
    assert report["registered"] == 2
    assert report["translated"] == report["drafts"] == report["published"] == 0


def test_successful_import_recovers_drafts_after_run_and_progress_deduplicates(corpus_apps, tmp_path, case_data, monkeypatch):
    import medlab.importer as importer_module
    import medlab.source_registry as registry_module

    application = corpus_apps()
    source = write_source(tmp_path / "source.csv")
    work = tmp_path / "private-work"
    events = []
    received = []
    cached = deepcopy(case_data)
    cached.update(caseId="cached-validated-draft", reviewStatus="review_required")

    class SuccessfulImporter:
        def __init__(self, config, work_dir):
            assert config["DEEPSEEK_API_KEY"] == "fake-key-no-network-test"
            assert Path(work_dir) == work
            self.run_completed = False
            self.source_validated = False
            events.append("construct")

        def run(self, path, **kwargs):
            assert Path(path) == source
            received.append({key: value for key, value in kwargs.items() if key != "on_progress"})
            self.source_validated = True
            events.append("run-validated")
            kwargs["on_progress"]({"pmcid": "PMC123", "status": "draft", "specialty": cached["specialty"]})
            self.run_completed = True
            events.append("run-finished")
            return {"processedThisRun": 1, "adaptationBlocked": False}

        def iter_drafts(self, pmcids=None, excluded_pmcids=None):
            assert excluded_pmcids == set()
            assert self.source_validated, "Drafts must not be recovered before source validation."
            if pmcids is None:
                assert self.run_completed, "Full cache recovery must follow a successful exclusive run."
                events.append("recover-all")
            else:
                assert pmcids == ["PMC123"]
                events.append("recover-progress")
            yield deepcopy(cached)

    sync = Mock(return_value={"updated": 0, "invalid": 0})
    monkeypatch.setattr(importer_module, "CaseImporter", SuccessfulImporter)
    monkeypatch.setattr(registry_module, "sync_import_records", sync)
    args = ["import-real-cases", "--source", str(source), "--work-dir", str(work),
        "--specialty", "Pneumologia", "--target-per-specialty", "2", "--limit", "7",
        "--adapt", "--all-records", "--source-workers", "3", "--adapt-workers", "4", "--retry-errors", "--refresh-sources"]
    report = command_json(application, *args)
    assert report["newDatabaseDrafts"] == 1
    assert events == ["construct", "run-validated", "recover-progress", "run-finished", "recover-all"]
    assert received == [{"specialties": ("Pneumologia",), "target_per_specialty": 2, "limit": 7,
        "adapt": True, "retry_errors": True, "refresh_sources": True, "all_records": True, "source_workers": 3,
        "adapt_workers": 4, "excluded_pmcids": set()}]
    assert sync.call_count == 2
    assert sync.call_args_list[0].kwargs == {"pmcids": ["PMC123"]}
    assert sync.call_args_list[1].kwargs == {}
    with application.app_context():
        draft = db.session.scalar(db.select(ClinicalCase))
        assert draft.slug == cached["caseId"] and draft.is_published is False
        assert draft.data["reviewStatus"] == "review_required"
    repeated = command_json(application, *args)
    assert repeated["newDatabaseDrafts"] == 0
    assert case_count(application) == 1
    with application.app_context():
        assert db.session.scalar(db.select(ClinicalCase.is_published)) is False


def test_cli_rejects_invalid_portable_inventory_without_cases_or_claimed_progress(corpus_apps, tmp_path):
    application = corpus_apps()
    malformed = tmp_path / "invalid.jsonl"
    malformed.write_text(json.dumps({"format": "medlab-source-metadata", "version": 1,
        "metadataOnly": True, "pipelineStateIncluded": True, "dataset": "MedCaseReasoning"}) + "\n", encoding="utf-8")
    result = application.test_cli_runner().invoke(args=["restore-sources", "--source", str(malformed)])
    assert result.exit_code != 0
    assert "formato de exportação válido" in result.output
    assert case_count(application) == 0
    assert command_json(application, "source-report")["registered"] == 0


def test_selected_cli_exclusion_is_portable_and_requires_explicit_ids(corpus_apps, tmp_path):
    application = corpus_apps()
    source = write_source(tmp_path / "source.csv")
    args = ["register-sources", "--source", str(source), "--work-dir", str(tmp_path / "work")]
    command_json(application, *args)
    with application.app_context():
        for record in db.session.scalars(db.select(SourceRecord)):
            record.import_status = "license_rejected"
        db.session.commit()
    missing_selection = application.test_cli_runner().invoke(args=["exclude-restricted-sources"])
    assert missing_selection.exit_code != 0 and "--pmcid" in missing_selection.output
    assert command_json(application, "source-report")["registered"] == 2
    report = command_json(application, "exclude-restricted-sources", "--pmcid", "PMC123")
    assert report["removed"] == 1 and report["database"]["registered"] == 1
    assert command_json(application, *args)["excluded"] == 1
    exported = tmp_path / "inventory.jsonl"
    export = command_json(application, "export-sources", "--output", str(exported))
    assert export["exported"] == export["excluded"] == 1

    restored_app = corpus_apps()
    restored = command_json(restored_app, "restore-sources", "--source", str(exported))
    assert restored["new"] == 1 and restored["exclusions"]["new"] == 1
    assert command_json(restored_app, *args)["excluded"] == 1
    assert command_json(restored_app, "source-report")["registered"] == 1
    assert case_count(application) == case_count(restored_app) == 0


@pytest.mark.parametrize("refresh", [False, True])
def test_cli_exclusions_prevent_verification_ai_and_cached_case_recovery(corpus_apps, tmp_path, monkeypatch, refresh):
    from medlab.ai import AIService

    application = corpus_apps()
    source = write_source(tmp_path / "source.csv")
    work = tmp_path / "work"
    command_json(application, "register-sources", "--source", str(source), "--work-dir", str(work))
    with application.app_context():
        for record in db.session.scalars(db.select(SourceRecord)):
            record.import_status = "license_rejected"
        db.session.commit()
    command_json(application, "exclude-restricted-sources", "--pmcid", "PMC123", "--pmcid", "PMC456")
    verify = Mock(side_effect=AssertionError("Excluded source must not be fetched."))
    classify = Mock(side_effect=AssertionError("Excluded source must not use AI."))
    adapt = Mock(side_effect=AssertionError("Excluded source must not use AI."))
    monkeypatch.setattr(PMCClient, "verify", verify)
    monkeypatch.setattr(AIService, "classify_case", classify)
    monkeypatch.setattr(AIService, "adapt_case", adapt)
    drafts = work / "drafts"
    drafts.mkdir(parents=True)
    (drafts / "PMC123.json").write_text("unreadable excluded cache")
    (drafts / "PMC456.json").write_text("unreadable excluded cache")
    args = ["import-real-cases", "--source", str(source), "--work-dir", str(work), "--adapt", "--all-records", "--adapt-workers", "2"]
    if refresh:
        args.append("--refresh-sources")
    report = command_json(application, *args)
    assert report["processedThisRun"] == 0 and report["excludedThisRun"] == 2
    assert report["newDatabaseDrafts"] == 0 and case_count(application) == 0
    assert command_json(application, "source-report")["registered"] == 0
    verify.assert_not_called()
    classify.assert_not_called()
    adapt.assert_not_called()
