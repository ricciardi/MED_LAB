"""Explicit informant voice metadata never changes the patient's sex."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from medlab.case_schema import UNKNOWN_FACT, validate_case
from medlab.extensions import db
from medlab.models import Consultation, Turn
from medlab.speech import voice_options
from test_speech import voice_assets


@pytest.mark.parametrize("patient_gender,informant_gender,expected_voice,locked", [
    ("Menino", "female", "pf_dora", True),
    ("Menina", "male", "pm_alex", True),
    ("Menino", "unknown", "pf_dora", False),
])
def test_explicit_informant_gender_does_not_overwrite_patient_sex(case_data, patient_gender, informant_gender, expected_voice, locked):
    case_data["patientProfile"].update(gender=patient_gender, age_value=6,
        respondent="Pessoa responsável que acompanha a criança.", respondent_gender=informant_gender)
    original = deepcopy(case_data)
    checked = validate_case(case_data)
    options = voice_options(checked)
    assert options["default_voice"] == expected_voice and options["gender_locked"] is locked
    assert options["gender"] == informant_gender
    assert checked["patientProfile"]["gender"] == patient_gender
    assert case_data == original
    if informant_gender == "unknown":
        assert {row["gender"] for row in options["voices"]} == {"female", "male"}


@pytest.mark.parametrize("invalid", ["Feminino", "mother", "", None, True, [], {}])
def test_informant_gender_has_a_closed_enum(case_data, invalid):
    case_data["patientProfile"].update(respondent="Responsável.", respondent_gender=invalid)
    with pytest.raises(ValueError, match="patientProfile.respondent_gender"):
        validate_case(case_data)


@pytest.mark.parametrize("respondent", [None, "", UNKNOWN_FACT, "unknown"])
def test_informant_gender_requires_an_identified_respondent(case_data, respondent):
    case_data["patientProfile"]["respondent_gender"] = "female"
    if respondent is not None:
        case_data["patientProfile"]["respondent"] = respondent
    with pytest.raises(ValueError, match="patientProfile.respondent"):
        validate_case(case_data)


def test_legacy_metadata_does_not_infer_gender_from_mother_text(case_data):
    case_data["patientProfile"].update(gender="Menino", respondent="Mãe, Maria, informa a história.")
    assert voice_options(validate_case(case_data))["default_voice"] == "pm_alex"
    assert "respondent_gender" not in case_data["patientProfile"]


@pytest.mark.parametrize("informant_gender,allowed_voice,forbidden_voice", [
    ("female", "pf_dora", "pm_alex"), ("unknown", "pm_alex", None),
])
def test_saved_consultation_voice_uses_informant_but_keeps_patient_header(
    app, client, start_session, voice_assets, informant_gender, allowed_voice, forbidden_voice,
):
    session_id = start_session()
    with app.app_context():
        consultation = db.session.get(Consultation, session_id)
        snapshot = deepcopy(consultation.case_snapshot)
        snapshot["patientProfile"].update(gender="Menino", age_value=6,
            respondent="Mãe responsável pela história.", respondent_gender=informant_gender)
        consultation.case_snapshot = validate_case(snapshot)
        turn = Turn(consultation_id=session_id, question="Onde dói?", answer="Ele aponta a barriga.",
            intent="Localização dos sintomas", request_id="informant-voice-test")
        db.session.add(turn)
        db.session.commit()
        turn_id = turn.id
    config = client.get(f"/sessao/{session_id}/voz/config")
    assert config.status_code == 200 and config.json["gender"] == informant_gender
    assert config.json["gender_locked"] is (informant_gender != "unknown")
    assert "Menino" in client.get(f"/sessao/{session_id}").get_data(as_text=True)
    endpoint = f"/sessao/{session_id}/voz/falar"
    assert client.post(endpoint, json={"turn_id": turn_id, "voice_id": allowed_voice}).status_code == 200
    if forbidden_voice:
        assert client.post(endpoint, json={"turn_id": turn_id, "voice_id": forbidden_voice}).status_code == 400
    with app.app_context():
        assert db.session.get(Consultation, session_id).case_snapshot["patientProfile"]["gender"] == "Menino"


def test_only_seven_explicit_maternal_basic_cases_receive_new_voice_metadata():
    expected = {"basic-digestive-lingua-aumentada", "basic-head-03", "basic-head-05", "basic-head-06",
        "basic-respiratory-02", "basic-urinary-febre-lombar", "basic-urinary-urina-escura-edema"}
    directory = Path(__file__).resolve().parents[1] / "medlab/data/basic_cases"
    tagged = set()
    count = 0
    for name in ("urinary", "digestive", "hematopoietic", "nervous", "respiratory", "cardiovascular", "head"):
        for case in json.loads((directory / f"{name}.json").read_text()):
            checked = validate_case(case)
            count += 1
            if "respondent_gender" in checked["patientProfile"]:
                tagged.add(case["caseId"])
                assert checked["patientProfile"]["respondent_gender"] == "female"
                assert voice_options(checked)["default_voice"] == "pf_dora"
                assert checked["patientProfile"]["gender"] == case["patientProfile"]["gender"]
    assert count == 60 and tagged == expected
