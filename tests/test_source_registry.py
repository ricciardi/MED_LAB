"""Private source inventory and checkpoint sync; no network or provider calls."""
from copy import deepcopy
import csv
import hashlib
import json
from pathlib import Path

from flask import Flask
import pytest
from sqlalchemy import inspect
from sqlalchemy.dialects import mysql
from sqlalchemy.schema import CreateTable

from medlab.extensions import db
from medlab.importer import DATASET_REVISION
from medlab.migrations import upgrade_database
from medlab.models import ClinicalCase, Consultation, SourceExclusion, SourceRecord, User
from medlab.source_registry import (SourceRegistryError, export_source_registry, import_source_registry,
                                    excluded_pmcids, prune_restricted_sources, register_sources, source_counts,
                                    source_provenance_matches, sync_import_records)


@pytest.fixture
def registry_app():
    app = Flask(__name__)
    app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
                      CASE_REUSE_SCOPE="commercial", CASE_ALLOW_CLINICAL_FACTS=False)
    db.init_app(app)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()
        db.engine.dispose()


def csv_source(path, rows=None):
    rows = rows or [{"pmcid": "PMC123"}, {"pmcid": "PMC456"}]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["", "pmcid", "title", "journal", "article_link", "publication_date", "text", "case_prompt", "diagnostic_reasoning", "final_diagnosis", "split"])
        writer.writeheader()
        for index, row in enumerate(rows):
            writer.writerow({"": index, "title": "Secret English diagnosis title", "journal": "Original Journal",
                             "article_link": "https://example.invalid/article", "publication_date": "2024-01-01",
                             "text": "RAW ARTICLE NOT FOR PORTABLE EXPORT", "case_prompt": "Cough,\nthree weeks",
                             "diagnostic_reasoning": "RAW REASONING NOT FOR PORTABLE EXPORT",
                             "final_diagnosis": "Original English diagnosis", "split": "train", **row})
    return path


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def work_files(tmp_path, source_path, *, pmcid="PMC123", status="verified", case=None,
               license_code="CC BY", reuse_basis=None):
    work = tmp_path / "work"
    write_json(work / "manifest.json", {"dataset": "MedCaseReasoning", "datasetRevision": DATASET_REVISION,
                                        "sourceSha256": hashlib.sha256(source_path.read_bytes()).hexdigest()})
    xml = b"<article><body>Original licensed clinical case.</body></article>"
    metadata = {"pmcid": pmcid, "version": 1, "license_code": license_code, "is_retracted": False, "is_manuscript": False}
    metadata_bytes = json.dumps(metadata).encode()
    provenance = {"dataset": "MedCaseReasoning", "datasetRevision": DATASET_REVISION, "sourceRecordId": pmcid,
                  "pmcid": pmcid, "doi": None, "articleUrl": f"https://pmc.ncbi.nlm.nih.gov/articles/{pmcid}/",
                  "articleTitle": "Original source", "citation": "Original Journal 2024", "authors": ["A. Author"],
                  "license": {"code": license_code, "url": f"https://creativecommons.org/licenses/{license_code[3:].lower()}/4.0/", "version": "4.0", "text": "Synthetic license notice"},
                  "pmcVersion": 1, "xmlSha256": hashlib.sha256(xml).hexdigest(), "xmlMd5": hashlib.md5(xml).hexdigest(),
                  "metadataSha256": hashlib.sha256(metadata_bytes).hexdigest(), "checkedAt": "2026-10-15T12:00:00+00:00"}
    if reuse_basis is not None:
        provenance["reuseBasis"] = reuse_basis
    cache = work / "pmc-cache"
    write_json(cache / f"{pmcid}.verified.json", {"provenance": provenance, "articleText": "FULL TEXT STAYS PRIVATE"})
    (cache / f"{pmcid}.1.{provenance['xmlMd5']}.xml").write_bytes(xml)
    (cache / f"{pmcid}.1.{provenance['metadataSha256']}.metadata.json").write_bytes(metadata_bytes)
    checkpoint = {"pmcid": pmcid, "status": status, "attempts": 1, "updatedAt": "2026-10-15T12:01:00+00:00",
                  "license": license_code, "specialty": None}
    if case:
        case = deepcopy(case)
        case.update(caseId=f"mcr-{pmcid.lower()}", catalogType="published_case", reviewStatus="review_required")
        case["patientProfile"]["name"] = "Paciente simulado"
        case["provenance"] = {**provenance, "language": "pt-BR", "adaptedAt": "2026-10-15T12:01:00+00:00", "adaptedBy": "test",
                              "clinicalMoment": "Primeira consulta antes do diagnóstico.", "changes": ["Tradução para português e desidentificação."],
                              "missingFacts": [], "evidence": [{"path": "/diagnoses/mainDiagnosis/disease", "excerpt": "Original licensed clinical case."}],
                              "audit": {key: True for key in ("portuguese", "factsSupported", "unknownsPreserved", "deidentified", "patientBeforeDiagnosis")}}
        checkpoint.update(caseId=case["caseId"], specialty=case["specialty"])
        write_json(work / "drafts" / f"{pmcid}.json", case)
    write_json(work / "records" / f"{pmcid}.json", checkpoint)
    return work, case


def test_register_all_rows_is_idempotent_and_never_creates_or_publishes_cases(registry_app, tmp_path):
    path = csv_source(tmp_path / "source.csv", [{"pmcid": "PMC123"}, {"pmcid": "PMC456"}, {"pmcid": "PMC123"}, {"pmcid": "INVALID"}])
    first = register_sources(path, batch_size=1)
    assert first["new"] == 2 and first["duplicates"] == 1 and first["invalid"] == 1
    assert first["database"] == {"registered": 2, "verified": 0, "translated": 0, "drafts": 0, "published": 0, "statusCounts": {"registered": 2}}
    source = db.session.scalar(db.select(SourceRecord).where(SourceRecord.pmcid == "PMC123"))
    assert source.original_metadata["index"] == "0"
    assert source.original_title == "Secret English diagnosis title"
    assert source.original_diagnosis == "Original English diagnosis"
    assert set(source.original_metadata) == {"journal", "article_link", "publication_date", "split", "index"}
    second = register_sources(path, batch_size=1)
    assert second["new"] == 0 and second["unchanged"] == 2
    assert db.session.scalar(db.select(db.func.count(ClinicalCase.id))) == 0


def test_conflicting_source_hash_preserves_original_and_processing_status(registry_app, tmp_path):
    path = csv_source(tmp_path / "source.csv")
    register_sources(path)
    source = db.session.scalar(db.select(SourceRecord).where(SourceRecord.pmcid == "PMC123"))
    source.import_status = "source_error"
    db.session.commit()
    csv_source(path, [{"pmcid": "PMC123", "final_diagnosis": "Changed diagnosis"}, {"pmcid": "PMC456"}])
    result = register_sources(path)
    assert result["conflicts"] == 1 and result["unchanged"] == 1
    assert source.original_diagnosis == "Original English diagnosis"
    assert source.import_status == "source_error"


def test_uniqueness_is_per_dataset_and_count_all_is_supported(registry_app, tmp_path):
    path = csv_source(tmp_path / "source.csv")
    register_sources(path)
    register_sources(path, dataset="MedCaseReasoning-copy")
    assert source_counts()["registered"] == 2
    assert source_counts(None)["registered"] == 4


def test_snapshot_verified_sync_is_idempotent_and_selective(registry_app, tmp_path):
    path = csv_source(tmp_path / "source.csv")
    register_sources(path)
    work, _ = work_files(tmp_path, path)
    work_files(tmp_path, path, pmcid="PMC456", status="license_rejected")
    first = sync_import_records(work, pmcids=["PMC123"])
    assert first["synced"] == 1 and first["database"]["verified"] == 1
    assert first["database"]["statusCounts"] == {"registered": 1, "verified": 1}
    assert sync_import_records(work, pmcids=["PMC123"])["unchanged"] == 1
    assert sync_import_records(work)["synced"] == 1
    source = db.session.scalar(db.select(SourceRecord).where(SourceRecord.pmcid == "PMC123"))
    assert "articleText" not in source.provenance
    assert source.clinical_case_id is None and not source.translated


def test_claimed_verification_without_matching_snapshot_is_not_imported(registry_app, tmp_path):
    path = csv_source(tmp_path / "source.csv")
    register_sources(path)
    work, _ = work_files(tmp_path, path)
    next((work / "pmc-cache").glob("*.xml")).write_text("corrupt", encoding="utf-8")
    result = sync_import_records(work)
    assert result["invalid"] == 1 and result["database"]["verified"] == 0
    assert result["database"]["statusCounts"] == {"registered": 2}


def test_draft_is_validated_before_translation_count_and_link_only(registry_app, tmp_path, case_data):
    path = csv_source(tmp_path / "source.csv")
    register_sources(path)
    work, case = work_files(tmp_path, path, status="draft", case=case_data)
    first = sync_import_records(work)
    assert first["database"]["translated"] == 1 and first["database"]["drafts"] == 0
    assert db.session.scalar(db.select(db.func.count(ClinicalCase.id))) == 0
    clinical = ClinicalCase(slug=case["caseId"], title=case["caseTitle"], specialty=case["specialty"],
                            difficulty=case["difficulty"], summary=case["summary"], data=case, is_published=False)
    db.session.add(clinical)
    db.session.commit()
    assert sync_import_records(work)["linked"] == 1
    assert source_counts()["drafts"] == 1
    clinical.is_published = True
    db.session.commit()
    assert source_counts()["published"] == 1 and source_counts()["drafts"] == 0
    sync_import_records(work)
    assert clinical.is_published is True
    db.session.delete(clinical)
    db.session.commit()
    assert source_counts()["published"] == 0


def test_missing_or_mismatched_draft_cannot_be_counted_as_translated(registry_app, tmp_path, case_data):
    path = csv_source(tmp_path / "source.csv")
    register_sources(path)
    work, case = work_files(tmp_path, path, status="draft", case=case_data)
    case["provenance"]["xmlSha256"] = "f" * 64
    write_json(work / "drafts" / "PMC123.json", case)
    result = sync_import_records(work)
    assert result["invalid"] == 1 and result["database"]["translated"] == 0


def comparison_provenance():
    return {"articleTitle": "Original source", "authors": ["A. Author", "B. Author"],
            "xmlSha256": "a" * 64, "metadataSha256": "b" * 64, "doi": None, "pmcVersion": 1,
            "citation": "Journal 2024", "license": {"code": "CC BY", "text": "License notice",
            "url": "https://creativecommons.org/licenses/by/4.0/"}}


def test_source_comparison_only_strips_outer_bibliographic_whitespace_without_mutation():
    expected = comparison_provenance()
    expected["articleTitle"] = "\u00a0Original source\u2007"
    expected["authors"] = ["\u202fA. Author\t", " B. Author\u00a0"]
    actual = {**comparison_provenance(), "language": "pt-BR", "localPreparation": {"translated": True}}
    before = deepcopy((actual, expected))
    assert source_provenance_matches(actual, expected)
    assert source_provenance_matches(expected, comparison_provenance())
    assert (actual, expected) == before


@pytest.mark.parametrize(("field", "value"), [
    ("articleTitle", "Different source"),
    ("articleTitle", "Original  source"),
    ("authors", ["Changed Author", "B. Author"]),
    ("authors", ["B. Author", "A. Author"]),
    ("authors", ["A.  Author", "B. Author"]),
    ("authors", ["A. Author"]),
    ("xmlSha256", "f" * 64),
    ("metadataSha256", "f" * 64),
    ("citation", "Journal 2024 "),
    ("pmcVersion", True),
    ("license", {"code": "CC BY-NC", "text": "License notice", "url": "https://creativecommons.org/licenses/by/4.0/"}),
    ("license", {"code": "CC BY", "text": "License notice ", "url": "https://creativecommons.org/licenses/by/4.0/"}),
    ("license", {"code": "CC BY", "text": "License notice", "url": "https://creativecommons.org/licenses/by/3.0/"}),
])
def test_source_comparison_rejects_real_changes_and_other_whitespace(field, value):
    expected = comparison_provenance()
    actual = {**deepcopy(expected), field: value}
    assert not source_provenance_matches(actual, expected)


@pytest.mark.parametrize(("actual", "expected"), [
    (None, {"articleTitle": "Title"}),
    ({"articleTitle": "Title"}, None),
    ({}, {}),
    ({"articleTitle": ["Title"]}, {"articleTitle": "Title"}),
    ({"articleTitle": "Title"}, {"articleTitle": None}),
    ({"authors": "A. Author"}, {"authors": ["A. Author"]}),
    ({"authors": ["A. Author"]}, {"authors": ("A. Author",)}),
    ({"authors": [None]}, {"authors": ["A. Author"]}),
    ({"authors": ["A. Author"]}, {"authors": [None]}),
    ({}, {"doi": None}),
])
def test_source_comparison_rejects_invalid_structure_and_missing_fields(actual, expected):
    assert not source_provenance_matches(actual, expected)


def test_sync_accepts_normalized_draft_and_case_against_raw_bibliographic_spaces(registry_app, tmp_path, case_data):
    path = csv_source(tmp_path / "source.csv", [{"pmcid": "PMC123"}])
    register_sources(path)
    work, case = work_files(tmp_path, path, status="draft", case=case_data)
    cache_path = work / "pmc-cache" / "PMC123.verified.json"
    cache = json.loads(cache_path.read_text())
    cache["provenance"]["articleTitle"] = "\u00a0Original source\u2007"
    cache["provenance"]["authors"] = ["A. Author\u00a0"]
    write_json(cache_path, cache)
    before_cache = cache_path.read_bytes()
    draft_path = work / "drafts" / "PMC123.json"
    before_draft = draft_path.read_bytes()
    before_case = deepcopy(case)
    clinical = ClinicalCase(slug=case["caseId"], title=case["caseTitle"], specialty=case["specialty"],
                            difficulty=case["difficulty"], summary=case["summary"], data=case, is_published=False)
    db.session.add(clinical)
    db.session.commit()
    result = sync_import_records(work)
    assert result["invalid"] == 0 and result["linked"] == 1
    assert result["database"]["translated"] == 1
    source = db.session.scalar(db.select(SourceRecord).where(SourceRecord.pmcid == "PMC123"))
    assert source.provenance == cache["provenance"]
    assert clinical.data == before_case
    assert cache_path.read_bytes() == before_cache and draft_path.read_bytes() == before_draft
    assert sync_import_records(work)["unchanged"] == 1


@pytest.mark.parametrize("location", ["draft", "stored_case"])
def test_sync_rejects_changed_bibliographic_author_in_either_comparison(registry_app, tmp_path, case_data, location):
    path = csv_source(tmp_path / "source.csv", [{"pmcid": "PMC123"}])
    register_sources(path)
    work, case = work_files(tmp_path, path, status="draft", case=case_data)
    changed = deepcopy(case)
    changed["provenance"]["authors"] = ["Different Author"]
    if location == "draft":
        write_json(work / "drafts" / "PMC123.json", changed)
    else:
        db.session.add(ClinicalCase(slug=case["caseId"], title=case["caseTitle"], specialty=case["specialty"],
            difficulty=case["difficulty"], summary=case["summary"], data=changed, is_published=False))
        db.session.commit()
    result = sync_import_records(work)
    assert result["invalid"] == 1 and result["database"]["translated"] == 0


def test_source_mismatch_stale_checkpoints_and_unregistered_records_are_reported(registry_app, tmp_path):
    path = csv_source(tmp_path / "source.csv")
    register_sources(path)
    work, _ = work_files(tmp_path, path)
    assert sync_import_records(work)["synced"] == 1
    checkpoint_path = work / "records" / "PMC123.json"
    checkpoint = json.loads(checkpoint_path.read_text())
    checkpoint["updatedAt"] = "2026-10-14T12:01:00+00:00"
    write_json(checkpoint_path, checkpoint)
    assert sync_import_records(work)["stale"] == 1
    write_json(work / "records" / "PMC789.json", {})
    assert sync_import_records(work)["unregistered"] == 1
    manifest = json.loads((work / "manifest.json").read_text())
    manifest["sourceSha256"] = "0" * 64
    write_json(work / "manifest.json", manifest)
    assert sync_import_records(work)["invalid"] == 1
    with pytest.raises(SourceRegistryError):
        sync_import_records(work, pmcids=["../../escape"])


def test_portable_inventory_excludes_full_text_and_never_restores_pipeline_claims(registry_app, tmp_path):
    path = csv_source(tmp_path / "source.csv")
    register_sources(path)
    work, _ = work_files(tmp_path, path)
    sync_import_records(work)
    output = tmp_path / "metadata.jsonl"
    assert export_source_registry(output)["exported"] == 2
    text = output.read_text()
    assert "RAW ARTICLE" not in text and "RAW REASONING" not in text and "Cough" not in text
    assert "Original English diagnosis" in text
    assert output.stat().st_mode & 0o077 == 0
    db.session.execute(db.delete(SourceRecord))
    db.session.commit()
    result = import_source_registry(output, batch_size=1)
    assert result["new"] == 2 and result["database"]["verified"] == 0
    assert result["database"]["translated"] == 0 and result["database"]["published"] == 0
    assert import_source_registry(output)["unchanged"] == 2
    assert sync_import_records(work)["database"]["verified"] == 1


def test_portable_extra_fields_and_public_export_location_are_rejected(registry_app, tmp_path):
    register_sources(csv_source(tmp_path / "source.csv"))
    output = tmp_path / "metadata.jsonl"
    export_source_registry(output)
    lines = output.read_text().splitlines()
    row = json.loads(lines[1])
    row["verified"] = True
    output.write_text(lines[0] + "\n" + json.dumps(row) + "\n")
    assert import_source_registry(output)["invalid"] == 1
    with pytest.raises(SourceRegistryError):
        export_source_registry(Path(registry_app.static_folder) / "metadata.jsonl")


def test_additive_upgrade_is_idempotent_and_mysql_ddl_has_safe_types(registry_app):
    SourceRecord.__table__.drop(db.engine)
    upgrade_database()
    upgrade_database()
    assert "source_record" in inspect(db.engine).get_table_names()
    ddl = str(CreateTable(SourceRecord.__table__).compile(dialect=mysql.dialect()))
    assert "UNIQUE (dataset, pmcid)" in ddl
    assert "original_metadata JSON" in ddl and "original_diagnosis TEXT" in ddl
    assert "ON DELETE SET NULL" in ddl


def test_registered_private_diagnoses_do_not_leak_into_student_catalog(app, login, tmp_path):
    with app.app_context():
        register_sources(csv_source(tmp_path / "source.csv"))
    response = login().get("/casos")
    assert response.status_code == 200
    assert b"Secret English diagnosis title" not in response.data
    assert b"Original English diagnosis" not in response.data


def test_explicit_selection_excludes_only_rejected_ids_and_cannot_be_reintroduced(registry_app, tmp_path):
    path = csv_source(tmp_path / "source.csv", [{"pmcid": "PMC123"}, {"pmcid": "PMC456"}, {"pmcid": "PMC789"}])
    register_sources(path)
    work, _ = work_files(tmp_path, path, status="license_rejected")
    work_files(tmp_path, path, pmcid="PMC456", status="license_rejected", license_code="CC BY-ND")
    synced = sync_import_records(work)
    assert synced["database"]["registered"] == 3
    assert synced["database"]["statusCounts"] == {"license_rejected": 2, "registered": 1}
    assert excluded_pmcids() == set(), "Synchronization must never prune automatically."

    result = prune_restricted_sources(pmcids=["PMC123", "PMC789"], batch_size=1)
    assert result["removed"] == 1 and result["skipped"] == 0 and result["excluded"] == 1
    assert result["database"]["registered"] == 2
    assert excluded_pmcids() == {"PMC123"}
    assert prune_restricted_sources(pmcids=["PMC123"])["removed"] == 0
    again = register_sources(path)
    assert again["excluded"] == 1 and again["unchanged"] == 2 and again["new"] == 0
    assert sync_import_records(work)["excluded"] == 1
    assert source_counts()["registered"] == 2
    assert db.session.scalar(db.select(db.func.count(ClinicalCase.id))) == 0

    # Identity and exclusion are scoped to a dataset, not an unrelated import.
    assert register_sources(path, dataset="AnotherDataset")["new"] == 3
    assert excluded_pmcids("AnotherDataset") == set()
    for invalid in ([], "PMC123", ["../PMC123"]):
        with pytest.raises(SourceRegistryError):
            prune_restricted_sources(pmcids=invalid)


def test_exclusion_preserves_linked_cases_history_and_legacy_case_slug(registry_app, tmp_path, case_data):
    register_sources(csv_source(tmp_path / "source.csv"))
    records = db.session.scalars(db.select(SourceRecord).order_by(SourceRecord.id)).all()
    cases = []
    for index, record in enumerate(records):
        slug = "mcr-" + record.pmcid.lower() if index else "reviewed-imported-case"
        case = ClinicalCase(slug=slug, title=case_data["caseTitle"], specialty=case_data["specialty"],
            difficulty=case_data["difficulty"], summary=case_data["summary"], data=case_data, is_published=True)
        db.session.add(case)
        db.session.flush()
        record.import_status = "license_rejected"
        if not index:
            record.clinical_case_id = case.id
        cases.append(case)
    user = User(name="Synthetic test", email="history@example.invalid", password_hash="unused-test-hash")
    db.session.add(user)
    db.session.flush()
    history = Consultation(user_id=user.id, case_id=cases[0].id, case_snapshot=case_data,
        status="completed", diagnosis="Recorded hypothesis", treatment="Recorded plan", score=80)
    db.session.add(history)
    db.session.commit()
    history_id = history.id
    result = prune_restricted_sources(pmcids=["PMC123", "PMC456"])
    assert result["removed"] == 0 and result["skipped"] == 2
    assert excluded_pmcids() == set()
    assert source_counts()["registered"] == 2
    assert db.session.get(Consultation, history_id).diagnosis == "Recorded hypothesis"
    assert db.session.scalar(db.select(db.func.count(ClinicalCase.id))) == 2


def test_minimal_exclusions_roundtrip_and_block_old_inventory(registry_app, tmp_path):
    path = csv_source(tmp_path / "source.csv", [
        {"pmcid": "PMC123", "title": "EXCLUDED_TITLE", "final_diagnosis": "EXCLUDED_DIAGNOSIS"}, {"pmcid": "PMC456"}])
    register_sources(path)
    old = tmp_path / "old-v1.jsonl"
    export_source_registry(old)
    old_lines = [json.loads(line) for line in old.read_text().splitlines()]
    old_lines[0]["version"] = 1
    old_lines[0].pop("exclusionsIncluded")
    old.write_text("\n".join(json.dumps(value) for value in old_lines) + "\n")
    record = db.session.scalar(db.select(SourceRecord).where(SourceRecord.pmcid == "PMC123"))
    record.import_status = "license_rejected"
    db.session.commit()
    prune_restricted_sources(pmcids=["PMC123"])
    assert import_source_registry(old)["excluded"] == 1
    output = tmp_path / "with-exclusions.jsonl"
    report = export_source_registry(output)
    assert report["exported"] == report["excluded"] == 1
    content = output.read_text()
    lines = [json.loads(line) for line in content.splitlines()]
    assert lines[0]["version"] == 2 and lines[0]["exclusionsIncluded"] is True
    assert set(lines[1]) == {"type", "dataset", "pmcid", "reason", "created_at"}
    assert "EXCLUDED_TITLE" not in content and "EXCLUDED_DIAGNOSIS" not in content
    assert set(SourceExclusion.__table__.columns.keys()) == {"dataset", "pmcid", "reason", "created_at"}
    db.session.execute(db.delete(SourceRecord))
    db.session.execute(db.delete(SourceExclusion))
    db.session.commit()
    restored = import_source_registry(output, batch_size=1)
    assert restored["new"] == 1 and restored["exclusions"]["new"] == 1
    assert restored["database"]["registered"] == 1 and restored["database"]["translated"] == 0
    assert excluded_pmcids() == {"PMC123"}
    again = import_source_registry(output)
    assert again["new"] == 0 and again["unchanged"] == 1
    assert again["exclusions"]["new"] == 0 and again["exclusions"]["unchanged"] == 1
    assert import_source_registry(old)["excluded"] == 1
    assert register_sources(path)["excluded"] == 1
    assert db.session.scalar(db.select(db.func.count(ClinicalCase.id))) == 0


def test_restore_exclusions_win_after_metadata_rows_and_reject_extra_contents(registry_app, tmp_path):
    path = csv_source(tmp_path / "source.csv")
    register_sources(path)
    output = tmp_path / "reordered.jsonl"
    export_source_registry(output)
    exclusion = {"type": "exclusion", "dataset": "MedCaseReasoning", "pmcid": "PMC123",
        "reason": "source_rejected", "created_at": "2026-10-15T12:00:00+00:00"}
    with output.open("a") as handle:
        handle.write(json.dumps(exclusion) + "\n")
        handle.write(json.dumps({**exclusion, "pmcid": "PMC456", "original_diagnosis": "must not be retained"}) + "\n")
    result = import_source_registry(output)
    assert result["exclusions"]["new"] == 1 and result["exclusions"]["removed"] == 1
    assert result["invalid"] == 1 and result["excluded"] == 1
    assert excluded_pmcids() == {"PMC123"}
    assert source_counts()["registered"] == 1


def test_restore_exclusion_cannot_delete_a_case_or_its_source(registry_app, tmp_path, case_data):
    path = csv_source(tmp_path / "source.csv")
    register_sources(path)
    case = ClinicalCase(slug="mcr-pmc123", title=case_data["caseTitle"], specialty=case_data["specialty"],
        difficulty=case_data["difficulty"], summary=case_data["summary"], data=case_data, is_published=True)
    db.session.add(case)
    db.session.commit()
    output = tmp_path / "source-with-link.jsonl"
    export_source_registry(output)
    lines = output.read_text().splitlines()
    tombstone = {"type": "exclusion", "dataset": "MedCaseReasoning", "pmcid": "PMC123",
        "reason": "source_rejected", "created_at": "2026-10-15T12:00:00+00:00"}
    output.write_text(lines[0] + "\n" + json.dumps(tombstone) + "\n")
    result = import_source_registry(output)
    assert result["exclusions"]["skipped"] == 1 and result["exclusions"]["removed"] == 0
    assert excluded_pmcids() == set() and source_counts()["registered"] == 2
    assert db.session.get(ClinicalCase, case.id).is_published is True


@pytest.mark.parametrize("code,scope,enabled,basis,expected", [
    ("CC BY-ND", "commercial", False, "clinical_facts", False),
    ("CC BY-ND", "commercial", True, "clinical_facts", True),
    ("CC BY-NC-ND", "noncommercial", True, "clinical_facts", True),
    ("CC BY-NC-ND", "commercial", True, "clinical_facts", False),
    ("CC BY-ND", "noncommercial", True, None, False),
    ("CC BY-ND", "noncommercial", True, "licensed_adaptation", False),
    ("CC BY", "noncommercial", True, "clinical_facts", False),
])
def test_factual_source_verification_requires_opt_in_basis_and_original_license(registry_app, tmp_path, code, scope, enabled, basis, expected):
    registry_app.config.update(CASE_REUSE_SCOPE=scope, CASE_ALLOW_CLINICAL_FACTS=enabled)
    path = csv_source(tmp_path / "source.csv")
    register_sources(path)
    work, _ = work_files(tmp_path, path, license_code=code, reuse_basis=basis)
    result = sync_import_records(work)
    assert result["synced"] == int(expected) and result["invalid"] == int(not expected)
    assert result["database"]["verified"] == int(expected)
    if expected:
        source = db.session.scalar(db.select(SourceRecord).where(SourceRecord.pmcid == "PMC123"))
        assert source.provenance["license"]["code"] == code
        assert source.provenance["reuseBasis"] == "clinical_facts"
        assert "articleText" not in source.provenance
        registry_app.config["CASE_ALLOW_CLINICAL_FACTS"] = False
        assert source_counts()["verified"] == 0
        registry_app.config["CASE_ALLOW_CLINICAL_FACTS"] = True
        assert source_counts()["verified"] == 1


def test_exclusion_additive_schema_upgrade_uses_only_minimal_safe_fields(registry_app):
    SourceExclusion.__table__.drop(db.engine)
    upgrade_database()
    upgrade_database()
    assert "source_exclusion" in inspect(db.engine).get_table_names()
    ddl = str(CreateTable(SourceExclusion.__table__).compile(dialect=mysql.dialect()))
    assert "PRIMARY KEY (dataset, pmcid)" in ddl
    assert "JSON" not in ddl and "TEXT" not in ddl
