"""Strict contracts for fictional and source-grounded educational simulations."""

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
UNKNOWN_FACT = "Não informado na fonte."
REUSE_POLICY_VERSION = 2
LICENSE_CODES_BY_SCOPE = {
    "commercial": frozenset({"CC BY", "CC0"}),
    "noncommercial": frozenset({"CC BY", "CC0", "CC BY-NC", "CC BY-SA", "CC BY-NC-SA"}),
}
FACTUAL_SOURCE_LICENSES = frozenset({"CC BY-ND", "CC BY-NC-ND"})


def allowed_license_codes(reuse_scope="commercial"):
    """Application policy; commercial intentionally retains the original subset."""
    if reuse_scope not in LICENSE_CODES_BY_SCOPE:
        raise ValueError("CASE_REUSE_SCOPE deve ser commercial ou noncommercial.")
    return LICENSE_CODES_BY_SCOPE[reuse_scope]


def license_code_from_url(url, include_nd=False):
    """Recognize exact, versioned international CC terms; never normalize ND to BY."""
    if not isinstance(url, str):
        return None
    if url == "https://creativecommons.org/publicdomain/zero/1.0/":
        return "CC0"
    if include_nd and url == "https://creativecommons.org/licenses/by-nc-nd/3.0/us/":
        return "CC BY-NC-ND"
    slugs = "by|by-nc|by-sa|by-nc-sa" + ("|by-nd|by-nc-nd" if include_nd else "")
    match = re.fullmatch(r"https://creativecommons.org/licenses/(" + slugs + r")/(1\.0|2\.0|2\.5|3\.0|4\.0)/", url)
    return "CC " + match[1].upper() if match else None


def license_version_from_url(url, include_nd=False):
    if license_code_from_url(url, include_nd=include_nd) is None:
        return None
    return urlparse(url).path.strip("/").split("/")[2]


def source_reuse_basis(code, reuse_scope="commercial", allow_clinical_facts=False):
    """Separate licensed expression from an explicitly enabled factual workflow."""
    if not isinstance(code, str):
        return None
    if code in allowed_license_codes(reuse_scope):
        return "licensed_adaptation"
    if allow_clinical_facts is True and code in FACTUAL_SOURCE_LICENSES:
        if code == "CC BY-NC-ND" and reuse_scope != "noncommercial":
            return None
        return "clinical_facts"
    return None


def source_license_allowed(provenance, reuse_scope="commercial", allow_clinical_facts=False):
    if not isinstance(provenance, dict) or not isinstance(provenance.get("license"), dict):
        return False
    license_data = provenance["license"]
    basis = source_reuse_basis(license_data.get("code"), reuse_scope, allow_clinical_facts)
    return bool(basis and provenance.get("reuseBasis", "licensed_adaptation") == basis
                and license_code_from_url(license_data.get("url"), include_nd=basis == "clinical_facts") == license_data.get("code"))


def license_obligations(code, url):
    return {"attributionRequired": code != "CC0", "nonCommercial": "-NC" in code,
            "shareAlike": code.endswith("-SA"), "adaptationLicenseUrl": url}


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
    optional = {"simulationOnly", "reviewStatus", "origin", "reviewNote", "catalogType",
                "caseType", "publicPresentation", "provenance", "examMedia"}
    if not isinstance(data, dict) or not required <= data.keys() or data.keys() - required - optional:
        _fail("estrutura")
    case = deepcopy(data)
    case.setdefault("catalogType", "fictional")
    if case["catalogType"] not in {"fictional", "published_case"}:
        _fail("catalogType")
    real_case = case["catalogType"] == "published_case"
    case.setdefault("caseType", "Primeira consulta")
    from .catalog import CASE_TYPES
    if case["caseType"] not in CASE_TYPES:
        _fail("caseType")
    if "publicPresentation" in case:
        case["publicPresentation"] = _text(case["publicPresentation"], "publicPresentation", 800)
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
    if not (real_case and age is None) and (isinstance(age, bool) or not isinstance(age, (int, float)) or not 0 <= age <= 120):
        _fail("patientProfile.age_value")
    for name, value in profile.items():
        if name not in {"age_value", "symptom_details"}:
            profile[name] = _text(value, "patientProfile." + name)
    if real_case and profile["name"] != "Paciente simulado":
        _fail("patientProfile.name: utilize Paciente simulado")
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
    if not isinstance(diagnoses["differentialDiagnoses"], list) or not (0 if real_case else 1) <= len(diagnoses["differentialDiagnoses"]) <= 10:
        _fail("diagnoses.differentialDiagnoses")
    for diagnosis in [diagnoses["mainDiagnosis"], *diagnoses["differentialDiagnoses"]]:
        if not isinstance(diagnosis, dict) or set(diagnosis) != {"disease"}:
            _fail("diagnoses")
        diagnosis["disease"] = _text(diagnosis["disease"], "diagnoses.disease", 300)
    answer = normalized(diagnoses["mainDiagnosis"]["disease"])
    if any(answer in normalized(case.get(field, "")) for field in ("caseTitle", "summary", "publicPresentation")):
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
        if not isinstance(section, dict) or not {"criteria", "criticalActions"} <= section.keys() or section.keys() - {"criteria", "criticalActions", "assessable", "reason"}:
            _fail("rubric." + key)
        assessable = section.get("assessable", True)
        if not isinstance(assessable, bool) or (key in {"consultation", "diagnosis", "overall"} and not assessable):
            _fail("rubric." + key + ".assessable")
        section["criteria"] = _strings(section["criteria"], "rubric." + key, 12, not assessable)
        section["criticalActions"] = _strings(section["criticalActions"], "rubric." + key, 12)
        if not assessable:
            if section["criteria"] or section["criticalActions"]:
                _fail("rubric." + key + ": dimensão sem evidência não deve pontuar")
            section["reason"] = _text(section.get("reason"), "rubric." + key + ".reason", 1200)
        elif "reason" in section:
            section["reason"] = _text(section["reason"], "rubric." + key + ".reason", 1200)
    if "simulationOnly" in case and case["simulationOnly"] is not True:
        _fail("simulationOnly")
    case["simulationOnly"] = True
    case.setdefault("reviewStatus", "review_required")
    if case["reviewStatus"] not in {"educational_seed", "review_required", "reviewed"}:
        _fail("reviewStatus")
    for key in ("origin", "reviewNote"):
        if key in case:
            case[key] = _text(case[key], key, 1200)
    if real_case:
        _validate_provenance(case.get("provenance"))
        if case["provenance"].get("reuseBasis") == "clinical_facts":
            _validate_factual_coverage(case)
        if case["reviewStatus"] == "educational_seed" or case["caseId"] != "mcr-" + case["provenance"]["pmcid"].lower():
            _fail("reviewStatus/caseId: relato real requer origem e revisão próprias")
        if profile["chief_complaint"] == UNKNOWN_FACT or profile["present_illness_history"] == UNKNOWN_FACT or diagnoses["mainDiagnosis"]["disease"] == UNKNOWN_FACT:
            _fail("patientProfile/diagnoses: faltam fatos centrais do relato")
        for key, available in (("physical", bool(case["physicalExams"])), ("auxiliary", bool(case["auxiliaryLabs"])), ("treatment", case["treatments"] != UNKNOWN_FACT)):
            if not available and rubric[key].get("assessable", True):
                _fail("rubric." + key + ": faltam fatos para avaliar")
    elif "provenance" in case:
        _fail("provenance: somente relatos reais adaptados")
    if "examMedia" in case:
        _validate_exam_media(case)
    return case


def _validate_exam_media(case):
    """Structural links only: the private media store separately approves each binding."""
    entries = case["examMedia"]
    if not isinstance(entries, list) or len(entries) > 12 or (entries and case["catalogType"] != "published_case"):
        _fail("examMedia")
    maps = {"physical": "physicalExams", "auxiliary": "auxiliaryLabs"}
    seen = set()
    diagnosis = normalized(case["diagnoses"]["mainDiagnosis"]["disease"])
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"examName", "group", "assetId", "captionPt"}:
            _fail("examMedia.estrutura")
        if not isinstance(entry["group"], str) or entry["group"] not in maps:
            _fail("examMedia.group")
        entry["examName"] = _text(entry["examName"], "examMedia.examName", 160)
        if entry["examName"] not in case[maps[entry["group"]]]:
            _fail("examMedia.examName: o exame deve existir no caso")
        asset = entry["assetId"]
        if not isinstance(asset, str) or not re.fullmatch(r"pmcmedia-[0-9a-f]{32}", asset) or asset in seen:
            _fail("examMedia.assetId")
        seen.add(asset)
        entry["captionPt"] = _text(entry["captionPt"], "examMedia.captionPt", 800)
        if diagnosis in normalized(entry["captionPt"]):
            _fail("examMedia.captionPt: descreva a imagem sem antecipar o diagnóstico")


def _validate_provenance(value):
    required = {"dataset", "datasetRevision", "sourceRecordId", "pmcid", "doi", "articleUrl",
                "articleTitle", "citation", "authors", "license", "pmcVersion", "xmlSha256",
                "xmlMd5", "metadataSha256", "checkedAt", "adaptedAt", "adaptedBy",
                "language", "clinicalMoment", "changes", "missingFacts", "evidence", "audit"}
    factual = isinstance(value, dict) and value.get("reuseBasis") == "clinical_facts"
    if factual:
        required |= {"reuseBasis", "sourceFacts", "factualEvidence"}
    if not isinstance(value, dict) or set(value) != required:
        _fail("provenance")
    if value["dataset"] != "MedCaseReasoning" or value["language"] != "pt-BR":
        _fail("provenance.dataset/language")
    if not re.fullmatch(r"PMC[1-9][0-9]{0,11}", str(value["pmcid"])):
        _fail("provenance.pmcid")
    if value["articleUrl"] != "https://pmc.ncbi.nlm.nih.gov/articles/" + value["pmcid"] + "/":
        _fail("provenance.articleUrl")
    if isinstance(value["pmcVersion"], bool) or not isinstance(value["pmcVersion"], int) or value["pmcVersion"] < 1:
        _fail("provenance.pmcVersion")
    for key, length in (("xmlSha256", 64), ("metadataSha256", 64), ("xmlMd5", 32)):
        if not re.fullmatch(r"[0-9a-f]{" + str(length) + r"}", str(value[key])):
            _fail("provenance." + key)
    for key in ("datasetRevision", "sourceRecordId", "articleTitle", "citation", "checkedAt", "adaptedAt", "adaptedBy", "clinicalMoment"):
        value[key] = _text(value[key], "provenance." + key, 1200)
    if value["doi"] is not None:
        value["doi"] = _text(value["doi"], "provenance.doi", 300)
    for key in ("authors", "changes", "missingFacts"):
        value[key] = _strings(value[key], "provenance." + key, 100)
    license_data = value["license"]
    license_required = {"code", "url", "version", "text"}
    license_optional = {"attributionRequired", "nonCommercial", "shareAlike", "adaptationLicenseUrl"}
    if not isinstance(license_data, dict) or not license_required <= set(license_data) or set(license_data) - license_required - license_optional:
        _fail("provenance.license")
    if not isinstance(license_data["code"], str) or license_data["code"] not in (FACTUAL_SOURCE_LICENSES if factual else LICENSE_CODES_BY_SCOPE["noncommercial"]):
        _fail("provenance.license.code")
    if license_code_from_url(license_data["url"], include_nd=factual) != license_data["code"]:
        _fail("provenance.license.url")
    if license_data["version"] != license_version_from_url(license_data["url"], include_nd=factual):
        _fail("provenance.license.version/code")
    for key, expected in license_obligations(license_data["code"], license_data["url"]).items():
        if factual and key == "adaptationLicenseUrl":
            if key in license_data:
                _fail("provenance.license: licença do artigo não licencia o roteiro factual")
            continue
        if key in license_data and (license_data[key] != expected or type(license_data[key]) is not type(expected)):
            _fail("provenance.license." + key)
    for key in ("version", "text"):
        license_data[key] = _text(license_data[key], "provenance.license." + key, 6000)
    evidence = value["evidence"]
    if factual:
        if evidence != []:
            _fail("provenance.evidence: fluxo factual não conserva citações")
        _validate_factual_proof(value)
    elif not isinstance(evidence, list) or not 1 <= len(evidence) <= 180:
        _fail("provenance.evidence")
    for item in evidence:
        if not isinstance(item, dict) or set(item) != {"path", "excerpt"}:
            _fail("provenance.evidence")
        item["path"] = _text(item["path"], "provenance.evidence.path", 500)
        item["excerpt"] = _text(item["excerpt"], "provenance.evidence.excerpt", 2000)
    audit = value["audit"]
    flags = {"portuguese", "factsSupported", "unknownsPreserved", "deidentified", "patientBeforeDiagnosis"}
    if factual:
        flags |= {"atomicFactsOnly", "originalWording"}
    if not isinstance(audit, dict) or set(audit) != flags or any(audit[k] is not True for k in flags):
        _fail("provenance.audit")


def _validate_factual_proof(value):
    facts, evidence = value["sourceFacts"], value["factualEvidence"]
    if not isinstance(facts, list) or not 2 <= len(facts) <= 80:
        _fail("provenance.sourceFacts")
    ids = set()
    for fact in facts:
        if not isinstance(fact, dict) or set(fact) != {"factId", "text", "sourceLocation", "sourceSha256"}:
            _fail("provenance.sourceFacts")
        if not isinstance(fact["factId"], str) or not re.fullmatch(r"F[0-9]{3}", fact["factId"]) or fact["factId"] in ids:
            _fail("provenance.sourceFacts.factId")
        ids.add(fact["factId"])
        fact["text"] = _text(fact["text"], "provenance.sourceFacts.text", 300)
        location = fact["sourceLocation"]
        if not isinstance(location, dict) or set(location) != {"start", "end"} or any(type(location[key]) is not int for key in location):
            _fail("provenance.sourceFacts.sourceLocation")
        if not 0 <= location["start"] < location["end"] <= 140000 or location["end"] - location["start"] > 1200:
            _fail("provenance.sourceFacts.sourceLocation")
        if not isinstance(fact["sourceSha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", fact["sourceSha256"]):
            _fail("provenance.sourceFacts.sourceSha256")
    if not isinstance(evidence, list) or not 1 <= len(evidence) <= 180:
        _fail("provenance.factualEvidence")
    for item in evidence:
        if not isinstance(item, dict) or set(item) != {"path", "factIds"}:
            _fail("provenance.factualEvidence")
        item["path"] = _text(item["path"], "provenance.factualEvidence.path", 500)
        refs = item["factIds"]
        if not isinstance(refs, list) or not 1 <= len(refs) <= 80 or any(not isinstance(ref, str) or ref not in ids for ref in refs) or len(set(refs)) != len(refs):
            _fail("provenance.factualEvidence.factIds")


def _validate_factual_coverage(case):
    leaves = {}
    def walk(value, path):
        if isinstance(value, dict):
            for key, item in value.items():
                if path == "/patientProfile" and key == "name":
                    continue
                walk(item, path + "/" + key.replace("~", "~0").replace("/", "~1"))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, path + "/" + str(index))
        else:
            leaves[path] = value
    for key in ("patientProfile", "physicalExams", "auxiliaryLabs", "diagnoses", "treatments"):
        walk(case[key], "/" + key)
    unknowns = {path for path, value in leaves.items() if value is None or value == UNKNOWN_FACT}
    evidence = case["provenance"]["factualEvidence"]
    covered = {item["path"] for item in evidence}
    if covered - set(leaves) or set(leaves) - unknowns - covered or unknowns != set(case["provenance"]["missingFacts"]):
        _fail("provenance.factualEvidence: cobertura factual incompleta")
