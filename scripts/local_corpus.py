#!/usr/bin/env python3
"""Prepare every registered case with local extraction and translation only.

The translation worker runs separately in its own environment. This command
never calls an LLM provider, never loads API credentials and never publishes
untranslated material. Each completed case and stage survives interruption.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DEFAULT_SOURCE = ROOT / "instance/imports/MedCaseReasoning/medcasereasoning_core.csv"
DEFAULT_PROCESSED = ROOT / "instance/imports/MedCaseReasoning/processed"
DEFAULT_OUTPUT = ROOT / "instance/local-preparation/corpus"
PMCID = re.compile(r"PMC[1-9][0-9]{0,11}\Z")


def now():
    return datetime.now(timezone.utc).isoformat()


def checksum(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def text_id(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def save_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, allow_nan=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def corpus_rows(source, excluded):
    seen = set()
    with Path(source).open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            identifier = row.get("pmcid", "").strip()
            if not PMCID.fullmatch(identifier) or identifier in seen:
                raise ValueError("Fonte com identificador inválido ou duplicado.")
            seen.add(identifier)
            if identifier not in excluded:
                yield row


def plan(source, processed, output):
    processed, output = Path(processed), Path(output)
    manifest = json.loads((processed / "manifest.json").read_text())
    source_sha = checksum(source)
    if source_sha != manifest.get("sourceSha256"):
        raise ValueError("O CSV mudou em relação à aquisição registrada.")
    excluded = set(manifest.get("excludedPmcids", []))
    active, existing, missing = [], [], []
    for row in corpus_rows(source, excluded):
        identifier = row["pmcid"]
        active.append(identifier)
        if not (processed / "pmc-cache" / (identifier + ".verified.json")).is_file():
            missing.append(identifier)
        if (processed / "drafts" / (identifier + ".json")).is_file():
            existing.append(identifier)
    value = {"format": "medlab-local-corpus-v1", "createdAt": now(),
             "sourceSha256": source_sha, "sourcePath": str(Path(source).resolve()),
             "processedPath": str(processed.resolve()), "activeIds": active,
             "excludedIds": sorted(excluded), "preservedIds": existing,
             "pendingIds": [identifier for identifier in active if identifier not in set(existing)],
             "missingSourceIds": missing, "providerCalls": 0,
             "clinicalReviewDeferred": True}
    previous = output / "plan.json"
    if previous.exists():
        original = json.loads(previous.read_text())
        if original["sourceSha256"] != source_sha or original["activeIds"] != active:
            raise ValueError("Use outra pasta para uma aquisição ou conjunto de fontes diferente.")
        return original
    save_json(previous, value)
    return value


def extract(source, processed, output, *, limit=None):
    import medlab.local_case_preparation as preparation
    from medlab.local_case_preparation import extract_case_record
    work = plan(source, processed, output)
    output, processed = Path(output), Path(processed)
    pending = set(work["pendingIds"])
    counters = Counter()
    errors = []
    extractor_sha = checksum(preparation.__file__)
    for row in corpus_rows(source, set(work["excludedIds"])):
        identifier = row["pmcid"]
        if identifier not in pending:
            counters["preserved"] += 1
            continue
        destination = output / "extracted" / (identifier + ".json")
        row_sha = text_id(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        if destination.exists():
            previous = json.loads(destination.read_text())
            if previous.get("orchestration") == {"extractorCodeSha256": extractor_sha, "sourceRowSha256": row_sha}:
                counters["reused"] += 1
                continue
        if limit is not None and counters["attempted"] >= limit:
            break
        counters["attempted"] += 1
        try:
            verified = json.loads((processed / "pmc-cache" / (identifier + ".verified.json")).read_text())
            provenance = verified["provenance"]
            xml_path = processed / "pmc-cache" / f"{identifier}.{provenance['pmcVersion']}.{provenance['xmlMd5']}.xml"
            result = extract_case_record(row, verified, xml_bytes=xml_path.read_bytes())
            result["orchestration"] = {"extractorCodeSha256": extractor_sha, "sourceRowSha256": row_sha}
            if result.get("pmcid") != identifier:
                raise ValueError("O extrator mudou a identidade da fonte.")
            units = result.get("translationUnits")
            if not isinstance(units, list) or not units:
                raise ValueError("O extrator não forneceu unidades de tradução.")
            if len({item["id"] for item in units}) != len(units):
                raise ValueError("Identificador de tradução duplicado.")
            save_json(destination, result)
            counters["extracted"] += 1
        except Exception as error:
            errors.append({"pmcid": identifier, "type": type(error).__name__, "message": str(error)[:300]})
            counters["errors"] += 1
        if counters["attempted"] % 100 == 0:
            print(json.dumps({"stage": "extract", **counters}), flush=True)
            save_json(output / "extract-report.json", {"updatedAt": now(), **counters, "errors": errors, "providerCalls": 0})
    report = {"updatedAt": now(), **counters, "errors": errors, "providerCalls": 0}
    save_json(output / "extract-report.json", report)
    return report


def package_paths(output, pmcids=None):
    """Use the frozen active plan, never residual excluded packages."""
    output = Path(output)
    plan_path = output / "plan.json"
    active = set(json.loads(plan_path.read_text())["pendingIds"]) if plan_path.exists() else None
    if pmcids is not None:
        selected = set(pmcids)
        if any(not PMCID.fullmatch(identifier) for identifier in selected) or (active is not None and not selected <= active):
            raise ValueError("Pacote fora do plano ativo.")
    else:
        selected = active
    if selected is not None:
        return [output / "extracted" / (identifier + ".json") for identifier in sorted(selected)]
    return sorted((output / "extracted").glob("PMC*.json"))


def translation_queue(output, *, destination=None, prior_queues=()):
    output = Path(output)
    seen, count, characters = set(), 0, 0
    destination = Path(destination) if destination is not None else output / "translation-queue.jsonl"
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    for previous in prior_queues:
        if Path(previous).resolve() == destination.resolve():
            raise ValueError("A fila adicional não pode substituir uma fila já utilizada.")
        with Path(previous).open(encoding="utf-8") as handle:
            for line in handle:
                item = json.loads(line)
                if item.get("id") != text_id(item["text"]):
                    raise ValueError("Identidade inválida na fila anterior.")
                seen.add(item["id"])
    descriptor, temporary = tempfile.mkstemp(prefix=".queue-", dir=destination.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            for path in package_paths(output):
                package = json.loads(path.read_text())
                for unit in package["translationUnits"]:
                    text = unit["text"]
                    if not isinstance(text, str) or not text.strip():
                        raise ValueError("Unidade de tradução vazia.")
                    identifier = text_id(text)
                    if identifier in seen:
                        continue
                    seen.add(identifier)
                    handle.write(json.dumps({"id": identifier, "text": text, "language": "en"}, ensure_ascii=False) + "\n")
                    count += 1
                    characters += len(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        destination.chmod(0o600)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    report = {"units": count, "characters": characters, "sha256": checksum(destination), "providerCalls": 0}
    save_json(destination.with_name(destination.stem + "-report.json"), report)
    return report


def post_edit_record(item):
    """Validate an explicitly source-checked assistant correction, not clinical review."""
    edited = item.get("translation", {}).get("postEditing")
    if edited is None:
        return None
    expected = {"method", "actor", "sourceSha256", "translationSha256", "numbersAndUnitsChecked"}
    if (not isinstance(edited, dict) or set(edited) != expected
            or edited["method"] != "assistant_post_edit" or edited["actor"] != "Codex"
            or edited["sourceSha256"] != text_id(item["text"])
            or edited["translationSha256"] != text_id(item["textPt"])
            or edited["numbersAndUnitsChecked"] is not True):
        raise ValueError("Correção assistida sem identidade ou conferência válida.")
    return {key: edited[key] for key in ("sourceSha256", "translationSha256")}


def case_translation_metadata(package, metadata):
    metadata = metadata or {}
    result = {key: value for key, value in metadata.items() if key != "_post_edits"}
    selected = {text_id(unit["text"]) for unit in package["translationUnits"]}
    edits = [value for key, value in sorted(metadata.get("_post_edits", {}).items()) if key in selected]
    if edits:
        result["postEditing"] = {"method": "assistant_post_edit", "actor": "Codex", "units": edits}
    return result


def translated_units(path):
    from scripts.local_translate_cases import needs_recovery
    values, priorities, edits, metadata = {}, {}, {}, None
    paths = path if isinstance(path, (list, tuple)) else [path]
    for current_path in paths:
        with Path(current_path).open(encoding="utf-8") as handle:
            for line in handle:
                # The translator appends complete JSONL records while the builder
                # may be reading its journal. Never consume an unfinished record.
                if not line.endswith("\n") and ".partial." in Path(current_path).name:
                    break
                if not line.strip():
                    continue
                item = json.loads(line)
                if item.get("translationError"):
                    continue
                if item["id"] != text_id(item["text"]) or not isinstance(item.get("textPt"), str) or not item["textPt"].strip():
                    raise ValueError("A tradução não corresponde à unidade original.")
                edited = post_edit_record(item)
                if edited is None and needs_recovery(item):
                    continue
                current = item.get("translation", {})
                if not isinstance(current, dict) or not current.get("model") or not current.get("revision"):
                    raise ValueError("A tradução não registra modelo e revisão.")
                identity = {key: current[key] for key in ("model", "revision", "target", "backend", "device", "beamSize") if key in current}
                if metadata is not None and metadata != identity:
                    raise ValueError("O lote mistura configurações de tradução.")
                metadata = identity
                priority = 2 if edited else int(isinstance(item.get("recoverySource"), dict))
                if item["id"] in values and values[item["id"]] != item["textPt"]:
                    if priorities[item["id"]] == priority:
                        raise ValueError("Traduções conflitantes da mesma unidade.")
                    if priorities[item["id"]] > priority:
                        continue
                values[item["id"]] = item["textPt"]
                priorities[item["id"]] = priority
                if edited:
                    edits[item["id"]] = edited
    if edits:
        metadata = {**metadata, "_post_edits": edits}
    return values, metadata


def build(output, translations=None, *, authorization_reference=None, pmcids=None, translated_cache=None):
    import medlab.local_case_integration as integration
    from medlab.local_case_integration import assemble_local_case
    from medlab.case_schema import validate_case
    output = Path(output)
    values, metadata = translated_cache if translated_cache is not None else translated_units(translations)
    assembler_sha = checksum(integration.__file__)
    counters, errors, completed = Counter(), [], []
    for path in package_paths(output, pmcids):
        package = json.loads(path.read_text())
        destination = output / "cases" / path.name
        if any(text_id(unit["text"]) not in values for unit in package["translationUnits"]):
            counters["awaitingTranslation"] += 1
            continue
        local = {unit["id"]: values[text_id(unit["text"])] for unit in package["translationUnits"]}
        case_metadata = case_translation_metadata(package, metadata)
        identity = {"packageSha256": checksum(path), "assemblerCodeSha256": assembler_sha,
                    "translationsSha256": text_id(json.dumps(local, ensure_ascii=False, sort_keys=True)),
                    "translationMetadata": case_metadata, "authorizationReference": authorization_reference}
        state_path = output / "build-state" / path.name
        if destination.exists() and state_path.exists():
            state = json.loads(state_path.read_text())
            if state.get("inputs") == identity and state.get("caseSha256") == checksum(destination):
                counters["reused"] += 1
                completed.append(path.stem)
                continue
        try:
            case = validate_case(assemble_local_case(package, local, case_metadata,
                accepted_for_use=bool(authorization_reference), authorization_reference=authorization_reference))
            if destination.exists():
                previous = validate_case(json.loads(destination.read_text()))
                if previous.get("caseId") == case["caseId"] and previous["provenance"].get("localPreparation", {}).get("packageSha256") == case["provenance"]["localPreparation"]["packageSha256"]:
                    case["provenance"]["adaptedAt"] = previous["provenance"]["adaptedAt"]
                    authorization = previous.get("useAuthorization", {})
                    if authorization.get("authorizationReference") == authorization_reference:
                        case["useAuthorization"] = authorization
            if case["provenance"]["pmcid"] != path.stem or case["provenance"]["language"] != "pt-BR":
                raise ValueError("A ficha não corresponde à fonte em português.")
            save_json(destination, case)
            save_json(state_path, {"inputs": identity, "caseSha256": checksum(destination)})
            counters["built"] += 1
            completed.append(path.stem)
        except Exception as error:
            counters["errors"] += 1
            errors.append({"pmcid": path.stem, "type": type(error).__name__, "message": str(error)[:300]})
        if (counters["built"] + counters["errors"]) % 100 == 0:
            print(json.dumps({"stage": "build", **counters}), flush=True)
    report = {"updatedAt": now(), **counters, "errors": errors, "completedIds": completed, "providerCalls": 0}
    save_json(output / "build-report.json", report)
    return report


def audit(output, processed):
    """Check every planned identity and contract; this is not clinical review."""
    from medlab.case_schema import UNKNOWN_FACT, is_provisionally_accepted, validate_case
    output, processed = Path(output), Path(processed)
    work = json.loads((output / "plan.json").read_text())
    counts, specialties, kinds, errors, missing, hashes = Counter(), Counter(), Counter(), [], [], {}
    preserved = set(work["preservedIds"])
    for identifier in work["activeIds"]:
        path = (processed / "drafts" if identifier in preserved else output / "cases") / (identifier + ".json")
        if not path.is_file():
            missing.append(identifier)
            continue
        try:
            case = validate_case(json.loads(path.read_text()))
            origin = case["provenance"]
            if (case["caseId"] != "mcr-" + identifier.lower() or origin["pmcid"] != identifier
                    or origin["language"] != "pt-BR" or case["catalogType"] != "published_case"):
                raise ValueError("Identidade, idioma ou tipo da ficha divergente.")
            if identifier in preserved:
                counts["preserved"] += 1
            else:
                counts["locallyPrepared"] += 1
                if origin.get("localPreparation", {}).get("translated") is not True:
                    raise ValueError("Tradução local não concluída.")
            counts["valid"] += 1
            counts["authorizedForUse"] += int(is_provisionally_accepted(case))
            counts["withPatientPresentation"] += int(case["patientProfile"]["present_illness_history"] != UNKNOWN_FACT)
            counts["withPhysicalExams"] += int(bool(case["physicalExams"]))
            counts["withAuxiliaryExams"] += int(bool(case["auxiliaryLabs"]))
            counts["withReferenceReasoning"] += int(bool(case.get("referenceReasoning")))
            counts["withKnownGender"] += int(case["patientProfile"]["gender"] != UNKNOWN_FACT)
            specialties[case["specialty"]] += 1
            kinds[case["caseType"]] += 1
            hashes[identifier] = checksum(path)
        except Exception as error:
            errors.append({"pmcid": identifier, "type": type(error).__name__, "message": str(error)[:300]})
    report = {"updatedAt": now(), "expected": len(work["activeIds"]), **counts,
              "complete": not missing and not errors and counts["valid"] == len(work["activeIds"]),
              "missingIds": missing, "errors": errors, "specialties": dict(specialties.most_common()),
              "caseTypes": dict(kinds.most_common()), "clinicalReviewPerformed": False, "providerCalls": 0}
    save_json(output / "audit-report.json", report)
    save_json(output / "case-checksums.json", hashes)
    return {key: value for key, value in report.items() if key not in {"missingIds", "specialties", "caseTypes"}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "extract", "queue", "build", "audit"))
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--processed", type=Path, default=DEFAULT_PROCESSED)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--translations", type=Path, action="append", help="Pode repetir para lotes adicionais e recuperados.")
    parser.add_argument("--queue-output", type=Path)
    parser.add_argument("--already-queued", type=Path, action="append", default=[])
    parser.add_argument("--limit", type=int)
    parser.add_argument("--authorization-reference", help="Solicitação explícita para uso com revisão clínica posterior.")
    arguments = parser.parse_args(argv)
    if arguments.command == "plan":
        value = plan(arguments.source, arguments.processed, arguments.output)
        report = {key: len(value[key]) for key in ("activeIds", "excludedIds", "preservedIds", "pendingIds", "missingSourceIds")}
    elif arguments.command == "extract":
        report = extract(arguments.source, arguments.processed, arguments.output, limit=arguments.limit)
    elif arguments.command == "queue":
        report = translation_queue(arguments.output, destination=arguments.queue_output, prior_queues=arguments.already_queued)
    elif arguments.command == "audit":
        report = audit(arguments.output, arguments.processed)
    else:
        report = build(arguments.output, arguments.translations or arguments.output / "translations.jsonl",
                       authorization_reference=arguments.authorization_reference)
    print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
