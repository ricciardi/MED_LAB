"""Voice runtime downloads retry transport failures without accepting bad bytes."""
import hashlib
from http.client import IncompleteRead
import io
import json
import socket
import ssl
from urllib.error import HTTPError, URLError

import pytest

from scripts import prepare_voice_assets as assets


def asset_root(tmp_path, *, append=""):
    source = b"pinned browser runtime"
    asset = {"path": "vendor/runtime.js", "url": "https://assets.example.invalid/runtime.js",
             "size": len(source), "sha256": hashlib.sha256(source).hexdigest()}
    if append:
        asset.update(append=append, output_sha256=hashlib.sha256(source + append.encode()).hexdigest())
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts/voice_assets.json").write_text(json.dumps([asset]))
    (tmp_path / "frontend").mkdir()
    (tmp_path / "frontend/worker.js").write_text("// worker")
    target = tmp_path / "medlab/static/voice-local" / asset["path"]
    target.parent.mkdir(parents=True)
    return asset, source, target


class InterruptedResponse(io.BytesIO):
    def __init__(self, chunk, error):
        super().__init__(chunk)
        self.error = error
        self.started = False

    def read(self, size=-1):
        if self.started:
            raise self.error
        self.started = True
        return super().read(size)


def test_restarts_partial_download_and_validates_prepared_output(tmp_path, monkeypatch):
    asset, source, target = asset_root(tmp_path, append="\nexport default runtime;")
    target.write_bytes(b"previous version")
    failures = [HTTPError(asset["url"], 503, "Unavailable", {}, None),
                InterruptedResponse(source[:7], TimeoutError("read timeout")), io.BytesIO(source)]
    requests, temporary_names, delays = [], [], []
    temporary_file = assets.tempfile.NamedTemporaryFile

    def temporary(*args, **kwargs):
        result = temporary_file(*args, **kwargs)
        temporary_names.append(result.name)
        return result

    def open_url(url, timeout):
        requests.append((url, timeout))
        result = failures.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def sleep(delay):
        # Failed attempts are removed before backoff; the prior file is intact.
        assert list(target.parent.iterdir()) == [target]
        assert target.read_bytes() == b"previous version"
        delays.append(delay)

    monkeypatch.setattr(assets, "urlopen", open_url)
    monkeypatch.setattr(assets.tempfile, "NamedTemporaryFile", temporary)
    monkeypatch.setattr(assets.time, "sleep", sleep)
    destination = assets.prepare(tmp_path)
    assert requests == [(asset["url"], 60)] * 3
    assert delays == [1, 2]
    assert len(set(temporary_names)) == 3
    assert target.read_bytes() == source + asset["append"].encode()
    assert hashlib.sha256(target.read_bytes()).hexdigest() == asset["output_sha256"]
    assert target.stat().st_mode & 0o777 == 0o644
    assert list(target.parent.iterdir()) == [target]
    assert (destination / "worker.js").read_text() == "// worker"


@pytest.mark.parametrize("code", [429, 500, 503])
def test_retry_limit_preserves_existing_asset(tmp_path, monkeypatch, code):
    asset, _, target = asset_root(tmp_path)
    target.write_bytes(b"previous version")
    calls, delays = [], []

    def fail(*args, **kwargs):
        calls.append(1)
        raise HTTPError(asset["url"], code, "Unavailable", {}, None)

    monkeypatch.setattr(assets, "urlopen", fail)
    monkeypatch.setattr(assets.time, "sleep", delays.append)
    with pytest.raises(HTTPError) as raised:
        assets.prepare(tmp_path)
    assert raised.value.code == code
    assert len(calls) == assets.DOWNLOAD_ATTEMPTS == 4
    assert delays == [1, 2, 4]
    assert target.read_bytes() == b"previous version"
    assert list(target.parent.iterdir()) == [target]
    assert not (target.parent.parent / "worker.js").exists()


@pytest.mark.parametrize("error", [
    URLError(TimeoutError("connect timeout")),
    URLError(ConnectionResetError("connection reset")),
    URLError(socket.gaierror(socket.EAI_AGAIN, "temporary DNS failure")),
    IncompleteRead(b"partial", 5),
])
def test_transient_connection_failure_recovers(tmp_path, monkeypatch, error):
    _, source, target = asset_root(tmp_path)
    calls, delays = [], []

    def open_url(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise error
        return io.BytesIO(source)

    monkeypatch.setattr(assets, "urlopen", open_url)
    monkeypatch.setattr(assets.time, "sleep", delays.append)
    assets.prepare(tmp_path)
    assert len(calls) == 2
    assert delays == [1]
    assert target.read_bytes() == source


@pytest.mark.parametrize("error", [
    HTTPError("https://assets.example.invalid", 404, "Not found", {}, None),
    HTTPError("https://assets.example.invalid", 403, "Forbidden", {}, None),
    URLError(ssl.SSLCertVerificationError(1, "certificate verification failed")),
    URLError(socket.gaierror(socket.EAI_NONAME, "name does not exist")),
])
def test_permanent_failure_is_not_retried(tmp_path, monkeypatch, error):
    _, _, target = asset_root(tmp_path)
    calls, delays = [], []

    def fail(*args, **kwargs):
        calls.append(1)
        raise error

    monkeypatch.setattr(assets, "urlopen", fail)
    monkeypatch.setattr(assets.time, "sleep", delays.append)
    with pytest.raises(type(error)):
        assets.prepare(tmp_path)
    assert len(calls) == 1
    assert delays == []
    assert not list(target.parent.iterdir())


@pytest.mark.parametrize("corruption", ["checksum", "short", "oversize", "prepared_checksum"])
def test_integrity_failure_is_not_retried_or_published(tmp_path, monkeypatch, corruption):
    asset, source, target = asset_root(tmp_path, append="\nexport default runtime;")
    body = source
    if corruption == "checksum":
        body = b"X" * len(source)
    elif corruption == "short":
        body = source[:-1]
    elif corruption == "oversize":
        body = source + b"X"
    else:
        asset["output_sha256"] = "0" * 64
        (tmp_path / "scripts/voice_assets.json").write_text(json.dumps([asset]))
    target.write_bytes(b"previous version")
    calls, delays = [], []

    def open_url(*args, **kwargs):
        calls.append(1)
        return io.BytesIO(body)

    monkeypatch.setattr(assets, "urlopen", open_url)
    monkeypatch.setattr(assets.time, "sleep", delays.append)
    with pytest.raises(ValueError):
        assets.prepare(tmp_path)
    assert len(calls) == 1
    assert delays == []
    assert target.read_bytes() == b"previous version"
    assert list(target.parent.iterdir()) == [target]


def test_verified_asset_skips_network(tmp_path, monkeypatch):
    _, source, target = asset_root(tmp_path)
    target.write_bytes(source)

    def unexpected(*args, **kwargs):
        pytest.fail("An already verified asset must not request the network or retry.")

    monkeypatch.setattr(assets, "urlopen", unexpected)
    monkeypatch.setattr(assets.time, "sleep", unexpected)
    assets.prepare(tmp_path)
    assert target.read_bytes() == source
