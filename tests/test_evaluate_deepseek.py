"""The live harness is opt-in, bounded, resumable, and records actual provider usage."""
from copy import deepcopy
import json
import stat
import threading
import time
from unittest.mock import Mock

import pytest

from scripts import evaluate_deepseek as harness
from medlab.ai import AIError, AIUnavailableError
from test_ai import reply
from test_web import evaluation_payload


class FakeService:
    created = []
    active = 0
    peak = 0
    lock = threading.Lock()

    def __init__(self, config, observer, stop):
        self.observer, self.stop = observer, stop
        self.counters = {key: 0 for key in harness.USAGE_KEYS}
        self.operation_id = "none"
        self.index = 0
        self.created.append(self)

    def usage_snapshot(self):
        return {**self.counters, "usageComplete": True, "cacheUsageComplete": True}

    def paid(self, n):
        if self.stop.is_set():
            raise AIUnavailableError("run_cancelled")
        with self.lock:
            type(self).active += 1
            type(self).peak = max(type(self).peak, type(self).active)
        try:
            time.sleep(.001)
            for _ in range(n):
                self.index += 1
                for key, amount in {"requests": 1, "responses": 1, "usageReports": 1, "cacheUsageReports": 1,
                    "promptTokens": 20, "completionTokens": 5, "totalTokens": 25, "cacheHitTokens": 15, "cacheMissTokens": 5}.items():
                    self.counters[key] += amount
                self.observer(self.operation_id, self.index, {"output": {"fake": True}})
        finally:
            with self.lock:
                type(self).active -= 1

    def respond(self, case, question, history, simulation_state=None):
        self.paid(2)
        state = deepcopy(simulation_state) if simulation_state is not None else {"version":1,"startedAfterTurn":len(history),"details":[]}
        state["details"].append({"topic":"Preocupações do paciente", "detail":f"Detalhe fictício {len(history)+1}", "turn":len(history)+1})
        return {"answer": "Não sei lhe dizer.", "intent": "Conversa geral", "simulation_state":state}

    def evaluate(self, case, session):
        self.paid(1)
        output = evaluation_payload()
        correct = session["userSubmissions"][0]["data"]["diagnosis"] == case["diagnoses"]["mainDiagnosis"]["disease"]
        output["score"] = 90 if correct else 10
        for section in output["sections"]:
            section["score"] = output["score"]
        return output

    def generate_case(self, specialty, difficulty):
        self.paid(1)
        return {"reviewStatus": "review_required", "catalogType": "fictional"}


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    monkeypatch.setattr("requests.sessions.Session.request", Mock(side_effect=AssertionError("No network in harness tests")))
    FakeService.created, FakeService.active, FakeService.peak = [], 0, 0


def config():
    return {"DEEPSEEK_API_KEY": "test-private-secret", "AI_MODEL": "test-model", "AI_BASE_URL": "https://example.invalid"}


def test_default_cli_only_prints_plan_and_never_constructs_service(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(harness, "EvaluationRun", Mock(side_effect=AssertionError("live required")))
    assert harness.main(["--output-dir", str(tmp_path / "should-not-exist")]) == 0
    assert "Nenhuma API chamada" in capsys.readouterr().out
    assert not (tmp_path / "should-not-exist").exists()


def test_run_persists_each_operation_usage_and_is_idempotent(case_data, tmp_path):
    run = harness.EvaluationRun(config(), [case_data], tmp_path, workers=2, service_factory=FakeService)
    report = run.run()
    assert report["operations"] == 10 and report["succeeded"] == 10
    assert report["byOperation"]["conversation"]["usage"]["requests"] == 12
    assert report["byOperation"]["conversation"]["usage"]["promptTokens"] == 240
    assert report["byOperation"]["conversation"]["usage"]["cacheHitRate"] == 75
    assert report["comparisons"][0]["correctHigher"] and report["comparisons"][0]["correctDiagnosisHigher"]
    operations = list((tmp_path / "operations").glob("*.json"))
    assert len(operations) == 10 and len(list((tmp_path / "requests").glob("*.json"))) == 16
    long = next(json.loads(path.read_text()) for path in operations if "evaluation_40" in path.name)
    assert len(long["input"]["history"]) == 40
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in tmp_path.rglob("*.json"))
    assert stat.S_IMODE(tmp_path.stat().st_mode) == 0o700
    before = sum(service.counters["requests"] for service in FakeService.created)
    repeated = harness.EvaluationRun(config(), [case_data], tmp_path, workers=2, service_factory=FakeService).run()
    assert repeated["succeeded"] == 10
    assert sum(service.counters["requests"] for service in FakeService.created) == before


def test_workers_are_bounded_and_source_cases_are_not_mutated(case_data, tmp_path):
    cases = []
    for index in range(7):
        case = deepcopy(case_data)
        case["caseId"] = f"test-{index}"
        cases.append(case)
    original = deepcopy(cases)
    harness.EvaluationRun(config(), cases, tmp_path, workers=3, service_factory=FakeService).run(assessments=False, generation=False)
    assert 1 < FakeService.peak <= 3
    assert cases == original
    with pytest.raises(harness.EvaluationError):
        harness.EvaluationRun(config(), cases, tmp_path, workers=9)


def test_changed_code_or_cases_requires_a_new_directory(case_data, tmp_path):
    harness.EvaluationRun(config(), [case_data], tmp_path, service_factory=FakeService).run(assessments=False, generation=False)
    modified = deepcopy(case_data)
    modified["patientProfile"]["chief_complaint"] = "Texto de teste alterado."
    with pytest.raises(harness.EvaluationError, match="mudaram"):
        harness.EvaluationRun(config(), [modified], tmp_path, service_factory=FakeService).run(assessments=False, generation=False)


def test_failure_keeps_paid_usage_without_exception_secrets(case_data, tmp_path):
    class Failure(FakeService):
        def respond(self, *args, **kwargs):
            self.paid(1)
            raise AIError("test-private-secret must never appear")
    report = harness.EvaluationRun(config(), [case_data], tmp_path, service_factory=Failure).run(assessments=False, generation=False)
    assert report["failed"] == 6
    assert report["byOperation"]["conversation"]["usage"]["requests"] == 6
    for path in tmp_path.rglob("*.json"):
        assert "test-private-secret" not in path.read_text()
    harness.EvaluationRun(config(), [case_data], tmp_path, service_factory=FakeService, retry_errors=True).run(assessments=False, generation=False)
    assert len(list((tmp_path / "previous-attempts").glob("*.json"))) == 6
    assert len(list((tmp_path / "requests").glob("*.json"))) == 18


def test_unknown_cache_is_not_rendered_as_zero():
    counters = {key: 0 for key in harness.USAGE_KEYS}
    after = {**counters, "requests": 1, "responses": 1}
    usage = harness.usage_delta(counters, after)
    assert usage["cacheUsageComplete"] is False
    report = harness.summarize([{"id": "x", "operation": "conversation", "status": "error", "elapsedMs": 10, "usage": usage}])
    assert report["byOperation"]["conversation"]["usage"]["cacheHitRate"] is None
    assert harness.percentile([10, 20, 30], .5) == 20
    assert harness.percentile([10, 20, 30], .95) == 29


def test_observed_service_records_provider_output_before_answer_validation(case_data, monkeypatch):
    records = []
    service = harness.ObservedService(config(), lambda *args: records.append(args), threading.Event())
    response = reply({"intents": ["Invalid category"]})
    body = response.json.return_value
    body["usage"] = {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120,
                     "prompt_cache_hit_tokens": 80, "prompt_cache_miss_tokens": 20}
    monkeypatch.setattr("medlab.ai.requests.post", Mock(return_value=response))
    with pytest.raises(AIError):
        service.respond(case_data, "O que você sente?", [])
    assert len(records) == 2  # One bounded repair preserves both paid attempts.
    request = records[0][2]
    assert request["output"] == {"intents": ["Invalid category"]}
    assert request["usage"]["requests"] == 1 and request["usage"]["cacheHitTokens"] == 80


def test_questions_cover_gaps_injection_and_responsible_voice(case_data):
    probes = harness.questions(case_data)
    assert len(probes) == 6 and {"unknown_fact", "injection", "consistency"} <= {p["category"] for p in probes}
    case_data["patientProfile"]["respondent"] = "A mãe responde pela criança."
    assert "respondent_perspective_requires_review" in harness.response_flags(case_data, probes[0], {"answer": "Estou doente."})
    assert "unknown_fact_requires_review" in harness.response_flags(case_data, probes[3], {"answer": "Foi ontem."})
    assert harness.response_flags(case_data, probes[3], {"answer": "Não sei lhe dizer."}) == []


def test_reusing_conversations_only_spends_on_new_assessments(case_data, tmp_path):
    baseline, review = tmp_path / "baseline", tmp_path / "review"
    harness.EvaluationRun(config(), [case_data], baseline, service_factory=FakeService).run(assessments=False, generation=False)
    report = harness.EvaluationRun(config(), [case_data], review, service_factory=FakeService).run(
        generation=False, reuse_conversations=baseline)
    assert report["operations"] == report["succeeded"] == 3
    assert "conversation" not in report["byOperation"]
    assert len(list((review / "requests").glob("*.json"))) == 3
    assert (review / "conversation-baseline.json").exists()
    baseline_last = json.loads((baseline / "operations" / f"{case_data['caseId']}-turn-06.json").read_text())
    assert len(baseline_last["input"]["simulationContext"]["details"]) == 5
    assert len(baseline_last["output"]["simulation_state"]["details"]) == 6
    for path in (review / "operations").glob("*.json"):
        record = json.loads(path.read_text())
        assert record["input"]["simulationContext"] == baseline_last["output"]["simulation_state"]


def test_legacy_evaluation_replay_does_not_invent_simulation_provenance(case_data):
    history = [{"question":"Como está?","answer":"Estou cansada.","intent":"Estado geral"}]
    assert "simulationContext" not in harness.evaluation_session(case_data, history)
    original = deepcopy(history)
    state = {"version":1,"startedAfterTurn":0,"details":[{"topic":"Estado geral","detail":"Estou cansada.","turn":1}]}
    history[0]["simulation_state"] = state
    result = harness.evaluation_session(case_data, history)
    assert result["simulationContext"] == state
    result["simulationContext"]["details"].clear()
    assert history[0]["simulation_state"]["details"]
    assert original[0]["answer"] == history[0]["answer"]


def test_reused_conversations_cannot_belong_to_a_modified_case(case_data, tmp_path):
    baseline = tmp_path / "baseline"
    harness.EvaluationRun(config(), [case_data], baseline, service_factory=FakeService).run(assessments=False, generation=False)
    modified = deepcopy(case_data)
    modified["patientProfile"]["chief_complaint"] = "Queixa diferente."
    before = sum(service.counters["requests"] for service in FakeService.created)
    with pytest.raises(harness.EvaluationError, match="outra versão"):
        harness.EvaluationRun(config(), [modified], tmp_path / "review", service_factory=FakeService).run(reuse_conversations=baseline)
    assert sum(service.counters["requests"] for service in FakeService.created) == before


def test_targeted_assessment_retest_does_not_repeat_other_paid_operations(case_data, tmp_path):
    baseline, review = tmp_path / "baseline", tmp_path / "review"
    harness.EvaluationRun(config(), [case_data], baseline, service_factory=FakeService).run(assessments=False, generation=False)
    report = harness.EvaluationRun(config(), [case_data], review, service_factory=FakeService).run(
        generation=False, reuse_conversations=baseline, evaluation_ids=[case_data["caseId"] + "-evaluation_wrong"])
    assert report["operations"] == 1 and report["byOperation"]["evaluation_wrong"]["usage"]["requests"] == 1
    assert report["comparisons"] == []


def test_a_negative_control_that_passes_is_explicit_in_report():
    counters = {key: 0 for key in harness.USAGE_KEYS}
    report = harness.summarize([{"id": "control", "operation": "audit_negative_control", "status": "ok",
        "output": {"rejected": False, "issues": []}, "elapsedMs": 1, "usage": counters}])
    assert report["negativeControls"] == [{"id": "control", "rejected": False}]
