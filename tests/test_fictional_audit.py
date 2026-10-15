"""Fictional audits cover each differential and require literal clinical context."""
from copy import deepcopy
from unittest.mock import Mock
import pytest
from medlab.ai import AIError, AIService, _patient_knowledge_paths
from test_ai import seed


@pytest.fixture
def service():
    return AIService({'DEEPSEEK_API_KEY': 'test-key'})


def response(case):
    return {
        'checks': [{'key': key, 'compatible': True, 'reason': 'Coerência conferida no caso.'}
                   for key in ('history', 'chronology', 'exams', 'mainDiagnosis', 'treatment')],
        'differentials': [{'index': i, 'disease': item['disease'], 'compatible': True,
                          'requiredContext': [], 'contextEvidence': [], 'reason': 'Diferencial coerente com a história.'}
                         for i, item in enumerate(case['diagnoses']['differentialDiagnoses'])],
        'patientKnowledge': [{'path': path, 'knowledge': 'reported', 'reason': 'Dado percebido ou relatado no momento inicial.'}
                             for path in sorted(_patient_knowledge_paths(case))],
    }


def test_all_dimensions_and_differentials_must_be_covered(service, seed):
    service._request = Mock(return_value=response(seed))
    assert service._audit_fictional_case(seed) == []
    system, data, budget = service._request.call_args.args
    assert 'NUNCA deduza presença' in system and budget > 4000
    assert data['case']['diagnoses'] == seed['diagnoses']
    assert 'provenance' not in data['case']


@pytest.mark.parametrize('gender', ['Masculino', 'Feminino', 'Não informado na fonte.'])
def test_required_anatomy_cannot_be_inferred_from_gender(service, seed, gender):
    seed['patientProfile']['gender'] = gender
    seed['diagnoses']['differentialDiagnoses'][0]['disease'] = 'Doença inflamatória pélvica'
    result = response(seed)
    result['differentials'][0]['requiredContext'] = ['Contexto anatômico compatível, com útero e tubas uterinas.']
    service._request = Mock(return_value=result)
    issues = service._audit_fictional_case(seed)
    assert len(issues) == 1
    assert issues[0]['path'] == '/diagnoses/differentialDiagnoses/0/disease'
    assert 'contexto explícito ausente' in issues[0]['correction']


def test_explicit_context_is_verified_against_literal_case_field(service, seed):
    text = 'História documentada de útero e tubas uterinas preservados.'
    seed['patientProfile']['reproductive_history'] = text
    result = response(seed)
    result['differentials'][0]['requiredContext'] = ['Útero e tubas uterinas.']
    result['differentials'][0]['contextEvidence'] = [{'path': '/patientProfile/reproductive_history', 'quote': text}]
    service._request = Mock(return_value=result)
    assert service._audit_fictional_case(seed) == []
    result['differentials'][0]['contextEvidence'][0]['quote'] = 'Texto inventado.'
    with pytest.raises(AIError, match='cobertura incompleta'):
        service._audit_fictional_case(seed)


def test_gender_is_not_anatomical_evidence_even_when_quote_is_literal(service, seed):
    result = response(seed)
    result['differentials'][0]['requiredContext'] = ['Órgão específico.']
    result['differentials'][0]['contextEvidence'] = [{'path': '/patientProfile/gender', 'quote': seed['patientProfile']['gender']}]
    service._request = Mock(return_value=result)
    with pytest.raises(AIError):
        service._audit_fictional_case(seed)


@pytest.mark.parametrize('failure', ['boolean_only', 'missing_check', 'duplicate_check', 'missing_differential', 'wrong_disease', 'wrong_index'])
def test_incomplete_or_misaligned_audit_is_rejected(service, seed, failure):
    result = response(seed)
    if failure == 'boolean_only':
        result = {'approved': True}
    elif failure == 'missing_check':
        result['checks'].pop()
    elif failure == 'duplicate_check':
        result['checks'][-1] = deepcopy(result['checks'][0])
    elif failure == 'missing_differential':
        result['differentials'].pop()
    elif failure == 'wrong_disease':
        result['differentials'][0]['disease'] = 'Outra doença'
    else:
        result['differentials'][0]['index'] = True
    service._request = Mock(return_value=result)
    with pytest.raises(AIError, match='cobertura incompleta'):
        service._audit_fictional_case(seed)


def test_specific_clinical_inconsistencies_are_returned_for_limited_repair(service, seed):
    result = response(seed)
    result['checks'][1].update(compatible=False, reason='Alinhar o início dos sintomas nas duas descrições.')
    result['differentials'][0].update(compatible=False, reason='Remover diferencial incompatível com os achados descritos.')
    service._request = Mock(return_value=result)
    issues = service._audit_fictional_case(seed)
    assert len(issues) == 2 and all(set(item) == {'path', 'correction'} for item in issues)


def test_patient_understanding_cannot_hint_at_diagnosis_without_exact_label(service, seed):
    seed['diagnoses']['mainDiagnosis']['disease'] = 'Apendicite aguda'
    seed['patientProfile'].setdefault('symptom_details', {})['Compreensão do paciente'] = 'Entende que pode ser algo no apêndice.'
    result = response(seed)
    for item in result['patientKnowledge']:
        if item['path'].endswith('/Compreensão do paciente'):
            item.update(knowledge='diagnostic_hint', reason='Retirar hipótese localizada no apêndice, que antecipa o raciocínio diagnóstico sem antecedente que a sustente.')
    service._request = Mock(return_value=result)
    issues = service._audit_fictional_case(seed)
    assert len(issues) == 1 and issues[0]['path'].endswith('/Compreensão do paciente')
    assert 'órgão sugestivo' in service._request.call_args.args[1]['patientKnowledgeRules']


def test_fictional_profile_checklist_cannot_omit_a_known_leaf(service, seed):
    result = response(seed)
    result['patientKnowledge'].pop()
    service._request = Mock(return_value=result)
    with pytest.raises(AIError, match='cobertura incompleta'):
        service._audit_fictional_case(seed)


def test_generation_rewrites_failed_candidate_and_reaudits_without_publication(service, seed):
    bad_audit = response(seed)
    bad_audit['checks'][1].update(compatible=False, reason='Alinhar cronologia nos campos redundantes.')
    service._request = Mock(side_effect=[deepcopy(seed), bad_audit, deepcopy(seed), response(seed)])
    result = service.generate_case(seed['specialty'], seed['difficulty'])
    assert result['reviewStatus'] == 'review_required' and result['origin'] == 'ai'
    assert service._request.call_count == 4
    correction = service._request.call_args_list[2].args[1]
    assert correction['validationIssues'][0]['path'] == '/patientProfile/present_illness_history'


def test_generation_stops_after_one_failed_repair(service, seed):
    bad_audit = response(seed)
    bad_audit['checks'][1].update(compatible=False, reason='Cronologia inconsistente.')
    service._request = Mock(side_effect=[deepcopy(seed), deepcopy(bad_audit), deepcopy(seed), deepcopy(bad_audit)])
    with pytest.raises(AIError, match='Nenhum caso foi criado'):
        service.generate_case(seed['specialty'], seed['difficulty'])
    assert service._request.call_count == 4 and service._runtime_deadline is None
