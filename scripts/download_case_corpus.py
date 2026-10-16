#!/usr/bin/env python3
"""Download the pinned raw MedCaseReasoning corpus, without AI or database writes.

Uses Python's standard library. The private server cache is not a voice asset
and is never downloaded by a student's browser.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from http.client import HTTPException
import json
import os
from pathlib import Path
import tempfile
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
REVISION = "469a5365bc534b5b2b9cfbc52b2ef2a10f43a339"
FILENAME = "medcasereasoning_core.csv"
SOURCE_URL = f"https://huggingface.co/datasets/zou-lab/MedCaseReasoning/resolve/{REVISION}/{FILENAME}"
EXPECTED_SIZE = 205157823
EXPECTED_SHA256 = "887274683b81d81f48f335ff0f87d56dac6ed8ea09b4bb5aa2b3979f5a1a88b8"
DEFAULT_CACHE = ROOT / "instance" / "imports" / "MedCaseReasoning"


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_metadata(path: Path, value: dict) -> None:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", prefix=".acquisition-", suffix=".part", dir=path.parent, delete=False) as output:
            temporary = Path(output.name)
            os.fchmod(output.fileno(), 0o600)
            json.dump(value, output, ensure_ascii=False, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def download_corpus(cache_dir: Path = DEFAULT_CACHE, *, opener=None, progress=None) -> dict:
    cache = Path(cache_dir)
    if cache.is_symlink():
        raise ValueError("A pasta de cache não pode ser um link simbólico.")
    cache.mkdir(mode=0o700, parents=True, exist_ok=True)
    cache.chmod(0o700)
    destination = cache / FILENAME
    acquisition = cache / "acquisition.json"
    for path in (destination, acquisition):
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise ValueError("O cache deve conter arquivos regulares, sem links simbólicos.")
    metadata = {}
    if acquisition.is_file():
        try:
            previous = json.loads(acquisition.read_text(encoding="utf-8"))
            if isinstance(previous, dict):
                metadata = previous
        except (ValueError, UnicodeError):
            pass
    reused = (destination.is_file() and destination.stat().st_size == EXPECTED_SIZE
              and file_sha256(destination) == EXPECTED_SHA256)
    if not reused:
        temporary = None
        try:
            request = Request(SOURCE_URL, headers={"User-Agent": "MED-LAB-corpus-download/1.0", "Accept-Encoding": "identity"})
            with (opener or urlopen)(request, timeout=120) as response:
                headers = {key: response.headers.get(key) for key in ("ETag", "Last-Modified", "Content-Length")}
                digest = hashlib.sha256()
                downloaded = last_progress = 0
                with tempfile.NamedTemporaryFile(mode="wb", prefix=".corpus-", suffix=".part", dir=cache, delete=False) as output:
                    temporary = Path(output.name)
                    os.fchmod(output.fileno(), 0o600)
                    while block := response.read(1024 * 1024):
                        downloaded += len(block)
                        if downloaded > EXPECTED_SIZE:
                            raise ValueError("O download excede o tamanho da revisão fixada.")
                        digest.update(block)
                        output.write(block)
                        if progress and downloaded - last_progress >= 32 * 1024 * 1024:
                            progress(downloaded, EXPECTED_SIZE)
                            last_progress = downloaded
                    output.flush()
                    os.fsync(output.fileno())
                if downloaded != EXPECTED_SIZE or digest.hexdigest() != EXPECTED_SHA256:
                    raise ValueError("Tamanho ou SHA256 diferente da revisão fixada; arquivo não instalado.")
            os.replace(temporary, destination)
            temporary = None
            metadata = {"downloaded_at_utc": datetime.now(timezone.utc).isoformat(), "response_headers": headers}
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    destination.chmod(0o600)
    metadata.update({
        "dataset": "zou-lab/MedCaseReasoning", "revision": REVISION,
        "source_url": SOURCE_URL, "file": FILENAME, "size_bytes": EXPECTED_SIZE,
        "sha256": EXPECTED_SHA256, "verified_at_utc": datetime.now(timezone.utc).isoformat(),
        "reused_existing_file": reused, "processing_status": "raw_source_only",
    })
    write_metadata(acquisition, metadata)
    return {"path": destination, "reused": reused, "size_bytes": EXPECTED_SIZE, "sha256": EXPECTED_SHA256}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    try:
        result = download_corpus(progress=lambda received, total: print(f"Baixados {received:,} de {total:,} bytes.", flush=True))
    except (OSError, ValueError, HTTPException):
        print("Não foi possível obter/conferir o acervo. Confira conexão, espaço e permissões e execute novamente; o CSV anterior não foi substituído por um download inválido.")
        return 1
    print("CSV existente conferido; download dispensado." if result["reused"] else "CSV baixado e conferido.")
    print(f"Cache privado: {result['path']}\nBytes: {result['size_bytes']}\nSHA256: {result['sha256']}")
    print("Fonte bruta no servidor; este comando não traduz, adapta nem publica casos.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
