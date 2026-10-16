#!/usr/bin/env python3
"""Cache and inventory the official MedCaseReasoning CSV without adapting cases.

No application DB, AI key, pandas or pyarrow is required. The raw corpus and all
acquisition evidence remain under instance/, outside Git and the release ZIP.
"""

from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

import requests


ROOT = Path(__file__).resolve().parents[1]
DATASET = "zou-lab/MedCaseReasoning"
DEFAULT_REVISION = "469a5365bc534b5b2b9cfbc52b2ef2a10f43a339"
FILENAME = "medcasereasoning_core.csv"


def save_json(path: Path, data: object) -> None:
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def json_response(session: requests.Session, url: str) -> object:
    response = session.get(url, timeout=(15, 90))
    response.raise_for_status()
    return response.json()


def inventory(path: Path) -> dict:
    csv.field_size_limit(16 * 1024 * 1024)
    journals: Counter = Counter()
    years: Counter = Counter()
    missing: Counter = Counter()
    pmcids: Counter = Counter()
    row_ids: Counter = Counter()
    diagnoses: Counter = Counter()
    splits: Counter = Counter()
    total = 0
    with path.open(encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        fields = reader.fieldnames or []
        for row in reader:
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"Malformed CSV record {total + 1}")
            total += 1
            for field in fields:
                if not row[field].strip():
                    missing[field] += 1
            pmcids[row.get("pmcid", "").strip()] += 1
            row_ids[row.get("Unnamed: 0", row.get("", "")).strip()] += 1
            journals[row.get("journal", "").strip()] += 1
            diagnoses[row.get("final_diagnosis", "").strip()] += 1
            if "split" in row:
                splits[row["split"].strip()] += 1
            year = re.search(r"\b(?:19|20)\d{2}\b", row.get("publication_date", ""))
            years[year.group() if year else "unknown"] += 1
    specialty_fields = [field for field in fields if "special" in field.casefold()]
    return {
        "records": total,
        "fields": fields,
        "original_index_field": "Unnamed: 0" if "Unnamed: 0" in fields else "" if "" in fields else None,
        "missing_values_by_field": {field: missing[field] for field in fields},
        "distinct_pmcids": len(pmcids),
        "duplicate_pmcids": {key: value for key, value in pmcids.items() if value > 1},
        "distinct_original_row_ids": len(row_ids),
        "duplicate_original_row_ids": {key: value for key, value in row_ids.items() if value > 1},
        "distinct_journals": len(journals),
        "records_by_journal": dict(journals.most_common()),
        "records_by_publication_year": dict(sorted(years.items())),
        "distinct_final_diagnosis_strings": len(diagnoses),
        "records_by_split": dict(splits),
        "specialty_fields": specialty_fields,
        "official_specialty_count": None,
        "official_specialty_distribution": None,
        "specialty_note": "The CSV has no specialty labels; journal or diagnosis counts are not specialty counts. No heuristic classification has been performed.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, default=ROOT / "instance/imports/MedCaseReasoning")
    parser.add_argument("--revision", default=DEFAULT_REVISION)
    args = parser.parse_args()
    cache = args.cache_dir.resolve()
    cache.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers["User-Agent"] = "MED-LAB-source-inventory/1.0"
    metadata_url = f"https://huggingface.co/api/datasets/{DATASET}/revision/{args.revision}"
    metadata = json_response(session, metadata_url)
    tree_url = f"https://huggingface.co/api/datasets/{DATASET}/tree/{args.revision}?recursive=true"
    tree = json_response(session, tree_url)
    file_info = next(item for item in tree if item["path"] == FILENAME)
    expected_sha256 = file_info["lfs"]["oid"]
    expected_size = file_info["size"]
    destination = cache / FILENAME
    source_url = f"https://huggingface.co/datasets/{DATASET}/resolve/{args.revision}/{FILENAME}"
    fetched_at = datetime.now(timezone.utc).isoformat()
    save_json(cache / "hf_metadata.json", metadata)
    save_json(cache / "hf_files.json", tree)
    verified_cache = destination.exists() and destination.stat().st_size == expected_size and sha256(destination) == expected_sha256
    acquisition_file = cache / "acquisition.json"
    if not verified_cache:
        temporary = destination.with_suffix(destination.suffix + ".part")
        digest = hashlib.sha256()
        downloaded = 0
        last_report = 0
        with session.get(source_url, stream=True, timeout=(15, 120)) as response:
            response.raise_for_status()
            headers = {key: response.headers.get(key) for key in ("ETag", "Last-Modified", "Content-Length")}
            with temporary.open("wb") as output:
                for block in response.iter_content(chunk_size=1024 * 1024):
                    if not block:
                        continue
                    output.write(block)
                    digest.update(block)
                    downloaded += len(block)
                    if downloaded - last_report >= 32 * 1024 * 1024:
                        print(f"Downloaded {downloaded:,}/{expected_size:,} bytes", flush=True)
                        last_report = downloaded
        if downloaded != expected_size or digest.hexdigest() != expected_sha256:
            raise ValueError("The downloaded CSV size or SHA-256 does not match the official repository")
        temporary.replace(destination)
        save_json(acquisition_file, {"downloaded_at_utc": fetched_at, "source_url": source_url, "response_headers": headers})
    else:
        print("Verified existing CSV; download skipped", flush=True)
    results = inventory(destination)
    card = metadata.get("cardData", {})
    info = card.get("dataset_info", {})
    if isinstance(info, list):
        info = info[0]
    splits = {item["name"]: item["num_examples"] for item in info.get("splits", [])}
    declared_records = sum(splits.values()) if splits else None
    results.update({
        "dataset": DATASET,
        "revision": args.revision,
        "source_url": source_url,
        "metadata_url": metadata_url,
        "inventory_created_at_utc": datetime.now(timezone.utc).isoformat(),
        "file": str(destination),
        "size_bytes": destination.stat().st_size,
        "sha256": expected_sha256,
        "checksum_verified_against": tree_url,
        "declared_splits": splits,
        "declared_records": declared_records,
        "declared_record_count_matches": declared_records == results["records"] if declared_records else None,
        "license_declarations": {
            "hugging_face_card": card.get("license"),
            "authors_repository_dataset": "CC BY 4.0",
            "authors_repository_url": "https://github.com/kevinwu23/Stanford-MedCaseReasoning",
            "article_level_audit": "Not performed for the complete corpus. Dataset declarations do not replace individual article licenses.",
        },
        "authors_specialty_claim": "30+ (README); not verifiable as a distribution from the CSV because it contains no specialty labels",
        "processing_status": "raw_source_only",
        "portuguese_adaptations_created_by_this_script": 0,
        "clinical_reviews_performed_by_this_script": 0,
        "application_cases_created_by_this_script": 0,
    })
    save_json(cache / "inventory.json", results)
    print(json.dumps({key: results[key] for key in ("file", "revision", "records", "size_bytes", "sha256", "distinct_pmcids", "distinct_journals", "specialty_fields", "declared_record_count_matches")}, ensure_ascii=False, indent=2))
    if declared_records and declared_records != results["records"]:
        raise SystemExit("CSV record count differs from dataset card; inspect inventory.json")


if __name__ == "__main__":
    main()
