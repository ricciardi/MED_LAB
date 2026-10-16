#!/usr/bin/env python3
"""Download pinned public Marian weights and convert to local CTranslate2 int8.

Run only in instance/local-preparation/venv. No credentials or application DB.
The nine original files total 468 MB; an existing valid download is reused.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import urllib.request

MODEL = "Helsinki-NLP/opus-mt-tc-big-en-pt"
REVISION = "9f2863d807ecf91a374bdbecb8d01e402e90622e"
ROOT = Path(__file__).resolve().parents[1]
FILES = [{'path': 'README.md', 'bytes': 6275, 'sha256': 'c18db3029081e770aa17fb261d3c6220279275eaf81f2575d20e1b02e079e85d'}, {'path': 'config.json', 'bytes': 1076, 'sha256': 'ca76b1818f066007e94fb2519c0752320cee36a5d4947bf7ef4477c845feacc5'}, {'path': 'generation_config.json', 'bytes': 301, 'sha256': '0cf63469feb877c42fee042a5e9f429117297abee3d5641a4c347fec09597126'}, {'path': 'model.safetensors', 'bytes': 465035080, 'sha256': 'f1772ec97f6cb5b942bb6a5555a04272960a228a523f7ed47e24014236aa1716'}, {'path': 'source.spm', 'bytes': 802741, 'sha256': '7a7fcf812cf03a5785daa35d4932bbbe69e7e605c0fe56fce5a3f731d6c355aa'}, {'path': 'special_tokens_map.json', 'bytes': 65, 'sha256': '09059cedc26bc46bc09a52f05b92d4922e11917e87f3b92059bb1a63a59ab2c4'}, {'path': 'target.spm', 'bytes': 824855, 'sha256': 'b3cf20ccd4e48a89882cfdba157c05fa1cd71a6b69517dfc80e3a34d387ea91f'}, {'path': 'tokenizer_config.json', 'bytes': 337, 'sha256': 'ad3762b908f346ceb1427259c041728c5e113a137e88b3e8153f6d8d62e9ceed'}, {'path': 'vocab.json', 'bytes': 1380764, 'sha256': 'dad10ad0acbf34ad92af16cb37fd71732d2b73851274698d58c5439386b506a1'}]

def checksum(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def prepare(model_dir, *, convert=True):
    model_dir = Path(model_dir)
    model_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    reused = downloaded = 0
    for entry in FILES:
        path = model_dir / entry["path"]
        if path.is_symlink():
            raise ValueError("Model assets cannot be symbolic links.")
        if path.is_file() and path.stat().st_size == entry["bytes"] and checksum(path) == entry["sha256"]:
            reused += 1
            continue
        descriptor, temporary = tempfile.mkstemp(prefix="." + path.name, dir=model_dir)
        try:
            url = f"https://huggingface.co/{MODEL}/resolve/{REVISION}/{entry['path']}"
            size = 0
            with urllib.request.urlopen(url, timeout=60) as source, os.fdopen(descriptor, "wb") as target:
                while block := source.read(1024 * 1024):
                    size += len(block)
                    if size > entry["bytes"]:
                        raise ValueError("Public model download exceeds its pinned size.")
                    target.write(block)
                target.flush(); os.fsync(target.fileno())
            if size != entry["bytes"] or checksum(temporary) != entry["sha256"]:
                raise ValueError("Pinned model checksum or size mismatch.")
            os.replace(temporary, path); path.chmod(0o600); downloaded += 1
        finally:
            Path(temporary).unlink(missing_ok=True)
    acquisition = {"model": MODEL, "revision": REVISION, "license": "CC-BY-4.0", "files": FILES,
                   "sourceUrl": "https://huggingface.co/" + MODEL,
                   "attribution": "Helsinki-NLP / OPUS-MT; Tiedemann and Thottingal, 2020"}
    manifest = model_dir / "acquisition.json"
    manifest.write_text(json.dumps(acquisition, indent=2) + "\n"); manifest.chmod(0o600)
    converted = model_dir / "ct2-int8"
    if convert and not converted.exists():
        from ctranslate2.converters import TransformersConverter
        staging = Path(tempfile.mkdtemp(prefix=".conversion-", dir=model_dir))
        try:
            destination = staging / "model"
            TransformersConverter(str(model_dir), load_as_float16=False).convert(str(destination), quantization="int8")
            os.rename(destination, converted)
        finally:
            shutil.rmtree(staging)
    if convert:
        if converted.is_symlink() or not (converted / "model.bin").is_file():
            raise ValueError("Invalid converted model directory.")
        entries = [{"path": p.name, "bytes": p.stat().st_size, "sha256": checksum(p)}
                   for p in sorted(converted.iterdir()) if p.is_file()]
        conversion = model_dir / "conversion.json"
        current = {"sourceRevision": REVISION, "computeType": "int8", "files": entries}
        if conversion.exists() and json.loads(conversion.read_text()) != current:
            raise ValueError("Existing converted model changed; conversion manifest was preserved.")
        conversion.write_text(json.dumps(current, indent=2) + "\n"); conversion.chmod(0o600)
    return {"modelDir": str(model_dir), "downloadedFiles": downloaded, "reusedFiles": reused,
            "sourceBytes": sum(entry["bytes"] for entry in FILES), "converted": convert,
            "paidApiCalls": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", type=Path, default=ROOT / "instance/local-preparation/models/opus-mt-tc-big-en-pt")
    parser.add_argument("--download-only", action="store_true")
    args = parser.parse_args()
    print(json.dumps(prepare(args.model_dir, convert=not args.download_only), indent=2))


if __name__ == "__main__":
    main()
