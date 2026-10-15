#!/usr/bin/env python3
"""Prepare pinned browser speech runtimes; no Node or heavy Python inference needed.

The release ZIP includes these generated assets. Repository checkouts can run
this script once using Python's standard library. No patient data is sent.
"""
import argparse
import errno
import hashlib
from http.client import IncompleteRead
import json
from pathlib import Path
import shutil
import socket
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]
DOWNLOAD_ATTEMPTS = 4
RETRY_DELAYS = (1, 2, 4)


def _transient_download_error(error):
    if isinstance(error, HTTPError):
        return error.code == 429 or 500 <= error.code <= 599
    if isinstance(error, URLError):
        return _transient_download_error(error.reason)
    if isinstance(error, (TimeoutError, ConnectionError, IncompleteRead)):
        return True
    if isinstance(error, socket.gaierror):
        return error.errno == socket.EAI_AGAIN
    return isinstance(error, OSError) and error.errno in {
        errno.ETIMEDOUT, errno.ECONNRESET, errno.ECONNABORTED,
        errno.ECONNREFUSED, errno.ENETUNREACH, errno.EHOSTUNREACH,
        errno.ENETDOWN, errno.EPIPE,
    }


def _download_once(asset, path):
    """Publish only a complete, verified file; discard every partial attempt."""
    digest = hashlib.sha256()
    size = 0
    temporary = None
    expected_output = asset.get("output_sha256", asset["sha256"])
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as output:
            temporary = Path(output.name)
            with urlopen(asset["url"], timeout=60) as response:
                while chunk := response.read(1024 * 1024):
                    size += len(chunk)
                    if size > asset["size"]:
                        raise ValueError("Downloaded asset exceeds pinned size")
                    digest.update(chunk)
                    output.write(chunk)
            output.flush()
            if size != asset["size"] or digest.hexdigest() != asset["sha256"]:
                raise ValueError(f"Checksum inválido: {asset['path']}")
            if asset.get("append"):
                # Explicit, auditable ESM export for the upstream classic JS module.
                output.write(asset["append"].encode("utf-8"))
                output.flush()
                if hashlib.sha256(temporary.read_bytes()).hexdigest() != expected_output:
                    raise ValueError(f"Checksum do módulo preparado inválido: {asset['path']}")
        temporary.chmod(0o644)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _download(asset, path):
    for attempt in range(DOWNLOAD_ATTEMPTS):
        try:
            _download_once(asset, path)
            return
        except Exception as error:
            if attempt == DOWNLOAD_ATTEMPTS - 1 or not _transient_download_error(error):
                raise
            delay = RETRY_DELAYS[attempt]
            print(f"Download temporariamente indisponível: {asset['path']}. "
                  f"Nova tentativa {attempt + 2}/{DOWNLOAD_ATTEMPTS} em {delay}s.", file=sys.stderr)
            time.sleep(delay)


def prepare(root=ROOT):
    destination = root / "medlab" / "static" / "voice-local"
    manifest = json.loads((root / "scripts" / "voice_assets.json").read_text())
    for asset in manifest:
        relative = Path(asset["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Invalid asset path")
        path = destination / relative
        expected_output = asset.get("output_sha256", asset["sha256"])
        if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == expected_output:
            print(f"Conferido: {relative}")
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        _download(asset, path)
        print(f"Preparado: {relative}")
    for source in (root / "frontend").glob("*.js"):
        destination.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination / source.name)
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    print("Voz local preparada em:", prepare())
