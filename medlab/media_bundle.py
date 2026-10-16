"""Portable private media archives: whitelisted files, hashes, no overwrite."""
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import tarfile
import tempfile

from .source_media import SourceMediaError, SourceMediaStore, _now

_ALLOWED = re.compile(r'(?:inventory\.json|(?:catalogs|proposals|reviews)/PMC[1-9][0-9]{0,11}\.json|selection-cache/PMC[1-9][0-9]{0,11}/[0-9a-f]{64}\.json|assets/pmcmedia-[0-9a-f]{32}\.json|objects/[0-9a-f]{2}/[0-9a-f]{64}\.(?:jpg|png|webp))\Z')
_MANIFEST = 'MEDIA-MANIFEST.json'
_MAX_FILES = 200000
_MAX_TOTAL = 25 * 1024 ** 3


def _digest(path):
    value = hashlib.sha256()
    with path.open('rb') as handle:
        for part in iter(lambda: handle.read(1024 * 1024), b''):
            value.update(part)
    return value.hexdigest()


def _validate_store(root, names):
    store = SourceMediaStore(root)
    for name in names:
        if name.startswith('assets/'):
            asset = store.lookup(Path(name).stem)
            if asset.get('downloadStatus') == 'downloaded':
                if asset.get('relativePath') not in names:
                    raise SourceMediaError('O pacote não contém um original referenciado.')
                store._read_original(asset)


def export_media(media_dir, output):
    root, output = Path(media_dir).resolve(), Path(output).absolute()
    if output.exists() or not root.is_dir() or output.is_relative_to(root):
        raise SourceMediaError('Use uma pasta de mídia existente e um arquivo de saída novo, fora dela.')
    names = []
    for path in root.rglob('*'):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            raise SourceMediaError('Links simbólicos não são permitidos na mídia portátil.')
        if path.is_file() and _ALLOWED.fullmatch(relative):
            names.append(relative)
    names.sort()
    if not names or len(names) > _MAX_FILES:
        raise SourceMediaError('Quantidade de arquivos de mídia inválida.')
    _validate_store(root, set(names))
    files, total = [], 0
    for name in names:
        path = root / name
        size = path.stat().st_size
        if size > (20 if name.startswith('objects/') else 8) * 1024 * 1024:
            raise SourceMediaError('Um arquivo excede o limite do pacote de mídia.')
        total += size
        if total > _MAX_TOTAL:
            raise SourceMediaError('O pacote de mídia excede o limite total.')
        files.append({'path': name, 'bytes': size, 'sha256': _digest(path)})
    manifest = {'schemaVersion': 1, 'kind': 'medlab-private-source-media', 'createdAt': _now(),
                'files': files, 'totalBytes': total,
                'notice': 'Originais e revisões privadas. Não publica casos nem substitui revisão clínica.'}
    manifest_raw = json.dumps(manifest, ensure_ascii=False, separators=(',', ':')).encode()
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix='.media-package-', dir=output.parent)
    os.close(descriptor)
    try:
        with tarfile.open(temporary, 'w:gz') as archive:
            for entry in files:
                info = tarfile.TarInfo(entry['path'])
                info.size, info.mode = entry['bytes'], 0o600
                with (root / entry['path']).open('rb') as handle:
                    archive.addfile(info, handle)
            info = tarfile.TarInfo(_MANIFEST)
            info.size, info.mode = len(manifest_raw), 0o600
            archive.addfile(info, io.BytesIO(manifest_raw))
        # Recheck the complete archive on restoration; detect source mutation here too.
        if any(_digest(root / item['path']) != item['sha256'] for item in files):
            raise SourceMediaError('A mídia mudou durante o empacotamento; repita com a preparação pausada.')
        os.link(temporary, output)  # Atomic create, cannot overwrite an existing archive.
        output.chmod(0o600)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return {'path': str(output), 'sha256': _digest(output), 'bytes': output.stat().st_size,
            'files': len(files), 'uncompressedBytes': total}


def restore_media(archive_path, media_dir, *, expected_sha256):
    archive_path, destination = Path(archive_path), Path(media_dir).absolute()
    if not isinstance(expected_sha256, str) or not re.fullmatch('[0-9a-f]{64}', expected_sha256):
        raise SourceMediaError('Informe o SHA-256 confiável recebido com o pacote.')
    if destination.exists() or destination.is_symlink():
        raise SourceMediaError('Restaure somente em uma pasta nova; o progresso existente não será sobrescrito.')
    if archive_path.is_symlink() or _digest(archive_path) != expected_sha256:
        raise SourceMediaError('O SHA-256 externo do pacote não confere.')
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    staging = Path(tempfile.mkdtemp(prefix='.media-restore-', dir=destination.parent))
    try:
        with tarfile.open(archive_path, 'r:gz') as archive:
            members = archive.getmembers()
            if len(members) > _MAX_FILES + 1 or len({m.name for m in members}) != len(members):
                raise SourceMediaError('Pacote com arquivos duplicados ou em excesso.')
            by_name = {m.name: m for m in members}
            metadata = by_name.get(_MANIFEST)
            if metadata is None or not metadata.isfile() or metadata.size > 64 * 1024 * 1024:
                raise SourceMediaError('Manifesto de mídia ausente ou inválido.')
            manifest = json.load(archive.extractfile(metadata))
            if manifest.get('schemaVersion') != 1 or manifest.get('kind') != 'medlab-private-source-media' or not isinstance(manifest.get('files'), list):
                raise SourceMediaError('Formato do manifesto de mídia inválido.')
            entries = manifest['files']
            names = [entry.get('path') for entry in entries if isinstance(entry, dict)]
            if len(names) != len(entries) or len(set(names)) != len(names) or set(by_name) != set(names) | {_MANIFEST}:
                raise SourceMediaError('Conteúdo do pacote diverge do manifesto.')
            total = 0
            for entry in entries:
                name, size, digest = entry.get('path'), entry.get('bytes'), entry.get('sha256')
                if not isinstance(name, str) or not _ALLOWED.fullmatch(name) or type(size) is not int or size < 0 or not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest):
                    raise SourceMediaError('Caminho ou checksum inválido no pacote.')
                member = by_name[name]
                limit = (20 if name.startswith('objects/') else 8) * 1024 * 1024
                total += size
                if not member.isfile() or member.size != size or size > limit or total > _MAX_TOTAL:
                    raise SourceMediaError('Tipo ou tamanho de arquivo não permitido no pacote.')
                target = staging / name
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                checksum = hashlib.sha256()
                with archive.extractfile(member) as source, target.open('xb') as output:
                    for part in iter(lambda: source.read(65536), b''):
                        checksum.update(part)
                        output.write(part)
                target.chmod(0o600)
                if checksum.hexdigest() != digest:
                    raise SourceMediaError('Checksum interno de mídia divergente.')
            if total != manifest.get('totalBytes'):
                raise SourceMediaError('Tamanho total divergente no manifesto.')
        _validate_store(staging, set(names))
        if destination.exists() or destination.is_symlink():
            raise SourceMediaError('A pasta de destino foi criada durante a restauração.')
        os.rename(staging, destination)
        return {'mediaDir': str(destination), 'files': len(names), 'bytes': total, 'sha256': expected_sha256}
    except (tarfile.TarError, ValueError, TypeError, KeyError, OSError) as exc:
        if isinstance(exc, SourceMediaError):
            raise
        raise SourceMediaError('Não foi possível validar ou restaurar o pacote de mídia.') from None
    finally:
        if staging.exists():
            shutil.rmtree(staging)
