"""Private release shards, including explicit provisional-use authorizations.

All cases retain pending clinical review. Only useAuthorization may request
availability on restore; the container's name never confers approval.
"""
import hashlib
import json
from pathlib import Path

MAX_SHARD_BYTES = 20 * 1024 * 1024


def bundle_shards(directory):
    """Validate the complete manifest and every shard before restoring a batch."""
    directory = Path(directory)
    manifest = directory / "manifest.json"
    if directory.is_symlink() or manifest.is_symlink() or manifest.stat().st_size > 1024 * 1024:
        raise ValueError("Invalid private bundle manifest")
    data = json.loads(manifest.read_text(encoding="utf-8"))
    if (not isinstance(data, dict) or set(data) != {"format", "cases", "shards"}
            or data.get("format") != "medlab-private-drafts-v1" or not isinstance(data.get("shards"), list)
            or type(data.get("cases")) is not int or not 1 <= data["cases"] <= 100000):
        raise ValueError("Invalid private bundle format")
    if not 1 <= len(data["shards"]) <= 10000:
        raise ValueError("Invalid shard count")
    paths, identities, case_ids, sources, count = [], set(), set(), set(), 0
    from .case_schema import validate_case
    for item in data["shards"]:
        if (not isinstance(item, dict) or set(item) != {"file", "cases", "sha256"}
                or type(item.get("cases")) is not int or not 1 <= item["cases"] <= 500):
            raise ValueError("Invalid private shard metadata")
        name = item["file"]
        if (not isinstance(name, str) or Path(name).name != name or not name.endswith(".json")
                or name == "manifest.json" or name in identities):
            raise ValueError("Invalid shard path")
        identities.add(name)
        path = directory / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_SHARD_BYTES:
            raise ValueError("Invalid private shard")
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != item["sha256"]:
            raise ValueError("Private shard checksum mismatch")
        payload = json.loads(content)
        if not isinstance(payload, list) or len(payload) != item["cases"]:
            raise ValueError("Private shard count mismatch")
        for value in payload:
            case = validate_case(value)
            origin = case.get("provenance", {})
            identity = (origin.get("dataset"), origin.get("pmcid"))
            if (case["catalogType"] != "published_case" or case["reviewStatus"] != "review_required"
                    or case["caseId"] in case_ids or identity in sources):
                raise ValueError("Duplicate case or case without pending review in bundle")
            case_ids.add(case["caseId"])
            sources.add(identity)
        count += len(payload)
        paths.append(path)
    if count != data.get("cases"):
        raise ValueError("Private bundle count mismatch")
    return paths


def write_bundle(cases, directory, *, shard_size=100):
    """Export an iterator without holding the complete library in RAM."""
    if isinstance(shard_size, bool) or not isinstance(shard_size, int) or not 1 <= shard_size <= 500:
        raise ValueError("Invalid shard size")
    directory = Path(directory)
    if directory.is_symlink():
        raise ValueError("Private export directory cannot be a symlink")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if any(directory.iterdir()):
        raise ValueError("Export destination must be empty")
    directory.chmod(0o700)
    manifest = {"format": "medlab-private-drafts-v1", "cases": 0, "shards": []}
    pending, identities, sources = [], set(), set()
    def flush():
        if not pending:
            return
        content = (json.dumps(pending, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()
        if len(content) > MAX_SHARD_BYTES:
            raise ValueError("Reduce shard_size to keep bounded restore memory")
        name = f"cases-{len(manifest['shards']) + 1:05d}.json"
        path = directory / name
        path.write_bytes(content)
        path.chmod(0o600)
        manifest["shards"].append({"file": name, "cases": len(pending), "sha256": hashlib.sha256(content).hexdigest()})
        manifest["cases"] += len(pending)
        pending.clear()
    from .case_schema import validate_case
    for payload in cases:
        data = validate_case(payload)
        identity = (data["provenance"]["dataset"], data["provenance"]["pmcid"])
        if (data["catalogType"] != "published_case" or data["reviewStatus"] != "review_required"
                or data["caseId"] in identities or identity in sources):
            raise ValueError("Only unique real cases with pending review may be exported")
        identities.add(data["caseId"])
        sources.add(identity)
        pending.append(data)
        if len(pending) == shard_size:
            flush()
    flush()
    if not manifest["cases"]:
        raise ValueError("No drafts to export")
    path = directory / "manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    path.chmod(0o600)
    return manifest
