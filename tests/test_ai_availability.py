"""Provider-wide errors expose only fixed local messages and stable codes."""
from unittest.mock import Mock

import pytest
import requests

from medlab.ai import AIError, AIService, AIUnavailableError


@pytest.mark.parametrize("status,code", [
    (401, "authentication_required"), (403, "authentication_required"),
    (402, "quota_exhausted"), (429, "rate_limited"),
    (500, "service_unavailable"), (502, "service_unavailable"),
    (503, "service_unavailable"), (504, "service_unavailable"),
])
def test_global_http_errors_stop_without_reading_sensitive_provider_body(monkeypatch, status, code):
    response = Mock(status_code=status)
    response.json.side_effect = AssertionError("provider body must never be read")
    post = Mock(return_value=response)
    monkeypatch.setattr("medlab.ai.requests.post", post)
    service = AIService({"DEEPSEEK_API_KEY": "hidden-key-for-test"})
    with pytest.raises(AIUnavailableError) as caught:
        service._request("Return json", {"private": "patient context"})
    assert caught.value.code == code
    assert isinstance(caught.value, AIError)
    assert "hidden-key" not in str(caught.value) and "patient" not in str(caught.value)
    post.assert_called_once()
    response.json.assert_not_called()


@pytest.mark.parametrize("failure,code", [
    (requests.Timeout("private request / bearer secret"), "timeout"),
    (requests.ConnectionError("private provider trace"), "connection_error"),
    (requests.RequestException("private internal details"), "connection_error"),
])
def test_transport_outages_have_safe_codes_and_no_retry(monkeypatch, failure, code):
    post = Mock(side_effect=failure)
    monkeypatch.setattr("medlab.ai.requests.post", post)
    with pytest.raises(AIUnavailableError) as caught:
        AIService({"DEEPSEEK_API_KEY": "test-key"})._request("Return json", {})
    assert caught.value.code == code and "private" not in str(caught.value)
    post.assert_called_once()


def test_unknown_availability_code_cannot_inject_provider_text():
    error = AIUnavailableError("provider body with sensitive key")
    assert error.code == "service_unavailable"
    assert "sensitive" not in str(error)


def test_bad_request_remains_a_record_error(monkeypatch):
    monkeypatch.setattr("medlab.ai.requests.post", Mock(return_value=Mock(status_code=400)))
    with pytest.raises(AIError) as caught:
        AIService({"DEEPSEEK_API_KEY": "test-key"})._request("Return json", {})
    assert not isinstance(caught.value, AIUnavailableError)
