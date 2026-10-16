"""Small idempotent schema upgrade for existing cPanel installations."""

from sqlalchemy import inspect, text

from .extensions import db


def upgrade_database():
    from .models import RankingState
    from .ranking import BOOTSTRAP_KEY, BOOTSTRAP_VERSION, rebuild_rankings
    needs_ranking_rebuild = not all(inspect(db.engine).has_table(name) for name in ("ranking_result", "ranking_standing"))
    if needs_ranking_rebuild and inspect(db.engine).has_table("ranking_state"):
        state = db.session.get(RankingState, BOOTSTRAP_KEY)
        if state is not None:
            db.session.delete(state)
            db.session.commit()
    db.create_all()
    columns = {column["name"] for column in inspect(db.engine).get_columns("clinical_case")}
    if "case_type" not in columns:
        with db.engine.begin() as connection:
            connection.execute(text("ALTER TABLE clinical_case ADD COLUMN case_type VARCHAR(80) NOT NULL DEFAULT 'Primeira consulta'"))
    indexes = {index["name"] for index in inspect(db.engine).get_indexes("clinical_case")}
    if "ix_clinical_case_case_type" not in indexes:
        with db.engine.begin() as connection:
            connection.execute(text("CREATE INDEX ix_clinical_case_case_type ON clinical_case (case_type)"))
    discovery_columns = {column["name"] for column in inspect(db.engine).get_columns("case_discovery")}
    discovery_indexes = {index["name"] for index in inspect(db.engine).get_indexes("case_discovery")}
    for name in ("learning_track", "body_system"):
        # Identifiers come only from this fixed allowlist. Empty values preserve
        # existing catalogue rows until upgrade-db/reindex-catalogue refreshes them.
        with db.engine.begin() as connection:
            if name not in discovery_columns:
                connection.execute(text(f"ALTER TABLE case_discovery ADD COLUMN {name} VARCHAR(30) NOT NULL DEFAULT ''"))
            if f"ix_case_discovery_{name}" not in discovery_indexes:
                connection.execute(text(f"CREATE INDEX ix_case_discovery_{name} ON case_discovery ({name})"))
    state = db.session.get(RankingState, BOOTSTRAP_KEY)
    if needs_ranking_rebuild or state is None or state.version != BOOTSTRAP_VERSION:
        rebuild_rankings()
