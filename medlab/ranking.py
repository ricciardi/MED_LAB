"""Persistent, rebuildable rankings with equal weight for each distinct case.

Every valid attempt contributes to its case mean, then all distinct cases have
equal weight. Scores rounded HALF_UP to two decimals define competition ranks:
1, 1, 3. Number of attempts is never a tiebreaker or an extra case weight.
"""
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP

from flask import current_app
from sqlalchemy import delete, func, inspect, select

from .extensions import db
from .models import Consultation, RankingResult, RankingStanding, RankingState, User, utcnow


TRACKS = {"basic": "Casos Básicos", "clinical": "Casos Clínicos"}
MINIMUM_CASES = 5
BOOTSTRAP_KEY = "initial-score-backfill"
BOOTSTRAP_VERSION = 1


def minimum_cases():
    return max(1, int(current_app.config.get("RANKING_MINIMUM_CASES", MINIMUM_CASES)))


def display_name(name):
    parts = str(name or "").split()
    if not parts:
        return "Estudante"
    # No email, surname or internal user ID is sent to public templates.
    first = parts[0] if "@" not in parts[0] else "Estudante"
    return " ".join([first, *(part[0].upper() + "." for part in parts[1:] if part and "@" not in part)])


def validated_score(training):
    """Use accepted numerical AI grades, never demo, pending or ambiguous legacy data."""
    from .web import score_breakdown

    value = training.evaluation
    if training.status != "completed" or not isinstance(value, dict):
        return None
    if value.get("evaluationStatus") == "pending_basic_questions" or "pendingBasicQuestions" in value:
        return None
    if value.get("evaluationMode") == "demo" or str(value.get("summary", "")).startswith("[Demonstração sem IA]"):
        return None
    mode = value.get("evaluationMode")
    verification = value.get("verification")
    # Older accepted AI evaluations carry checked/limited provenance. Records
    # without trustworthy mode/provenance are preserved but do not enter a race.
    if mode != "ai" and not (isinstance(verification, dict) and verification.get("status") in {"checked", "limited"}):
        return None
    if type(value.get("evaluationVersion", 1)) is not int or value.get("evaluationVersion", 1) not in (1, 2):
        return None
    if not score_breakdown(value, training.score)["consistent"]:
        return None
    return int(training.score)


def _recompute_user(user_id):
    # FOR UPDATE uses a current read on MySQL, including another completed
    # consultation that committed while this request waited on the user lock.
    rows = db.session.scalars(select(RankingResult).where(RankingResult.user_id == user_id).with_for_update()).all()
    grouped = defaultdict(lambda: defaultdict(list))
    for row in rows:
        grouped[row.track][row.case_id].append(Decimal(row.score))
    for track in TRACKS:
        standing = db.session.scalar(select(RankingStanding)
            .where(RankingStanding.user_id == user_id, RankingStanding.track == track)
            .with_for_update().execution_options(populate_existing=True))
        cases = grouped[track]
        if not cases:
            if standing is not None:
                db.session.delete(standing)
            continue
        means = [sum(scores) / Decimal(len(scores)) for scores in cases.values()]
        average = (sum(means) / Decimal(len(means))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        fields = {"average_score": average, "distinct_cases": len(cases),
                  "consultations": sum(len(scores) for scores in cases.values())}
        if standing is None:
            db.session.add(RankingStanding(user_id=user_id, track=track, **fields))
        elif any(getattr(standing, key) != value for key, value in fields.items()):
            for key, value in fields.items():
                setattr(standing, key, value)
            standing.updated_at = utcnow()


def sync_consultation_ranking(training):
    """Update the projection in the caller's transaction; never commit here."""
    # Serialize different consultations of the same user. SQLite serializes the
    # preceding score write; MySQL additionally locks this parent user row.
    db.session.execute(select(User.id).where(User.id == training.user_id).with_for_update()).scalar_one()
    current = db.session.get(RankingResult, training.id)
    score = validated_score(training)
    if score is None:
        if current is not None:
            db.session.delete(current)
    else:
        fields = {"user_id": training.user_id, "case_id": training.case_id,
                  "track": "basic" if training.case_snapshot.get("learningTrack") == "basic" else "clinical",
                  "score": score}
        if current is None:
            db.session.add(RankingResult(consultation_id=training.id, **fields))
        else:
            for key, value in fields.items():
                setattr(current, key, value)
    db.session.flush()
    _recompute_user(training.user_id)


def rebuild_rankings():
    """Idempotent administrative rebuild, one atomic user transaction at a time."""
    # Leave no completion marker until every user's projection is committed.
    # A process interruption is therefore retried by the next upgrade-db.
    db.session.execute(delete(RankingState).where(RankingState.key == BOOTSTRAP_KEY))
    users = db.session.scalars(select(User.id).order_by(User.id)).all()
    db.session.commit()
    for user_id in users:
        db.session.execute(select(User.id).where(User.id == user_id).with_for_update()).scalar_one()
        # Never lock source consultations while holding User: runtime finalizes
        # a consultation before locking User. A normal committed read avoids an
        # inverted lock order; any in-flight finalization syncs after this commit.
        consultations = db.session.scalars(select(Consultation).where(Consultation.user_id == user_id)).all()
        db.session.execute(delete(RankingResult).where(RankingResult.user_id == user_id))
        for training in consultations:
            score = validated_score(training)
            if score is not None:
                db.session.add(RankingResult(consultation_id=training.id, user_id=user_id,
                    case_id=training.case_id, track="basic" if training.case_snapshot.get("learningTrack") == "basic" else "clinical",
                    score=score))
        db.session.flush()
        _recompute_user(user_id)
        db.session.commit()
    db.session.add(RankingState(key=BOOTSTRAP_KEY, version=BOOTSTRAP_VERSION))
    db.session.commit()
    return {"users": len(users), "results": db.session.scalar(select(func.count(RankingResult.consultation_id))),
            "standings": db.session.scalar(select(func.count()).select_from(RankingStanding))}


def _entry(standing, name, rank=None, user_id=None):
    return {"rank": rank, "display_name": display_name(name), "average_score": float(standing.average_score),
            "distinct_cases": standing.distinct_cases, "consultations": standing.consultations,
            "is_current_user": user_id == standing.user_id}


def ranking_context(user_id=None, *, include_boards=False):
    """Read stored aggregates only; never scan consultations during page rendering."""
    minimum = minimum_cases()
    boards = {track: {"title": title, "minimum_cases": minimum, "entries": []} for track, title in TRACKS.items()}
    personal = {track: {"title": title, "rank": None, "average_score": None, "distinct_cases": 0,
        "consultations": 0, "eligible": False, "remaining_cases": minimum, "minimum_cases": minimum}
        for track, title in TRACKS.items()}
    # An upgrade is required on an existing cPanel database. Keep its pages
    # usable until the CLI creates these additive tables; GET never migrates.
    if not inspect(db.engine).has_table("ranking_standing"):
        return {"leaderboards": boards, "personal_rankings": personal}
    eligible = (RankingStanding.distinct_cases >= minimum, User.is_active.is_(True))
    if include_boards:
        for track in TRACKS:
            rows = db.session.execute(select(RankingStanding, User.name).join(User, User.id == RankingStanding.user_id)
                .where(RankingStanding.track == track, *eligible)
                .order_by(RankingStanding.average_score.desc(), RankingStanding.user_id).limit(15)).all()
            rank, previous = 0, None
            for index, (standing, name) in enumerate(rows, 1):
                if standing.average_score != previous:
                    rank, previous = index, standing.average_score
                boards[track]["entries"].append(_entry(standing, name, rank, user_id))
    if user_id is not None:
        rows = db.session.scalars(select(RankingStanding).where(RankingStanding.user_id == user_id)).all()
        for standing in rows:
            if standing.track not in TRACKS:
                continue
            ready = standing.distinct_cases >= minimum
            rank = None
            if ready:
                above = db.session.scalar(select(func.count()).select_from(RankingStanding).join(User, User.id == RankingStanding.user_id)
                    .where(RankingStanding.track == standing.track, RankingStanding.average_score > standing.average_score, *eligible))
                rank = above + 1
            personal[standing.track].update(rank=rank, average_score=float(standing.average_score),
                distinct_cases=standing.distinct_cases, consultations=standing.consultations, eligible=ready,
                remaining_cases=max(0, minimum - standing.distinct_cases))
    return {"leaderboards": boards, "personal_rankings": personal}
