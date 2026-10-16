"""Discovery is diagnosis-free, reproducible and uses actual available results."""
from copy import deepcopy
import json

import pytest

from medlab.discovery import (difficulty_for, exam_groups, folded, presentation_types,
                              rebuild_discovery, result_entries)
from medlab.extensions import db
from medlab.models import CaseDiscovery, ClinicalCase, Consultation
from test_catalogue import add_case, card_ids
from test_exam_media import media_session, edit_metadata  # Synthetic, isolated files and DB.


def local_case(case_data, complaint, history="Não informado na fonte."):
    data = deepcopy(case_data)
    data["provenance"] = {"localPreparation": {"model": "synthetic-test"}}
    data["patientProfile"]["chief_complaint"] = complaint
    data["patientProfile"]["present_illness_history"] = history
    return data


@pytest.mark.parametrize("complaint,expected", [
    ("Nega dor torácica, falta de ar e febre. Apresenta dor abdominal.", ["Dor abdominal"]),
    ("Não relata tosse. Nega cefaleia. Refere corrimento vaginal.", ["Sintomas ginecológicos"]),
    ("Tosse com febre há três dias, sem dor torácica.", ["Sintomas respiratórios", "Febre e sintomas gerais"]),
    ("Dor abdominal com náuseas e vômitos.", ["Dor abdominal", "Sintomas digestivos"]),
    ("Perda da visão desde ontem.", ["Alterações visuais"]),
    ("Dor de garganta e zumbido.", ["Sintomas de ouvido, nariz e garganta"]),
    ("Sem febre, mas com tosse persistente.", ["Sintomas respiratórios"]),
    ("Corrimento nasal, sem febre.", ["Sintomas de ouvido, nariz e garganta"]),
    ("Queda de cabelo.", ["Alterações da pele"]),
    ("Queda da pálpebra.", ["Alterações visuais"]),
    ("Dores de cabeça e visão borrada.", ["Cefaleia e sintomas neurológicos", "Alterações visuais"]),
    ("Massa de pescoço.", ["Nódulos e massas"]),
    ("Aumento da massa corporal.", ["Outros sintomas"]),
    ("Não apresentava febre ou tosse. Corrimento vaginal.", ["Sintomas ginecológicos"]),
    ("Febre e tosse ausentes. Corrimento vaginal.", ["Sintomas ginecológicos"]),
])
def test_types_follow_affirmed_complaint_not_regex_priority(case_data, complaint, expected):
    data = local_case(case_data, complaint, "Teve dor torácica há dez anos.")
    assert presentation_types(data) == expected


def test_source_titles_diagnosis_and_article_do_not_classify_or_override_authored_data(case_data):
    data = local_case(case_data, "Estou com tosse.")
    baseline = presentation_types(data)
    data.update(caseTitle="Dor torácica", referenceReasoning="Dor torácica", treatments="Dor abdominal")
    data["diagnoses"]["mainDiagnosis"]["disease"] = "Perda da visão"
    assert presentation_types(data) == baseline == ["Sintomas respiratórios"]
    data.pop("provenance"); data["caseType"] = "Acompanhamento e prevenção"
    assert presentation_types(data) == ["Acompanhamento e prevenção"]


def test_pediatric_age_is_not_a_replacement_for_specific_complaint(case_data):
    data = local_case(case_data, "Estou com dor de cabeça.")
    data["patientProfile"]["age_value"] = 8
    assert presentation_types(data) == ["Cefaleia e sintomas neurológicos"]
    data["patientProfile"]["chief_complaint"] = "Não informado na fonte."
    assert presentation_types(data) == ["Queixa pediátrica"]


def test_mixed_placeholder_preserves_later_bound_patient_statement(case_data):
    data = local_case(case_data, "Não informado na fonte.",
        "Menções clínicas extraídas; relação com o paciente ainda não revisada: dor torácica.\n"
        "Antecedentes documentados: cirurgia prévia.\nRelata dor de cabeça e fadiga.")
    assert presentation_types(data) == ["Cefaleia e sintomas neurológicos", "Febre e sintomas gerais"]
    assert difficulty_for(data, {})[0] != "Não classificada"


def test_unknown_mentions_and_continuations_do_not_inflate_exam_availability(case_data):
    data = deepcopy(case_data)
    data["auxiliaryLabs"] = {"A": "Não informado na fonte.", "B": "",
        "C": "Menções clínicas extraídas; relação com o paciente ainda não revisada: sangue.",
        "Hemograma (parte 1 de 2)": "Hemoglobina: 12 g/dL.",
        "Hemograma (continuação 2 de 2)": "Leucócitos: 7000/mm³.", "D": "Normal."}
    entries = result_entries(data, "auxiliaryLabs")
    assert len(entries) == 3 and len(exam_groups(entries)) == 2
    assert "D" in entries


def test_difficulty_measures_script_workload_and_preserves_authored_level(case_data):
    data = local_case(case_data, "Tosse.")
    data["patientProfile"] = {"chief_complaint": "Tosse.", "present_illness_history": "Tosse há um dia."}
    assert difficulty_for(data, {})[:2] == ("Iniciante", True)
    labs = {f"Grupo {i}": "Resultado documentado." for i in range(3)}
    assert difficulty_for(data, labs)[0] == "Intermediário"
    data["patientProfile"].update(past_medical_history="Antecedente descrito.", personal_history="Rotina descrita.")
    labs.update({f"Grupo {i}": "Resultado documentado." for i in range(3, 6)})
    assert difficulty_for(data, labs)[0] == "Avançado"
    data["patientProfile"] = {"chief_complaint": "Não informado na fonte."}
    assert difficulty_for(data, labs)[0] == "Não classificada"
    data.pop("provenance"); data["difficulty"] = "Intermediário"
    assert difficulty_for(data, labs)[:2] == ("Intermediário", False)


def test_filters_search_without_accents_and_include_secondary_presentations(app, login, case_data):
    with app.app_context():
        data = local_case(case_data, "Tosse com febre.")
        expected = add_case(data, index="local", specialty="Clínica médica")
        db.session.commit()
    client = login()
    for search in ("clinica", "CLÍNICA", "Clínica"):
        assert expected in card_ids(client.get("/casos", query_string={"q": search}))
    for kind in ("Sintomas respiratórios", "Febre e sintomas gerais"):
        assert card_ids(client.get("/casos", query_string={"case_type": kind})) == [expected]
    for secret in ("Diagnóstico reservado do cenário", "%", "_"):
        assert card_ids(client.get("/casos", query_string={"q": secret})) == []


def test_result_filter_combines_with_level_type_pagination_and_random(app, login, case_data):
    with app.app_context():
        expected = {add_case(case_data, index=i) for i in range(14)}
        empty = deepcopy(case_data); empty["auxiliaryLabs"] = {"X": "Não informado na fonte."}
        add_case(empty, index=40)
        add_case(case_data, index=41, difficulty="Avançado")
        add_case(case_data, index=42, published=False)
        db.session.commit()
    client = login()
    filters = {"specialty": "Cardiologia", "case_type": "Dor torácica", "difficulty": "Intermediário", "exams": "results"}
    pages = [card_ids(client.get("/casos", query_string={**filters, "page": p})) for p in (1, 2)]
    assert [len(p) for p in pages] == [12, 2]
    assert set(pages[0] + pages[1]) == expected
    started = client.post("/casos/iniciar", data=filters)
    with app.app_context():
        assert db.session.get(Consultation, started.location.rsplit("/", 1)[-1]).case_id in expected


def test_index_updates_on_edits_and_rebuild_is_idempotent_without_changing_source(app):
    with app.app_context():
        record = db.session.scalar(db.select(ClinicalCase))
        old = deepcopy(record.data)
        record.data = {**record.data, "auxiliaryLabs": {}}
        db.session.commit()
        assert record.discovery.exam_result_count == 0
        record.data = old
        db.session.commit()
        assert record.discovery.exam_result_count == 1
        before = [(c.id, c.data, c.updated_at, c.case_type, c.difficulty) for c in db.session.scalars(db.select(ClinicalCase))]
        rebuild_discovery(2); rebuild_discovery(2)
        after = [(c.id, c.data, c.updated_at, c.case_type, c.difficulty) for c in db.session.scalars(db.select(ClinicalCase))]
        assert before == after
        assert db.session.scalar(db.select(db.func.count(CaseDiscovery.case_id))) == 3


def test_unbound_exam_mentions_are_not_offered_as_results(app, login):
    with app.app_context():
        case = db.session.scalar(db.select(ClinicalCase))
        data = deepcopy(case.data)
        data["auxiliaryLabs"] = {"Exame sem achado": "Menções clínicas extraídas; relação com o paciente ainda não revisada: sangue."}
        case.data = data; identifier = case.id
        db.session.commit()
    client = login()
    started = client.post("/casos/iniciar", data={"case_id": identifier})
    assert "Exame sem achado" not in client.get(started.location).text
    response = client.post(started.location + "/exame", json={"type": "auxiliary_exam", "name": "Exame sem achado"})
    assert response.status_code == 400


def test_image_filter_rechecks_revoked_binding_or_missing_file(client, media_session):
    fixture = media_session
    assert card_ids(client.get("/casos?exams=images")) == [fixture["case_id"]]
    body = client.get("/casos?exams=images").text
    assert "1 imagem disponível" in body and "PRIVATE ORIGINAL CAPTION" not in body
    edit_metadata(fixture, lambda meta: meta.update(privacyStatus="pending_visual_review"))
    assert card_ids(client.get("/casos?exams=images")) == []
    edit_metadata(fixture, lambda meta: meta.update(privacyStatus="approved"))
    fixture["file"].unlink()
    assert card_ids(client.get("/casos?exams=images")) == []
