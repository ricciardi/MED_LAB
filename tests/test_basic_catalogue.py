"""Basic-track metadata stays public-safe and imports preserve existing records."""
from copy import deepcopy
from html import unescape
import json
import re
from urllib.parse import parse_qs, urlparse

import pytest
from flask import render_template
from sqlalchemy import inspect, text

from medlab.case_schema import CaseValidationError, validate_case
from medlab.catalog import BODY_SYSTEMS
from medlab.catalogue import filtered_cases
from medlab.cli import import_basic_cases
from medlab.extensions import db
from medlab.models import CaseDiscovery, ClinicalCase, Consultation
from test_catalogue_presentation import public_card, render_catalogue


def basic_case(case_data, identifier="basic-synthetic", system="urinary"):
    data = deepcopy(case_data)
    data.update(caseId=identifier, learningTrack="basic", bodySystem=system,
                reviewStatus="review_required")
    return data


def write_bundle(tmp_path, payload, name="basic.json"):
    path = tmp_path / name
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.mark.parametrize("system", BODY_SYSTEMS)
def test_basic_schema_preserves_pair_and_private_objectives(case_data, system):
    data = basic_case(case_data, system=system)
    assert validate_case(data) == data
    assert data["catalogType"] == "fictional"
    assert validate_case(case_data) == case_data  # Older records gain no invented metadata.


@pytest.mark.parametrize("changes", [{"learningTrack": "advanced"}, {"bodySystem": "unknown"},
    {"bodySystem": None}, {"bodySystem": []}, {"learningTrack": None}])
def test_invalid_basic_metadata_is_rejected(case_data, changes):
    data = basic_case(case_data); data.update(changes)
    with pytest.raises(CaseValidationError):
        validate_case(data)


@pytest.mark.parametrize("removed", ["learningTrack", "bodySystem"])
def test_basic_metadata_is_a_pair(case_data, removed):
    data = basic_case(case_data); data.pop(removed)
    with pytest.raises(CaseValidationError):
        validate_case(data)


def test_basic_import_and_discovery_preserve_drafts_and_never_index_answer_keys(app, case_data, tmp_path):
    data = basic_case(case_data)
    data["learningObjectives"] = ["Objetivo secreto exclusivo"]
    bundle = write_bundle(tmp_path, [data])
    with app.app_context():
        assert import_basic_cases([bundle]) == {"new": 1, "unchanged": 0}
        case = db.session.scalar(db.select(ClinicalCase).where(ClinicalCase.slug == data["caseId"]))
        assert case.is_published and case.data["reviewStatus"] == "review_required"
        assert case.discovery.learning_track == "basic" and case.discovery.body_system == "urinary"
        assert "sistema urinario" in case.discovery.search_text
        assert all(secret not in case.discovery.search_text for secret in ("secreto", "diagnostico reservado"))
        case.is_published = False
        case.data = {**case.data, "reviewNote": "Revisão local preservada"}
        original = deepcopy(case.data)
        db.session.commit()
        assert import_basic_cases([bundle]) == {"new": 0, "unchanged": 1}
        assert case.data == original and not case.is_published


def test_import_preflights_all_files_and_duplicate_ids_before_insertion(app, case_data, tmp_path):
    good = write_bundle(tmp_path, [basic_case(case_data)])
    bad = write_bundle(tmp_path, [{"invalid": True}], "bad.json")
    with app.app_context():
        before = db.session.scalar(db.select(db.func.count(ClinicalCase.id)))
        for paths in ([good, bad], [good, good]):
            with pytest.raises(Exception, match="pacote de Casos Básicos é inválido"):
                import_basic_cases(paths)
            assert db.session.scalar(db.select(db.func.count(ClinicalCase.id))) == before


def test_basic_filters_combine_without_searching_objectives_and_system_implies_track(app, case_data, tmp_path):
    urinary = basic_case(case_data)
    respiratory = basic_case(case_data, "basic-other", "respiratory")
    with app.app_context():
        import_basic_cases([write_bundle(tmp_path, [urinary, respiratory])])
        for filters in ({"system": "urinary"}, {"track": "basic", "system": "urinary", "exams": "results", "difficulty": "Iniciante"}):
            query, context = filtered_cases(filters)
            assert [case.slug for case in db.session.scalars(query)] == [urinary["caseId"]]
            assert context["selected_track"] == "basic" and context["selected_system"] == "urinary"
        query, _ = filtered_cases({"track": "basic", "q": urinary["diagnoses"]["mainDiagnosis"]["disease"]})
        assert list(db.session.scalars(query)) == []
        _, context = filtered_cases({"track": "unknown", "system": "<script>"})
        assert context["selected_track"] == context["selected_system"] == ""


def test_basic_import_commands_are_idempotent_and_upgrade_keeps_existing_cases(app, case_data, tmp_path, monkeypatch):
    import medlab.cli as commands
    bundle = write_bundle(tmp_path, [basic_case(case_data)])
    monkeypatch.setattr(commands, "basic_case_bundles", lambda: [bundle])
    for command in ("init-db", "import-cases", "upgrade-db"):
        result = app.test_cli_runner().invoke(args=[command])
        assert result.exit_code == 0, result.output
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(ClinicalCase.id))) == 12
        case = db.session.scalar(db.select(ClinicalCase).where(ClinicalCase.slug == "basic-synthetic"))
        assert case.data["reviewStatus"] == "review_required"
        assert db.session.get(CaseDiscovery, case.id).body_system == "urinary"


def test_bundled_allowlist_ignores_coverage_and_unrelated_exports(tmp_path, monkeypatch):
    import medlab.cli as commands
    package = tmp_path / "medlab"
    folder = package / "data" / "basic_cases"
    folder.mkdir(parents=True)
    for name in ("urinary.json", "head.json", "coverage_delivery.json", "coverage-root.json", "private-export.json"):
        (folder / name).write_text("[]")
    monkeypatch.setattr(commands, "__file__", str(package / "cli.py"))
    assert [path.name for path in commands.basic_case_bundles()] == ["urinary.json", "head.json"]


def test_basic_random_uses_standard_consultation_and_preserves_snapshot(app, login, case_data, tmp_path):
    data = basic_case(case_data)
    with app.app_context():
        import_basic_cases([write_bundle(tmp_path, [data])])
    client = login()
    response = client.post("/casos/iniciar", data={"track": "basic", "system": "urinary", "exams": "results"})
    assert response.status_code == 302 and "/sessao/" in response.location
    with app.app_context():
        training = db.session.get(Consultation, response.location.rsplit("/", 1)[-1])
        assert training.case_snapshot == data
    body = client.get(response.location).text
    assert 'id="chat-form"' in body and '/static/voice.js' in body
    assert data["diagnoses"]["mainDiagnosis"]["disease"] not in body
    assert data["learningObjectives"][0] not in body


def test_additive_index_migration_keeps_data_and_can_repeat(app):
    from medlab.migrations import upgrade_database
    with app.app_context():
        original = db.session.scalar(db.select(ClinicalCase)).data
        db.session.remove()
        with db.engine.begin() as connection:
            for column in ("learning_track", "body_system"):
                connection.execute(text(f"DROP INDEX ix_case_discovery_{column}"))
                connection.execute(text(f"ALTER TABLE case_discovery DROP COLUMN {column}"))
        upgrade_database(); upgrade_database()
        assert {"learning_track", "body_system"} <= {col["name"] for col in inspect(db.engine).get_columns("case_discovery")}
        assert db.session.scalar(db.select(ClinicalCase)).data == original
        assert all(row.learning_track == row.body_system == "" for row in db.session.scalars(db.select(CaseDiscovery)))


def test_basic_ui_preserves_filters_and_never_renders_private_objectives(app):
    card = public_card(learning_track="basic", body_system="urinary", body_system_label="Sistema urinário",
                      learningObjectives=["Objetivo reservado"], diagnoses={"secret": "Gabarito reservado"})
    body = render_catalogue(app, selected_track="basic", selected_system="urinary", body_systems=BODY_SYSTEMS, cases=[card])
    assert 'id="body-system"' in body and 'value="urinary" selected' in body
    assert all(label in body for label in BODY_SYSTEMS.values())
    assert 'Objetivo reservado' not in body and 'Gabarito reservado' not in body
    for form in re.findall(r'<form method="post" action="/casos/iniciar">(.*?)</form>', body, re.S):
        assert 'name="track" value="basic"' in form and 'name="system" value="urinary"' in form
    link = re.findall(r'href="([^"]+page=2[^"]*)"', body)[0]
    params = parse_qs(urlparse(unescape(link)).query)
    assert params["track"] == ["basic"] and params["system"] == ["urinary"] and params["exams"] == ["results"]
    assert re.search(r'href="/casos\?track=basic" class="active"\s+aria-current="page"', body)


def test_basic_question_review_separates_questions_from_context_and_translates_status(app):
    section = {"groups": [{"id": "hpi", "title": "História da doença atual", "score": 50, "assessable": True}],
        "criteria": [{"id": "H01", "groupId": "hpi", "title": "Início", "status": "partial",
            "reason": "Aprofunde a instalação.", "exemption": None,
            "evidence": [{"id": "Q1", "text": "<script>Pergunta literal</script>"}],
            "contextEvidence": [{"id": "A1", "text": "Resposta registrada."}]},
            {"id": "H02", "groupId": "hpi", "title": "Duração", "status": "not_applicable",
             "reason": "Informação indisponível.", "exemption": "source_unavailable", "evidence": [], "contextEvidence": []}]}
    with app.test_request_context():
        body = render_template("basic_questions.html", section=section)
    assert "50<small>/100</small>" in body and "Parcial" in body and "Não aplicável" in body
    assert "PERGUNTAS REGISTRADAS" in body and "CONTEXTO CONSIDERADO" in body
    assert "&lt;script&gt;Pergunta literal&lt;/script&gt;" in body and "<script>" not in body
    assert "Informação não disponível no cenário." in body and "source_unavailable" not in body
