"""The authored curriculum uses the same private consultation and voice flow."""
import json
from pathlib import Path

from medlab.catalog import BODY_SYSTEMS, SPECIALTIES
from medlab.case_schema import normalized, validate_case
from medlab.cli import import_basic_cases
from medlab.extensions import db
from medlab.models import ClinicalCase, Consultation
from medlab.web import public_case

DATA = Path(__file__).resolve().parents[1] / 'medlab/data/basic_cases'


def payloads():
    return [case for system in BODY_SYSTEMS for case in json.loads((DATA / f'{system}.json').read_text())]


def test_authored_bundles_are_complete_unique_and_publicly_safe():
    cases = payloads()
    assert len(cases) >= 60
    assert len({case['caseId'] for case in cases}) == len(cases)
    assert {case['bodySystem'] for case in cases} == set(BODY_SYSTEMS)
    for raw in cases:
        case = validate_case(raw)
        assert case['learningTrack'] == 'basic' and case['catalogType'] == 'fictional'
        assert case['specialty'] in SPECIALTIES
        assert case['simulationOnly'] is True
        assert case['physicalExams'] and case['referenceReasoning']
        assert all(case['rubric'][key]['criteria'] for key in ('consultation','diagnosis','auxiliary','treatment'))
        visible = normalized(case['caseTitle'] + case['summary'] + case.get('publicPresentation',''))
        answer = normalized(case['diagnoses']['mainDiagnosis']['disease'])
        assert answer not in visible


def test_all_seven_systems_start_the_existing_text_voice_and_exam_screen(app, login):
    client = login()
    with app.app_context():
        assert import_basic_cases()['new'] == len(payloads())
        assert import_basic_cases() == {'new':0,'unchanged':len(payloads())}
    for system in BODY_SYSTEMS:
        with app.app_context():
            cases = db.session.scalars(db.select(ClinicalCase)).all()
            selected = next(case for case in cases if case.data.get('bodySystem') == system)
            card = public_case(selected)
            assert card['body_system_label'] == BODY_SYSTEMS[system]
            assert card['summary'] == selected.data['publicPresentation']
            assert 'diagnoses' not in card and 'learningObjectives' not in card
            identifier, answer = selected.id, selected.data['diagnoses']['mainDiagnosis']['disease']
        response = client.post('/casos/iniciar',data={'case_id':identifier,'track':'basic','system':system})
        assert response.status_code == 302 and '/sessao/' in response.location
        page = client.get(response.location).get_data(as_text=True)
        for marker in ('id="chat-form"','id="voice-record-button"','id="tts-model-activate"',
                       'id="exams-panel"','Concluir consulta e receber avaliação'):
            assert marker in page
        assert answer not in page  # Answer key and results stay hidden until requested/completed.
        with app.app_context():
            training = db.session.get(Consultation,response.location.rsplit('/',1)[-1])
            assert training.case_snapshot['learningTrack']=='basic'
            assert training.case_snapshot['bodySystem']==system


def test_root_manifest_covers_the_three_supplied_slides():
    coverage=json.loads((DATA/'coverage-root.json').read_text())['coverage']
    expected={
        'respiratory': 'Epiglote|Sinusite|Rinite|Otite|Amigdalite|Parotidite|Adenite|Hemoptise|Hemoptóicos|Vômica|Pneumotórax|Pneumomediastino|Broncogênico|Sopro tubário|Intersticial|Cianose|Paquipleuris|Expectorar|Frêmito tóraco-vocal|Broncoespasmo|Micetoma|Bronquite|Bronquiectasia|Tráqueobrônquica|Hipóxia; hipoxemia|Dispnéia|Apnéia|Platipnéia|Ortopnéia|Estetoscópio',
        'cardiovascular': 'Taquicardia|Taquisfigmia|Bradicardia|Cardiomegalia|Cardite|Endocardite|Pericardite|Cardiopatia|Hipertensão arterial|Miocardiopatias|Valvulopatia|Hipertrofia|Isquemia|Aterosclerose|Eletrocardiografia|Precordialgia|Levocardia|Hipervolemia',
        'head': 'Alopécia|Tricotomia|Acrocefalia|Anencefalia|Microcefalia|Macrocefalia|Hidrocefalia|Craniosinostose|Hirsutismo|Amaurose|Exoftalmia|Rinoscopia|Oftalmoplegia|Hipertelorismo|Xeroftalmia',
    }
    cases={case['caseId']:case for case in payloads()}
    for system, terms in expected.items():
        assert set(terms.split('|')) <= set(coverage[system])
        for term, identifiers in coverage[system].items():
            assert identifiers and all(identifier in cases for identifier in identifiers)
            for identifier in identifiers:
                case=cases[identifier]
                assert normalized(term) in normalized(' '.join(case['learningObjectives']))


def test_infant_curriculum_identification_has_consistent_age_and_residence():
    cases={case['caseId']:case for case in payloads()}
    for identifier, months in [('basic-head-03',6),('basic-head-05',4),('basic-head-06',6)]:
        profile=cases[identifier]['patientProfile']
        assert profile['age_value']==months and profile['age_unit']=='meses'
        assert 'Mãe' in profile['basic_info'] and 'mãe' in profile['respondent']
        assert 'seis anos' not in profile['address']
        assert 'leite materno' in profile['personal_history']
