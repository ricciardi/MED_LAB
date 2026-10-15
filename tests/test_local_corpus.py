"""Completeness and identity checks for the zero-provider corpus preparation."""
import csv
import importlib.util
import json
from pathlib import Path

import pytest

MODULE = Path(__file__).resolve().parents[1] / "scripts/local_corpus.py"
spec = importlib.util.spec_from_file_location("local_corpus", MODULE)
local = importlib.util.module_from_spec(spec)
spec.loader.exec_module(local)


@pytest.fixture
def corpus(tmp_path):
    source, processed, output = tmp_path / "source.csv", tmp_path / "processed", tmp_path / "output"
    (processed / "pmc-cache").mkdir(parents=True)
    (processed / "drafts").mkdir()
    with source.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["pmcid", "case_prompt"])
        writer.writeheader()
        writer.writerows({"pmcid": identifier, "case_prompt": "Documented presentation."}
                        for identifier in ("PMC1", "PMC2", "PMC3"))
    local.save_json(processed / "manifest.json", {"sourceSha256": local.checksum(source), "excludedPmcids": ["PMC3"]})
    for identifier in ("PMC1", "PMC2"):
        local.save_json(processed / "pmc-cache" / (identifier + ".verified.json"), {})
    local.save_json(processed / "drafts/PMC1.json", {})
    return source, processed, output


def test_plan_covers_all_active_preserves_existing_and_keeps_exclusions(corpus):
    source, processed, output = corpus
    value = local.plan(source, processed, output)
    assert value["activeIds"] == ["PMC1", "PMC2"]
    assert value["pendingIds"] == ["PMC2"]
    assert value["preservedIds"] == ["PMC1"]
    assert value["excludedIds"] == ["PMC3"]
    assert value["missingSourceIds"] == []
    assert value["providerCalls"] == 0
    assert local.plan(source, processed, output) == value
    assert (output / "plan.json").stat().st_mode & 0o777 == 0o600


def test_plan_rejects_changed_acquisition(corpus):
    source, processed, output = corpus
    local.plan(source, processed, output)
    source.write_text(source.read_text() + "PMC4,Another presentation.\n")
    with pytest.raises(ValueError, match="CSV mudou"):
        local.plan(source, processed, output)


def test_translation_queue_deduplicates_text_without_losing_case_identity(tmp_path):
    for identifier in ("PMC1", "PMC2"):
        local.save_json(tmp_path / "extracted" / (identifier + ".json"), {"pmcid": identifier,
            "translationUnits": [{"id": identifier + ":symptom", "text": "Fever for two days."},
                                 {"id": identifier + ":diagnosis", "text": identifier + " label"}]})
    report = local.translation_queue(tmp_path)
    rows = [json.loads(line) for line in (tmp_path / "translation-queue.jsonl").read_text().splitlines()]
    assert report["units"] == len(rows) == 3
    assert all(row["id"] == local.text_id(row["text"]) for row in rows)
    assert all((tmp_path / "extracted" / (identifier + ".json")).exists() for identifier in ("PMC1", "PMC2"))


def translation(text="Fever.", translated="Febre."):
    return {"id": local.text_id(text), "text": text, "textPt": translated,
            "translation": {"model": "local-model", "revision": "fixed-revision", "target": ">>pob<<"}}


def test_translated_units_refuses_identity_changes_and_conflicting_outputs(tmp_path):
    path = tmp_path / "translated.jsonl"
    first, second = translation(), translation(translated="Outro resultado.")
    path.write_text(json.dumps(first) + "\n" + json.dumps(second) + "\n")
    with pytest.raises(ValueError, match="conflitantes"):
        local.translated_units(path)
    first["text"] = "Different source"
    path.write_text(json.dumps(first) + "\n")
    with pytest.raises(ValueError, match="não corresponde"):
        local.translated_units(path)


def test_translation_errors_remain_pending_instead_of_becoming_english_cases(tmp_path):
    path = tmp_path / "translated.jsonl"
    failed = {"id": local.text_id("Unknown"), "text": "Unknown", "translationError": "local_translation_failed"}
    path.write_text(json.dumps(translation()) + "\n" + json.dumps(failed) + "\n")
    values, metadata = local.translated_units(path)
    assert values == {local.text_id("Fever."): "Febre."}
    assert metadata["target"] == ">>pob<<"


def test_mixed_model_revisions_cannot_be_claimed_as_one_preparation(tmp_path):
    first, second = translation(), translation("Cough.", "Tosse.")
    second["translation"]["revision"] = "different-revision"
    path = tmp_path / "translated.jsonl"
    path.write_text(json.dumps(first) + "\n" + json.dumps(second) + "\n")
    with pytest.raises(ValueError, match="mistura"):
        local.translated_units(path)


def test_partial_journal_can_be_read_while_a_record_is_being_written(tmp_path):
    path = tmp_path / "translations.partial.jsonl"
    path.write_text(json.dumps(translation()) + '\n{"id":"interrupted')
    values, metadata = local.translated_units(path)
    assert values == {local.text_id("Fever."): "Febre."}
    assert metadata["model"] == "local-model"


def test_final_journal_does_not_hide_a_corrupt_record(tmp_path):
    path = tmp_path / "translations.jsonl"
    path.write_text(json.dumps(translation()) + '\n{"id":"interrupted')
    with pytest.raises(json.JSONDecodeError):
        local.translated_units(path)


def test_additional_queue_keeps_running_queue_unchanged_and_only_adds_new_text(tmp_path):
    path = tmp_path / "extracted/PMC1.json"
    package = {"translationUnits": [{"id": "old", "text": "Fever."}]}
    local.save_json(path, package)
    local.translation_queue(tmp_path)
    original = tmp_path / "translation-queue.jsonl"
    old_bytes = original.read_bytes()
    package["translationUnits"].append({"id": "new", "text": "Cough."})
    local.save_json(path, package)
    added = tmp_path / "supplement.jsonl"
    report = local.translation_queue(tmp_path, destination=added, prior_queues=[original])
    assert report["units"] == 1
    assert json.loads(added.read_text())["text"] == "Cough."
    assert original.read_bytes() == old_bytes
    with pytest.raises(ValueError, match="substituir"):
        local.translation_queue(tmp_path, destination=original, prior_queues=[original])


def test_recovered_translation_fills_error_from_original_journal(tmp_path):
    first, second = tmp_path / "first.jsonl", tmp_path / "recovered.jsonl"
    first.write_text(json.dumps({"translationError": "local_translation_failed"}) + "\n")
    second.write_text(json.dumps(translation()) + "\n")
    values, _ = local.translated_units([first, second])
    assert values == {local.text_id("Fever."): "Febre."}
