"""Concrete failures from the ten-case live runtime sample; no provider calls."""
from copy import deepcopy
import json
from unittest.mock import Mock
import pytest
from medlab.ai import AIError, AIService, _spoken_fact
from medlab.case_schema import UNKNOWN_FACT
from test_ai import seed, service, reply


def test_unassigned_terms_are_not_patient_facts_but_explicit_asymptomatic_status_is_kept(seed):
    p=seed['patientProfile'];p['chief_complaint']='Menções clínicas extraídas; relação com o paciente ainda não revisada: abdômen.'
    p['present_illness_history']=p['chief_complaint']+'\nRelata Assintomática.'
    before=deepcopy(seed)
    assert AIService._facts(seed,['Queixa principal']) == {'Queixa principal':{'documented_symptom_status':'Relata Assintomática.'}}
    assert seed==before


def test_unassigned_mass_and_duration_never_become_confirmed_symptoms(seed,service,monkeypatch):
    seed['patientProfile']['chief_complaint']='Menções clínicas extraídas; relação com o paciente ainda não revisada: inchaço; Uma semana.'
    seed['patientProfile']['present_illness_history']=seed['patientProfile']['chief_complaint']
    post=Mock(return_value=reply({'intents':['Queixa principal']}));monkeypatch.setattr('medlab.ai.requests.post',post)
    assert service.respond(seed,'O que trouxe vocês?',[])['answer']=='Não sei lhe dizer.'
    assert post.call_count==1


@pytest.mark.parametrize('answer',['Vim só para uma consulta de rotina.','Vim fazer um check-up.','Começou ontem.','Hoje piorou.'])
def test_unsupported_routine_reason_or_calendar_anchor_rejected(seed,service,monkeypatch,answer):
    seed['patientProfile']['chief_complaint']='Sem sintomas.'
    monkeypatch.setattr('medlab.ai.requests.post',Mock(side_effect=[reply({'intents':['Queixa principal']}),reply({'answer':answer})]))
    with pytest.raises(AIError,match='data ou motivo'):service.respond(seed,'O que trouxe você?',[])


def test_documented_calendar_anchor_and_routine_are_preserved(seed,service,monkeypatch):
    seed['patientProfile']['chief_complaint']='Consulta de rotina hoje, sem sintomas.'
    answer='Vim para uma consulta de rotina hoje.'
    monkeypatch.setattr('medlab.ai.requests.post',Mock(side_effect=[reply({'intents':['Queixa principal']}),reply({'answer':answer})]))
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


def test_unclear_question_is_clarified_without_sending_case_facts(seed,service,monkeypatch):
    post=Mock(return_value=reply({'intents':['Características dos sintomas'],'needsClarification':True}))
    monkeypatch.setattr('medlab.ai.requests.post',post)
    result=service.respond(seed,'O incômodo na qucira aparece sempre?',[])
    assert 'Pode repetir' in result['answer']
    assert post.call_count==1
    sent=json.loads(post.call_args.kwargs['json']['messages'][1]['content'])
    assert 'facts' not in sent and 'patientProfile' not in sent


@pytest.mark.parametrize('invalid',[None,'false',0,[],{}])
def test_malformed_clarification_flag_does_not_trigger_patient(seed,service,monkeypatch,invalid):
    post=Mock(return_value=reply({'intents':['Queixa principal'],'needsClarification':invalid}))
    monkeypatch.setattr('medlab.ai.requests.post',post)
    with pytest.raises(AIError,match='classificar'): service.respond(seed,'O que sente?',[])
    assert post.call_count==1


def test_mixed_trigger_question_keeps_known_topic_and_excludes_unknown(seed,service,monkeypatch):
    p=seed['patientProfile'];p.setdefault('symptom_details',{}).update({'Fatores desencadeantes':'Palpitações aos esforços.','Sintomas associados':UNKNOWN_FACT})
    post=Mock(side_effect=[reply({'intents':['Fatores desencadeantes','Sintomas associados'],'needsClarification':False}),reply({'answer':'As palpitações aparecem quando faço esforço.'})])
    monkeypatch.setattr('medlab.ai.requests.post',post)
    response=service.respond(seed,'Sente mais alguma coisa? Ocorre no esforço?',[])
    assert 'quando faço esforço' in response['answer']
    sent=json.loads(post.call_args.kwargs['json']['messages'][1]['content'])
    assert 'O que costuma desencadear' in sent['question']
    assert 'Sintomas associados' not in sent['facts']


@pytest.mark.parametrize('answer',[
    'Não tenho nenhuma doença crônica.',
    'Nunca tive doença.',
    'Não tenho diabetes.',
    'Não tenho hipertensão importante.',
    'Não tenho problemas importantes, nem diabetes.',
    'Não tenho histórico relevante e nunca tive diabetes.',
])
def test_qualified_history_does_not_authorize_general_or_individual_denial(seed,service,monkeypatch,answer):
    seed['patientProfile']['past_medical_history']='Sem história pregressa significativa.'
    post=Mock(side_effect=[reply({'intents':['Doenças crônicas']}),reply({'answer':answer})])
    monkeypatch.setattr('medlab.ai.requests.post',post)
    with pytest.raises(AIError,match='qualificada'):service.respond(seed,'Antes disso tinha alguma doença?',[])


@pytest.mark.parametrize('answer',[
    'Não tenho histórico de problemas de saúde importantes.',
    'Não tenho antecedentes relevantes.',
    'Não sei lhe dizer sobre diabetes.',
])
def test_qualified_history_preserves_qualifier_or_unknown(seed,service,monkeypatch,answer):
    seed['patientProfile']['past_medical_history']='Sem história pregressa significativa.'
    monkeypatch.setattr('medlab.ai.requests.post',Mock(side_effect=[reply({'intents':['Doenças crônicas']}),reply({'answer':answer})]))
    assert service.respond(seed,'Antes disso tinha alguma doença?',[])['answer']==answer


def test_explicit_individual_negative_is_not_mistaken_for_qualified_history(seed,service,monkeypatch):
    seed['patientProfile']['past_medical_history']='Nega diabetes.'
    monkeypatch.setattr('medlab.ai.requests.post',Mock(side_effect=[reply({'intents':['Doenças crônicas']}),reply({'answer':'Não tenho diabetes.'})]))
    assert service.respond(seed,'Tem diabetes?',[])['answer']=='Não tenho diabetes.'
