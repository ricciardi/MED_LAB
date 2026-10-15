"""Portable real drafts restore privately, atomically and without XML claims."""
from copy import deepcopy
import json
from pathlib import Path

import pytest
import click

from medlab import cli
from medlab.extensions import db
from medlab.importer import DATASET_URL
from medlab.models import ClinicalCase, SourceRecord, User
from medlab.source_registry import source_counts
from test_clinical_facts import factual_inputs
from test_corpus_cli import corpus_apps
from test_importer import ARTICLE, SPEC, real_case


def create_bundle(tmp_path, monkeypatch, cases, *, inventory_cases=None, excluded=()):
    data = tmp_path / "package" / "medlab" / "data"
    data.mkdir(parents=True)
    original_cli = Path(cli.__file__)
    (data / "cases_pt.json").write_bytes((original_cli.parent / "data" / "cases_pt.json").read_bytes())
    (data / "real_case_drafts.json").write_text(json.dumps(cases, ensure_ascii=False), encoding="utf-8")
    header = {"format": "medlab-source-metadata", "version": 2, "dataset": "MedCaseReasoning",
        "metadataOnly": True, "pipelineStateIncluded": False, "exclusionsIncluded": True}
    rows = [header]
    for pmcid in excluded:
        rows.append({"type": "exclusion", "dataset": "MedCaseReasoning", "pmcid": pmcid,
            "reason": "source_rejected", "created_at": "2026-10-15T12:00:00+00:00"})
    for index, case in enumerate(inventory_cases if inventory_cases is not None else cases, 1):
        origin = case["provenance"]
        if origin["pmcid"] in excluded:
            continue
        rows.append({"dataset": origin["dataset"], "pmcid": origin["pmcid"],
            "dataset_revision": origin["datasetRevision"], "dataset_url": DATASET_URL,
            "source_file_sha256": "a" * 64, "record_sha256": "b" * 64, "source_row_number": index,
            "original_title": origin["articleTitle"], "original_diagnosis": "Synthetic original diagnosis",
            "original_metadata": {key: "" for key in ("journal", "article_link", "publication_date", "split", "index")}})
    (data / "source_registry.jsonl").write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    monkeypatch.setattr(cli, "__file__", str(data.parent / "cli.py"))
    return data


def invoke(application, command):
    result = application.test_cli_runner().invoke(args=[command])
    assert result.exit_code == 0, result.output
    return result


def test_fresh_init_restores_private_draft_then_preserves_review_and_local_verification(corpus_apps, tmp_path, monkeypatch):
    payload = real_case()
    payload["reviewStatus"] = "reviewed"  # Package review never auto-publishes on installation.
    create_bundle(tmp_path, monkeypatch, [payload])
    app = corpus_apps()
    result = invoke(app, "init-db")
    assert "Rascunhos reais: 1 novos" in result.output
    with app.app_context():
        draft = db.session.scalar(db.select(ClinicalCase).where(ClinicalCase.slug == payload["caseId"]))
        source = db.session.scalar(db.select(SourceRecord))
        assert draft is not None and draft.is_published is False and draft.is_generated is False
        assert draft.data["reviewStatus"] == "review_required"
        assert source.clinical_case_id == draft.id and source.translated is True
        assert source.verified is False and source.import_status == "draft"
        assert source.checkpoint_sha256 is None and source.checkpoint_updated_at is None
        assert source_counts() == {"registered": 1, "verified": 0, "translated": 1, "drafts": 1,
            "published": 0, "statusCounts": {"draft": 1}}
        assert db.session.scalar(db.select(db.func.count(User.id))) == 0
        draft_id, source_id = draft.id, source.id
        initial_count = db.session.scalar(db.select(db.func.count(ClinicalCase.id)))
    repeated = invoke(app, "init-db")
    assert "0 novos; 1 já existentes" in repeated.output
    with app.app_context():
        draft = db.session.get(ClinicalCase, draft_id)
        revised = deepcopy(draft.data)
        revised["reviewStatus"] = "reviewed"
        revised["caseTitle"] = "Local review title"
        draft.data, draft.title, draft.is_published = revised, "Local review title", True
        source = db.session.get(SourceRecord, source_id)
        source.verified = True
        source.checkpoint_sha256 = "d" * 64
        db.session.commit()
    invoke(app, "upgrade-db")
    with app.app_context():
        draft = db.session.get(ClinicalCase, draft_id)
        source = db.session.get(SourceRecord, source_id)
        assert draft.title == draft.data["caseTitle"] == "Local review title"
        assert draft.data["reviewStatus"] == "reviewed" and draft.is_published is True
        assert source.verified is True and source.checkpoint_sha256 == "d" * 64
        assert source.clinical_case_id == draft_id
        assert db.session.scalar(db.select(db.func.count(ClinicalCase.id))) == initial_count


@pytest.mark.parametrize("failure", ["schema", "revision", "source_id", "missing_source", "duplicate"])
def test_entire_bundle_validates_before_creating_any_draft(corpus_apps, tmp_path, monkeypatch, failure):
    valid, second = real_case(), real_case("PMC456")
    inventory = [deepcopy(valid), deepcopy(second)]
    if failure == "schema":
        second.pop("diagnoses")
    elif failure == "revision":
        second["provenance"]["datasetRevision"] = "different-revision"
    elif failure == "source_id":
        second["provenance"]["sourceRecordId"] = "PMC789"
    elif failure == "missing_source":
        inventory = [valid]
    elif failure == "duplicate":
        second = deepcopy(valid)
    create_bundle(tmp_path, monkeypatch, [valid, second], inventory_cases=inventory)
    app = corpus_apps()
    result = app.test_cli_runner().invoke(args=["init-db"])
    assert result.exit_code != 0
    assert "Nenhum rascunho deste lote foi alterado" in result.output
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(ClinicalCase.id)).where(ClinicalCase.slug.like("mcr-%"))) == 0
        assert source_counts()["translated"] == source_counts()["drafts"] == source_counts()["verified"] == 0
        assert all(source.clinical_case_id is None for source in db.session.scalars(db.select(SourceRecord)))


@pytest.mark.parametrize("enabled,expected", [(False, 0), (True, 1)])
def test_factual_drafts_require_opt_in_without_claiming_local_verification(corpus_apps, tmp_path, monkeypatch, enabled, expected):
    service, origin, *_ = factual_inputs()
    payload = service.adapt_case({"articleText": ARTICLE}, origin, SPEC)
    create_bundle(tmp_path, monkeypatch, [payload])
    app = corpus_apps()
    app.config["CASE_ALLOW_CLINICAL_FACTS"] = enabled
    result = invoke(app, "init-db")
    assert f"{1 - expected} ignorados pela política atual" in result.output
    with app.app_context():
        assert source_counts()["translated"] == source_counts()["drafts"] == expected
        assert source_counts()["verified"] == source_counts()["published"] == 0
        assert db.session.scalar(db.select(db.func.count(ClinicalCase.id)).where(ClinicalCase.slug.like("mcr-%"))) == expected


def test_excluded_source_in_bundle_remains_absent(corpus_apps, tmp_path, monkeypatch):
    payload = real_case()
    create_bundle(tmp_path, monkeypatch, [payload], excluded=["PMC123"])
    app = corpus_apps()
    result = invoke(app, "init-db")
    assert "1 fontes excluídas" in result.output
    with app.app_context():
        assert source_counts()["registered"] == 0
        assert db.session.scalar(db.select(ClinicalCase).where(ClinicalCase.slug == payload["caseId"])) is None


def test_conflicting_local_provenance_cannot_be_overwritten(corpus_apps, tmp_path, monkeypatch):
    payload = real_case()
    create_bundle(tmp_path, monkeypatch, [payload])
    app = corpus_apps()
    with app.app_context():
        db.create_all()
        cli.restore_bundled_inventory()
        source = db.session.scalar(db.select(SourceRecord))
        source.provenance = {**payload["provenance"], "xmlSha256": "f" * 64}
        source.verified = True
        db.session.commit()
        with pytest.raises(click.ClickException, match="Nenhum rascunho deste lote"):
            cli.restore_bundled_drafts()
        source = db.session.scalar(db.select(SourceRecord))
        assert source.verified is True and source.provenance["xmlSha256"] == "f" * 64
        assert source.translated is False and source.clinical_case_id is None
        assert db.session.scalar(db.select(db.func.count(ClinicalCase.id))) == 0
