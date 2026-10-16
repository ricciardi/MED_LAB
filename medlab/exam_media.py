"""Private, case-bound figures. Only the consultation snapshot grants access."""
from pathlib import Path
import re

from flask import current_app, url_for

from .case_schema import license_code_from_url


ASSET_ID_PATTERN = r"pmcmedia-[0-9a-f]{32}"
EXAM_GROUPS = {"physical": ("physicalExams", "physical_exam"),
               "auxiliary": ("auxiliaryLabs", "auxiliary_exam")}


def get_media_store():
    from .source_media import SourceMediaStore
    directory = current_app.config.get("CASE_MEDIA_DIR") or (
        Path(current_app.instance_path) / "imports" / "MedCaseReasoning" / "media")
    return SourceMediaStore(Path(directory))


def snapshot_media(case):
    """Reject malformed legacy or manually edited bindings without leaking them."""
    if not isinstance(case, dict) or case.get("catalogType") != "published_case":
        return []
    provenance = case.get("provenance")
    if not isinstance(provenance, dict) or not isinstance(provenance.get("pmcid"), str):
        return []
    entries = case.get("examMedia", [])
    if not isinstance(entries, list) or len(entries) > 12:
        return []
    valid = []
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"examName", "group", "assetId", "captionPt"}:
            continue
        if not isinstance(entry["group"], str) or entry["group"] not in EXAM_GROUPS:
            continue
        key, _ = EXAM_GROUPS[entry["group"]]
        if not isinstance(entry["examName"], str) or entry["examName"] not in case.get(key, {}):
            continue
        if not isinstance(entry["assetId"], str) or not re.fullmatch(ASSET_ID_PATTERN, entry["assetId"]):
            continue
        if not isinstance(entry["captionPt"], str) or not 1 <= len(entry["captionPt"].strip()) <= 800:
            continue
        valid.append(entry)
    return valid


def _approved_metadata(store, case, entry):
    """A same-article match alone does not establish the correct patient."""
    from .source_media import SourceMediaError
    try:
        metadata = store.lookup(entry["assetId"])
    except (SourceMediaError, OSError, ValueError, TypeError, KeyError):
        return None
    if not isinstance(metadata, dict):
        return None
    source = case["provenance"]
    expected = {"assetId": entry["assetId"], "pmcid": source["pmcid"],
                "xmlSha256": source.get("xmlSha256"), "metadataSha256": source.get("metadataSha256"),
                "pmcVersion": source.get("pmcVersion"), "downloadStatus": "downloaded",
                "rightsStatus": "article_license"}
    if any(metadata.get(key) != value for key, value in expected.items()):
        return None
    if metadata.get("privacyStatus") not in {"approved", "machine_reviewed"}:
        return None
    license_info, attribution = metadata.get("license"), metadata.get("attribution")
    if not isinstance(license_info, dict) or not isinstance(attribution, dict):
        return None
    if not license_info.get("code") or license_code_from_url(license_info.get("url"), include_nd=True) != license_info["code"]:
        return None
    if any(license_info.get(key) != source.get("license", {}).get(key) for key in ("code", "url")):
        return None
    authors = attribution.get("authors", [])
    if not isinstance(authors, list) or any(not isinstance(author, str) for author in authors):
        return None
    bindings = metadata.get("caseBindings", [])
    if not isinstance(bindings, list):
        return None
    for binding in bindings:
        if not isinstance(binding, dict):
            continue
        required = {"caseId": case.get("caseId"), "examName": entry["examName"],
                    "group": entry["group"], "captionPt": entry["captionPt"]}
        if binding.get("status") in {"approved", "machine_reviewed"} and all(binding.get(key) == value for key, value in required.items()) and all(
                isinstance(binding.get(key), str) and binding[key].strip()
                for key in ("patientRef", "patientEvidence", "reviewer", "reviewedAt")):
            return {**metadata, "selectionReviewMode": "machine" if "machine_reviewed" in
                    {metadata.get("privacyStatus"), binding.get("status")} else "reviewed"}
    return None


def accessible_entry(training, asset_id):
    performed = {(exam.exam_type, exam.name) for exam in training.exams}
    for entry in snapshot_media(training.case_snapshot):
        if entry["assetId"] != asset_id:
            continue
        if training.status == "completed" or (EXAM_GROUPS[entry["group"]][1], entry["examName"]) in performed:
            return entry
    return None


def exam_media_items(training, *, exam_name=None, exam_type=None, include_all=False):
    """Return only approved display fields; never expose original figure captions."""
    entries = snapshot_media(training.case_snapshot)
    if not entries:
        return []
    performed = {(exam.exam_type, exam.name) for exam in training.exams}
    store = get_media_store()
    items = []
    for entry in entries:
        kind = EXAM_GROUPS[entry["group"]][1]
        if exam_name is not None and (entry["examName"] != exam_name or kind != exam_type):
            continue
        requested = (kind, entry["examName"]) in performed
        if not requested and not (include_all and training.status == "completed"):
            continue
        metadata = _approved_metadata(store, training.case_snapshot, entry)
        if metadata is None:
            continue
        source = training.case_snapshot["provenance"]
        license_info = metadata.get("license", {})
        attribution = metadata.get("attribution", {})
        if not isinstance(license_info, dict) or not isinstance(attribution, dict):
            continue
        item = {"url": url_for("web.exam_image", session_id=training.id, asset_id=entry["assetId"]),
                "caption": entry["captionPt"], "exam_name": entry["examName"], "requested": requested,
                "credit": ", ".join(attribution.get("authors", source.get("authors", []))),
                "license_code": license_info.get("code", ""), "license_url": license_info.get("url", ""),
                "width": metadata.get("width"), "height": metadata.get("height")}
        item["review_mode"] = metadata["selectionReviewMode"]
        if training.status == "completed":
            item.update(source_url=source.get("articleUrl", ""), source_title=source.get("articleTitle", ""),
                        attribution=attribution.get("citation", ""), attribution_notice=attribution.get("notice", ""),
                        license_text=license_info.get("text", ""))
        items.append(item)
    return items


def read_exam_media(training, asset_id):
    """Check authorization before even looking up an asset on disk."""
    entry = accessible_entry(training, asset_id)
    if entry is None:
        return None
    store = get_media_store()
    if _approved_metadata(store, training.case_snapshot, entry) is None:
        return None
    from .source_media import SourceMediaError
    source = training.case_snapshot["provenance"]
    try:
        return store.read_media_asset(asset_id, pmcid=source["pmcid"], xml_sha256=source["xmlSha256"],
            case_id=training.case_snapshot["caseId"], exam_name=entry["examName"],
            group=entry["group"], caption_pt=entry["captionPt"])
    except (SourceMediaError, OSError, ValueError, KeyError, TypeError):
        return None
