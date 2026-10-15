"""Public case discovery uses only controlled, diagnosis-free metadata."""

from sqlalchemy import func, or_, select

from .extensions import db
from .models import ClinicalCase


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
    from .catalog import CASE_TYPES
    query = select(ClinicalCase).where(ClinicalCase.is_published.is_(True))
    specialty = values.get("specialty", "").strip()[:80]
    difficulty = values.get("difficulty", "").strip()[:30]
    case_type = values.get("case_type", "").strip()[:80]
    search = values.get("q", "").strip()[:100]
    if specialty:
        query = query.where(ClinicalCase.specialty == specialty)
    if difficulty:
        query = query.where(ClinicalCase.difficulty == difficulty)
    if case_type:
        query = query.where(ClinicalCase.case_type == case_type)
    if search:
        # Match only the controlled metadata shown in a card, never title/summary,
        # article metadata, patient history, references or diagnostic answer keys.
        predicates = [ClinicalCase.specialty.icontains(search, autoescape=True),
                      ClinicalCase.case_type.icontains(search, autoescape=True),
                      ClinicalCase.difficulty.icontains(search, autoescape=True)]
        numeric = search.removeprefix("#")
        if numeric.isdigit() and len(numeric) <= 9:
            predicates.append(ClinicalCase.id == int(numeric))
        query = query.where(or_(*predicates))
    return query, {"selected_specialty": specialty, "selected_difficulty": difficulty,
                   "selected_case_type": case_type, "search_query": search,
                   "case_types": CASE_TYPES}


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
