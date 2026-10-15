"""Append/rename/readiness contracts without a model, network or real database."""
from contextlib import nullcontext
from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from scripts import stream_local_corpus as stream


def record(text, translated=None, **changes):
    value = {"id": stream.text_id(text), "text": text, "textPt": translated or "Traduzido: " + text,
             "translation": {"model": "synthetic-local-model", "revision": "fixed-test-revision", "target": ">>pob<<"}}
    value.update(changes)
    return value


def line(value):
    return (json.dumps(value, ensure_ascii=False) + "\n").encode()


def corpus(tmp_path, packages, *, preserved=(), excluded=()):
    plan = {"format": "medlab-local-corpus-v1", "activeIds": sorted([*packages, *preserved]),
            "pendingIds": sorted(packages), "preservedIds": list(preserved), "excludedIds": list(excluded)}
    stream.save_json(tmp_path / "plan.json", plan)
    for pmcid, texts in packages.items():
        stream.save_json(tmp_path / "extracted" / (pmcid + ".json"), {
            "pmcid": pmcid, "translationUnits": [{"id": f"{pmcid}:{index}", "text": text}
                                                  for index, text in enumerate(texts)]})
    return plan


def test_partial_tail_consumes_only_complete_append_and_follows_atomic_rename(tmp_path):
    final = tmp_path / "translations.jsonl"
    partial = tmp_path / "translations.partial.jsonl"
    first, second, third = [record(text) for text in ("Fever.", "Cough.", "Pain.")]
    second_line = line(second)
    partial.write_bytes(line(first) + second_line[:17])
    tail = stream.TranslationTail()
    assert tail.read(final) == {first["id"]}
    assert tail.read(final) == set()
    with partial.open("ab") as handle:
        handle.write(second_line[17:])
    assert tail.read(final) == {second["id"]}
    partial.replace(final)
    assert tail.read(final) == set()
    with final.open("ab") as handle:
        handle.write(line(third))
    assert tail.read(final) == {third["id"]}
    assert tail.values == {item["id"]: item["textPt"] for item in (first, second, third)}


def test_final_incomplete_or_truncated_journal_is_not_silently_accepted(tmp_path):
    final = tmp_path / "translations.jsonl"
    final.write_bytes(line(record("Fever.")) + b'{"id":"unfinished')
    with pytest.raises(ValueError, match="registro incompleto"):
        stream.TranslationTail().read(final)
    final.write_bytes(line(record("Fever.")))
    tail = stream.TranslationTail()
    tail.read(final)
    final.write_bytes(b"")
    with pytest.raises(ValueError, match="truncado"):
        tail.read(final)


def test_failed_unit_only_becomes_ready_after_an_exact_successful_recovery(tmp_path):
    final, recovery = tmp_path / "translations.jsonl", tmp_path / "recovery.jsonl"
    failed = record("Pending source.", translationError="local_translation_failed")
    final.write_bytes(line(failed))
    tail = stream.TranslationTail()
    assert tail.read(final) == set() and tail.failed == {failed["id"]}
    recovery.write_bytes(line(record("Pending source.")))
    assert tail.read(recovery) == {failed["id"]} and not tail.failed
    assert tail.values[failed["id"]] == "Traduzido: Pending source."


def test_numeric_warning_never_releases_case_until_clean_recovery(tmp_path):
    from scripts.local_corpus import translated_units
    text = "Symptoms for 2½ days."
    corpus(tmp_path, {"PMC1": [text]})
    journal, recovery = tmp_path / "translations.jsonl", tmp_path / "recovery.jsonl"
    damaged = record(text, "Sintomas há212dias.")
    damaged["translation"]["warnings"] = ["numeric_tokens_changed"]
    journal.write_bytes(line(damaged))
    tail, index = stream.TranslationTail(), stream.ReadyCases(tmp_path)
    index.accept(tail.read(journal))
    assert not index.ready and index.remaining == {"PMC1": 1}
    assert tail.failed == {damaged["id"]} and tail.values == {}
    assert translated_units(journal) == ({}, None)
    recovered = record(text, "Sintomas há2½dias.")
    recovered["translation"]["warnings"] = []
    recovery.write_bytes(line(recovered))
    index.accept(tail.read(recovery))
    assert index.ready == {"PMC1"} and index.remaining["PMC1"] == 0
    assert not tail.failed and tail.values[damaged["id"]] == recovered["textPt"]
    values, _ = translated_units([journal, recovery])
    assert values == {recovered["id"]: recovered["textPt"]}


def test_rename_between_stat_and_open_preserves_cursor_and_retries_next_pass(tmp_path, monkeypatch):
    final, partial = tmp_path / "translations.jsonl", tmp_path / "translations.partial.jsonl"
    value = record("Fever.")
    partial.write_bytes(line(value))
    original = Path.open
    renamed = False
    def open_after_rename(path, *args, **kwargs):
        nonlocal renamed
        if path == partial and not renamed:
            renamed = True
            partial.replace(final)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", open_after_rename)
    tail = stream.TranslationTail()
    assert tail.read(final) == set() and tail.positions == {} and tail.values == {}
    assert tail.read(final) == {value["id"]}
    assert tail.read(final) == set()


@pytest.mark.parametrize("recovery_first", [False, True])
def test_explicit_recovery_stays_authoritative_when_old_warning_becomes_equivalent(tmp_path, recovery_first):
    from scripts.local_corpus import translated_units
    primary = record("Thirty-six hours later.", "36 horas depois.")
    primary["translation"]["warnings"] = ["numeric_tokens_changed"]
    recovered = record(primary["text"], "Trinta e seis horas depois.",
                       recoverySource={"sourceSha256": primary["id"]})
    paths = [tmp_path / "primary.jsonl", tmp_path / "recovery.jsonl"]
    for path, value in zip(paths, [primary, recovered]):
        path.write_bytes(line(value))
    if recovery_first:
        paths.reverse()
    tail = stream.TranslationTail()
    for path in paths:
        tail.read(path)
    assert tail.values == {primary["id"]: recovered["textPt"]}
    assert translated_units(paths)[0] == tail.values


def test_assistant_post_edit_has_hash_bound_provenance_scoped_to_each_case(tmp_path):
    from scripts.local_corpus import translated_units, case_translation_metadata
    original = record("A 12-year-old girl.", "Uma menina de 12 anos.")
    amended = record(original["text"], "Uma paciente de 12 anos de idade.")
    pair = {"sourceSha256": original["id"], "translationSha256": stream.text_id(amended["textPt"])}
    amended["translation"]["postEditing"] = {"method": "assistant_post_edit", "actor": "Codex",
                                             **pair, "numbersAndUnitsChecked": True}
    first, second = tmp_path / "first.jsonl", tmp_path / "edited.jsonl"
    first.write_bytes(line(original)); second.write_bytes(line(amended))
    tail = stream.TranslationTail()
    tail.read(first); tail.read(second)
    values, metadata = translated_units([first, second])
    assert values == tail.values == {original["id"]: amended["textPt"]}
    assert metadata["_post_edits"] == tail.post_edits == {original["id"]: pair}
    selected = case_translation_metadata({"translationUnits": [{"text": original["text"]}]}, metadata)
    assert selected["postEditing"]["units"] == [pair]
    assert "_post_edits" not in selected
    unrelated = case_translation_metadata({"translationUnits": [{"text": "Unrelated case."}]}, metadata)
    assert "postEditing" not in unrelated and "_post_edits" not in unrelated
    amended["translation"]["postEditing"]["translationSha256"] = "0" * 64
    second.write_bytes(line(amended))
    with pytest.raises(ValueError, match="Correção assistida"):
        stream.TranslationTail().read(second)


@pytest.mark.parametrize("same_text", [False, True])
def test_post_edit_rebuilds_completed_dependents_without_releasing_incomplete_case(tmp_path, same_text):
    source = "Upper limb weakness."
    corpus(tmp_path, {"PMC1": [source], "PMC2": [source, "Pending examination."]})
    original = record(source, "Fraqueza do membro superior." if same_text else "Fraqueza da perna.")
    edited = record(source, "Fraqueza do membro superior.")
    edited["translation"]["postEditing"] = {
        "method": "assistant_post_edit", "actor": "Codex", "sourceSha256": original["id"],
        "translationSha256": stream.text_id(edited["textPt"]), "numbersAndUnitsChecked": True}
    primary, correction = tmp_path / "primary.jsonl", tmp_path / "correction.jsonl"
    primary.write_bytes(line(original)); correction.write_bytes(line(edited))
    tail, index = stream.TranslationTail(), stream.ReadyCases(tmp_path)
    index.accept(tail.read(primary))
    assert index.ready == {"PMC1"}
    index.ready.clear()  # First version has already been assembled.
    index.accept(tail.read(correction))
    assert index.ready == {"PMC1"} and index.remaining == {"PMC1": 0, "PMC2": 1}
    assert tail.values[original["id"]] == edited["textPt"]
    index.ready.clear()
    index.accept(tail.read(correction))
    assert not index.ready  # Reading the same append twice creates no rebuild loop.


def test_equal_older_translation_cannot_demote_post_edit_priority(tmp_path):
    from scripts.local_corpus import translated_units
    source = "Pain."
    edited, primary = record(source, "Dor."), record(source, "Dor.")
    edited["translation"]["postEditing"] = {
        "method": "assistant_post_edit", "actor": "Codex", "sourceSha256": edited["id"],
        "translationSha256": stream.text_id(edited["textPt"]), "numbersAndUnitsChecked": True}
    recovery = record(source, "Dor referida.", recoverySource={"sourceSha256": edited["id"]})
    paths = [tmp_path / (str(i) + ".jsonl") for i in range(3)]
    for path, row in zip(paths, [edited, primary, recovery]):
        path.write_bytes(line(row))
    tail = stream.TranslationTail()
    assert tail.read(paths[0]) == {edited["id"]}
    assert tail.read(paths[1]) == tail.read(paths[2]) == set()
    assert tail.priorities[edited["id"]] == 2
    assert translated_units(paths)[0] == tail.values == {edited["id"]: "Dor."}


@pytest.mark.parametrize("mutation,message", [
    (lambda row: row.update(text="Different original."), "Identidade"),
    (lambda row: row.update(textPt="Conflicting translation."), "conflitantes"),
    (lambda row: row["translation"].update(revision="other-revision"), "configurações"),
    (lambda row: row.update(textPt=" "), "vazia"),
])
def test_conflicting_or_invalid_journal_cannot_satisfy_readiness(tmp_path, mutation, message):
    first, later = tmp_path / "first.jsonl", tmp_path / "later.jsonl"
    original = record("Fever.")
    first.write_bytes(line(original))
    changed = deepcopy(original)
    mutation(changed)
    later.write_bytes(line(changed))
    tail = stream.TranslationTail()
    tail.read(first)
    with pytest.raises(ValueError, match=message):
        tail.read(later)


def test_readiness_requires_all_unique_units_and_excludes_residual_packages(tmp_path):
    corpus(tmp_path, {"PMC1": ["Shared.", "Unique1.", "Shared."], "PMC2": ["Shared.", "Unique2."]},
           excluded=["PMC3"])
    stream.save_json(tmp_path / "extracted/PMC3.json", {"pmcid": "PMC3",
        "patientSpecies": {"kind": "nonhuman"}, "translationUnits": [{"text": "Shared."}]})
    index = stream.ReadyCases(tmp_path)
    assert set(index.remaining) == {"PMC1", "PMC2"}
    index.accept({stream.text_id("Shared.")})
    assert not index.ready and index.remaining == {"PMC1": 1, "PMC2": 1}
    index.accept({stream.text_id("Shared.")})  # Replayed append cannot decrement twice.
    assert not index.ready and index.remaining == {"PMC1": 1, "PMC2": 1}
    index.accept({stream.text_id("Unique1.")})
    assert index.ready == {"PMC1"} and index.remaining["PMC2"] == 1
    index.accept({stream.text_id("Unique2.")})
    assert index.ready == {"PMC1", "PMC2"}


def test_active_veterinary_or_missing_package_stops_before_any_incorporation(tmp_path):
    corpus(tmp_path, {"PMC1": ["Fever."]})
    path = tmp_path / "extracted/PMC1.json"
    package = json.loads(path.read_text())
    package["patientSpecies"] = {"kind": "nonhuman"}
    stream.save_json(path, package)
    with pytest.raises(ValueError, match="veterinário"):
        stream.ReadyCases(tmp_path)
    path.unlink()
    with pytest.raises(FileNotFoundError):
        stream.ReadyCases(tmp_path)


def fake_runtime(monkeypatch):
    app = Mock()
    app.app_context.side_effect = nullcontext
    monkeypatch.setattr("medlab.create_app", Mock(return_value=app))
    incorporated = set()
    def integrate(output, processed, **kwargs):
        assert kwargs["ready_only"] is True and kwargs["apply"] is True
        incorporated.update(kwargs["pmcids"])
        expected = len(json.loads((Path(output) / "plan.json").read_text())["activeIds"])
        return {"completed": len(incorporated), "pending": expected - len(incorporated)}
    integration = Mock(side_effect=integrate)
    monkeypatch.setattr("scripts.integrate_local_corpus.integrate_corpus", integration)
    def build(output, *, pmcids, translated_cache, **kwargs):
        values, metadata = translated_cache
        for pmcid in pmcids:
            package = json.loads((Path(output) / "extracted" / (pmcid + ".json")).read_text())
            assert all(stream.text_id(unit["text"]) in values for unit in package["translationUnits"])
        return {"completedIds": pmcids, "errors": []}
    builder = Mock(side_effect=build)
    monkeypatch.setattr(stream, "build", builder)
    return app, integration, builder


def test_stream_never_builds_or_integrates_case_before_its_last_unit(tmp_path, monkeypatch):
    corpus(tmp_path, {"PMC1": ["First.", "Second."], "PMC2": ["First."]}, excluded=["PMC3"])
    stream.save_json(tmp_path / "extracted/PMC3.json", {"pmcid": "PMC3", "translationUnits": [{"text": "First."}]})
    journal = tmp_path / "translations.jsonl"
    journal.write_bytes(line(record("First.")))
    app, integration, builder = fake_runtime(monkeypatch)
    report = stream.stream(tmp_path, tmp_path / "processed", [journal],
                           authorization_reference="Synthetic authorization.", apply=True, watch=False)
    assert builder.call_args.kwargs["pmcids"] == ["PMC2"]
    assert integration.call_args.kwargs["pmcids"] == ["PMC2"]
    assert report["completed"] == 1 and report["pending"] == report["awaitingTranslation"] == 1
    assert app.app_context.call_count == 1


def test_stream_rejects_package_changed_after_indexing(tmp_path, monkeypatch):
    corpus(tmp_path, {"PMC1": ["First."]})
    journal = tmp_path / "translations.jsonl"
    journal.write_bytes(line(record("First.")))
    _, integration, builder = fake_runtime(monkeypatch)
    original = stream.TranslationTail.read
    def change_package(self, path):
        added = original(self, path)
        package = tmp_path / "extracted/PMC1.json"
        value = json.loads(package.read_text())
        value["newSourceInformation"] = True
        stream.save_json(package, value)
        return added
    monkeypatch.setattr(stream.TranslationTail, "read", change_package)
    with pytest.raises(ValueError, match="Pacote mudou"):
        stream.stream(tmp_path, tmp_path / "processed", [journal],
                      authorization_reference="Synthetic authorization.", apply=True)
    builder.assert_not_called()
    integration.assert_not_called()
