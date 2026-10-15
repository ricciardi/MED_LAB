"""Resumable, private-file MedCaseReasoning import; never publishes a case.

PMC distribution contract: https://pmc.ncbi.nlm.nih.gov/tools/pmcaws/
https://pmc-oa-opendata.s3.amazonaws.com/README.txt
Run from a CLI, not a long Flask request. No workers, Redis or AWS SDK required.
"""
from collections import Counter
from contextlib import contextmanager
import csv
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import parse_qs, urlparse
import xml.etree.ElementTree as ET

import requests

from .ai import AIError, AIService
from .case_schema import CaseValidationError, validate_case
from .catalog import SPECIALTIES

DATASET_REVISION = "469a5365bc534b5b2b9cfbc52b2ef2a10f43a339"
DATASET_URL = "https://huggingface.co/datasets/zou-lab/MedCaseReasoning"
PMC_BUCKET = "https://pmc-oa-opendata.s3.amazonaws.com"
_PMCID = re.compile(r"PMC[1-9][0-9]{0,11}\Z")
_RETRYABLE = {"source_error", "classification_error", "adaptation_error"}


class ImportError(RuntimeError):
    """Safe operational failure; never includes provider body or credentials."""


class SourceRejected(ImportError):
    """A source is not eligible under this import's CC BY / CC0 policy."""


def _now():
    return datetime.now(timezone.utc).isoformat()


def _hash_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_bytes(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _atomic_json(path, value):
    _atomic_bytes(path, (json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode("utf-8"))


def _json_file(path):
    try:
        with Path(path).open(encoding="utf-8") as handle:
            return json.load(handle, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, OSError):
        raise ImportError("Um arquivo da importação está inválido; restaure o arquivo ou use outro diretório de trabalho.") from None


def _xml(raw):
    # JATS uses an external DOCTYPE. ElementTree does not fetch that DTD;
    # explicit entities/internal subsets are refused before parsing.
    if b"<!ENTITY" in raw.upper() or re.search(br"<!DOCTYPE[^>]*\[", raw, re.I):
        raise SourceRejected("XML da fonte contém uma estrutura não permitida.")
    try:
        return ET.fromstring(raw)
    except ET.ParseError:
        raise ImportError("O PMC retornou XML inválido; tente novamente em outro lote.") from None


def _xml_text(element):
    return " ".join(" ".join(element.itertext()).split()) if element is not None else ""


class PMCClient:
    """Uses only the public PMC S3 endpoint and its versioned metadata/XML."""
    def __init__(self, cache_dir, session=None, timeout=20):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.session = session or requests.Session()
        self.timeout = min(45, max(1, float(timeout)))

    def _get(self, url, maximum=12000000):
        if not url.startswith(PMC_BUCKET + "/"):
            raise ImportError("Endereço de origem não permitido.")
        try:
            with self.session.get(url, timeout=(min(5, self.timeout), self.timeout), allow_redirects=False, stream=True) as response:
                if response.status_code != 200:
                    raise ImportError("Não foi possível consultar a versão do artigo no PMC; tente novamente em outro lote.")
                data = bytearray()
                for chunk in response.iter_content(65536):
                    data.extend(chunk)
                    if len(data) > maximum:
                        raise SourceRejected("A fonte ultrapassa o limite seguro de tamanho para esta importação.")
                return bytes(data)
        except requests.RequestException:
            raise ImportError("A consulta ao PMC falhou temporariamente; tente novamente em outro lote.") from None

    def verify(self, pmcid, refresh=False):
        if not isinstance(pmcid, str) or not _PMCID.fullmatch(pmcid):
            raise SourceRejected("O registro não possui um PMCID válido.")
        cached = self.cache_dir / (pmcid + ".verified.json")
        if cached.exists() and not refresh:
            value = _json_file(cached)
            if isinstance(value, dict) and value.get("provenance", {}).get("pmcid") == pmcid and value.get("articleText"):
                provenance = value["provenance"]
                snapshot = self.cache_dir / f"{pmcid}.{provenance.get('pmcVersion')}.{provenance.get('xmlMd5')}.xml"
                metadata_snapshot = self.cache_dir / f"{pmcid}.{provenance.get('pmcVersion')}.{provenance.get('metadataSha256')}.metadata.json"
                if snapshot.exists() and metadata_snapshot.exists():
                    if _hash_file(snapshot) != provenance.get("xmlSha256") or _hash_file(metadata_snapshot) != provenance.get("metadataSha256"):
                        raise ImportError("O snapshot do artigo foi alterado; revalide a fonte com refresh_sources.")
                    return value
                # Older caches are upgraded by rechecking the current source.
            else:
                raise ImportError("O cache verificado do artigo está inválido.")
        listing = _xml(self._get(PMC_BUCKET + "/?list-type=2&prefix=" + pmcid + ".&delimiter=/", 1000000))
        versions = []
        for item in listing.iter():
            if item.tag.rsplit("}", 1)[-1] == "Prefix" and item.text:
                match = re.fullmatch(re.escape(pmcid) + r"\.([1-9][0-9]*)/", item.text)
                if match:
                    versions.append(int(match.group(1)))
        if not versions or len(versions) > 30:
            raise SourceRejected("O artigo não possui uma versão distribuída elegível no PMC Cloud.")
        candidates = []
        for version in sorted(set(versions), reverse=True):
            metadata_raw = self._get(PMC_BUCKET + f"/metadata/{pmcid}.{version}.json", 1000000)
            try:
                metadata = json.loads(metadata_raw)
            except ValueError:
                raise ImportError("Os metadados do PMC estão inválidos.") from None
            _atomic_json(self.cache_dir / f"{pmcid}.{version}.metadata.json", metadata)
            _atomic_bytes(self.cache_dir / f"{pmcid}.{version}.{hashlib.sha256(metadata_raw).hexdigest()}.metadata.json", metadata_raw)
            if not isinstance(metadata, dict) or metadata.get("pmcid") != pmcid or metadata.get("version") != version:
                raise ImportError("A identidade da versão do artigo não confere.")
            # Do not silently choose another version of a retracted article.
            if metadata.get("is_retracted") is True:
                raise SourceRejected("Artigo retratado: não elegível para a biblioteca.")
            if metadata.get("is_retracted") is not False or metadata.get("is_manuscript") is not False:
                continue
            if metadata.get("license_code") in {"CC BY", "CC0"}:
                candidates.append((version, metadata, metadata_raw))
        if not candidates:
            raise SourceRejected("A fonte não confirma licença CC BY ou CC0 para uma versão publicada sem retratação.")
        last_rejection = None
        for version, metadata, metadata_raw in candidates:
            try:
                value = self._verify_version(pmcid, version, metadata, metadata_raw)
                _atomic_json(cached, value)
                return value
            except SourceRejected as exc:
                last_rejection = exc
        raise last_rejection

    def _verify_version(self, pmcid, version, metadata, metadata_raw):
        location = urlparse(str(metadata.get("xml_url", "")))
        expected = f"/{pmcid}.{version}/{pmcid}.{version}.xml"
        checksum = parse_qs(location.query).get("md5", [""])[0]
        if location.scheme != "s3" or location.netloc != "pmc-oa-opendata" or location.path != expected or not re.fullmatch(r"[a-fA-F0-9]{32}", checksum):
            raise SourceRejected("O XML do artigo não possui origem e checksum verificáveis.")
        raw = self._get(PMC_BUCKET + expected)
        if hashlib.md5(raw).hexdigest() != checksum.lower():
            raise ImportError("O checksum do XML mudou; atualize os metadados e tente novamente.")
        _atomic_bytes(self.cache_dir / f"{pmcid}.{version}.{checksum.lower()}.xml", raw)
        article = _xml(raw)
        article_meta = article.find("./front/article-meta")
        if article_meta is None:
            raise SourceRejected("O artigo não contém os metadados JATS necessários.")
        for item in article_meta.findall("article-id"):
            if item.get("pub-id-type") in {"pmc", "pmcid"} and (item.text or "").removeprefix("PMC") != pmcid[3:]:
                raise SourceRejected("O identificador do XML não corresponde ao artigo solicitado.")
        licenses = article_meta.findall("./permissions/license")
        urls, statements = set(), []
        for license_node in licenses:
            statements.append(_xml_text(license_node))
            for item in license_node.iter():
                links = [value for key, value in item.attrib.items() if key.rsplit("}", 1)[-1] == "href"]
                if item.tag.rsplit("}", 1)[-1] == "license_ref" and item.text:
                    links.append(item.text.strip())
                for value in links:
                    if "creativecommons.org/" in value:
                        parsed = urlparse(value)
                        if parsed.hostname not in {"creativecommons.org", "www.creativecommons.org"}:
                            continue
                        path = parsed.path.rstrip("/")
                        path = re.sub(r"/(?:legalcode|deed)(?:\.[a-zA-Z_-]+)?$", "", path)
                        urls.add("https://creativecommons.org" + path + "/")
        allowed = re.compile(r"https://creativecommons.org/(licenses/by/(?:1\.0|2\.0|2\.5|3\.0|4\.0)|publicdomain/zero/1\.0)/\Z")
        if not urls or not all(allowed.fullmatch(url) for url in urls):
            raise SourceRejected("A licença exata no XML não confirma exclusivamente CC BY ou CC0.")
        statement = " ".join(statements)
        candidates = {url for url in urls if ("/zero/" in url) == (metadata["license_code"] == "CC0")}
        # BMC's article license often includes an additional CC0 waiver for data.
        # Keep the article's CC BY license and the complete declaration for attribution.
        if len(candidates) != 1:
            raise SourceRejected("A versão da licença do artigo é ambígua no XML.")
        license_url = next(iter(candidates))
        extra = urls - candidates
        if extra and not (metadata["license_code"] == "CC BY" and extra == {"https://creativecommons.org/publicdomain/zero/1.0/"}
                          and re.search(r"waiver.*applies to the data", statement, re.I)):
            raise SourceRejected("O XML mistura licenças sem distinguir a licença do artigo.")
        code = "CC0" if "/zero/" in license_url else "CC BY"
        if code != metadata["license_code"] or not statement:
            raise SourceRejected("A licença do XML diverge dos metadados ou não traz declaração verificável.")
        authors = []
        for person in article_meta.findall("./contrib-group/contrib"):
            if person.get("contrib-type") == "author":
                name = person.find("name")
                label = " ".join(filter(None, (name.findtext("given-names"), name.findtext("surname")))) if name is not None else _xml_text(person.find("collab"))
                if label:
                    authors.append(label)
        body = article.find("body")
        article_text = " ".join(filter(None, [_xml_text(article_meta.find("abstract")), _xml_text(body)]))
        if len(article_text) < 100:
            raise SourceRejected("O artigo não fornece texto clínico suficiente para adaptação.")
        provenance = {
            "dataset": "MedCaseReasoning", "datasetRevision": DATASET_REVISION,
            "sourceRecordId": pmcid, "pmcid": pmcid, "doi": metadata.get("doi"),
            "articleUrl": f"https://pmc.ncbi.nlm.nih.gov/articles/{pmcid}/",
            "articleTitle": metadata.get("title") or _xml_text(article_meta.find("./title-group/article-title")),
            "citation": metadata.get("citation") or "Citação não disponibilizada pelo PMC.",
            "authors": list(dict.fromkeys(authors)),
            "license": {"code": code, "url": license_url, "version": license_url.rstrip("/").rsplit("/", 1)[-1], "text": statement},
            "pmcVersion": version, "xmlSha256": hashlib.sha256(raw).hexdigest(), "xmlMd5": checksum.lower(),
            "metadataSha256": hashlib.sha256(metadata_raw).hexdigest(), "checkedAt": _now(),
        }
        return {"provenance": provenance, "articleText": article_text}


def iter_source(path):
    """Stream CSV with quoted multiline fields, or JSON Lines with one object per row."""
    path = Path(path)
    try:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            if path.suffix.lower() in {".jsonl", ".ndjson"}:
                for line in handle:
                    if line.strip():
                        value = json.loads(line)
                        if not isinstance(value, dict):
                            raise ValueError
                        yield value
            else:
                csv.field_size_limit(16 * 1024 * 1024)
                reader = csv.DictReader(handle)
                if not {"pmcid", "case_prompt", "final_diagnosis"} <= set(reader.fieldnames or []):
                    raise ValueError
                yield from reader
    except (OSError, UnicodeError, ValueError, csv.Error):
        raise ImportError("A fonte deve ser um CSV ou JSONL válido do MedCaseReasoning, com PMCID, apresentação e diagnóstico.") from None


class CaseImporter:
    def __init__(self, config, work_dir, ai_service=None, pmc_client=None):
        self.config = config
        self.work_dir = Path(work_dir).resolve()
        self.work_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Protect custom work directories too; unverified raw data are never bundled.
        ignore = self.work_dir / ".gitignore"
        if not ignore.exists():
            ignore.write_text("*\n", encoding="utf-8")
        try:
            self.ai = ai_service or AIService(config, request_timeout=config.get("AI_IMPORT_TIMEOUT", 120))
        except AIError as exc:
            raise ImportError(str(exc)) from None
        self.pmc = pmc_client or PMCClient(self.work_dir / "pmc-cache")
        for name in ("records", "drafts"):
            (self.work_dir / name).mkdir(exist_ok=True, mode=0o700)

    @contextmanager
    def _lock(self):
        with (self.work_dir / ".import.lock").open("a") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ImportError("Já existe uma importação usando este diretório. Aguarde o lote terminar.") from None
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def _record(self, pmcid):
        path = self.work_dir / "records" / (pmcid + ".json")
        return _json_file(path) if path.exists() else {"pmcid": pmcid, "status": "pending", "specialty": None, "attempts": 0}

    def _save_record(self, record):
        record["updatedAt"] = _now()
        _atomic_json(self.work_dir / "records" / (record["pmcid"] + ".json"), record)

    def run(self, source_path, specialties=None, target_per_specialty=300, limit=50,
            adapt=False, retry_errors=False, refresh_sources=False, on_progress=None):
        """Process at most `limit` records requiring work; cache hits do not consume it.

        Each record is checkpointed atomically. Errors require retry_errors=True;
        source/license changes require refresh_sources=True. No automatic retries.
        `adapt=False` never calls AI. Without a key, adapt=True still verifies sources
        and reports translation blocked, leaving specialty explicitly unclassified.
        """
        selected = tuple(specialties or SPECIALTIES)
        if not selected or len(set(selected)) != len(selected) or any(s not in SPECIALTIES for s in selected):
            raise ImportError("Escolha especialidades válidas do catálogo oficial.")
        if isinstance(target_per_specialty, bool) or not isinstance(target_per_specialty, int) or not 1 <= target_per_specialty <= 10000:
            raise ImportError("A meta por especialidade deve estar entre 1 e 10.000.")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100000:
            raise ImportError("O limite do lote deve estar entre 1 e 100.000 registros.")
        source_path = Path(source_path).resolve()
        if not source_path.is_file():
            raise ImportError("O arquivo de origem não foi encontrado.")
        with self._lock():
            fingerprint = _hash_file(source_path)
            manifest_path = self.work_dir / "manifest.json"
            if manifest_path.exists():
                manifest = _json_file(manifest_path)
                if manifest.get("sourceSha256") != fingerprint:
                    raise ImportError("A fonte mudou. Use outro diretório de trabalho para preservar a proveniência e a retomada.")
            else:
                manifest = {"schemaVersion": 1, "dataset": "MedCaseReasoning", "datasetRevision": DATASET_REVISION,
                            "sourcePath": str(source_path), "sourceSha256": fingerprint, "createdAt": _now()}
                _atomic_json(manifest_path, manifest)
            ready = Counter(case["specialty"] for case in self.iter_drafts())
            available = bool(str(self.config.get("DEEPSEEK_API_KEY") or "").strip()) and not self.ai.is_demo
            processed, seen, duplicates, invalid_ids, total = 0, set(), 0, 0, 0
            for source in iter_source(source_path):
                total += 1
                pmcid = str(source.get("pmcid", "")).strip()
                if not _PMCID.fullmatch(pmcid):
                    invalid_ids += 1
                    continue
                if pmcid in seen:
                    duplicates += 1
                    continue
                seen.add(pmcid)
                if processed >= limit:
                    continue
                record = self._record(pmcid)
                status = record["status"]
                if status == "draft":
                    continue
                if status in _RETRYABLE and not retry_errors:
                    continue
                if status == "license_rejected" and not refresh_sources:
                    continue
                if status == "verified" and not refresh_sources and (not adapt or not available):
                    continue
                if status == "quota_deferred" and not refresh_sources:
                    if not adapt or not available or record.get("specialty") not in selected or ready[record["specialty"]] >= target_per_specialty:
                        continue
                processed += 1
                record["attempts"] += 1
                record.pop("error", None)
                stage = "source"
                try:
                    verified = self.pmc.verify(pmcid, refresh=refresh_sources)
                    record.update({"status": "verified", "license": verified["provenance"]["license"]["code"],
                                   "sourceCheckedAt": verified["provenance"]["checkedAt"]})
                    if adapt and available:
                        stage = "classification"
                        if not record.get("specialty"):
                            classified = self.ai.classify_case(source, SPECIALTIES)
                            if classified.get("specialty") not in SPECIALTIES:
                                raise AIError("A especialidade classificada não pertence ao catálogo.")
                            record.update({"specialty": classified["specialty"], "classificationReason": classified["reason"]})
                        specialty = record["specialty"]
                        if specialty not in selected or ready[specialty] >= target_per_specialty:
                            record["status"] = "quota_deferred"
                        else:
                            stage = "adaptation"
                            case = validate_case(self.ai.adapt_case({**source, "articleText": verified["articleText"]}, verified["provenance"], specialty))
                            if case["catalogType"] != "published_case" or case["reviewStatus"] != "review_required" or case["specialty"] != specialty or case["provenance"]["pmcid"] != pmcid:
                                raise CaseValidationError("Rascunho incompatível com a origem ou a revisão obrigatória.")
                            if any(case["provenance"].get(key) != value for key, value in verified["provenance"].items()):
                                raise CaseValidationError("A adaptação alterou a proveniência verificada.")
                            _atomic_json(self.work_dir / "drafts" / (pmcid + ".json"), case)
                            record["status"] = "draft"
                            record["caseId"] = case["caseId"]
                            ready[specialty] += 1
                except SourceRejected as exc:
                    record.update({"status": "license_rejected", "error": str(exc)})
                except (AIError, ImportError, CaseValidationError) as exc:
                    record.update({"status": {"source": "source_error", "classification": "classification_error", "adaptation": "adaptation_error"}[stage], "error": str(exc)})
                self._save_record(record)
                if on_progress:
                    on_progress({key: record.get(key) for key in ("pmcid", "status", "specialty", "error")})
            manifest.update({"sourceRecords": total, "uniquePmcids": len(seen), "duplicatePmcids": duplicates,
                             "invalidPmcids": invalid_ids, "lastRunAt": _now()})
            _atomic_json(manifest_path, manifest)
            report = self.report(selected, target_per_specialty)
            report.update({"processedThisRun": processed, "adaptationRequested": bool(adapt), "aiAvailable": available,
                           "adaptationBlocked": bool(adapt and not available)})
            _atomic_json(self.work_dir / "report.json", report)
            return report

    def iter_drafts(self):
        """Only valid, source-attributed drafts; caller controls DB insertion/review."""
        for path in sorted((self.work_dir / "drafts").glob("PMC*.json")):
            try:
                case = validate_case(_json_file(path))
            except CaseValidationError:
                raise ImportError("Um rascunho salvo falhou na validação; revise-o antes de importar para o banco.") from None
            if case["catalogType"] != "published_case" or case["reviewStatus"] != "review_required" or path.stem != case["provenance"]["pmcid"]:
                raise ImportError("Um rascunho não corresponde à fonte ou à revisão obrigatória.")
            yield case

    def report(self, specialties=None, target_per_specialty=300):
        selected = tuple(specialties or SPECIALTIES)
        manifest_path = self.work_dir / "manifest.json"
        manifest = _json_file(manifest_path) if manifest_path.exists() else {}
        records = [_json_file(path) for path in (self.work_dir / "records").glob("PMC*.json")]
        counts = Counter(record["status"] for record in records)
        drafts = Counter(case["specialty"] for case in self.iter_drafts())
        classified = Counter(record["specialty"] for record in records if record.get("specialty"))
        eligible = sum(record.get("license") in {"CC BY", "CC0"} and record["status"] not in {"license_rejected", "source_error"} for record in records)
        source_total = manifest.get("uniquePmcids", 0)
        return {
            "dataset": "MedCaseReasoning", "datasetRevision": manifest.get("datasetRevision", DATASET_REVISION),
            "sourceSha256": manifest.get("sourceSha256"), "sourceRecords": manifest.get("sourceRecords", 0),
            "uniquePmcids": source_total, "duplicatePmcids": manifest.get("duplicatePmcids", 0),
            "invalidPmcids": manifest.get("invalidPmcids", 0), "sourceVerified": eligible,
            "licenseRejected": counts["license_rejected"], "pendingVerification": max(0, source_total - len(records)),
            "unclassified": sum(not record.get("specialty") and record["status"] == "verified" for record in records),
            "adaptedDrafts": sum(drafts.values()), "reviewed": 0, "published": None,
            "statusCounts": dict(counts), "targetTotal": len(selected) * target_per_specialty,
            "minimumSourceShortfall": max(0, len(selected) * target_per_specialty - source_total),
            "specialties": {specialty: {"target": target_per_specialty, "classified": classified[specialty],
                                        "drafts": drafts[specialty], "remainingDrafts": max(0, target_per_specialty - drafts[specialty])} for specialty in selected},
            "notes": ["Registros brutos não equivalem a casos traduzidos, revisados ou publicados.",
                      "Especialidade só é atribuída após classificação por IA; cada relato tem uma especialidade principal.",
                      "Rascunhos passam por validação estrutural e auditoria por IA, sem revisão clínica humana.",
                      "Publicado não é contado por este importador; consulte o banco da aplicação.",
                      "Snapshots de fonte podem mudar; revalide origem, licença e retratação antes da publicação."],
        }
