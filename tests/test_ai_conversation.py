"""One-call dialogue, append-only elaboration, privacy and bounded correction."""
from copy import deepcopy
import json
from unittest.mock import Mock

import pytest

from medlab.ai import AIError, AIService
from medlab.case_schema import UNKNOWN_FACT
from medlab.patient_dialogue import PatientDialogueError, patient_context, validate_simulation_state
from test_ai import seed, service, reply


def turn(question, answer, intent='Conversa geral'):
    return {'question': question, 'answer': answer, 'intentClassification': intent}


def output(answer, intents=None, details=None):
    return {'answer': answer, 'intents': intents or ['Conversa geral'], 'newDetails': details or []}


def test_social_reply_is_generated_not_a_fixed_unknown_fallback(seed, service, monkeypatch):
    post = Mock(return_value=reply(output('Bom dia, doutora. Pode perguntar, estou um pouco preocupada.')))
    monkeypatch.setattr('medlab.ai.requests.post', post)
    result = service.respond(seed, 'Olá, bom dia!', [])
    assert result['answer'] == 'Bom dia, doutora. Pode perguntar, estou um pouco preocupada.'
    assert result['simulation_state'] == {'version': 1, 'startedAfterTurn': 0, 'details': []}
    assert post.call_count == 1


def test_real_complaint_sequence_can_deepen_and_remember_without_mutating_case(seed, service, monkeypatch):
    before = deepcopy(seed)
    detail = {'topic': 'Preocupações do paciente', 'detail': 'Fico preocupada quando a tosse atrapalha minhas tarefas.'}
    replies = [output('Bom dia! Pode me perguntar.'),
               output('Além da tosse, fico preocupada quando ela atrapalha minhas tarefas.', ['Preocupações do paciente'], [detail]),
               output('Desculpe, antes respondi de forma curta. Posso explicar melhor como isso me incomoda.'),
               output('Como contei, minha preocupação é a tosse atrapalhar as tarefas.', ['Preocupações do paciente'])]
    post = Mock(side_effect=[reply(item) for item in replies])
    monkeypatch.setattr('medlab.ai.requests.post', post)
    history, state = [], None
    for question in ['Olá, bom dia!', 'O que mais sente?', 'Por que voce nao sabe me dizer?', 'O que preocupa você?']:
        result = service.respond(seed, question, history, simulation_state=state)
        state = result['simulation_state']
        history.append(turn(question, result['answer'], result['intent']))
    assert post.call_count == 4 and state['startedAfterTurn'] == 0
    assert state['details'] == [{**detail, 'turn': 2}]
    last = json.loads(post.call_args.kwargs['json']['messages'][1]['content'])
    assert last['simulationState']['details'] == state['details']
    assert last['history'] == history[:-1]
    assert seed == before


def test_mode_starts_after_legacy_history_and_preserves_all_turns(seed, service, monkeypatch):
    history = [turn(f'Pergunta {i}', f'Resposta antiga {i}') for i in range(39)]
    post = Mock(return_value=reply(output('Posso explicar melhor essa sensação.')))
    monkeypatch.setattr('medlab.ai.requests.post', post)
    result = service.respond(seed, 'Pode explicar melhor?', history)
    sent = json.loads(post.call_args.kwargs['json']['messages'][1]['content'])
    assert sent['history'] == history
    assert result['simulation_state']['startedAfterTurn'] == 39
    with pytest.raises(AIError, match='40 interações'):
        service.respond(seed, 'Outra pergunta', history + [turn('Agora?', result['answer'])], result['simulation_state'])
    assert post.call_count == 1


def test_authorized_low_specificity_negative_and_sensation_are_recorded(seed, service, monkeypatch):
    details = [{'topic': 'Sintomas associados', 'detail': 'Não sinto náuseas.'},
               {'topic': 'Preocupações do paciente', 'detail': 'Fico preocupada com essa sensação.'}]
    post = Mock(return_value=reply(output('Não sinto náuseas. Fico preocupada com essa sensação.', ['Sintomas associados'], details)))
    monkeypatch.setattr('medlab.ai.requests.post', post)
    result = service.respond(seed, 'Sente náusea? Como é a sensação?', [])
    assert result['simulation_state']['details'] == [{**detail, 'turn': 1} for detail in details]
    assert post.call_count == 1


def test_repeated_detail_is_not_overwritten_or_duplicated(seed, service, monkeypatch):
    state = {'version': 1, 'startedAfterTurn': 0, 'details': [{'topic': 'Preocupações do paciente', 'detail': 'Estou preocupada.', 'turn': 1}]}
    original = deepcopy(state)
    post = Mock(return_value=reply(output('Continuo preocupada.', ['Preocupações do paciente'], [{'topic': 'Preocupações do paciente', 'detail': 'Estou preocupada.'}])))
    monkeypatch.setattr('medlab.ai.requests.post', post)
    result = service.respond(seed, 'Ainda preocupa?', [turn('Preocupa?', 'Estou preocupada.')], state)
    assert result['simulation_state'] == original == state
    result['simulation_state']['details'][0]['detail'] = 'Mutação externa'
    assert state == original


@pytest.mark.parametrize('bad', [None, [], {'answer': 'Olá'}, output('Olá', ['Categoria inexistente']),
                                 {**output('Olá'), 'simulation_state': {}}, output('Olá', details=[{'topic': [], 'detail': 'x'}]), output('Olá', details=[{'topic': 'Conversa geral', 'detail': 'x', 'turn': 1}])])
def test_bad_output_receives_only_one_repair_and_no_memory_write(seed, service, monkeypatch, bad):
    post = Mock(return_value=reply(bad))
    monkeypatch.setattr('medlab.ai.requests.post', post)
    state = {'version': 1, 'startedAfterTurn': 0, 'details': []}
    with pytest.raises(AIError):
        service.respond(seed, 'Olá', [], state)
    assert post.call_count == 2 and state['details'] == []
    assert service._runtime_deadline is None


def test_repair_uses_only_safe_code_and_discards_rejected_details(seed, service, monkeypatch):
    bad = output(seed['diagnoses']['mainDiagnosis']['disease'], details=[{'topic': 'Conversa geral', 'detail': 'Rejeitado'}])
    post = Mock(side_effect=[reply(bad), reply(output('Ainda estou tentando entender o que sinto.'))])
    monkeypatch.setattr('medlab.ai.requests.post', post)
    result = service.respond(seed, 'Qual é seu diagnóstico?', [])
    assert result['simulation_state']['details'] == [] and post.call_count == 2
    data = json.loads(post.call_args.kwargs['json']['messages'][1]['content'])
    assert data['repair']['reason'] == 'private_diagnosis_disclosed'
    assert seed['diagnoses']['mainDiagnosis']['disease'] not in json.dumps(data, ensure_ascii=False)


def test_repeated_answer_is_regenerated_once_without_fixed_phrase(seed, service, monkeypatch):
    previous = 'Tenho tosse e isso está me incomodando durante o dia.'
    post = Mock(side_effect=[reply(output(previous)), reply(output('Desculpe a repetição. A tosse também me deixa preocupada.', ['Preocupações do paciente'], [{'topic': 'Preocupações do paciente', 'detail': 'A tosse me deixa preocupada.'}]))])
    monkeypatch.setattr('medlab.ai.requests.post', post)
    result = service.respond(seed, 'Isso já contou. O que mais?', [turn('O que sente?', previous)])
    assert result['answer'] != previous and post.call_count == 2
    assert result['simulation_state']['details'][0]['turn'] == 2


def test_requested_repeat_is_allowed(seed, service, monkeypatch):
    answer = 'Tenho tosse e isso está me incomodando durante o dia.'
    post = Mock(return_value=reply(output(answer)))
    monkeypatch.setattr('medlab.ai.requests.post', post)
    assert service.respond(seed, 'Pode repetir o que disse?', [turn('O que sente?', answer)])['answer'] == answer
    assert post.call_count == 1


@pytest.mark.parametrize('answer', ['Tenho diabetes.', 'Estou grávida.', 'Comecei a tomar insulina.', 'A tomografia confirmou uma lesão.'])
def test_important_new_conditions_and_objective_results_are_not_elaboration(seed, service, monkeypatch, answer):
    post = Mock(return_value=reply(output(answer)))
    monkeypatch.setattr('medlab.ai.requests.post', post)
    with pytest.raises(AIError):
        service.respond(seed, 'Pode contar mais?', [])
    assert post.call_count == 2


def test_full_profile_context_excludes_exam_results_future_and_answer_key(seed):
    seed['patientProfile']['present_illness_history'] = 'Relata dor abdominal. Exame de espéculo revelou massa de 8 × 6 cm. Após a cirurgia, recebeu tratamento.'
    seed['patientProfile']['past_medical_history'] = 'Foi diagnosticada previamente com hipertensão.'
    seed['patientProfile']['family_history'] = 'Uma informação familiar documentada.'
    seed['patientProfile']['symptom_details'] = {'Características dos sintomas': UNKNOWN_FACT}
    value = patient_context(seed)
    text = json.dumps(value, ensure_ascii=False)
    assert 'Relata dor abdominal.' in text and 'hipertensão' in text
    assert 'informação familiar' in text
    for excluded in ['espéculo', 'massa de 8', 'Após a cirurgia', UNKNOWN_FACT, seed['diagnoses']['mainDiagnosis']['disease']]:
        assert excluded not in text


@pytest.mark.parametrize('state', [False, {}, {'version': True, 'startedAfterTurn': 0, 'details': []},
    {'version': 1, 'startedAfterTurn': 2, 'details': []},
    {'version': 1, 'startedAfterTurn': 0, 'details': [{'topic': 'Conversa geral', 'detail': 'x', 'turn': 2}]},
    {'version': 1, 'startedAfterTurn': 0, 'details': [{'topic': 'Conversa geral', 'detail': 'x', 'turn': True}]},
    {'version': 1, 'startedAfterTurn': 0, 'details': [{'topic': 'Outro', 'detail': 'x', 'turn': 1}]}])
def test_invalid_memory_rejected_before_provider(seed, service, monkeypatch, state):
    post = Mock(); monkeypatch.setattr('medlab.ai.requests.post', post)
    with pytest.raises(AIError, match='memória'):
        service.respond(seed, 'Olá', [turn('Anterior?', 'Resposta')], state)
    post.assert_not_called()


def test_state_boundaries_reject_duplicates_and_oversized_details():
    item = {'topic': 'Conversa geral', 'detail': 'Um detalhe.', 'turn': 1}
    for details in [[item, item], [{**item, 'detail': 'x' * 801}], [{**item, 'detail': 'x' * 799 + str(i)} for i in range(31)]]:
        with pytest.raises(PatientDialogueError):
            validate_simulation_state({'version': 1, 'startedAfterTurn': 0, 'details': details}, 1)


def test_demo_is_explicit_and_preserves_state_without_network(seed, monkeypatch):
    post = Mock(); monkeypatch.setattr('medlab.ai.requests.post', post)
    demo = AIService({'DEMO_MODE': True})
    state = {'version': 1, 'startedAfterTurn': 0, 'details': [{'topic': 'Preocupações do paciente', 'detail': 'Preocupada.', 'turn': 1}]}
    result = demo.respond(seed, 'Olá, bom dia!', [turn('Oi', 'Olá')], state)
    assert result['answer'].startswith('[Demonstração sem IA] Bom dia!')
    assert result['simulation_state'] == state
    post.assert_not_called()


@pytest.mark.parametrize('text,field', [
    ('Foi diagnosticado com asma na infância.', 'past_medical_history'),
    ('Foi submetido a apendicectomia aos 20 anos.', 'surgery_injury_history'),
])
def test_historical_events_are_not_mistaken_for_current_outcome(seed, text, field):
    seed['patientProfile'][field] = text
    assert patient_context(seed)['patientProfile'][field] == text


@pytest.mark.parametrize('text', [
    'Ao exame físico, apresentava rigidez de nuca.',
    'A radiografia era normal.',
    'Na avaliação inicial, a tomografia mostrou uma lesão expansiva.',
])
def test_objective_findings_are_removed_from_mixed_history(seed, text):
    seed['patientProfile']['present_illness_history'] = 'Relata dor. ' + text
    assert patient_context(seed)['patientProfile']['present_illness_history'] == 'Relata dor.'


@pytest.mark.parametrize('answer', ['Tenho medo de ter câncer.', 'Quero saber se é câncer.', 'Nega ter pressão alta, diabetes ou outras doenças crônicas importantes.'])
def test_concerns_and_compatible_negatives_are_not_new_positive_diagnoses(seed, service, monkeypatch, answer):
    post = Mock(return_value=reply(output(answer, ['Preocupações do paciente'], [{'topic': 'Preocupações do paciente', 'detail': answer}])))
    monkeypatch.setattr('medlab.ai.requests.post', post)
    assert service.respond(seed, 'O que preocupa?', [])['answer'] == answer
    assert post.call_count == 1


@pytest.mark.parametrize('answer,details', [
    ('De remédio todo dia, não tomo nenhum. Só fiz a cirurgia de retirar a vesícula, mas isso faz muitos anos.', [{'topic': 'Cirurgias e traumas', 'detail': 'Fez cirurgia de retirada da vesícula há muitos anos, sem lembrar a data exata.'}]),
    ('Remédio de todo dia eu não tomo nenhum. Eu tenho pressão alta, sim. Tomo um comprimidinho pra pressão todo dia.', [{'topic': 'Doenças crônicas', 'detail': 'Tem pressão alta (hipertensão) há cerca de dez anos.'}]),
])
def test_live_rejected_surgery_and_contradictory_medication_controls(seed, service, monkeypatch, answer, details):
    seed['patientProfile']['past_medical_history'] = UNKNOWN_FACT
    post = Mock(return_value=reply(output(answer, ['Doenças crônicas'], details)))
    monkeypatch.setattr('medlab.ai.requests.post', post)
    with pytest.raises(AIError):
        service.respond(seed, 'Toma remédio ou tem doença importante?', [])
    assert post.call_count == 2
    repair = json.loads(post.call_args.kwargs['json']['messages'][1]['content'])['repair']
    assert repair['reason'] == 'unsupported_important_detail'
    assert 'Não substitua uma invenção por outra' in repair['instruction']


def test_new_clinical_details_are_limited_per_turn(seed, service, monkeypatch):
    details = [{'topic': 'Sintomas associados', 'detail': 'Estou fraca.'},
               {'topic': 'Hábitos intestinais e urinários', 'detail': 'Urino mais vezes.'}]
    post = Mock(return_value=reply(output('Estou fraca e urinando mais.', ['Sintomas associados'], details)))
    monkeypatch.setattr('medlab.ai.requests.post', post)
    with pytest.raises(AIError):
        service.respond(seed, 'O que mais sente?', [])
    assert json.loads(post.call_args.kwargs['json']['messages'][1]['content'])['repair']['reason'] == 'too_many_clinical_details'


@pytest.mark.parametrize('answer', [
    'Bom dia! Pode me chamar de... ah, pode me chamar pelo meu nome mesmo.',
    'Meu nome é Maria. Bom dia!',
])
def test_placeholder_name_is_omitted_and_vacuous_or_invented_name_repaired(seed, service, monkeypatch, answer):
    seed['patientProfile']['name'] = 'Paciente simulado'
    post = Mock(side_effect=[reply(output(answer)), reply(output('Bom dia! Estou pronta para conversar.'))])
    monkeypatch.setattr('medlab.ai.requests.post', post)
    result = service.respond(seed, 'Olá, bom dia!', [])
    assert result['answer'] == 'Bom dia! Estou pronta para conversar.' and post.call_count == 2
    data = json.loads(post.call_args.kwargs['json']['messages'][1]['content'])
    assert 'name' not in data['patientProfile'] and data['repair']['reason'] == 'invented_name_presentation'


@pytest.mark.parametrize('answer', [
    'Eu até meço a temperatura e sempre dá normal.',
    'Uso um termômetro e está tudo normal.',
    'Aferi a pressão e estava normal.',
])
def test_qualitative_objective_measurement_is_not_a_subjective_elaboration(seed, service, monkeypatch, answer):
    post = Mock(return_value=reply(output(answer)))
    monkeypatch.setattr('medlab.ai.requests.post', post)
    with pytest.raises(AIError):
        service.respond(seed, 'Sentiu febre?', [])
    assert json.loads(post.call_args.kwargs['json']['messages'][1]['content'])['repair']['reason'] == 'undocumented_measurement'


def test_negative_subjective_fever_does_not_imply_measurement(seed, service, monkeypatch):
    answer = 'Não medi a temperatura. Não senti febre.'
    detail = {'topic':'Sintomas associados', 'detail': 'Não sentiu febre, sem medir a temperatura.'}
    monkeypatch.setattr('medlab.ai.requests.post', Mock(return_value=reply(output(answer, ['Sintomas associados'], [detail]))))
    assert service.respond(seed, 'Sentiu febre?', [])['answer'] == answer


@pytest.mark.parametrize('question,answer', [
    ('Seu xixi sai líquido ou seco?', 'Líquido. Você quer saber se sai pouco ou se arde?'),
    ('Você respira pelo nariz ou pelo cotovelo?', 'Pelo nariz e pela boca. Você quer saber se sinto dificuldade para respirar?'),
    ('Quando você dorme, está acordada ou dormindo?', 'Quando durmo, estou dormindo. Quer saber se acordo durante a noite?'),
])
def test_ordinary_knowledge_and_false_alternatives_need_no_invented_patient_detail(seed, service, monkeypatch, question, answer):
    post = Mock(return_value=reply(output(answer)))
    monkeypatch.setattr('medlab.ai.requests.post', post)
    result = service.respond(seed, question, [])
    assert result['answer'] == answer and result['simulation_state']['details'] == []
    assert post.call_count == 1
    prompt = post.call_args.kwargs['json']['messages'][0]['content']
    assert 'conhecimento cotidiano e corporal básico' in prompt
    assert 'não comprova normalidade, ausência de sintomas, quantidade, frequência ou resultado de exame' in prompt
    assert 'não entram em newDetails' in prompt
    assert 'Os fatos documentados prevalecem' in prompt


def test_clear_part_can_be_answered_while_only_unintelligible_part_is_clarified(seed, service, monkeypatch):
    answer = 'O xixi sai líquido. Não entendi a palavra qucira; pode explicar essa parte?'
    post = Mock(return_value=reply(output(answer, ['Hábitos intestinais e urinários'])))
    monkeypatch.setattr('medlab.ai.requests.post', post)
    result = service.respond(seed, 'O xixi é líquido e tem qucira?', [])
    assert result['answer'] == answer and result['simulation_state']['details'] == []
    assert 'Responda primeiro à parte clara' in post.call_args.kwargs['json']['messages'][0]['content']


def test_common_sense_does_not_ban_a_legitimate_clinical_information_limit(seed, service, monkeypatch):
    answer = 'Não recebi essa informação ainda; não consigo dizer qual foi o valor.'
    post = Mock(return_value=reply(output(answer, ['Diagnóstico e tratamento prévios'])))
    monkeypatch.setattr('medlab.ai.requests.post', post)
    result = service.respond(seed, 'Qual foi o valor exato do exame de sangue?', [])
    assert result['answer'] == answer and result['simulation_state']['details'] == []
    assert post.call_count == 1


def test_general_function_is_not_persisted_as_a_normal_exam_or_negative_symptom(seed, service, monkeypatch):
    history = [turn('Seu xixi sai líquido ou seco?', 'Líquido. Quer saber se arde?')]
    state = {'version': 1, 'startedAfterTurn': 0, 'details': []}
    answer = 'Você quer saber se sinto ardência ao urinar?'
    post = Mock(return_value=reply(output(answer, ['Hábitos intestinais e urinários'])))
    monkeypatch.setattr('medlab.ai.requests.post', post)
    result = service.respond(seed, 'Então está tudo normal?', history, state)
    assert result['simulation_state'] == state
    prompt = post.call_args.kwargs['json']['messages'][0]['content']
    assert 'essa pergunta NÃO afirma pouca urina nem ardência' in prompt
    assert 'Evite garantias absolutas' in prompt



def test_false_alternative_clarification_does_not_answer_its_own_clinical_hypotheses(seed, service, monkeypatch):
    answer = 'Pelo nariz e pela boca. Você quer saber se tenho dificuldade para respirar?'
    post = Mock(return_value=reply(output(answer)))
    monkeypatch.setattr('medlab.ai.requests.post', post)
    result = service.respond(seed, 'Você respira pelo nariz ou pelo joelho?', [])
    assert result['answer'] == answer and result['simulation_state']['details'] == []
    prompt = post.call_args.kwargs['json']['messages'][0]['content']
    assert 'encerre no convite para esclarecer: não responda antecipadamente' in prompt
    assert 'não tem falta de ar ou obstrução nasal' in prompt
    assert 'Uma pergunta clínica aberta legítima continua permitindo' in prompt
    assert post.call_count == 1


def test_mixed_general_fact_and_explicit_symptom_question_records_only_individual_detail(seed, service, monkeypatch):
    answer = 'Sai líquido. E não sinto ardência quando faço xixi.'
    detail = {'topic': 'Hábitos intestinais e urinários', 'detail': 'Nega ardência ao urinar.'}
    post = Mock(return_value=reply(output(answer, ['Hábitos intestinais e urinários'], [detail])))
    monkeypatch.setattr('medlab.ai.requests.post', post)
    result = service.respond(seed, 'O xixi sai líquido ou seco? E arde?', [])
    assert result['simulation_state']['details'] == [{**detail, 'turn': 1}]
    prompt = post.call_args.kwargs['json']['messages'][0]['content']
    assert 'registre em newDetails SOMENTE o detalhe clínico individual criado' in prompt
    assert 'sem juntar a generalidade' in prompt
    assert post.call_count == 1
