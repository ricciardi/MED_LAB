#!/usr/bin/env python3
"""Build a deterministic cPanel release from a narrow source allowlist."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import zipfile


ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = {
    "app.py", "passenger_wsgi.py", "requirements.txt", "requirements-dev.txt",
    ".env.example", "README.md", "LICENSE", "LICENSE.md", "LICENSE.txt",
    "pyproject.toml", "pytest.ini",
}
TREES = ("medlab", "docs", "scripts", "tests", "frontend", "EasyMED", "SPBench")
SUFFIXES = {".py", ".html", ".css", ".js", ".cjs", ".mjs", ".wasm", ".json", ".md", ".txt", ".toml", ".ini", ".svg"}
EXCLUDED_PARTS = {
    ".git", ".venv", "venv", "env", "__pycache__", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", "node_modules", "instance", "dist",
    "logs", "output", "output_with_intent", "Eva_data", "screenshots",
    "artifacts", "test-results", "playwright-report", "backups",
}


def release_files(root: Path = ROOT) -> list[Path]:
    """Only authored source, reference cases and documentation may be shipped."""
    candidates = [root / name for name in ROOT_FILES if (root / name).is_file()]
    for directory in TREES:
        folder = root / directory
        if folder.is_dir():
            candidates.extend(folder.rglob("*"))
    assets_manifest = root / "scripts" / "voice_assets.json"
    voice_assets = {"medlab/static/voice-local/" + item["path"]
                    for item in json.loads(assets_manifest.read_text())} if assets_manifest.is_file() else set()
    selected = []
    for path in candidates:
        relative = path.relative_to(root)
        # This optional private bundle is validated and injected separately.
        if relative.as_posix() == "medlab/data/real_case_drafts.json":
            continue
        if path.is_symlink() or not path.is_file():
            continue
        if any((root / Path(*relative.parts[:index])).is_symlink()
               for index in range(1, len(relative.parts))):
            continue
        if any(part in EXCLUDED_PARTS or part.startswith(".env") for part in relative.parts):
            if relative.as_posix() != ".env.example":
                continue
        if relative.as_posix() not in ROOT_FILES | voice_assets and path.suffix.lower() not in SUFFIXES:
            continue
        if any(part.startswith(".") for part in relative.parts) and relative.as_posix() != ".env.example":
            continue
        selected.append(path)
    return sorted(set(selected), key=lambda path: path.relative_to(root).as_posix())


def write_archive(output: Path, root: Path = ROOT, source_registry: Path | None = None,
                  case_drafts: Path | None = None) -> tuple[int, str]:
    required = ("app.py", "passenger_wsgi.py", "requirements.txt", ".env.example")
    missing = [name for name in required if not (root / name).is_file()]
    if missing:
        raise SystemExit("Entrega incompleta: faltam " + ", ".join(missing))
    assets_manifest = root / "scripts" / "voice_assets.json"
    if assets_manifest.exists():
        for asset in json.loads(assets_manifest.read_text()):
            path = root / "medlab" / "static" / "voice-local" / asset["path"]
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != asset.get("output_sha256", asset["sha256"]):
                raise SystemExit("Prepare os recursos de voz com python scripts/prepare_voice_assets.py antes de empacotar.")
        for source in (root / "frontend").glob("*.js"):
            generated = root / "medlab" / "static" / "voice-local" / source.name
            if not generated.is_file() or generated.read_bytes() != source.read_bytes():
                raise SystemExit("Worker desatualizado: execute python scripts/prepare_voice_assets.py antes de empacotar.")
    if source_registry is None and (root / "medlab/data/source_registry.jsonl").is_file():
        source_registry = root / "medlab/data/source_registry.jsonl"
    if case_drafts is None and (root / "medlab/data/real_case_drafts.json").is_file():
        case_drafts = root / "medlab/data/real_case_drafts.json"
    registry_bytes = None
    registry_records = {}
    if source_registry is not None:
        # Import paths explicitly, so running this script works from any cwd.
        import sys
        sys.path.insert(0, str(ROOT))
        from medlab.source_registry import _validate_exclusion, _validate_portable, _validate_registry_header
        try:
            registry_bytes = source_registry.read_bytes()
            lines = registry_bytes.decode("utf-8").splitlines()
            header = _validate_registry_header(json.loads(lines[0]))
            if header["dataset"] != "MedCaseReasoning":
                raise ValueError("Invalid registry dataset")
            seen = set()
            for line in lines[1:]:
                value = json.loads(line)
                is_exclusion = isinstance(value, dict) and value.get("type") == "exclusion"
                if is_exclusion and header["version"] != 2:
                    raise ValueError("Exclusions require metadata version 2")
                record = _validate_exclusion(value) if is_exclusion else _validate_portable(value)
                if record["dataset"] != header["dataset"] or record["pmcid"] in seen:
                    raise ValueError("Invalid or contradictory registry identity")
                seen.add(record["pmcid"])
                if not is_exclusion:
                    registry_records[(record["dataset"], record["pmcid"])] = record
            if not seen:
                raise ValueError("Empty registry")
        except Exception:
            raise SystemExit("O inventário deve ser uma exportação metadata-only válida e sem duplicatas do MED LAB.") from None
    drafts_bytes = None
    if case_drafts is not None:
        import sys
        sys.path.insert(0, str(ROOT))
        from medlab.case_schema import validate_case
        try:
            if case_drafts.stat().st_size > 20 * 1024 * 1024:
                raise ValueError("Draft bundle is too large")
            drafts_bytes = case_drafts.read_bytes()
            payloads = json.loads(drafts_bytes.decode("utf-8"))
            if not isinstance(payloads, list):
                raise ValueError("Draft bundle must be a list")
            seen_cases, seen_sources = set(), set()
            for payload in payloads:
                data = validate_case(payload)
                if data["catalogType"] != "published_case" or payload.get("reviewStatus") != "review_required":
                    raise ValueError("Only private real drafts may be bundled")
                origin = data["provenance"]
                identity = (origin["dataset"], origin["pmcid"])
                record = registry_records.get(identity)
                if (data["caseId"] in seen_cases or identity in seen_sources or record is None
                        or origin["sourceRecordId"] != record["pmcid"]
                        or origin["datasetRevision"] != record["dataset_revision"]):
                    raise ValueError("Duplicate, excluded or unregistered draft source")
                seen_cases.add(data["caseId"])
                seen_sources.add(identity)
        except Exception:
            raise SystemExit("Os rascunhos devem ser casos reais válidos, privados, únicos e vinculados ao inventário incluído, sem fontes excluídas.") from None
    paths = release_files(root)
    output.parent.mkdir(parents=True, exist_ok=True)
    manifest = []
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in paths:
            relative = path.relative_to(root).as_posix()
            content = path.read_bytes()
            manifest.append(f"{hashlib.sha256(content).hexdigest()}  {relative}")
            entry = zipfile.ZipInfo(relative, date_time=(2026, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = 0o100644 << 16
            archive.writestr(entry, content)
        if registry_bytes is not None:
            relative = "medlab/data/source_registry.jsonl"
            manifest.append(f"{hashlib.sha256(registry_bytes).hexdigest()}  {relative}")
            entry = zipfile.ZipInfo(relative, date_time=(2026, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = 0o100600 << 16
            archive.writestr(entry, registry_bytes)
        if drafts_bytes is not None:
            relative = "medlab/data/real_case_drafts.json"
            manifest.append(f"{hashlib.sha256(drafts_bytes).hexdigest()}  {relative}")
            entry = zipfile.ZipInfo(relative, date_time=(2026, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = 0o100600 << 16
            archive.writestr(entry, drafts_bytes)
        entry = zipfile.ZipInfo("MANIFEST.sha256", date_time=(2026, 1, 1, 0, 0, 0))
        entry.compress_type = zipfile.ZIP_DEFLATED
        entry.external_attr = 0o100644 << 16
        archive.writestr(entry, "\n".join(manifest) + "\n")
    return len(paths) + (registry_bytes is not None) + (drafts_bytes is not None), hashlib.sha256(output.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist" / "medlab-cpanel.zip")
    parser.add_argument("--source-registry", type=Path, help="Exportação privada metadata-only a incluir e registrar no init-db/upgrade-db.")
    parser.add_argument("--case-drafts", type=Path, help="Lista privada de rascunhos reais validados, vinculados ao inventário incluído; serão restaurados sem publicar.")
    arguments = parser.parse_args()
    count, digest = write_archive(arguments.output, source_registry=arguments.source_registry, case_drafts=arguments.case_drafts)
    print(f"Pacote: {arguments.output.resolve()}")
    print(f"Arquivos: {count} + MANIFEST.sha256")
    print(f"SHA256: {digest}")


if __name__ == "__main__":
    main()
