"""Real worker scheduling against fake HTTP responses; no provider calls."""
import csv
import json
import threading
import time
from unittest.mock import Mock

import pytest

from medlab.ai import AIService
from medlab.importer import CaseImporter, ImportError
from test_importer import fake_ai, fake_pmc, real_case
from test_ai_usage import response, USAGE


class CaseAI(AIService):
    def classify_case(self, source, specialties):
        self._request("classification", {"pmcid": source["pmcid"]})
        return {"specialty": "Pneumologia", "reason": "Quadro respiratório documentado."}

    def adapt_case(self, source, provenance, specialty):
        self._request("adaptation", {"pmcid": source["pmcid"]})
        self._request("audit", {"pmcid": source["pmcid"]})
        return real_case(provenance["pmcid"], provenance)


def setup(tmp_path, *, count=32, factory_hook=None):
    source = tmp_path / "source.csv"
    with source.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["pmcid", "case_prompt", "final_diagnosis"])
        writer.writeheader()
        for index in range(1, count + 1):
            writer.writerow({"pmcid": f"PMC{index}", "case_prompt": "Cough", "final_diagnosis": "Pneumonia"})
    services = []
    def factory(**kwargs):
        if factory_hook:
            kwargs = factory_hook(kwargs)
        service = CaseAI({"DEEPSEEK_API_KEY": "test-key"}, **kwargs)
        services.append(service)
        return service
    importer = CaseImporter({"DEEPSEEK_API_KEY": "test-key"}, tmp_path / "work",
                            ai_service=fake_ai(), pmc_client=fake_pmc(), ai_service_factory=factory)
    return importer, source, services


def request_identity(kwargs):
    messages = kwargs["json"]["messages"]
    return messages[0]["content"], json.loads(messages[1]["content"])["pmcid"]


def test_sixteen_workers_are_bounded_independent_and_all_writes_stay_on_main(tmp_path, monkeypatch):
    importer, source, services = setup(tmp_path)
    lock = threading.Lock()
    active, maximum, calls = 0, 0, []
    def post(*args, **kwargs):
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
            calls.append(request_identity(kwargs))
        time.sleep(0.005)
        with lock:
            active -= 1
        return response(usage=USAGE)
    monkeypatch.setattr("medlab.ai.requests.post", post)
    main = threading.get_ident()
    writes, progress = [], []
    original_save = importer._save_record
    def save(record):
        writes.append(threading.get_ident())
        original_save(record)
    monkeypatch.setattr(importer, "_save_record", save)
    report = importer.run(source, limit=100, adapt=True, all_records=True, source_workers=4, adapt_workers=16,
                          on_progress=lambda record: progress.append((threading.get_ident(), record)))
    assert 1 < maximum <= 16
    assert len(services) == len({id(service) for service in services}) == 32
    assert len(calls) == 96 and set(writes) == {main}
    assert len(progress) == 32 and all(identity == main for identity, _ in progress)
    assert report["adaptedDrafts"] == 32 and report["processedThisRun"] == 32
    assert report["caseTasks"] == {"submitted": 32, "finished": 32, "interrupted": 0, "cancelledBeforeStart": 0}
    assert report["aiUsageThisRun"]["totalTokens"] == 96 * 12
    assert report["aiUsage"]["totalTokens"] == 96 * 12
    assert all(service.usage_snapshot()["requests"] == 3 for service in services)
    repeat = importer.run(source, limit=100, adapt=True, all_records=True, adapt_workers=16)
    assert repeat["processedThisRun"] == 0 and len(calls) == 96
    assert repeat["aiUsageThisRun"]["requests"] == 0 and repeat["aiUsage"]["requests"] == 96


def test_global_outage_blocks_every_followup_request_and_keeps_rest_pending(tmp_path, monkeypatch):
    failure = threading.Event()
    def hook(options):
        original = options["on_unavailable"]
        def unavailable(error):
            original(error)
            failure.set()
        return {**options, "on_unavailable": unavailable}
    importer, source, _ = setup(tmp_path, factory_hook=hook)
    admitted = threading.Barrier(4)
    calls = []
    def post(*args, **kwargs):
        stage, pmcid = request_identity(kwargs)
        calls.append((stage, pmcid))
        admitted.wait(timeout=3)
        if pmcid == "PMC1":
            return response(status=429)
        assert failure.wait(timeout=3)
        return response(usage=USAGE)
    monkeypatch.setattr("medlab.ai.requests.post", post)
    report = importer.run(source, limit=100, adapt=True, all_records=True, source_workers=4, adapt_workers=4)
    assert report["stopReason"] == "rate_limited"
    assert len(calls) == 4 and all(stage == "classification" for stage, _ in calls)
    assert report["caseTasks"]["submitted"] == 4 and report["caseTasks"]["interrupted"] == 3
    assert report["processedThisRun"] == 4 and report["pendingVerification"] == 28
    assert report["sourceRecords"] == 32 and report["uniquePmcids"] == 32
    assert report["adaptedDrafts"] == 0
    assert report["aiUsageThisRun"]["requests"] == 4
    assert report["aiUsageThisRun"]["totalTokens"] == 36
    assert report["aiUsageThisRun"]["usageComplete"] is False


def test_final_audit_already_in_flight_can_finish_and_its_valid_draft_is_preserved(tmp_path, monkeypatch):
    failure, audit_started = threading.Event(), threading.Event()
    def hook(options):
        original = options["on_unavailable"]
        def unavailable(error):
            original(error)
            failure.set()
        return {**options, "on_unavailable": unavailable}
    importer, source, _ = setup(tmp_path, count=3, factory_hook=hook)
    def post(*args, **kwargs):
        stage, pmcid = request_identity(kwargs)
        if pmcid == "PMC1":
            assert audit_started.wait(timeout=3)
            return response(status=503)
        if stage == "audit":
            audit_started.set()
            assert failure.wait(timeout=3)
        return response(usage=USAGE)
    monkeypatch.setattr("medlab.ai.requests.post", post)
    report = importer.run(source, adapt=True, all_records=True, adapt_workers=2)
    assert report["stopReason"] == "service_unavailable"
    assert report["adaptedDrafts"] == 1
    assert report["statusCounts"] == {"classification_error": 1, "draft": 1}
    assert report["pendingVerification"] == 1
    assert (tmp_path / "work/drafts/PMC2.json").exists()


@pytest.mark.parametrize("options", [{"adapt_workers": 0}, {"adapt_workers": 17}, {"adapt_workers": True},
    {"adapt_workers": 2, "adapt": False, "all_records": True},
    {"adapt_workers": 2, "adapt": True, "all_records": False}])
def test_parallel_validation_rejects_unsafe_quota_or_worker_settings(tmp_path, options):
    importer, source, _ = setup(tmp_path)
    with pytest.raises(ImportError):
        importer.run(source, **options)
