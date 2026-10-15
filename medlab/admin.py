import copy
from uuid import uuid4
from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_user, logout_user
from sqlalchemy import func, select
from .extensions import db
from .models import ClinicalCase, Consultation, User
from .security import admin_required, consume_limit, login_rate_allowed

bp = Blueprint("admin", __name__)
SPECIALTIES = ["Clínica médica", "Cardiologia", "Pneumologia", "Gastroenterologia", "Neurologia", "Pediatria", "Saúde da mulher", "Saúde mental", "Medicina de família"]

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
                           model=current_app.config["AI_MODEL"], key_configured=bool(current_app.config["DEEPSEEK_API_KEY"]))

@bp.get("/casos")
@admin_required
def cases():
    found = db.session.execute(select(ClinicalCase).order_by(ClinicalCase.created_at.desc())).scalars().all()
    return render_template("admin/cases.html", cases=found, specialties=SPECIALTIES)

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
        data = validate_case(AIService(current_app.config).generate_case(specialty, difficulty))
        data["caseId"] = "ia-" + uuid4().hex[:16]
        data["reviewStatus"] = "review_required"
        case = ClinicalCase(slug=data["caseId"], title=data["caseTitle"], specialty=data["specialty"],
            difficulty=data["difficulty"], summary=data["summary"], data=data, is_generated=True, is_published=False)
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
            case.data, case.title, case.summary = data, data["caseTitle"], data["summary"]
            case.is_published = False
            db.session.commit()
            flash("Alterações salvas. Confirme a revisão para publicar esta versão.", "success")
            return redirect(url_for("admin.edit_case", case_id=case.id))
        except CaseValidationError as exc:
            db.session.rollback()
            flash(str(exc), "error")
    return render_template("admin/case.html", case=case, data=case.data)

@bp.post("/casos/<int:case_id>/publicar")
@admin_required
def publish_case(case_id):
    from .case_schema import CaseValidationError, validate_case
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
        data["reviewStatus"] = "reviewed"
        case.data, case.is_published = data, True
        db.session.commit()
        flash("Caso publicado e disponível para os alunos.", "success")
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
