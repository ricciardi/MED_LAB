"""Private, idempotent source inventory. All entry points require app_context.

No network requests, model calls, case creation or publication occur here.
Registration deliberately stores metadata, not article/prompt/reasoning text.
JSONL portability restores inventory only; local checkpoints must be rechecked
before a restored record is counted as verified or translated.
"""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

from flask import current_app
from sqlalchemy import and_, false, func, or_
from sqlalchemy.exc import SQLAlchemyError

from .case_schema import CaseValidationError, source_license_allowed, validate_case
from .catalog import SPECIALTIES
from .extensions import db
from .importer import DATASET_REVISION, DATASET_URL, ImportError as SourceImportError, iter_source
from .models import ClinicalCase, SourceExclusion, SourceRecord

DEFAULT_DATASET = "MedCaseReasoning"
STATUSES = frozenset({"registered", "pending", "verified", "license_rejected", "source_error",
                      "classification_error", "adaptation_error", "quota_deferred", "draft"})
_ELIGIBLE = {"verified", "classification_error", "adaptation_error", "quota_deferred", "draft"}
_PMCID = re.compile(r"PMC[1-9][0-9]{0,11}\Z")
_HASH = re.compile(r"[a-f0-9]{64}\Z")
_PORTABLE = ("dataset", "pmcid", "dataset_revision", "dataset_url", "source_file_sha256", "record_sha256",
             "source_row_number", "original_title", "original_diagnosis", "original_metadata")
_EXCLUSION_FIELDS = {"type", "dataset", "pmcid", "reason", "created_at"}
_METADATA = {"journal", "article_link", "publication_date", "split", "index"}
_BASE_PROVENANCE = {"dataset", "datasetRevision", "sourceRecordId", "pmcid", "doi", "articleUrl",
                    "articleTitle", "citation", "authors", "license", "pmcVersion", "xmlSha256",
                    "xmlMd5", "metadataSha256", "checkedAt"}


class SourceRegistryError(RuntimeError):
    """Safe administrative error, without source bodies or credentials."""


def _hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json(path, maximum=4 * 1024 * 1024):
    path = Path(path)
    if path.stat().st_size > maximum:
        raise ValueError("oversized metadata")
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    if not isinstance(value, dict):
        raise ValueError("invalid object")
    return value


def _dataset(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", value):
        raise SourceRegistryError("O identificador da base é inválido.")
    return value


def _batch_size(value):
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 2000:
        raise SourceRegistryError("O tamanho do lote deve estar entre 1 e 2.000.")
    return value


def _text(value, maximum, empty=True):
    if not isinstance(value, str) or "\x00" in value or len(value.encode("utf-8")) > maximum or (not empty and not value.strip()):
        raise ValueError("invalid metadata field")
    return value


def _validate_portable(value):
    if not isinstance(value, dict) or set(value) != set(_PORTABLE):
        raise ValueError("invalid metadata fields")
    _dataset(value["dataset"])
    if not isinstance(value["pmcid"], str) or not _PMCID.fullmatch(value["pmcid"]):
        raise ValueError("invalid identity")
    _text(value["dataset_revision"], 64, False)
    if value["dataset_url"] != DATASET_URL:
        raise ValueError("invalid dataset origin")
    for field in ("source_file_sha256", "record_sha256"):
        if not isinstance(value[field], str) or not _HASH.fullmatch(value[field]):
            raise ValueError("invalid hash")
    number = value["source_row_number"]
    if isinstance(number, bool) or not isinstance(number, int) or not 1 <= number <= 2147483647:
        raise ValueError("invalid source row")
    _text(value["original_title"], 60000)
    _text(value["original_diagnosis"], 60000, False)
    metadata = value["original_metadata"]
    if not isinstance(metadata, dict) or set(metadata) != _METADATA:
        raise ValueError("invalid original metadata")
    for field in _METADATA:
        _text(metadata[field], 10000)
    return value



def excluded_pmcids(dataset=DEFAULT_DATASET):
    """IDs blocked from registration and adaptation, without source contents."""
    dataset = _dataset(dataset)
    return set(db.session.scalars(db.select(SourceExclusion.pmcid).where(SourceExclusion.dataset == dataset)))


def _linked_case_id(source):
    if source.clinical_case_id is not None:
        return source.clinical_case_id
    # Protect an older imported case whose registry link was never synchronized.
    return db.session.scalar(db.select(ClinicalCase.id).where(ClinicalCase.slug == "mcr-" + source.pmcid.lower()))


def _exclude_record(source):
    """Caller commits deletion and its minimal exclusion in the same transaction."""
    if _linked_case_id(source) is not None:
        return False
    key = (source.dataset, source.pmcid)
    if db.session.get(SourceExclusion, key) is None:
        db.session.add(SourceExclusion(dataset=source.dataset, pmcid=source.pmcid, reason="source_rejected"))
    db.session.delete(source)
    return True


def prune_restricted_sources(*, dataset=DEFAULT_DATASET, batch_size=500, pmcids=None):
    """Remove rejected source metadata without deleting any linked case/history."""
    dataset, batch_size = _dataset(dataset), _batch_size(batch_size)
    selected = None
    if pmcids is not None:
        if isinstance(pmcids, str):
            raise SourceRegistryError("Informe uma lista de PMCIDs para excluir.")
        selected = list(dict.fromkeys(pmcids))
        if not selected or any(not isinstance(value, str) or not _PMCID.fullmatch(value) for value in selected):
            raise SourceRegistryError("A lista de PMCIDs para exclusão é inválida.")
    report = {"removed": 0, "skipped": 0}
    last_id = 0
    try:
        while True:
            query = db.select(SourceRecord).where(SourceRecord.dataset == dataset,
                SourceRecord.import_status == "license_rejected", SourceRecord.id > last_id)
            if selected is not None:
                query = query.where(SourceRecord.pmcid.in_(selected))
            records = db.session.scalars(query.order_by(SourceRecord.id).limit(batch_size)).all()
            if not records:
                break
            last_id = records[-1].id
            for source in records:
                report["removed" if _exclude_record(source) else "skipped"] += 1
            db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        raise SourceRegistryError("Não foi possível concluir a exclusão. Os lotes anteriores foram preservados; repita o comando para retomar.") from None
    report["excluded"] = len(excluded_pmcids(dataset))
    report["database"] = source_counts(dataset)
    return report


def _validate_exclusion(value):
    if not isinstance(value, dict) or set(value) != _EXCLUSION_FIELDS or value.get("type") != "exclusion":
        raise ValueError("invalid exclusion fields")
    _dataset(value["dataset"])
    if not isinstance(value["pmcid"], str) or not _PMCID.fullmatch(value["pmcid"]):
        raise ValueError("invalid exclusion identity")
    if value["reason"] != "source_rejected" or not isinstance(value["created_at"], str):
        raise ValueError("invalid exclusion reason")
    created = datetime.fromisoformat(value["created_at"])
    if created.tzinfo is None:
        raise ValueError("exclusion timestamp requires timezone")
    return {**value, "created_at": created.astimezone(timezone.utc).replace(tzinfo=None)}


def _validate_registry_header(header):
    if not isinstance(header, dict):
        raise ValueError("invalid metadata header")
    version = header.get("version")
    expected = {"format": "medlab-source-metadata", "version": version, "dataset": header.get("dataset"),
                "metadataOnly": True, "pipelineStateIncluded": False}
    if type(version) is not int or version not in {1, 2}:
        raise ValueError("invalid metadata version")
    if version == 2:
        expected["exclusionsIncluded"] = True
    if (header != expected or header.get("metadataOnly") is not True or header.get("pipelineStateIncluded") is not False
            or (version == 2 and header.get("exclusionsIncluded") is not True)):
        raise ValueError("invalid metadata header")
    _dataset(header["dataset"])
    return header


def _restore_exclusions(values, *, dataset, batch_size):
    report = {"new": 0, "unchanged": 0, "removed": 0, "skipped": 0, "invalid": 0}
    try:
        for index, value in enumerate(values, 1):
            try:
                value = _validate_exclusion(value)
                if value["dataset"] != dataset:
                    raise ValueError("mixed datasets")
            except (ValueError, TypeError, KeyError, SourceRegistryError):
                report["invalid"] += 1
                continue
            source = db.session.scalar(db.select(SourceRecord).where(SourceRecord.dataset == dataset, SourceRecord.pmcid == value["pmcid"]))
            linked = _linked_case_id(source) if source is not None else db.session.scalar(
                db.select(ClinicalCase.id).where(ClinicalCase.slug == "mcr-" + value["pmcid"].lower()))
            if linked is not None:
                report["skipped"] += 1
                continue
            existing = db.session.get(SourceExclusion, (dataset, value["pmcid"]))
            if existing is None:
                db.session.add(SourceExclusion(dataset=dataset, pmcid=value["pmcid"],
                    reason=value["reason"], created_at=value["created_at"]))
                report["new"] += 1
            else:
                report["unchanged"] += 1
            if source is not None:
                db.session.delete(source)
                report["removed"] += 1
            if index % batch_size == 0:
                db.session.commit()
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        raise SourceRegistryError("Não foi possível restaurar as exclusões. Os lotes anteriores foram preservados; repita o comando.") from None
    return report

def _insert_inventory(values, *, dataset, batch_size):
    existing = {row.pmcid: (row.record_sha256, row.dataset_revision) for row in db.session.execute(
        db.select(SourceRecord.pmcid, SourceRecord.record_sha256, SourceRecord.dataset_revision).where(SourceRecord.dataset == dataset)
    )}
    excluded = excluded_pmcids(dataset)
    report = dict(read=0, new=0, unchanged=0, duplicates=0, conflicts=0, invalid=0, excluded=0)
    seen, pending = set(), []
    try:
        for value in values:
            report["read"] += 1
            try:
                value = _validate_portable(value)
                if value["dataset"] != dataset:
                    raise ValueError("mixed datasets")
            except (ValueError, TypeError, SourceRegistryError):
                report["invalid"] += 1
                continue
            pmcid = value["pmcid"]
            if pmcid in seen:
                report["duplicates"] += 1
                continue
            seen.add(pmcid)
            if pmcid in excluded:
                report["excluded"] += 1
                continue
            if pmcid in existing:
                report["unchanged" if existing[pmcid] == (value["record_sha256"], value["dataset_revision"]) else "conflicts"] += 1
                continue
            pending.append(value)
            if len(pending) >= batch_size:
                db.session.execute(db.insert(SourceRecord), pending)
                db.session.commit()
                report["new"] += len(pending)
                pending.clear()
        if pending:
            db.session.execute(db.insert(SourceRecord), pending)
            db.session.commit()
            report["new"] += len(pending)
    except SQLAlchemyError:
        db.session.rollback()
        raise SourceRegistryError("Não foi possível registrar este lote no banco. Os lotes anteriores foram preservados; repita o comando para retomar.") from None
    report["database"] = source_counts(dataset)
    return report


def register_sources(source_path, *, dataset=DEFAULT_DATASET, batch_size=500):
    """Register every unique valid row; unchanged rows and completed work survive reruns."""
    dataset, batch_size = _dataset(dataset), _batch_size(batch_size)
    source_path = Path(source_path)
    try:
        fingerprint = _hash(source_path)

        def values():
            for number, row in enumerate(iter_source(source_path), 1):
                if not isinstance(row, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in row.items()):
                    yield {}
                    continue
                row_hash = hashlib.sha256(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
                yield {"dataset": dataset, "pmcid": row.get("pmcid", "").strip(), "dataset_revision": DATASET_REVISION,
                       "dataset_url": DATASET_URL, "source_file_sha256": fingerprint, "record_sha256": row_hash,
                       "source_row_number": number, "original_title": row.get("title", ""),
                       "original_diagnosis": row.get("final_diagnosis", ""),
                       "original_metadata": {**{key: row.get(key, "") for key in _METADATA - {"index"}},
                                             "index": row.get("", row.get("Unnamed: 0", ""))}}
        return _insert_inventory(values(), dataset=dataset, batch_size=batch_size)
    except (OSError, UnicodeError, SourceImportError):
        db.session.rollback()
        raise SourceRegistryError("A fonte privada não pôde ser lida. Use o CSV/JSONL original e repita o registro para retomar os lotes já salvos.") from None


def verified_source_filter():
    """SQL predicate shared by the report and private list's current-policy filter."""
    license_url = SourceRecord.provenance["license"]["url"].as_string()
    reuse_basis = func.coalesce(SourceRecord.provenance["reuseBasis"].as_string(), "licensed_adaptation")
    groups = db.session.execute(db.select(SourceRecord.license_code, license_url, reuse_basis)
        .where(SourceRecord.verified.is_(True)).distinct())
    eligible = [and_(SourceRecord.license_code == code, license_url == url, reuse_basis == basis)
        for code, url, basis in groups if source_license_allowed(
            {"license": {"code": code, "url": url}, "reuseBasis": basis},
            current_app.config.get("CASE_REUSE_SCOPE", "commercial"),
            current_app.config.get("CASE_ALLOW_CLINICAL_FACTS", False))]
    return and_(SourceRecord.verified.is_(True), or_(*eligible) if eligible else false())


def source_counts(dataset=DEFAULT_DATASET):
    """Last successfully synchronized stages, not continuous disk monitoring.

    Invalid newer checkpoints preserve prior state and are reported by sync.
    Drafts/published count actual linked database cases.
    """
    if dataset is not None:
        _dataset(dataset)
    filters = [] if dataset is None else [SourceRecord.dataset == dataset]
    def count(*conditions):
        return db.session.scalar(db.select(func.count(SourceRecord.id)).where(*filters, *conditions)) or 0
    def linked(published):
        return db.session.scalar(db.select(func.count(SourceRecord.id)).join(ClinicalCase, SourceRecord.clinical_case_id == ClinicalCase.id)
                                 .where(*filters, ClinicalCase.is_published.is_(published))) or 0
    return {"registered": count(), "verified": count(verified_source_filter()),
            "translated": count(SourceRecord.translated.is_(True)), "drafts": linked(False), "published": linked(True),
            "statusCounts": dict(db.session.execute(db.select(SourceRecord.import_status, func.count(SourceRecord.id))
                                .where(*filters).group_by(SourceRecord.import_status)).all())}


def _verified_provenance(work_dir, source):
    cache = work_dir / "pmc-cache"
    provenance = _json(cache / f"{source.pmcid}.verified.json", maximum=24 * 1024 * 1024)["provenance"]
    if not isinstance(provenance, dict) or set(provenance) not in (_BASE_PROVENANCE, _BASE_PROVENANCE | {"reuseBasis"}) or provenance.get("pmcid") != source.pmcid or provenance.get("dataset") != source.dataset or provenance.get("datasetRevision") != source.dataset_revision:
        raise ValueError("source provenance mismatch")
    version = provenance.get("pmcVersion")
    if isinstance(version, bool) or not isinstance(version, int) or not 1 <= version <= 100000:
        raise ValueError("invalid version")
    for key in ("xmlSha256", "metadataSha256"):
        if not isinstance(provenance.get(key), str) or not _HASH.fullmatch(provenance[key]):
            raise ValueError("invalid snapshot hash")
    if not isinstance(provenance.get("xmlMd5"), str) or not re.fullmatch(r"[a-f0-9]{32}", provenance["xmlMd5"]):
        raise ValueError("invalid snapshot checksum")
    xml = cache / f"{source.pmcid}.{version}.{provenance['xmlMd5']}.xml"
    metadata_path = cache / f"{source.pmcid}.{version}.{provenance['metadataSha256']}.metadata.json"
    if _hash(xml) != provenance["xmlSha256"] or _hash(metadata_path) != provenance["metadataSha256"]:
        raise ValueError("snapshot changed")
    metadata = _json(metadata_path)
    if not isinstance(provenance.get("license"), dict):
        raise ValueError("invalid source license")
    license_code = provenance["license"].get("code")
    if not source_license_allowed(provenance, current_app.config.get("CASE_REUSE_SCOPE", "commercial"),
            current_app.config.get("CASE_ALLOW_CLINICAL_FACTS", False)) or metadata.get("license_code") != license_code or metadata.get("pmcid") != source.pmcid or metadata.get("version") != version or metadata.get("is_retracted") is not False or metadata.get("is_manuscript") is not False:
        raise ValueError("invalid source license")
    # Store just attribution metadata, never the cached articleText field.
    return provenance


def sync_import_records(work_dir, *, dataset=DEFAULT_DATASET, batch_size=500, pmcids=None, include_counts=True):
    """Mirror valid local checkpoints, and link existing cases without changing them."""
    dataset, batch_size = _dataset(dataset), _batch_size(batch_size)
    work_dir = Path(work_dir)
    if pmcids is None:
        paths = sorted((work_dir / "records").glob("PMC*.json"))
    else:
        if isinstance(pmcids, str):
            raise SourceRegistryError("Informe uma lista de PMCIDs para sincronizar.")
        selected = list(dict.fromkeys(pmcids))
        if any(not isinstance(value, str) or not _PMCID.fullmatch(value) for value in selected):
            raise SourceRegistryError("A lista de PMCIDs contém um identificador inválido.")
        paths = [work_dir / "records" / f"{pmcid}.json" for pmcid in selected]
    report = dict(synced=0, unchanged=0, unregistered=0, invalid=0, stale=0, linked=0, removed=0, excluded=0, skipped=0)
    excluded = excluded_pmcids(dataset)
    try:
        manifest = _json(work_dir / "manifest.json")
        if manifest.get("dataset") != dataset or not isinstance(manifest.get("sourceSha256"), str) or not _HASH.fullmatch(manifest["sourceSha256"]):
            raise ValueError("invalid manifest")
    except (OSError, ValueError, KeyError, TypeError):
        raise SourceRegistryError("O manifesto da importação está ausente ou inválido. Registre e confira a fonte antes de sincronizar.") from None
    try:
        for index, path in enumerate(paths, 1):
            source = db.session.scalar(db.select(SourceRecord).where(SourceRecord.dataset == dataset, SourceRecord.pmcid == path.stem))
            if source is None:
                report["excluded" if path.stem in excluded else "unregistered"] += 1
                continue
            try:
                checkpoint = _json(path)
                status, pmcid = checkpoint["status"], checkpoint["pmcid"]
                if pmcid != source.pmcid or status not in STATUSES or manifest["sourceSha256"] != source.source_file_sha256 or manifest.get("datasetRevision") != source.dataset_revision:
                    raise ValueError("checkpoint identity mismatch")
                updated = datetime.fromisoformat(checkpoint["updatedAt"])
                if updated.tzinfo is None:
                    raise ValueError("checkpoint requires timezone")
                updated = updated.astimezone(timezone.utc).replace(tzinfo=None)
                checkpoint_hash = _hash(path)
                if source.checkpoint_updated_at and updated < source.checkpoint_updated_at:
                    report["stale"] += 1
                    continue
                if source.checkpoint_updated_at == updated and source.checkpoint_sha256 != checkpoint_hash:
                    raise ValueError("checkpoint changed without timestamp")
                attempts = checkpoint.get("attempts", 0)
                if isinstance(attempts, bool) or not isinstance(attempts, int) or not 0 <= attempts <= 2147483647:
                    raise ValueError("invalid attempts")
                specialty = checkpoint.get("specialty")
                if specialty is not None and specialty not in SPECIALTIES:
                    raise ValueError("invalid specialty")
                error = checkpoint.get("error")
                if error is not None:
                    _text(error, 60000)
                provenance = _verified_provenance(work_dir, source) if status in _ELIGIBLE else None
                if provenance and checkpoint.get("license") != provenance["license"]["code"]:
                    raise ValueError("checkpoint license mismatch")
                case, linked_id = None, source.clinical_case_id
                # Rejected checkpoints remain reviewable. Durable exclusions are
                # an explicit maintenance action, never an automatic sync side effect.
                if status == "license_rejected" and linked_id is None:
                    linked_id = _linked_case_id(source)
                if status == "draft":
                    case = validate_case(_json(work_dir / "drafts" / f"{pmcid}.json"))
                    if case["catalogType"] != "published_case" or case["reviewStatus"] != "review_required" or case["caseId"] != checkpoint.get("caseId") or case["specialty"] != specialty or any(case["provenance"].get(k) != v for k, v in provenance.items()):
                        raise ValueError("invalid adapted draft")
                    clinical = db.session.scalar(db.select(ClinicalCase).where(ClinicalCase.slug == case["caseId"]))
                    if clinical:
                        data = validate_case(clinical.data)
                        if data["catalogType"] != "published_case" or any(data["provenance"].get(k) != v for k, v in provenance.items()):
                            raise ValueError("case origin conflict")
                        linked_id = clinical.id
                changes = {"import_status": status, "verified": provenance is not None, "translated": case is not None,
                           "license_code": provenance["license"]["code"] if provenance else None,
                           "specialty": specialty, "attempts": attempts, "last_error": error, "provenance": provenance,
                           "checkpoint_sha256": checkpoint_hash, "checkpoint_updated_at": updated,
                           "clinical_case_id": linked_id}
                if any(getattr(source, key) != value for key, value in changes.items()):
                    if linked_id and source.clinical_case_id != linked_id:
                        report["linked"] += 1
                    for key, value in changes.items():
                        setattr(source, key, value)
                    report["synced"] += 1
                else:
                    report["unchanged"] += 1
            except (OSError, ValueError, KeyError, TypeError, CaseValidationError):
                report["invalid"] += 1
            if index % batch_size == 0:
                db.session.commit()
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        raise SourceRegistryError("A sincronização do banco falhou. Repita o comando para retomar os lotes preservados.") from None
    if include_counts:
        report["database"] = source_counts(dataset)
    return report


def export_source_metadata(path, *, dataset=DEFAULT_DATASET):
    """Write private metadata and minimal durable exclusions, never case contents."""
    dataset = _dataset(dataset)
    path = Path(path).resolve()
    if path.is_relative_to(Path(current_app.static_folder).resolve()):
        raise SourceRegistryError("Salve os metadados privados fora da pasta pública static.")
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor, temporary = tempfile.mkstemp(prefix=".source-metadata-", dir=path.parent)
        count, excluded_count = 0, 0
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"format": "medlab-source-metadata", "version": 2, "dataset": dataset,
                "metadataOnly": True, "pipelineStateIncluded": False, "exclusionsIncluded": True}) + "\n")
            exclusions = db.session.scalars(db.select(SourceExclusion).where(SourceExclusion.dataset == dataset)
                .order_by(SourceExclusion.pmcid)).yield_per(500)
            for exclusion in exclusions:
                handle.write(json.dumps({"type": "exclusion", "dataset": dataset, "pmcid": exclusion.pmcid,
                    "reason": exclusion.reason, "created_at": exclusion.created_at.replace(tzinfo=timezone.utc).isoformat()},
                    ensure_ascii=False, allow_nan=False) + "\n")
                excluded_count += 1
            records = db.session.scalars(db.select(SourceRecord).where(SourceRecord.dataset == dataset)
                .order_by(SourceRecord.id)).yield_per(500)
            for source in records:
                handle.write(json.dumps({key: getattr(source, key) for key in _PORTABLE}, ensure_ascii=False, allow_nan=False) + "\n")
                count += 1
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        return {"exported": count, "excluded": excluded_count, "sha256": _hash(path), "metadataOnly": True}
    except (OSError, ValueError):
        raise SourceRegistryError("Não foi possível exportar os metadados privados.") from None
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def import_source_metadata(path, *, batch_size=500):
    """Restore v1/v2 metadata; exclusions win regardless of line ordering."""
    batch_size = _batch_size(batch_size)
    try:
        with Path(path).open(encoding="utf-8") as handle, tempfile.TemporaryFile(mode="w+t", encoding="utf-8") as sources:
            header = _validate_registry_header(json.loads(handle.readline(100000)))
            dataset = header["dataset"]
            def exclusions():
                for line in handle:
                    try:
                        value = json.loads(line) if len(line) <= 200000 else {}
                    except ValueError:
                        value = {}
                    if header["version"] == 2 and isinstance(value, dict) and value.get("type") == "exclusion":
                        yield value
                    else:
                        sources.write(json.dumps(value, ensure_ascii=False) + "\n")
            restored_exclusions = _restore_exclusions(exclusions(), dataset=dataset, batch_size=batch_size)
            sources.seek(0)
            result = _insert_inventory((json.loads(line) for line in sources), dataset=dataset, batch_size=batch_size)
            result["invalid"] += restored_exclusions["invalid"]
            result["exclusions"] = restored_exclusions
            return result
    except (OSError, UnicodeError, ValueError):
        db.session.rollback()
        raise SourceRegistryError("O arquivo de metadados não possui formato de exportação válido.") from None


# CLI-friendly names; the format transfers inventory and exclusions, not cases.
export_source_registry = export_source_metadata
import_source_registry = import_source_metadata
