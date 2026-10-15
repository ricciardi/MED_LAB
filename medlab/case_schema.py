"""Strict, dependency-free contracts for fictional MED LAB educational cases."""

from copy import deepcopy
import re
import unicodedata
from urllib.parse import urlparse


INTENT_CATEGORIES = (
    "Informações pessoais", "Queixa principal", "Início dos sintomas",
    "Fatores desencadeantes", "Localização dos sintomas", "Características dos sintomas",
    "Duração e frequência", "Fatores de melhora ou piora", "Sintomas associados",
    "Evolução do quadro", "Diagnóstico e tratamento prévios", "Estado geral",
    "Hábitos intestinais e urinários", "Mudança de peso", "Doenças crônicas",
    "Doenças infecciosas", "Cirurgias e traumas", "Transfusões", "Alergias",
    "Vacinação", "Medicamentos em uso", "Viagens", "Hábitos de vida",
    "História ocupacional", "História sexual", "História reprodutiva",
    "História familiar", "História menstrual", "Compreensão do paciente",
    "Preocupações do paciente", "Expectativas do paciente", "Conversa geral",
)
SECTION_TITLES = {
    "consultation": "Anamnese", "physical": "Exame físico",
    "auxiliary": "Exames complementares", "diagnosis": "Raciocínio diagnóstico",
    "treatment": "Plano terapêutico", "overall": "Avaliação global",
}
DIFFICULTIES = ("Iniciante", "Intermediário", "Avançado")


class CaseValidationError(ValueError):
    """A case is incomplete or inconsistent; safe to show to an administrator."""


def normalized(value):
    return "".join(c for c in unicodedata.normalize("NFKD", value.casefold()) if not unicodedata.combining(c))


def _fail(path):
    raise CaseValidationError(f"Caso inválido: confira o campo {path}.")


def _text(value, path, maximum=6000):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or "\x00" in value:
        _fail(path)
    return value.strip()


def _strings(value, path, maximum=40, allow_empty=True):
    if not isinstance(value, list) or len(value) > maximum or (not value and not allow_empty):
        _fail(path)
    result = [_text(v, path, 1200) for v in value]
    if len(set(result)) != len(result):
        _fail(path)
    return result


def validate_case(data):
    """Return a normalized independent copy, or reject without partially saving it.

    Schema validation is structural, not certification of medical correctness.
    `rubric` and diagnoses are private and must never appear in a student case view.
    """
    required = {
        "caseId", "caseTitle", "specialty", "difficulty", "summary", "patientProfile",
        "mustConsultionItems", "optionalConsultionItems", "mustPhysicalExams",
        "optionalPhysicalExams", "mustAuxiliaryLabs", "optionalAuxiliaryLabs",
        "physicalExams", "auxiliaryLabs", "diagnoses", "treatments",
        "learningObjectives", "references", "rubric",
    }
    optional = {"simulationOnly", "reviewStatus", "origin", "reviewNote"}
    if not isinstance(data, dict) or not required <= data.keys() or data.keys() - required - optional:
        _fail("estrutura")
    case = deepcopy(data)
    field_limits = {
        "caseId": 80, "caseTitle": 180, "specialty": 80,
        "difficulty": 40, "summary": 800, "treatments": 6000,
    }
    for name, maximum in field_limits.items():
        case[name] = _text(case[name], name, maximum)
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", case["caseId"]) or len(case["caseId"]) > 80:
        _fail("caseId")
    if case["difficulty"] not in DIFFICULTIES:
        _fail("difficulty")
    required_profile = {
        "name", "age_value", "gender", "occupation", "chief_complaint",
        "present_illness_history", "past_medical_history", "personal_history",
        "family_history", "other_medical_history", "allergy_history",
    }
    optional_profile = {
        "age_unit", "marital_status", "address", "social_background", "basic_info",
        "hospital_department_name", "surgery_injury_history", "transfusion_history",
        "infection_history", "system_review", "menstrual_history", "reproductive_history",
        "sexual_history", "vaccination_history", "travel_history", "idea", "concern",
        "expectation", "respondent", "symptom_details",
    }
    profile = case["patientProfile"]
    if not isinstance(profile, dict) or not required_profile <= profile.keys() or profile.keys() - required_profile - optional_profile:
        _fail("patientProfile")
    age = profile["age_value"]
    if isinstance(age, bool) or not isinstance(age, (int, float)) or not 0 <= age <= 120:
        _fail("patientProfile.age_value")
    for name, value in profile.items():
        if name not in {"age_value", "symptom_details"}:
            profile[name] = _text(value, "patientProfile." + name)
    details = profile.get("symptom_details", {})
    if not isinstance(details, dict) or details.keys() - set(INTENT_CATEGORIES):
        _fail("patientProfile.symptom_details")
    profile["symptom_details"] = {k: _text(v, "symptom_details." + k, 1800) for k, v in details.items()}
    for name in ("mustConsultionItems", "optionalConsultionItems", "mustPhysicalExams", "optionalPhysicalExams", "mustAuxiliaryLabs", "optionalAuxiliaryLabs"):
        case[name] = _strings(case[name], name)
    for name in ("mustConsultionItems", "optionalConsultionItems"):
        if set(case[name]) - set(INTENT_CATEGORIES):
            _fail(name)
    for required_name, optional_name in (("mustConsultionItems", "optionalConsultionItems"), ("mustPhysicalExams", "optionalPhysicalExams"), ("mustAuxiliaryLabs", "optionalAuxiliaryLabs")):
        if set(case[required_name]) & set(case[optional_name]):
            _fail(optional_name)
    for map_name, required_name, optional_name in (("physicalExams", "mustPhysicalExams", "optionalPhysicalExams"), ("auxiliaryLabs", "mustAuxiliaryLabs", "optionalAuxiliaryLabs")):
        values = case[map_name]
        if not isinstance(values, dict) or len(values) > 50:
            _fail(map_name)
        case[map_name] = {_text(k, map_name, 160): _text(v, map_name, 3000) for k, v in values.items()}
        if set(case[required_name] + case[optional_name]) != set(values):
            _fail(map_name)
    diagnoses = case["diagnoses"]
    if not isinstance(diagnoses, dict) or set(diagnoses) != {"mainDiagnosis", "differentialDiagnoses"}:
        _fail("diagnoses")
    if not isinstance(diagnoses["differentialDiagnoses"], list) or not 1 <= len(diagnoses["differentialDiagnoses"]) <= 10:
        _fail("diagnoses.differentialDiagnoses")
    for diagnosis in [diagnoses["mainDiagnosis"], *diagnoses["differentialDiagnoses"]]:
        if not isinstance(diagnosis, dict) or set(diagnosis) != {"disease"}:
            _fail("diagnoses")
        diagnosis["disease"] = _text(diagnosis["disease"], "diagnoses.disease", 300)
    answer = normalized(diagnoses["mainDiagnosis"]["disease"])
    if answer in normalized(case["caseTitle"]) or answer in normalized(case["summary"]):
        _fail("caseTitle/summary: não inclua a resposta")
    case["learningObjectives"] = _strings(case["learningObjectives"], "learningObjectives", 12, False)
    references = case["references"]
    if not isinstance(references, list) or not 1 <= len(references) <= 12:
        _fail("references")
    for ref in references:
        if not isinstance(ref, dict) or set(ref) != {"title", "url"}:
            _fail("references")
        ref["title"] = _text(ref["title"], "references.title", 300)
        ref["url"] = _text(ref["url"], "references.url", 1000)
        parsed = urlparse(ref["url"])
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            _fail("references.url")
    rubric = case["rubric"]
    if not isinstance(rubric, dict) or set(rubric) != set(SECTION_TITLES):
        _fail("rubric")
    for key, section in rubric.items():
        if not isinstance(section, dict) or set(section) != {"criteria", "criticalActions"}:
            _fail("rubric." + key)
        section["criteria"] = _strings(section["criteria"], "rubric." + key, 12, False)
        section["criticalActions"] = _strings(section["criticalActions"], "rubric." + key, 12)
    if "simulationOnly" in case and case["simulationOnly"] is not True:
        _fail("simulationOnly")
    case["simulationOnly"] = True
    case.setdefault("reviewStatus", "review_required")
    if case["reviewStatus"] not in {"educational_seed", "review_required", "reviewed"}:
        _fail("reviewStatus")
    for key in ("origin", "reviewNote"):
        if key in case:
            case[key] = _text(case[key], key, 1200)
    return case
