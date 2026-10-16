"""Auditable discovery metadata. No API calls and no alteration of clinical facts.

The local corpus was assigned the same difficulty and a first-regex presentation.
This index estimates the *workload of the available script*, not disease severity
or diagnostic rarity. Authored classifications remain authoritative.
"""
import hashlib
import json
import re
import unicodedata

from sqlalchemy import event
from sqlalchemy.orm import Session

from .case_schema import UNKNOWN_FACT

VERSION = "discovery-v1"
DIFFICULTIES = ("Iniciante", "Intermediário", "Avançado", "Não classificada")


def folded(value):
    return " ".join("".join(c for c in unicodedata.normalize("NFKD", str(value))
                            if not unicodedata.combining(c)).lower().split())


def _usable_line(value):
    text = folded(value).strip(" .")
    return not (text.startswith(("nao informado na fonte", "mencoes clinicas extraidas",
                                 "valor mencionado, associacao pendente")) or
                text in {"", "nao informado", "nao informada", "indisponivel", "nao disponivel",
                         "nao realizado", "nao realizada", "sem resultado", "unknown", "n/a"})


def usable_content(value):
    if not isinstance(value, str):
        return ""
    return "\n".join(line.strip() for line in value.splitlines() if _usable_line(line))


def usable_text(value):
    return bool(usable_content(value))


def result_entries(data, key):
    entries = data.get(key, {})
    if not isinstance(entries, dict):
        return {}
    return {name: result for name, result in entries.items()
            if isinstance(name, str) and name.strip() and usable_text(result)}


def exam_groups(entries):
    # Continuations and repeated results do not become separate workload points.
    groups = {}
    for name, result in entries.items():
        stem = re.sub(r"\s*\((?:parte|continuacao|continuação) \d+ de \d+\)$", "", name, flags=re.I)
        groups.setdefault(folded(stem), []).append(result)
    return groups


_RULES = (
    ("Dor torácica", r"\b(?:dor(?:es)? (?:toracic\w*|no peito)|chest pain)\b"),
    ("Dor abdominal", r"\b(?:dor(?:es)? (?:abdomina\w*|epigastric\w*|na barriga|no abdome)|abdominal pain|epigastric pain)\b"),
    ("Sintomas respiratórios", r"\b(?:tosse|dispneia|falta de ar|chiado|hemoptise|cough\w*|dyspn\w*|wheez\w*|shortness of breath)\b"),
    ("Sintomas urinários", r"\b(?:disuria|hematuria|polaciuria|oliguria|anuria|nocturia|incontinencia urinaria|(?:ard[eo]r|dor) (?:ao|para) urinar|retencao urinaria|urina (?:escura|avermelhada|com sangue|espumosa)|dysuria|ha?ematuria)\b"),
    ("Cefaleia e sintomas neurológicos", r"\b(?:cefaleia|dor(?:es)? de cabeca|convuls\w*|dormencia|formigamento|paralisia|fraqueza (?:no|na|dos|das)\b|headache|seizure\w*|numbness|tingling)"),
    ("Sintomas digestivos", r"\b(?:vomit\w*|nausea\w*|diarreia|constipacao|disfagia|obstipacao|sangramento retal|diarrh\w*|constipat\w*)\b"),
    ("Alterações da pele", r"\b(?:prurido|coceira|erupcao|alopecia|queda de cabelo\w*|les(?:ao|oes) (?:cutanea\w*|na pele)|rash|itch\w*|skin lesion\w*)\b"),
    ("Dor e limitação de movimento", r"\b(?:lombalgia|artralgia|dor(?:es)? (?:lombar|articular\w*|nas costas|no joelho|no ombro)|limitacao (?:de movimento|funcional)|back pain|joint pain|limb pain)\b"),
    ("Sintomas ginecológicos", r"\b(?:corrimento (?:vaginal|genital)|leucorreia|sangramento vaginal|dor pelvica|amenorreia|menstrual|vaginal bleed\w*|pelvic pain)\b"),
    ("Saúde emocional", r"\b(?:ansiedade|tristeza|humor deprimido|alucinac\w*|anxiety|low mood|hallucinat\w*)\b"),
    ("Febre e sintomas gerais", r"\b(?:febre|fadiga|cansaco|mal-estar|fever|fatigue|malaise)\b"),
    ("Alterações visuais", r"\b(?:visao (?:turva|emba\w*|dupla|borrada)|perda (?:da |de )?visao|dor ocular|dor no olho|(?:deficit|defeito) (?:de|do) campo visual|queda da palpebra|diplopia|visual loss|blurred vision)\b"),
    ("Sintomas de ouvido, nariz e garganta", r"\b(?:otalgia|zumbido|dor (?:de ouvido|de garganta)|rouquidao|perda auditiva|diminuicao da audicao|epistaxe|obstrucao nasal|corrimento nasal|rinorreia|hearing loss|tinnitus)\b"),
    ("Palpitações e desmaios", r"\b(?:palpitac\w*|desmaio\w*|sincope|palpitation\w*|syncope)\b"),
    ("Edema e alterações circulatórias", r"\b(?:edema|inchaco|claudicacao|perna\w* inchad\w*|swelling)\b"),
    ("Nódulos e massas", r"\b(?:nodulo\w*|caroco\w*|massa palpavel|massa (?:no|na|de|do|da) (?:pescoco|mama|axila|virilha|superficie|regiao|face|cabeca|braco|perna)|lump\w*|palpable mass)\b"),
    ("Sede, fome e alterações de peso", r"\b(?:sede|polidipsia|polifagia|perda de peso|ganho de peso|weight loss|weight gain|thirst)\b"),
    ("Trauma e lesões", r"\b(?:trauma\w*|acidente|queda(?!\s+(?:d[aeo]\s+)?(?:cabelo|palpebra|pressao|hemoglobina|saturacao|contagem|nivel|peso))|ferimento\w*|queimadura\w*|injur\w*)\b"),
)
_NEGATIVE = re.compile(r"\b(?:nega|negou|negava|sem|ausencia de|nao (?:tem|tinha|apresent\w*|relat\w*|refer\w*|houve)|denies|denied|without|no evidence of)\b")


def _positive_matches(text):
    text = folded(text)
    found = []
    for label, pattern in _RULES:
        for match in re.finditer(pattern, text):
            # Negation can scope a comma-separated list, but not a new sentence,
            # contrast, or explicit positive predicate.
            prefix = re.split(r"[.;!?\n]|\b(?:mas|porem|entretanto|but)\b|(?<!nao )\b(?:relata|apresenta|refere)\b", text[:match.start()])[-1]
            if _NEGATIVE.search(prefix):
                continue
            suffix = re.split(r"[.;!?]|\b(?:mas|porem|entretanto|but)\b", text[match.end():])[0]
            if re.fullmatch(r"[^:]{0,65}\b(?:ausentes?|negad[ao]s?)\s*", suffix):
                continue
            found.append((match.start(), label))
            break
    return [label for _, label in sorted(found)]


def presentation_types(data):
    from .catalog import CASE_TYPES
    local = bool(data.get("provenance", {}).get("localPreparation"))
    value = data.get("caseType", "Primeira consulta")
    if not local and value not in {"Primeira consulta", "Outros sintomas"}:
        return [value if value in CASE_TYPES else "Primeira consulta"]
    profile = data.get("patientProfile", {})
    # Complaint wins over a long history containing old diseases and negatives.
    for key in ("chief_complaint", "present_illness_history"):
        value = usable_content(profile.get(key))
        if value:
            value = "\n".join(line for line in value.splitlines() if not re.match(
                r"^(?:antecedentes|historia (?:familiar|pregressa)|historico|previamente|no passado)\b", folded(line)))
            labels = _positive_matches(value[:3000])
            if labels:
                return list(dict.fromkeys(labels))[:3]
            if value:
                break  # An unmatched current complaint must not become an old symptom from the history.
    age = profile.get("age_value")
    if type(age) in (int, float) and age >= 0 and (profile.get("age_unit") != "anos" or age < 18):
        return ["Queixa pediátrica"]
    return ["Outros sintomas" if local else value if value in CASE_TYPES else "Primeira consulta"]


def difficulty_for(data, auxiliary):
    if not data.get("provenance", {}).get("localPreparation"):
        value = data.get("difficulty")
        return (value if value in DIFFICULTIES else "Não classificada", False,
                "Nível definido na preparação do caso.")
    profile = data.get("patientProfile", {})
    if not any(usable_text(profile.get(k)) for k in ("chief_complaint", "present_illness_history")):
        return "Não classificada", True, "Dados de entrevista insuficientes para estimar a extensão do roteiro."
    topics = sum(usable_text(profile.get(k)) for k in (
        "chief_complaint", "present_illness_history", "past_medical_history", "personal_history",
        "family_history", "other_medical_history", "allergy_history", "surgery_injury_history", "reproductive_history"))
    groups = len(exam_groups(auxiliary))
    # Explicit workload bands; no hidden diagnosis, rarity or severity heuristic.
    score = (2 if groups >= 6 else 1 if groups >= 3 else 0) + (2 if topics >= 7 else 1 if topics >= 4 else 0)
    level = "Avançado" if score >= 3 else "Intermediário" if score >= 1 else "Iniciante"
    return level, True, (f"Estimativa pela extensão do roteiro: {topics} tópicos de entrevista e {groups} grupos "
                         "de exames complementares. Não mede gravidade, raridade nem competência clínica.")


def available_image_count(data):
    from .exam_media import _approved_metadata, get_media_store, snapshot_media
    from .source_media import SourceMediaError
    entries = snapshot_media(data)
    if not entries:
        return 0
    store, available = get_media_store(), set()
    for entry in entries:
        if _approved_metadata(store, data, entry) is None:
            continue
        source = data["provenance"]
        try:
            asset = store.read_media_asset(entry["assetId"], pmcid=source["pmcid"],
                xml_sha256=source["xmlSha256"], case_id=data["caseId"], exam_name=entry["examName"],
                group=entry["group"], caption_pt=entry["captionPt"])
            if asset:
                available.add(entry["assetId"])
        except (SourceMediaError, OSError, ValueError, KeyError, TypeError):
            continue
    return len(available)


def discovery_values(case):
    data = case.data if isinstance(case.data, dict) else {}
    # For authored cases, the editable catalogue columns remain authoritative.
    authored = {**data, "caseType": case.case_type or data.get("caseType", "Primeira consulta"),
                "difficulty": case.difficulty}
    types = presentation_types(authored)
    auxiliary, physical = result_entries(data, "auxiliaryLabs"), result_entries(data, "physicalExams")
    difficulty, estimated, note = difficulty_for(authored, auxiliary)
    return dict(version=VERSION, source_digest=hashlib.sha256(json.dumps(
        [data, case.specialty, case.case_type, case.difficulty], ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
        case_type=types[0], presentation_tags="|" + "|".join(types) + "|", difficulty=difficulty,
        difficulty_estimated=estimated, classification_note=note,
        search_text=folded(" ".join([case.specialty, *types, difficulty])),
        exam_result_count=len(exam_groups(auxiliary)), physical_result_count=len(exam_groups(physical)),
        exam_image_count=available_image_count(data))


def refresh_discovery(case):
    from .models import CaseDiscovery
    values = discovery_values(case)
    if case.discovery is None:
        case.discovery = CaseDiscovery(**values)
    else:
        for key, value in values.items():
            if getattr(case.discovery, key) != value:
                setattr(case.discovery, key, value)


@event.listens_for(Session, "before_flush")
def maintain_discovery(session, _context, _instances):
    from .models import ClinicalCase
    for item in list(session.new) + list(session.dirty):
        if isinstance(item, ClinicalCase) and item not in session.deleted:
            refresh_discovery(item)


def rebuild_discovery(batch_size=100):
    from .extensions import db
    from .models import ClinicalCase
    last, count = 0, 0
    while True:
        rows = db.session.scalars(db.select(ClinicalCase).where(ClinicalCase.id > last)
                                 .order_by(ClinicalCase.id).limit(batch_size)).all()
        if not rows:
            break
        for case in rows:
            refresh_discovery(case)
        last = rows[-1].id
        count += len(rows)
        db.session.commit()
        db.session.expunge_all()
    return count
