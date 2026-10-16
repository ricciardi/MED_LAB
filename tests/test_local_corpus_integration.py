"""Backup, atomic progress and preservation for the explicit local incorporation."""
import json
import hashlib
import sqlite3

import pytest

from medlab import cli
from medlab.extensions import db
from medlab.models import ClinicalCase, SourceRecord
from medlab.source_registry import source_counts
from scripts import integrate_local_corpus as integration
from test_bundled_drafts import create_bundle
from test_corpus_cli import corpus_apps
from test_importer import real_case


AUTHORIZATION = "Synthetic explicit owner authorization before clinical review."


def test_ready_batches_skip_pending_cases_and_reuse_the_original_backup(corpus_apps, tmp_path, monkeypatch):
    application, output, processed, cases = setup_corpus(tmp_path, monkeypatch, corpus_apps)
    pending = output / "cases" / "PMC789.json"
    pending.unlink()
    with application.app_context():
        first = integration.integrate_corpus(output, processed, apply=True, ready_only=True,
            pmcids=["PMC456", "PMC789"], operation_id="synthetic-ready-operation",
            authorization_reference=AUTHORIZATION)
        assert (first["expected"], first["selected"], first["ready"]) == (3, 2, 1)
        assert (first["completed"], first["pending"], first["batchCompleted"]) == (1, 2, 1)
        assert first["backupReused"] is False and first["awaitingCase"] == 1
        pending.write_text(json.dumps(cases[2]))
        second = integration.integrate_corpus(output, processed, apply=True, ready_only=True,
            pmcids=["PMC123", "PMC789"], operation_id="synthetic-ready-operation",
            authorization_reference=AUTHORIZATION)
        assert second["completed"] == 3 and second["pending"] == 0
        assert second["backupReused"] is True and second["backup"] == first["backup"]
        assert len(list((output / "backups").glob("*.sqlite"))) == 1
        with sqlite3.connect(first["backup"]) as saved:
            assert saved.execute("SELECT count(*) FROM clinical_case").fetchone()[0] == 1
        assert source_counts()["published"] == 3 and source_counts()["verified"] == 0
        again = integration.integrate_corpus(output, processed, apply=True, ready_only=True,
            pmcids=["PMC789"], operation_id="synthetic-ready-operation",
            authorization_reference=AUTHORIZATION)
        assert again["unchanged"] == 1 and again["inserted"] == again["updated"] == 0


def test_empty_ready_selection_does_not_create_backup_or_require_unselected_cases(corpus_apps, tmp_path, monkeypatch):
    application, output, processed, cases = setup_corpus(tmp_path, monkeypatch, corpus_apps)
    (output / "cases" / "PMC789.json").unlink()
    (output / "cases" / "PMC456.json").write_text("not-valid-json")
    with application.app_context():
        report = integration.integrate_corpus(output, processed, apply=True, ready_only=True,
            pmcids=["PMC789"], operation_id="synthetic-empty-operation",
            authorization_reference=AUTHORIZATION)
        assert report["ready"] == report["inserted"] == 0 and report["pending"] == 3
        assert not (output / "backups").exists()
        with pytest.raises(ValueError, match="fora do plano"):
            integration.integrate_corpus(output, processed, ready_only=True, pmcids=["PMC999"],
                authorization_reference=AUTHORIZATION)


def setup_corpus(tmp_path, monkeypatch, corpus_apps):
    cases = [real_case(pmcid) for pmcid in ("PMC123", "PMC456", "PMC789")]
    create_bundle(tmp_path, monkeypatch, cases)
    application = corpus_apps()
    output, processed = tmp_path / "corpus", tmp_path / "processed"
    (output / "cases").mkdir(parents=True)
    (processed / "drafts").mkdir(parents=True)
    (output / "plan.json").write_text(json.dumps({"format": "medlab-local-corpus-v1",
        "activeIds": [case["provenance"]["pmcid"] for case in cases]}))
    for case in cases:
        (output / "cases" / (case["provenance"]["pmcid"] + ".json")).write_text(json.dumps(case))
    with application.app_context():
        db.create_all()
        cli.restore_bundled_inventory()
        case = cases[0]
        db.session.add(ClinicalCase(slug=case["caseId"], title=case["caseTitle"], specialty=case["specialty"],
            difficulty=case["difficulty"], summary=case["summary"], data=case, is_published=False))
        db.session.commit()
    return application, output, processed, cases


def test_preflight_is_read_only_and_apply_backs_up_then_preserves_content(corpus_apps, tmp_path, monkeypatch):
    application, output, processed, cases = setup_corpus(tmp_path, monkeypatch, corpus_apps)
    (processed / "records").mkdir()
    usage = {"requests": 3, "promptTokens": 1234, "cacheHitTokens": 400}
    (processed / "records" / "PMC123.json").write_text(json.dumps({"pmcid": "PMC123", "status": "adaptation_error",
        "attempts": 3, "error": "Synthetic previous failure", "aiUsage": usage,
        "preparedBy": "Original preparation identity", "updatedAt": "2026-10-15T00:00:00+00:00"}))
    with application.app_context():
        source = db.session.scalar(db.select(SourceRecord).where(SourceRecord.pmcid == "PMC123"))
        source.last_error = "Synthetic previous failure"
        db.session.commit()
        report = integration.integrate_corpus(output, processed, authorization_reference=AUTHORIZATION)
        assert report["dryRun"] is True and report["preparedToInsert"] == 2
        assert db.session.scalar(db.select(db.func.count(ClinicalCase.id))) == 1
        assert not (output / "backups").exists()
        report = integration.integrate_corpus(output, processed, apply=True, authorization_reference=AUTHORIZATION)
        assert report["inserted"] == 2 and report["updated"] == 1
        with sqlite3.connect(report["backup"]) as backup:
            assert backup.execute("SELECT count(*) FROM clinical_case").fetchone()[0] == 1
        assert source_counts()["published"] == source_counts()["translated"] == 3
        assert source_counts()["verified"] == 0
        original = db.session.scalar(db.select(ClinicalCase).where(ClinicalCase.slug == cases[0]["caseId"]))
        assert original.data["provenance"] == cases[0]["provenance"]
        assert original.data["patientProfile"] == cases[0]["patientProfile"]
        assert original.data["reviewStatus"] == "review_required" and original.is_published
        record_path = processed / "records" / "PMC123.json"
        record = json.loads(record_path.read_text())
        assert record["aiUsage"] == usage and record["attempts"] == 3
        assert record["preparedBy"] == "Original preparation identity" and "error" not in record
        draft = processed / "drafts" / "PMC123.json"
        assert record["draftSha256"] == hashlib.sha256(draft.read_bytes()).hexdigest()
        source = db.session.scalar(db.select(SourceRecord).where(SourceRecord.pmcid == "PMC123"))
        assert source.last_error is None and source.attempts == 3
        assert source.checkpoint_sha256 == hashlib.sha256(record_path.read_bytes()).hexdigest()
        assert source.verified is False
        again = integration.integrate_corpus(output, processed, apply=True, authorization_reference=AUTHORIZATION)
        assert again["inserted"] == again["updated"] == 0 and again["unchanged"] == 3


def test_missing_last_case_fails_before_backup_or_first_mutation(corpus_apps, tmp_path, monkeypatch):
    application, output, processed, _ = setup_corpus(tmp_path, monkeypatch, corpus_apps)
    (output / "cases" / "PMC789.json").unlink()
    with application.app_context():
        with pytest.raises(ValueError, match="Ficha local ausente"):
            integration.integrate_corpus(output, processed, apply=True, authorization_reference=AUTHORIZATION)
        assert not (output / "backups").exists()
        assert source_counts()["published"] == source_counts()["translated"] == 0
        assert not db.session.scalar(db.select(ClinicalCase)).is_published


def test_interrupted_batch_preserves_each_completed_case_and_resumes(corpus_apps, tmp_path, monkeypatch):
    application, output, processed, _ = setup_corpus(tmp_path, monkeypatch, corpus_apps)
    original_load = integration.load_case
    last_reads = 0
    def interrupted(path):
        nonlocal last_reads
        if path.stem == "PMC789":
            last_reads += 1
            if last_reads == 2:
                raise OSError("Synthetic interrupted read after two committed records")
        return original_load(path)
    monkeypatch.setattr(integration, "load_case", interrupted)
    with application.app_context():
        with pytest.raises(OSError, match="Synthetic interrupted"):
            integration.integrate_corpus(output, processed, apply=True, authorization_reference=AUTHORIZATION)
        assert source_counts()["published"] == source_counts()["translated"] == 2
        monkeypatch.setattr(integration, "load_case", original_load)
        report = integration.integrate_corpus(output, processed, apply=True, authorization_reference=AUTHORIZATION)
        assert report["inserted"] == 1 and report["unchanged"] == 2
        assert source_counts()["published"] == 3
