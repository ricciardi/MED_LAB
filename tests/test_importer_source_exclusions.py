"""Durable database exclusions are enforced before any importer source or AI work."""
import json

import pytest

from medlab.importer import CaseImporter, ImportError
from test_importer import fake_ai, fake_pmc, real_case, source_file


@pytest.mark.parametrize("refresh", [False, True])
def test_excluded_ids_never_reach_source_or_ai_even_with_refresh(tmp_path, source_file, refresh):
    ai, pmc = fake_ai(), fake_pmc()
    importer = CaseImporter({"DEEPSEEK_API_KEY": "test-key"}, tmp_path / "work", ai_service=ai, pmc_client=pmc)
    report = importer.run(source_file, adapt=True, refresh_sources=refresh, excluded_pmcids={"PMC123", "PMC456", "PMC789"})
    pmc.verify.assert_not_called()
    ai.classify_case.assert_not_called()
    ai.adapt_case.assert_not_called()
    assert report["processedThisRun"] == 0 and report["excludedThisRun"] == 3
    assert report["sourceRecords"] == 3 and report["uniquePmcids"] == 3
    assert report["activeSourceRecords"] == 0 and report["pendingVerification"] == 0
    assert importer.report()["excludedSources"] == 3
    assert not list((tmp_path / "work/records").glob("*.json"))


def test_excluded_cached_draft_is_not_opened_or_recovered(tmp_path, source_file):
    importer = CaseImporter({}, tmp_path / "work", ai_service=fake_ai(), pmc_client=fake_pmc())
    (tmp_path / "work/drafts/PMC123.json").write_text("invalid stale content")
    (tmp_path / "work/drafts/PMC456.json").write_text(json.dumps(real_case("PMC456")))
    assert [case["provenance"]["pmcid"] for case in importer.iter_drafts(excluded_pmcids=["PMC123"])] == ["PMC456"]
    report = importer.run(source_file, excluded_pmcids=["PMC123"])
    assert report["excludedThisRun"] == 1 and report["adaptedDrafts"] == 1


@pytest.mark.parametrize("values", ["PMC123", ["PMC000"], [123], {"PMC123": True}])
def test_exclusions_validate_collection_and_identifiers(tmp_path, source_file, values):
    importer = CaseImporter({}, tmp_path / "work", ai_service=fake_ai(), pmc_client=fake_pmc())
    with pytest.raises(ImportError, match="PMCID"):
        importer.run(source_file, excluded_pmcids=values)
