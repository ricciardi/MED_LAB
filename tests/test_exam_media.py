"""Clinical media access is private, requested-exam gated and snapshot bound."""
import base64
from copy import deepcopy
import hashlib
import json

import pytest

from medlab.case_schema import CaseValidationError, validate_case
from medlab.extensions import db
from medlab.models import ClinicalCase, Consultation
from medlab.source_media import SourceMediaStore
from test_importer import real_case
from test_web import evaluation_payload


ASSET = "pmcmedia-" + "a" * 32
EXAM = "Radiografia de tórax"
CAPTION = "Radiografia do tórax em projeção frontal, obtida no atendimento inicial."
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aX1sAAAAASUVORK5CYII=")


def image_case():
    case = real_case()
    case["auxiliaryLabs"] = {EXAM: "Achados radiográficos descritos no relato."}
    case["mustAuxiliaryLabs"] = [EXAM]
    case["examMedia"] = [{"examName": EXAM, "group": "auxiliary", "assetId": ASSET, "captionPt": CAPTION}]
    return case


@pytest.mark.parametrize("change", [
    lambda c: c["examMedia"][0].update(assetId="../../private.png"),
    lambda c: c["examMedia"][0].update(assetId="https://example.invalid/scan.png"),
    lambda c: c["examMedia"][0].update(examName="Exame não descrito"),
    lambda c: c["examMedia"][0].update(group="physical"),
    lambda c: c["examMedia"][0].update(group=[]),
    lambda c: c["examMedia"][0].update(captionPt=""),
    lambda c: c["examMedia"][0].update(captionPt="Pneumonia confirmada nesta imagem."),
    lambda c: c["examMedia"][0].update(url="https://example.invalid/scan.png"),
    lambda c: c["examMedia"].append(deepcopy(c["examMedia"][0])),
    lambda c: c.update(examMedia="imagem"),
    lambda c: c.update(examMedia=[{**c["examMedia"][0], "assetId": "pmcmedia-" + f"{i:032x}"} for i in range(13)]),
])
def test_schema_rejects_unbound_ambiguous_or_spoiling_media(change):
    case = image_case()
    change(case)
    with pytest.raises(CaseValidationError, match="examMedia"):
        validate_case(case)


def test_optional_media_is_normalized_without_mutating_input(case_data):
    assert "examMedia" not in validate_case(case_data)
    case = image_case()
    original = deepcopy(case)
    assert validate_case(case)["examMedia"] == original["examMedia"]
    assert case == original
    fictional = deepcopy(case_data)
    fictional["examMedia"] = case["examMedia"]
    with pytest.raises(CaseValidationError, match="examMedia"):
        validate_case(fictional)


@pytest.fixture
def media_session(app, client, login, tmp_path, monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("Media requests must never download images on the request path")
    monkeypatch.setattr("requests.sessions.Session.request", no_network)
    media_dir = tmp_path / "private-media"
    app.config["CASE_MEDIA_DIR"] = str(media_dir)
    sha = hashlib.sha256(PNG).hexdigest()
    relative = f"objects/{sha[:2]}/{sha}.png"
    object_path = media_dir / relative
    object_path.parent.mkdir(parents=True)
    object_path.write_bytes(PNG)
    metadata = {"schemaVersion": 1, "assetId": ASSET, "pmcid": "PMC123", "pmcVersion": 2,
        "xmlSha256": "a" * 64, "metadataSha256": "c" * 64, "figureId": "F1",
        "caption": "PRIVATE ORIGINAL CAPTION WITH THE DIAGNOSIS", "sourceUrl": "https://pmc-oa-opendata.s3.amazonaws.com/private-original.png",
        "relativePath": relative, "sha256": sha, "sourceMd5": hashlib.md5(PNG).hexdigest(),
        "byteSize": len(PNG), "mimeType": "image/png", "width": 1, "height": 1,
        "license": {"code": "CC BY", "url": "https://creativecommons.org/licenses/by/4.0/", "text": "Original CC notice"},
        "attribution": {"authors": ["Source Author"], "title": "PRIVATE SOURCE TITLE", "citation": "Source citation",
                        "notice": "Fonte original, sem endosso dos autores."},
        "downloadStatus": "downloaded", "privacyStatus": "pending_visual_review", "rightsStatus": "article_license", "caseBindings": []}
    metadata_path = media_dir / "assets" / f"{ASSET}.json"
    metadata_path.parent.mkdir()
    metadata_path.write_text(json.dumps(metadata))
    store = SourceMediaStore(media_dir)
    store.review_privacy(ASSET, approved=True, reviewer="test-reviewer", note="Synthetic tiny fixture, no patient data.")
    store.bind_asset(ASSET, case_id="mcr-pmc123", exam_name=EXAM, group="auxiliary", caption_pt=CAPTION,
        patient_ref="patient-1", patient_evidence="Only patient in synthetic source; figure explicitly tied to the examined patient.",
        reviewer="test-reviewer", approved=True)
    case = validate_case(image_case())
    with app.app_context():
        record = db.session.scalar(db.select(ClinicalCase).where(ClinicalCase.slug == "cenario-teste-1"))
        record.data = case
        case_id = record.id
        db.session.commit()
    login()
    response = client.post("/casos/iniciar", data={"case_id": case_id})
    session_id = response.headers["Location"].rsplit("/", 1)[-1]
    return {"id": session_id, "url": f"/sessao/{session_id}/imagem/{ASSET}", "metadata": metadata_path,
            "file": object_path, "root": media_dir, "case_id": case_id}


def request_exam(client, session_id):
    return client.post(f"/sessao/{session_id}/exame", json={"name": EXAM, "type": "auxiliary_exam"})


def edit_metadata(fixture, transform):
    metadata = json.loads(fixture["metadata"].read_text())
    transform(metadata)
    fixture["metadata"].write_text(json.dumps(metadata))


def test_media_is_revealed_only_after_request_and_persists_on_reload(client, media_session):
    fixture = media_session
    body = client.get(f"/sessao/{fixture['id']}").text
    assert ASSET not in body and CAPTION not in body and "PRIVATE ORIGINAL CAPTION" not in body
    assert client.get(fixture["url"]).status_code == 404
    assert client.post(f"/sessao/{fixture['id']}/exame", json={"name": EXAM, "type": "physical_exam"}).status_code == 400
    assert client.get(fixture["url"]).status_code == 404
    response = request_exam(client, fixture["id"])
    assert response.status_code == 200
    media = response.json["media"]
    assert len(media) == 1 and media[0]["url"] == fixture["url"] and media[0]["caption"] == CAPTION
    assert "source_url" not in media[0] and "source_title" not in media[0]
    assert "PRIVATE ORIGINAL CAPTION" not in response.text and "PRIVATE SOURCE TITLE" not in response.text
    assert "relativePath" not in response.text and str(fixture["root"]) not in response.text
    image = client.get(fixture["url"])
    assert image.status_code == 200 and image.data == PNG and image.mimetype == "image/png"
    assert "no-store" in image.headers["Cache-Control"]
    assert image.headers["X-Content-Type-Options"] == "nosniff"
    assert image.headers["Cross-Origin-Resource-Policy"] == "same-origin"
    assert "ETag" not in image.headers and "Last-Modified" not in image.headers
    body = client.get(f"/sessao/{fixture['id']}").text
    assert fixture["url"] in body and CAPTION in body and "Source Author" in body
    assert "PRIVATE ORIGINAL CAPTION" not in body and "PRIVATE SOURCE TITLE" not in body
    assert len(request_exam(client, fixture["id"]).json["media"]) == 1


def test_media_does_not_cross_user_boundary(app, client, login, media_session):
    request_exam(client, media_session["id"])
    client.post("/sair")
    assert client.get(media_session["url"]).status_code == 302
    login("colega@example.invalid")
    assert client.get(media_session["url"]).status_code == 404
    client.post("/sair")
    login("admin@example.invalid", admin=True)
    assert client.get(media_session["url"]).status_code == 404


def test_machine_review_is_explicit_and_never_presented_as_human(client, media_session):
    def machine(metadata):
        metadata["privacyStatus"] = "machine_reviewed"
        metadata["caseBindings"][0].update(status="machine_reviewed", reviewMode="machine")
    edit_metadata(media_session, machine)
    result = request_exam(client, media_session["id"])
    assert result.json["media"][0]["review_mode"] == "machine"
    assert client.get(media_session["url"]).status_code == 200
    page = client.get(f"/sessao/{media_session['id']}").text
    assert "Seleção e conferência por IA." in page


def test_media_uses_immutable_consultation_snapshot(app, client, media_session):
    request_exam(client, media_session["id"])
    with app.app_context():
        current = db.session.get(ClinicalCase, media_session["case_id"])
        updated = deepcopy(current.data)
        updated["examMedia"] = []
        current.data = updated
        db.session.commit()
    assert client.get(media_session["url"]).data == PNG


@pytest.mark.parametrize("change", [
    lambda m: m.update(privacyStatus="pending_visual_review"),
    lambda m: m.update(downloadStatus="pending"),
    lambda m: m.update(rightsStatus="review_required"),
    lambda m: m.update(pmcid="PMC999"),
    lambda m: m.update(xmlSha256="b" * 64),
    lambda m: m.update(metadataSha256="d" * 64),
    lambda m: m.update(pmcVersion=999),
    lambda m: m["license"].update(url="javascript:alert(1)"),
    lambda m: m.update(license={"code": "CC0", "url": "https://creativecommons.org/publicdomain/zero/1.0/"}),
    lambda m: m.update(caseBindings=[]),
    lambda m: m["caseBindings"][0].update(status="pending"),
    lambda m: m["caseBindings"][0].update(patientEvidence=""),
    lambda m: m["caseBindings"][0].update(caseId="mcr-pmc999"),
    lambda m: m["caseBindings"][0].update(examName="Outro exame"),
    lambda m: m["caseBindings"][0].update(captionPt="Legenda alterada sem revisão"),
])
def test_media_requires_matching_source_patient_exam_and_review(client, media_session, change):
    edit_metadata(media_session, change)
    response = request_exam(client, media_session["id"])
    assert response.status_code == 200 and response.json["media"] == []
    assert client.get(media_session["url"]).status_code == 404


@pytest.mark.parametrize("corrupt", ["checksum", "path", "symlink", "mime"])
def test_private_route_revalidates_bytes_and_path(client, media_session, tmp_path, corrupt):
    request_exam(client, media_session["id"])
    if corrupt == "checksum":
        media_session["file"].write_bytes(PNG[:-1] + b"X")
    elif corrupt == "path":
        edit_metadata(media_session, lambda m: m.update(relativePath="../../secret.png"))
    elif corrupt == "mime":
        edit_metadata(media_session, lambda m: m.update(mimeType="text/html"))
    else:
        outside = tmp_path / "outside.png"
        outside.write_bytes(PNG)
        media_session["file"].unlink()
        media_session["file"].symlink_to(outside)
    response = client.get(media_session["url"])
    assert response.status_code == 404 and PNG not in response.data


def test_completed_result_reveals_case_images_without_requiring_prior_exam(app, client, media_session):
    with app.app_context():
        training = db.session.get(Consultation, media_session["id"])
        training.status = "completed"
        training.evaluation = evaluation_payload()
        training.score = 73
        training.diagnosis = "Test hypothesis"
        training.treatment = "Test plan"
        db.session.commit()
    response = client.get(f"/sessao/{media_session['id']}/resultado")
    assert response.status_code == 200 and "As imagens deste caso." in response.text
    assert "Disponível nesta revisão" in response.text and CAPTION in response.text
    assert "Source citation" in response.text and "Original CC notice" in response.text
    assert "PRIVATE ORIGINAL CAPTION" not in response.text
    assert client.get(media_session["url"]).data == PNG
