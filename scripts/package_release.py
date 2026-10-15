#!/usr/bin/env python3
"""Build a deterministic cPanel release from a narrow source allowlist."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import zipfile


ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = {
    "app.py", "passenger_wsgi.py", "requirements.txt", "requirements-dev.txt",
    ".env.example", "README.md", "LICENSE", "LICENSE.md", "LICENSE.txt",
    "pyproject.toml", "pytest.ini",
}
TREES = ("medlab", "docs", "scripts", "tests", "EasyMED", "SPBench")
SUFFIXES = {".py", ".html", ".css", ".js", ".json", ".md", ".txt", ".toml", ".ini", ".svg"}
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
    selected = []
    for path in candidates:
        relative = path.relative_to(root)
        if path.is_symlink() or not path.is_file():
            continue
        if any((root / Path(*relative.parts[:index])).is_symlink()
               for index in range(1, len(relative.parts))):
            continue
        if any(part in EXCLUDED_PARTS or part.startswith(".env") for part in relative.parts):
            if relative.as_posix() != ".env.example":
                continue
        if relative.as_posix() not in ROOT_FILES and path.suffix.lower() not in SUFFIXES:
            continue
        if any(part.startswith(".") for part in relative.parts) and relative.as_posix() != ".env.example":
            continue
        selected.append(path)
    return sorted(set(selected), key=lambda path: path.relative_to(root).as_posix())


def write_archive(output: Path, root: Path = ROOT) -> tuple[int, str]:
    required = ("app.py", "passenger_wsgi.py", "requirements.txt", ".env.example")
    missing = [name for name in required if not (root / name).is_file()]
    if missing:
        raise SystemExit("Entrega incompleta: faltam " + ", ".join(missing))
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
        entry = zipfile.ZipInfo("MANIFEST.sha256", date_time=(2026, 1, 1, 0, 0, 0))
        entry.compress_type = zipfile.ZIP_DEFLATED
        entry.external_attr = 0o100644 << 16
        archive.writestr(entry, "\n".join(manifest) + "\n")
    return len(paths), hashlib.sha256(output.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist" / "medlab-cpanel.zip")
    arguments = parser.parse_args()
    count, digest = write_archive(arguments.output)
    print(f"Pacote: {arguments.output.resolve()}")
    print(f"Arquivos: {count} + MANIFEST.sha256")
    print(f"SHA256: {digest}")


if __name__ == "__main__":
    main()
