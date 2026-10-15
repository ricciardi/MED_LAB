"""Private, provider-reported runtime usage; never infer unavailable token data."""
from contextlib import contextmanager

from flask import current_app
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from .extensions import db
from .models import AIUsage


OPERATIONS = {"conversation": "Conversa com o paciente", "evaluation": "Avaliação final",
              "generation": "Geração de caso"}
COUNTERS = {"requests": "requests", "responses": "responses", "usageReports": "usage_reports",
    "cacheUsageReports": "cache_usage_reports", "promptTokens": "prompt_tokens",
    "completionTokens": "completion_tokens", "totalTokens": "total_tokens",
    "cacheHitTokens": "cache_hit_tokens", "cacheMissTokens": "cache_miss_tokens"}


def _snapshot(service):
    try:
        value = service.usage_snapshot()
    except Exception:
        return None
    if not isinstance(value, dict) or any(type(value.get(key)) is not int or not 0 <= value[key] <= 2**63 - 1 for key in COUNTERS):
        return None
    return {key: value[key] for key in COUNTERS}


def _delta(before, after):
    if before is None or after is None:
        return None
    values = {column: after[key] - before[key] for key, column in COUNTERS.items()}
    if any(value < 0 for value in values.values()) or values["requests"] == 0:
        return None
    if not (0 <= values["cache_usage_reports"] <= values["usage_reports"] <= values["responses"] <= values["requests"] <= 2147483647):
        return None
    if values["total_tokens"] != values["prompt_tokens"] + values["completion_tokens"]:
        return None
    if values["cache_hit_tokens"] + values["cache_miss_tokens"] > values["prompt_tokens"]:
        return None
    values["usage_complete"] = values["usage_reports"] == values["requests"]
    values["cache_usage_complete"] = values["cache_usage_reports"] == values["requests"]
    return values


@contextmanager
def capture_usage(service, operation, *, user_id=None, consultation_id=None):
    """Capture this call's delta even when output validation or HTTP fails.

    A separate short transaction keeps counters after the caller rolls back a
    failed turn. It never commits any pending learner/admin content.
    """
    if operation not in OPERATIONS:
        raise ValueError("Operação de IA inválida.")
    before = None if getattr(service, "is_demo", False) is True else _snapshot(service)
    successful = False
    try:
        yield
        successful = True
    finally:
        values = _delta(before, _snapshot(service)) if before is not None else None
        if values is not None:
            try:
                with Session(db.engine) as metrics:
                    metrics.add(AIUsage(operation=operation, successful=successful,
                        user_id=user_id, consultation_id=consultation_id, **values))
                    metrics.commit()
            except SQLAlchemyError:
                # A metrics outage must not overwrite the provider result or
                # log a SQL/provider exception that could contain private data.
                current_app.logger.warning("Não foi possível registrar os contadores de uso da IA.")


def usage_summary(*conditions):
    columns = list(COUNTERS.values())
    row = db.session.execute(select(func.count(AIUsage.id),
        *(func.coalesce(func.sum(getattr(AIUsage, key)), 0) for key in columns))
        .where(*conditions)).one()
    result = {"operations": row[0], **dict(zip(columns, row[1:]))}
    result["usage_complete"] = bool(result["requests"] and result["requests"] == result["usage_reports"])
    result["cache_usage_complete"] = bool(result["requests"] and result["requests"] == result["cache_usage_reports"])
    denominator = result["cache_hit_tokens"] + result["cache_miss_tokens"]
    result["cache_rate"] = 100 * result["cache_hit_tokens"] / denominator if result["cache_usage_complete"] and denominator else None
    return result


def usage_row(record):
    denominator = record.cache_hit_tokens + record.cache_miss_tokens
    return {"record": record, "cache_rate": 100 * record.cache_hit_tokens / denominator
        if record.cache_usage_complete and denominator else None}
