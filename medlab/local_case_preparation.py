"""Private, deterministic preparation of source material for a local translator.

No network, language model, database, or clinical approval lives here. Dataset
summaries are locators, not original evidence. Exact source spans and heuristic
matches remain distinguishable. Output is an intermediate package, not a case
validated by ``case_schema.validate_case``. In particular it never fabricates
provenance.audit flags or claims that English text has been translated.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import unicodedata
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timezone

from .catalog import SPECIALTIES

SCHEMA_VERSION = "local-sentence-extraction-v1"
EXTRACTOR_VERSION = "2026-10-15.4"
GROUPS = ("symptoms", "history", "physical_exams", "auxiliary_exams", "diagnosis", "treatment", "unclassified")
GROUP_LABELS = {
    "symptoms": "Sintomas e apresentação", "history": "Antecedentes e exposições",
    "physical_exams": "Exame físico", "auxiliary_exams": "Exames complementares",
    "diagnosis": "Diagnóstico e confirmação", "treatment": "Conduta histórica",
    "unclassified": "Dados ainda sem categoria determinada",
}

# Rules label candidate sentences; a keyword match is NOT a medical assertion.
_PATTERNS = {
    "symptoms": r"\b(?:present(?:s|ed|ing|ation)?|complain\w*|symptom\w*|asymptomatic|pain|ache|fever|fatigue|weakness|dyspn[oe]+a|cough|vomit\w*|nausea|diarrh[oe]+a|constipat\w*|dizz\w*|headaches?|swelling|rash|pruritus|itch\w*|bleed\w*|discharge|seizure\w*|syncope|weight loss|anorexia|polyuria|polydipsia|dysuria|hematuria|haematuria|numbness|tingling|blurred vision|palpitation\w*|falls|confusion|stridor|hemoptysis|haemoptysis|mass|lump|nodule)\b",
    "history": r"\b(?:history|previous\w*|prior|familial|family|smok\w*|tobacco|alcohol|allerg\w*|vaccinat\w*|immuniz\w*|medication\w*|occupation\w*|worked|travel\w*|exposure|trauma|menstrua\w*|pregnan\w*|gravida|para|sexually|contracept\w*|comorbid\w*|taking|had been treated|past medical|past surgical)\b",
    "physical_exams": r"\b(?:physical exam\w*|on examination|clinical examination|vital signs|blood pressure|heart rate|respiratory rate|temperature|afebrile|febrile|palpat\w*|auscultat\w*|percussion|tender\w*|murmur\w*|rebound|guarding|cyanosis|pallor|edema|oedema|erythema|lymphadenopathy|range of motion|neurological examination|mental status|Glasgow|BMI|body mass index)\b",
    "auxiliary_exams": r"\b(?:laborator\w*|blood test\w*|blood count|hemoglobin|haemoglobin|leukocyt\w*|white blood|platelet\w*|creatinine|urea|glucose|glycemi\w*|glycaemi\w*|sodium|potassium|C[- ]reactive|CRP|ESR|sedimentation|serolog\w*|cultures?|polymerase|PCR|radiograph\w*|X[- ]ray\w*|computed tomography|CT|magnetic resonance|MRI|ultraso\w*|sonograph\w*|echocardiogra\w*|electrocardiogra\w*|ECG|EEG|PET|angiogra\w*|endoscop\w*|colonoscop\w*|bronchoscop\w*|biops\w*|histolog\w*|histopatholog\w*|immunohistochem\w*|patholog\w*|cytolog\w*|microscop\w*|sequenc\w*|intraoperativ\w*|operative findings)\b",
    "diagnosis": r"\b(?:diagnos\w*|confirm\w*|consistent with|compatible with|suggestive of|revealed|demonstrated|identified|showed|ruled out|excluded|differential|suspected|provisional)\b",
    "treatment": r"\b(?:treat\w*|therap\w*|administer\w*|prescrib\w*|underwent|resect\w*|excis\w*|surgery|surgical|drain\w*|discharg\w*|follow[- ]up|outcome\w*|recover\w*|improv\w*|resolved|recurr\w*|chemotherap\w*|radiotherap\w*|antibiotic\w*|analges\w*|steroid\w*|insulin|dialysis|transplant\w*)\b",
}
_RULES = {key: re.compile(pattern, re.I) for key, pattern in _PATTERNS.items()}
_HISTORY_FIELDS = (
    ("family_history", r"\bfamily|familial|mother|father|sibling|parents?\b"),
    ("allergy_history", r"\ballerg\w*\b"),
    ("other_medical_history", r"\bmedication\w*|taking|on \w+ therapy|treated with|prescribed\b"),
    ("personal_history", r"\bsmok\w*|tobacco|alcohol|occupation\w*|worked|travel\w*|exposure\b"),
    ("surgery_injury_history", r"\bsurg\w*|operat\w*|trauma|injury\b"),
    ("reproductive_history", r"\bpregnan\w*|gravida|contracept\w*|menstrua\w*\b"),
)
_EXAM_TERMS = (
    r"(?:computed tomography|(?-i:CT))(?:\s+(?:scan|angiography))?", r"(?:magnetic resonance imaging|(?-i:MRI))",
    r"(?:ultrasonography|ultrasound|sonography)", r"(?:chest |panoramic )?radiograph\w*", r"X[- ]ray\w*",
    r"(?:histopatholog\w*|histolog\w*|biopsy|biopsies)", r"immunohistochem\w*", r"intraoperative (?:findings|examination)",
    r"(?:blood|urine|sputum|bacterial|fungal) cultures?", r"polymerase chain reaction|PCR", r"DNA sequencing",
    r"white[- ]blood[- ]cell count|white blood cell count|leukocyte count", r"hemoglobin|haemoglobin|platelet count",
    r"C[- ]reactive protein|CRP|erythrocyte sedimentation rate|ESR", r"creatinine|glucose|sodium|potassium",
    r"echocardiogra\w*|electrocardiogra\w*|ECG|EEG", r"endoscop\w*|colonoscop\w*|bronchoscop\w*", r"PET(?:[- ]CT)?",
    r"blood pressure|heart rate|respiratory rate|body temperature|oxygen saturation|BMI",
    r"physical examination|palpation|auscultation|neurological examination|range of motion",
)
_EXAMS = re.compile(r"\b(?:" + "|".join("(?:" + term + ")" for term in _EXAM_TERMS) + r")\b", re.I)
# Compact clinical concepts for the factual route; no full narrative translation.
_CONCEPTS = re.compile(
    r"\b(?:abdominal pain|chest pain|back pain|headache|pain|fever|fatigue|weakness|dyspn[oe]+a|cough|nausea|"
    r"vomiting|diarrh[oe]+a|constipation|swelling|rash|pruritus|bleeding|discharge|seizures?|syncope|weight loss|"
    r"anorexia|polyuria|polydipsia|dysuria|h[ae]+maturia|numbness|tingling|blurred vision|palpitations?|headaches?|mass|lump|nodules?|falls|confusion|asymptomatic|stridor|hemoptysis|haemoptysis|"
    r"hypertension|diabetes(?: mellitus)?|hypothyroidism|hyperthyroidism|asthma|hepatitis [ABC]|HIV|tuberculosis|"
    r"smoking|alcohol|allergies|allergy|trauma|surgery|appendectomy|appendicectomy|pregnancy|"
    r"edema|oedema|erythema|tenderness|murmur|pallor|cyanosis|necrosis|abscess|perforation|torsion|"
    r"resection|excision|drainage|chemotherapy|radiotherapy|antibiotics?|analgesics?|corticosteroids?|"
    r"prednisone|prednisolone|insulin|metformin|levothyroxine|captopril|dialysis|transplantation)\b", re.I)
_MEASUREMENT = re.compile(r"(?<!\w)(?:[<>≤≥]\s*)?\d+(?:[.,]\d+)*(?:\s*[×x–-]\s*\d+(?:[.,]\d+)*)?\s*(?:mg/dL|mmol/L|mg/L|g/dL|ng/mL|pg/mL|IU/L|U/L|mIU/L|mEq/L|cells?/mm3|cell/mm3|/mm3|mmHg|mm/h|mm/hr|bpm|beats/min|breaths/min|°C|°F|cm|mm|kg|mL|L/day|L/d|%)(?!\w)", re.I)
_AGE = re.compile(r"\b(\d{1,3}(?:\.\d+)?)\s*[-‐‑– ]?(year|month|week|day)s?\s*[-‐‑– ]?old\b", re.I)
_AGE_BAND = re.compile(r"\b(?:early|mid|late)\s*[- ]?\d0s\b|\b(?:second|third|fourth|fifth|sixth|seventh|eighth|ninth) decade\b", re.I)
_NEGATION = re.compile(r"\b(?:no|not|without|denied|denies|negative for|absence of|absent)\b", re.I)
_PATIENT_ID = re.compile(r"\b(?:patient|case)\s*(?:no\.?\s*)?([1-9]|[IVX]{1,3})\b", re.I)
_MULTI = re.compile(r"\b(?:two|three|four|both|2|3|4)\s+(?:(?:healthy|young|elderly|adult|male|female)\s+){0,2}(?:patients|cases|siblings|sisters|brothers)\b", re.I)
_SHARED = re.compile(r"\b(?:both (?:patients|cases|of|were|had)|they both|for both|in both|two (?:patients|siblings|sisters))\b", re.I)
_CASE_HEADING = re.compile(r"case\s*(?:report|presentation|description|history|series|study|[1-9])|clinical\s*(?:report|presentation|case|observation)|patient\s*[1-9]|report of|observations?", re.I)
_EDITORIAL_HEADING = re.compile(r"discussion|introduction|background|conclusion|literature|references|acknowledg|consent|author|funding|conflict", re.I)
_CONFIRMATION = re.compile(r"\b(?:biops\w*|histolog\w*|histopatholog\w*|immunohistochem\w*|culture\w*|sequenc\w*|confirmed|final diagnosis|intraoperativ\w*|operative findings|patholog\w*)\b", re.I)
_POST_EVENT = re.compile(r"\b(?:postoperativ\w*|after (?:surgery|operation|treatment)|follow[- ]up|discharg\w*|recurr\w*|subsequently|later|underwent|was treated|were treated)\b", re.I)
_PREVIOUS = re.compile(r"\b(?:previous\w*|prior|before|past|history of|had been|was taking|were taking)\b", re.I)
_DURATION = re.compile(r"\b(?:\d+(?:\.\d+)?|one|two|three|four|five|six|seven|eight|nine|ten|several)\s*[-‐‑– ]?(?:hours?|days?|weeks?|months?|years?)\b(?!\s*[-‐‑–]?\s*old\b)", re.I)
_ANATOMY = re.compile(r"\b(?:(?:right|left|bilateral|upper|lower|anterior|posterior)\s+){0,2}(?:abdomen|abdominal|chest|back|neck|head|face|facial|eye|eyes|ear|ears|leg|legs|arm|arms|hand|hands|foot|feet|breast|breasts|palate|penis|scrotum|testis|testicle|vagina|pelvis|pelvic|iliac fossa|quadrant|flank|groin|perineal|buttock|shoulder|elbow|wrist|hip|knee|ankle)\b", re.I)
_STOP = frozenset("a an and are as at be been but by for from had has have he her his i in is it its of on or our she that the their them they this to was were with without we which who not no case patient patients showed revealed performed results examination normal history clinical diagnosis diagnosed treatment".split())
_TECHNICAL_START = re.compile(
    r"(?i)(?:(?<=;)\s*|(?<=,)\s*(?:and\s+)?|\s+(?=on\s+(?:physical\s+)?examination\b)|\n\s*)"
    r"(?=(?:on\s+(?:physical\s+)?examination|physical examination|laboratory (?:studies|tests|results|evaluation)|"
    r"blood (?:tests|studies)|(?:a |an |the )?(?:CT|MRI|ultrasound|radiograph)|"
    r"histopatholog\w* (?:showed|revealed)|(?:his|her|the patient's) (?:blood pressure|heart rate))\b)")
_TECHNICAL_PHYSICAL = re.compile(r"\b(?:on examination|physical examination|clinical examination|palpat\w*|auscultat\w*|percussion|vital signs|blood pressure|heart rate|respiratory rate|oxygen saturation|SpO2|SpO₂|SaO2|SaO₂|hypotension|hypotensive|range of motion|neurological examination|Glasgow|BMI)\b", re.I)
_NEW_DIAGNOSIS = re.compile(r"\b(?:was diagnosed|were diagnosed|diagnosis (?:of|was)|suspected|suggestive of|compatible with|confirmed|histopatholog\w*|cytogenetic|myelogram|bone marrow|imaging|karyotype|blasts|hypercellularity)\b", re.I)
_NEW_TREATMENT = re.compile(r"\b(?:underwent|was administered|were administered|was prescribed|were prescribed|was treated|were treated|discharged|postoperativ\w*|follow[- ]up|recovered)\b", re.I)

# Transparent lexical classification only. Unmatched records remain unclassified;
# no mass fallback to Clínica médica and no unsupported claim of 55-way coverage.
_SPECIALTY_RULES = {
    "Cardiologia": "cardiac cardiology myocard infarct arrhythm coronary cardiomyopath endocardit pericard atrial ventricular aorticvalve",
    "Cirurgia cardiovascular": "cardiac surgery coronary bypass valvereplacement",
    "Cirurgia da mão": "hand surgery finger tendon handtrauma",
    "Cirurgia de cabeça e pescoço": "head neck parotid salivary mandib oral palate maxillofacial",
    "Cirurgia do aparelho digestivo": "digestive surgery pancreatic biliary cholecyst appendic bowel gastric perforation hernia",
    "Cirurgia geral": "surgery surgical abscess hernia volvulus intussusception",
    "Cirurgia oncológica": "surgical oncology resection carcinoma",
    "Cirurgia pediátrica": "pediatric surgery congenital intestinal",
    "Cirurgia plástica": "plastic reconstructive cosmetic burn fatinjection",
    "Cirurgia torácica": "thoracic surgery mediastinal pneumothorax",
    "Cirurgia vascular": "vascular surgery aneurysm dissection ischemia thrombosis",
    "Clínica médica": "internal medicine multisystem general medicine",
    "Coloproctologia": "colorectal rectal rectum anal fistula colon",
    "Dermatologia": "dermat dermatology cutaneous skin rash pemphigus psoriasis lichen melanoma",
    "Endocrinologia e metabologia": "endocrin thyroid pituitary adrenal diabetes insulin parathyroid hypercalcem hypocalcem",
    "Endoscopia": "endoscopy endoscopic colonoscopy gastroscopy",
    "Gastroenterologia": "gastroenter hepat liver cirrhos cholang pancreat esophag stomach bowel crohn colitis",
    "Genética médica": "genetic genomic inherited chromosom mutation congenital syndrome",
    "Geriatria": "geriatric gerontology frailty",
    "Ginecologia e obstetrícia": "gynecol gynaecol obstetric ovarian ovary uterine uterus pregnancy pregnant pelvic ectopic placenta endometriosis",
    "Hematologia e hemoterapia": "hematol haematol leukemia leukaemia lymphoma myeloma anemia anaemia hemophil haemophil thrombocytopen",
    "Infectologia": "infect mycobacter tuberculosis bacterial viral fungal sepsis malaria leishmania cryptococc candida brucell hiv",
    "Mastologia": "breast mammary mastitis",
    "Medicina de emergência": "emergency emergencies poisoning intoxication anaphylaxis",
    "Medicina de família e comunidade": "family medicine primary care community medicine",
    "Medicina do trabalho": "occupational workplace industrial exposure",
    "Medicina esportiva": "sports athlete athletic exercise injury",
    "Medicina física e reabilitação": "rehabilitation physiatry physiotherapy",
    "Medicina intensiva": "intensive critical care shock mechanical ventilation",
    "Medicina nuclear": "nuclear scintigraphy radionuclide",
    "Nefrologia": "nephrol renal kidney glomerul dialysis nephrotic nephritic",
    "Neurocirurgia": "neurosurg glioma meningioma subdural epidural hydrocephal",
    "Neurologia": "neurol encephal seizure epilepsy stroke neuropath myelitis demyelin parkinson multiple sclerosis",
    "Nutrologia": "nutrition nutritional malnutrition vitamin deficiency",
    "Oftalmologia": "ophthalm retinal retina eye ocular glaucoma optic cataract uveitis",
    "Oncologia clínica": "oncolog cancer carcinoma metastatic metastasis neoplasm tumor tumour sarcoma",
    "Ortopedia e traumatologia": "orthop fracture bone osteo joint arthritis musculoskeletal femur tibia humerus ligament",
    "Otorrinolaringologia": "otolaryng rhinolog ear nasal sinus larynx tonsil hearing vestibular",
    "Patologia": "pathology histopathology",
    "Patologia clínica/medicina laboratorial": "laboratory medicine clinical pathology",
    "Pediatria": "pediatric paediatric newborn neonatal infant childhood child",
    "Pneumologia": "pulmon respiratory lung bronch asthma pneumonia pleural pneumothorax",
    "Psiquiatria": "psychiatr psychosis schizophrenia depression bipolar catatonia mental",
    "Radiologia e diagnóstico por imagem": "radiolog imaging radiography",
    "Radioterapia": "radiotherapy radiation oncology",
    "Reumatologia": "rheumat lupus scleroderma sclerosis vasculitis sjogren dermatomyositis polymyositis",
    "Urologia": "urolog prostate bladder ureter urethra testicular testis scrotal penis urinary",
    "Alergia e imunologia": "allerg immunolog immunodeficiency hypersensitivity",
    "Anestesiologia": "anesthes anaesthes anesthesia anaesthesia",
    "Angiologia": "angiology angiolog venous lymphatic",
    "Medicina legal e perícia médica": "forensic medico legal",
    "Medicina preventiva e social": "preventive public health epidemiology",
}


def _sha(value: str | bytes) -> str:
    return hashlib.sha256(value.encode("utf-8") if isinstance(value, str) else value).hexdigest()


def _normalized(value):
    value = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", str(value))
    return " ".join(re.findall(r"[a-z0-9]+", unicodedata.normalize("NFKD", value).casefold()))


def _tokens(value):
    return {word for word in _normalized(value).split() if len(word) > 2 and word not in _STOP}


def _sentences(text):
    """Keep exact offsets and avoid splitting decimal values or common abbreviations."""
    start = 0
    for match in re.finditer(r"[.!?](?:[\"')\]]*)\s+(?=[A-Z0-9])|\n\s*\n", text):
        left = text[max(start, match.start() - 12):match.start() + 1]
        if re.search(r"\b(?:Dr|Mr|Mrs|Ms|Fig|Figs|No|vs|et al|e\.g|i\.e)\.$", left):
            continue
        end = match.start() + 1 if text[match.start()] in ".!?" else match.start()
        first, last = start, end
        while first < last and text[first].isspace(): first += 1
        while last > first and text[last - 1].isspace(): last -= 1
        if last > first:
            yield first, last, text[first:last]
        start = match.end()
    first, last = start, len(text)
    while first < last and text[first].isspace(): first += 1
    while last > first and text[last - 1].isspace(): last -= 1
    if last > first:
        yield first, last, text[first:last]


def _classify(text):
    hits = {key: list(dict.fromkeys(match.group() for match in rule.finditer(text))) for key, rule in _RULES.items()}
    if hits.get('treatment') and _EXAMS.search(text) and all(word.casefold() == 'underwent' for word in hits['treatment']):
        hits['treatment'] = []  # Undergoing an MRI is an investigation, not therapy.
    groups = [key for key, terms in hits.items() if terms]
    return groups or ["unclassified"], {key: value for key, value in hits.items() if value}


def _presentation_clauses(text):
    """Split mixed sentences at explicit technical transitions, retaining offsets."""
    for start, end, sentence in _sentences(text):
        cuts = [0] + [m.end() for m in _TECHNICAL_START.finditer(sentence)]
        cuts += [m.start() for m in re.finditer(
            r'[,;]\s*(?:but |and |although )?(?=(?:his |her |the )?(?:stool|blood|urine|serum)\s+(?:studies|tests|cultures|results))|'
            r'[,;]\s*(?:and |but )?(?=(?:his |her |the )?(?:oxygen saturation|SpO2|SpO₂|blood pressure|heart rate|hypotension))', sentence, re.I)]
        # A documented technical past history must not swallow a separately
        # stated new complaint. Each output remains an exact substring.
        cuts += [m.start() for m in re.finditer(r'\b(?:presented|presents|presenting|complained|complains)\s+(?:with|of)\b', sentence, re.I) if m.start() > 20]
        # Preserve an explicit initial demographic/history clause before an
        # investigation/referral when both were written in a single sentence.
        cuts += [m.start() for m in re.finditer(r'\b(?:was|were)\s+referred\b|\bunderwent\b', sentence, re.I)
                 if m.start() > 20 and re.search(r'\b(?:history|gravida|para|exposure|pregnancy)\b', sentence[:m.start()], re.I)]
        cuts = sorted(set(cuts + [len(sentence)]))
        for first, last in zip(cuts, cuts[1:]):
            while first < last and sentence[first].isspace(): first += 1
            while last > first and sentence[last - 1].isspace(): last -= 1
            if last > first:
                yield start + first, start + last, sentence[first:last]


def _reasoning_items(text, factual):
    """Keep dataset reasoning distinct from the confirmed diagnosis and article.

    The factual route extracts diagnostic labels from text outside quotations;
    quoted article narrative is never returned as a translation unit.
    """
    result = []
    for item in re.split(r"(?:^|\n)\s*\d+[.)]\s+", text):
        item = item.strip()
        if not item: continue
        if not factual:
            result.append({'text': item, 'kind': 'dataset_reasoning', 'origin': 'dataset_diagnostic_reasoning'})
            continue
        unquoted = re.sub(r'“[^”]*”|"[^"]*"|‘[^’]*’', '', item, flags=re.S).strip()
        # In malformed quotes, discard everything after the opening character.
        unquoted = re.split(r'[“"]', unquoted, maxsplit=1)[0].strip()
        unquoted = re.sub(r'^\d+[.)]\s*', '', unquoted)
        label = re.split(r'\s+[—–]\s+|\s+-\s+|\b(?:was considered|were considered|considered|was suspected|were suspected|because|given|due to|supported by|excluded by|ruled out by)\b', unquoted, maxsplit=1, flags=re.I)[0]
        label = label.strip(' \n\t:;,.—–-')
        if not label or len(label) > 200 or len(label.split()) > 24:
            continue
        status = 'excluded_in_dataset_reasoning' if re.search(r'\b(?:excluded|ruled out)\b', unquoted, re.I) else 'considered_in_dataset_reasoning'
        result.append({'text': label, 'kind': 'factual_reasoning', 'origin': 'dataset_diagnostic_reasoning_outside_quotes',
                       'assessmentCue': status, 'sourceQuotesRemoved': True, 'confirmedFinalDiagnosis': False})
    return result


def _patient_candidate(text, final_diagnosis):
    if text.rstrip().endswith('?') or re.search(r"\b(?:most likely diagnosis|what is the diagnosis)\b", text, re.I):
        return False
    if final_diagnosis and _normalized(final_diagnosis) in _normalized(text):
        return False
    # Weak descriptive words (erythema, swelling, recurrent) alone are not
    # evidence that the sentence contains a professional examination/result.
    if _RULES['auxiliary_exams'].search(text) or _TECHNICAL_PHYSICAL.search(text) or _NEW_DIAGNOSIS.search(text):
        return False
    if _NEW_TREATMENT.search(text) and not _PREVIOUS.search(text):
        return False
    return bool(_RULES['symptoms'].search(text) or _RULES['history'].search(text) or _AGE.search(text))


def _recovery_clauses(text):
    """Recover exact presentation clauses hidden inside mixed clinical sentences.

    Used only when the ordinary extraction found no patient-facing material.
    Separators never concatenate words or remove an internal negative qualifier.
    """
    for start, end, sentence in _presentation_clauses(text):
        cuts = [0, len(sentence)]
        cuts += [m.end() for m in re.finditer(r";\s*|\n\s*[•–-]?\s*", sentence)]
        cuts += [m.start() for m in re.finditer(
            r"\b(?:presented|presents|presenting|complained|complains)\b|"
            r"\b(?:and |but )?(?:was|were|is|are)\s+(?:diagnosed|noted|found)\b|"
            r"\b(?:after |before |during |when |which |that )(?=(?:a |an |the |routine |screening |follow-up )?(?:ultrasound|ultrasonography|CT|MRI|imaging|radiograph))|"
            r"\b(?:and |but )?(?:her|his|their|the patient's)\s+(?:blood|serum|hemoglobin|haemoglobin|platelet)|"
            r"\b(?:underwent|undergoing|received)\b", sentence, re.I) if m.start() > 16]
        cuts += [m.start() for m in re.finditer(
            r',\s*(?:and |but )?(?=(?:he|she|the patient|the infant)\s+(?:was started|was treated|received|required))|'
            r'\s+(?=but (?:had|showed) no clinical signs)|'
            r'\s+(?=requiring (?:endotracheal|mechanical|CPAP))|'
            r'\s+(?=with (?:associated )?(?:desaturation|SpO|oxygen saturation))', sentence, re.I)]
        # A parenthetic BMI/laboratory value does not invalidate a separate
        # history or complaint before/after it; both remain original substrings.
        for match in re.finditer(r"\([^)]*(?:BMI|blood pressure|HbA1c|hemoglobin|platelet count)[^)]*\)", sentence, re.I):
            cuts.extend((match.start(), match.end()))
        cuts = sorted(set(cuts))
        for first, last in zip(cuts, cuts[1:]):
            while first < last and (sentence[first].isspace() or sentence[first] in '•–;'): first += 1
            while last > first and sentence[last - 1].isspace(): last -= 1
            if last > first:
                yield start + first, start + last, sentence[first:last]


def _recovery_candidate(text, final_diagnosis):
    """Conservative presentation/history recovery; no examination knowledge."""
    if text.rstrip().endswith('?') or (final_diagnosis and _normalized(final_diagnosis) in _normalized(text)):
        return False
    if _RULES['auxiliary_exams'].search(text) or _TECHNICAL_PHYSICAL.search(text):
        return False
    if re.search(r'\b(?:examination|exam|scan|mammogra\w*|echocardiogra\w*|TTE|TEE|ECG|EKG|labs?|'
                 r'investigations?|workup|work.up|serum|toxicology|biochemistry|Apgar|GCS|'
                 r'FiO2|SpO2|SpO₂|saturation|hemodynamic\w*|intraoperativ\w*|'
                 r'paracentesis|PVBs|ventriculoatrial|symblepharon|foramen|ductus|'
                 r'not interpreted|hyperechoic|retinal nerve|optic coherence|optical coherence|'
                 r'central venous|bil irubin|bilirubin|neutropenia|pulmonary hypertension|'
                 r'encephalitis|hydrocephalus|intracranial hypertension|peristalsis|cervical effacement|'
                 r'CTCAE|MELD|Child.Pugh|valve area|cardiopulmonary|intubat\w*|tracheostomy|'
                 r'shunt|arterioarterial|venovenous|catheter ablation|electrophysiology)\b', text, re.I):
        return False
    # An explicitly known unrelated condition is historical patient knowledge.
    detection = re.sub(r'\b(?:known|previous|prior) diagnosis of\b', 'known condition', text, flags=re.I)
    if _NEW_DIAGNOSIS.search(detection):
        return False
    if re.search(r'\b(?:revealed|showed|demonstrated|identified|detected|consistent with|interpreted as|presumed|'
                 r'could not be assessed|noted on|noted in|were noted|was noted|treated with|managed with|'
                 r'were normal|was normal|negative|were present|were absent|required|requiring|'
                 r'completed|started|initiated|drained|resected|performed|attributed to|'
                 r'ruled out|newly diagnosed|concern for|differential)\b', detection, re.I):
        return False
    if re.search(r'\b(?:underwent|was administered|were administered|was prescribed|were prescribed|was treated|were treated|discharged|recovered)\b', text, re.I):
        return False
    if re.search(r'\bdeveloped\b', text, re.I) and not re.search(
            r'\b(?:pain|ache|fever|cough|fatigue|weakness|dyspn\w*|vomit\w*|nausea|diarrh\w*|'
            r'constipation|distension|distention|bloating|bleed\w*|melena|hematochezia|'
            r'hemoptysis|haemoptysis|discomfort|confusion|tremor|seizure|jerking|itch\w*|'
            r'pruritus|swelling|rash|papules|purpura|polyuria|dysphagia|stridor|tachypnea|'
            r'respiratory distress|breathing|dizziness|vision|ptosis|feeding|jaundice|somnolence|'
            r'asthenia|numbness|tingling|lesions|loss|inflammation|vigilance)\b', text, re.I):
        return False
    subjective = re.search(
        r'\b(?:he|she|patient|infant|child|boy|girl|woman|man)\s+(?:had |has |was |were |also |then |first |never )?'
        r'(?:complain\w*|reported|noted|developed|experienced|present\w*|admitted|brought|referred|'
        r'found unresponsive|born|delivered|maintained on)|'
        r'^(?:present\w*|complain\w*)\b|\b(?:medical history|family history|past history|'
        r'collateral history|medications on presentation|no complaints|no symptoms|never received)\b|'
        r'^(?:A |An |The )?(?:male |female |term |full-term |preterm )?(?:infant|neonate)\b.*\b(?:born|delivered)\b', text, re.I)
    if not subjective and not _RULES['history'].search(text):
        # A stated comorbidity in the first demographic clause is still history.
        return bool(_AGE.search(text) and re.search(r'\bwith\b.+\w', text, re.I))
    return bool(subjective or _RULES['history'].search(text))


def _patient_species(prompt):
    """Identify the described subject, never an exposure or research animal.

    Literal evidence stays private. Unknown wording does not become human by
    default; a breed is retained in the evidence alongside its species label.
    """
    first = next(_sentences(prompt), (0, 0, ''))
    start, end, text = first
    head = re.split(r'\b(?:was|were|is|are|presented|presents|had|has|with|after|who)\b', text, maxsplit=1, flags=re.I)[0]
    evidence = [{'field': 'case_prompt', 'start': start, 'end': end,
                 'text': text, 'textSha256': _sha(text)}] if text else []
    human = re.search(r'\b(?:man|men|woman|women|boy|girl|infant|neonate|child|children|'
                      r'primigravida|gravida|patient|human|worker|farmer|student|'
                      r'teacher|breeder|veterinarian|athlete|runner)\b', head, re.I)
    if human:
        return {'kind': 'human', 'species': 'human', 'evidence': evidence, 'method': 'explicit_presenting_subject'}
    labels = (
        ('cat', r'cats?|kitten|feline|British Shorthair|Maine Coon|Ragdoll'),
        ('dog', r'dogs?|puppy|canine|terrier|retriever|shepherd|sheepdog|Boxer|Blue Heeler|Bichon Frise|Havanese|Poodle|Bulldog|Irish Setter|Pointer|Doberman Pinscher|Husky|Alaskan Malamute|Tsvetnaya Bolonka|Great Dane|whippet|Shar.Pei|Spaniel|Dachshund|Rottweiler|Collie'),
        ('llama', r'llama'), ('alpaca', r'alpaca'),
        ('horse', r'horse|mare|stallion|gelding|pony'),
        ('cattle', r'cow|calf|bovine|heifer|bull'), ('sheep', r'sheep|ewes?|Texel'),
        ('pig', r'sow|piglet|piglets|porcine'),
        ('goat', r'goats?|alpine wether'), ('rabbit', r'rabbits?|leporine'),
        ('pangolin', r'pangolin'), ('ferret', r'ferrets?'),
        ('guinea pig', r'guinea pig'), ('hamster', r'hamster'),
        ('rat', r'rat|Sprague.Dawley'),
        ('nonhuman primate', r'macaque|chimpanzee|monkey'),
        ('bird', r'parrot|cockatoo|parakeet|macaw|chickens?|broilers?|poultry'), ('reptile', r'tortoise|turtle|iguana|bearded dragon'),
    )
    for label, pattern in labels:
        if re.search(r'\b(?:' + pattern + r')\b', head, re.I):
            return {'kind': 'nonhuman', 'species': label, 'evidence': evidence, 'method': 'explicit_presenting_subject'}
    # Humans described only as age/sex remain undetermined rather than silently
    # classifying a veterinary breed absent from the bounded vocabulary.
    return {'kind': 'undetermined', 'species': None, 'evidence': [], 'method': 'no_explicit_species_detected'}


def _xml_text(node):
    return " ".join(" ".join(node.itertext()).split())


def _article_paragraphs(article, xml_bytes=None):
    """Prefer JATS paragraph boundaries; all emitted spans match the normalized source."""
    paragraphs = []
    if xml_bytes:
        root = ET.fromstring(xml_bytes)
        body = root.find("body")
        if body is not None:
            body_text = _xml_text(body)
            body_offset = article.find(body_text)
            def visit(node, sections):
                if node.tag == "sec":
                    title = node.find("title")
                    sections = sections + ([_xml_text(title)] if title is not None else [])
                if node.tag == "p":
                    value = _xml_text(node)
                    offset = article.find(value, max(body_offset, 0)) if value else -1
                    if offset >= 0:
                        paragraphs.append({"text": value, "start": offset, "end": offset + len(value), "sections": sections})
                    return
                if node.tag in {"fig", "table-wrap", "ref-list", "ack"}:
                    return
                for child in node:
                    visit(child, sections)
            visit(body, [])
    if not paragraphs:
        # No guessed paragraph boundaries. Sentence candidates retain original offsets.
        paragraphs = [{"text": text, "start": start, "end": end, "sections": []} for start, end, text in _sentences(article)]
    return paragraphs


def _specialty(record):
    sources = ((record.get("final_diagnosis", ""), 4), (record.get("journal", ""), 3), (record.get("case_prompt", ""), 1))
    scored = []
    for name, words in _SPECIALTY_RULES.items():
        score, matched = 0, set()
        for text, weight in sources:
            text = _normalized(text)
            for word in words.split():
                if word in {'medicine', 'clinical', 'surgery', 'surgical', 'care', 'head', 'neck', 'hand', 'general', 'primary', 'critical', 'internal', 'public', 'health'}:
                    continue
                if re.search(r"\b" + re.escape(word) + r"[a-z]*\b", text):
                    score += weight; matched.add(word)
        if score:
            scored.append({"specialty": name, "score": score, "terms": sorted(matched)})
    scored.sort(key=lambda item: (-item["score"], item["specialty"]))
    return {"method": "deterministic_lexical_candidates", "specialty": scored[0]["specialty"] if scored else None,
            "candidates": scored[:5], "taxonomySize": len(SPECIALTIES), "clinicallyValidated": False}


def _subject(text, inherited=None):
    ids = list(dict.fromkeys("P" + m.group(1).upper() for m in _PATIENT_ID.finditer(text)))
    if len(ids) > 1:
        return "mixed", ids
    if _SHARED.search(text):
        return "shared", ids
    return (ids[0] if ids else inherited), ids


def _relation_descriptors(snippet):
    """Extract explicit clinical predicate/value relations for original templates.

    Values are bounded clinical constituents, not complete narrative sentences.
    No measurement is assigned to the nearest test and no absent item is added.
    Ambiguous prose remains in the private snippet rather than being resolved by
    guesswork. Patient identity/clinical timing are inherited as unresolved flags.
    """
    text = snippet['text']
    output, seen = [], set()
    def add(relation, subject, value, start, *, negated=False):
        # A list under one explicitly named observation shares that observation;
        # commas are retained because they may be decimal/thousands separators.
        parts = list(re.finditer(r'[^;\n•]+', value))
        for part in parts:
            raw = part.group()
            clean = raw.strip(' \t:;.')
            offset = raw.find(clean)
            if not clean or len(clean) > 600 or len(clean.split()) > 85:
                continue
            if re.search(r'\b(?:we believe|we recommend|this suggests|the literature|should be considered)\b', clean, re.I):
                continue
            explicit_negative = re.match(r'(?i)(no|without|absence of|negative for)\s+', clean)
            negative = negated or bool(explicit_negative)
            if explicit_negative:
                offset += explicit_negative.end(); clean = clean[explicit_negative.end():]
            key = (relation, subject, clean, negative)
            if key in seen: continue
            seen.add(key)
            begin = start + part.start() + offset
            output.append({'kind': 'relation', 'relation': relation, 'subjectOriginal': subject,
                           'valueOriginal': clean, 'negated': negative, 'snippetId': snippet['id'],
                           'textLocation': {'start': begin, 'end': begin + len(clean)},
                           'patientRef': snippet['patientRef'], 'temporal': snippet['temporal'],
                           'sourceKind': snippet['sourceKind'], 'support': snippet['support'],
                           'assertionVerified': False, 'binding': 'explicit_predicate_same_clause'})

    symptom = re.search(r'\b(?:presented|presents|presenting|complained|complains|experienced|experiences|reported|reports|developed|noticed|noted)\s+(?:(?:with|of|from)\s+)?(.+)', text, re.I | re.S)
    if symptom and snippet['patientCandidate']:
        value, begin = symptom.group(1), symptom.start(1)
        referral = re.match(r'to\s+[^.;]+?\s+with\s+', value, re.I)
        if referral: begin += referral.end(); value = value[referral.end():]
        add('reported_symptom', 'patient', value, begin)
    denied = re.search(r'\b(?:denied|denies)\s+(?:any\s+)?(.+)', text, re.I | re.S)
    if denied and snippet['patientCandidate']:
        add('history' if 'history' in snippet['groups'] else 'reported_symptom', 'patient', denied.group(1), denied.start(1), negated=True)
    if re.search(r'\b(?:was|is|were|remained)\s+asymptomatic\b', text, re.I) and snippet['patientCandidate']:
        match = re.search(r'\basymptomatic\b', text, re.I)
        add('reported_symptom', 'patient', match.group(), match.start())

    history = re.search(r'\b((?:(?:medical|surgical|family|social|medication|obstetric)\s+)?history)\s+(?:(?:was\s+(?:notable|significant|remarkable)\s+for)|of|included|includes|was|is|revealed)\s+(.+)', text, re.I | re.S)
    if history:
        # Stop at an explicit current complaint; it has a separate relation.
        value = re.split(r';|\n|\b(?:presented|presents|presenting)\s+with\b', history.group(2), maxsplit=1, flags=re.I)[0].strip(' ,')
        prefix = text[:history.start()]
        duration_history = bool(re.search(r'(?:hour|day|week|month|year)s?\s*[-‐‑–]?\s*$', prefix, re.I))
        if not duration_history:
            add('history', history.group(1), value, history.start(2))
    no_history = re.search(r'\b(?:no|without)\s+([^.;,]*\b(?:history|exposure)\b[^.;,]*)', text, re.I)
    if no_history:
        add('history', 'patient', no_history.group(1), no_history.start(1), negated=True)

    observed = re.search(r'\b(?:was|were)\s+found\s+to\s+have\s+(.+)', text, re.I | re.S)
    if observed and any(key in snippet['groups'] for key in ('symptoms', 'physical_exams')):
        add('physical_finding', 'documented observation', observed.group(1), observed.start(1))

    examination = re.search(r'\b(?:showed|revealed|demonstrated|confirmed|identified|yielded)\s*:?[ \t\n]*(.+)', text, re.I | re.S)
    if examination:
        prefix = text[:examination.start()]
        exams = list(_EXAMS.finditer(prefix))
        subject = exams[-1].group() if exams else None
        if subject is None:
            generic = re.search(r'\b(?:laboratory (?:studies|tests|results|data|evaluation)|blood tests|physical examination|abdominal examination|neurological examination|clinical examination|pathologic evaluation|pathological examination|imaging|ultrasonographic imaging)\b', prefix, re.I)
            subject = generic.group() if generic else None
        if subject:
            relation = 'physical_finding' if _TECHNICAL_PHYSICAL.search(subject) or 'physical_exams' in snippet['groups'] and 'auxiliary_exams' not in snippet['groups'] else 'exam_result'
            add(relation, subject, examination.group(1), examination.start(1))
    # Explicit analyte/measurement syntax has its own bound value. Do not turn
    # every numeric mention elsewhere in the sentence into this test's result.
    for exam in _EXAMS.finditer(text):
        suffix = text[exam.end():]
        value = re.match(r'\s*(?::|=|was|were|of)\s*((?:[<>≤≥]\s*)?\d[^;\n]*)', suffix, re.I)
        if value:
            add('exam_result', exam.group(), value.group(1), exam.end() + value.start(1))
    treatment = re.search(r'\b(?:underwent|was given|were given|was administered|were administered|was prescribed|were prescribed|was treated with|were treated with|received)\s+(.+)', text, re.I | re.S)
    if treatment and ('treatment' in snippet['groups'] or re.search(r'treat|therap|surg|resect|drain|excis', treatment.group(1), re.I)):
        value = treatment.group(1)
        diagnostic_only = _EXAMS.search(value) and not re.search(r'treat|therap|surg|resect|drain|excis|antibiotic', value, re.I)
        if not diagnostic_only:
            add('historical_treatment', 'patient', value, treatment.start(1))
    for bullet in re.finditer(r'(?:^|\n)\s*[•–-]\s*([^\n]+)', text):
        # Dataset bullet findings are already structured clinical propositions.
        # Their examination modality is not guessed when the prefix omits it.
        add('documented_finding', 'documented finding', bullet.group(1), bullet.start(1))
    return output


def _atomic_descriptors(snippet):
    """Extract bounded terms and values, not sentence-length paraphrases.

    These are mentions with explicit cues. They do not imply the absence of any
    unmentioned symptom and must not be promoted to a patient assertion when the
    snippet belongs to another/ambiguous patient or a hypothetical discussion.
    """
    text = snippet["text"]
    matches = sorted(list(_CONCEPTS.finditer(text)) + list(_EXAMS.finditer(text)), key=lambda m: (m.start(), m.end()))
    unique, facts = set(), []
    for match in matches:
        span = (match.start(), match.end())
        if span in unique: continue
        unique.add(span)
        left = text[max(0, match.start() - 55):match.start()]
        left = re.split(r"[.;:]|\bbut\b|\bhowever\b", left, flags=re.I)[-1]
        # A distant 'no improvement' must not turn 'antibiotics' into absence
        # of medication. Only an adjacent explicit cue is retained as such.
        direct_cue = re.search(r"\b(no|without|denied|denies|negative for|absence of)\s+(?:(?:any|significant|known|reported)\s+)?$", left, re.I)
        cue = direct_cue.group(1) if direct_cue else None
        # Measurements remain associated with the sentence, not guessed to be
        # the nearest analyte. The assembler must preserve these as unbound
        # values unless their association can be independently established.
        facts.append({"kind": "clinical_term", "term": match.group(), "negationCue": cue,
                      "polarity": "explicit_negative_cue" if cue else "not_determined",
                      "snippetId": snippet["id"], "textLocation": {"start": match.start(), "end": match.end()},
                      "patientRef": snippet["patientRef"], "context": snippet["context"],
                      "temporal": snippet["temporal"], "assertionVerified": False})
    for match in _MEASUREMENT.finditer(text):
        facts.append({"kind": "measurement", "value": match.group(), "snippetId": snippet["id"],
                      "textLocation": {"start": match.start(), "end": match.end()},
                      "patientRef": snippet["patientRef"], "association": "unbound_sentence_value", "assertionVerified": False})
    for kind, rule in (('anatomical_term', _ANATOMY), ('duration', _DURATION)):
        for match in rule.finditer(text):
            facts.append({'kind': kind, 'term': match.group(), 'snippetId': snippet['id'],
                          'textLocation': {'start': match.start(), 'end': match.end()},
                          'patientRef': snippet['patientRef'], 'association': 'same_sentence_only', 'assertionVerified': False})
    return facts


def extract_case_record(source_record, verified_source, *, xml_bytes=None):
    """Return a reproducible private extraction package; never perform translation.

    ``source_record`` is one MedCaseReasoning CSV row. ``verified_source`` is the
    existing PMC snapshot ``{provenance, articleText}``; an optional matching JATS
    XML improves exclusion of generic discussion and selection of case sections.
    Returned translationUnits have stable ``id,pmcid,field,text,language,kind``.
    ND packages expose only short terms/values to translation, never sentences.
    """
    if not isinstance(source_record, dict) or not isinstance(verified_source, dict):
        raise ValueError("Fonte local inválida.")
    provenance = verified_source.get("provenance", {})
    pmcid = str(source_record.get("pmcid", ""))
    if not re.fullmatch(r"PMC[1-9][0-9]{0,11}", pmcid) or provenance.get("pmcid") != pmcid:
        raise ValueError("PMCID da linha e do snapshot não coincidem.")
    article = " ".join(str(verified_source.get("articleText", "")).split())
    if not article:
        raise ValueError("O snapshot verificado não contém artigo.")
    if xml_bytes is not None and _sha(xml_bytes) != provenance.get("xmlSha256"):
        raise ValueError("O XML local não corresponde ao hash verificado.")
    prompt = str(source_record.get("case_prompt", "")).strip()
    final = str(source_record.get("final_diagnosis", "")).strip()
    rationale = str(source_record.get("diagnostic_reasoning", "")).strip()
    factual = provenance.get("reuseBasis") == "clinical_facts"
    paragraphs = _article_paragraphs(article, xml_bytes)
    has_case_section = any(any(_CASE_HEADING.search(title) for title in p["sections"]) for p in paragraphs)
    article_sentences = []
    inherited = None
    explicit_ids = []
    for paragraph in paragraphs:
        titles = " / ".join(paragraph["sections"])
        heading_ref, heading_ids = _subject(titles)
        if heading_ref and heading_ref != "mixed": inherited = heading_ref
        is_case = bool(_CASE_HEADING.search(titles))
        editorial = bool(_EDITORIAL_HEADING.search(titles)) and not is_case
        for start, end, text in _sentences(paragraph["text"]):
            ref, ids = _subject(text, inherited)
            explicit_ids.extend(ids)
            if len(ids) == 1: inherited = ids[0]
            article_sentences.append({"text": text, "start": paragraph["start"] + start, "end": paragraph["start"] + end,
                                      "sections": paragraph["sections"], "patientRef": ref,
                                      "context": "case_section" if is_case else "editorial" if editorial else "unmarked_article",
                                      "tokens": _tokens(text)})
    ids = list(dict.fromkeys(explicit_ids))
    multi = len(ids) > 1 or bool(_MULTI.search(prompt))
    # The first explicit individual is selected; mixed/shared blocks stay visible
    # as private context and never silently become that individual's history.
    selected = ids[0] if ids else None
    prompt_tokens = _tokens(prompt)
    prompt_is_individual = not _MULTI.search(prompt) and len(set(m.group(1) for m in _PATIENT_ID.finditer(prompt))) <= 1
    if multi and prompt_is_individual:
        scores = {identifier: set() for identifier in ids}
        for item in article_sentences:
            if item['patientRef'] in scores and item['context'] != 'editorial':
                scores[item['patientRef']].update(item['tokens'])
        ranked = sorted(((len(tokens & prompt_tokens), identifier) for identifier, tokens in scores.items()), reverse=True)
        if ranked and (len(ranked) == 1 or ranked[0][0] > ranked[1][0]):
            selected = ranked[0][1]
    snippets, flags = [], []
    if multi: flags.append("multiple_patients_detected; explicit individual or shared context must be retained")
    if not has_case_section: flags.append("no_explicit_case_section; article supplements use conservative lexical selection")
    if not prompt: flags.append("dataset_case_prompt_missing")
    if not final: flags.append("dataset_final_diagnosis_missing")

    def add(text, source_kind, start, end, *, match=None, context="dataset_summary", ref=None):
        groups, terms = _classify(text)
        identifier = "S" + str(len(snippets) + 1).zfill(4)
        temporal = "previous" if _PREVIOUS.search(text) else "later_or_treatment" if _POST_EVENT.search(text) else "unspecified"
        contains_diagnosis = bool(final and _normalized(final) in _normalized(text))
        patient_safe = source_kind == "dataset_case_prompt" and _patient_candidate(text, final) and ref != 'mixed' and (not multi or ref in {selected, 'shared'})
        snippet = {"id": identifier, "text": text, "sourceKind": source_kind,
                   "sourceLocation": {"start": start, "end": end} if source_kind == "verified_article" else None,
                   "sourceSha256": _sha(text) if source_kind == "verified_article" else None,
                   "datasetLocation": {"field": "case_prompt", "start": start, "end": end} if source_kind == "dataset_case_prompt" else None,
                   "textSha256": _sha(text), "groups": groups, "matchedTerms": terms,
                   "patientRef": ref, "context": context, "temporal": temporal,
                   "patientCandidate": patient_safe, "containsDiagnosisLabel": contains_diagnosis,
                   "support": match or {"kind": "original_article" if source_kind == "verified_article" else "unmatched", "literal": source_kind == "verified_article"},
                   "historyFields": [field for field, pattern in _HISTORY_FIELDS if re.search(pattern, text, re.I)],
                   "examTerms": list(dict.fromkeys(m.group() for m in _EXAMS.finditer(text)))}
        snippets.append(snippet)
        return snippet

    inherited_prompt = selected if multi and prompt_is_individual else None
    for start, end, text in _presentation_clauses(prompt):
        ref, direct_ids = _subject(text, inherited_prompt)
        if len(direct_ids) == 1: inherited_prompt = direct_ids[0]
        location = article.find(text)
        if location >= 0:
            support = {"kind": "literal_article_span", "literal": True, "sourceLocation": {"start": location, "end": location + len(text)}, "sourceSha256": _sha(text)}
        else:
            tokens = _tokens(text)
            matches = sorted(((len(tokens & s['tokens']) / max(1, len(tokens)), s) for s in article_sentences
                              if s['context'] != 'editorial' and (not multi or s['patientRef'] in {None, ref, 'shared'})),
                             key=lambda item: item[0], reverse=True)
            best_score, best = matches[0] if matches else (0, None)
            support = {"kind": "lexical_candidate" if best_score >= .45 else "unmatched", "literal": False}
            if best is not None and best_score >= .45:
                support.update(overlap=round(best_score, 4), sourceLocation={"start": best['start'], "end": best['end']}, sourceSha256=_sha(best['text']))
        add(text, "dataset_case_prompt", start, end, match=support, ref=ref)

    recovered = []
    if not any(s['patientCandidate'] for s in snippets):
        for original in list(snippets):
            if original['sourceKind'] != 'dataset_case_prompt': continue
            if multi and original['patientRef'] not in {selected, 'shared'}: continue
            for first, last, text in _recovery_clauses(original['text']):
                if not _recovery_candidate(text, final): continue
                # Exclude bare demographic fragments: recovery must add a
                # complaint, history, referral or birth circumstance.
                if len(text.split()) < 5: continue
                start = original['datasetLocation']['start'] + first
                location = article.find(text)
                support = ({'kind': 'literal_article_span', 'literal': True,
                            'sourceLocation': {'start': location, 'end': location + len(text)}, 'sourceSha256': _sha(text)}
                           if location >= 0 else {'kind': 'dataset_clause', 'literal': False,
                                                  'parentSupport': original['support']})
                if text == original['text']:
                    snippet = original
                elif any(s['text'] == text and s['patientCandidate'] for s in snippets):
                    continue
                else:
                    snippet = add(text, 'dataset_case_prompt', start, start + len(text), match=support, ref=original['patientRef'])
                snippet['patientCandidate'] = True
                snippet['presentationRecovery'] = {'method': 'explicit_original_clause',
                                                   'parentSnippetId': original['id'], 'clinicalValidation': False}
                recovered.append(snippet['id'])
        if recovered:
            flags.append('patient_presentation_recovered_from_explicit_original_clauses')

    # Supplement the short presentation only with case-level confirmation and
    # management, never the article's general recommended differential/treatment.
    supplements = []
    for item in article_sentences:
        if item["context"] == "editorial": continue
        if has_case_section and item["context"] != "case_section": continue
        if multi and item["patientRef"] not in {selected, "shared"}: continue
        text, tokens = item["text"], item["tokens"]
        categories, _ = _classify(text)
        confirm = bool(_CONFIRMATION.search(text))
        treatment = "treatment" in categories
        label = bool(final and _normalized(final) in _normalized(text))
        if not (confirm or treatment or label): continue
        if not has_case_section and not (re.search(r"\b(?:patient|she|he|our case)\b", text, re.I) and len(tokens & prompt_tokens) >= 2): continue
        supplements.append(item)
    # Preserve every qualifying case-level supplement; no arbitrary count cap.
    for item in supplements:
        if any(s["text"] == item["text"] for s in snippets): continue
        add(item["text"], "verified_article", item["start"], item["end"], context=item["context"], ref=item["patientRef"])

    diagnosis_mentions = [s["id"] for s in snippets if s["containsDiagnosisLabel"]]
    if final and not diagnosis_mentions:
        flags.append("dataset_diagnosis_not_literal_in_selected_spans; do not claim original confirmation")
    descriptors = []
    for snippet in snippets:
        # Other patients and generic discussion remain private snippets only.
        if multi and snippet["patientRef"] not in {selected, "shared"}: continue
        descriptors.extend(_atomic_descriptors(snippet))
        descriptors.extend(_relation_descriptors(snippet))
    units, seen = [], set()
    def unit(field, text, kind, snippet=None):
        if not text or (field, text, kind) in seen: return
        seen.add((field, text, kind))
        row = {"id": pmcid + ":" + kind + ":" + _sha(field + "\0" + text)[:20], "pmcid": pmcid,
               "field": field, "text": text, "language": "en", "kind": kind}
        if snippet:
            row.update(snippetId=snippet["id"], sourceLocation=snippet["sourceLocation"], sourceSha256=snippet["sourceSha256"], support=snippet["support"])
        units.append(row)
    if factual:
        for fact in descriptors:
            if fact['kind'] in {'clinical_term', 'anatomical_term', 'duration'}:
                unit("clinical_terms", fact['term'], fact['kind'])
            elif fact['kind'] == 'relation':
                unit('clinical_relation_subject', fact['subjectOriginal'], 'factual_relation_subject')
                unit('clinical_relation_value', fact['valueOriginal'], 'factual_relation_value')
        # Numerical values remain byte-for-byte unchanged; they need no translation.
    else:
        for snippet in snippets:
            if multi and snippet["patientRef"] not in {selected, "shared"}: continue
            unit(snippet["groups"][0], snippet["text"], "source_sentence", snippet)
    # Only expose word boundaries to the translator; retain the exact dataset
    # label and do not expand abbreviations or reinterpret a clinical term.
    diagnosis_translation = re.sub(r'([A-Z])([A-Z][a-z])', r'\1 \2', final)
    diagnosis_translation = re.sub(r'([a-z])([A-Z])', r'\1 \2', diagnosis_translation)
    unit("diagnosis", diagnosis_translation, "dataset_diagnosis_label")
    reasoning_items = _reasoning_items(rationale, factual)
    for item in reasoning_items:
        unit('reference_reasoning', item['text'], item['kind'])
        item['translationUnitId'] = pmcid + ':' + item['kind'] + ':' + _sha('reference_reasoning' + '\0' + item['text'])[:20]
    groups = {key: [snippet["id"] for snippet in snippets if key in snippet["groups"]] for key in GROUPS}
    demographics = []
    for snippet in snippets:
        for match in _AGE.finditer(snippet['text']):
            demographics.append({"kind": "age", "valueOriginal": match.group(1), "unitOriginal": match.group(2), "phraseOriginal": match.group(), "snippetId": snippet['id'], "patientRef": snippet['patientRef']})
            linked_gender = re.match(r'\s+(?:(?:[A-Za-z]+|[A-Za-z]+-[A-Za-z]+)\s+){0,3}(man|woman|male|female|boy|girl)\b', snippet['text'][match.end():], re.I)
            if linked_gender:
                value = linked_gender.group(1).casefold()
                demographics.append({'kind': 'gender', 'valueOriginal': 'male' if value in {'man', 'male', 'boy'} else 'female',
                                     'phraseOriginal': linked_gender.group().strip(), 'snippetId': snippet['id'], 'patientRef': snippet['patientRef']})
        for match in _AGE_BAND.finditer(snippet['text']):
            demographics.append({"kind": "age_range", "phraseOriginal": match.group(), "snippetId": snippet['id'], "patientRef": snippet['patientRef'], "numericAge": None})
    # Do not average ages, infer exact ages from decades, or infer anatomy from
    # gender. Multiple different ages within an introduction remain unresolved.
    intro = next((s for s in snippets if s['sourceKind'] == 'dataset_case_prompt' and (not multi or s['patientRef'] == selected)), None)
    intro_ages = list(_AGE.finditer(intro['text'])) if intro else []
    intro_sexes = list(re.finditer(r'\b(man|woman|male|female|boy|girl)\b', intro['text'], re.I)) if intro else []
    unique_sexes = {'male' if m.group().casefold() in {'man', 'male', 'boy'} else 'female' for m in intro_sexes}
    age_value = float(intro_ages[0].group(1)) if len(intro_ages) == 1 else None
    if age_value is not None and (age_value < 0 or age_value > 120): age_value = None
    demographic_values = {'age_value': age_value, 'age_unit_original': intro_ages[0].group(2) if len(intro_ages) == 1 else None,
                          'gender_original': next(iter(unique_sexes)) if len(unique_sexes) == 1 else None,
                          'sourceSnippetId': intro['id'] if intro else None, 'inferred': False}
    if any(s['support']['kind'] == 'unmatched' for s in snippets if s['sourceKind'] == 'dataset_case_prompt'):
        flags.append("some_dataset_sentences_have_no_source_match; preserved as dataset claims, not verified evidence")
    patient_species = _patient_species(prompt)
    if patient_species['kind'] == 'nonhuman':
        flags.append('nonhuman_presenting_subject; preserve_species_and_do_not_convert_to_human')
    return {
        "schemaVersion": SCHEMA_VERSION, "extractorVersion": EXTRACTOR_VERSION,
        "pmcid": pmcid, "caseId": "mcr-" + pmcid.lower(), "stage": "extracted_original",
        "patientSpecies": patient_species,
        "reviewStatus": "review_required", "language": "en", "translationCompleted": False,
        "clinicalApproval": False, "sourceProvenance": provenance,
        "sourceHashes": {"articleNormalizedSha256": _sha(article), "casePromptSha256": _sha(prompt), "finalDiagnosisSha256": _sha(final), "rationaleSha256": _sha(rationale)},
        "selection": {"method": "first_explicit_individual_or_single_case", "selectedPatientRef": selected,
                      "multiplePatientsDetected": multi, "explicitPatientRefs": ids, "identityResolution": "heuristic"},
        "specialty": _specialty(source_record), "groups": groups, "groupLabels": GROUP_LABELS,
        "snippets": snippets, "atomicDescriptors": descriptors, "demographicMentions": demographics, "demographics": demographic_values,
        "diagnosis": {"originalLabel": final, "translationLabel": diagnosis_translation,
                      "normalizationMethod": "camel_case_word_boundaries" if diagnosis_translation != final else "none",
                      "origin": "dataset_final_diagnosis", "sourceMentionIds": diagnosis_mentions, "clinicallyConfirmedByExtractor": False},
        "rationale": {"original": rationale, "origin": "dataset_diagnostic_reasoning", "privateOnly": True,
                      "translationRequested": bool(reasoning_items), "items": reasoning_items,
                      "note": "Raciocínio do dataset, não comprovação independente; no fluxo factual traduzem-se somente rótulos fora das citações."},
        "translationUnits": units,
        "statistics": {"casePromptCharacters": len(prompt), "articleCharacters": len(article), "sentences": len(snippets),
                       "translationUnits": len(units), "translationCharacters": sum(len(u['text']) for u in units), "atomicDescriptors": len(descriptors)},
        "flags": flags,
        "assemblyPlan": {"name": "Paciente simulado", "reviewStatus": "review_required",
            "unknownValue": "Não informado na fonte.", "unknownAge": None,
            "patientSnippetIds": [s['id'] for s in snippets if s['patientCandidate']],
            "examSnippetIds": [s['id'] for s in snippets if set(s['groups']) & {'physical_exams', 'auxiliary_exams'}],
            "laterInformationOnlyInReference": True, "missingGroups": [key for key in GROUPS[:-1] if not groups[key]],
            "rubricDimensions": ['consultation', 'physical', 'auxiliary', 'diagnosis', 'treatment', 'overall'],
            "auditFlags": None, "readyForCaseSchema": False,
            "note": "Montar após tradução local identificada; separar dados do paciente, achados profissionais e gabarito. Não inventar flags de auditoria, negações, sexo, parentesco ou fatos ausentes. ND: usar somente descritores clínicos em templates originais, sem traduzir os snippets narrativos."},
    }


def _atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix=".prepare-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.flush(); os.fsync(handle.fileno())
        os.chmod(name, 0o600); os.replace(name, path)
    finally:
        if os.path.exists(name): os.unlink(name)


def iter_translation_units(packages):
    """Yield serializable segments for a persistent local translator."""
    for package in packages:
        yield from package['translationUnits']


def prepare_sources(source_path, cache_dir, output_dir, *, excluded_pmcids=(), skip_pmcids=(), limit=None, on_progress=None):
    """Register every CSV row honestly; atomically extract available verified sources.

    Resumption requires the extractor version, CSV clinical field hashes and
    verified XML identity to match. Failures are reported per record, never
    converted to successful cases. This function has no network or DB access.
    ``limit`` limits newly processed records, not inventory counting. Existing
    clinical drafts can be preserved using ``skip_pmcids``.
    """
    if limit is not None and (type(limit) is not int or limit < 0):
        raise ValueError('limit deve ser inteiro não negativo ou None.')
    excluded, skipped = set(excluded_pmcids), set(skip_pmcids)
    if any(not isinstance(v, str) or not re.fullmatch(r'PMC[1-9][0-9]{0,11}', v) for v in excluded | skipped):
        raise ValueError('Lista de PMCIDs inválida.')
    source_path, cache_dir, output_dir = Path(source_path), Path(cache_dir), Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    (output_dir / '.gitignore').write_text('*\n')
    counts, specialties, flags, failures, seen = Counter(), Counter(), Counter(), [], set()
    csv.field_size_limit(max(csv.field_size_limit(), 4_000_000))
    with source_path.open(encoding='utf-8-sig', newline='') as handle:
        reader = csv.DictReader(handle)
        if not {'pmcid', 'case_prompt', 'final_diagnosis', 'diagnostic_reasoning'} <= set(reader.fieldnames or []):
            raise ValueError('CSV não contém os campos clínicos necessários.')
        for record in reader:
            counts['sourceRecords'] += 1
            pmcid = record['pmcid']
            if pmcid in seen:
                counts['duplicates'] += 1; continue
            seen.add(pmcid)
            if pmcid in excluded: counts['excluded'] += 1; continue
            if pmcid in skipped: counts['existingDraftPreserved'] += 1; continue
            if not re.fullmatch(r'PMC[1-9][0-9]{0,11}', pmcid):
                counts['invalidSourceIds'] += 1; continue
            verified_path = cache_dir / (pmcid + '.verified.json')
            if not verified_path.exists(): counts['verifiedSnapshotMissing'] += 1; continue
            try:
                verified = json.loads(verified_path.read_text())
                p = verified['provenance']
                target = output_dir / 'records' / (pmcid + '.json')
                signature = _sha(json.dumps({key: record.get(key, '') for key in ('pmcid','case_prompt','final_diagnosis','diagnostic_reasoning','journal')}, sort_keys=True) + p['xmlSha256'] + EXTRACTOR_VERSION)
                cached = json.loads(target.read_text()) if target.exists() else None
                if isinstance(cached, dict) and cached.get('inputSignature') == signature:
                    package = cached; counts['reused'] += 1
                elif limit is not None and counts['processedThisRun'] >= limit:
                    counts['pendingLimit'] += 1; continue
                else:
                    counts['processedThisRun'] += 1
                    xml_path = cache_dir / f"{pmcid}.{p['pmcVersion']}.{p['xmlMd5']}.xml"
                    package = extract_case_record(record, verified, xml_bytes=xml_path.read_bytes() if xml_path.exists() else None)
                    package['inputSignature'] = signature
                    _atomic_json(target, package)
                    counts['extractedThisRun'] += 1
                counts['extractedTotal'] += 1
                counts['translationUnits'] += len(package['translationUnits'])
                counts['translationCharacters'] += package['statistics']['translationCharacters']
                counts['factualSources' if p.get('reuseBasis') == 'clinical_facts' else 'licensedSources'] += 1
                counts['withoutPatientCandidates'] += not bool(package['assemblyPlan']['patientSnippetIds'])
                if p.get('reuseBasis') == 'clinical_facts':
                    patient_ids = set(package['assemblyPlan']['patientSnippetIds'])
                    counts['factualWithoutInitialClinicalTerms'] += not any(f['kind'] == 'clinical_term' and f['snippetId'] in patient_ids for f in package['atomicDescriptors'])
                specialties[package['specialty']['specialty'] or 'Unclassified'] += 1
                flags.update(flag.split(';',1)[0] for flag in package['flags'])
                if on_progress: on_progress({'pmcid': pmcid, 'status': 'extracted_original', 'path': str(target), 'counts': dict(counts)})
            except (OSError, ValueError, KeyError, TypeError, ET.ParseError) as exc:
                counts['errors'] += 1
                failures.append({'pmcid': pmcid, 'errorType': type(exc).__name__, 'message': str(exc)[:300]})
                if on_progress: on_progress({'pmcid': pmcid, 'status': 'extraction_error', 'counts': dict(counts)})
    counts['uniquePmcids'] = len(seen)
    report = {'schemaVersion': SCHEMA_VERSION, 'extractorVersion': EXTRACTOR_VERSION,
              'generatedAt': datetime.now(timezone.utc).isoformat(), 'counts': dict(counts),
              'specialtyCandidates': dict(specialties), 'flags': dict(flags), 'failures': failures,
              'apiCalls': 0, 'translatedCases': 0, 'publishedCases': 0, 'databaseMutations': 0}
    _atomic_json(output_dir / 'report.json', report)
    return report
