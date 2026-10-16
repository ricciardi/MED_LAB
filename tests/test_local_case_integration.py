"""Local preparation/availability contracts; synthetic inputs, isolated DB, no API."""
from copy import deepcopy
import json

import pytest

from medlab import cli
from medlab.case_schema import CaseValidationError, UNKNOWN_FACT, is_provisionally_accepted, validate_case
from medlab.draft_bundle import bundle_shards
from medlab.extensions import db
from medlab.local_case_integration import assemble_local_case, authorize_provisional_use
from medlab.local_case_preparation import extract_case_record
from medlab.models import ClinicalCase, SourceRecord
from medlab.source_registry import source_counts
from test_bundled_drafts import create_bundle, invoke
from test_corpus_cli import corpus_apps
from test_importer import provenance, real_case


REFERENCE = "Autorização sintética do proprietário; revisão clínica adiada."
MODEL = {"model": "synthetic-local-translator", "revision": "test-only"}


def package_inputs(*, factual=False):
    text = "A 35-year-old woman presented with cough for three weeks. Chest CT showed an opacity."
    origin = provenance()
    if factual:
        origin.update(reuseBasis="clinical_facts", license={"code": "CC BY-ND",
            "url": "https://creativecommons.org/licenses/by-nd/4.0/", "version": "4.0", "text": "Synthetic ND license"})
    package = extract_case_record({"pmcid": "PMC123", "case_prompt": text,
        "final_diagnosis": "Synthetic pneumonia", "diagnostic_reasoning": "Synthetic private reasoning."},
        {"provenance": origin, "articleText": text + " The diagnosis was synthetic pneumonia."})
    words = {text.split(". ")[0] + ".": "Uma mulher de 35 anos apresentou tosse por três semanas.",
        "Chest CT showed an opacity.": "A tomografia do tórax mostrou uma opacidade.",
        "Synthetic pneumonia": "Pneumonia sintética", "cough": "tosse", "CT": "TC",
        "patient": "paciente", "cough for three weeks": "tosse por três semanas", "an opacity": "uma opacidade",
        "three weeks": "três semanas", "Chest": "Tórax", "Synthetic private reasoning.": "Raciocínio privado sintético."}
    translations = {unit["id"]: words.get(unit["text"], "Trecho sintético traduzido para revisão.") for unit in package["translationUnits"]}
    return package, translations


def local_case(*, accepted=True):
    package, translations = package_inputs()
    return assemble_local_case(package, translations, MODEL, accepted_for_use=accepted,
                               authorization_reference=REFERENCE if accepted else None)


def test_local_case_records_actual_method_and_keeps_review_pending():
    case = local_case()
    assert validate_case(case) == case
    assert case["reviewStatus"] == "review_required" and is_provisionally_accepted(case)
    assert case["provenance"]["audit"] == {"method": "not_performed",
        "reason": "Preparação local; revisão clínica e auditoria semântica ainda não realizadas."}
    preparation = case["provenance"]["localPreparation"]
    assert preparation["model"] == MODEL["model"] and preparation["sourceSha256"] == case["provenance"]["xmlSha256"]
    assert preparation["translated"] is True and preparation["clinicalReviewDeferred"] is True
    assert case["patientProfile"]["gender"] == "Feminino" and case["patientProfile"]["age_value"] == 35
    assert case["caseType"] == "Sintomas respiratórios"
    assert "tosse" in case["patientProfile"]["present_illness_history"]
    assert "opacidade" not in case["patientProfile"]["present_illness_history"]
    assert any("opacidade" in value for value in case["auxiliaryLabs"].values())
    assert case["provenance"]["evidence"] == []


def test_availability_requires_explicit_authorization_and_never_relabels_legacy_audit():
    assert not is_provisionally_accepted(local_case(accepted=False))
    legacy = real_case()
    authorized = authorize_provisional_use(legacy, REFERENCE)
    assert authorized["provenance"] == legacy["provenance"]
    assert "localPreparation" not in authorized["provenance"]
    assert "useAuthorization" not in legacy
    with pytest.raises(CaseValidationError):
        authorize_provisional_use(legacy, None)
    legacy["reviewStatus"] = "reviewed"
    with pytest.raises(ValueError):
        authorize_provisional_use(legacy, REFERENCE)


@pytest.mark.parametrize("mutation", ["source", "model", "translated", "audit", "authorization", "status"])
def test_local_contract_cannot_forge_review_or_translation(mutation):
    case = local_case()
    if mutation == "source": case["provenance"]["localPreparation"]["sourceSha256"] = "d" * 64
    elif mutation == "model": case["provenance"]["localPreparation"]["model"] = ""
    elif mutation == "translated": case["provenance"]["localPreparation"]["translated"] = False
    elif mutation == "audit": case["provenance"]["audit"] = {"factsSupported": True}
    elif mutation == "authorization": case["useAuthorization"]["acceptedForUse"] = "true"
    elif mutation == "status": case["reviewStatus"] = "reviewed"
    with pytest.raises(CaseValidationError):
        validate_case(case)


def test_missing_translation_is_not_counted_as_translated():
    package, translations = package_inputs()
    translations.pop(next(iter(translations)))
    with pytest.raises(ValueError, match="Tradução pendente"):
        assemble_local_case(package, translations, MODEL)


def test_long_exam_results_remain_complete_in_ordered_continuations():
    package, translations = package_inputs()
    long_result = "\n".join("Achado documentado " + str(number) + ": descrição preservada integralmente." for number in range(170))
    for unit in package["translationUnits"]:
        if unit.get("kind") == "source_sentence" and unit["text"] == "Chest CT showed an opacity.":
            translations[unit["id"]] = long_result
    case = assemble_local_case(package, translations, MODEL)
    exams = case["auxiliaryLabs"]
    assert len(exams) >= 3 and all(len(value) <= 3000 for value in exams.values())
    assert "parte 1 de" in next(iter(exams))
    assert "continuação 2 de" in list(exams)[1]
    assert " ".join(" ".join(exams.values()).split()) == " ".join(long_result.split())
    assert case["optionalAuxiliaryLabs"] == list(exams)


def test_explicit_parent_report_is_preserved_without_inventing_identity():
    package, translations = package_inputs()
    package["snippets"][0]["text"] = "The mother reported cough for three weeks."
    case = assemble_local_case(package, translations, MODEL)
    assert "Responsável pelo paciente fornece o relato" in case["patientProfile"]["respondent"]
    package["snippets"][0]["text"] = "The mother had a history of cough."
    case = assemble_local_case(package, translations, MODEL)
    assert "respondent" not in case["patientProfile"]


def test_recurrent_initial_symptoms_are_not_removed_by_lexical_temporal_label():
    package, translations = package_inputs()
    for snippet in package["snippets"]:
        if snippet.get("patientCandidate"):
            snippet["temporal"] = "later_or_treatment"
            snippet["groups"] = ["unclassified"]
    case = assemble_local_case(package, translations, MODEL)
    assert "tosse" in case["patientProfile"]["present_illness_history"]


def test_unknown_core_is_explicit_only_for_unaudited_local_preparation():
    package, translations = package_inputs()
    for snippet in package["snippets"]: snippet["patientCandidate"] = False
    case = assemble_local_case(package, translations, MODEL)
    assert case["patientProfile"]["chief_complaint"] == UNKNOWN_FACT
    old = real_case()
    old["patientProfile"]["chief_complaint"] = UNKNOWN_FACT
    with pytest.raises(CaseValidationError): validate_case(old)


def test_factual_local_path_preserves_nd_without_claiming_audited_propositions():
    package, translations = package_inputs(factual=True)
    case = assemble_local_case(package, translations, MODEL)
    assert case["provenance"]["license"]["code"] == "CC BY-ND"
    assert case["provenance"]["sourceFacts"] == case["provenance"]["factualEvidence"] == []
    assert case["patientProfile"]["present_illness_history"] == "Relata tosse por três semanas."
    assert any("uma opacidade" in result for result in case["auxiliaryLabs"].values())
    assert "opacidade" not in case["patientProfile"]["present_illness_history"]
    assert "A 35-year-old" not in json.dumps(case)
    package["translationUnits"][0]["kind"] = "source_sentence"
    with pytest.raises(ValueError, match="não traduz a narrativa"):
        assemble_local_case(package, translations, MODEL)


def test_restore_keeps_provisional_availability_distinct_from_source_verification(corpus_apps, tmp_path, monkeypatch):
    case = local_case()
    ordinary = real_case("PMC456")
    create_bundle(tmp_path, monkeypatch, [case, ordinary])
    application = corpus_apps()
    invoke(application, "init-db")
    with application.app_context():
        current = db.session.scalar(db.select(ClinicalCase).where(ClinicalCase.slug == case["caseId"]))
        assert current.is_published and current.data["reviewStatus"] == "review_required"
        ordinary_row = db.session.scalar(db.select(ClinicalCase).where(ClinicalCase.slug == ordinary["caseId"]))
        assert not ordinary_row.is_published
        assert source_counts()["verified"] == 0
        assert source_counts()["translated"] == 2 and source_counts()["published"] == 1
        assert source_counts()["drafts"] == 1
        assert all(s.checkpoint_sha256 is None for s in db.session.scalars(db.select(SourceRecord)))
        report = cli.restore_bundled_drafts()
        assert report["unchanged"] == 2 and report["new"] == report["provisional"] == 0


def test_export_preserves_active_acceptance_but_not_suspended_availability(corpus_apps, tmp_path, monkeypatch):
    create_bundle(tmp_path, monkeypatch, [local_case()])
    application = corpus_apps()
    invoke(application, "init-db")
    first = tmp_path / "first"
    result = application.test_cli_runner().invoke(args=["export-real-drafts", "--output", str(first)])
    assert result.exit_code == 0, result.output
    exported = json.loads(bundle_shards(first)[0].read_text())[0]
    assert is_provisionally_accepted(exported)
    with application.app_context():
        row = db.session.scalar(db.select(ClinicalCase).where(ClinicalCase.slug == "mcr-pmc123"))
        row.is_published = False
        db.session.commit()
    second = tmp_path / "second"
    result = application.test_cli_runner().invoke(args=["export-real-drafts", "--output", str(second)])
    assert result.exit_code == 0, result.output
    suspended = json.loads(bundle_shards(second)[0].read_text())[0]
    assert not is_provisionally_accepted(suspended)


def test_admin_provisional_action_is_explicit_private_and_keeps_pending_review(app, client, login):
    data = local_case(accepted=False)
    with app.app_context():
        row = ClinicalCase(slug=data["caseId"], title=data["caseTitle"], specialty=data["specialty"],
            summary=data["summary"], difficulty=data["difficulty"], data=data, is_published=False)
        db.session.add(row); db.session.commit(); identity = row.id
    endpoint = f"/gestao-teste/casos/{identity}/uso-provisorio"
    assert client.post(endpoint, data={"provisional_confirmed": "yes"}).status_code == 404
    login("admin@example.invalid", admin=True)
    client.post(endpoint, data={})
    with app.app_context(): assert not db.session.get(ClinicalCase, identity).is_published
    client.post(endpoint, data={"provisional_confirmed": "yes"})
    with app.app_context():
        current = db.session.get(ClinicalCase, identity)
        assert current.is_published and is_provisionally_accepted(current.data)
        assert current.data["provenance"]["audit"]["method"] == "not_performed"
    page = client.get(f"/gestao-teste/casos/{identity}").text
    assert "Uso provisório autorizado" in page and "Revisão clínica pendente" in page
    client.post("/sair")
    login()
    response = client.post("/casos/iniciar", data={"case_id": identity})
    page = client.get(response.location).text
    assert "Ficha com revisão clínica pendente" in page
    assert "Pneumonia sintética" not in page and "synthetic-local-translator" not in page
