import hashlib
import io
import json
from pathlib import Path
import tarfile

import pytest

from medlab.media_bundle import export_media, restore_media
from medlab.source_media import SourceMediaError, SourceMediaStore
from test_source_media import source_fixture, download, binding, read


def test_roundtrip_preserves_original_reviews_and_binding(tmp_path):
    store, _, raw = source_fixture(tmp_path)
    asset = download(store)
    store.review_privacy(asset['assetId'], approved=True, reviewer='Revisor', note='Inspeção')
    binding(store, asset)
    archive = tmp_path / 'media.tar.gz'
    result = export_media(store.media_dir, archive)
    destination = tmp_path / 'new-media'
    restore_media(archive, destination, expected_sha256=result['sha256'])
    restored = SourceMediaStore(destination)
    assert read(restored, asset)['data'] == raw
    assert (destination / asset['relativePath']).stat().st_mode & 0o777 == 0o600
    assert destination.stat().st_mode & 0o777 == 0o700
    with pytest.raises(SourceMediaError, match='nova'):
        restore_media(archive, destination, expected_sha256=result['sha256'])


def test_export_excludes_unrelated_secrets_and_rejects_symlink(tmp_path):
    store, _, _ = source_fixture(tmp_path)
    download(store)
    (store.media_dir / '.env').write_text('private-secret')
    result = export_media(store.media_dir, tmp_path / 'media.tar.gz')
    with tarfile.open(result['path']) as archive:
        assert '.env' not in archive.getnames()
    (store.media_dir / 'link').symlink_to(tmp_path / 'outside')
    with pytest.raises(SourceMediaError):
        export_media(store.media_dir, tmp_path / 'other.tar.gz')


def test_invalid_archive_checksum_does_not_create_destination(tmp_path):
    store, _, _ = source_fixture(tmp_path)
    download(store)
    result = export_media(store.media_dir, tmp_path / 'media.tar.gz')
    with pytest.raises(SourceMediaError, match='externo'):
        restore_media(result['path'], tmp_path / 'restore', expected_sha256='0' * 64)
    assert not (tmp_path / 'restore').exists()


@pytest.mark.parametrize('kind', ['traversal', 'symlink', 'bad_hash', 'extra', 'missing'])
def test_hostile_archive_rejected_atomically(tmp_path, kind):
    name, raw = ('../outside' if kind == 'traversal' else 'inventory.json'), b'{}'
    digest = hashlib.sha256(raw).hexdigest()
    manifest = {'schemaVersion': 1, 'kind': 'medlab-private-source-media', 'totalBytes': 2,
                'files': [{'path': name, 'bytes': 2, 'sha256': '0' * 64 if kind == 'bad_hash' else digest}]}
    path = tmp_path / 'hostile.tar.gz'
    with tarfile.open(path, 'w:gz') as archive:
        if kind != 'missing':
            entry = tarfile.TarInfo(name); entry.size = 2
            if kind == 'symlink':
                entry.type = tarfile.SYMTYPE; entry.linkname = '../../outside'
            archive.addfile(entry, io.BytesIO(raw))
        if kind == 'extra':
            entry = tarfile.TarInfo('extra'); archive.addfile(entry, io.BytesIO(b''))
        data = json.dumps(manifest).encode(); entry = tarfile.TarInfo('MEDIA-MANIFEST.json'); entry.size = len(data)
        archive.addfile(entry, io.BytesIO(data))
    with pytest.raises(SourceMediaError):
        restore_media(path, tmp_path / 'restore', expected_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    assert not (tmp_path / 'restore').exists()
    assert not (tmp_path / 'outside').exists()


def test_changed_object_prevents_export(tmp_path):
    store, _, _ = source_fixture(tmp_path)
    asset = download(store)
    (store.media_dir / asset['relativePath']).write_bytes(b'broken')
    with pytest.raises(SourceMediaError):
        export_media(store.media_dir, tmp_path / 'media.tar.gz')
