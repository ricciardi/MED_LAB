"""Private PMC images: pinned identity, hostile paths, review and resumability."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import struct
from unittest.mock import Mock
import zlib

import pytest
import requests

from medlab.source_media import SourceMediaError, SourceMediaStore, _image_info


def png():
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00")) + chunk(b"IEND", b"")


def response(raw, status=200, headers=None):
    item = Mock(status_code=status, headers=headers or {})
    item.iter_content.return_value = [raw]
    item.__enter__ = Mock(return_value=item)
    item.__exit__ = Mock(return_value=False)
    return item


def source_fixture(tmp_path, *, caption="Chest radiograph.", href="figure-one", media_url=None, permissions=""):
    raw_image = png()
    md5 = hashlib.md5(raw_image).hexdigest()
    xml = (f'<article xmlns:xlink="http://www.w3.org/1999/xlink"><body><fig id="F1">'
           f'<label>Figure 1</label><caption><p>{caption}</p></caption>{permissions}'
           f'<graphic xlink:href="{href}"/></fig></body></article>').encode()
    metadata = {"pmcid": "PMC123", "version": 1, "is_retracted": False, "license_code": "CC BY",
        "media_urls": [media_url or f"s3://pmc-oa-opendata/PMC123.1/figure-one.png?md5={md5}"]}
    meta = json.dumps(metadata).encode()
    p = {"pmcid": "PMC123", "pmcVersion": 1, "xmlMd5": hashlib.md5(xml).hexdigest(),
        "xmlSha256": hashlib.sha256(xml).hexdigest(), "metadataSha256": hashlib.sha256(meta).hexdigest(),
        "license": {"code": "CC BY", "url": "https://creativecommons.org/licenses/by/4.0/", "version": "4.0"},
        "authors": ["A. Author"], "articleTitle": "Published case", "articleUrl": "https://pmc.ncbi.nlm.nih.gov/articles/PMC123/"}
    cache = tmp_path / "pmc-cache"
    cache.mkdir()
    (cache / "PMC123.verified.json").write_text(json.dumps({"provenance": p, "articleText": "Verified article."}))
    (cache / f'PMC123.1.{p["xmlMd5"]}.xml').write_bytes(xml)
    (cache / f'PMC123.1.{p["metadataSha256"]}.metadata.json').write_bytes(meta)
    session = Mock()
    session.get.return_value = response(raw_image)
    store = SourceMediaStore(tmp_path / "media", cache_dir=cache, session=session)
    return store, p, raw_image


def download(store):
    catalog = store.catalog_source("PMC123")
    asset_id = catalog["figures"][0]["assetIds"][0]
    return store.download_asset(asset_id)


def binding(store, asset, approved=True, **overrides):
    values = {"case_id": "mcr-pmc123", "exam_name": "Radiografia", "group": "auxiliary",
        "caption_pt": "Radiografia de tórax.", "patient_ref": "Paciente único do relato",
        "patient_evidence": "Figura F1 vinculada à apresentação do paciente único.",
        "reviewer": "Revisor de teste", "approved": approved}
    values.update(overrides)
    return store.bind_asset(asset["assetId"], **values)


def read(store, asset, **changes):
    args = {"pmcid": "PMC123", "xml_sha256": asset["xmlSha256"], "case_id": "mcr-pmc123",
        "exam_name": "Radiografia", "group": "auxiliary", "caption_pt": "Radiografia de tórax."}
    args.update(changes)
    return store.read_media_asset(asset["assetId"], **args)


def test_catalog_from_pinned_xml_and_metadata_without_network(tmp_path):
    store, p, _ = source_fixture(tmp_path)
    catalog = store.catalog_source("PMC123")
    store.session.get.assert_not_called()
    assert catalog["xmlSha256"] == p["xmlSha256"]
    asset = store.lookup(catalog["figures"][0]["assetIds"][0])
    assert asset["privacyStatus"] == "pending_visual_review"
    assert asset["caseBindings"] == [] and asset["downloadStatus"] == "pending"
    assert asset["license"] == p["license"]


def test_download_original_checks_digest_and_reuses_good_file(tmp_path):
    store, _, raw = source_fixture(tmp_path)
    asset = download(store)
    assert (store.media_dir / asset["relativePath"]).read_bytes() == raw
    assert asset["sha256"] == hashlib.sha256(raw).hexdigest()
    assert asset["mimeType"] == "image/png" and asset["width"] == 1
    assert (store.media_dir / asset["relativePath"]).stat().st_mode & 0o777 == 0o600
    assert store.session.get.call_args.kwargs["allow_redirects"] is False
    store.session.get.reset_mock()
    assert store.download_asset(asset["assetId"])["sha256"] == asset["sha256"]
    store.session.get.assert_not_called()


@pytest.mark.parametrize("url", ["https://evil.example/file.png?md5=" + "a"*32,
    "s3://pmc-oa-opendata/PMC999.1/figure-one.png?md5=" + "a"*32,
    "s3://pmc-oa-opendata/PMC123.1/../figure-one.png?md5=" + "a"*32,
    "https://pmc-oa-opendata.s3.amazonaws.com.evil.example/PMC123.1/figure-one.png?md5=" + "a"*32])
def test_catalog_refuses_external_cross_source_and_traversal_urls(tmp_path, url):
    store, _, _ = source_fixture(tmp_path, media_url=url)
    catalog = store.catalog_source("PMC123")
    assert catalog["assetCount"] == 0 and catalog["invalidMediaReferences"] == 1
    store.session.get.assert_not_called()


@pytest.mark.parametrize("caption,permissions", [("Photograph of the patient's face.", ""),
    ("Patient name printed in the image.", ""), ("Reproduced from another publisher with permission.", ""),
    ("Clinical image.", "<permissions><copyright-statement>Other rights.</copyright-statement></permissions>")])
def test_privacy_or_third_party_holds_prevent_automatic_download(tmp_path, caption, permissions):
    store, _, _ = source_fixture(tmp_path, caption=caption, permissions=permissions)
    catalog = store.catalog_source("PMC123")
    asset_id = catalog["figures"][0]["assetIds"][0]
    with pytest.raises(SourceMediaError, match="revisão"):
        store.download_asset(asset_id)
    store.session.get.assert_not_called()


def test_changed_xml_cannot_create_catalog(tmp_path):
    store, p, _ = source_fixture(tmp_path)
    (store.cache_dir / f'PMC123.1.{p["xmlMd5"]}.xml').write_bytes(b"changed")
    with pytest.raises(SourceMediaError, match="integridade"):
        store.catalog_source("PMC123")
    assert not (store.media_dir / "catalogs/PMC123.json").exists()


@pytest.mark.parametrize("http", [302, 404, 500])
def test_failed_download_is_private_and_explicitly_resumable(tmp_path, http):
    store, _, raw = source_fixture(tmp_path)
    catalog = store.catalog_source("PMC123"); aid = catalog["figures"][0]["assetIds"][0]
    store.session.get.return_value = response(b"private-provider-error", http)
    with pytest.raises(SourceMediaError):store.download_asset(aid)
    assert store.lookup(aid)["downloadStatus"] == "error"
    assert "private-provider-error" not in json.dumps(store.lookup(aid))
    store.session.get.return_value = response(raw)
    assert store.download_asset(aid)["downloadStatus"] == "downloaded"


def test_digest_mismatch_saves_no_image(tmp_path):
    store, _, _ = source_fixture(tmp_path)
    catalog = store.catalog_source("PMC123"); aid = catalog["figures"][0]["assetIds"][0]
    store.session.get.return_value = response(b"not-an-image")
    with pytest.raises(SourceMediaError, match="checksum"):store.download_asset(aid)
    assert not (store.media_dir / "objects").exists()


def test_requires_visual_review_and_exact_approved_patient_exam_caption_binding(tmp_path):
    store, _, raw = source_fixture(tmp_path); asset = download(store)
    with pytest.raises(SourceMediaError):read(store, asset)
    store.review_privacy(asset["assetId"], approved=True, reviewer="Visual", note="Imagem inspecionada sem identificadores visíveis.")
    binding(store, asset, approved=False)
    with pytest.raises(SourceMediaError):read(store, asset)
    binding(store, asset)
    assert read(store, asset)["data"] == raw
    for changes in ({"case_id":"mcr-pmc456"},{"pmcid":"PMC456"},{"xml_sha256":"f"*64},
                    {"exam_name":"Outro exame"},{"caption_pt":"Legenda modificada"},{"group":"physical"}):
        with pytest.raises(SourceMediaError):read(store, asset, **changes)
    store.catalog_source("PMC123")
    assert read(store, asset)["data"] == raw


def test_tampered_or_escaped_file_cannot_be_served(tmp_path):
    store, _, _ = source_fixture(tmp_path); asset = download(store)
    store.review_privacy(asset["assetId"], approved=True, reviewer="Visual", note="Inspecionado")
    binding(store, asset)
    (store.media_dir / asset["relativePath"]).write_bytes(b"tampered")
    with pytest.raises(SourceMediaError):read(store, asset)
    metadata = store.lookup(asset["assetId"]); metadata["relativePath"] = "../../secret.png"
    store._asset_path(asset["assetId"]).write_text(json.dumps(metadata))
    with pytest.raises(SourceMediaError):read(store, asset)


def test_headers_bound_stream_before_reading_large_body(tmp_path):
    store, _, raw = source_fixture(tmp_path)
    c=store.catalog_source("PMC123"); aid=c["figures"][0]["assetIds"][0]
    store.session.get.return_value=response(raw,headers={"Content-Length":str(30*1024*1024)})
    with pytest.raises(SourceMediaError,match="Tamanho"):store.download_asset(aid)
    store.session.get.return_value.iter_content.assert_not_called()


def test_active_allowlist_inventory_does_not_scan_other_sources(tmp_path):
    store, _, _=source_fixture(tmp_path)
    report=store.inventory(["PMC123","PMC123"])
    assert report["counts"]["sources"]==1 and report["counts"]["assets"]==1
    assert report["counts"]["errors"]==0
    store.session.get.assert_not_called()


def test_svg_and_impossible_dimensions_are_not_raster_images():
    with pytest.raises(SourceMediaError):_image_info(b'<svg onload="alert(1)">' + b"x"*100)
    data=bytearray(png()); data[16:20]=struct.pack(">I",100000)
    with pytest.raises(SourceMediaError):_image_info(bytes(data))


def test_static_webp_dimensions_and_animation_rejection():
    payload=b"\x00\x00\x00\x00"+(20-1).to_bytes(3,"little")+(30-1).to_bytes(3,"little")
    body=b"WEBPVP8X"+len(payload).to_bytes(4,"little")+payload
    raw=b"RIFF"+len(body).to_bytes(4,"little")+body
    assert _image_info(raw)=={"mimeType":"image/webp","extension":"webp","width":20,"height":30}
    altered=bytearray(raw);altered[20]=0x02
    with pytest.raises(SourceMediaError):_image_info(bytes(altered))


def test_legacy_parentheses_in_official_filename_are_safe(tmp_path):
    filename="ASC_51(4)_326-331-f1.png"
    url="s3://pmc-oa-opendata/PMC123.1/"+filename+"?md5="+hashlib.md5(png()).hexdigest()
    store,_,_=source_fixture(tmp_path,href=filename,media_url=url)
    asset=download(store)
    assert "%28" in asset["sourceUrl"] and asset["downloadStatus"]=="downloaded"


def test_embedded_text_metadata_requires_assisted_review_without_altering_bytes():
    from medlab.source_media import _embedded_metadata_flags
    import zlib
    raw = png()
    data = b'Author\x00An identifier'
    marker = struct.pack('>I', len(data)) + b'tEXt' + data + struct.pack('>I', zlib.crc32(b'tEXt' + data))
    original = raw[:-12] + marker + raw[-12:]
    assert _embedded_metadata_flags(original) == ['embedded_text_metadata']
    assert _embedded_metadata_flags(raw) == []
    assert _image_info(original)['mimeType'] == 'image/png'
