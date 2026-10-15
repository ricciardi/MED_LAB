"""Private, checksum-pinned PMC figure inventory and original-image storage.

Only media_urls in the original verified PMC Cloud metadata are fetched.
No article links, HTML scraping, image editing or publication is performed.
Cloud contract: https://pmc-oa-opendata.s3.amazonaws.com/README.txt
"""
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import tempfile
from urllib.parse import parse_qs, quote, unquote, urlparse
import xml.etree.ElementTree as ET

import requests


PMC_BUCKET = "https://pmc-oa-opendata.s3.amazonaws.com"
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
_PMCID = re.compile(r"PMC[1-9][0-9]{0,11}\Z")
_ASSET = re.compile(r"pmcmedia-[0-9a-f]{32}\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_MD5 = re.compile(r"[0-9a-f]{32}\Z")
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._() -]{0,199}\Z")
_OBJECT = re.compile(r"objects/[0-9a-f]{2}/[0-9a-f]{64}\.(?:jpg|png|webp)\Z")
_GROUPS = {"physical", "auxiliary"}
_SOURCE_LICENSES = {"CC BY", "CC0", "CC BY-NC", "CC BY-SA", "CC BY-NC-SA", "CC BY-ND", "CC BY-NC-ND"}


class SourceMediaError(ValueError):
    """Safe error; response bodies and private source text are never included."""


MediaError = SourceMediaError


def _now():
    return datetime.now(timezone.utc).isoformat()


def _atomic(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _save_json(path, value):
    _atomic(path, (json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode())


def _read_json(path, limit=8 * 1024 * 1024):
    try:
        path = Path(path)
        if path.stat().st_size > limit or path.is_symlink():
            raise ValueError
        result = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(result, dict):
            raise ValueError
        return result
    except (OSError, ValueError, TypeError):
        raise MediaError("Metadados privados de mídia ausentes ou inválidos.") from None


def _text(node):
    return " ".join(" ".join(node.itertext()).split()) if node is not None else ""


def _image_info(raw):
    """Read raster dimensions without a codec dependency or modifying bytes."""
    if not 24 <= len(raw) <= MAX_IMAGE_BYTES:
        raise MediaError("A imagem excede o limite ou está incompleta.")
    if raw.startswith(b"\x89PNG\r\n\x1a\n") and raw[12:16] == b"IHDR":
        width, height = struct.unpack(">II", raw[16:24])
        if b"IEND" not in raw[-20:]:
            raise MediaError("Imagem PNG incompleta.")
        mime, extension = "image/png", "png"
    elif len(raw) >= 30 and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        if int.from_bytes(raw[4:8], "little") + 8 != len(raw):
            raise MediaError("Imagem WebP incompleta.")
        kind = raw[12:16]
        if kind == b"VP8X" and not raw[20] & 0x02:
            width = 1 + int.from_bytes(raw[24:27], "little")
            height = 1 + int.from_bytes(raw[27:30], "little")
        elif kind == b"VP8 " and raw[23:26] == b"\x9d\x01\x2a":
            width = int.from_bytes(raw[26:28], "little") & 0x3FFF
            height = int.from_bytes(raw[28:30], "little") & 0x3FFF
        elif kind == b"VP8L" and raw[20] == 0x2F:
            dimensions = int.from_bytes(raw[21:25], "little")
            width, height = (dimensions & 0x3FFF) + 1, ((dimensions >> 14) & 0x3FFF) + 1
        else:
            raise MediaError("Formato WebP animado ou não reconhecido.")
        mime, extension = "image/webp", "webp"
    elif raw.startswith(b"\xff\xd8") and raw.endswith(b"\xff\xd9"):
        width = height = 0
        pos = 2
        while pos < len(raw) - 3:
            if raw[pos] != 0xFF:
                break
            while pos < len(raw) and raw[pos] == 0xFF:
                pos += 1
            if pos >= len(raw):
                break
            marker = raw[pos]
            pos += 1
            if marker in (0xD8, 0xD9, 0x01) or 0xD0 <= marker <= 0xD7:
                continue
            if marker == 0xDA or pos + 2 > len(raw):
                break
            length = int.from_bytes(raw[pos:pos + 2], "big")
            if length < 2 or pos + length > len(raw):
                break
            if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF} and length >= 8:
                height, width = struct.unpack(">HH", raw[pos + 3:pos + 7])
                break
            pos += length
        mime, extension = "image/jpeg", "jpg"
    else:
        raise MediaError("Somente imagens originais JPEG, PNG e WebP estáticas são aceitas.")
    if not width or not height or width > 20000 or height > 20000 or width * height > MAX_IMAGE_PIXELS:
        raise MediaError("Dimensões da imagem inválidas ou acima do limite.")
    return {"mimeType": mime, "extension": extension, "width": width, "height": height}


def _embedded_metadata_flags(raw):
    """Hold text-bearing metadata without decoding, editing or deleting it."""
    flags = []
    if raw.startswith(b"\xff\xd8"):
        pos = 2
        while pos + 4 <= len(raw) and raw[pos] == 0xFF:
            while pos < len(raw) and raw[pos] == 0xFF:
                pos += 1
            if pos >= len(raw):
                break
            marker = raw[pos]; pos += 1
            if marker in (0xDA, 0xD9):
                break
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                continue
            if pos + 2 > len(raw):
                break
            length = int.from_bytes(raw[pos:pos + 2], 'big')
            if length < 2 or pos + length > len(raw):
                break
            if marker in (0xE1, 0xED, 0xFE):
                flags.append('embedded_text_metadata')
            pos += length
    elif raw.startswith(b"\x89PNG\r\n\x1a\n"):
        pos = 8
        while pos + 12 <= len(raw):
            size = int.from_bytes(raw[pos:pos + 4], 'big')
            if raw[pos + 4:pos + 8] in (b'tEXt', b'iTXt', b'zTXt', b'eXIf'):
                flags.append('embedded_text_metadata')
            pos += size + 12
    elif raw[:4] == b'RIFF' and raw[8:12] == b'WEBP':
        pos = 12
        while pos + 8 <= len(raw):
            size = int.from_bytes(raw[pos + 4:pos + 8], 'little')
            if raw[pos:pos + 4] in (b'EXIF', b'XMP '):
                flags.append('embedded_text_metadata')
            pos += 8 + size + size % 2
    return sorted(set(flags))


def _official_media_url(value, pmcid, version):
    if not isinstance(value, str) or not isinstance(pmcid, str) or not _PMCID.fullmatch(pmcid) or type(version) is not int or version < 1:
        raise MediaError("Referência oficial de mídia inválida.")
    parsed = urlparse(value)
    allowed = ((parsed.scheme == "s3" and parsed.netloc == "pmc-oa-opendata") or
               (parsed.scheme == "https" and parsed.netloc == "pmc-oa-opendata.s3.amazonaws.com"))
    if not allowed or parsed.username or parsed.password or parsed.fragment:
        raise MediaError("A mídia não pertence ao serviço oficial permitido.")
    prefix = f"/{pmcid}.{version}/"
    path = unquote(parsed.path)
    name = path[len(prefix):] if path.startswith(prefix) else ""
    try:
        query = parse_qs(parsed.query, strict_parsing=True)
    except ValueError:
        raise MediaError("Parâmetros da referência oficial de mídia inválidos.") from None
    if not _NAME.fullmatch(name) or set(query) != {"md5"} or len(query["md5"]) != 1 or not _MD5.fullmatch(query["md5"][0].lower()):
        raise MediaError("Mídia sem caminho e checksum oficiais válidos.")
    return {"name": name, "url": PMC_BUCKET + prefix + quote(name, safe="._-"), "md5": query["md5"][0].lower()}


def _figure_flags(fig, caption):
    rights = _text(fig.find("permissions"))
    # These are conservative holds, never automatic privacy approval.
    third_party = bool(rights or re.search(r"reproduc(?:ed|tion)\s+(?:from|with)|reprinted|courtesy\s+of|with\s+permission|©|copyright", caption, re.I))
    personal = bool(re.search(r"patient(?:'s)?\s+(?:name|initials|identification)|date\s+of\s+birth|hospital\s+(?:number|id)|medical\s+record\s+number", caption, re.I))
    face = bool(re.search(r"(?:clinical\s+)?photograph.{0,55}\b(?:face|facial|portrait)|(?:face|facial|portrait).{0,55}photograph", caption, re.I))
    return {"rightsStatus": "review_required" if third_party else "article_license",
            "privacyStatus": "selection_hold" if personal or face else "pending_visual_review",
            "selectionFlags": (["possible_third_party_rights"] if third_party else []) +
                              (["possible_personal_identifier"] if personal else []) +
                              (["possibly_recognizable_face"] if face else []),
            "figurePermissions": rights}


class SourceMediaStore:
    def __init__(self, media_dir, cache_dir=None, session=None, timeout=25):
        self.media_dir = Path(media_dir).resolve()
        self.cache_dir = Path(cache_dir).resolve() if cache_dir is not None else None
        self.session = session or requests.Session()
        self.timeout = min(45, max(1, float(timeout)))

    def _asset_path(self, asset_id):
        if not isinstance(asset_id, str) or not _ASSET.fullmatch(asset_id):
            raise MediaError("Identificador de mídia inválido.")
        path = self.media_dir / "assets" / (asset_id + ".json")
        if not path.resolve().is_relative_to(self.media_dir):
            raise MediaError("O catálogo de imagens saiu da pasta privada configurada.")
        return path

    def lookup(self, asset_id):
        value = _read_json(self._asset_path(asset_id))
        pmcid, source_hash = value.get("pmcid"), value.get("xmlSha256")
        bindings = value.get("caseBindings")
        if (value.get("assetId") != asset_id or not isinstance(pmcid, str) or not _PMCID.fullmatch(pmcid)
                or not isinstance(source_hash, str) or not _HASH.fullmatch(source_hash)
                or not isinstance(bindings, list) or any(not isinstance(b, dict) for b in bindings)):
            raise MediaError("Identidade da mídia não confere.")
        return value

    def catalog_source(self, pmcid):
        """Index verified local XML/metadata; no network or importer writes."""
        if self.cache_dir is None or not isinstance(pmcid, str) or not _PMCID.fullmatch(pmcid):
            raise MediaError("Fonte de mídia inválida.")
        verified = _read_json(self.cache_dir / f"{pmcid}.verified.json")
        p = verified.get("provenance", {})
        try:
            version = p["pmcVersion"]
            if p["pmcid"] != pmcid or type(version) is not int or version < 1 or not _MD5.fullmatch(p["xmlMd5"]) or not _HASH.fullmatch(p["xmlSha256"]) or not _HASH.fullmatch(p["metadataSha256"]):
                raise ValueError
            xml_path = self.cache_dir / f'{pmcid}.{version}.{p["xmlMd5"]}.xml'
            meta_path = self.cache_dir / f'{pmcid}.{version}.{p["metadataSha256"]}.metadata.json'
            if xml_path.is_symlink() or meta_path.is_symlink() or xml_path.stat().st_size > 12 * 1024 * 1024 or meta_path.stat().st_size > 8 * 1024 * 1024:
                raise ValueError
            raw, meta_raw = xml_path.read_bytes(), meta_path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != p["xmlSha256"] or hashlib.md5(raw).hexdigest() != p["xmlMd5"] or hashlib.sha256(meta_raw).hexdigest() != p["metadataSha256"]:
                raise ValueError
            metadata = json.loads(meta_raw)
            if metadata.get("pmcid") != pmcid or metadata.get("version") != version or metadata.get("is_retracted") is not False or metadata.get("license_code") != p["license"]["code"] or p["license"]["code"] not in _SOURCE_LICENSES:
                raise ValueError
            if b"<!ENTITY" in raw.upper() or re.search(br"<!DOCTYPE[^>]*\[", raw, re.I):
                raise ValueError
            article = ET.fromstring(raw)
        except (KeyError, TypeError, ValueError, OSError, ET.ParseError):
            raise MediaError("O snapshot verificado não permite catalogar imagens com integridade.") from None
        for node in article.iter():
            node.tag = node.tag.rsplit("}", 1)[-1]
        media, invalid_media = [], 0
        for url in metadata.get("media_urls") or []:
            try:
                media.append(_official_media_url(url, pmcid, version))
            except (MediaError, ValueError):
                invalid_media += 1
        figures, assets, seen_figures = [], {}, set()
        for index, fig in enumerate(article.findall(".//fig"), 1):
            figure_id = fig.get("id") or f"figure-{index}"
            if figure_id in seen_figures:
                raise MediaError("O artigo contém identificadores de figura ambíguos.")
            seen_figures.add(figure_id)
            caption, label = _text(fig.find("caption")), _text(fig.find("label"))
            flags = _figure_flags(fig, caption)
            item = {"figureId": figure_id, "label": label, "caption": caption,
                    "assetIds": [], "unmatchedGraphics": [], **flags}
            for graphic in fig.findall(".//graphic"):
                href = next((value for key, value in graphic.attrib.items() if key.rsplit("}", 1)[-1] == "href"), "")
                if not _NAME.fullmatch(href):
                    item["unmatchedGraphics"].append({"reason": "unsupported_graphic_reference"})
                    continue
                matches = [m for m in media if m["name"] == href or ("." not in href and Path(m["name"]).stem == href)]
                # Some publishers use dotted stems without an extension.
                if not matches:
                    matches = [m for m in media if Path(m["name"]).stem == href]
                candidates = [m for m in matches if Path(m["name"]).suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}]
                if len(candidates) != 1:
                    item["unmatchedGraphics"].append({"reference": href, "reason": "ambiguous_media" if len(candidates) > 1 else "no_supported_official_image"})
                    continue
                m = candidates[0]
                identity = "|".join((pmcid, str(version), p["xmlSha256"], figure_id, m["name"], m["md5"]))
                asset_id = "pmcmedia-" + hashlib.sha256(identity.encode()).hexdigest()[:32]
                if asset_id not in item["assetIds"]:
                    item["assetIds"].append(asset_id)
                record = {"schemaVersion": 1, "assetId": asset_id, "pmcid": pmcid, "pmcVersion": version,
                    "xmlSha256": p["xmlSha256"], "metadataSha256": p["metadataSha256"],
                    "figureId": figure_id, "label": label, "caption": caption, "graphicRef": href,
                    "sourceUrl": m["url"], "sourceMd5": m["md5"], "sourceFilename": m["name"],
                    "license": deepcopy(p["license"]), "mediaReuseBasis": "unmodified_source_media",
                    "attribution": {"authors": p.get("authors", []), "title": p.get("articleTitle"),
                        "citation": p.get("citation"), "doi": p.get("doi"), "articleUrl": p.get("articleUrl"),
                        "repository": "PubMed Central / NLM", "notice": "Fonte original preservada; sem endosso dos autores ou da NLM."},
                    "cataloguedAt": _now(), "downloadStatus": "pending", "caseBindings": [], **deepcopy(flags)}
                existing_path = self._asset_path(asset_id)
                if existing_path.exists():
                    old = self.lookup(asset_id)
                    for field in ("downloadStatus", "relativePath", "sha256", "mimeType", "byteSize", "width", "height", "downloadedAt", "privacyStatus", "privacyReview", "caseBindings", "lastDownloadError"):
                        if field in old:
                            record[field] = old[field]
                    # A newly recognized hold must never be cleared by cached approval.
                    if flags["privacyStatus"] == "selection_hold":
                        record["privacyStatus"] = "selection_hold"
                assets[asset_id] = record
            figures.append(item)
        for asset_id, record in assets.items():
            _save_json(self._asset_path(asset_id), record)
        catalog = {"schemaVersion": 1, "pmcid": pmcid, "pmcVersion": version,
            "xmlSha256": p["xmlSha256"], "metadataSha256": p["metadataSha256"],
            "provenance": deepcopy(p), "cataloguedAt": _now(), "figures": figures,
            "assetCount": len(assets), "invalidMediaReferences": invalid_media,
            "privacyNotice": "Legendas e imagens são privadas; catalogação não aprova identificação do paciente, privacidade ou publicação."}
        _save_json(self.media_dir / "catalogs" / f"{pmcid}.json", catalog)
        return catalog

    def download_asset(self, asset_id):
        record = self.lookup(asset_id)
        if record.get("rightsStatus") != "article_license" or record.get("privacyStatus") in {"selection_hold", "rejected"}:
            raise MediaError("A seleção desta figura exige revisão de direitos ou privacidade antes do download.")
        if record.get("downloadStatus") == "downloaded":
            self._read_original(record)
            return record
        official = _official_media_url(record["sourceUrl"] + "?md5=" + record["sourceMd5"], record["pmcid"], record["pmcVersion"])
        try:
            with self.session.get(official["url"], stream=True, timeout=(5, self.timeout), allow_redirects=False) as response:
                if response.status_code != 200:
                    raise MediaError("A imagem oficial não está disponível; o download pode ser retomado depois.")
                header_size = response.headers.get("Content-Length")
                if header_size is not None and (not header_size.isdigit() or int(header_size) > MAX_IMAGE_BYTES):
                    raise MediaError("Tamanho da imagem oficial não permitido.")
                parts, size = [], 0
                for chunk in response.iter_content(chunk_size=65536):
                    size += len(chunk)
                    if size > MAX_IMAGE_BYTES:
                        raise MediaError("A imagem oficial excede o limite de download.")
                    parts.append(chunk)
                raw = b"".join(parts)
            if hashlib.md5(raw).hexdigest() != official["md5"]:
                raise MediaError("O checksum da imagem diverge do snapshot oficial.")
            info = _image_info(raw)
            digest = hashlib.sha256(raw).hexdigest()
            relative = f'objects/{digest[:2]}/{digest}.{info.pop("extension")}'
            target = self.media_dir / relative
            if not target.resolve().is_relative_to(self.media_dir):
                raise MediaError("O destino da imagem saiu da pasta privada configurada.")
            _atomic(target, raw)
            record.update(info, downloadStatus="downloaded", relativePath=relative, sha256=digest,
                          byteSize=len(raw), downloadedAt=_now())
            embedded = _embedded_metadata_flags(raw)
            if embedded:
                record["privacyStatus"] = "selection_hold"
                record["selectionFlags"] = list(dict.fromkeys(record.get("selectionFlags", []) + embedded))
            record.pop("lastDownloadError", None)
            _save_json(self._asset_path(asset_id), record)
            return record
        except (requests.RequestException, MediaError, ValueError) as exc:
            message = str(exc) if isinstance(exc, MediaError) else "Falha de rede ao obter a imagem oficial."
            record.update(downloadStatus="error", lastDownloadError=message)
            _save_json(self._asset_path(asset_id), record)
            raise MediaError(message) from None

    def _read_original(self, record):
        relative = record.get("relativePath", "")
        if not isinstance(relative, str) or not _OBJECT.fullmatch(relative):
            raise MediaError("Caminho privado da imagem inválido.")
        path = self.media_dir / relative
        try:
            if path.is_symlink() or not path.resolve().is_relative_to(self.media_dir) or path.stat().st_size > MAX_IMAGE_BYTES:
                raise ValueError
            raw = path.read_bytes()
            info = _image_info(raw)
            if len(raw) != record.get("byteSize") or hashlib.sha256(raw).hexdigest() != record.get("sha256") or hashlib.md5(raw).hexdigest() != record.get("sourceMd5") or any(info[k] != record.get(k) for k in ("mimeType", "width", "height")):
                raise ValueError
            return raw
        except (OSError, ValueError):
            raise MediaError("A imagem privada não passou na verificação de integridade.") from None

    def review_privacy(self, asset_id, *, approved, reviewer, note, review_mode="assisted", machine_evidence=None):
        """Record explicit visual selection; never edits/crops the source image."""
        if type(approved) is not bool or not isinstance(reviewer, str) or not reviewer.strip() or not isinstance(note, str) or not note.strip():
            raise MediaError("A revisão visual precisa de responsável e justificativa.")
        if review_mode not in {"assisted", "human", "machine"}:
            raise MediaError("Modo de revisão inválido.")
        if review_mode == "machine" and approved:
            required = {"samePatient", "sameMoment", "sameExam", "neutralCaption", "imageShowsExam", "fullyInspectable", "noVisibleIdentifiers", "noRecognizableFace", "noDiagnosticLabels", "independentPrivacyReview"}
            if not isinstance(machine_evidence, dict) or any(machine_evidence.get(key) is not True for key in required):
                raise MediaError("A revisão automática não confirmou todos os critérios.")
        record = self.lookup(asset_id)
        if approved:
            if record.get("rightsStatus") != "article_license" or record.get("downloadStatus") != "downloaded":
                raise MediaError("A imagem não está disponível para aprovação visual.")
            original = self._read_original(record)
            if review_mode == "machine" and (record.get("privacyStatus") == "selection_hold" or _embedded_metadata_flags(original)):
                raise MediaError("Metadados ou marcações retidos exigem revisão assistida; a máquina não pode liberá-los.")
        record["privacyStatus"] = ("machine_reviewed" if review_mode == "machine" else "approved") if approved else "rejected"
        record["privacyReview"] = {"reviewer": reviewer[:200], "note": note[:1000], "reviewedAt": _now(), "reviewMode": review_mode}
        if review_mode == "machine":
            record["privacyReview"]["checks"] = deepcopy(machine_evidence)
        _save_json(self._asset_path(asset_id), record)
        return record

    def bind_asset(self, asset_id, *, case_id, exam_name, group, caption_pt, patient_ref,
                   patient_evidence, reviewer, approved=False, review_mode="assisted"):
        if review_mode not in {"assisted", "human", "machine"}:
            raise MediaError("Modo de revisão inválido.")
        values = (case_id, exam_name, caption_pt, patient_ref, patient_evidence, reviewer)
        if any(not isinstance(v, str) or not v.strip() or len(v) > 2000 for v in values) or group not in _GROUPS or type(approved) is not bool:
            raise MediaError("O vínculo da figura exige identificação do paciente, exame, legenda e revisão.")
        record = self.lookup(asset_id)
        if case_id != "mcr-" + record["pmcid"].lower():
            raise MediaError("A figura não pertence à fonte do caso.")
        if approved and (record.get("privacyStatus") not in {"approved", "machine_reviewed"} or record.get("downloadStatus") != "downloaded"):
            raise MediaError("A figura precisa de revisão visual antes da aprovação do vínculo.")
        binding = {"caseId": case_id, "examName": exam_name, "group": group, "captionPt": caption_pt,
                   "patientRef": patient_ref, "patientEvidence": patient_evidence,
                   "reviewer": reviewer, "status": ("machine_reviewed" if review_mode == "machine" else "approved") if approved else "pending", "reviewedAt": _now(), "reviewMode": review_mode}
        others = [b for b in record.get("caseBindings", []) if (b.get("caseId"), b.get("examName"), b.get("group")) != (case_id, exam_name, group)]
        record["caseBindings"] = others + [binding]
        _save_json(self._asset_path(asset_id), record)
        return binding

    def read_media_asset(self, asset_id, *, pmcid, xml_sha256, case_id, exam_name, group, caption_pt):
        record = self.lookup(asset_id)
        if record["pmcid"] != pmcid or record["xmlSha256"] != xml_sha256 or record.get("downloadStatus") != "downloaded" or record.get("privacyStatus") not in {"approved", "machine_reviewed"} or record.get("rightsStatus") != "article_license":
            raise MediaError("Imagem indisponível para este caso ou ainda em revisão.")
        if not any(b.get("status") in {"approved", "machine_reviewed"} and all(b.get(k) == v for k, v in {
            "caseId": case_id, "examName": exam_name, "group": group, "captionPt": caption_pt}.items())
            for b in record.get("caseBindings", [])):
            raise MediaError("A imagem não tem vínculo revisado com este paciente, exame e legenda.")
        return {"data": self._read_original(record), "mime_type": record["mimeType"], "metadata": record}

    def inventory(self, pmcids, *, on_progress=None):
        """Complete local inventory; caller supplies the active source allowlist."""
        counts = Counter(sources=0, figures=0, assets=0, noFigures=0, unmatchedGraphics=0,
                         privacyHolds=0, rightsHolds=0, errors=0)
        failures = []
        for pmcid in sorted(set(pmcids)):
            try:
                catalog = self.catalog_source(pmcid)
                counts["sources"] += 1
                counts["figures"] += len(catalog["figures"])
                counts["assets"] += catalog["assetCount"]
                counts["noFigures"] += not catalog["figures"]
                for fig in catalog["figures"]:
                    counts["unmatchedGraphics"] += len(fig["unmatchedGraphics"])
                    counts["privacyHolds"] += fig["privacyStatus"] == "selection_hold"
                    counts["rightsHolds"] += fig["rightsStatus"] != "article_license"
            except MediaError as exc:
                counts["errors"] += 1
                failures.append({"pmcid": pmcid, "reason": str(exc)})
            if on_progress:
                on_progress(dict(counts))
        report = {"schemaVersion": 1, "completedAt": _now(), "counts": dict(counts), "failures": failures,
                  "notes": ["Catálogo privado; nenhuma imagem é publicada ou aprovada automaticamente.",
                            "Direitos específicos, identificação visual e vínculo ao paciente/exame exigem revisão."]}
        _save_json(self.media_dir / "inventory.json", report)
        return report
