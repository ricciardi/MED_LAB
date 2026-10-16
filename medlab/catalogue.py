"""Public case discovery uses only controlled, diagnosis-free metadata."""

from sqlalchemy import func, or_, select

from .extensions import db
from .models import ClinicalCase, CaseDiscovery
from .discovery import DIFFICULTIES, available_image_count, folded


def case_type_for(data):
    from .catalog import CASE_TYPES
    value = data.get("caseType", "Primeira consulta")
    return value if value in CASE_TYPES else "Primeira consulta"


def public_title(identifier):
    return f"Encontro clínico #{identifier:05d}"


def public_summary(case_type):
    return f"{case_type}. Conduza a entrevista, reúna as informações e construa sua hipótese."


def filtered_cases(values):
    """Identical filters for browsing and random selection. Never search case JSON."""
    from .catalog import BODY_SYSTEMS, CASE_TYPES
    query = select(ClinicalCase).outerjoin(CaseDiscovery).where(ClinicalCase.is_published.is_(True))
    specialty = values.get("specialty", "").strip()[:80]
    difficulty = values.get("difficulty", "").strip()[:30]
    case_type = values.get("case_type", "").strip()[:80]
    search = values.get("q", "").strip()[:100]
    exams = values.get("exams", "all")
    exams = exams if exams in {"all", "results", "images"} else "all"
    track = "basic" if values.get("track") == "basic" else ""
    system = values.get("system", "")
    system = system if system in BODY_SYSTEMS else ""
    if system:
        track = "basic"
    if track:
        query = query.where(CaseDiscovery.learning_track == track)
    if system:
        query = query.where(CaseDiscovery.body_system == system)
    if specialty:
        query = query.where(ClinicalCase.specialty == specialty)
    if difficulty:
        query = query.where(func.coalesce(CaseDiscovery.difficulty, ClinicalCase.difficulty) == difficulty)
    if case_type:
        query = query.where(or_(CaseDiscovery.presentation_tags.contains(f"|{case_type}|", autoescape=True),
                                (CaseDiscovery.case_id.is_(None)) & (ClinicalCase.case_type == case_type)))
    if exams == "results":
        query = query.where(CaseDiscovery.exam_result_count > 0)
    elif exams == "images":
        # Recheck the few bound assets: an index must not advertise a removed
        # file or revoked binding. Pending downloaded figures never qualify.
        candidates = db.session.execute(select(ClinicalCase.id, ClinicalCase.data).join(CaseDiscovery)
            .where(ClinicalCase.is_published.is_(True), CaseDiscovery.exam_image_count > 0)).all()
        available = [identifier for identifier, data in candidates if available_image_count(data)]
        query = query.where(ClinicalCase.id.in_(available))
    if search:
        # Match only the controlled metadata shown in a card, never title/summary,
        # article metadata, patient history, references or diagnostic answer keys.
        predicates = [CaseDiscovery.search_text.contains(folded(search), autoescape=True)]
        numeric = search.removeprefix("#")
        if numeric.isdigit() and len(numeric) <= 9:
            predicates.append(ClinicalCase.id == int(numeric))
        query = query.where(or_(*predicates))
    return query, {"selected_specialty": specialty, "selected_difficulty": difficulty,
                   "selected_case_type": case_type, "search_query": search,
                   "selected_exams": exams, "case_types": CASE_TYPES, "difficulties": DIFFICULTIES,
                   "selected_track": track, "selected_system": system, "body_systems": BODY_SYSTEMS}


def catalogue_counts(target=300):
    from .catalog import SPECIALTIES
    kind = ClinicalCase.data["catalogType"].as_string()
    rows = db.session.execute(select(ClinicalCase.specialty, ClinicalCase.is_published,
        kind, func.count(ClinicalCase.id)).group_by(ClinicalCase.specialty, ClinicalCase.is_published, kind)).all()
    report = {name: {"specialty": name, "published_real": 0, "draft_real": 0,
                    "published_fictional": 0, "target": target, "missing": target} for name in SPECIALTIES}
    for specialty, published, catalog_type, count in rows:
        if specialty not in report:
            report[specialty] = {"specialty": specialty, "published_real": 0, "draft_real": 0,
                                "published_fictional": 0, "target": target, "missing": target}
        row = report[specialty]
        if catalog_type == "published_case":
            row["published_real" if published else "draft_real"] += count
        elif published:
            row["published_fictional"] += count
        row["missing"] = max(0, target - row["published_real"])
    return list(report.values())


def catalogue_facets(values):
    """Offer categories that exist with the other filters, with real counts."""
    from collections import Counter
    from .catalog import CASE_TYPES
    type_query, _ = filtered_cases({**dict(values), "case_type": ""})
    rows = db.session.execute(type_query.with_only_columns(
        CaseDiscovery.presentation_tags, ClinicalCase.case_type, func.count(ClinicalCase.id))
        .group_by(CaseDiscovery.presentation_tags, ClinicalCase.case_type)).all()
    types = Counter()
    for tags, fallback, count in rows:
        for kind in tags.strip("|").split("|") if tags else [fallback]:
            types[kind] += count
    difficulty_query, _ = filtered_cases({**dict(values), "difficulty": ""})
    level = func.coalesce(CaseDiscovery.difficulty, ClinicalCase.difficulty)
    difficulties = dict(db.session.execute(difficulty_query.with_only_columns(level, func.count(ClinicalCase.id))
                                          .group_by(level)).all())
    chosen_type, chosen_level = values.get("case_type", ""), values.get("difficulty", "")
    return {"case_types": [t for t in CASE_TYPES if types[t] or t == chosen_type],
            "difficulties": [d for d in DIFFICULTIES if difficulties.get(d, 0) or d == chosen_level],
            "type_counts": dict(types), "difficulty_counts": difficulties}
