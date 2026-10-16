import copy
import math
import re
import secrets
from datetime import timedelta
from uuid import uuid4

from flask import Blueprint, Response, abort, current_app, flash, jsonify, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy import case as sql_case, func, or_, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from .extensions import db
from .models import ClinicalCase, Consultation, ConsultationSimulation, PerformedExam, Turn, User, utcnow
from .security import api_login_required, consume_limit, login_rate_allowed, safe_next
from .catalogue import filtered_cases, catalogue_facets, public_summary, public_title
from .case_schema import SECTION_TITLES, UNKNOWN_FACT
from .usage import capture_usage
from .exam_media import exam_media_items, read_exam_media

bp = Blueprint("web", __name__)


def get_ai_service():
    from .ai import AIService
    return AIService(current_app.config)


def public_case(case):
    from .discovery import available_image_count
    metadata = case.discovery
    kind = metadata.case_type if metadata else case.case_type
    return {"id": case.id, "title": public_title(case.id), "specialty": case.specialty,
            "difficulty": metadata.difficulty if metadata else case.difficulty,
            "difficulty_estimated": metadata.difficulty_estimated if metadata else False,
            "classification_note": metadata.classification_note if metadata else "",
            "summary": public_summary(kind), "case_type": kind,
            "case_types": metadata.presentation_tags.strip("|").split("|") if metadata else [kind],
            "exam_result_count": metadata.exam_result_count if metadata else 0,
            "exam_image_count": available_image_count(case.data) if metadata and metadata.exam_image_count else 0,
            "estimated_minutes": 15, "generated": case.is_generated}


def public_session(training):
    case = training.case_snapshot
    return {"id": training.id, "title": public_title(training.case_id),
            "patient_name": case.get("patientProfile", {}).get("name", "Paciente"),
            "specialty": case.get("specialty", "Clínica médica"), "status": training.status,
            "status_label": "Concluída" if training.status == "completed" else "Em andamento",
            "created_at": training.created_at, "score": training.score,
            "diagnosis": training.diagnosis or "", "differential": training.differential or "",
            "treatment": training.treatment or "",
            "url": url_for("web.consultation", session_id=training.id),
            "result_url": url_for("web.result", session_id=training.id)}


def simulation_context(training):
    record = db.session.get(ConsultationSimulation, training.id)
    return copy.deepcopy(record.payload) if record is not None else None


def score_breakdown(evaluation, persisted_score):
    """Explain saved scores without changing or repairing a historical result."""
    def valid_score(value):
        return type(value) in (int, float) and 0 <= value <= 100 and math.isfinite(value)

    def display(value):
        return format(value, ".12g").replace(".", ",") if valid_score(value) else None

    evaluation = evaluation if isinstance(evaluation, dict) else {}
    sections = evaluation.get("sections")
    sections = sections if isinstance(sections, list) else []
    known = {key: [] for key in SECTION_TITLES}
    malformed = False
    for section in sections:
        if not isinstance(section, dict) or not isinstance(section.get("key"), str) or section["key"] not in known:
            malformed = True
            continue
        known[section["key"]].append(section)
    dimensions, scores = [], []
    for key, title in SECTION_TITLES.items():
        if key == "overall":
            continue
        row = {"key": key, "title": title, "status": "unavailable", "score": None, "score_display": None}
        if len(known[key]) == 1:
            section = known[key][0]
            assessable, value = section.get("assessable", True), section.get("score")
            if assessable is False and value is None:
                row["status"] = "excluded"
            elif assessable is True and valid_score(value):
                row.update(status="included", score=value, score_display=display(value))
                scores.append(value)
        dimensions.append(row)
    complete = all(row["status"] != "unavailable" for row in dimensions)
    total = sum(scores) if complete and scores else None
    average = total / len(scores) if total is not None else None
    calculated = round(average) if average is not None else None
    saved, recorded = evaluation.get("score"), persisted_score
    overall = known["overall"][0] if len(known["overall"]) == 1 else None
    # Legacy records may lack keys, repeat dimensions or disagree with the
    # history column. Display the discrepancy; never silently choose a score.
    consistent = (complete and not malformed and overall is not None
        and calculated is not None and valid_score(saved) and valid_score(recorded)
        and valid_score(overall.get("score"))
        and saved == recorded == overall["score"] == calculated)
    unscored = (complete and not scores and not malformed and saved is None and recorded is None
        and overall is not None and overall.get("score") is None)
    return {"dimensions": dimensions, "anamnese": dimensions[0],
        "divisor": len(scores) if complete else None, "sum": total,
        "sum_display": format(total, ".12g").replace(".", ",") if total is not None else None,
        "terms": " + ".join(row["score_display"] for row in dimensions if row["status"] == "included"),
        "calculated_score": calculated, "rounded": average is not None and average != calculated,
        "saved_score_display": display(saved), "history_score_display": display(recorded),
        "consistent": consistent, "unscored": unscored,
        "warning": not consistent and not unscored}


def own_session(session_id):
    training = db.session.execute(select(Consultation).where(
        Consultation.id == session_id, Consultation.user_id == current_user.id
    )).scalar_one_or_none()
    if not training:
        abort(404)
    return training


def acquire_lock(training, *, status="active"):
    token = str(uuid4())
    stale = utcnow() - timedelta(minutes=3)
    result = db.session.execute(update(Consultation).where(
        Consultation.id == training.id, Consultation.status == status,
        or_(Consultation.busy_since.is_(None), Consultation.busy_since < stale)
    ).values(busy_since=utcnow(), lock_token=token))
    db.session.commit()
    return token if result.rowcount else None


def release_lock(session_id, token):
    db.session.execute(update(Consultation).where(
        Consultation.id == session_id, Consultation.lock_token == token
    ).values(busy_since=None, lock_token=None))
    db.session.commit()


def lock_is_owned(session_id, token, *, status="active"):
    result = db.session.execute(update(Consultation).where(
        Consultation.id == session_id, Consultation.lock_token == token, Consultation.status == status
    ).values(busy_since=utcnow()))
    return bool(result.rowcount)


def ai_quota():
    return consume_limit("ai-daily", str(current_user.id), current_app.config["MAX_DAILY_AI_REQUESTS"], 86400)


@bp.get("/")
def landing():
    return render_template("landing.html")


@bp.route("/cadastro", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("web.dashboard"))
    errors = {}
    if request.method == "POST":
        if not consume_limit("register", request.remote_addr or "unknown", 8, 3600):
            abort(429)
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        if not 2 <= len(name) <= 100:
            errors["name"] = "Informe seu nome, entre 2 e 100 caracteres."
        if len(email) > 254 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
            errors["email"] = "Informe um e-mail válido."
        if not 10 <= len(password) <= 128:
            errors["password"] = "Use uma senha de 10 a 128 caracteres."
        if password != request.form.get("password_confirm", ""):
            errors["password_confirm"] = "As senhas precisam ser iguais."
        if not errors:
            user = User(name=name, email=email)
            user.set_password(password)
            try:
                db.session.add(user)
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                errors["email"] = "Este e-mail já tem cadastro. Acesse sua conta."
            else:
                flash("Sua conta está pronta. Entre para começar a praticar.", "success")
                return redirect(url_for("web.login"))
    return render_template("auth.html", mode="register", errors=errors), (400 if errors else 200)


@bp.route("/entrar", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("web.dashboard"))
    errors = {}
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()[:254]
        if not login_rate_allowed(email):
            abort(429)
        user = db.session.execute(select(User).where(User.email == email)).scalar_one_or_none()
        password = request.form.get("password", "")[:128]
        if user and user.is_active and user.check_password(password):
            session.clear()
            login_user(user, remember=False)
            return redirect(safe_next(request.args.get("next")))
        errors["login"] = "E-mail ou senha incorretos. Confira seus dados."
    return render_template("auth.html", mode="login", errors=errors), (400 if errors else 200)


@bp.post("/sair")
@login_required
def logout():
    logout_user()
    session.clear()
    flash("Você saiu da sua conta.", "info")
    return redirect(url_for("web.landing"))


@bp.get("/painel")
@login_required
def dashboard():
    scored = (Consultation.status == "completed") & Consultation.score.is_not(None)
    total, completed, active, average = db.session.execute(select(
        func.count(Consultation.id), func.sum(sql_case((Consultation.status == "completed", 1), else_=0)),
        func.sum(sql_case((Consultation.status == "active", 1), else_=0)),
        func.avg(sql_case((scored, Consultation.score), else_=None))
    ).where(Consultation.user_id == current_user.id)).one()
    trainings = db.session.execute(select(Consultation).where(Consultation.user_id == current_user.id)
                                  .order_by(Consultation.created_at.desc(), Consultation.id.desc()).limit(5)).scalars().all()
    cases = db.session.execute(select(ClinicalCase).where(ClinicalCase.is_published.is_(True)).limit(3)).scalars().all()
    return render_template("dashboard.html", stats={"completed": completed or 0,
        "active": active or 0, "total": total,
        "average_score": round(average) if average is not None else None},
        recent_sessions=[public_session(t) for t in trainings], recommended_cases=[public_case(c) for c in cases])


@bp.get("/casos")
@login_required
def cases():
    query, filters = filtered_cases(request.args)
    filters.update(catalogue_facets(request.args))
    specialties = db.session.execute(select(ClinicalCase.specialty).where(ClinicalCase.is_published.is_(True)).distinct()).scalars().all()
    pagination = db.paginate(query.order_by(ClinicalCase.id), page=max(1, request.args.get("page", 1, type=int)),
                             per_page=12, error_out=False)
    total = db.session.scalar(select(func.count(ClinicalCase.id)).where(ClinicalCase.is_published.is_(True)))
    return render_template("cases.html", cases=[public_case(c) for c in pagination.items],
        specialties=sorted(specialties), pagination=pagination, total_catalog=total,
        specialty_count=len(specialties), **filters)


@bp.post("/casos/iniciar")
@login_required
def start_session():
    if not consume_limit("session-start", str(current_user.id), 30, 86400):
        abort(429)
    if not current_app.config["DEEPSEEK_API_KEY"] and not current_app.config["DEMO_MODE"]:
        flash("A prática estará disponível assim que a equipe configurar a inteligência artificial.", "info")
        return redirect(url_for("web.cases"))
    case_id = request.form.get("case_id", type=int)
    if case_id:
        query = select(ClinicalCase).where(ClinicalCase.is_published.is_(True), ClinicalCase.id == case_id)
    else:
        query, _ = filtered_cases(request.form)
    # Fetch identifiers only: avoid loading thousands of private clinical documents to draw one case.
    available = db.session.execute(query.with_only_columns(ClinicalCase.id)).scalars().all()
    if not available:
        flash("Não há casos disponíveis para estes filtros.", "info")
        # Keep the actual selection so the student can adjust one filter.
        return redirect(url_for("web.cases", **{key: request.form[key] for key in
            ("specialty", "case_type", "difficulty", "exams", "q") if request.form.get(key)}))
    recent_ids = set(db.session.execute(select(Consultation.case_id).where(Consultation.user_id == current_user.id)
                                       .order_by(Consultation.created_at.desc()).limit(5)).scalars().all())
    candidates = [identifier for identifier in available if identifier not in recent_ids] or available
    case = db.session.get(ClinicalCase, secrets.choice(candidates))
    training = Consultation(user_id=current_user.id, case_id=case.id, case_snapshot=copy.deepcopy(case.data))
    db.session.add(training)
    db.session.commit()
    return redirect(url_for("web.consultation", session_id=training.id))


@bp.get("/historico")
@login_required
def history():
    page = max(1, request.args.get("page", 1, type=int))
    pagination = db.paginate(select(Consultation).where(Consultation.user_id == current_user.id)
                            .order_by(Consultation.created_at.desc()), page=page, per_page=20, error_out=False)
    return render_template("history.html", sessions=[public_session(t) for t in pagination.items], pagination=pagination)


@bp.get("/sessao/<session_id>")
@login_required
def consultation(session_id):
    from .discovery import result_entries
    training = own_session(session_id)
    if training.status == "completed":
        return redirect(url_for("web.result", session_id=training.id))
    case = training.case_snapshot
    profile = case.get("patientProfile", {})
    occupation = profile.get("occupation", "")
    if not occupation or not occupation.strip() or occupation.strip() == UNKNOWN_FACT:
        occupation = ""
    respondent = profile.get("respondent", "")
    if not respondent or not respondent.strip() or respondent.strip() == UNKNOWN_FACT:
        respondent = ""
    performed = {(e.exam_type, e.name): e.result for e in training.exams}
    catalog = lambda key, kind: [{"name": name, "type": kind, "result": performed.get((kind, name)),
        "media": exam_media_items(training, exam_name=name, exam_type=kind) if (kind, name) in performed else []}
        for name in case.get(key, {}) if name in result_entries(case, key) or (kind, name) in performed]
    return render_template("consultation.html", training=public_session(training), patient={
        "name": profile.get("name", "Paciente"), "age": profile.get("age_value", ""),
        "age_unit": profile.get("age_unit", "anos"),
        "gender": profile.get("gender", ""), "occupation": occupation, "respondent": respondent,
        "chief_complaint": profile.get("chief_complaint", ""),
        "initials": "".join(w[0] for w in profile.get("name", "Paciente").split()[:2])},
        turns=[{"id": t.id, "question": t.question, "answer": t.answer, "intent": t.intent} for t in training.turns],
        review_pending=case.get("reviewStatus") == "review_required",
        physical_exams=catalog("physicalExams", "physical_exam"), auxiliary_exams=catalog("auxiliaryLabs", "auxiliary_exam"),
        performed_exams=[{"name": e.name, "result": e.result, "type": e.exam_type} for e in training.exams])


@bp.post("/sessao/<session_id>/mensagem")
@api_login_required
def message(session_id):
    from .ai import AIError
    from .patient_dialogue import PatientDialogueError, validate_simulation_state
    training = own_session(session_id)
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify(error="Envie uma pergunta válida."), 400
    question, request_id = payload.get("question"), payload.get("request_id")
    if not isinstance(question, str) or not 2 <= len(question.strip()) <= 2000:
        return jsonify(error="Escreva uma pergunta entre 2 e 2.000 caracteres."), 400
    if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", request_id):
        return jsonify(error="Identificador da pergunta inválido. Atualize a página."), 400
    existing = db.session.execute(select(Turn).where(Turn.consultation_id == session_id, Turn.request_id == request_id)).scalar_one_or_none()
    if existing:
        if existing.question != question.strip():
            return jsonify(error="Esta pergunta já foi enviada com outro conteúdo."), 409
        return jsonify(answer=existing.answer, intent=existing.intent, turn_id=existing.id, turn_count=len(training.turns))
    if training.status != "active":
        return jsonify(error="Esta consulta já foi concluída."), 409
    if len(training.turns) >= current_app.config["MAX_SESSION_TURNS"]:
        return jsonify(error="Você atingiu o limite de 40 perguntas. Finalize a consulta para receber sua avaliação."), 400
    token = acquire_lock(training)
    if not token:
        return jsonify(error="Uma ação está em andamento. Aguarde a conclusão."), 409
    try:
        if not ai_quota():
            return jsonify(error="Seu limite diário de interações foi atingido. Retome amanhã."), 429
        # Recheck after the lock to serialize both question limits and idempotency.
        db.session.refresh(training)
        existing = db.session.execute(select(Turn).where(Turn.consultation_id == session_id, Turn.request_id == request_id)).scalar_one_or_none()
        if existing:
            return jsonify(answer=existing.answer, intent=existing.intent, turn_id=existing.id, turn_count=len(training.turns))
        if len(training.turns) >= current_app.config["MAX_SESSION_TURNS"]:
            return jsonify(error="Limite de perguntas atingido. Finalize a consulta."), 400
        history = [{"question": t.question, "answer": t.answer, "intentClassification": t.intent} for t in training.turns]
        case_data = copy.deepcopy(training.case_snapshot)
        previous_state = simulation_context(training)
        db.session.commit()
        service = get_ai_service()
        with capture_usage(service, "conversation", user_id=current_user.id, consultation_id=session_id):
            response = service.respond(case_data, question.strip(), history,
                                       simulation_state=copy.deepcopy(previous_state))
            if response.get("simulation_state") is None:
                raise AIError("Não foi possível preservar os detalhes da simulação. Tente novamente.")
            try:
                next_state = validate_simulation_state(response["simulation_state"], len(history) + 1)
            except PatientDialogueError:
                raise AIError("Os detalhes da simulação não puderam ser confirmados. Tente novamente.") from None
            # Preserve the accepted session history even if a provider response
            # tries to rewrite it. New details belong only to this new turn.
            previous_details = previous_state["details"] if previous_state is not None else []
            expected_start = previous_state["startedAfterTurn"] if previous_state is not None else len(history)
            if (next_state["startedAfterTurn"] != expected_start
                    or next_state["details"][:len(previous_details)] != previous_details
                    or any(item["turn"] != len(history) + 1 for item in next_state["details"][len(previous_details):])):
                raise AIError("A simulação tentou alterar detalhes de uma resposta anterior. Tente novamente.")
        if not lock_is_owned(session_id, token):
            db.session.rollback()
            return jsonify(error="A solicitação expirou. Atualize a página antes de tentar novamente."), 409
        turn = Turn(consultation_id=session_id, question=question.strip(), answer=response["answer"],
                    intent=response["intent"], request_id=request_id)
        db.session.add(turn)
        db.session.flush()
        simulation = db.session.get(ConsultationSimulation, session_id)
        if simulation is None:
            simulation = ConsultationSimulation(consultation_id=session_id, payload=next_state)
            db.session.add(simulation)
        else:
            # Assign a whole new JSON value; nested mutation is not tracked.
            simulation.payload = copy.deepcopy(next_state)
        db.session.commit()
        return jsonify(answer=response["answer"], intent=response["intent"], turn_id=turn.id, turn_count=len(history) + 1)
    except AIError as exc:
        db.session.rollback()
        return jsonify(error=str(exc)), 503
    except SQLAlchemyError:
        db.session.rollback()
        return jsonify(error="Não foi possível salvar esta resposta. Sua consulta foi preservada; tente novamente."), 503
    finally:
        release_lock(session_id, token)


def get_speech_service():
    from .speech import SpeechService
    return SpeechService(current_app.config)


@bp.get("/sessao/<session_id>/voz/config")
@api_login_required
def voice_configuration(session_id):
    training = own_session(session_id)
    return jsonify(get_speech_service().configuration(training.case_snapshot))


@bp.post("/sessao/<session_id>/voz/falar")
@api_login_required
def synthesize_audio(session_id):
    from .speech import voice_options
    training = own_session(session_id)
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or type(payload.get("turn_id")) is not int:
        return jsonify(error="Selecione uma resposta desta consulta para ouvir."), 400
    turn = db.session.scalar(select(Turn).where(Turn.id == payload["turn_id"], Turn.consultation_id == training.id))
    if turn is None:
        abort(404)
    voice_id = payload.get("voice_id")
    if not isinstance(voice_id, str) or voice_id not in {v["id"] for v in voice_options(training.case_snapshot)["voices"]}:
        return jsonify(error="Escolha uma voz compatível com este paciente."), 400
    service = get_speech_service()
    if not service.available:
        return jsonify(error=service.configuration(training.case_snapshot)["reason"]), 503
    # Only the already saved answer leaves the server. Inference and audio stay
    # in the student's browser; caller-supplied text is deliberately ignored.
    return jsonify(text=turn.answer, voice_id=voice_id)



@bp.post("/sessao/<session_id>/exame")
@api_login_required
def exam(session_id):
    from .discovery import usable_text
    training = own_session(session_id)
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify(error="Exame inválido."), 400
    name, kind = payload.get("name"), payload.get("type")
    if not isinstance(name, str) or kind not in {"physical_exam", "auxiliary_exam"}:
        return jsonify(error="Exame inválido."), 400
    catalog = training.case_snapshot.get("physicalExams" if kind == "physical_exam" else "auxiliaryLabs", {})
    if name not in catalog:
        return jsonify(error="Este exame não está disponível neste caso."), 400
    if not usable_text(catalog[name]):
        return jsonify(error="A fonte deste caso não oferece um resultado utilizável para este exame."), 400
    if training.status != "active":
        return jsonify(error="Esta consulta já foi concluída."), 409
    token = acquire_lock(training)
    if not token:
        return jsonify(error="Aguarde a ação em andamento."), 409
    try:
        found = db.session.execute(select(PerformedExam).where(PerformedExam.consultation_id == session_id,
            PerformedExam.name == name, PerformedExam.exam_type == kind)).scalar_one_or_none()
        if not found:
            db.session.add(PerformedExam(consultation_id=session_id, name=name, exam_type=kind, result=catalog[name]))
            db.session.commit()
        db.session.expire(training, ["exams"])
        return jsonify(name=name, type=kind, result=catalog[name],
                       media=exam_media_items(training, exam_name=name, exam_type=kind))
    finally:
        release_lock(session_id, token)


@bp.get("/sessao/<session_id>/imagem/<asset_id>")
@login_required
def exam_image(session_id, asset_id):
    training = own_session(session_id)
    asset = read_exam_media(training, asset_id)
    if asset is None:
        abort(404)
    # The response has no source filename, redirects or shared-cache validators.
    return Response(asset["data"], mimetype=asset["mime_type"], headers={
        "Cache-Control": "private, no-store", "Content-Disposition": "inline",
        "Cross-Origin-Resource-Policy": "same-origin", "X-Content-Type-Options": "nosniff"})


@bp.post("/sessao/<session_id>/finalizar")
@login_required
def finish(session_id):
    from .ai import AIError
    training = own_session(session_id)
    if training.status == "completed":
        return redirect(url_for("web.result", session_id=session_id))
    diagnosis = request.form.get("diagnosis", "").strip()
    differential = request.form.get("differential", "").strip()
    treatment = request.form.get("treatment", "").strip()
    if not 3 <= len(diagnosis) <= 2000 or not 10 <= len(treatment) <= 5000 or len(differential) > 2000:
        flash("Informe sua hipótese (3 a 2.000 caracteres) e conduta (10 a 5.000 caracteres).", "error")
        return redirect(url_for("web.consultation", session_id=session_id))
    if not training.turns:
        flash("Converse com o paciente antes de concluir a consulta.", "info")
        return redirect(url_for("web.consultation", session_id=session_id))
    token = acquire_lock(training)
    if not token:
        flash("Uma ação está em andamento. Aguarde antes de finalizar.", "info")
        return redirect(url_for("web.consultation", session_id=session_id))
    try:
        if not ai_quota():
            flash("Seu limite diário foi atingido. Sua consulta está salva; você pode concluí-la amanhã.", "info")
            return redirect(url_for("web.consultation", session_id=session_id))
        db.session.refresh(training)
        history = [{"question": t.question, "answer": t.answer, "intentClassification": t.intent} for t in training.turns]
        session_data = {"sessionId": training.id, "caseId": training.case_snapshot.get("caseId"), "history": history,
            "performedExams": [{"itemName": e.name, "result": e.result, "examType": e.exam_type} for e in training.exams],
            "userSubmissions": [{"data": {"mainDiagnoses": [{"diagnosisName": diagnosis}],
                "differentialDiagnoses": [{"diagnosisName": differential}] if differential else []}}],
            "userTreatments": [{"data": {"treatmentPlan": treatment}}]}
        context = simulation_context(training)
        if context is not None:
            session_data["simulationContext"] = context
        case_data = copy.deepcopy(training.case_snapshot)
        # Save the learner's draft before an external request so a failed evaluation loses no reasoning.
        training.diagnosis, training.differential, training.treatment = diagnosis, differential, treatment
        db.session.commit()
        service = get_ai_service()
        with capture_usage(service, "evaluation", user_id=current_user.id, consultation_id=session_id):
            evaluation = service.evaluate(case_data, session_data)
        if not lock_is_owned(session_id, token):
            db.session.rollback()
            flash("A avaliação expirou. Sua consulta permanece salva; tente novamente.", "error")
            return redirect(url_for("web.consultation", session_id=session_id))
        training = db.session.get(Consultation, session_id)
        training.evaluation, training.score = evaluation, evaluation["score"]
        training.status, training.completed_at = "completed", utcnow()
        db.session.commit()
        return redirect(url_for("web.result", session_id=session_id))
    except AIError as exc:
        db.session.rollback()
        flash(str(exc) + " Sua consulta foi preservada para tentar novamente.", "error")
        return redirect(url_for("web.consultation", session_id=session_id))
    finally:
        release_lock(session_id, token)


@bp.post("/api/sessoes/<session_id>/parecer")
@api_login_required
def teaching_review(session_id):
    """Generate one saved debrief in a separate request; grades are immutable."""
    from .ai import AIError
    from .teaching_review import build_teaching_review

    training = own_session(session_id)
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or payload:
        return jsonify(error="O parecer usa somente os registros salvos desta consulta."), 400
    if training.status != "completed" or not isinstance(training.evaluation, dict):
        return jsonify(error="Conclua a consulta para receber o parecer pedagógico."), 409
    existing = training.evaluation.get("learningReview")
    if isinstance(existing, dict) and existing.get("version") == 1:
        return jsonify(learningReview=existing)
    if current_app.config["DEMO_MODE"]:
        return jsonify(error="O parecer detalhado precisa da DeepSeek. O modo demonstrativo não realiza análise clínica."), 503
    token = acquire_lock(training, status="completed")
    if not token:
        return jsonify(error="O parecer desta consulta está sendo preparado. Aguarde e tente novamente."), 409
    try:
        db.session.refresh(training)
        existing = training.evaluation.get("learningReview")
        if isinstance(existing, dict) and existing.get("version") == 1:
            return jsonify(learningReview=existing)
        if not consume_limit("teaching-review", f"{current_user.id}:{session_id}", 4, 3600) or not ai_quota():
            return jsonify(error="O limite de solicitações foi atingido. Suas notas e sua consulta continuam salvas."), 429
        case_data = copy.deepcopy(training.case_snapshot)
        evaluation = copy.deepcopy(training.evaluation)
        session_data = {
            "history": [{"question": turn.question, "answer": turn.answer,
                         "intentClassification": turn.intent} for turn in training.turns],
            "performedExams": [{"itemName": exam.name, "result": exam.result,
                                "examType": exam.exam_type} for exam in training.exams],
            "userSubmissions": [{"data": {
                "mainDiagnoses": [{"diagnosisName": training.diagnosis or ""}],
                "differentialDiagnoses": [{"diagnosisName": training.differential}] if training.differential else []}}],
            "userTreatments": [{"data": {"treatmentPlan": training.treatment or ""}}],
        }
        context = simulation_context(training)
        if context is not None:
            session_data["simulationContext"] = context
        db.session.commit()
        service = get_ai_service()
        with capture_usage(service, "teaching", user_id=current_user.id, consultation_id=session_id):
            review = build_teaching_review(service, case_data, session_data, evaluation)
        if not lock_is_owned(session_id, token, status="completed"):
            db.session.rollback()
            return jsonify(error="A preparação do parecer expirou. Suas notas foram preservadas; tente novamente."), 409
        db.session.refresh(training)
        updated = copy.deepcopy(training.evaluation)
        updated["learningReview"] = review
        training.evaluation = updated
        db.session.commit()
        return jsonify(learningReview=review)
    except AIError as exc:
        db.session.rollback()
        return jsonify(error=str(exc) + " Suas notas continuam salvas."), 503
    except SQLAlchemyError:
        db.session.rollback()
        return jsonify(error="Não foi possível salvar o parecer. Suas notas foram preservadas; tente novamente."), 503
    finally:
        try:
            release_lock(session_id, token)
        except SQLAlchemyError:
            db.session.rollback()
            current_app.logger.warning("Não foi possível liberar o bloqueio do parecer pedagógico.")


@bp.get("/sessao/<session_id>/resultado")
@login_required
def result(session_id):
    training = own_session(session_id)
    if training.status != "completed":
        return redirect(url_for("web.consultation", session_id=session_id))
    return render_template("result.html", training=public_session(training), evaluation=training.evaluation,
        score_breakdown=score_breakdown(training.evaluation, training.score),
        reference=training.case_snapshot, turns=training.turns, simulation_context=simulation_context(training),
        case_images=exam_media_items(training, include_all=True),
        submission={"diagnosis": training.diagnosis, "differential": training.differential, "treatment": training.treatment},
        performed_exams=[{"name": e.name, "result": e.result, "type": e.exam_type} for e in training.exams])


@bp.get("/privacidade")
def privacy():
    return render_template("privacy.html")


@bp.get("/saude")
def health():
    return jsonify(status="ok", service="BayesCore MedRounds")
