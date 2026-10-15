#!/usr/bin/env python3
"""Prepare pinned browser speech runtimes; no Node or heavy Python inference needed.

The release ZIP includes these generated assets. Repository checkouts can run
this script once using Python's standard library. No patient data is sent.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]


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
        digest = hashlib.sha256()
        size = 0
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as output:
            temporary = Path(output.name)
            try:
                with urlopen(asset["url"], timeout=60) as response:
                    while chunk := response.read(1024 * 1024):
                        size += len(chunk)
                        if size > asset["size"]:
                            raise ValueError("Downloaded asset exceeds pinned size")
                        digest.update(chunk)
                        output.write(chunk)
                output.flush()
                if size != asset["size"] or digest.hexdigest() != asset["sha256"]:
                    raise ValueError(f"Checksum inválido: {relative}")
                if asset.get("append"):
                    # Explicit, auditable ESM export for the upstream classic JS module.
                    output.write(asset["append"].encode("utf-8"))
                    output.flush()
                    if hashlib.sha256(temporary.read_bytes()).hexdigest() != expected_output:
                        raise ValueError(f"Checksum do módulo preparado inválido: {relative}")
            except BaseException:
                temporary.unlink(missing_ok=True)
                raise
        temporary.chmod(0o644)
        temporary.replace(path)
        print(f"Preparado: {relative}")
    for source in (root / "frontend").glob("*.js"):
        destination.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination / source.name)
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    print("Voz local preparada em:", prepare())
