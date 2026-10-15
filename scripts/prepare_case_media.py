#!/usr/bin/env python3
"""Catalog verified PMC figures privately; optional bounded original downloads."""
import argparse
from collections import Counter
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from medlab.source_media import SourceMediaError, SourceMediaStore


def active_pmcids(path):
    active, excluded = set(), set()
    with Path(path).open(encoding="utf-8") as handle:
        header = json.loads(next(handle))
        if not isinstance(header, dict) or header.get("metadataOnly") is not True or header.get("version") not in (1, 2):
            raise ValueError("Use uma exportação privada de inventário válida.")
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            pmcid = row.get("pmcid")
            if row.get("dataset") != "MedCaseReasoning" or not isinstance(pmcid, str) or not re.fullmatch(r"PMC[1-9][0-9]{0,11}", pmcid):
                raise ValueError("O inventário contém uma identidade inválida.")
            (excluded if row.get("type") == "exclusion" else active).add(pmcid)
    return active - excluded


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, default=ROOT / "instance/exports/source_registry.jsonl")
    parser.add_argument("--cache-dir", type=Path, default=ROOT / "instance/imports/MedCaseReasoning/processed/pmc-cache")
    parser.add_argument("--media-dir", type=Path, default=ROOT / "instance/imports/MedCaseReasoning/media")
    parser.add_argument("--pmcid", action="append", help="Restringir a fontes ativas específicas; repetir para selecionar várias.")
    parser.add_argument("--download-limit", type=int, default=0, help="Máximo de novos arquivos a tentar obter; zero apenas cataloga. Não aprova imagens.")
    args = parser.parse_args()
    if not 0 <= args.download_limit <= 100000:
        parser.error("--download-limit deve estar entre 0 e 100000.")
    try:
        active = active_pmcids(args.registry)
        if args.pmcid:
            if not set(args.pmcid) <= active:
                raise ValueError("Uma fonte solicitada está ausente ou foi excluída.")
            active = set(args.pmcid)
        store = SourceMediaStore(args.media_dir, cache_dir=args.cache_dir)
        reported = -1
        def progress(counts):
            nonlocal reported
            done = counts["sources"] + counts["errors"]
            if done // 500 > reported:
                reported = done // 500
                print(f"Fontes catalogadas: {done}/{len(active)}; figuras: {counts['figures']}; falhas: {counts['errors']}", flush=True)
        report = store.inventory(active, on_progress=progress)
        downloads = Counter(attempted=0, downloaded=0, errors=0, skippedReview=0)
        if args.download_limit:
            for pmcid in sorted(active):
                catalog_path = args.media_dir / "catalogs" / f"{pmcid}.json"
                if not catalog_path.is_file():
                    continue
                catalog = json.loads(catalog_path.read_text())
                for figure in catalog["figures"]:
                    for aid in figure["assetIds"]:
                        if downloads["attempted"] >= args.download_limit:
                            break
                        asset = store.lookup(aid)
                        if asset["downloadStatus"] == "downloaded":
                            continue
                        if asset["privacyStatus"] in {"selection_hold", "rejected"} or asset["rightsStatus"] != "article_license":
                            downloads["skippedReview"] += 1
                            continue
                        downloads["attempted"] += 1
                        try:
                            store.download_asset(aid)
                            downloads["downloaded"] += 1
                        except SourceMediaError:
                            downloads["errors"] += 1
        print(json.dumps({"catalog": report["counts"], "downloads": dict(downloads),
            "mediaDir": str(args.media_dir), "note": "Privacidade, vínculo paciente/exame e publicação continuam sujeitos a revisão."}, ensure_ascii=False, indent=2))
        return 1 if report["counts"]["errors"] or downloads["errors"] else 0
    except (OSError, ValueError, StopIteration, SourceMediaError):
        print("Não foi possível preparar a mídia. Confira inventário, snapshots, permissões e parâmetros.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
