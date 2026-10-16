"""Pinned corpus transfer tests: small synthetic payloads, no network."""
import hashlib
import io
import json
import stat

import pytest

from scripts import download_case_corpus as download


@pytest.fixture
def corpus(monkeypatch):
    content = b"pmcid,title\nPMC123,Synthetic case\n"
    monkeypatch.setattr(download, "EXPECTED_SIZE", len(content))
    monkeypatch.setattr(download, "EXPECTED_SHA256", hashlib.sha256(content).hexdigest())
    return content


def response(content):
    result = io.BytesIO(content)
    result.headers = {"ETag": '"test-version"', "Content-Length": str(len(content))}
    return result


def test_download_uses_pinned_url_and_installs_private_verified_file(tmp_path, corpus):
    calls = []
    def opener(request, timeout):
        calls.append((request.full_url, timeout))
        return response(corpus)
    result = download.download_corpus(tmp_path, opener=opener)
    assert calls == [(download.SOURCE_URL, 120)]
    assert download.REVISION in calls[0][0]
    assert result["reused"] is False
    assert result["path"].read_bytes() == corpus
    metadata = json.loads((tmp_path / "acquisition.json").read_text())
    assert metadata["sha256"] == hashlib.sha256(corpus).hexdigest()
    assert metadata["processing_status"] == "raw_source_only"
    assert metadata["response_headers"]["ETag"] == '"test-version"'
    assert stat.S_IMODE(tmp_path.stat().st_mode) == 0o700
    assert stat.S_IMODE(result["path"].stat().st_mode) == 0o600
    assert stat.S_IMODE((tmp_path / "acquisition.json").stat().st_mode) == 0o600


def test_valid_existing_file_reused_without_any_http_call(tmp_path, corpus):
    (tmp_path / download.FILENAME).write_bytes(corpus)
    (tmp_path / "acquisition.json").write_text(json.dumps({"downloaded_at_utc": "original-time", "response_headers": {"ETag": "original"}}))
    def forbidden(*args, **kwargs):
        pytest.fail("A valid corpus must not be downloaded again")
    assert download.download_corpus(tmp_path, opener=forbidden)["reused"] is True
    metadata = json.loads((tmp_path / "acquisition.json").read_text())
    assert metadata["downloaded_at_utc"] == "original-time"
    assert metadata["response_headers"]["ETag"] == "original"
    assert metadata["reused_existing_file"] is True


@pytest.mark.parametrize("kind", ["truncated", "wrong-checksum", "oversized"])
def test_invalid_download_preserves_previous_file_and_cleans_partial(tmp_path, corpus, kind):
    previous = tmp_path / download.FILENAME
    previous.write_bytes(b"previous-invalid-but-preserved")
    invalid = {"truncated": corpus[:-1], "wrong-checksum": b"x" * len(corpus), "oversized": corpus + b"x"}[kind]
    with pytest.raises(ValueError):
        download.download_corpus(tmp_path, opener=lambda *args, **kwargs: response(invalid))
    assert previous.read_bytes() == b"previous-invalid-but-preserved"
    assert list(tmp_path.iterdir()) == [previous]


def test_network_failure_does_not_create_incomplete_csv(tmp_path, corpus):
    def fail(*args, **kwargs):
        raise OSError("simulated connection failure")
    with pytest.raises(OSError):
        download.download_corpus(tmp_path, opener=fail)
    assert list(tmp_path.iterdir()) == []


def test_destination_symlink_is_rejected(tmp_path, corpus):
    target = tmp_path / "unrelated.csv"
    target.write_bytes(corpus)
    (tmp_path / download.FILENAME).symlink_to(target)
    with pytest.raises(ValueError):
        download.download_corpus(tmp_path, opener=lambda *args, **kwargs: pytest.fail("No HTTP expected"))
    assert target.read_bytes() == corpus
