"""Resumable, private-file MedCaseReasoning import; never publishes a case.

PMC distribution contract: https://pmc.ncbi.nlm.nih.gov/tools/pmcaws/
https://pmc-oa-opendata.s3.amazonaws.com/README.txt
Run from a CLI, not a long Flask request. No external worker service, Redis or AWS SDK required.
"""
from collections import Counter, deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import threading
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

from .ai import AIError, AIInterruptedError, AIService, AIUnavailableError, USAGE_KEYS
from .case_schema import (CaseValidationError, FACTUAL_SOURCE_LICENSES, REUSE_POLICY_VERSION, allowed_license_codes,
                          license_code_from_url, license_obligations, license_version_from_url, source_license_allowed,
                          source_reuse_basis, validate_case)
from .catalog import SPECIALTIES

DATASET_REVISION = "469a5365bc534b5b2b9cfbc52b2ef2a10f43a339"
DATASET_URL = "https://huggingface.co/datasets/zou-lab/MedCaseReasoning"
PMC_BUCKET = "https://pmc-oa-opendata.s3.amazonaws.com"
_PMCID = re.compile(r"PMC[1-9][0-9]{0,11}\Z")
_RETRYABLE = {"source_error", "classification_error", "adaptation_error"}


class ImportError(RuntimeError):
    """Safe operational failure; never includes provider body or credentials."""


class SourceRejected(ImportError):
    """A source is not eligible under the configured reuse policy."""


class _AIStop:
    """Shared provider-wide circuit breaker; first failure determines the reason."""
    def __init__(self):
        self.event = threading.Event()
        self.lock = threading.Lock()
        self.code = None

    def trip(self, error):
        with self.lock:
            if not self.event.is_set():
                self.code = error.code
                self.event.set()

    def check(self):
        if self.event.is_set():
            raise AIInterruptedError(self.code)


def _usage_snapshot(service):
    snapshot = service.usage_snapshot() if callable(getattr(service, "usage_snapshot", None)) else {}
    return {key: snapshot.get(key, 0) if isinstance(snapshot, dict) and isinstance(snapshot.get(key, 0), int)
            and not isinstance(snapshot.get(key, 0), bool) else 0 for key in USAGE_KEYS}


def _add_usage(first, second):
    first = first if isinstance(first, dict) else {}
    second = second if isinstance(second, dict) else {}
    value = {key: max(0, int(first.get(key, 0))) + max(0, int(second.get(key, 0))) for key in USAGE_KEYS}
    return {**value, "usageComplete": value["requests"] == value["usageReports"],
            "cacheUsageComplete": value["requests"] == value["cacheUsageReports"]}


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


def _license_url(value):
    """Normalize publisher formatting only; the caller still checks exact CC terms."""
    # Some older JATS license_ref values contain an escaped closing XML tag.
    # Remove only that known suffix, never arbitrary content following a URL.
    value = re.sub(r"</url>[),.]*$", "", value.strip(), flags=re.I)
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in {"creativecommons.org", "www.creativecommons.org"} or parsed.username or parsed.password:
        return None
    path = parsed.path.rstrip("/").lower()
    path = re.sub(r"/(?:legalcode|deed)(?:\.[a-z_-]+)?$", "", path)
    return "https://creativecommons.org" + path + "/"


def _excluded_ids(values):
    if values is None:
        return frozenset()
    if not isinstance(values, (list, tuple, set, frozenset)) or any(not isinstance(value, str) or not _PMCID.fullmatch(value) for value in values):
        raise ImportError("A lista de exclusões deve conter apenas identificadores PMCID válidos.")
    return frozenset(values)


class PMCClient:
    """Uses only the public PMC S3 endpoint and its versioned metadata/XML."""
    def __init__(self, cache_dir, session=None, timeout=20, reuse_scope="commercial", allow_clinical_facts=False):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.session = session or requests.Session()
        self.timeout = min(45, max(1, float(timeout)))
        try:
            self.allowed_licenses = allowed_license_codes(reuse_scope)
        except ValueError as exc:
            raise ImportError(str(exc)) from None
        self.reuse_scope = reuse_scope
        self.allow_clinical_facts = allow_clinical_facts is True
        self.source_licenses = self.allowed_licenses | frozenset(code for code in FACTUAL_SOURCE_LICENSES
            if source_reuse_basis(code, reuse_scope, self.allow_clinical_facts))
        self._metadata_index = None

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
                license_data = provenance.get("license", {})
                compatible = source_license_allowed(provenance, self.reuse_scope, self.allow_clinical_facts)
                if compatible and snapshot.exists() and metadata_snapshot.exists():
                    if _hash_file(snapshot) != provenance.get("xmlSha256") or _hash_file(metadata_snapshot) != provenance.get("metadataSha256"):
                        raise ImportError("O snapshot do artigo foi alterado; revalide a fonte com refresh_sources.")
                    return value
                # Older caches are upgraded by rechecking the current source.
            else:
                raise ImportError("O cache verificado do artigo está inválido.")
        snapshots = self._cached_metadata(pmcid) if not refresh else []
        if not snapshots:
            listing = _xml(self._get(PMC_BUCKET + "/?list-type=2&prefix=" + pmcid + ".&delimiter=/", 1000000))
            versions = []
            for item in listing.iter():
                if item.tag.rsplit("}", 1)[-1] == "Prefix" and item.text:
                    match = re.fullmatch(re.escape(pmcid) + r"\.([1-9][0-9]*)/", item.text)
                    if match:
                        versions.append(int(match[1]))
            if not versions or len(versions) > 30:
                raise SourceRejected("O artigo não possui uma versão distribuída elegível no PMC Cloud.")
            for version in sorted(set(versions), reverse=True):
                metadata_raw = self._get(PMC_BUCKET + f"/metadata/{pmcid}.{version}.json", 1000000)
                try:
                    metadata = json.loads(metadata_raw)
                except ValueError:
                    raise ImportError("Os metadados do PMC estão inválidos.") from None
                _atomic_json(self.cache_dir / f"{pmcid}.{version}.metadata.json", metadata)
                snapshot_path = self.cache_dir / f"{pmcid}.{version}.{hashlib.sha256(metadata_raw).hexdigest()}.metadata.json"
                _atomic_bytes(snapshot_path, metadata_raw)
                if self._metadata_index is not None:
                    known = self._metadata_index.setdefault(pmcid, [])
                    for path in (self.cache_dir / f"{pmcid}.{version}.metadata.json", snapshot_path):
                        if path not in known:
                            known.append(path)
                snapshots.append((version, metadata, metadata_raw))
        candidates = []
        for version, metadata, metadata_raw in snapshots:
            if not isinstance(metadata, dict) or metadata.get("pmcid") != pmcid or metadata.get("version") != version:
                raise ImportError("A identidade da versão do artigo não confere.")
            # Do not silently choose another version of a retracted article.
            if metadata.get("is_retracted") is True:
                raise SourceRejected("Artigo retratado: não elegível para a biblioteca.")
            if metadata.get("is_retracted") is not False or metadata.get("is_manuscript") is not False:
                continue
            if metadata.get("license_code") in self.source_licenses:
                candidates.append((version, metadata, metadata_raw))
        if not candidates:
            raise SourceRejected("A fonte não confirma uma licença permitida pela política de uso para uma versão publicada sem retratação.")
        last_rejection = None
        for version, metadata, metadata_raw in candidates:
            try:
                value = self._verify_version(pmcid, version, metadata, metadata_raw)
                _atomic_json(cached, value)
                return value
            except SourceRejected as exc:
                last_rejection = exc
        raise last_rejection

    def _cached_metadata(self, pmcid):
        """Reuse exact private metadata snapshots when policy changes, not a live claim."""
        if self._metadata_index is None:
            # One directory scan per client, not one 50,000-file scan per case.
            self._metadata_index = {}
            for path in self.cache_dir.iterdir():
                match = re.fullmatch(r"(PMC[1-9][0-9]*)\.[1-9][0-9]*(?:\.[a-f0-9]{64})?\.metadata\.json", path.name)
                if match:
                    self._metadata_index.setdefault(match[1], []).append(path)
        paths = self._metadata_index.get(pmcid, [])
        snapshots = []
        for path in paths:
            match = re.fullmatch(re.escape(pmcid) + r"\.([1-9][0-9]*)\.metadata\.json", path.name)
            if not match:
                continue
            version, latest = int(match[1]), _json_file(path)
            for snapshot in paths:
                if not snapshot.name.startswith(f"{pmcid}.{version}."):
                    continue
                digest = snapshot.name.split(".")[2]
                if not re.fullmatch(r"[a-f0-9]{64}", digest):
                    continue
                raw = snapshot.read_bytes()
                if hashlib.sha256(raw).hexdigest() != digest:
                    raise ImportError("Um snapshot de metadados foi alterado; use refresh_sources.")
                try:
                    if json.loads(raw) == latest:
                        snapshots.append((version, latest, raw))
                        break
                except ValueError:
                    raise ImportError("O snapshot de metadados está inválido; use refresh_sources.") from None
        return sorted(snapshots, key=lambda item: item[0], reverse=True)

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
        legacy_factual_notice = not licenses and self.allow_clinical_facts and metadata["license_code"] in FACTUAL_SOURCE_LICENSES
        if legacy_factual_notice:
            licenses = [node for node in article_meta.findall("./permissions/copyright-statement")
                        if re.search(r"distributed under.*Creative Commons", _xml_text(node), re.I)]
        urls, statements, data_waiver = set(), [], False
        for license_node in licenses:
            node_statement, node_urls = _xml_text(license_node), set()
            statements.append(node_statement)
            if legacy_factual_notice:
                for value in re.findall(r"https?://(?:www\.)?creativecommons\.org/[^\s<>()\"']+", node_statement, re.I):
                    canonical = _license_url(value.rstrip(".,"))
                    if canonical:
                        node_urls.add(canonical)
            for item in license_node.iter():
                links = [value for key, value in item.attrib.items() if key.rsplit("}", 1)[-1] == "href"]
                if item.tag.rsplit("}", 1)[-1] == "license_ref" and item.text:
                    links.append(item.text.strip())
                for value in links:
                    if "creativecommons.org/" in value.lower():
                        canonical = _license_url(value)
                        if canonical:
                            node_urls.add(canonical)
            urls.update(node_urls)
            if "https://creativecommons.org/publicdomain/zero/1.0/" in node_urls:
                data_waiver |= bool(re.search(
                    r"waiver.*applies to the data|data associated with the article are available under the terms of .*data waiver",
                    node_statement, re.I))
        if not urls or not all(license_code_from_url(url, include_nd=self.allow_clinical_facts) in self.source_licenses for url in urls):
            raise SourceRejected("A declaração e a URL exata da licença no XML não foram reconhecidas para o fluxo de uso configurado.")
        statement = " ".join(statements)
        candidates = {url for url in urls if license_code_from_url(url, include_nd=self.allow_clinical_facts) == metadata["license_code"]}
        # BMC's article license often includes an additional CC0 waiver for data.
        # Keep the article's CC BY license and the complete declaration for attribution.
        if len(candidates) != 1:
            raise SourceRejected("A versão da licença do artigo é ambígua no XML.")
        license_url = next(iter(candidates))
        extra = urls - candidates
        if extra and not (metadata["license_code"] != "CC0" and extra == {"https://creativecommons.org/publicdomain/zero/1.0/"}
                          and data_waiver):
            raise SourceRejected("O XML mistura licenças sem distinguir a licença do artigo.")
        code = license_code_from_url(license_url, include_nd=self.allow_clinical_facts)
        if code != metadata["license_code"] or not statement:
            raise SourceRejected("A licença do XML diverge dos metadados ou não traz declaração verificável.")
        notices = [_xml_text(item) for name in ("copyright-statement", "copyright-year", "copyright-holder")
                   for item in article_meta.findall("./permissions/" + name)]
        attribution_text = " ".join(dict.fromkeys([statement, *filter(None, notices)]))
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
            "license": {"code": code, "url": license_url, "version": license_version_from_url(license_url, include_nd=self.allow_clinical_facts), "text": attribution_text,
                        **license_obligations(code, license_url)},
            "pmcVersion": version, "xmlSha256": hashlib.sha256(raw).hexdigest(), "xmlMd5": checksum.lower(),
            "metadataSha256": hashlib.sha256(metadata_raw).hexdigest(), "checkedAt": _now(),
        }
        if source_reuse_basis(code, self.reuse_scope, self.allow_clinical_facts) == "clinical_facts":
            provenance["reuseBasis"] = "clinical_facts"
            provenance["license"].pop("adaptationLicenseUrl", None)
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
    def __init__(self, config, work_dir, ai_service=None, pmc_client=None, ai_service_factory=None):
        self.config = config
        self._injected_ai = ai_service is not None
        self._ai_service_factory = ai_service_factory
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
        self.reuse_scope = config.get("CASE_REUSE_SCOPE", "commercial")
        self.allow_clinical_facts = config.get("CASE_ALLOW_CLINICAL_FACTS", False) is True
        try:
            self.allowed_licenses = allowed_license_codes(self.reuse_scope)
        except ValueError as exc:
            raise ImportError(str(exc)) from None
        self.source_licenses = self.allowed_licenses | frozenset(code for code in FACTUAL_SOURCE_LICENSES
            if source_reuse_basis(code, self.reuse_scope, self.allow_clinical_facts))
        self.pmc = pmc_client or PMCClient(self.work_dir / "pmc-cache", reuse_scope=self.reuse_scope,
                                        allow_clinical_facts=self.allow_clinical_facts)
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
        record.update(reuseScope=self.reuse_scope, policyVersion=REUSE_POLICY_VERSION,
                      allowClinicalFacts=self.allow_clinical_facts)
        _atomic_json(self.work_dir / "records" / (record["pmcid"] + ".json"), record)

    def run(self, source_path, specialties=None, target_per_specialty=300, limit=50,
            adapt=False, retry_errors=False, refresh_sources=False, on_progress=None,
            all_records=False, source_workers=1, adapt_workers=1, excluded_pmcids=None):
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
        if isinstance(source_workers, bool) or not isinstance(source_workers, int) or not 1 <= source_workers <= 8:
            raise ImportError("Use entre 1 e 8 conexões de verificação de fontes.")
        if isinstance(adapt_workers, bool) or not isinstance(adapt_workers, int) or not 1 <= adapt_workers <= 16:
            raise ImportError("Use entre 1 e 16 tarefas de adaptação.")
        if adapt_workers > 1 and (not adapt or not all_records):
            raise ImportError("Adaptação paralela exige adapt=True e all_records=True.")
        if adapt_workers > 1 and self._injected_ai and self._ai_service_factory is None:
            raise ImportError("Adaptação paralela requer uma fábrica de serviços de IA independentes.")
        excluded = _excluded_ids(excluded_pmcids)
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
            ready = Counter(case["specialty"] for case in self.iter_drafts(excluded_pmcids=excluded))
            available = bool(str(self.config.get("DEEPSEEK_API_KEY") or "").strip()) and not self.ai.is_demo
            processed, seen, duplicates, invalid_ids, total, excluded_count = 0, set(), 0, 0, 0, 0
            completed, stop_reason, stop_message = 0, None, None
            stop = _AIStop()
            task_counts = {"submitted": 0, "finished": 0, "interrupted": 0, "cancelledBeforeStart": 0}
            run_usage = _add_usage({}, {})
            def work_items():
                nonlocal processed, duplicates, invalid_ids, total, excluded_count
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
                    if pmcid in excluded:
                        excluded_count += 1
                        continue
                    if processed >= limit or stop.event.is_set():
                        continue
                    record = self._record(pmcid)
                    status = record["status"]
                    if status == "draft":
                        continue
                    if status in _RETRYABLE and not retry_errors:
                        continue
                    same_policy = (record.get("reuseScope", "commercial") == self.reuse_scope and record.get("policyVersion", 1) == REUSE_POLICY_VERSION
                                   and record.get("allowClinicalFacts", False) is self.allow_clinical_facts)
                    if status == "license_rejected" and not refresh_sources and same_policy:
                        continue
                    if status == "verified" and record.get("license") in self.source_licenses and not refresh_sources and (not adapt or not available):
                        continue
                    if status == "quota_deferred" and record.get("license") in self.source_licenses and not refresh_sources:
                        if not adapt or not available or record.get("specialty") not in selected or (not all_records and ready[record["specialty"]] >= target_per_specialty):
                            continue
                    processed += 1
                    yield source, record

            verified_items = self._verify_batch(work_items(), source_workers, refresh_sources)
            for outcome in self._process_batch(verified_items, adapt_workers, stop, task_counts,
                    selected=selected, ready=ready, all_records=all_records,
                    target_per_specialty=target_per_specialty, adapt=adapt, available=available):
                record, case, usage = outcome["record"], outcome["case"], outcome["usage"]
                run_usage = _add_usage(run_usage, usage)
                if stop.event.is_set():
                    stop_reason, stop_message = stop.code, str(AIUnavailableError(stop.code))
                if outcome["interrupted"]:
                    task_counts["interrupted"] += 1
                    if not usage["requests"]:
                        # No request was sent: leave this record untouched.
                        continue
                if case is not None:
                    _atomic_json(self.work_dir / "drafts" / (record["pmcid"] + ".json"), case)
                    ready[case["specialty"]] += 1
                if usage["requests"] or record.get("aiUsage"):
                    record["aiUsage"] = _add_usage(record.get("aiUsage"), usage)
                self._save_record(record)
                completed += 1
                if on_progress:
                    on_progress({key: record.get(key) for key in ("pmcid", "status", "specialty", "error")})
            manifest.update({"sourceRecords": total, "uniquePmcids": len(seen), "duplicatePmcids": duplicates,
                             "invalidPmcids": invalid_ids, "lastRunAt": _now(),
                             "stopReason": stop_reason, "stopMessage": stop_message,
                             "reuseScope": self.reuse_scope, "policyVersion": REUSE_POLICY_VERSION,
                             "allowClinicalFacts": self.allow_clinical_facts,
                             "excludedPmcids": sorted(seen & excluded)})
            _atomic_json(manifest_path, manifest)
            report = self.report(selected, target_per_specialty, excluded_pmcids=seen & excluded)
            report.update({"processedThisRun": completed, "adaptationRequested": bool(adapt), "aiAvailable": available,
                           "adaptationBlocked": bool(adapt and not available), "allRecords": bool(all_records),
                           "sourceWorkers": source_workers, "adaptWorkers": adapt_workers,
                           "caseTasks": task_counts, "aiUsageThisRun": run_usage,
                           "excludedThisRun": excluded_count})
            _atomic_json(self.work_dir / "report.json", report)
            return report

    def _process_case(self, item, ai, stop, *, selected, ready, all_records,
                      target_per_specialty, adapt, available):
        """Pure case work: workers never write files, invoke callbacks or touch DB."""
        source, record, verification = item
        record = dict(record)
        record["attempts"] += 1
        record.pop("error", None)
        record.pop("availabilityError", None)
        before = _usage_snapshot(ai)
        stage, case, interrupted = "source", None, False
        try:
            if isinstance(verification, Exception):
                raise verification
            verified = verification
            if not source_license_allowed(verified["provenance"], self.reuse_scope, self.allow_clinical_facts):
                raise SourceRejected("A licença da fonte não é permitida pela política de uso atual.")
            record.update({"status": "verified", "license": verified["provenance"]["license"]["code"],
                           "sourceCheckedAt": verified["provenance"]["checkedAt"],
                           "reuseBasis": verified["provenance"].get("reuseBasis", "licensed_adaptation")})
            if adapt and available:
                stage = "classification"
                stop.check()
                if not record.get("specialty"):
                    classified = ai.classify_case(source, SPECIALTIES)
                    if classified.get("specialty") not in SPECIALTIES:
                        raise AIError("A especialidade classificada não pertence ao catálogo.")
                    record.update({"specialty": classified["specialty"], "classificationReason": classified["reason"]})
                specialty = record["specialty"]
                if specialty not in selected or (not all_records and ready[specialty] >= target_per_specialty):
                    record["status"] = "quota_deferred"
                else:
                    stage = "adaptation"
                    stop.check()
                    case = validate_case(ai.adapt_case({**source, "articleText": verified["articleText"]}, verified["provenance"], specialty))
                    if case["catalogType"] != "published_case" or case["reviewStatus"] != "review_required" or case["specialty"] != specialty or case["provenance"]["pmcid"] != record["pmcid"]:
                        raise CaseValidationError("Rascunho incompatível com a origem ou a revisão obrigatória.")
                    if any(case["provenance"].get(key) != value for key, value in verified["provenance"].items()):
                        raise CaseValidationError("A adaptação alterou a proveniência verificada.")
                    record.update(status="draft", caseId=case["caseId"])
        except SourceRejected as exc:
            case = None
            record.update(status="license_rejected", error=str(exc))
        except AIUnavailableError as exc:
            case = None
            interrupted = isinstance(exc, AIInterruptedError)
            stop.trip(exc)
            record.update(status={"source": "source_error", "classification": "classification_error", "adaptation": "adaptation_error"}[stage],
                          error=str(exc), availabilityError=exc.code)
        except (AIError, ImportError, CaseValidationError) as exc:
            case = None
            record.update(status={"source": "source_error", "classification": "classification_error", "adaptation": "adaptation_error"}[stage], error=str(exc))
        after = _usage_snapshot(ai)
        usage = {key: max(0, after[key] - before[key]) for key in USAGE_KEYS}
        return {"record": record, "case": case, "usage": usage, "interrupted": interrupted}

    def _process_batch(self, items, workers, stop, counts, **options):
        if workers == 1:
            for item in items:
                if stop.event.is_set():
                    continue  # drain prefetched sources and complete CSV inventory
                counts["submitted"] += 1
                result = self._process_case(item, self.ai, stop, **options)
                counts["finished"] += 1
                yield result
            return

        def process(item):
            # Each case owns its service and accounting. Shared state is only the
            # stop signal; no Flask context or database session enters workers.
            ai = (self._ai_service_factory(request_guard=stop.check, on_unavailable=stop.trip)
                  if self._ai_service_factory else AIService(self.config,
                  request_timeout=self.config.get("AI_IMPORT_TIMEOUT", 120),
                  request_guard=stop.check, on_unavailable=stop.trip))
            return self._process_case(item, ai, stop, **options)

        iterator = iter(items)
        exhausted, pending = False, set()
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="medlab-adapt") as pool:
            try:
                while not exhausted or pending:
                    while not exhausted and len(pending) < workers and not stop.event.is_set():
                        try:
                            item = next(iterator)
                        except StopIteration:
                            exhausted = True
                            break
                        if stop.event.is_set():
                            break
                        pending.add(pool.submit(process, item))
                        counts["submitted"] += 1
                    if stop.event.is_set():
                        for future in tuple(pending):
                            if future.cancel():
                                pending.remove(future)
                                counts["cancelledBeforeStart"] += 1
                        if not exhausted:
                            for _ in iterator:
                                pass  # no new source work; work_items checks stop
                            exhausted = True
                    if pending:
                        done, _ = wait(pending, return_when=FIRST_COMPLETED)
                        for future in done:
                            pending.remove(future)
                            counts["finished"] += 1
                            yield future.result()
            finally:
                # Also guard subsequent HTTP calls if Ctrl-C/callback failure
                # unwinds the main thread. Existing requests are allowed to end.
                if pending:
                    stop.trip(AIUnavailableError("run_cancelled"))
                    for future in pending:
                        future.cancel()

    def _verify_batch(self, items, workers, refresh):
        """Bounded source I/O, separate from adaptation; callers own checkpoints."""
        local = threading.local()
        def verify(item):
            source, record = item
            if workers == 1 or type(self.pmc) is not PMCClient:
                client = self.pmc
            else:
                if not hasattr(local, "client"):
                    local.client = PMCClient(self.pmc.cache_dir, timeout=self.pmc.timeout, reuse_scope=self.pmc.reuse_scope,
                                             allow_clinical_facts=self.pmc.allow_clinical_facts)
                client = local.client
            try:
                value = client.verify(record["pmcid"], refresh=refresh)
            except (SourceRejected, ImportError) as exc:
                value = exc
            return source, record, value

        if workers == 1:
            for item in items:
                yield verify(item)
            return
        # At most workers records in flight: compatible with Python 3.10 and
        # shared hosting memory limits; no eagerly queued 14,000-row task list.
        with ThreadPoolExecutor(max_workers=workers) as pool:
            pending = deque()
            for item in items:
                pending.append(pool.submit(verify, item))
                if len(pending) >= workers:
                    yield pending.popleft().result()
            while pending:
                yield pending.popleft().result()

    def iter_drafts(self, pmcids=None, excluded_pmcids=None):
        """Only valid, source-attributed drafts; caller controls DB insertion/review."""
        excluded = _excluded_ids(excluded_pmcids)
        if pmcids is None:
            paths = sorted((self.work_dir / "drafts").glob("PMC*.json"))
        else:
            if any(not isinstance(pmcid, str) or not _PMCID.fullmatch(pmcid) for pmcid in pmcids):
                raise ImportError("Identificador inválido para recuperar o rascunho.")
            paths = [self.work_dir / "drafts" / (pmcid + ".json") for pmcid in pmcids]
        for path in paths:
            if path.stem in excluded:
                continue
            try:
                case = validate_case(_json_file(path))
            except CaseValidationError:
                raise ImportError("Um rascunho salvo falhou na validação; revise-o antes de importar para o banco.") from None
            if case["catalogType"] != "published_case" or case["reviewStatus"] != "review_required" or path.stem != case["provenance"]["pmcid"]:
                raise ImportError("Um rascunho não corresponde à fonte ou à revisão obrigatória.")
            if not source_license_allowed(case["provenance"], self.reuse_scope, self.allow_clinical_facts):
                raise ImportError("Um rascunho possui licença incompatível com CASE_REUSE_SCOPE.")
            yield case

    def report(self, specialties=None, target_per_specialty=300, excluded_pmcids=None):
        selected = tuple(specialties or SPECIALTIES)
        manifest_path = self.work_dir / "manifest.json"
        manifest = _json_file(manifest_path) if manifest_path.exists() else {}
        excluded = _excluded_ids(excluded_pmcids if excluded_pmcids is not None else manifest.get("excludedPmcids", []))
        records = [_json_file(path) for path in (self.work_dir / "records").glob("PMC*.json") if path.stem not in excluded]
        counts = Counter(record["status"] for record in records)
        drafts = Counter(case["specialty"] for case in self.iter_drafts(excluded_pmcids=excluded))
        classified = Counter(record["specialty"] for record in records if record.get("specialty"))
        eligible = sum(record.get("license") in self.source_licenses and record["status"] not in {"license_rejected", "source_error"} for record in records)
        factual = sum(record.get("license") in self.source_licenses - self.allowed_licenses and record.get("reuseBasis") == "clinical_facts"
                      and record["status"] not in {"license_rejected", "source_error"} for record in records)
        source_total = manifest.get("uniquePmcids", 0)
        active_total = max(0, source_total - len(excluded))
        usage = _add_usage({}, {})
        for record in records:
            usage = _add_usage(usage, record.get("aiUsage"))
        return {
            "dataset": "MedCaseReasoning", "datasetRevision": manifest.get("datasetRevision", DATASET_REVISION),
            "reuseScope": self.reuse_scope, "policyVersion": REUSE_POLICY_VERSION,
            "allowClinicalFacts": self.allow_clinical_facts,
            "allowedLicenses": sorted(self.allowed_licenses), "aiUsage": usage,
            "factualSourceLicenses": sorted(self.source_licenses - self.allowed_licenses),
            "sourceSha256": manifest.get("sourceSha256"), "sourceRecords": manifest.get("sourceRecords", 0),
            "uniquePmcids": source_total, "duplicatePmcids": manifest.get("duplicatePmcids", 0),
            "activeSourceRecords": active_total, "excludedSources": len(excluded),
            "invalidPmcids": manifest.get("invalidPmcids", 0), "sourceVerified": eligible,
            "sourceVerifiedForLicensedAdaptation": eligible - factual, "sourceVerifiedForClinicalFacts": factual,
            "stoppedEarly": bool(manifest.get("stopReason")), "stopReason": manifest.get("stopReason"),
            "stopMessage": manifest.get("stopMessage"),
            "licenseRejected": counts["license_rejected"], "pendingVerification": max(0, active_total - len(records)),
            "unclassified": sum(not record.get("specialty") and record["status"] == "verified" for record in records),
            "adaptedDrafts": sum(drafts.values()), "reviewed": 0, "published": None,
            "statusCounts": dict(counts), "targetTotal": len(selected) * target_per_specialty,
            "minimumSourceShortfall": max(0, len(selected) * target_per_specialty - active_total),
            "specialties": {specialty: {"target": target_per_specialty, "classified": classified[specialty],
                                        "drafts": drafts[specialty], "remainingDrafts": max(0, target_per_specialty - drafts[specialty])} for specialty in selected},
            "notes": ["Registros brutos não equivalem a casos traduzidos, revisados ou publicados.",
                      "Especialidade só é atribuída após classificação por IA; cada relato tem uma especialidade principal.",
                      "Rascunhos passam por validação estrutural e auditoria por IA, sem revisão clínica humana.",
                      "Publicado não é contado por este importador; consulte o banco da aplicação.",
                      "Tokens contabilizados são somente os informados pelo provedor nas tentativas instrumentadas.",
                      "Snapshots de fonte podem mudar; revalide origem, licença e retratação antes da publicação."],
        }
