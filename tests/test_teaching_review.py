"""Educational feedback preserves scores and server-owned references."""
from copy import deepcopy
from unittest.mock import Mock

import pytest

from medlab.ai import AIError, AIService, AIValidationError, _session_evidence
from medlab.teaching_review import build_teaching_review, _catalogs
from test_ai import seed, evaluation


@pytest.fixture
def service():
    return AIService({'DEEPSEEK_API_KEY': 'test-only-not-a-real-key', 'AI_TIMEOUT': 12})


@pytest.fixture
def session(seed):
    physical = seed['mustPhysicalExams'][0]
    auxiliary = seed['mustAuxiliaryLabs'][0]
    return {
        'history': [{'question': 'Quando começou?', 'answer': 'Há três dias.',
                     'intentClassification': 'Início dos sintomas'}],
        'performedExams': [
            {'examType': 'physical_exam', 'itemName': physical, 'result': seed['physicalExams'][physical]},
            {'examType': 'auxiliary_exam', 'itemName': auxiliary, 'result': seed['auxiliaryLabs'][auxiliary]},
        ],
        'userSubmissions': [{'data': {'diagnosis': 'Hipótese provisória registrada.',
                                     'reasoning': 'Relaciono a evolução e os achados disponíveis.'}}],
        'userTreatments': [{'data': {'treatmentPlan': 'Investigar e reavaliar conforme os achados.'}}],
    }


def response(data):
    """Build a structurally coherent reply using only IDs supplied by the server."""
    catalog = data['criteriaCatalog']
    actions = data['evidenceCatalog']
    has_question = any(item['id'] == 'Q1' for item in actions)
    discussed_exams = [item for item in data['examCatalog'] if item['requested']]
    if not discussed_exams:
        discussed_exams = data['examCatalog'][:1]
    result = {
        'version': 1,
        'summary': 'Aprofunde a relação entre evolução, hipótese e plano registrado.',
        'interview': [{
            'criterionId': next(item['id'] for item in catalog if item['dimension'] == 'consultation'),
            'status': 'partial' if has_question else 'missed',
            'actionIds': ['Q1'] if has_question else [],
            'why': 'A investigação registrada precisa de maior detalhamento.',
            'nextTime': 'Confirme como a evolução afetou as atividades.',
            'exampleQuestion': 'Como isso mudou suas atividades?',
        }],
        'exams': [{'examId': item['id'], 'judgement': 'uncertain',
                   'why': 'O registro não explicita a justificativa do pedido.',
                   'nextTime': 'Relacione o pedido à hipótese investigada.'}
                  for item in discussed_exams],
    }
    for key, dimension, prefix in [('diagnosis', 'diagnosis', 'D'), ('plan', 'treatment', 'T')]:
        result[key] = {
            'criterionIds': [item['id'] for item in catalog if item['dimension'] == dimension][:1],
            'actionIds': [item['id'] for item in actions if item['id'].startswith(prefix)][:1],
            'why': 'Compare a proposta registrada com os dados disponíveis.',
            'nextTime': 'Explicite a justificativa e as condições para reavaliar.',
        }
    return result


def provider(service, change=None):
    def call(system, data, max_tokens):
        value = response(data)
        if change:
            change(value, data)
        return value
    service._request = Mock(side_effect=call)
    return service._request


def test_normalizes_only_from_catalogs_and_never_changes_score_or_inputs(seed, session, service):
    grade = evaluation(53)
    originals = deepcopy((seed, session, grade))
    request = provider(service)
    result = build_teaching_review(service, seed, session, grade)
    assert (seed, session, grade) == originals
    assert request.call_count == 1
    assert result['status'] == 'educational'
    assert 'score' not in result
    assert result['interview'][0]['evidence'] == [{'id': 'Q1', 'text': 'Quando começou?'}]
    assert result['interview'][0]['criterion'] == seed['rubric']['consultation']['criteria'][0]
    assert result['diagnosis']['criteria'] == seed['rubric']['diagnosis']['criteria'][:1]
    assert result['plan']['criteria'] == seed['rubric']['treatment']['criteria'][:1]
    assert result['plan']['evidence'][0]['text'] == session['userTreatments'][0]['data']['treatmentPlan']
    assert {item['type'] for item in result['exams']} == {'physical_exam', 'auxiliary_exam'}
    for item in result['exams']:
        assert item['requested'] and item['required'] and item['available']
        assert item['evidence'][0]['text'] == 'Solicitou: ' + item['name']
    _, data, _ = request.call_args.args
    assert 'score' not in data['grades'] and data['grades']['scale'] == '0-100'
    assert all('score' not in row for row in data['grades']['sections'])
    assert len(data['examCatalog']) == len(seed['physicalExams']) + len(seed['auxiliaryLabs'])
    assert len(data['case']['rubric']) == 6


def test_catalog_ids_stable_for_map_order_and_homonymous_exams_are_distinct(seed):
    case = deepcopy(seed)
    case['physicalExams'] = {'Mesmo nome': 'Físico', 'A': 'Resultado'}
    case['auxiliaryLabs'] = {'Mesmo nome': 'Laboratorial'}
    case['mustPhysicalExams'] = ['Mesmo nome']
    case['mustAuxiliaryLabs'] = []
    case['catalogType'] = 'published_case'
    session = AIService._session({'performedExams': [
        {'examType': 'physical_exam', 'itemName': 'Mesmo nome', 'result': 'Físico'}]})
    evidence = _session_evidence(session)
    first = _catalogs(case, session, evidence)
    case['physicalExams'] = dict(reversed(list(case['physicalExams'].items())))
    assert _catalogs(case, session, evidence) == first
    criteria, exams = first
    assert next(item for item in exams if item['id'] == 'XP2')['actionIds'] == ['P1']
    auxiliary = next(item for item in exams if item['id'] == 'XL1')
    assert auxiliary['requested'] is False and auxiliary['actionIds'] == []
    assert all(item['historicalLimitation'] for item in criteria if item['dimension'] == 'treatment')


@pytest.mark.parametrize('change', [
    lambda v, d: v['interview'][0].update(actionIds=['A1']),
    lambda v, d: v['interview'][0].update(actionIds=['Q999']),
    lambda v, d: v['interview'][0].update(actionIds=['Q1', 'Q1']),
    lambda v, d: v['interview'][0].update(status='missed'),
    lambda v, d: v['interview'][0].update(status='strength', actionIds=[]),
    lambda v, d: v['interview'][0].update(criterionId=v['diagnosis']['criterionIds'][0]),
    lambda v, d: v['diagnosis'].update(actionIds=['T1']),
    lambda v, d: v['plan'].update(actionIds=['D1']),
    lambda v, d: v['plan'].update(actionIds=[]),
    lambda v, d: v['diagnosis'].update(actionIds=[]),
    lambda v, d: v['exams'].pop(),
    lambda v, d: v['exams'][0].update(examId='XP999'),
    lambda v, d: v['exams'].append(deepcopy(v['exams'][0])),
])
def test_invalid_references_or_authorship_fail_without_retry(seed, session, service, change):
    request = provider(service, change)
    with pytest.raises(AIError, match='referências e autoria'):
        build_teaching_review(service, seed, session, evaluation())
    assert request.call_count == 1


@pytest.mark.parametrize('judgement', ['consider', 'not_needed'])
def test_requested_exam_cannot_be_described_as_unrequested(seed, session, service, judgement):
    request = provider(service, lambda v, d: v['exams'][0].update(judgement=judgement))
    with pytest.raises(AIError, match='referências e autoria'):
        build_teaching_review(service, seed, session, evaluation())
    assert request.call_count == 1


@pytest.mark.parametrize('judgement', ['appropriate', 'unnecessary', 'premature'])
def test_unrequested_exam_cannot_receive_a_requested_judgement(seed, service, judgement):
    def change(value, data):
        value['exams'] = [{'examId': data['examCatalog'][0]['id'], 'judgement': judgement,
                          'why': 'Motivo.', 'nextTime': 'Abordagem.'}]
    request = provider(service, change)
    with pytest.raises(AIError, match='referências e autoria'):
        build_teaching_review(service, seed, {}, evaluation())
    assert request.call_count == 1


@pytest.mark.parametrize('judgement', ['consider', 'not_needed', 'uncertain'])
def test_optional_exam_discussion_is_allowed_and_request_status_is_server_owned(seed, service, judgement):
    def change(value, data):
        value['exams'] = [{'examId': data['examCatalog'][0]['id'], 'judgement': judgement,
                          'why': 'Considere os dados disponíveis.', 'nextTime': 'Explique o motivo.'}]
    provider(service, change)
    result = build_teaching_review(service, seed, {}, evaluation())
    assert result['exams'][0]['requested'] is False
    assert result['exams'][0]['evidence'] == []


@pytest.mark.parametrize('change', [
    lambda v: v.update(score=100),
    lambda v: v.update(version=True),
    lambda v: v['plan'].update(evidence=[{'id': 'T1', 'text': 'Texto inventado'}]),
    lambda v: v['interview'][0].update(criterion='Texto inventado'),
    lambda v: v.update(summary='x' * 1601),
    lambda v: v['interview'][0].update(why='x' * 1001),
    lambda v: v['interview'][0].update(exampleQuestion=None),
    lambda v: v['interview'][0].update(criterionId=[]),
])
def test_invalid_shape_gets_one_bounded_correction(seed, session, service, change):
    seen = []
    def request(system, data, tokens):
        seen.append(deepcopy(data))
        value = response(data)
        if len(seen) == 1:
            change(value)
        return value
    service._request = Mock(side_effect=request)
    result = build_teaching_review(service, seed, session, evaluation())
    assert result['version'] == 1 and service._request.call_count == 2
    assert 'repair' not in seen[0] and 'reason' in seen[1]['repair']
    assert 'score' not in result


def test_second_invalid_shape_fails_explicitly(seed, session, service):
    service._request = Mock(return_value={'version': 1})
    with pytest.raises(AIError, match='formato incompleto ou inválido'):
        build_teaching_review(service, seed, session, evaluation())
    assert service._request.call_count == 2


@pytest.mark.parametrize('code', ['invalid_json', 'output_truncated'])
def test_provider_format_failure_gets_only_one_retry(seed, session, service, code):
    attempts = []
    def request(system, data, tokens):
        attempts.append(data)
        if len(attempts) == 1:
            raise AIValidationError(code)
        return response(data)
    service._request = Mock(side_effect=request)
    assert build_teaching_review(service, seed, session, evaluation())['status'] == 'educational'
    assert len(attempts) == 2 and attempts[1]['repair']['reason'] == code


def test_provider_error_is_not_retried_or_converted_to_success(seed, session, service):
    service._request = Mock(side_effect=AIError('O serviço está indisponível.'))
    with pytest.raises(AIError, match='indisponível'):
        build_teaching_review(service, seed, session, evaluation())
    assert service._request.call_count == 1
    assert service._runtime_deadline is None


@pytest.mark.parametrize('prior, expected', [(None, 150), (120, 120), (180, 150)])
def test_total_deadline_is_at_most_fifty_seconds_and_existing_deadline_is_preserved(seed, session, service, monkeypatch, prior, expected):
    service._runtime_deadline = prior
    monkeypatch.setattr('medlab.teaching_review.time.monotonic', lambda: 100)
    seen = []
    def request(system, data, tokens):
        seen.append(service._runtime_deadline)
        return response(data)
    service._request = Mock(side_effect=request)
    build_teaching_review(service, seed, session, evaluation())
    assert seen == [expected]
    assert service._runtime_deadline == prior


def test_demo_is_explicit_and_makes_no_request(seed, session):
    service = AIService({'DEMO_MODE': True, 'DEEPSEEK_API_KEY': 'unused'})
    service._request = Mock()
    with pytest.raises(AIError, match='modo demonstrativo'):
        build_teaching_review(service, seed, session, evaluation())
    service._request.assert_not_called()


@pytest.mark.parametrize('field, text', [
    ('plan', 'Não registrou plano.'), ('plan', 'Nenhuma conduta foi registrada.'),
    ('diagnosis', 'Não apresentou hipótese.'), ('diagnosis', 'Diagnóstico ausente.'),
    ('diagnosis', 'Sem hipótese.'), ('plan', 'Sem plano.'),
    ('diagnosis', 'Avance sem transformar a hipótese inicial em certeza; sem hipótese registrada.'),
])
def test_recorded_diagnosis_or_plan_cannot_be_called_wholly_absent(seed, session, service, field, text):
    request = provider(service, lambda v, d: v[field].update(why=text))
    with pytest.raises(AIError, match='referências e autoria'):
        build_teaching_review(service, seed, session, evaluation())
    assert request.call_count == 1


def test_existing_but_insufficient_plan_can_be_explained(seed, session, service):
    provider(service, lambda v, d: v['plan'].update(why='O plano foi registrado sem detalhamento adequado.'))
    result = build_teaching_review(service, seed, session, evaluation())
    assert result['plan']['actionIds'] == ['T1']


def test_caution_against_turning_a_hypothesis_into_certainty_is_not_claimed_absence(seed, session, service):
    text = 'Declare a incerteza, sem transformar a hipótese inicial em certeza.'
    request = provider(service, lambda v, d: v['diagnosis'].update(nextTime=text))
    result = build_teaching_review(service, seed, session, evaluation())
    assert result['diagnosis']['nextTime'] == text
    assert result['diagnosis']['actionIds'] == ['D1']
    assert request.call_count == 1


def test_full_history_and_simulation_remain_context_and_not_student_authorship(seed, session, service):
    session['history'] = [{'question': f'Pergunta {n}?', 'answer': f'Resposta {n}.'} for n in range(1, 41)]
    session['simulationContext'] = {'version': 1, 'startedAfterTurn': 39, 'details': [
        {'topic': 'Início dos sintomas', 'detail': 'Resposta 40.', 'turn': 40}]}
    request = provider(service)
    build_teaching_review(service, seed, session, evaluation())
    prompt, data, _ = request.call_args.args
    assert len(data['session']['history']) == 40
    assert data['session']['simulationContext'] == session['simulationContext']
    last_answer = next(item for item in data['evidenceCatalog'] if item['id'] == 'A40')
    assert last_answer['origin'] == 'simulated_dialogue'
    assert 'nem exija repetir mecanicamente' in prompt
    assert 'retrospectivamente porque seu resultado foi normal' in prompt
    assert 'DISPONÍVEL NÃO SIGNIFICA INDICADO' in prompt
    assert 'não criam critérios obrigatórios' in prompt
    assert 'sem dosagens ou prescrição' in prompt
    assert data['case']['gradingPolicy']['historicalTreatmentReference'] is False


def test_unknown_exam_and_missing_case_fail_before_request(seed, session, service):
    service._request = Mock()
    invalid = deepcopy(session)
    invalid['performedExams'][0]['itemName'] = 'Exame não presente na ficha'
    with pytest.raises(AIError, match='não pertence'):
        build_teaching_review(service, seed, invalid, evaluation())
    with pytest.raises(AIError):
        build_teaching_review(service, {}, session, evaluation())
    service._request.assert_not_called()


def test_case_result_is_authoritative_without_mutating_session(seed, session, service):
    session['performedExams'][0]['result'] = 'RESULTADO_ALTERADO'
    request = provider(service)
    build_teaching_review(service, seed, session, evaluation())
    data = request.call_args.args[1]
    assert data['session']['performedExams'][0]['result'] != 'RESULTADO_ALTERADO'
    assert session['performedExams'][0]['result'] == 'RESULTADO_ALTERADO'


def test_no_exams_or_assessable_exam_dimensions_do_not_create_catalog_items(seed, service):
    seed['physicalExams'] = {}
    seed['auxiliaryLabs'] = {}
    for field in ('mustPhysicalExams', 'optionalPhysicalExams', 'mustAuxiliaryLabs', 'optionalAuxiliaryLabs'):
        seed[field] = []
    for dimension in ('physical', 'auxiliary'):
        seed['rubric'][dimension] = {'criteria': [], 'criticalActions': [], 'assessable': False,
                                    'reason': 'A ficha não contém resultados desta dimensão.'}
    grade = evaluation()
    for row in grade['sections']:
        if row['key'] in ('physical', 'auxiliary'):
            row['score'] = None
    request = provider(service)
    result = build_teaching_review(service, seed, {}, grade)
    assert result['exams'] == []
    data = request.call_args.args[1]
    assert data['examCatalog'] == []
    assert all(not row['assessable'] and 'score' not in row for row in data['grades']['sections']
               if row['key'] in ('physical', 'auxiliary'))


def test_expired_deadline_does_not_allow_network_call(seed, session, service, monkeypatch):
    service._runtime_deadline = 99
    monkeypatch.setattr('medlab.teaching_review.time.monotonic', lambda: 100)
    network = Mock()
    monkeypatch.setattr('medlab.ai.requests.post', network)
    with pytest.raises(AIError):
        build_teaching_review(service, seed, session, evaluation())
    network.assert_not_called()
    assert service._runtime_deadline == 99


@pytest.mark.parametrize('change', [lambda e: e.update(score=True), lambda e: e.update(score=101),
    lambda e: e.update(sections=[]), lambda e: e['sections'][0].update(score=float('nan')),
    lambda e: e['sections'][1].update(key='consultation')])
def test_invalid_existing_grades_cannot_be_reinterpreted(seed, session, service, change):
    grade = evaluation()
    change(grade)
    service._request = Mock()
    with pytest.raises(AIError, match='avaliação existente está incompleta'):
        build_teaching_review(service, seed, session, grade)
    service._request.assert_not_called()


def test_format_examples_are_valid_and_demonstrate_each_nested_shape(seed, session, service):
    provider(service)
    build_teaching_review(service, seed, session, evaluation())
    prompt, data, _ = service._request.call_args.args
    example = data['format']
    assert isinstance(example['diagnosis'], dict) and isinstance(example['plan'], dict)
    assert set(example['diagnosis']) == {'criterionIds', 'actionIds', 'why', 'nextTime'}
    assert set(example['plan']) == {'criterionIds', 'actionIds', 'why', 'nextTime'}
    assert set(example['interview'][0]) == {'criterionId', 'status', 'actionIds', 'why', 'nextTime', 'exampleQuestion'}
    assert len(example['exams']) == len(session['performedExams'])
    assert all(set(item) == {'examId', 'judgement', 'why', 'nextTime'} for item in example['exams'])
    assert 'interview.status é OBRIGATÓRIO' in prompt
    assert 'nem status na RAIZ' in prompt
    from medlab.teaching_review import _normalize
    assert _normalize(example, data['criteriaCatalog'], data['examCatalog'], data['evidenceCatalog'])['status'] == 'educational'


@pytest.mark.parametrize('field', ['diagnosis', 'plan'])
def test_live_regression_lists_instead_of_single_diagnosis_or_plan_get_format_repair(seed, session, service, field):
    attempts = []
    def request(prompt, data, tokens):
        attempts.append(data)
        result = response(data)
        if len(attempts) == 1:
            result[field] = [{**result[field], 'exampleQuestion': 'Campo incompatível.'}]
        return result
    service._request = Mock(side_effect=request)
    result = build_teaching_review(service, seed, session, evaluation())
    assert isinstance(result[field], dict)
    assert attempts[1]['repair']['reason'] == 'invalid_' + field + '_shape'
    assert 'nunca listas; exampleQuestion é opcional' in attempts[1]['repair']['instruction']
    assert attempts[1]['repair']['diagnostic']['path'] == '/' + field
    assert isinstance(attempts[1]['repair']['previousResponse'][field], list)


def test_live_regression_exam_criterion_ids_are_not_silently_reinterpreted(seed, session, service):
    attempts = []
    def request(prompt, data, tokens):
        attempts.append(data)
        result = response(data)
        if len(attempts) == 1:
            result['exams'][0] = {'criterionId': data['criteriaCatalog'][0]['id'], 'actionIds': ['L1'],
                                 'why': 'Motivo.', 'nextTime': 'Próxima abordagem.', 'status': 'appropriate'}
        return result
    service._request = Mock(side_effect=request)
    result = build_teaching_review(service, seed, session, evaluation())
    assert attempts[1]['repair']['reason'] == 'invalid_exam_item'
    assert len(result['exams']) == len(session['performedExams'])


def test_zero_requested_exams_have_explicit_examples_and_cannot_silently_skip_discussion(seed, service):
    seen = []
    def request(prompt, data, tokens):
        seen.append(data)
        result = response(data)
        if len(seen) == 1:
            result['exams'] = []
        return result
    service._request = Mock(side_effect=request)
    result = build_teaching_review(service, seed, {}, evaluation())
    assert seen[1]['repair']['reason'] == 'empty_available_exams_discussion'
    assert all(item['judgement'] == 'uncertain' for item in seen[0]['format']['exams'])
    assert len(seen[0]['format']['exams']) == 2
    assert len(result['exams']) == 1  # Discussion does not require every available examination.
    assert result['exams'][0]['requested'] is False


def test_only_requested_results_are_known_and_unrequested_results_remain_reference_only(seed, session, service):
    request = provider(service)
    originals = deepcopy((seed, session))
    build_teaching_review(service, seed, session, evaluation())
    prompt, data, _ = request.call_args.args
    assert 'physicalExams' not in data['case'] and 'auxiliaryLabs' not in data['case']
    assert data['case']['contextRole'] == 'reference_case_not_proof_of_student_knowledge'
    known = {(item['type'], item['name']): item for item in data['knownExams']}
    unseen = {(item['type'], item['name']): item for item in data['referenceOnlyExamResults']}
    assert not (known.keys() & unseen.keys())
    assert set(known) == {(item['examType'], item['itemName']) for item in session['performedExams']}
    for kind, field in (('physical_exam', 'physicalExams'), ('auxiliary_exam', 'auxiliaryLabs')):
        for name, value in seed[field].items():
            row = (known if (kind, name) in known else unseen)[(kind, name)]
            assert row['result'] == value
            assert row['origin'] == ('requested_case_result' if (kind, name) in known else 'unrequested_reference_only')
    assert data['knownPatientContext']['patientStatements'][0] == {'id': 'A1', 'text': 'Há três dias.'}
    assert data['knownPatientContext']['studentActionCredit'] is False
    assert 'se tivesse solicitado o exame' in prompt
    assert (seed, session) == originals


def test_spontaneous_information_historical_treatment_and_asr_have_explicit_boundaries(seed, service):
    request = provider(service)
    session = {'history': [{'question': 'Quantas vezes vomitou?', 'answer': 'Algumas vezes nesses três dias.'}]}
    build_teaching_review(service, seed, session, evaluation())
    prompt, data, _ = request.call_args.args
    assert 'não marque partial/missed por faltar uma pergunta direta sobre os mesmos três dias' in prompt
    assert 'não marque missed por não obter esse dado' in prompt
    assert 'nem mesmo uma rubrica que diga realizar hemicolectomia' in prompt
    assert 'use uncertain e explicite possível falha de transcrição' in prompt
    assert data['knownPatientContext']['patientStatements'][0]['text'] == session['history'][0]['answer']
    assert next(item for item in data['evidenceCatalog'] if item['id'] == 'Q1')['text'] == 'Quantas vezes vomitou?'


def test_qualitative_existing_result_is_preserved_without_inventing_a_score(seed, service):
    grade = evaluation()
    grade['score'] = None
    for row in grade['sections']:
        row.update(score=None, assessable=False)
    original = deepcopy(grade)
    request = provider(service)
    result = build_teaching_review(service, seed, {}, grade)
    assert grade == original and 'score' not in result
    grades = request.call_args.args[1]['grades']
    assert 'score' not in grades
    assert all('score' not in row and row['assessable'] is False for row in grades['sections'])


def test_null_total_does_not_discard_existing_numeric_dimension(seed, service):
    grade = evaluation()
    grade['score'] = None
    service._request = Mock()
    with pytest.raises(AIError, match='avaliação existente está incompleta'):
        build_teaching_review(service, seed, {}, grade)
    service._request.assert_not_called()


@pytest.mark.parametrize('length', [827, 937, 1600])
def test_useful_summary_up_to_sixteen_hundred_characters_is_not_regenerated(seed, session, service, length):
    request = provider(service, lambda value, data: value.update(summary='x' * length))
    result = build_teaching_review(service, seed, session, evaluation())
    assert result['summary'] == 'x' * length and request.call_count == 1


@pytest.mark.parametrize('field', ['diagnosis', 'plan'])
def test_optional_example_question_is_preserved_and_bounded(seed, session, service, field):
    request = provider(service, lambda v, d: v[field].update(exampleQuestion='Como explicaria a justificativa?'))
    assert build_teaching_review(service, seed, session, evaluation())[field]['exampleQuestion'] == 'Como explicaria a justificativa?'
    assert request.call_count == 1
    request = provider(service, lambda v, d: v[field].update(exampleQuestion='x' * 501))
    with pytest.raises(AIError, match='formato incompleto'):
        build_teaching_review(service, seed, session, evaluation())
    assert request.call_count == 2
    assert request.call_args.args[1]['repair']['diagnostic']['path'] == '/' + field + '/exampleQuestion'


def test_duplicate_valid_criterion_is_repaired_from_previous_output_not_silently_merged(seed, session, service):
    seen = []
    def request(prompt, data, tokens):
        seen.append(deepcopy(data))
        if len(seen) == 1:
            value = response(data)
            value['interview'].append({**value['interview'][0], 'status': 'uncertain', 'actionIds': []})
            return value
        assert data['repair']['diagnostic']['code'] == 'duplicate_criterion'
        assert data['repair']['diagnostic']['duplicates'][0]['path'] == '/interview/1/criterionId'
        value = deepcopy(data['repair']['previousResponse'])
        value['interview'] = value['interview'][:1]
        return value
    service._request = Mock(side_effect=request)
    result = build_teaching_review(service, seed, session, evaluation())
    assert len(seen) == 2 and len(result['interview']) == 1
    assert result['diagnosis']['why'] == seen[1]['repair']['previousResponse']['diagnosis']['why']


@pytest.mark.parametrize('defect', ['unknown_criterion', 'patient_credit'])
def test_duplicate_criterion_does_not_mask_invalid_reference_or_authorship(seed, session, service, defect):
    def change(value, data):
        value['interview'].append(deepcopy(value['interview'][0]))
        if defect == 'unknown_criterion':
            value['interview'].append({**value['interview'][0], 'criterionId': 'C999'})
        else:
            value['interview'].append({**value['interview'][0], 'actionIds': ['A1']})
    request = provider(service, change)
    with pytest.raises(AIError, match='referências e autoria'):
        build_teaching_review(service, seed, session, evaluation())
    assert request.call_count == 1


def test_field_diagnostic_preserves_previous_response_for_a_local_text_repair(seed, session, service):
    seen = []
    def request(prompt, data, tokens):
        seen.append(deepcopy(data))
        value = response(data)
        if len(seen) == 1:
            value['interview'][0]['nextTime'] = 'x' * 1001
        return value
    service._request = Mock(side_effect=request)
    build_teaching_review(service, seed, session, evaluation())
    repair = seen[1]['repair']
    assert repair['diagnostic'] == {'code': 'invalid_text', 'path': '/interview/0/nextTime',
                                    'expectedType': 'string', 'maximumCharacters': 1000, 'actualCharacters': 1001}
    assert len(repair['previousResponse']['interview'][0]['nextTime']) == 1001
    assert 'preserve os trechos válidos' in repair['instruction']


def test_interview_limit_is_eight_unique_criteria_and_all_ids_are_explicit(seed, session, service):
    seed['rubric']['consultation']['criteria'] = [f'Critério de comunicação {n}.' for n in range(9)]
    seed['rubric']['consultation']['criticalActions'] = []
    attempts = []
    def request(prompt, data, tokens):
        attempts.append(data)
        value = response(data)
        count = 9 if len(attempts) == 1 else 8
        value['interview'] = [{**value['interview'][0], 'criterionId': identifier}
                              for identifier in data['allowedInterviewCriterionIds'][:count]]
        return value
    service._request = Mock(side_effect=request)
    result = build_teaching_review(service, seed, session, evaluation())
    assert attempts[0]['interviewLimit'] == 8
    assert len(attempts[0]['allowedInterviewCriterionIds']) == 9
    assert attempts[1]['repair']['diagnostic']['maximumItems'] == 8
    assert len(result['interview']) == 8


def test_reference_profile_values_not_heard_are_not_sent_as_known_context(seed, session, service):
    seed['patientProfile']['family_history'] = 'REFERENCE_ONLY_FAMILY_VALUE'
    seed['patientProfile']['past_medical_history'] = 'REFERENCE_ONLY_PRIOR_VALUE'
    request = provider(service)
    build_teaching_review(service, seed, session, evaluation(5))
    data = request.call_args.args[1]
    assert 'patientProfile' not in data['case']
    import json
    sent = json.dumps(data, ensure_ascii=False)
    assert 'REFERENCE_ONLY_FAMILY_VALUE' not in sent and 'REFERENCE_ONLY_PRIOR_VALUE' not in sent
    assert data['knownPatientContext']['patientStatements'][0]['text'] == 'Há três dias.'
    assert 'score' not in data['grades'] and all('score' not in row for row in data['grades']['sections'])


@pytest.mark.parametrize('criterion, topic', [
    ('Questiona sobre alergias.', 'Alergias'),
    ('Investiga história familiar.', 'História familiar'),
    ('Avalia doenças crônicas ou condições de imunossupressão.', 'Doenças crônicas'),
])
def test_unavailable_criterion_requires_uncertain_instead_of_an_invented_omission(seed, session, service, criterion, topic):
    from medlab.case_schema import UNKNOWN_FACT
    seed['rubric']['consultation']['criteria'] = [criterion]
    seed['rubric']['consultation']['criticalActions'] = []
    for field in ('allergy_history', 'family_history', 'past_medical_history', 'other_medical_history'):
        seed['patientProfile'][field] = UNKNOWN_FACT
    seed['patientProfile']['symptom_details'] = {}
    request = provider(service, lambda v, d: v['interview'][0].update(status='missed', actionIds=[]))
    with pytest.raises(AIError, match='referências e autoria'):
        build_teaching_review(service, seed, session, evaluation())
    assert request.call_count == 1
    criterion_data = request.call_args.args[1]['criteriaCatalog'][0]
    assert criterion_data['sourceUnavailable'] and topic in criterion_data['unavailableTopics']
    provider(service, lambda v, d: v['interview'][0].update(status='uncertain', actionIds=[]))
    assert build_teaching_review(service, seed, session, evaluation())['interview'][0]['status'] == 'uncertain'


def test_token_budget_accounts_for_unrequested_discussible_exams_and_keeps_twelve_thousand_cap(seed, service):
    request = provider(service)
    build_teaching_review(service, seed, {}, evaluation())
    _, data, tokens = request.call_args.args
    assert tokens == min(12000, 3200 + 350 * min(8, len(data['examCatalog'])) + 300 * data['interviewLimit'])
    assert 3200 < tokens <= 12000
