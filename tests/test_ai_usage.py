"""Usage is provider-reported, including paid answers rejected by validation."""
import json
from unittest.mock import Mock

import pytest

from medlab.ai import AIError, AIInterruptedError, AIService, AIUnavailableError


def response(content='{"ok":true}', usage=None, status=200):
    body = {"choices": [{"finish_reason": "stop", "message": {"content": content}}]}
    if usage is not None:
        body["usage"] = usage
    return Mock(status_code=status, content=json.dumps(body).encode(), json=Mock(return_value=body))


USAGE = {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12,
         "prompt_cache_hit_tokens": 4, "prompt_cache_miss_tokens": 6}


def test_usage_is_recorded_even_when_completion_json_is_rejected(monkeypatch):
    monkeypatch.setattr("medlab.ai.requests.post", Mock(return_value=response('not JSON', USAGE)))
    service = AIService({"DEEPSEEK_API_KEY": "test-key"})
    with pytest.raises(AIError):
        service._request("json", {})
    assert service.usage_snapshot() == {"requests": 1, "responses": 1, "usageReports": 1,
        "cacheUsageReports": 1, "promptTokens": 10, "completionTokens": 2, "totalTokens": 12,
        "cacheHitTokens": 4, "cacheMissTokens": 6, "usageComplete": True, "cacheUsageComplete": True}


@pytest.mark.parametrize("usage", [None, {}, {**USAGE, "total_tokens": 999}, {**USAGE, "prompt_tokens": -10}, {**USAGE, "completion_tokens": True}])
def test_missing_or_invalid_usage_is_never_estimated(monkeypatch, usage):
    monkeypatch.setattr("medlab.ai.requests.post", Mock(return_value=response(usage=usage)))
    service = AIService({"DEEPSEEK_API_KEY": "test-key"})
    assert service._request("json", {}) == {"ok": True}
    snapshot = service.usage_snapshot()
    assert snapshot["requests"] == 1 and snapshot["totalTokens"] == 0
    assert snapshot["usageComplete"] is False


def test_shared_guard_blocks_http_without_counting_a_request(monkeypatch):
    post = Mock()
    monkeypatch.setattr("medlab.ai.requests.post", post)
    guard = Mock(side_effect=AIInterruptedError("rate_limited"))
    service = AIService({"DEEPSEEK_API_KEY": "test-key"}, request_guard=guard)
    with pytest.raises(AIInterruptedError):
        service._request("json", {})
    assert service.usage_snapshot()["requests"] == 0
    post.assert_not_called()


def test_outage_callback_signals_other_workers_before_exception_leaves_service(monkeypatch):
    post = Mock(return_value=response(status=503))
    monkeypatch.setattr("medlab.ai.requests.post", post)
    errors = []
    service = AIService({"DEEPSEEK_API_KEY": "test-key"}, on_unavailable=errors.append)
    with pytest.raises(AIUnavailableError) as failure:
        service._request("json", {})
    assert errors == [failure.value]
    assert service.usage_snapshot()["usageComplete"] is False


@pytest.mark.parametrize("cached", [0, 4, 10])
def test_cached_tokens_fallback_uses_reported_prompt_total(monkeypatch, cached):
    usage = {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12,
             "prompt_tokens_details": {"cached_tokens": cached}}
    monkeypatch.setattr("medlab.ai.requests.post", Mock(return_value=response(usage=usage)))
    service = AIService({"DEEPSEEK_API_KEY": "test-key"})
    service._request("json", {})
    snapshot = service.usage_snapshot()
    assert snapshot["cacheHitTokens"] == cached
    assert snapshot["cacheMissTokens"] == 10 - cached
    assert snapshot["cacheUsageReports"] == 1 and snapshot["cacheUsageComplete"] is True
    assert snapshot["totalTokens"] == 12 and snapshot["usageComplete"] is True


@pytest.mark.parametrize("cache", [
    {}, {"prompt_tokens_details": {}}, {"prompt_tokens_details": {"cached_tokens": None}},
    {"prompt_tokens_details": {"cached_tokens": -1}}, {"prompt_tokens_details": {"cached_tokens": 11}},
    {"prompt_tokens_details": {"cached_tokens": True}}, {"prompt_tokens_details": {"cached_tokens": 4.0}},
    {"prompt_tokens_details": {"cached_tokens": "4"}},
    {"prompt_cache_hit_tokens": 4, "prompt_tokens_details": {"cached_tokens": 4}},
    {"prompt_cache_miss_tokens": 6, "prompt_tokens_details": {"cached_tokens": 4}},
    {"prompt_cache_hit_tokens": 4, "prompt_cache_miss_tokens": 7, "prompt_tokens_details": {"cached_tokens": 4}},
    {"prompt_cache_hit_tokens": None, "prompt_cache_miss_tokens": None, "prompt_tokens_details": {"cached_tokens": 4}},
    {"prompt_cache_hit_tokens": 4, "prompt_cache_miss_tokens": 6, "prompt_tokens_details": {"cached_tokens": 5}},
    {"prompt_cache_hit_tokens": 4, "prompt_cache_miss_tokens": 6, "prompt_tokens_details": {"cached_tokens": True}},
])
def test_unavailable_or_contradictory_cache_reports_are_not_silently_estimated(monkeypatch, cache):
    usage = {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12, **cache}
    monkeypatch.setattr("medlab.ai.requests.post", Mock(return_value=response(usage=usage)))
    service = AIService({"DEEPSEEK_API_KEY": "test-key"})
    service._request("json", {})
    snapshot = service.usage_snapshot()
    assert snapshot["usageComplete"] is True and snapshot["totalTokens"] == 12
    assert snapshot["cacheUsageReports"] == snapshot["cacheHitTokens"] == snapshot["cacheMissTokens"] == 0
    assert snapshot["cacheUsageComplete"] is False


def test_matching_usage_alias_is_counted_once_and_snapshots_are_independent(monkeypatch):
    usage = {**USAGE, "prompt_tokens_details": {"cached_tokens": 4}}
    post = Mock(side_effect=[response(usage=usage), response(usage={
        "prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12,
        "prompt_tokens_details": {"cached_tokens": 3}}), response()])
    monkeypatch.setattr("medlab.ai.requests.post", post)
    service = AIService({"DEEPSEEK_API_KEY": "test-key"})
    service._request("json", {})
    first = service.usage_snapshot()
    first["cacheHitTokens"] = 9999
    service._request("json", {})
    second = service.usage_snapshot()
    assert second["cacheHitTokens"] == 7 and second["cacheMissTokens"] == 13
    assert second["cacheUsageReports"] == 2 and second["cacheUsageComplete"] is True
    service._request("json", {})
    third = service.usage_snapshot()
    assert third["requests"] == 3 and third["usageReports"] == third["cacheUsageReports"] == 2
    assert third["usageComplete"] is False and third["cacheUsageComplete"] is False
    assert third["cacheHitTokens"] == 7 and third["totalTokens"] == 24
