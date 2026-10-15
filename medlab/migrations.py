"""Small idempotent schema upgrade for existing cPanel installations."""

from sqlalchemy import inspect, text

from .extensions import db


def upgrade_database():
    db.create_all()
    columns = {column["name"] for column in inspect(db.engine).get_columns("clinical_case")}
    if "case_type" not in columns:
        with db.engine.begin() as connection:
            connection.execute(text("ALTER TABLE clinical_case ADD COLUMN case_type VARCHAR(80) NOT NULL DEFAULT 'Primeira consulta'"))
    indexes = {index["name"] for index in inspect(db.engine).get_indexes("clinical_case")}
    if "ix_clinical_case_case_type" not in indexes:
        with db.engine.begin() as connection:
            connection.execute(text("CREATE INDEX ix_clinical_case_case_type ON clinical_case (case_type)"))
