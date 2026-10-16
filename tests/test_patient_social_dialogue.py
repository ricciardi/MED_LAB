"""Social elaboration is allowed without bypassing clinical/privacy guards."""
from copy import deepcopy
import json
from unittest.mock import Mock

import pytest

from medlab.ai import AIError
from medlab.patient_dialogue import PATIENT_PROMPT, REPAIR_INSTRUCTIONS
from test_ai import reply, seed, service


def output(answer, details=None):
    return {"answer": answer, "intents": ["Informações pessoais"], "newDetails": details or []}


@pytest.mark.parametrize("question,answer", [
    ("Qual é sua religião?", "Sou católica, mas não frequento a igreja toda semana."),
    ("Onde você nasceu?", "Nasci no interior de São Paulo."),
    ("É casada?", "Sou casada."),
    ("Como é sua moradia?", "Moro numa casa pequena com minha família."),
    ("Até que ano estudou?", "Terminei o ensino médio."),
    ("Tem alguém para ajudar?", "Minha irmã costuma me ajudar quando preciso."),
    ("Consegue chegar às consultas?", "Normalmente venho de ônibus e organizo o dinheiro da passagem."),
])
def test_social_detail_under_personal_information_is_accepted_in_one_call(
    seed, service, monkeypatch, question, answer,
):
    before = deepcopy(seed)
    detail = {"topic": "Informações pessoais", "detail": answer}
    post = Mock(return_value=reply(output(answer, [detail])))
    monkeypatch.setattr("medlab.ai.requests.post", post)
    result = service.respond(seed, question, [])
    assert result["answer"] == answer and post.call_count == 1
    assert result["simulation_state"] == {"version": 1, "startedAfterTurn": 0,
        "details": [{**detail, "turn": 1}]}
    assert seed == before


def test_religion_and_housing_remain_in_memory_across_general_followups(seed, service, monkeypatch):
    religion = {"topic": "Informações pessoais", "detail": "É católica, sem prática semanal."}
    housing = {"topic": "Informações pessoais", "detail": "Mora com a irmã numa casa pequena."}
    answers = [output("Sou católica, mas não vou à igreja toda semana.", [religion]),
        output("Moro com minha irmã numa casa pequena.", [housing]),
        output("Sim, sou católica, como comentei. Não costumo ir toda semana.")]
    post = Mock(side_effect=[reply(value) for value in answers])
    monkeypatch.setattr("medlab.ai.requests.post", post)
    state, history = None, []
    for question in ("Qual sua religião?", "Com quem mora?", "Então você é católica?"):
        result = service.respond(seed, question, history, simulation_state=state)
        state = result["simulation_state"]
        history.append({"question": question, "answer": result["answer"], "intentClassification": result["intent"]})
    assert post.call_count == 3
    assert state["details"] == [{**religion, "turn": 1}, {**housing, "turn": 2}]
    sent = json.loads(post.call_args.kwargs["json"]["messages"][1]["content"])
    assert sent["simulationState"] == state and sent["history"] == history[:-1]


def test_two_social_details_do_not_consume_the_single_new_clinical_detail_limit(seed, service, monkeypatch):
    details = [{"topic": "Informações pessoais", "detail": "É católica."},
        {"topic": "Informações pessoais", "detail": "Mora com a irmã."},
        {"topic": "Sintomas associados", "detail": "Não sente náusea."}]
    post = Mock(return_value=reply(output("Sou católica, moro com minha irmã e não sinto náusea.", details)))
    monkeypatch.setattr("medlab.ai.requests.post", post)
    result = service.respond(seed, "Qual sua religião, com quem mora e sente náusea?", [])
    assert result["simulation_state"]["details"] == [{**row, "turn": 1} for row in details]
    assert post.call_count == 1


def test_social_label_does_not_remove_the_limit_on_two_other_clinical_details(seed, service, monkeypatch):
    details = [{"topic": "Informações pessoais", "detail": "É católica."},
        {"topic": "Sintomas associados", "detail": "Sente náusea."},
        {"topic": "Hábitos intestinais e urinários", "detail": "Urina mais vezes."}]
    post = Mock(return_value=reply(output("Sou católica. Sinto náusea e urino mais vezes.", details)))
    monkeypatch.setattr("medlab.ai.requests.post", post)
    with pytest.raises(AIError, match="fala válida"):
        service.respond(seed, "Pode contar mais?", [])
    assert post.call_count == 2
    repair = json.loads(post.call_args.kwargs["json"]["messages"][1]["content"])["repair"]
    assert repair["reason"] == "too_many_clinical_details"


@pytest.mark.parametrize("detail", ["Tenho diabetes.", "Comecei a tomar insulina.",
    "Fiz cirurgia de retirada da vesícula.", "A tomografia mostrou uma lesão."])
def test_personal_information_label_cannot_smuggle_important_clinical_invention(seed, service, monkeypatch, detail):
    state = {"version": 1, "startedAfterTurn": 0, "details": []}
    post = Mock(return_value=reply(output("Posso contar um pouco da minha vida.",
        [{"topic": "Informações pessoais", "detail": detail}])))
    monkeypatch.setattr("medlab.ai.requests.post", post)
    with pytest.raises(AIError, match="fala válida"):
        service.respond(seed, "Como é sua rotina?", [], simulation_state=state)
    assert post.call_count == 2 and state["details"] == []


def test_private_reference_diagnosis_is_not_allowed_as_a_social_memory(seed, service, monkeypatch):
    post = Mock(return_value=reply(output("Posso falar da minha rotina.", [
        {"topic": "Informações pessoais", "detail": seed["diagnoses"]["mainDiagnosis"]["disease"]}])))
    monkeypatch.setattr("medlab.ai.requests.post", post)
    with pytest.raises(AIError, match="fala válida"):
        service.respond(seed, "E a sua vida?", [])
    repair = json.loads(post.call_args.kwargs["json"]["messages"][1]["content"])["repair"]
    assert repair["reason"] == "private_diagnosis_disclosed"


def test_existing_identity_guard_and_repair_distinguish_social_gaps(seed, service, monkeypatch):
    seed["patientProfile"].pop("respondent", None)
    age = seed["patientProfile"]["age_value"]
    post = Mock(side_effect=[reply(output(f"Tenho {age + 1} anos.")),
        reply(output(f"Tenho {age} anos."))])
    monkeypatch.setattr("medlab.ai.requests.post", post)
    result = service.respond(seed, "Qual sua idade?", [])
    assert result["answer"] == f"Tenho {age} anos." and post.call_count == 2
    repair = json.loads(post.call_args.kwargs["json"]["messages"][1]["content"])["repair"]
    assert repair["reason"] == "documented_identity_changed"
    assert "Lacunas sociais" in repair["instruction"]
    assert "Não crie dados pessoais" not in REPAIR_INSTRUCTIONS["documented_identity_changed"]


def test_documented_social_context_and_identity_are_preserved_and_prompt_prohibits_stereotyping(seed, service, monkeypatch):
    seed["patientProfile"]["basic_info"] = "Religião: sem religião. Naturalidade: Recife."
    original = deepcopy(seed)
    post = Mock(return_value=reply(output("Não sigo uma religião.")))
    monkeypatch.setattr("medlab.ai.requests.post", post)
    result = service.respond(seed, "Qual sua religião?", [])
    sent = json.loads(post.call_args.kwargs["json"]["messages"][1]["content"])
    assert " ".join(sent["patientProfile"]["basic_info"].split()) == original["patientProfile"]["basic_info"]
    assert result["simulation_state"]["details"] == [] and seed == original
    for rule in ("não recuse nem responda desconhecimento só por falta de", "Não deduza crenças",
                 "nome, sexo ou origem", "Religião não implica automaticamente restrição de tratamento",
                 "Não altere nome, idade, sexo ou identidade do informante", "Preserve os dados sociais documentados",
                 "não escolha outra crença", "Não responda defensivamente"):
        assert rule in PATIENT_PROMPT
