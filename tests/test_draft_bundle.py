import hashlib
import json
import zipfile

import pytest

from medlab.draft_bundle import bundle_shards, write_bundle
from medlab import cli
from medlab.source_registry import source_counts
from test_importer import real_case
from test_corpus_cli import corpus_apps
from test_bundled_drafts import create_bundle, invoke
from test_corpus_release import minimal_release, real_draft_export
from scripts.package_release import write_archive


def test_shards_restore_in_bounded_atomic_batches_and_resume(corpus_apps, tmp_path, monkeypatch):
    cases = [real_case(f"PMC{number}") for number in range(1, 6)]
    data = create_bundle(tmp_path, monkeypatch, cases)
    (data / "real_case_drafts.json").unlink()
    directory = data / "real_case_drafts"
    result = write_bundle(iter(cases), directory, shard_size=2)
    assert result["cases"] == 5 and len(bundle_shards(directory)) == 3
    application = corpus_apps()
    invoke(application, "init-db")
    with application.app_context():
        assert source_counts()["translated"] == 5
        report = cli.restore_bundled_drafts()
        assert report["new"] == 0 and report["unchanged"] == 5


def test_corrupt_last_shard_fails_before_restoring_first(corpus_apps, tmp_path, monkeypatch):
    cases = [real_case("PMC123"), real_case("PMC456")]
    data = create_bundle(tmp_path, monkeypatch, cases)
    (data / "real_case_drafts.json").unlink()
    directory = data / "real_case_drafts"
    write_bundle(cases, directory, shard_size=1)
    (directory / "cases-00002.json").write_text("[]")
    app = corpus_apps()
    result = app.test_cli_runner().invoke(args=["init-db"])
    assert result.exit_code != 0 and "Nenhum lote foi iniciado" in result.output
    with app.app_context():
        assert source_counts()["translated"] == 0


def test_export_command_exports_only_real_private_drafts(corpus_apps, tmp_path, monkeypatch):
    create_bundle(tmp_path, monkeypatch, [real_case()])
    app = corpus_apps()
    invoke(app, "init-db")
    output = tmp_path / "export"
    result = app.test_cli_runner().invoke(args=["export-real-drafts", "--output", str(output)])
    assert result.exit_code == 0, result.output
    assert "Exportados 1 rascunhos" in result.output
    shard = bundle_shards(output)[0]
    assert json.loads(shard.read_text())[0]["caseId"] == "mcr-pmc123"
    repeat = app.test_cli_runner().invoke(args=["export-real-drafts", "--output", str(output)])
    assert repeat.exit_code != 0 and "diretório vazio" in repeat.output


@pytest.mark.parametrize("mutation", ["../outside.json", "manifest.json", "duplicate", "symlink"])
def test_manifest_cannot_read_unlisted_or_escaping_paths(tmp_path, mutation):
    write_bundle([real_case()], tmp_path / "bundle")
    directory = tmp_path / "bundle"
    manifest = directory / "manifest.json"
    data = json.loads(manifest.read_text())
    if mutation == "duplicate":
        data["shards"].append(data["shards"][0])
    elif mutation == "symlink":
        path = directory / data["shards"][0]["file"]
        outside = tmp_path / "outside.json"
        path.rename(outside)
        path.symlink_to(outside)
    else:
        data["shards"][0]["file"] = mutation
    manifest.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        bundle_shards(directory)


def test_sharded_release_repackages_once_and_preserves_checksums(tmp_path):
    root = minimal_release(tmp_path)
    inventory, _, _, _, payload = real_draft_export(tmp_path)
    directory = tmp_path / "drafts"
    write_bundle([payload], directory)
    target = tmp_path / "release.zip"
    count, _ = write_archive(target, root, source_registry=inventory, case_drafts=directory)
    extracted = tmp_path / "unpacked"
    with zipfile.ZipFile(target) as archive:
        archive.extractall(extracted)
        assert count == len(archive.namelist()) - 1
        for path in directory.iterdir():
            name = "medlab/data/real_case_drafts/" + path.name
            assert archive.getinfo(name).external_attr >> 16 & 0o777 == 0o600
            assert archive.read(name) == path.read_bytes()
    second = tmp_path / "again.zip"
    write_archive(second, extracted)
    with zipfile.ZipFile(second) as archive:
        assert len(archive.namelist()) == len(set(archive.namelist()))
        assert archive.read("medlab/data/real_case_drafts/manifest.json") == (directory / "manifest.json").read_bytes()
