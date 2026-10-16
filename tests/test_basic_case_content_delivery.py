"""Content contracts for the four authored basic-case bundles; no DB or network."""
import json
from pathlib import Path
from urllib.parse import urlparse

import pytest

from medlab.case_schema import SECTION_TITLES, normalized, validate_case
from medlab.catalog import CASE_TYPES, SPECIALTIES

DATA = Path(__file__).resolve().parents[1] / 'medlab/data/basic_cases'
COUNTS = {'urinary': 8, 'digestive': 8, 'hematopoietic': 5, 'nervous': 7}
CASES = [case for system in COUNTS for case in json.loads((DATA / f'{system}.json').read_text())]
BY_ID = {case['caseId']: case for case in CASES}


@pytest.mark.parametrize('case', CASES, ids=lambda case: case['caseId'])
def test_complete_synthetic_basic_case_with_separate_answer(case):
    checked = validate_case(case)
    assert checked['catalogType'] == 'fictional'
    assert checked['learningTrack'] == 'basic'
    assert checked['bodySystem'] in COUNTS
    assert checked['specialty'] in SPECIALTIES
    assert checked['caseType'] in CASE_TYPES
    assert checked['simulationOnly'] is True
    assert checked['reviewStatus'] == 'educational_seed'
    assert 'provenance' not in checked and not checked.get('examMedia')
    assert checked['patientProfile']['age_value'] > 0
    assert len(checked['patientProfile']['present_illness_history']) >= 180
    assert checked['patientProfile']['social_background']
    assert checked['patientProfile']['concern']
    assert len(checked['physicalExams']) >= 2
    assert len(checked['diagnoses']['differentialDiagnoses']) >= 2
    assert set(checked['rubric']) == set(SECTION_TITLES)
    assert all(section['criteria'] for section in checked['rubric'].values())
    answer = normalized(checked['diagnoses']['mainDiagnosis']['disease'])
    for name in ('caseTitle', 'summary', 'publicPresentation'):
        assert answer not in normalized(checked[name])
    assert answer not in normalized(checked['patientProfile']['chief_complaint'])
    assert 'Justificativa dos exames:' in checked['referenceReasoning']
    assert all(urlparse(ref['url']).hostname in {
        'www.niddk.nih.gov', 'www.nhlbi.nih.gov', 'www.cdc.gov', 'www.nice.org.uk',
        'www.nhs.uk', 'www.nidcr.nih.gov', 'www.nidcd.nih.gov', 'www.issva.org',
        'www.ninds.nih.gov', 'www.nbt.nhs.uk', 'www.radiologyinfo.org',
    } for ref in checked['references'])


def test_all_slide_terms_have_explicit_linked_objectives_and_no_empty_coverage():
    manifest = json.loads((DATA / 'coverage_delivery.json').read_text())
    assert len(BY_ID) == len(CASES) == manifest['caseCount'] == 28
    assert manifest['termCount'] == 75
    expected_terms = {'urinary': 17, 'digestive': 25, 'hematopoietic': 15, 'nervous': 18}
    all_terms = []
    for group in manifest['groups']:
        system = group['bodySystem']
        assert group['caseCount'] == COUNTS[system]
        assert len(group['terms']) == expected_terms[system]
        assert len(group['sourceImageSha256']) == 64
        for entry in group['terms']:
            all_terms.append(entry['termAsProvided'])
            assert entry['cases']
            for coverage in entry['cases']:
                case = BY_ID[coverage['caseId']]
                assert coverage['role'] in {
                    'active_symptom', 'exam_finding', 'diagnosis', 'selective_test',
                    'differential_concept', 'history_concept', 'temporal_descriptor',
                    'anatomy', 'differential', 'terminology', 'physiology', 'exam_parameter',
                }
                path = coverage['evidencePaths'][0]
                index = int(path.removeprefix('learningObjectives[').removesuffix(']'))
                assert case['learningObjectives'][index] == coverage['objective']
    assert len(set(all_terms)) == 75


def test_selective_procedures_are_not_forced_and_pending_tests_do_not_invent_normality():
    cystitis = BY_ID['basic-urinary-ardor-frequencia']
    reflux = BY_ID['basic-digestive-queimacao-refeicoes']
    assert cystitis['mustAuxiliaryLabs'] == reflux['mustAuxiliaryLabs'] == []
    pyelo = BY_ID['basic-urinary-febre-lombar']
    assert 'Cistografia miccional' in pyelo['optionalAuxiliaryLabs']
    assert 'Etapa posterior' in pyelo['auxiliaryLabs']['Cistografia miccional']
    cauda = BY_ID['basic-nervous-pernas-bexiga']
    assert cauda['mustAuxiliaryLabs'] == ['Ressonância lombossacra urgente']
    assert not any('mielografia' in name.lower() for name in cauda['auxiliaryLabs'])
    assert 'não rotina inicial' in cauda['referenceReasoning']
    smell = BY_ID['basic-nervous-perda-olfato']
    assert smell['mustAuxiliaryLabs'] == []
    assert 'Imagem de crânio' not in smell['auxiliaryLabs']


def test_syndromic_and_temporal_distinctions_remain_explicit():
    retention = BY_ID['basic-urinary-sem-conseguir-urinar']
    assert 'não prova de anúria renal' in retention['referenceReasoning']
    pigment = BY_ID['basic-urinary-urina-marrom-cansaco']
    assert 'hemácias 0–1' in pigment['auxiliaryLabs']['Urina tipo I']
    assert 'bilirrubina negativa' in pigment['auxiliaryLabs']['Urina tipo I']
    hepatitis = BY_ID['basic-digestive-olhos-amarelos']
    assert 'Bilirrubina 2+' in hepatitis['auxiliaryLabs']['Urina tipo I']
    oral = BY_ID['basic-digestive-boca-seca-ardor']
    assert 'não ageusia' in oral['referenceReasoning']
    aneurysm = BY_ID['basic-nervous-visao-dupla-palpebra']
    assert 'direita de 6 mm' in aneurysm['physicalExams']['Pupilas e motricidade ocular']
    assert 'Miose associada a ptose seria outro padrão' in aneurysm['referenceReasoning']
    stroke = BY_ID['basic-nervous-fraqueza-subita']
    assert 'direitos' in stroke['physicalExams']['Exame neurológico focal']
    assert 'esquerda' in stroke['auxiliaryLabs']['Angiotomografia de cabeça e pescoço']
    assert 'Esposa' in stroke['patientProfile']['respondent']


def test_clinical_catalogue_metadata_is_independent_of_slide_system():
    diabetes = BY_ID['basic-urinary-sede-muito-xixi']
    assert diabetes['bodySystem'] == 'urinary'
    assert diabetes['specialty'] == 'Endocrinologia e metabologia'
    assert diabetes['caseType'] == 'Sede, fome e alterações de peso'
    nephritis = BY_ID['basic-urinary-urina-escura-edema']
    assert nephritis['specialty'] == 'Nefrologia'
    throat = BY_ID['basic-digestive-garganta-dolorosa']
    assert throat['bodySystem'] == 'digestive'
    assert throat['specialty'] == 'Otorrinolaringologia'
    assert throat['caseType'] == 'Sintomas de ouvido, nariz e garganta'
    assert BY_ID['basic-digestive-dor-alta-abdome']['caseType'] == 'Dor abdominal'
    assert BY_ID['basic-nervous-perda-olfato']['specialty'] == 'Otorrinolaringologia'
    assert BY_ID['basic-nervous-visao-dupla-palpebra']['caseType'] == 'Alterações visuais'
