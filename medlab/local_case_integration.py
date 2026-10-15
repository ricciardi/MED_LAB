"""Assemble locally translated cases without claiming a clinical or AI review.

The extractor package remains the private evidence ledger. Heuristic matches are
not promoted to audited provenance, and availability is a separate, explicit
authorization. This module performs no network request or database write.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import re

from .case_schema import UNKNOWN_FACT, is_nonhuman_case, validate_case

VERSION = "local-structured-translation-v1"
REVIEW_NOTE = ("Preparação local com tradução automática. Revisão clínica, verificação da tradução "
               "e auditoria semântica ainda pendentes. A disponibilidade não constitui aprovação médica.")

# Names organize a documented result; they never generate a normal result.
_EXAM_NAMES = (
    (r"computed tomography|\bCT\b", "Tomografia computadorizada"),
    (r"magnetic resonance|\bMRI\b", "Ressonância magnética"),
    (r"ultrason|ultrasound|sonograph", "Ultrassonografia"),
    (r"radiograph|x[- ]ray", "Radiografia"),
    (r"histopath|histolog|biops", "Biópsia e exame histopatológico"),
    (r"immunohistochem", "Imuno-histoquímica"),
    (r"echocardiograph", "Ecocardiograma"),
    (r"electrocardiograph|\bECG\b", "Eletrocardiograma"),
    (r"\bEEG\b", "Eletroencefalograma"),
    (r"endoscop", "Endoscopia"),
    (r"colonoscop", "Colonoscopia"),
    (r"bronchoscop", "Broncoscopia"),
    (r"cultures?", "Culturas"),
    (r"polymerase|\bPCR\b", "Pesquisa molecular"),
    (r"leukocyte|white.*cell|hemoglobin|haemoglobin|platelet", "Hemograma"),
    (r"creatinine", "Creatinina"),
    (r"glucose", "Glicemia"),
    (r"sodium|potassium", "Eletrólitos"),
    (r"reactive protein|\bCRP\b|sedimentation|\bESR\b", "Marcadores inflamatórios"),
    (r"blood pressure|heart rate|respiratory rate|body temperature|oxygen saturation|\bBMI\b", "Sinais vitais e medidas"),
    (r"neurological examination", "Exame neurológico"),
    (r"physical examination|palpation|auscultation|range of motion", "Exame físico documentado"),
)


def _now():
    return datetime.now(timezone.utc).isoformat()


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode()).hexdigest()


def authorize_provisional_use(case, reference, *, authorized_at=None):
    """Record a deliberate authorization, preserving how the case was prepared.

    This is not the clinical-review action. Callers must separately choose to set
    is_published; no database state is changed by this helper.
    """
    data = validate_case(case)
    if is_nonhuman_case(data):
        raise ValueError("Relatos de animais permanecem no acervo privado, separados da prática clínica humana.")
    if data["catalogType"] != "published_case" or data["reviewStatus"] != "review_required":
        raise ValueError("A autorização provisória se aplica apenas a relatos com revisão pendente.")
    data["useAuthorization"] = {
        "version": "provisional-use-v1", "acceptedForUse": True,
        "clinicalReviewDeferred": True, "authorizationReference": reference,
        "authorizedAt": authorized_at or _now(),
    }
    return validate_case(data)


def _joined(values, maximum=6000):
    """Bound stored fields without inventing a missing continuation."""
    unique = list(dict.fromkeys(v.strip() for v in values if isinstance(v, str) and v.strip()))
    if not unique:
        return UNKNOWN_FACT
    text = "\n".join(unique)
    if len(text) <= maximum:
        return text
    suffix = " [Trecho limitado; conteúdo completo no pacote de preparação.]"
    return text[:maximum - len(suffix)].rsplit(" ", 1)[0] + suffix


def _full_joined(values):
    return "\n".join(dict.fromkeys(value.strip() for value in values if isinstance(value, str) and value.strip()))


def _exam_continuations(exams):
    """Keep full findings, with explicitly ordered continuation entries."""
    output = {}
    for name, text in exams.items():
        remaining, parts = text.strip(), []
        while len(remaining) > 3000:
            boundary = remaining.rfind("\n", 1500, 3001)
            if boundary < 0:
                boundary = remaining.rfind(" ", 1500, 3001)
            if boundary < 0:
                boundary = 3000
            parts.append(remaining[:boundary].rstrip())
            remaining = remaining[boundary:].lstrip()
        if remaining:
            parts.append(remaining)
        for index, part in enumerate(parts, 1):
            suffix = "" if len(parts) == 1 else (f" (parte 1 de {len(parts)})" if index == 1 else f" (continuação {index} de {len(parts)})")
            key = name[:160 - len(suffix)] + suffix
            if key in output:
                raise ValueError("Nomes de exames em continuação não são únicos.")
            output[key] = part
    if len(output) > 50:
        raise ValueError("Os resultados completos excedem 50 entradas; reorganize os exames sem descartar conteúdo.")
    return output


def _eligible(snippet, package):
    selection = package.get("selection", {})
    if snippet.get("context") == "editorial":
        return False
    if selection.get("multiplePatientsDetected"):
        # Shared/mixed observations do not establish an individual's finding.
        return snippet.get("patientRef") == selection.get("selectedPatientRef") and snippet.get("patientRef") not in {None, "mixed", "shared"}
    return snippet.get("patientRef") not in {"mixed", "shared"}


def _age(package, snippets):
    values = set()
    for mention in package.get("demographicMentions", []):
        snippet = snippets.get(mention.get("snippetId"), {})
        if mention.get("kind") != "age" or not _eligible(snippet, package):
            continue
        raw = str(mention.get("valueOriginal", ""))
        unit = {"year": "anos", "month": "meses", "week": "semanas", "day": "dias"}.get(mention.get("unitOriginal"))
        if re.fullmatch(r"\d{1,3}(?:\.\d+)?", raw) and 0 <= float(raw) <= 120 and unit:
            value = float(raw)
            values.add((int(value) if value.is_integer() else value, unit))
    return next(iter(values)) if len(values) == 1 else (None, UNKNOWN_FACT)


def _gender(package, snippets):
    values = {mention.get("valueOriginal") for mention in package.get("demographicMentions", [])
              if mention.get("kind") == "gender" and mention.get("valueOriginal") in {"male", "female"}
              and mention.get("snippetId") in snippets and _eligible(snippets[mention["snippetId"]], package)}
    return {"male": "Masculino", "female": "Feminino"}[next(iter(values))] if len(values) == 1 else UNKNOWN_FACT


def _respondent(package, snippets):
    """Preserve an explicit informant, never infer one solely from age or gender."""
    relative = r"(?:mother|father|parents|guardian|caregiver)"
    patterns = (
        r"\b" + relative + r"\s+(?:(?:had|also|initially|later|then|both)\s+){0,2}(?:reported|stated|noticed|observed|described|denied|recalled|provided|said)\b",
        r"\baccording to (?:the |his |her |their )?" + relative + r"\b",
        r"\bhistory (?:was |had been )?(?:obtained|provided|reported) by (?:the |his |her |their )?" + relative + r"\b",
    )
    if any(_eligible(snippet, package) and any(re.search(pattern, snippet.get("text", ""), re.I) for pattern in patterns)
           for snippet in snippets.values()):
        return "Responsável pelo paciente fornece o relato. Não há identidade, sexo ou idade do informante definidos nesta ficha."
    return None


def _species_metadata(package):
    """Preserve the classifier's result without copying original source excerpts."""
    species = package.get("patientSpecies")
    if species is None:
        return None  # Compatible with older packages and preserved cases.
    if not isinstance(species, dict) or not isinstance(species.get("evidence"), list):
        raise ValueError("Metadados de espécie inválidos.")
    hashes = []
    for evidence in species["evidence"]:
        if (not isinstance(evidence, dict) or evidence.get("field") != "case_prompt"
                or not isinstance(evidence.get("text"), str)
                or type(evidence.get("start")) is not int or type(evidence.get("end")) is not int
                or evidence["start"] < 0 or evidence["end"] <= evidence["start"]
                or evidence.get("textSha256") != hashlib.sha256(evidence["text"].encode()).hexdigest()):
            raise ValueError("Evidência privada de espécie inconsistente.")
        hashes.append(evidence["textSha256"])
    return {"kind": species.get("kind"), "species": species.get("species"),
            "method": species.get("method"), "evidenceHashes": list(dict.fromkeys(hashes))}


def _presentation(snippets, age, age_unit):
    """Controlled presentation labels use only the selected initial complaint."""
    text = " ".join(s.get("text", "") for s in snippets)
    rules = (
        (r"\bchest pain\b", "Dor torácica"),
        (r"\babdominal pain\b|\bepigastric pain\b", "Dor abdominal"),
        (r"\bcough|dyspn[oe]+a|wheez|shortness of breath", "Sintomas respiratórios"),
        (r"\bdysuria|hematuria|haematuria|urinar", "Sintomas urinários"),
        (r"\bheadache|seizure|syncope|numbness|tingling|weakness of", "Cefaleia e sintomas neurológicos"),
        (r"\bvomit|nausea|diarrh|constipat|rectal bleed", "Sintomas digestivos"),
        (r"\brash|pruritus|itch|skin lesion", "Alterações da pele"),
        (r"\bback pain|joint pain|limb pain|limited movement", "Dor e limitação de movimento"),
        (r"\bvaginal bleed|menstrual|pelvic pain", "Sintomas ginecológicos"),
        (r"\bdepress|anxiety|low mood|hallucinat", "Saúde emocional"),
        (r"\bfever|fatigue|weight loss|malaise", "Febre e sintomas gerais"),
    )
    for pattern, label in rules:
        if re.search(pattern, text, re.I):
            return label
    if age is not None and (age_unit != "anos" or age < 18):
        return "Queixa pediátrica"
    return "Outros sintomas" if text.strip() else "Primeira consulta"


def _missing(case):
    output = []
    def walk(value, path):
        if isinstance(value, dict):
            for key, item in value.items():
                walk(item, path + "/" + key.replace("~", "~0").replace("/", "~1"))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, path + "/" + str(index))
        elif value is None or value == UNKNOWN_FACT:
            output.append(path)
    for field in ("patientProfile", "physicalExams", "auxiliaryLabs", "diagnoses", "treatments"):
        walk(case[field], "/" + field)
    return output


def assemble_local_case(package, translations, translation_metadata, *, accepted_for_use=False,
                        authorization_reference=None):
    """Build a structurally valid PT case from the extractor's explicit units.

    ``translations`` maps each local unit id to its completed Portuguese string.
    ``translation_metadata`` identifies model/revision; no online fallback exists.
    Incomplete translations fail before a case can be marked translated.
    """
    if (not isinstance(package, dict) or package.get("schemaVersion") != "local-sentence-extraction-v1"
            or package.get("stage") != "extracted_original" or package.get("clinicalApproval") is not False):
        raise ValueError("Pacote de extração local inválido.")
    if type(accepted_for_use) is not bool or not isinstance(translations, dict) or not isinstance(translation_metadata, dict):
        raise ValueError("Parâmetros de preparação local inválidos.")
    origin = deepcopy(package.get("sourceProvenance"))
    if not isinstance(origin, dict) or package.get("pmcid") != origin.get("pmcid") or package.get("caseId") != "mcr-" + str(origin.get("pmcid", "")).lower():
        raise ValueError("Identidade da fonte não corresponde à ficha.")
    units = package.get("translationUnits")
    if not isinstance(units, list) or not units:
        raise ValueError("Não há unidades de tradução.")
    translated, unit_ids, sentences, terms, relation_translations = {}, set(), {}, {}, {}
    for unit in units:
        if (not isinstance(unit, dict) or unit.get("pmcid") != origin["pmcid"]
                or not isinstance(unit.get("id"), str) or unit["id"] in unit_ids):
            raise ValueError("Unidade de tradução inconsistente.")
        key = unit["id"]
        unit_ids.add(key)
        value = translations.get(key)
        if not isinstance(value, str) or not value.strip() or "\x00" in value or len(value) > 40000:
            raise ValueError("Tradução pendente ou inválida: " + key)
        translated[key] = value.strip()
        if unit.get("kind") == "source_sentence":
            sentences[unit.get("snippetId")] = value.strip()
        elif unit.get("kind") in {"clinical_term", "anatomical_term", "duration"}:
            terms[unit.get("text")] = value.strip()
        elif unit.get("kind") in {"factual_relation_subject", "factual_relation_value"}:
            relation_translations[(unit["kind"], unit.get("text"))] = value.strip()
    factual = origin.get("reuseBasis") == "clinical_facts"
    if factual and any(unit.get("kind") == "source_sentence" for unit in units):
        raise ValueError("O roteiro factual não traduz a narrativa da fonte ND.")
    snippets = {item["id"]: item for item in package.get("snippets", [])}
    if len(snippets) != len(package.get("snippets", [])):
        raise ValueError("Identificadores de trechos repetidos.")
    relations = {}
    if factual:
        # Preserve the limited meaning of the extractor's unreviewed mentions.
        # A negation cue is not silently converted into a clinical negative.
        per_snippet = {}
        for descriptor in package.get("atomicDescriptors", []):
            sid = descriptor.get("snippetId")
            if sid not in snippets or not _eligible(snippets[sid], package):
                continue
            if descriptor.get("kind") == "relation":
                if descriptor.get("binding") != "explicit_predicate_same_clause" or type(descriptor.get("negated")) is not bool:
                    raise ValueError("Relação clínica local sem predicado explícito.")
                subject = relation_translations.get(("factual_relation_subject", descriptor.get("subjectOriginal")))
                value = relation_translations.get(("factual_relation_value", descriptor.get("valueOriginal")))
                if not subject or not value:
                    raise ValueError("Relação clínica sem tradução completa.")
                relation = descriptor.get("relation")
                negative = descriptor["negated"]
                value = value.rstrip(". ")
                if relation == "reported_symptom":
                    text = ("Nega " if negative else "Relata ") + value + "."
                elif relation == "history":
                    text = "Antecedentes documentados: " + ("nega " if negative else "") + value + "."
                elif relation in {"exam_result", "physical_finding"}:
                    text = subject + ": " + ("ausência de " if negative else "") + value + "."
                elif relation == "historical_treatment":
                    text = "Conduta registrada no relato: " + ("não recebeu " if negative else "") + value + "."
                elif relation == "documented_finding":
                    text = "Achado documentado: " + ("ausência de " if negative else "") + value + "."
                else:
                    raise ValueError("Tipo de relação clínica não reconhecido.")
                relations.setdefault(sid, []).append({"relation": relation, "subject": subject,
                    "subjectOriginal": descriptor["subjectOriginal"], "text": text})
                continue
            if descriptor.get("kind") in {"clinical_term", "anatomical_term", "duration"}:
                if descriptor.get("kind") == "duration" and any(
                    mention.get("kind") == "age" and mention.get("snippetId") == sid
                    and str(mention.get("phraseOriginal", "")).startswith(str(descriptor.get("term", "")))
                    for mention in package.get("demographicMentions", [])
                ):
                    continue
                term = terms.get(descriptor.get("term"))
                if not term:
                    raise ValueError("Termo clínico sem tradução.")
                mention = term + (" (marcador de negação no trecho; conferir contexto)" if descriptor.get("negationCue") else "")
                per_snippet.setdefault(sid, []).append(mention)
            elif descriptor.get("kind") == "measurement":
                # The value's association with a particular test is unresolved.
                per_snippet.setdefault(sid, []).append("valor mencionado, associação pendente: " + str(descriptor.get("value", "")))
        sentences = {sid: "Menções clínicas extraídas; relação com o paciente ainda não revisada: " + "; ".join(dict.fromkeys(values)) + "."
                     for sid, values in per_snippet.items()}
        sentences.update({sid: _joined([item["text"] for item in items]) for sid, items in relations.items()})
    def snippet_text(snippet, purpose):
        sid = snippet["id"]
        if not factual or sid not in relations:
            return sentences.get(sid, UNKNOWN_FACT)
        kinds = {"patient": {"reported_symptom", "history"}, "physical": {"physical_finding"},
                 "auxiliary": {"exam_result"}, "treatment": {"historical_treatment"}}[purpose]
        return _joined([r["text"] for r in relations[sid] if r["relation"] in kinds])
    usable = [s for s in snippets.values() if s["id"] in sentences and _eligible(s, package)]
    profile_fields = ("occupation", "chief_complaint", "present_illness_history", "past_medical_history",
                      "personal_history", "family_history", "other_medical_history", "allergy_history")
    profile = {field: UNKNOWN_FACT for field in profile_fields}
    age, age_unit = _age(package, snippets)
    profile.update(name="Paciente simulado", age_value=age, age_unit=age_unit, gender=_gender(package, snippets))
    respondent = _respondent(package, snippets)
    if respondent:
        profile["respondent"] = respondent
    # The extractor already excludes professional/future information. Its
    # lexical temporal tag also matches recurrence and symptoms after a prior
    # event, so it cannot independently disqualify an initial complaint.
    patient_snippets = [s for s in usable if s.get("patientCandidate")]
    patient_snippets = [s for s in patient_snippets if snippet_text(s, "patient") != UNKNOWN_FACT]
    symptoms = [snippet_text(s, "patient") for s in patient_snippets if "symptoms" in s.get("groups", [])]
    patient_text = [snippet_text(s, "patient") for s in patient_snippets]
    profile["present_illness_history"] = _joined(patient_text)
    profile["chief_complaint"] = _joined((symptoms or patient_text)[:1], 1200)
    for field in ("past_medical_history", "personal_history", "family_history", "other_medical_history", "allergy_history", "surgery_injury_history", "reproductive_history"):
        values = [snippet_text(s, "patient") for s in patient_snippets
                  if field in s.get("historyFields", []) or (field == "past_medical_history" and "history" in s.get("groups", []) and not s.get("historyFields"))]
        if values or field in profile:
            profile[field] = _joined(values)
    physical, auxiliary, bound_exam_names = {}, {}, set()
    neutral_exam_names = {}
    for snippet in usable:
        if factual and snippet["id"] in relations:
            for relation in relations[snippet["id"]]:
                if relation["relation"] not in {"physical_finding", "exam_result"}:
                    continue
                target = physical if relation["relation"] == "physical_finding" else auxiliary
                name = next((label for pattern, label in _EXAM_NAMES
                             if re.search(pattern, relation["subjectOriginal"], re.I)), None)
                if name is None:
                    # An arbitrary translated subject can remain English or
                    # reveal a finding. Keep it within the requested result;
                    # the chooser uses a neutral Portuguese label instead.
                    group = "physical" if target is physical else "auxiliary"
                    key = (group, relation["subjectOriginal"])
                    if key not in neutral_exam_names:
                        number = sum(existing[0] == group for existing in neutral_exam_names) + 1
                        label = "Exame físico documentado" if group == "physical" else "Exame complementar documentado"
                        neutral_exam_names[key] = f"{label} {number}"
                    name = neutral_exam_names[key]
                identity = ("physical" if target is physical else "auxiliary", name)
                previous = target.get(name, "") if identity in bound_exam_names else ""
                target[name] = _full_joined([previous, relation["text"]])
                bound_exam_names.add(identity)
            continue
        groups = set(snippet.get("groups", []))
        if "auxiliary_exams" in groups:
            target, default = auxiliary, "Exames complementares documentados"
        elif "physical_exams" in groups:
            target, default = physical, "Exame físico documentado"
        else:
            continue
        names = list(dict.fromkeys(label for pattern, label in _EXAM_NAMES
                                  if any(re.search(pattern, term, re.I) for term in snippet.get("examTerms", []))))
        name = " / ".join(names[:3])[:160] or default
        if factual and ("physical" if target is physical else "auxiliary", name) in bound_exam_names:
            continue
        # One source sentence appears once, avoiding several exams with identical
        # results that would imply separately documented measurements.
        target[name] = _full_joined([target.get(name, ""), sentences[snippet["id"]]])
    physical, auxiliary = _exam_continuations(physical), _exam_continuations(auxiliary)
    treatment_ids = {item.get("snippetId") for item in package.get("atomicDescriptors", [])
                     if item.get("kind") == "relation" and item.get("relation") == "historical_treatment"}
    treatments = _joined([snippet_text(s, "treatment") for s in usable
                          if s["id"] in treatment_ids and snippet_text(s, "treatment") != UNKNOWN_FACT])
    diagnosis_units = [unit for unit in units if unit.get("kind") == "dataset_diagnosis_label"]
    diagnosis = _joined([translated[unit["id"]] for unit in diagnosis_units], 300)
    specialty = package.get("specialty", {}).get("specialty") or "Clínica médica"
    consultation_criteria, consultation_items = [], []
    for field, category, label in (
        ("chief_complaint", "Queixa principal", "Investigar a queixa apresentada"),
        ("present_illness_history", "Evolução do quadro", "Esclarecer a evolução e os dados da história atual"),
        ("past_medical_history", "Doenças crônicas", "Investigar os antecedentes documentados"),
        ("personal_history", "Hábitos de vida", "Investigar hábitos e exposições documentados"),
        ("family_history", "História familiar", "Investigar a história familiar documentada"),
        ("other_medical_history", "Medicamentos em uso", "Investigar medicamentos documentados"),
        ("allergy_history", "Alergias", "Investigar as alergias documentadas"),
        ("surgery_injury_history", "Cirurgias e traumas", "Investigar cirurgias e traumas documentados"),
    ):
        if profile.get(field, UNKNOWN_FACT) != UNKNOWN_FACT:
            consultation_items.append(category)
            consultation_criteria.append(label + ": " + profile[field][:650])
    rubric = {
        "consultation": {"criteria": consultation_criteria, "criticalActions": [], "assessable": bool(consultation_criteria)},
        "physical": {"criteria": ["Relacionar os exames solicitados aos achados físicos disponíveis: " + "; ".join(physical)[:900], "Distinguir solicitação de exame de execução ou interpretação efetivamente registrada pelo aluno."] if physical else [], "criticalActions": [], "assessable": bool(physical)},
        "auxiliary": {"criteria": ["Justificar os exames solicitados em relação ao quadro e aos resultados disponíveis: " + "; ".join(auxiliary)[:900]] if auxiliary else [], "criticalActions": [], "assessable": bool(auxiliary)},
        "diagnosis": {"criteria": ["Reconhecer o diagnóstico de referência: " + diagnosis, "Relacionar a hipótese proposta aos achados e ao raciocínio de referência do caso."] if diagnosis != UNKNOWN_FACT else [], "criticalActions": [], "assessable": diagnosis != UNKNOWN_FACT},
        "treatment": {"criteria": ["Comparar a conduta proposta com a referência documentada: " + treatments[:900]] if treatments != UNKNOWN_FACT else [], "criticalActions": [], "assessable": treatments != UNKNOWN_FACT},
        "overall": {"criteria": ["Relacionar perguntas, exames solicitados, hipótese e conduta efetivamente registrados.", "Não atribuir ao aluno ações ou interpretações que não constam da consulta, nem penalizar informações ausentes na referência."], "criticalActions": []},
    }
    for section in rubric.values():
        if section.get("assessable") is False:
            section["reason"] = "A preparação local não identificou dados suficientes nesta dimensão."
    case = {
        "caseId": package["caseId"], "caseTitle": "Encontro clínico " + origin["pmcid"],
        "specialty": specialty, "difficulty": "Intermediário", "caseType": _presentation(patient_snippets, age, age_unit),
        "summary": "Encontro com dados de relato publicado, preparado localmente e com revisão clínica pendente.",
        "catalogType": "published_case", "simulationOnly": True, "reviewStatus": "review_required",
        "origin": "Preparação local de relato publicado", "reviewNote": REVIEW_NOTE,
        "patientProfile": profile,
        "mustConsultionItems": consultation_items, "optionalConsultionItems": [],
        "mustPhysicalExams": [], "optionalPhysicalExams": list(physical),
        "mustAuxiliaryLabs": [], "optionalAuxiliaryLabs": list(auxiliary),
        "physicalExams": physical, "auxiliaryLabs": auxiliary,
        "diagnoses": {"mainDiagnosis": {"disease": diagnosis}, "differentialDiagnoses": []},
        "treatments": treatments,
        "learningObjectives": ["Praticar a coleta de dados disponíveis e reconhecer lacunas documentais.",
                               "Comparar a conclusão com a referência, considerando a revisão clínica pendente."],
        "references": [{"title": origin["articleTitle"][:300], "url": origin["articleUrl"]}],
        "rubric": rubric,
    }
    patient_species = _species_metadata(package)
    if patient_species is not None:
        case["patientSpecies"] = patient_species
        if patient_species["kind"] == "nonhuman":
            profile["name"] = "Paciente animal — relato reservado ao acervo"
            case["caseType"] = "Outros sintomas"
    reasoning_units = [unit for unit in units if unit.get("kind") in {"dataset_reasoning", "factual_reasoning"}]
    if reasoning_units:
        reasoning_items = {item.get("translationUnitId"): item for item in package.get("rationale", {}).get("items", [])}
        values = []
        for unit in reasoning_units:
            value = translated[unit["id"]]
            if unit["kind"] == "factual_reasoning":
                item = reasoning_items.get(unit["id"], {})
                status = ("Hipótese descrita como excluída no raciocínio do conjunto de dados"
                          if item.get("assessmentCue") == "excluded_in_dataset_reasoning"
                          else "Hipótese considerada no raciocínio do conjunto de dados")
                value = status + ": " + value + ". Contexto clínico e conclusão ainda não revisados."
            values.append(value)
        case["referenceReasoning"] = _joined(values, 10000)
    origin.update(adaptedAt=_now(), adaptedBy="Extrator determinístico e tradutor local; revisão clínica pendente",
        language="pt-BR", clinicalMoment="Apresentação selecionada por regras locais. Cronologia e vínculo individual aguardam revisão clínica.",
        changes=["Extração por regras locais e tradução automática identificada; sem solicitação à DeepSeek.",
                 "Diagnóstico de referência conserva o rótulo do conjunto de dados; não equivale a confirmação clínica independente.",
                 "Achados e conduta descrevem o relato histórico; não constituem recomendações médicas validadas.",
                 "Campos não identificados permanecem desconhecidos. A ficha pode conter dados incompletos ou classificação a corrigir."],
        missingFacts=_missing(case), evidence=[],
        audit={"method": "not_performed", "reason": "Preparação local; revisão clínica e auditoria semântica ainda não realizadas."},
        localPreparation={"version": VERSION, "preparationMethod": "local_structured_translation",
            "sourceSha256": origin["xmlSha256"], "extractorVersion": package["extractorVersion"],
            "packageSha256": _hash(package), "model": translation_metadata.get("model"),
            "modelRevision": translation_metadata.get("revision", translation_metadata.get("modelRevision")),
            "translated": True, "translatedUnits": len(units), "clinicalReviewDeferred": True})
    if factual:
        origin.update(sourceFacts=[], factualEvidence=[])
        origin["changes"].append("Fonte ND: somente termos e valores em templates próprios; nenhuma tradução narrativa do artigo.")
    if not package.get("specialty", {}).get("specialty"):
        origin["changes"].append("Clínica médica usada como categoria provisória de organização; especialidade não identificada pelo extrator.")
    if neutral_exam_names:
        origin["changes"].append("Rótulos não padronizados de exames receberam nomes neutros em português; sujeito e achado traduzidos foram preservados somente no resultado solicitado.")
    if patient_species is not None:
        origin["changes"].append("Espécie preservada da classificação por sujeito explícito; trechos originais e posições permanecem no pacote privado, vinculados por hash.")
        if patient_species["kind"] == "nonhuman":
            origin["changes"].append("Relato de animal mantido no acervo privado; autorização de revisão posterior não o converte em caso clínico humano.")
    if reasoning_units:
        origin["changes"].append("Raciocínio de referência derivado do campo diagnostic_reasoning do conjunto de dados; aguarda verificação clínica independente.")
    if age is not None and (age_unit != "anos" or age < 3) and not respondent:
        origin["changes"].append("Paciente muito jovem: o extrator não identificou informante explícito. O papel de quem responde requer revisão; acompanhante não foi inventado.")
    case["provenance"] = origin
    data = validate_case(case)
    if accepted_for_use and not is_nonhuman_case(data):
        return authorize_provisional_use(data, authorization_reference)
    return data
