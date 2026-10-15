"""Optional corpus inventory is portable data, never a copy of a live database."""
import hashlib
import json
from pathlib import Path
import zipfile

import pytest

from scripts.package_release import write_archive


def sample_export(path):
    header = {'format': 'medlab-source-metadata', 'version': 1, 'dataset': 'MedCaseReasoning',
              'metadataOnly': True, 'pipelineStateIncluded': False}
    item = {'dataset': 'MedCaseReasoning', 'pmcid': 'PMC123', 'dataset_revision': 'abc',
            'dataset_url': 'https://huggingface.co/datasets/zou-lab/MedCaseReasoning',
            'source_file_sha256': 'a' * 64, 'record_sha256': 'b' * 64,
            'source_row_number': 1, 'original_title': 'Published case', 'original_diagnosis': 'Pneumonia',
            'original_metadata': {k: '' for k in ('journal', 'article_link', 'publication_date', 'split', 'index')}}
    path.write_text(json.dumps(header)+'\n'+json.dumps(item)+'\n')
    return header, item


def minimal_release(tmp_path):
    root = tmp_path / 'app'
    root.mkdir()
    for name in ('app.py', 'passenger_wsgi.py', 'requirements.txt', '.env.example'):
        (root / name).write_text('')
    (root / '.env').write_text('SECRET_KEY=must-not-ship')
    return root


def test_inventory_has_manifest_and_private_mode(tmp_path):
    root = minimal_release(tmp_path)
    inventory = tmp_path / 'inventory.jsonl'
    sample_export(inventory)
    output = tmp_path / 'release.zip'
    count, digest = write_archive(output, root, source_registry=inventory)
    assert digest == hashlib.sha256(output.read_bytes()).hexdigest()
    with zipfile.ZipFile(output) as archive:
        name = 'medlab/data/source_registry.jsonl'
        assert archive.read(name) == inventory.read_bytes()
        assert archive.getinfo(name).external_attr >> 16 & 0o777 == 0o600
        assert '.env' not in archive.namelist()
        assert f'{hashlib.sha256(inventory.read_bytes()).hexdigest()}  {name}' in archive.read('MANIFEST.sha256').decode()
        assert count == len(archive.namelist()) - 1
    assert output.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("sharded", [False, True])
def test_release_rejects_draft_changes_after_validation_and_preserves_prior_zip(tmp_path, monkeypatch, sharded):
    import scripts.package_release as package
    from medlab.draft_bundle import write_bundle
    root = minimal_release(tmp_path)
    inventory, drafts, _, _, payload = real_draft_export(tmp_path)
    if sharded:
        drafts = tmp_path / "shards"
        write_bundle([payload], drafts)
    changed = drafts / "cases-00001.json" if sharded else drafts
    original = package.release_files
    def mutate_after_validation(directory):
        changed.write_text('{"unvalidated": true}')
        return original(directory)
    monkeypatch.setattr(package, "release_files", mutate_after_validation)
    target = tmp_path / "existing.zip"
    target.write_bytes(b"Previous valid delivery")
    with pytest.raises(SystemExit, match="mudaram durante"):
        package.write_archive(target, root, inventory, drafts)
    assert target.read_bytes() == b"Previous valid delivery"
    assert not list(tmp_path.glob(".medlab-release-*"))


def test_release_io_failure_preserves_previous_delivery_and_cleans_temporary(tmp_path, monkeypatch):
    root = minimal_release(tmp_path)
    target = tmp_path / "existing.zip"
    target.write_bytes(b"Previous delivery")
    read = Path.read_bytes
    def fail_on_app(path):
        if path == root / "app.py":
            raise OSError("Synthetic disk error")
        return read(path)
    monkeypatch.setattr(Path, "read_bytes", fail_on_app)
    with pytest.raises(OSError, match="Synthetic"):
        write_archive(target, root)
    assert target.read_bytes() == b"Previous delivery"
    assert not list(tmp_path.glob(".medlab-release-*"))


def test_release_checksum_never_reads_entire_archive_into_memory(tmp_path, monkeypatch):
    root = minimal_release(tmp_path)
    target = tmp_path / "delivery.zip"
    read = Path.read_bytes
    def no_full_archive_read(path):
        if path == target:
            raise AssertionError("Archive checksum must stream")
        return read(path)
    monkeypatch.setattr(Path, "read_bytes", no_full_archive_read)
    _, digest = write_archive(target, root)
    assert len(digest) == 64 and target.is_file()


def test_optional_real_draft_bundle_ships_with_private_permissions(tmp_path):
    root = minimal_release(tmp_path)
    drafts = root / "medlab/data/real_case_drafts.json"
    drafts.parent.mkdir(parents=True)
    drafts.write_text("[]\n")
    output = tmp_path / "release.zip"
    write_archive(output, root)
    with zipfile.ZipFile(output) as archive:
        name = "medlab/data/real_case_drafts.json"
        assert archive.read(name) == drafts.read_bytes()
        assert archive.getinfo(name).external_attr >> 16 & 0o777 == 0o600
        assert f"{hashlib.sha256(drafts.read_bytes()).hexdigest()}  {name}" in archive.read("MANIFEST.sha256").decode()


def real_draft_export(tmp_path):
    from test_importer import real_case
    inventory, drafts = tmp_path / "sources.jsonl", tmp_path / "private-drafts.json"
    header, item = sample_export(inventory)
    payload = real_case()
    item["dataset_revision"] = payload["provenance"]["datasetRevision"]
    inventory.write_text(json.dumps(header) + "\n" + json.dumps(item) + "\n")
    drafts.write_text(json.dumps([payload]))
    return inventory, drafts, header, item, payload


def test_private_drafts_are_injected_and_repackaged_once_with_matching_inventory(tmp_path):
    root = minimal_release(tmp_path)
    inventory, drafts, *_ = real_draft_export(tmp_path)
    output = tmp_path / "release.zip"
    count, _ = write_archive(output, root, source_registry=inventory, case_drafts=drafts)
    extracted = tmp_path / "extracted"
    with zipfile.ZipFile(output) as archive:
        name = "medlab/data/real_case_drafts.json"
        assert archive.read(name) == drafts.read_bytes()
        assert archive.getinfo(name).external_attr >> 16 & 0o777 == 0o600
        assert archive.namelist().count(name) == 1
        assert count == len(archive.namelist()) - 1
        archive.extractall(extracted)
    repacked = tmp_path / "repacked.zip"
    write_archive(repacked, extracted)
    with zipfile.ZipFile(repacked) as archive:
        assert archive.namelist().count(name) == 1
        assert archive.read(name) == drafts.read_bytes()


@pytest.mark.parametrize("failure", ["schema", "reviewed", "duplicate", "duplicate_source", "source_id", "revision", "missing_inventory", "excluded"])
def test_invalid_or_unregistered_draft_bundle_never_creates_release(tmp_path, failure):
    root = minimal_release(tmp_path)
    inventory, drafts, header, item, payload = real_draft_export(tmp_path)
    if failure == "schema":
        payload["DEEPSEEK_API_KEY"] = "must-not-ship"
    elif failure == "reviewed":
        payload["reviewStatus"] = "reviewed"
    elif failure == "source_id":
        payload["provenance"]["sourceRecordId"] = "PMC456"
    elif failure == "revision":
        payload["provenance"]["datasetRevision"] = "wrong-revision"
    elif failure == "missing_inventory":
        inventory = None
    elif failure == "excluded":
        header.update(version=2, exclusionsIncluded=True)
        inventory.write_text(json.dumps(header) + "\n" + json.dumps(exclusion_record("PMC123")) + "\n")
    bundle = [payload, payload] if failure == "duplicate" else [payload]
    if failure == "duplicate_source":
        bundle.append({**payload, "caseId": "another-valid-slug"})
    drafts.write_text(json.dumps(bundle))
    output = tmp_path / "release.zip"
    with pytest.raises(SystemExit, match="rascunhos devem ser casos reais"):
        write_archive(output, root, source_registry=inventory, case_drafts=drafts)
    assert not output.exists()


@pytest.mark.parametrize('mutation', ['private_field', 'claimed_translation', 'duplicate'])
def test_release_rejects_non_metadata_export(tmp_path, mutation):
    root = minimal_release(tmp_path)
    inventory = tmp_path / 'inventory.jsonl'
    header, item = sample_export(inventory)
    if mutation == 'private_field':
        item['DEEPSEEK_API_KEY'] = 'synthetic-secret'
    if mutation == 'claimed_translation':
        header['pipelineStateIncluded'] = True
    contents = json.dumps(header)+'\n'+json.dumps(item)+'\n'
    if mutation == 'duplicate':
        contents += json.dumps(item)+'\n'
    inventory.write_text(contents)
    with pytest.raises(SystemExit, match='metadata-only'):
        write_archive(tmp_path / 'release.zip', root, source_registry=inventory)


def exclusion_record(pmcid='PMC456'):
    return {'type': 'exclusion', 'dataset': 'MedCaseReasoning', 'pmcid': pmcid,
            'reason': 'source_rejected', 'created_at': '2026-10-15T12:00:00+00:00'}


def test_version_two_bundles_minimal_exclusions_with_or_without_source_rows(tmp_path):
    root = minimal_release(tmp_path)
    inventory = tmp_path / 'inventory.jsonl'
    header, item = sample_export(inventory)
    header.update(version=2, exclusionsIncluded=True)
    for rows in ([exclusion_record(), item], [exclusion_record()]):
        inventory.write_text('\n'.join(json.dumps(value) for value in [header, *rows])+'\n')
        output = tmp_path / 'release.zip'
        write_archive(output, root, source_registry=inventory)
        with zipfile.ZipFile(output) as archive:
            assert archive.read('medlab/data/source_registry.jsonl') == inventory.read_bytes()


@pytest.mark.parametrize('mutation', ['extra_title', 'raw_error', 'unversioned_date', 'bad_id',
    'duplicate', 'source_collision', 'v1_exclusion', 'false_header', 'mixed_dataset'])
def test_release_rejects_unsafe_or_contradictory_exclusions(tmp_path, mutation):
    root = minimal_release(tmp_path)
    inventory = tmp_path / 'inventory.jsonl'
    header, item = sample_export(inventory)
    header.update(version=2, exclusionsIncluded=True)
    exclusion = exclusion_record()
    if mutation == 'extra_title':
        exclusion['title'] = 'Private original title'
    elif mutation == 'raw_error':
        exclusion['reason'] = 'Raw copied article text must not be retained'
    elif mutation == 'unversioned_date':
        exclusion['created_at'] = '2026-10-15T12:00:00'
    elif mutation == 'bad_id':
        exclusion['pmcid'] = '../article'
    elif mutation == 'source_collision':
        exclusion['pmcid'] = item['pmcid']
    elif mutation == 'v1_exclusion':
        header['version'] = 1
        header.pop('exclusionsIncluded')
    elif mutation == 'false_header':
        header['exclusionsIncluded'] = False
    elif mutation == 'mixed_dataset':
        exclusion['dataset'] = 'DifferentDataset'
    rows = [header, item, exclusion]
    if mutation == 'duplicate':
        rows.append(exclusion)
    inventory.write_text('\n'.join(json.dumps(value) for value in rows)+'\n')
    with pytest.raises(SystemExit, match='metadata-only'):
        write_archive(tmp_path / 'release.zip', root, source_registry=inventory)
