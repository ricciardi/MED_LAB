#!/usr/bin/env python3
"""Incorporate an authorized local corpus, with backup and resumable transactions.

Dry run is the default. No provider is called, no clinical review is recorded,
and existing case content is preserved. Run only after local translation/build.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode()).hexdigest()


def load_case(path):
    from medlab.case_schema import validate_case
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 1024 * 1024:
        raise ValueError("Ficha local ausente ou inválida: " + path.name)
    return validate_case(json.loads(path.read_text(encoding="utf-8")))


def _without_authorization(data):
    return {key: value for key, value in data.items() if key != "useAuthorization"}


def _json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def _atomic_bytes(path, content):
    if path.is_symlink():
        raise ValueError("O arquivo privado não pode ser um link simbólico.")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=".local-incorporation-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content); handle.flush(); os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


def _checkpoint(path, pmcid, source):
    if not path.exists():
        return {"pmcid": pmcid, "status": source.import_status, "attempts": source.attempts or 0}
    if path.is_symlink() or path.stat().st_size > 1024 * 1024:
        raise ValueError("Checkpoint privado inválido: " + pmcid)
    value = json.loads(path.read_text())
    if not isinstance(value, dict) or value.get("pmcid") != pmcid:
        raise ValueError("Identidade do checkpoint inválida: " + pmcid)
    if type(value.get("attempts", 0)) is not int or value.get("attempts", 0) < 0:
        raise ValueError("Contagem de tentativas inválida: " + pmcid)
    return value


def _persist_processed(processed, data, source):
    """Persist a completed case without resetting paid usage or source verification."""
    from flask import current_app
    from medlab.case_schema import REUSE_POLICY_VERSION
    if data["reviewStatus"] == "reviewed":
        # An already reviewed live case is never downgraded into an import draft.
        return None
    pmcid, origin = source.pmcid, data["provenance"]
    path = processed / "drafts" / (pmcid + ".json")
    content = _json_bytes(data)
    if path.exists():
        stored = load_case(path)
        if _without_authorization(stored) != _without_authorization(data):
            raise ValueError("Rascunho privado mudou durante a incorporação: " + pmcid)
    if not path.exists() or path.read_bytes() != content:
        _atomic_bytes(path, content)
    record_path = processed / "records" / (pmcid + ".json")
    record = _checkpoint(record_path, pmcid, source)
    updated = {**record, "status": "draft", "caseId": data["caseId"], "specialty": data["specialty"],
        "license": origin["license"]["code"], "reuseBasis": origin.get("reuseBasis", "licensed_adaptation"),
        "sourceCheckedAt": origin["checkedAt"], "reuseScope": current_app.config["CASE_REUSE_SCOPE"],
        "allowClinicalFacts": current_app.config["CASE_ALLOW_CLINICAL_FACTS"], "policyVersion": REUSE_POLICY_VERSION,
        "reviewStatus": "review_required", "draftSha256": hashlib.sha256(content).hexdigest()}
    updated.setdefault("preparedBy", origin["adaptedBy"])
    updated.pop("error", None); updated.pop("availabilityError", None)
    if "localPreparation" in origin:
        updated["preparationMethod"] = "local_structured_translation"
    if updated != record or not record_path.exists():
        updated["updatedAt"] = datetime.now(timezone.utc).isoformat()
        _atomic_bytes(record_path, _json_bytes(updated))
    timestamp = datetime.fromisoformat(updated["updatedAt"].replace("Z", "+00:00"))
    if timestamp.tzinfo is None:
        raise ValueError("Checkpoint sem fuso horário: " + pmcid)
    return (hashlib.sha256(record_path.read_bytes()).hexdigest(),
            timestamp.astimezone(timezone.utc).replace(tzinfo=None), updated.get("attempts", 0))


def _source_identity(data, source):
    from flask import current_app
    from medlab.case_schema import source_license_allowed
    origin = data.get("provenance", {})
    if (data.get("catalogType") != "published_case" or origin.get("dataset") != source.dataset
            or origin.get("pmcid") != source.pmcid or origin.get("sourceRecordId") != source.pmcid
            or data["caseId"] != "mcr-" + source.pmcid.lower()
            or origin.get("datasetRevision") != source.dataset_revision):
        raise ValueError("Ficha e registro de origem não coincidem: " + source.pmcid)
    if isinstance(source.provenance, dict):
        for key in ("dataset", "datasetRevision", "sourceRecordId", "pmcid", "pmcVersion", "xmlSha256", "xmlMd5", "metadataSha256"):
            if source.provenance.get(key) != origin.get(key):
                raise ValueError("Hash ou versão da fonte diverge: " + source.pmcid)
        if source.provenance.get("license") != origin.get("license"):
            raise ValueError("Licença da fonte diverge: " + source.pmcid)
    if not source_license_allowed(origin, current_app.config["CASE_REUSE_SCOPE"], current_app.config["CASE_ALLOW_CLINICAL_FACTS"]):
        raise ValueError("Fonte fora da política configurada: " + source.pmcid)


def _backup_sqlite(directory):
    from medlab.extensions import db
    if db.engine.url.get_backend_name() != "sqlite" or db.engine.url.database in {None, "", ":memory:"}:
        raise ValueError("A incorporação exige um banco SQLite em arquivo para cópia de segurança.")
    source = Path(db.engine.url.database).resolve()
    if not source.is_file():
        raise ValueError("Banco de origem ausente.")
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    target = directory / ("before-local-corpus-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid4().hex[:8] + ".sqlite")
    descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    try:
        with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as original:
            with sqlite3.connect(target) as backup:
                original.backup(backup)
                if backup.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise ValueError("Cópia de segurança não passou na verificação estrutural.")
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return target


def _operation_backup(output, directory, active, authorization_reference, operation_id):
    """One verified backup per incremental operation, shared by every ready batch."""
    from medlab.extensions import db
    database = str(Path(db.engine.url.database or "").resolve())
    operation_id = operation_id or "ready-" + digest({"database": database,
        "active": sorted(active), "authorization": authorization_reference})[:24]
    if not isinstance(operation_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,119}", operation_id):
        raise ValueError("Identificador da operação inválido.")
    identity = {"database": database, "operationId": operation_id,
                "planSha256": digest(sorted(active)), "authorizationSha256": digest(authorization_reference)}
    marker = Path(output) / "integration-operations" / (operation_id + ".json")
    if marker.exists() or marker.is_symlink():
        if marker.is_symlink() or marker.stat().st_size > 1024 * 1024:
            raise ValueError("Marcador de backup inválido.")
        saved = json.loads(marker.read_text())
        if saved.get("format") != "medlab-integration-operation-v1" or saved.get("identity") != identity:
            raise ValueError("A operação não corresponde ao banco, plano ou autorização atuais.")
        backup = Path(saved.get("backup", ""))
        if backup.is_symlink() or not backup.is_file() or backup.stat().st_size != saved.get("backupBytes"):
            raise ValueError("O backup original da operação está ausente ou foi alterado.")
        return backup, operation_id, True
    backup = _backup_sqlite(directory)
    _atomic_bytes(marker, _json_bytes({"format": "medlab-integration-operation-v1", "identity": identity,
        "backup": str(backup.resolve()), "backupBytes": backup.stat().st_size,
        "createdAt": datetime.now(timezone.utc).isoformat()}))
    return backup, operation_id, False


@contextmanager
def _import_lock(processed, apply):
    """Use the existing importer's lock, preventing a simultaneous paid rewrite."""
    import fcntl
    path = Path(processed) / ".import.lock"
    if not apply and not path.exists():
        yield
        return
    if path.is_symlink():
        raise ValueError("Lock da importação inválido.")
    if apply:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("a" if apply else "r") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("Já existe uma preparação usando este diretório. Aguarde a conclusão.") from None
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def integrate_corpus(output, processed, *, apply=False, authorization_reference=None, backup_dir=None,
                     on_progress=None, ready_only=False, pmcids=None, operation_id=None):
    with _import_lock(processed, apply):
        return _integrate_corpus(output, processed, apply=apply, authorization_reference=authorization_reference,
                                 backup_dir=backup_dir, on_progress=on_progress, ready_only=ready_only,
                                 pmcids=pmcids, operation_id=operation_id)


def _integrate_corpus(output, processed, *, apply=False, authorization_reference=None, backup_dir=None,
                      on_progress=None, ready_only=False, pmcids=None, operation_id=None):
    """Validate the selected ready inventory, then commit one case at a time.

    Call inside an app context. Failures preserve previous completed transactions;
    rerunning keeps content and resumes the remaining records. No case is deleted.
    """
    from medlab.case_schema import is_nonhuman_case, is_provisionally_accepted, validate_case
    from medlab.catalogue import case_type_for
    from medlab.extensions import db
    from medlab.local_case_integration import authorize_provisional_use
    from medlab.models import ClinicalCase, SourceRecord
    from medlab.source_registry import _BASE_PROVENANCE, excluded_pmcids
    output, processed = Path(output), Path(processed)
    plan = json.loads((output / "plan.json").read_text())
    active = plan.get("activeIds")
    if (plan.get("format") != "medlab-local-corpus-v1" or not isinstance(active, list) or not active
            or len(set(active)) != len(active) or any(not isinstance(p, str) for p in active)):
        raise ValueError("Plano local inválido.")
    if not isinstance(authorization_reference, str) or not authorization_reference.strip():
        raise ValueError("Informe a autorização explícita de uso com revisão pendente.")
    if type(ready_only) is not bool:
        raise ValueError("Modo incremental inválido.")
    known = set(db.session.scalars(db.select(SourceRecord.pmcid).where(SourceRecord.dataset == "MedCaseReasoning")))
    if set(active) != known or set(active) & excluded_pmcids("MedCaseReasoning"):
        raise ValueError("O plano e o inventário ativo do banco não coincidem.")
    if pmcids is not None and (not isinstance(pmcids, (list, tuple, set))
            or any(not isinstance(p, str) or not re.fullmatch(r"PMC[1-9][0-9]*", p) for p in pmcids)
            or len(pmcids) != len(set(pmcids)) or not set(pmcids) <= known):
        raise ValueError("A seleção contém fontes inválidas, repetidas ou fora do plano ativo.")
    selected = list(active) if pmcids is None else sorted(pmcids)
    sources = {s.pmcid: s for s in db.session.scalars(db.select(SourceRecord).where(
        SourceRecord.dataset == "MedCaseReasoning", SourceRecord.pmcid.in_(selected)))}
    def completed_count():
        return db.session.scalar(db.select(db.func.count(SourceRecord.id)).join(ClinicalCase,
            SourceRecord.clinical_case_id == ClinicalCase.id).where(
                SourceRecord.dataset == "MedCaseReasoning", SourceRecord.translated.is_(True))) or 0
    completed_before = completed_count()
    prepared, counts = [], Counter()
    for pmcid in selected:
        source = sources[pmcid]
        existing = db.session.scalar(db.select(ClinicalCase).where(ClinicalCase.slug == "mcr-" + pmcid.lower()))
        if existing is not None:
            data, path = validate_case(existing.data), None
            preserved = processed / "drafts" / (pmcid + ".json")
            if preserved.exists() and data["reviewStatus"] != "reviewed" and _without_authorization(load_case(preserved)) != _without_authorization(data):
                raise ValueError("DB e rascunho legado divergem; nenhuma revisão será sobrescrita: " + pmcid)
            counts["existingPreserved"] += 1
        else:
            old = processed / "drafts" / (pmcid + ".json")
            path = old if old.is_file() else output / "cases" / (pmcid + ".json")
            if ready_only and not path.exists() and not path.is_symlink():
                counts["awaitingCase"] += 1
                continue
            data = load_case(path)
            counts["preparedToInsert"] += 1
        _source_identity(data, source)
        _checkpoint(processed / "records" / (pmcid + ".json"), pmcid, source)
        if source.clinical_case_id is not None and (existing is None or source.clinical_case_id != existing.id):
            raise ValueError("A fonte já está vinculada a outra ficha: " + pmcid)
        if data["reviewStatus"] != "reviewed" and not is_nonhuman_case(data):
            authorize_provisional_use(data, authorization_reference)  # Validate acceptance before any write.
        if is_nonhuman_case(data):
            counts["nonhumanPrivate"] += 1
        prepared.append((pmcid, source.id, existing.id if existing else None, path, digest(data)))
        if is_provisionally_accepted(data): counts["alreadyAuthorized"] += 1
    report = {"active": len(active), "expected": len(active), "selected": len(selected), "ready": len(prepared),
              "completedBefore": completed_before, "completed": completed_before,
              "pending": len(active) - completed_before, "batchCompleted": 0, "readyOnly": ready_only,
              "dryRun": not apply, **counts, "inserted": 0, "updated": 0, "unchanged": 0,
              "clinicalReviewDeferred": True, "providerCalls": 0}
    report.setdefault("nonhumanPrivate", 0)
    report["preflightCasesSha256"] = digest([{ "pmcid": item[0], "caseSha256": item[4]} for item in prepared])
    db.session.rollback()
    if not apply or not prepared:
        return report
    if ready_only or operation_id is not None:
        backup, operation_id, reused = _operation_backup(output, backup_dir or output / "backups",
            active, authorization_reference, operation_id)
        report.update(operationId=operation_id, backupReused=reused)
    else:
        backup = _backup_sqlite(backup_dir or output / "backups")
    report["backup"] = str(backup)
    for index, (pmcid, source_id, case_id, path, planned_hash) in enumerate(prepared, 1):
        try:
            source = db.session.get(SourceRecord, source_id)
            if source is None or pmcid in excluded_pmcids("MedCaseReasoning"):
                raise ValueError("Inventário mudou durante a incorporação: " + pmcid)
            existing = db.session.get(ClinicalCase, case_id) if case_id else db.session.scalar(
                db.select(ClinicalCase).where(ClinicalCase.slug == "mcr-" + pmcid.lower()))
            data = validate_case(existing.data) if existing else load_case(path)
            if digest(data) != planned_hash:
                raise ValueError("Ficha alterada após a validação inicial: " + pmcid)
            _source_identity(data, source)
            publish = not is_nonhuman_case(data)
            if publish and data["reviewStatus"] != "reviewed" and not is_provisionally_accepted(data):
                data = authorize_provisional_use(data, authorization_reference)
            if existing is None:
                existing = ClinicalCase(slug=data["caseId"], title=data["caseTitle"], specialty=data["specialty"],
                    case_type=case_type_for(data), difficulty=data["difficulty"], summary=data["summary"],
                    data=data, is_generated=False, is_published=publish)
                db.session.add(existing)
                db.session.flush()
                report["inserted"] += 1
                changed = True
            else:
                changed = existing.data != data or existing.is_published != publish
                if existing.data != data:
                    existing.data = data
                existing.is_published = publish
            if source.clinical_case_id not in {None, existing.id}:
                raise ValueError("Vínculo da fonte mudou durante a incorporação: " + pmcid)
            updates = {"clinical_case_id": existing.id, "translated": True, "import_status": "draft",
                       "specialty": existing.specialty, "license_code": data["provenance"]["license"]["code"], "last_error": None}
            checkpoint = _persist_processed(processed, data, source)
            if checkpoint:
                updates.update(checkpoint_sha256=checkpoint[0], checkpoint_updated_at=checkpoint[1], attempts=checkpoint[2])
            for key, value in updates.items():
                if getattr(source, key) != value:
                    setattr(source, key, value)
                    changed = True
            if source.provenance is None:
                source.provenance = {key: value for key, value in data["provenance"].items()
                                     if key in _BASE_PROVENANCE or key == "reuseBasis"}
                changed = True
            # Never manufacture verified/checkpoint hashes from the translated case.
            db.session.commit()
            if path is None: report["updated" if changed else "unchanged"] += 1
            report["batchCompleted"] = index
            if on_progress and (index % 100 == 0 or index == len(prepared)):
                report["completed"] = completed_count()
                report["pending"] = report["expected"] - report["completed"]
                on_progress(dict(report))
            db.session.remove()
        except Exception:
            db.session.rollback()
            raise
    report["completed"] = completed_count()
    report["pending"] = report["expected"] - report["completed"]
    db.session.rollback()
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "instance/local-preparation/corpus")
    parser.add_argument("--processed", type=Path, default=ROOT / "instance/imports/MedCaseReasoning/processed")
    parser.add_argument("--backup-dir", type=Path)
    parser.add_argument("--authorization-reference", required=True)
    parser.add_argument("--apply", action="store_true", help="Cria backup e incorpora; sem esta flag apenas confere.")
    parser.add_argument("--ready-only", action="store_true", help="Incorpora apenas fichas prontas; as demais permanecem pendentes.")
    parser.add_argument("--pmcid", action="append", dest="pmcids", help="Seleciona uma fonte ativa; opção repetível.")
    parser.add_argument("--operation-id", help="Identificador estável do lote incremental, para reutilizar seu backup inicial.")
    args = parser.parse_args(argv)
    from medlab import create_app
    application = create_app()
    with application.app_context():
        report = integrate_corpus(args.output, args.processed, apply=args.apply,
            authorization_reference=args.authorization_reference, backup_dir=args.backup_dir,
            ready_only=args.ready_only, pmcids=args.pmcids, operation_id=args.operation_id,
            on_progress=lambda value: print(json.dumps(value, ensure_ascii=False), flush=True))
    print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
