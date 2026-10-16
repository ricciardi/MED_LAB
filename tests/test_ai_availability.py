"""Provider-wide errors expose only fixed local messages and stable codes."""
from unittest.mock import Mock

import pytest
import requests
from urllib3.exceptions import MaxRetryError, ReadTimeoutError

from medlab.ai import AIError, AIService, AIUnavailableError, _transport_error_code


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


def with_cause(error):
    wrapper = requests.ConnectionError("private connection wrapper")
    wrapper.__cause__ = error
    return wrapper


def with_context(error):
    wrapper = requests.ConnectionError("private connection wrapper")
    wrapper.__context__ = error
    return wrapper


@pytest.mark.parametrize("wrap", [
    requests.ConnectionError,
    with_cause,
    with_context,
    lambda error: requests.ConnectionError(MaxRetryError(None, "/private-request", reason=error)),
    lambda error: requests.ConnectionError(RuntimeError(error)),
])
def test_wrapped_read_timeout_keeps_safe_code_callback_and_accounting(monkeypatch, caplog, wrap):
    read_timeout = ReadTimeoutError(None, "/private-request", "private response bearer-secret")
    failure = wrap(read_timeout)
    original_args, original_cause = failure.args, failure.__cause__
    post = Mock(side_effect=failure)
    stopped = Mock()
    monkeypatch.setattr("medlab.ai.requests.post", post)
    service = AIService({"DEEPSEEK_API_KEY": "bearer-secret"}, on_unavailable=stopped)
    with pytest.raises(AIUnavailableError) as caught:
        service._request("private system prompt", {"patient": "private clinical data"})
    assert caught.value.code == "timeout"
    stopped.assert_called_once_with(caught.value)
    post.assert_called_once()
    usage = service.usage_snapshot()
    assert usage["requests"] == 1 and usage["responses"] == usage["usageReports"] == 0
    assert usage["usageComplete"] is False and service._last_completion is None
    assert failure.args == original_args and failure.__cause__ is original_cause
    assert "code=timeout class=ConnectionError" in caplog.text
    for secret in ("private", "bearer-secret", "clinical data", "/private-request"):
        assert secret not in caplog.text and secret not in str(caught.value)


def test_real_requests_content_wrapper_is_classified_without_any_network(monkeypatch):
    response = requests.Response()
    response.status_code = 200
    response.raw = Mock()
    response.raw.stream.side_effect = ReadTimeoutError(None, "/private-request", "private response")

    def consume_response(*args, **kwargs):
        # This is the same Requests path used before post(stream=False) returns.
        response.content
        return response

    post = Mock(side_effect=consume_response)
    monkeypatch.setattr("medlab.ai.requests.post", post)
    service = AIService({"DEEPSEEK_API_KEY": "test-key"})
    with pytest.raises(AIUnavailableError) as caught:
        service._request("Return json", {})
    assert caught.value.code == "timeout"
    post.assert_called_once()
    response.raw.stream.assert_called_once()
    assert service.usage_snapshot()["responses"] == 0


def test_cyclic_exception_graph_terminates_and_can_still_find_typed_timeout():
    outer = requests.ConnectionError("private outer")
    middle = MaxRetryError(None, "/private-request", reason=outer)
    outer.__cause__ = middle
    middle.__context__ = outer
    assert _transport_error_code(outer) == "connection_error"
    middle.reason = ReadTimeoutError(None, "/private-request", "private timeout")
    assert _transport_error_code(outer) == "timeout"
    assert middle.__context__ is outer and outer.__cause__ is middle


def test_exception_traversal_has_a_work_limit_and_does_not_parse_messages():
    deep = ReadTimeoutError(None, "/private-request", "private timeout")
    for _ in range(40):
        deep = requests.ConnectionError(deep)
    assert _transport_error_code(deep) == "connection_error"
    assert _transport_error_code(requests.ConnectionError("ReadTimeoutError: read timed out")) == "connection_error"
    assert _transport_error_code(requests.ConnectionError({"reason": requests.Timeout("hidden")})) == "connection_error"


def test_exception_text_is_never_evaluated_or_logged(monkeypatch, caplog):
    class NoTextConnectionError(requests.ConnectionError):
        def __str__(self):
            raise AssertionError("Exception text must not be read")

    post = Mock(side_effect=NoTextConnectionError("private secret"))
    monkeypatch.setattr("medlab.ai.requests.post", post)
    with pytest.raises(AIUnavailableError) as caught:
        AIService({"DEEPSEEK_API_KEY": "test-key"})._request("Return json", {})
    assert caught.value.code == "connection_error"
    assert "code=connection_error class=ConnectionError" in caplog.text
    assert "private secret" not in caplog.text
    post.assert_called_once()
