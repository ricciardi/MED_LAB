import copy
from datetime import timedelta
from uuid import uuid4
from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_user, logout_user
from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload
from urllib.parse import urlsplit
from .extensions import db
from .models import AIUsage, ClinicalCase, Consultation, User, utcnow
from .security import admin_required, consume_limit, login_rate_allowed
from .catalog import CASE_TYPES, SPECIALTIES
from .catalogue import case_type_for, catalogue_counts
from .usage import OPERATIONS, capture_usage, usage_row, usage_summary

bp = Blueprint("admin", __name__)

@bp.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()[:254]
        if not login_rate_allowed(email):
            abort(429)
        user = db.session.execute(select(User).where(User.email == email)).scalar_one_or_none()
        if user and user.is_active and user.is_admin and user.check_password(request.form.get("password", "")[:128]):
            session.clear()
            login_user(user, remember=False)
            session["admin_access"] = True
            return redirect(url_for("admin.dashboard"))
        error = "Não foi possível acessar a administração com estes dados."
    return render_template("admin/login.html", error=error), (400 if error else 200)

@bp.post("/sair")
@admin_required
def logout():
    logout_user()
    session.clear()
    return redirect(url_for("admin.login"))

@bp.get("/")
@bp.get("")
@admin_required
def dashboard():
    counts = {"users": db.session.scalar(select(func.count(User.id))),
              "cases": db.session.scalar(select(func.count(ClinicalCase.id))),
              "completed": db.session.scalar(select(func.count(Consultation.id)).where(Consultation.status == "completed")),
              "drafts": db.session.scalar(select(func.count(ClinicalCase.id)).where(ClinicalCase.is_published.is_(False)))}
    recent = db.session.execute(select(Consultation).order_by(Consultation.created_at.desc()).limit(10)).scalars().all()
    return render_template("admin/dashboard.html", counts=counts, recent=recent,
                           catalog_counts=catalogue_counts(),
                           model=current_app.config["AI_MODEL"], key_configured=bool(current_app.config["DEEPSEEK_API_KEY"]))


@bp.get("/uso-ia")
@admin_required
def ai_usage():
    period = request.args.get("period", "30")
    if period not in {"7", "30", "all"}:
        period = "30"
    operation = request.args.get("operation", "")
    if operation not in OPERATIONS:
        operation = ""
    conditions = []
    if period != "all":
        conditions.append(AIUsage.created_at >= utcnow() - timedelta(days=int(period)))
    if operation:
        conditions.append(AIUsage.operation == operation)
    pagination = db.paginate(select(AIUsage).where(*conditions).order_by(AIUsage.id.desc()),
        page=min(1_000_000, max(1, request.args.get("page", 1, type=int))), per_page=25, error_out=False)
    filters = {"period": period, "operation": operation}
    if pagination.page > max(1, pagination.pages):
        return redirect(url_for("admin.ai_usage", page=max(1, pagination.pages), **filters))
    return render_template("admin/usage.html", totals=usage_summary(*conditions),
        entries=[usage_row(item) for item in pagination.items], pagination=pagination,
        operations=OPERATIONS, filters=filters)


@bp.get("/casos")
@admin_required
def cases():
    query = select(ClinicalCase)
    specialty = request.args.get("specialty", "")[:80]
    status = request.args.get("status", "")
    if specialty:
        query = query.where(ClinicalCase.specialty == specialty)
    if status in {"published", "draft"}:
        query = query.where(ClinicalCase.is_published.is_(status == "published"))
    pagination = db.paginate(query.order_by(ClinicalCase.created_at.desc()),
        page=max(1, request.args.get("page", 1, type=int)), per_page=20, error_out=False)
    return render_template("admin/cases.html", cases=pagination.items, specialties=SPECIALTIES,
        pagination=pagination, selected_specialty=specialty, selected_status=status)


SOURCE_STATUS_LABELS = {
    "registered": "Registrado",
    "pending": "Aguardando verificação",
    "verified": "Fonte verificada",
    "license_rejected": "Não elegível na política atual",
    "source_error": "Falha ao obter a fonte",
    "classification_error": "Classificação pendente",
    "adaptation_error": "Adaptação pendente",
    "quota_deferred": "Aguardando cota",
    "draft": "Adaptação criada",
}
SOURCE_STAGE_LABELS = {
    "verified": "Fontes verificadas",
    "translated": "Português preparado",
    "untranslated": "Português ainda não preparado",
    "draft": "Rascunhos em revisão",
    "published": "Publicados para os alunos",
}


def source_external_url(record):
    """Only turn a registry URL into an ordinary HTTP(S) link."""
    metadata = record.original_metadata if isinstance(record.original_metadata, dict) else {}
    value = metadata.get("article_link", "")
    if isinstance(value, str):
        try:
            parts = urlsplit(value)
            if parts.scheme in {"https", "http"} and parts.hostname and not parts.username and not parts.password:
                return value
        except ValueError:
            pass
    if record.pmcid and record.pmcid.startswith("PMC") and record.pmcid[3:].isdigit():
        return f"https://pmc.ncbi.nlm.nih.gov/articles/{record.pmcid}/"
    return None


@bp.get("/acervo")
@admin_required
def sources():
    from .models import SourceRecord
    from .source_registry import source_counts, verified_source_filter

    search = request.args.get("q", "").strip()[:160]
    dataset = request.args.get("dataset", "").strip()[:64]
    specialty = request.args.get("specialty", "").strip()[:80]
    stage = request.args.get("stage", "")
    import_status = request.args.get("status", "")
    if stage not in SOURCE_STAGE_LABELS:
        stage = ""
    if import_status not in SOURCE_STATUS_LABELS:
        import_status = ""
    query = select(SourceRecord).options(
        selectinload(SourceRecord.clinical_case).load_only(ClinicalCase.id, ClinicalCase.title, ClinicalCase.is_published, ClinicalCase.data)
    )
    if search:
        # Wildcards typed by an editor are literal search characters.
        escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        query = query.where(or_(SourceRecord.original_title.ilike(pattern, escape="\\"),
            SourceRecord.pmcid.ilike(pattern, escape="\\"),
            SourceRecord.original_diagnosis.ilike(pattern, escape="\\")))
    if dataset:
        query = query.where(SourceRecord.dataset == dataset)
    if specialty:
        query = query.where(SourceRecord.specialty == specialty)
    if import_status:
        query = query.where(SourceRecord.import_status == import_status)
    if stage == "verified":
        query = query.where(verified_source_filter())
    elif stage in {"translated", "untranslated"}:
        query = query.where(SourceRecord.translated.is_(stage == "translated"))
    elif stage in {"draft", "published"}:
        query = query.where(SourceRecord.clinical_case.has(ClinicalCase.is_published.is_(stage == "published")))
    filters = {"q": search, "dataset": dataset, "specialty": specialty, "stage": stage, "status": import_status}
    page = min(1_000_000, max(1, request.args.get("page", 1, type=int)))
    pagination = db.paginate(query.order_by(SourceRecord.id.asc()),
        page=page, per_page=25, error_out=False)
    if pagination.page > max(1, pagination.pages):
        return redirect(url_for("admin.sources", page=max(1, pagination.pages), **filters))
    # Counts describe the entire registry. Filters affect only the record list.
    counts = source_counts(dataset=None)
    datasets = db.session.scalars(select(SourceRecord.dataset).distinct().order_by(SourceRecord.dataset)).all()
    specialties = db.session.scalars(select(SourceRecord.specialty).where(
        SourceRecord.specialty.is_not(None), SourceRecord.specialty != "").distinct().order_by(SourceRecord.specialty)).all()
    records = [{"record": record, "source_url": source_external_url(record),
        "clinical_case": record.clinical_case} for record in pagination.items]
    return render_template("admin/sources.html", records=records, pagination=pagination,
        source_counts=counts, datasets=datasets, specialties=specialties, filters=filters,
        stage_labels=SOURCE_STAGE_LABELS, status_labels=SOURCE_STATUS_LABELS,
        filters_active=any(filters.values()))

@bp.post("/casos/gerar")
@admin_required
def generate_case():
    from .ai import AIError, AIService
    from .case_schema import CaseValidationError, validate_case
    specialty = request.form.get("specialty", "Clínica médica")
    difficulty = request.form.get("difficulty", "Intermediário")
    if specialty not in SPECIALTIES or difficulty not in {"Iniciante", "Intermediário", "Avançado"}:
        abort(400)
    if not consume_limit("generate-case", str(current_user.id), 12, 3600):
        abort(429)
    try:
        service = AIService(current_app.config)
        with capture_usage(service, "generation", user_id=current_user.id):
            data = validate_case(service.generate_case(specialty, difficulty))
        data["caseId"] = "ia-" + uuid4().hex[:16]
        data["reviewStatus"] = "review_required"
        case = ClinicalCase(slug=data["caseId"], title=data["caseTitle"], specialty=data["specialty"],
            difficulty=data["difficulty"], case_type=case_type_for(data), summary=data["summary"], data=data, is_generated=True, is_published=False)
        db.session.add(case)
        db.session.commit()
        flash("Rascunho criado. Revise o paciente, os exames e o gabarito antes de publicar.", "success")
        return redirect(url_for("admin.edit_case", case_id=case.id))
    except (AIError, CaseValidationError) as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return redirect(url_for("admin.cases"))

@bp.route("/casos/<int:case_id>", methods=["GET", "POST"])
@admin_required
def edit_case(case_id):
    from .case_schema import CaseValidationError, validate_case
    case = db.get_or_404(ClinicalCase, case_id)
    if request.method == "POST":
        data = copy.deepcopy(case.data)
        data["caseTitle"] = request.form.get("title", "").strip()
        data["summary"] = request.form.get("summary", "").strip()
        data["caseType"] = request.form.get("case_type", case_type_for(data))
        data["treatments"] = request.form.get("treatments", "").strip()
        data["diagnoses"]["mainDiagnosis"]["disease"] = request.form.get("main_diagnosis", "").strip()
        for field in ("chief_complaint", "present_illness_history", "past_medical_history", "personal_history", "family_history", "other_medical_history", "allergy_history"):
            data["patientProfile"][field] = request.form.get(field, "").strip()
        for category in ("physicalExams", "auxiliaryLabs"):
            for index, name in enumerate(data.get(category, {})):
                data[category][name] = request.form.get(f"{category}_{index}", data[category][name]).strip()
        for key, section in data.get("rubric", {}).items():
            if isinstance(section, dict):
                for field in ("criteria", "criticalActions"):
                    form_key = f"rubric_{key}_{field}"
                    if form_key in request.form:
                        section[field] = [s.strip() for s in request.form[form_key].splitlines() if s.strip()]
        try:
            data = validate_case(data)
            data.pop("useAuthorization", None)
            case.data, case.title, case.summary = data, data["caseTitle"], data["summary"]
            case.case_type = case_type_for(data)
            case.is_published = False
            db.session.commit()
            flash("Alterações salvas. Confirme a revisão para publicar esta versão.", "success")
            return redirect(url_for("admin.edit_case", case_id=case.id))
        except CaseValidationError as exc:
            db.session.rollback()
            flash(str(exc), "error")
    return render_template("admin/case.html", case=case, data=case.data, case_types=CASE_TYPES)

@bp.post("/casos/<int:case_id>/publicar")
@admin_required
def publish_case(case_id):
    from .case_schema import CaseValidationError, is_nonhuman_case, source_license_allowed, validate_case
    case = db.get_or_404(ClinicalCase, case_id)
    if case.is_published:
        case.is_published = False
        db.session.commit()
        flash("Caso retirado do sorteio. Os históricos foram preservados.", "success")
    else:
        if request.form.get("review_confirmed") != "yes":
            flash("Confirme que revisou o caso e o gabarito antes de publicar.", "error")
            return redirect(url_for("admin.edit_case", case_id=case.id))
        try:
            data = validate_case(case.data)
        except CaseValidationError as exc:
            flash(str(exc), "error")
            return redirect(url_for("admin.edit_case", case_id=case.id))
        if is_nonhuman_case(data):
            flash("Relatos de animais permanecem no acervo privado, separados da prática clínica humana.", "error")
            return redirect(url_for("admin.edit_case", case_id=case.id))
        if data.get("catalogType") == "published_case" and not source_license_allowed(
            data.get("provenance"), current_app.config.get("CASE_REUSE_SCOPE", "commercial"),
            current_app.config.get("CASE_ALLOW_CLINICAL_FACTS", False)
        ):
            flash("A origem deste caso não atende à política de reutilização atual. Confira a licença e a configuração de preparação factual antes de publicar.", "error")
            return redirect(url_for("admin.edit_case", case_id=case.id))
        data.pop("useAuthorization", None)
        data["reviewStatus"] = "reviewed"
        case.data, case.is_published = data, True
        case.case_type = case_type_for(data)
        db.session.commit()
        flash("Caso publicado e disponível para os alunos.", "success")
    return redirect(url_for("admin.edit_case", case_id=case.id))


@bp.post("/casos/<int:case_id>/uso-provisorio")
@admin_required
def provisional_case(case_id):
    """Availability authorized by the owner, separately from clinical approval."""
    from .case_schema import CaseValidationError, source_license_allowed
    from .local_case_integration import authorize_provisional_use
    case = db.get_or_404(ClinicalCase, case_id)
    if request.form.get("provisional_confirmed") != "yes":
        flash("Confirme a disponibilização com revisão clínica pendente.", "error")
        return redirect(url_for("admin.edit_case", case_id=case.id))
    try:
        data = authorize_provisional_use(case.data,
            "Autorização explícita do administrador para disponibilização antes da revisão clínica.")
        if not source_license_allowed(data["provenance"], current_app.config.get("CASE_REUSE_SCOPE", "commercial"),
                                     current_app.config.get("CASE_ALLOW_CLINICAL_FACTS", False)):
            raise ValueError("A fonte não atende à política de reutilização atual.")
        case.data, case.is_published = data, True
        db.session.commit()
        flash("Caso disponível por autorização provisória. A revisão clínica continua pendente.", "success")
    except (CaseValidationError, ValueError) as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("admin.edit_case", case_id=case.id))

@bp.get("/usuarios")
@admin_required
def users():
    page = max(1, request.args.get("page", 1, type=int))
    pagination = db.paginate(select(User).order_by(User.created_at.desc()), page=page, per_page=30, error_out=False)
    return render_template("admin/users.html", users=pagination.items, pagination=pagination)

@bp.post("/usuarios/<int:user_id>/status")
@admin_required
def user_status(user_id):
    user = db.get_or_404(User, user_id)
    if user.id == current_user.id or user.is_admin:
        flash("Administradores são gerenciados pelo terminal da hospedagem.", "info")
    else:
        user.is_active = not user.is_active
        db.session.commit()
        flash("Acesso do usuário atualizado.", "success")
    return redirect(url_for("admin.users"))
