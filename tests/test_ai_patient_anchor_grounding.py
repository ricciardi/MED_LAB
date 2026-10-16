"""Concrete failures from the ten-case live runtime sample; no provider calls."""
from copy import deepcopy
import json
from unittest.mock import Mock
import pytest
from medlab.ai import AIError, AIService, _spoken_fact
from medlab.case_schema import UNKNOWN_FACT
from test_ai import seed, service, reply, patient_reply
from medlab.patient_dialogue import patient_context


def test_unassigned_terms_are_not_patient_facts_but_explicit_asymptomatic_status_is_kept(seed):
    p=seed['patientProfile'];p['chief_complaint']='Menções clínicas extraídas; relação com o paciente ainda não revisada: abdômen.'
    p['present_illness_history']=p['chief_complaint']+'\nRelata Assintomática.'
    before=deepcopy(seed)
    assert AIService._facts(seed,['Queixa principal']) == {'Queixa principal':{'documented_symptom_status':'Relata Assintomática.'}}
    assert seed==before


def test_unassigned_mass_and_duration_are_not_canonical_patient_facts(seed):
    seed['patientProfile']['chief_complaint']='Menções clínicas extraídas; relação com o paciente ainda não revisada: inchaço; Uma semana.'
    seed['patientProfile']['present_illness_history']=seed['patientProfile']['chief_complaint']
    profile = patient_context(seed)['patientProfile']
    assert 'chief_complaint' not in profile and 'present_illness_history' not in profile


@pytest.mark.parametrize('answer', ['Tenho 92 anos.', 'Tenho 92 anos e estou preocupada.'])
def test_documented_age_cannot_be_changed_by_elaboration(seed, service, monkeypatch, answer):
    post = Mock(return_value=reply(patient_reply(answer, ['Informações pessoais'])))
    monkeypatch.setattr('medlab.ai.requests.post', post)
    with pytest.raises(AIError):
        service.respond(seed, 'Qual sua idade?', [])
    assert post.call_count == 2


def test_documented_calendar_anchor_and_routine_are_preserved(seed,service,monkeypatch):
    seed['patientProfile']['chief_complaint']='Consulta de rotina hoje, sem sintomas.'
    answer='Vim para uma consulta de rotina hoje.'
    monkeypatch.setattr('medlab.ai.requests.post',Mock(return_value=reply(patient_reply(answer, ['Queixa principal']))))
    assert service.respond(seed,'O que trouxe você?',[])['answer']==answer


def test_literal_hpi_prior_condition_is_recovered_without_current_findings(seed):
    p=seed['patientProfile'];p['past_medical_history']=UNKNOWN_FACT
    p['present_illness_history']='Um homem de 48 anos, portador de hepatite B,\napresentou fraqueza.\nExame neurológico mostrou hiperreflexia.\nUma laparotomia revelou um tumor.'
    facts=AIService._facts(seed,['Doenças crônicas'])
    assert facts=={'Doenças crônicas':{'documented_prior_history':'portador de hepatite B'}}
    symptoms=AIService._facts(seed,['Evolução do quadro'])
    assert 'hiperreflexia' not in json.dumps(symptoms)
    assert 'tumor' not in json.dumps(symptoms)


def test_medication_already_documented_in_prior_history_does_not_become_unknown(seed):
    p=seed['patientProfile'];p['other_medical_history']=UNKNOWN_FACT
    p['past_medical_history']='Sua história médica era notável apenas por gastrite intermitente com antiácidos e antagonistas H2.'
    facts=AIService._facts(seed,['Medicamentos em uso'])
    assert facts['Medicamentos em uso']['documented_prior_history']==p['past_medical_history']


def test_post_discharge_medication_is_not_prior_history(seed):
    p=seed['patientProfile'];p['other_medical_history']=p['past_medical_history']=UNKNOWN_FACT
    p['present_illness_history']='Após a cirurgia, teve alta em uso de um medicamento.'
    assert AIService._facts(seed,['Medicamentos em uso'])=={'Medicamentos em uso':{}}


@pytest.mark.parametrize('text',[
    'Recebeu antiácidos após a internação.',
    'Foi prescrito um medicamento para o estômago.',
    'Está em uso de antiácidos.',
    'A história médica foi registrada e recebeu antiácidos durante a internação.',
])
def test_medication_fallback_needs_explicit_prior_use(seed,text):
    p=seed['patientProfile'];p['other_medical_history']=p['past_medical_history']=UNKNOWN_FACT
    p['present_illness_history']=text
    assert AIService._facts(seed,['Medicamentos em uso'])=={'Medicamentos em uso':{}}


def test_explicit_prior_medication_negation_is_preserved(seed):
    p=seed['patientProfile'];p['other_medical_history']=p['past_medical_history']=UNKNOWN_FACT
    p['present_illness_history']='Não tomava medicamentos habitualmente.'
    assert AIService._facts(seed,['Medicamentos em uso'])['Medicamentos em uso']['documented_prior_history']==p['present_illness_history']


def test_newly_diagnosed_condition_is_not_prior_history(seed):
    p=seed['patientProfile'];p['past_medical_history']=UNKNOWN_FACT
    p['present_illness_history']='Foi diagnosticado como portador de hepatite B após a internação.'
    assert AIService._facts(seed,['Doenças crônicas'])=={'Doenças crônicas':{}}


def test_prior_condition_negation_is_preserved(seed):
    p=seed['patientProfile'];p['past_medical_history']=UNKNOWN_FACT
    p['present_illness_history']='Ele não é portador de hepatite B, apresentou cansaço.'
    assert AIService._facts(seed,['Doenças crônicas'])['Doenças crônicas']['documented_prior_history']=='Ele não é portador de hepatite B'


def test_numeric_fallback_example_agrees_with_unassessable_rubric(seed,service,monkeypatch):
    seed['rubric']['treatment']['assessable']=False
    response={'sections':[{'key':key,'score':None if key=='treatment' else 0,'actionIds':[]} for key in seed['rubric']]}
    post=Mock(return_value=reply(response));monkeypatch.setattr('medlab.ai.requests.post',post)
    result=service._score_only_evaluation(seed,[])
    data=json.loads(post.call_args.kwargs['json']['messages'][1]['content'])
    treatment=next(item for item in data['format']['sections'] if item['key']=='treatment')
    assert treatment=={'key':'treatment','score':None,'actionIds':[]}
    assert data['unassessableSections']==['treatment']
    assert next(item for item in result['sections'] if item['key']=='treatment')['score'] is None


def test_unclear_question_can_request_clarification_using_full_context(seed,service,monkeypatch):
    answer = 'Não entendi o que quis dizer com qucira. Pode repetir essa parte?'
    post=Mock(return_value=reply(patient_reply(answer,['Características dos sintomas'])))
    monkeypatch.setattr('medlab.ai.requests.post',post)
    assert service.respond(seed,'O incômodo na qucira aparece sempre?',[])['answer'] == answer
    assert post.call_count==1
    sent=json.loads(post.call_args.kwargs['json']['messages'][1]['content'])
    assert 'patientProfile' in sent and 'diagnoses' not in sent


@pytest.mark.parametrize('invalid',[None,'false',0,[],{}])
def test_old_classifier_flags_are_not_accepted_as_new_patient_output(seed,service,monkeypatch,invalid):
    post=Mock(return_value=reply({**patient_reply('Olá', ['Queixa principal']), 'needsClarification':invalid}))
    monkeypatch.setattr('medlab.ai.requests.post',post)
    with pytest.raises(AIError): service.respond(seed,'O que sente?',[])
    assert post.call_count==2


def test_mixed_trigger_keeps_original_question_and_known_facts(seed,service,monkeypatch):
    p=seed['patientProfile'];p.setdefault('symptom_details',{}).update({'Fatores desencadeantes':'Palpitações aos esforços.','Sintomas associados':UNKNOWN_FACT})
    post=Mock(return_value=reply(patient_reply('As palpitações aparecem quando faço esforço.', ['Fatores desencadeantes','Sintomas associados'])))
    monkeypatch.setattr('medlab.ai.requests.post',post)
    question = 'Sente mais alguma coisa? Ocorre no esforço?'
    response=service.respond(seed,question,[])
    assert 'quando faço esforço' in response['answer']
    sent=json.loads(post.call_args.kwargs['json']['messages'][1]['content'])
    assert sent['question'] == question
    assert sent['patientProfile']['symptom_details']['Fatores desencadeantes'] == 'Palpitações aos esforços.'


def test_qualified_history_remains_canonical_and_negative_elaboration_is_separate(seed,service,monkeypatch):
    seed['patientProfile']['past_medical_history']='Sem história pregressa significativa.'
    details = [{'topic':'Doenças crônicas', 'detail':'Não tem diabetes.'}]
    post=Mock(return_value=reply(patient_reply('Não tenho diabetes.', ['Doenças crônicas'], details)))
    monkeypatch.setattr('medlab.ai.requests.post',post)
    result=service.respond(seed,'Tem diabetes?',[])
    assert result['simulation_state']['details'] == [{**details[0], 'turn':1}]
    assert seed['patientProfile']['past_medical_history']=='Sem história pregressa significativa.'
    sent=json.loads(post.call_args.kwargs['json']['messages'][1]['content'])
    assert sent['patientProfile']['past_medical_history']=='Sem história pregressa significativa.'


@pytest.mark.parametrize('answer',['Não tenho histórico de problemas de saúde importantes.','Não tenho antecedentes relevantes.'])
def test_qualified_history_preserves_documented_qualifier(seed,service,monkeypatch,answer):
    seed['patientProfile']['past_medical_history']='Sem história pregressa significativa.'
    monkeypatch.setattr('medlab.ai.requests.post',Mock(return_value=reply(patient_reply(answer,['Doenças crônicas']))))
    assert service.respond(seed,'Antes disso tinha alguma doença?',[])['answer']==answer


def test_explicit_individual_negative_is_preserved(seed,service,monkeypatch):
    seed['patientProfile']['past_medical_history']='Nega diabetes.'
    monkeypatch.setattr('medlab.ai.requests.post',Mock(return_value=reply(patient_reply('Não tenho diabetes.',['Doenças crônicas']))))
    assert service.respond(seed,'Tem diabetes?',[])['answer']=='Não tenho diabetes.'
