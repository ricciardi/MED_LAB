import hashlib
import hmac
from datetime import datetime, timezone
from uuid import uuid4

from flask import current_app
from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from .extensions import db


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(254), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    is_admin = db.Column(db.Boolean, default=False, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password, method="pbkdf2:sha256:600000")

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def get_id(self):
        # Changing a password invalidates existing signed sessions immediately.
        fingerprint = hmac.new(str(current_app.secret_key).encode(), self.password_hash.encode(), hashlib.sha256).hexdigest()
        return f"{self.id}:{fingerprint}"


class ClinicalCase(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    slug = db.Column(db.String(100), unique=True, nullable=False)
    title = db.Column(db.String(180), nullable=False)
    specialty = db.Column(db.String(80), nullable=False)
    case_type = db.Column(db.String(80), nullable=False, default="Primeira consulta", server_default="Primeira consulta", index=True)
    difficulty = db.Column(db.String(30), nullable=False)
    summary = db.Column(db.Text, nullable=False)
    data = db.Column(db.JSON, nullable=False)
    is_published = db.Column(db.Boolean, default=False, nullable=False, index=True)
    is_generated = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)
    discovery = db.relationship("CaseDiscovery", uselist=False, lazy="selectin",
                                cascade="all, delete-orphan", passive_deletes=True)


class CaseDiscovery(db.Model):
    """Rebuildable public metadata, separate from source facts and saved consultations."""
    case_id = db.Column(db.Integer, db.ForeignKey("clinical_case.id", ondelete="CASCADE"), primary_key=True)
    version = db.Column(db.String(40), nullable=False)
    source_digest = db.Column(db.String(64), nullable=False)
    case_type = db.Column(db.String(80), nullable=False, index=True)
    presentation_tags = db.Column(db.String(1024), nullable=False)
    difficulty = db.Column(db.String(30), nullable=False, index=True)
    difficulty_estimated = db.Column(db.Boolean, nullable=False)
    classification_note = db.Column(db.String(500), nullable=False)
    learning_track = db.Column(db.String(30), nullable=False, default="", server_default="", index=True)
    body_system = db.Column(db.String(30), nullable=False, default="", server_default="", index=True)
    search_text = db.Column(db.String(1600), nullable=False)
    exam_result_count = db.Column(db.Integer, nullable=False, default=0, index=True)
    physical_result_count = db.Column(db.Integer, nullable=False, default=0)
    exam_image_count = db.Column(db.Integer, nullable=False, default=0, index=True)


class SourceRecord(db.Model):
    """Private source inventory, never a student-facing clinical case.

    Full articles stay in the private source files. Original titles/diagnoses
    must only be displayed in authorized administration views.
    """
    id = db.Column(db.Integer, primary_key=True)
    dataset = db.Column(db.String(64), nullable=False, index=True)
    pmcid = db.Column(db.String(16), nullable=False, index=True)
    dataset_revision = db.Column(db.String(64), nullable=False)
    dataset_url = db.Column(db.String(512), nullable=False)
    source_file_sha256 = db.Column(db.String(64), nullable=False)
    record_sha256 = db.Column(db.String(64), nullable=False)
    source_row_number = db.Column(db.Integer, nullable=False)
    original_title = db.Column(db.Text, nullable=False, default="")
    original_diagnosis = db.Column(db.Text, nullable=False)
    original_metadata = db.Column(db.JSON, nullable=False)
    import_status = db.Column(db.String(32), nullable=False, default="registered", index=True)
    verified = db.Column(db.Boolean, nullable=False, default=False, index=True)
    translated = db.Column(db.Boolean, nullable=False, default=False, index=True)
    license_code = db.Column(db.String(20))
    specialty = db.Column(db.String(80), index=True)
    attempts = db.Column(db.Integer, nullable=False, default=0)
    last_error = db.Column(db.Text)
    provenance = db.Column(db.JSON)
    checkpoint_sha256 = db.Column(db.String(64))
    checkpoint_updated_at = db.Column(db.DateTime)
    clinical_case_id = db.Column(db.Integer, db.ForeignKey("clinical_case.id", ondelete="SET NULL"), index=True)
    clinical_case = db.relationship("ClinicalCase")
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)
    __table_args__ = (db.UniqueConstraint("dataset", "pmcid", name="uq_source_dataset_pmcid"),)


class SourceExclusion(db.Model):
    """Minimal durable blocklist; no article text, diagnosis or source metadata."""
    dataset = db.Column(db.String(64), primary_key=True)
    pmcid = db.Column(db.String(16), primary_key=True)
    reason = db.Column(db.String(32), nullable=False, default="source_rejected")
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)


class AIUsage(db.Model):
    """Provider counters for one operation; no prompts, answers or error text."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="SET NULL"), index=True)
    consultation_id = db.Column(db.String(36), db.ForeignKey("consultation.id", ondelete="SET NULL"), index=True)
    operation = db.Column(db.String(32), nullable=False, index=True)
    successful = db.Column(db.Boolean, nullable=False)
    requests = db.Column(db.Integer, nullable=False)
    responses = db.Column(db.Integer, nullable=False)
    usage_reports = db.Column(db.Integer, nullable=False)
    cache_usage_reports = db.Column(db.Integer, nullable=False)
    prompt_tokens = db.Column(db.BigInteger, nullable=False)
    completion_tokens = db.Column(db.BigInteger, nullable=False)
    total_tokens = db.Column(db.BigInteger, nullable=False)
    cache_hit_tokens = db.Column(db.BigInteger, nullable=False)
    cache_miss_tokens = db.Column(db.BigInteger, nullable=False)
    usage_complete = db.Column(db.Boolean, nullable=False)
    cache_usage_complete = db.Column(db.Boolean, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False, index=True)


class Consultation(db.Model):
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    case_id = db.Column(db.Integer, db.ForeignKey("clinical_case.id"), nullable=False, index=True)
    case_snapshot = db.Column(db.JSON, nullable=False)
    status = db.Column(db.String(20), default="active", nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    completed_at = db.Column(db.DateTime)
    diagnosis = db.Column(db.Text)
    differential = db.Column(db.Text)
    treatment = db.Column(db.Text)
    evaluation = db.Column(db.JSON)
    score = db.Column(db.Integer)
    busy_since = db.Column(db.DateTime)
    lock_token = db.Column(db.String(36))
    user = db.relationship("User")
    clinical_case = db.relationship("ClinicalCase")
    turns = db.relationship("Turn", order_by="Turn.id", cascade="all, delete-orphan", lazy="select")
    exams = db.relationship("PerformedExam", order_by="PerformedExam.id", cascade="all, delete-orphan", lazy="select")
    simulation = db.relationship("ConsultationSimulation", uselist=False, cascade="all, delete-orphan", lazy="select")


class ConsultationSimulation(db.Model):
    """Session-only elaborations; never a change to the source case or its review."""
    consultation_id = db.Column(db.String(36), db.ForeignKey("consultation.id", ondelete="CASCADE"), primary_key=True)
    payload = db.Column(db.JSON, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class RankingResult(db.Model):
    """Rebuildable validated score projection; no conversation or clinical data."""
    consultation_id = db.Column(db.String(36), db.ForeignKey("consultation.id", ondelete="CASCADE"), primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    case_id = db.Column(db.Integer, db.ForeignKey("clinical_case.id", ondelete="CASCADE"), nullable=False)
    track = db.Column(db.String(16), nullable=False)
    score = db.Column(db.Integer, nullable=False)
    __table_args__ = (db.Index("ix_ranking_result_user_track_case", "user_id", "track", "case_id"),)


class RankingStanding(db.Model):
    """One saved mean of case means per learner and catalogue."""
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), primary_key=True)
    track = db.Column(db.String(16), primary_key=True)
    average_score = db.Column(db.Numeric(5, 2), nullable=False)
    distinct_cases = db.Column(db.Integer, nullable=False)
    consultations = db.Column(db.Integer, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    __table_args__ = (db.Index("ix_ranking_standing_track_score", "track", "average_score"),)


class RankingState(db.Model):
    """Completion marker; missing row makes an interrupted initial backfill resumable."""
    key = db.Column(db.String(40), primary_key=True)
    version = db.Column(db.Integer, nullable=False)
    completed_at = db.Column(db.DateTime, default=utcnow, nullable=False)


class Turn(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    consultation_id = db.Column(db.String(36), db.ForeignKey("consultation.id"), nullable=False, index=True)
    question = db.Column(db.Text, nullable=False)
    answer = db.Column(db.Text, nullable=False)
    intent = db.Column(db.String(300), nullable=False)
    request_id = db.Column(db.String(64), nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    __table_args__ = (db.UniqueConstraint("consultation_id", "request_id", name="uq_turn_request"),)


class PerformedExam(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    consultation_id = db.Column(db.String(36), db.ForeignKey("consultation.id"), nullable=False, index=True)
    name = db.Column(db.String(180), nullable=False)
    exam_type = db.Column(db.String(30), nullable=False)
    result = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    __table_args__ = (db.UniqueConstraint("consultation_id", "name", "exam_type", name="uq_exam_session"),)


class RateLimit(db.Model):
    key = db.Column(db.String(80), primary_key=True)
    bucket = db.Column(db.BigInteger, primary_key=True)
    hits = db.Column(db.Integer, default=0, nullable=False)
