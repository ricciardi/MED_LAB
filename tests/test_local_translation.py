"""Cache and interruption guarantees without loading/downloading any model."""
from collections import Counter
import json
import sqlite3

import pytest

from scripts.local_translate_cases import LocalTranslator, digest, numeric_warnings, protect_quantities, restore_quantities, translate_jsonl


class FakeTranslator(LocalTranslator):
    def __init__(self, path, *, identity="model-a", batch_size=2):
        self.identity = {"version": identity}
        self.cache = sqlite3.connect(path)
        self.cache.execute("CREATE TABLE IF NOT EXISTS translation (key TEXT PRIMARY KEY, source_sha256 TEXT NOT NULL, result TEXT NOT NULL, result_sha256 TEXT NOT NULL, created_at TEXT NOT NULL)")
        self.batch_size = batch_size
        self.max_source_tokens = 5
        self.stats = Counter()
        self.calls = []

    def _encoded(self, text):
        return text.split()

    def _infer(self, texts):
        self.calls.append(list(texts))
        if any("BROKEN" in value for value in texts):
            raise RuntimeError("local model error")
        return ["Tradução: " + value for value in texts]


def test_persistent_cache_deduplicates_and_preserves_input_order(tmp_path):
    path = tmp_path / "cache.sqlite3"
    translator = FakeTranslator(path)
    texts = ["First clinical sentence.", "Second sentence.", "First clinical sentence."]
    first = translator.translate_many(texts)
    assert translator.calls == [[texts[0], texts[1]]]
    assert first[0] == first[2]
    translator.close()
    resumed = FakeTranslator(path)
    assert resumed.translate_many(texts) == first
    assert resumed.calls == []
    resumed.close()


def test_cache_configuration_is_part_of_identity(tmp_path):
    path = tmp_path / "cache.sqlite3"
    first = FakeTranslator(path)
    first.translate_many(["A clinical sentence."])
    first.close()
    other = FakeTranslator(path, identity="model-b")
    other.translate_many(["A clinical sentence."])
    assert len(other.calls) == 1
    other.close()


def test_cache_corruption_is_detected(tmp_path):
    translator = FakeTranslator(tmp_path / "cache.sqlite3")
    translator.translate_many(["A clinical sentence."])
    translator.cache.execute("UPDATE translation SET result=?", ('{"textPt":"Alterado"}',))
    translator.cache.commit()
    with pytest.raises(ValueError, match="checksum"):
        translator.translate_many(["A clinical sentence."])
    translator.close()


def test_long_sentence_is_split_without_dropping_words(tmp_path):
    translator = FakeTranslator(tmp_path / "cache.sqlite3")
    text = "one two three four five six seven eight nine ten eleven twelve"
    output = translator.translate_records([text])[0]
    assert output["segments"] == 3
    assert output["textPt"].replace("Tradução: ", "") == text
    assert all(len(segment.split()) <= 5 for batch in translator.calls for segment in batch)
    translator.close()


def test_jsonl_failed_unit_does_not_prevent_independent_units(tmp_path):
    translator = FakeTranslator(tmp_path / "cache.sqlite3")
    source, output = tmp_path / "in.jsonl", tmp_path / "out.jsonl"
    units = [{"id": digest(text), "text": text, "pmcid": "PMC1"} for text in ["Good sentence", "BROKEN sentence", "Another good sentence"]]
    source.write_text("".join(json.dumps(unit) + "\n" for unit in units))
    result = translate_jsonl(translator, source, output, unit_batch_size=3)
    rows = [json.loads(line) for line in output.read_text().splitlines()]
    assert result["units"] == 3 and result["errors"] == 1
    assert rows[0]["textPt"] == "Tradução: Good sentence"
    assert rows[1]["translationError"] == "local_translation_failed"
    assert rows[2]["textPt"] == "Tradução: Another good sentence"
    assert rows[0]["id"] == units[0]["id"] and rows[0]["pmcid"] == "PMC1"
    assert output.stat().st_mode & 0o777 == 0o600
    translator.close()


def test_invalid_json_preserves_previous_export_and_finished_cache(tmp_path):
    translator = FakeTranslator(tmp_path / "cache.sqlite3")
    source, output = tmp_path / "in.jsonl", tmp_path / "out.jsonl"
    output.write_text("previous valid export\n")
    source.write_text('{"text":"First valid sentence"}\nnot json\n')
    with pytest.raises(json.JSONDecodeError):
        translate_jsonl(translator, source, output, unit_batch_size=1)
    assert output.read_text() == "previous valid export\n"
    translator.calls.clear()
    assert translator.translate_many(["First valid sentence"])[0]
    assert translator.calls == []
    translator.close()


def test_numeric_hint_accepts_decimal_comma_but_does_not_hide_changed_values():
    assert numeric_warnings("Level 3.2 mg/L at 18 hours", "Nível 3,2 mg/L às 18 horas") == []
    assert "numeric_tokens_changed" in numeric_warnings("Level 3.2 mg/L", "Nível 32 mg/L")


@pytest.mark.parametrize(('source', 'translated'), [
    ('Thirty-six patients.', '36 pacientes.'),
    ('A four-month history.', 'Uma história de 4 meses.'),
    ('The twenty-first day.', 'O 21º dia.'),
    ('One hundred and twenty-three patients.', '123 pacientes.'),
    ('Two thousand three hundred forty-five participants.', '2345 participantes.'),
    ('And four patients had fever.', 'E 4 pacientes tiveram febre.'),
    ('CO₂ and HbA₁c were measured.', 'CO2 e HbA1c foram medidos.'),
    ('A CD4⁺ count; 10⁹ cells.', 'Uma contagem de CD4+; 10^9 células.'),
    ('A 2½-year history; ¾ mg.', 'História de 2,5 anos; 0,75 mg.'),
    ('⅓ of the tablet.', '⅓ do comprimido.'),
    ('Four doses of 0.5 mg.', '4 doses de 0,5 mg.'),
])
def test_numeric_equivalence_accepts_only_exact_supported_values(source, translated):
    assert 'numeric_tokens_changed' not in numeric_warnings(source, translated)


@pytest.mark.parametrize(('source', 'translated'), [
    ('A 2½-year history.', 'Uma história de 212 anos.'),
    ('⅓ tablet.', 'Comprimido.'),
    ('An 85-year-old patient.', 'Um homem de 85 anos de 1 ano.'),
    ('A count of 123.', 'Uma contagem de 12.'),
    ('A dose of 0.5 mg.', 'Uma dose de 5 mg.'),
    ('Four doses of 0.5 mg.', '4 doses de 0,05 mg.'),
    ('A CD4⁺ count; 10⁹ cells.', 'Uma contagem de CD4+; 109 células.'),
    ('Thirty-six patients.', '37 pacientes.'),
    ('Thirty-six patients.', '36 pacientes e 36 controles.'),
    ('12 patients and twelve controls.', 'Doze pacientes e controles.'),
    ('Symptoms for 1 second.', 'Sintomas por 1 segundo e 2 dias.'),
    ('One and two possible explanations.', '3 explicações possíveis.'),
    ('One half tablet.', '1 comprimido.'),
    ('One point five milligrams.', '1 e 5 miligramas.'),
    ('One hundred million cells.', '100 células.'),
])
def test_numeric_equivalence_never_pays_for_lost_or_invented_literal_values(source, translated):
    assert 'numeric_tokens_changed' in numeric_warnings(source, translated)


def test_recovery_guard_rechecks_text_instead_of_trusting_old_metadata():
    from scripts.local_translate_cases import needs_recovery
    assert not needs_recovery({'text': 'Thirty-six patients.', 'textPt': '36 pacientes.',
                               'translation': {'warnings': ['numeric_tokens_changed']}})
    assert needs_recovery({'text': 'A 2½-year history.', 'textPt': 'Uma história de 212 anos.',
                           'translation': {'warnings': []}})
    assert needs_recovery({'text': 'Four patients.', 'translationError': 'local_translation_failed'})


@pytest.mark.parametrize(('source', 'translated'), [
    ('Base excess -6 mmol/L.', 'Excesso de base 6 mmol/L.'),
    ('A temperature of −2 degrees.', 'Uma temperatura de 2 graus.'),
    ('A value of 73 µmol/L.', 'Um valor de 73 mol/L.'),
    ('A dose of 5 µg.', 'Uma dose de 5 mg.'),
    ('A dose of 5 mg.', 'Uma dose de 5 g.'),
    ('A level of 6 mg/dL.', 'Um nível de 6 mg/mL.'),
    ('A level <6 mg/dL.', 'Um nível de 6 mg/dL.'),
    ('A level ≤6 mg/dL.', 'Um nível <6 mg/dL.'),
    ('A level greater than 6 mg/dL.', 'Um nível menor que 6 mg/dL.'),
    ('Weight loss of 25 pounds.', 'Perda de peso de 25.'),
    ('Weight loss of 25 lbs.', 'Perda de peso de 25 kg.'),
    ('A 12-year-old patient.', 'Um paciente de 12.'),
    ('A range of 2–5 mmol/L.', 'Valores de 2 e 5 mmol/L.'),
    ('Two doses of 5 mg.', '2 doses de 5 g.'),
])
def test_clinical_quantity_guard_blocks_unit_sign_comparator_and_interval_loss(source, translated):
    from scripts.local_translate_cases import needs_recovery
    assert 'clinical_units_changed' in numeric_warnings(source, translated)
    assert needs_recovery({'text': source, 'textPt': translated, 'translation': {'warnings': []}})


@pytest.mark.parametrize(('source', 'translated'), [
    ('Base excess -6 mmol/L.', 'Excesso de base −6 mmol/l.'),
    ('A temperature of -2 degrees.', 'Uma temperatura de −2 graus.'),
    ('A value of 73 µmol/L.', 'Um valor de 73 μmol/l.'),
    ('A dose of 5 µg.', 'Uma dose de 5 microgramas.'),
    ('A dose of 0.5 mg.', 'Uma dose de 0,5 miligramas.'),
    ('A level <6 mg/dL.', 'Um nível menor que 6 mg/dl.'),
    ('A level ≥6 mg/dL.', 'Um nível de pelo menos 6 mg/dL.'),
    ('Weight loss of 25 pounds.', 'Perda de peso de 25 libras.'),
    ('Weight loss of 25 lbs.', 'Perda de peso de 25 lb.'),
    ('A 12-year-old patient.', 'Um paciente de 12 anos.'),
    ('A four-month history.', 'Uma história de 4 meses.'),
    ('A range of 2–5 mmol/L.', 'Um intervalo de 2 a 5 mmol/L.'),
])
def test_clinical_quantity_guard_accepts_exact_aliases_without_converting_units(source, translated):
    from scripts.local_translate_cases import needs_recovery
    assert not needs_recovery({'text': source, 'textPt': translated})


def test_protection_copies_si_and_comparators_but_leaves_english_unit_words_translatable():
    source = 'A 12-year-old lost 25 pounds in 2 days; level <6 mg/dL, -6 mmol/L and 73 µmol/L.'
    encoded, values = protect_quantities(source)
    assert '<6 mg/dL' in values.values() and '-6 mmol/L' in values.values() and '73 µmol/L' in values.values()
    assert 'year-old' in encoded and 'pounds' in encoded and 'days' in encoded
    assert restore_quantities(encoded, values) == source


@pytest.mark.parametrize(('source', 'translated'), [
    ('A 6‐year history.', 'Uma história de 6 anos.'),
    ('A 6‑year history.', 'Uma história de 6ano.'),
    ('Treatment at age 61 and recurrence at age 71.', 'Tratamento aos 61 anos e recidiva aos 71 anos.'),
    ('A patient aged 85.', 'Um paciente de 85 anos.'),
    ('A patient at the age of 61.', 'Um paciente de 61 anos.'),
    ('A patient aged 6 months.', 'Um paciente de 6 meses.'),
])
def test_explicit_age_and_unicode_unit_hyphens_have_exact_year_equivalence(source, translated):
    from scripts.local_translate_cases import needs_recovery
    assert not needs_recovery({'text': source, 'textPt': translated})


@pytest.mark.parametrize(('source', 'translated'), [
    ('A patient aged 85.', 'Um paciente de 85 anos e 1 ano.'),
    ('A count of 61.', 'Uma contagem de 61 anos.'),
    ('A patient aged 6 months.', 'Um paciente de 6 anos.'),
    ('A value of 10^9/L.', 'Um valor de 10-9/L.'),
    ('A value of 10^−3 mm^2/s.', 'Um valor de 10-3 mm-2/s.'),
    ('An area in mm^2.', 'Uma área em mm-2.'),
])
def test_age_and_exponent_equivalence_remains_strict(source, translated):
    from scripts.local_translate_cases import needs_recovery
    assert needs_recovery({'text': source, 'textPt': translated})


def test_ascii_caret_powers_are_protected_as_complete_quantities_and_units():
    source = 'Count 10^9/L; diffusivity 10^−3mm^2/s; area mm^2.'
    encoded, literals = protect_quantities(source)
    assert list(literals.values()) == ['10^9/L', '10^−3mm^2/s', 'mm^2']
    assert '^' not in encoded and restore_quantities(encoded, literals) == source


def test_protection_preserves_clinical_powers_ranges_and_units():
    source = "WBC 4.43 × 10 3 mm 3 /µL; RBC 4.2–6.3 × 10 6 /µL; CRP 0.37 mg/dL."
    protected, values = protect_quantities(source)
    assert list(values.values()) == ["4.43 × 10 3 mm 3 /µL", "4.2–6.3 × 10 6 /µL", "0.37 mg/dL"]
    assert restore_quantities(protected, values) == source
    assert restore_quantities(protected.replace(next(iter(values)), ""), values) is None
    assert restore_quantities(protected + " " + next(iter(values)), values) is None


def test_citation_and_incomplete_figure_reference_are_not_completed_by_model():
    source = "Diagnosis supported by history [ 1–4 , 6 ]. Findings ( Fig."
    protected, values = protect_quantities(source)
    assert list(values.values()) == ["[ 1–4 , 6 ]", "( Fig."]
    assert restore_quantities(protected, values) == source


def test_interrupted_partial_journal_resumes_completed_prefix(tmp_path):
    translator = FakeTranslator(tmp_path / "cache.sqlite3")
    source, output = tmp_path / "in.jsonl", tmp_path / "translations.jsonl"
    source.write_text('{"id":"one","text":"First valid sentence"}\nnot json\n')
    with pytest.raises(json.JSONDecodeError):
        translate_jsonl(translator, source, output, unit_batch_size=1)
    partial = tmp_path / "translations.partial.jsonl"
    assert len(partial.read_text().splitlines()) == 1
    with partial.open("ab") as stream:
        stream.write(b'{"id":"interrupted')
    source.write_text('{"id":"one","text":"First valid sentence"}\n{"id":"two","text":"Second valid sentence"}\n')
    translator.calls.clear()
    result = translate_jsonl(translator, source, output, unit_batch_size=1)
    assert result["resumedRows"] == 1 and result["units"] == 2
    assert translator.calls == [["Second valid sentence"]]
    assert not partial.exists()
    translator.close()


def test_partial_journal_rejects_changed_input_without_overwriting_history(tmp_path):
    translator = FakeTranslator(tmp_path / "cache.sqlite3")
    source, output = tmp_path / "in.jsonl", tmp_path / "translations.jsonl"
    source.write_text('{"text":"First valid sentence"}\ninvalid\n')
    with pytest.raises(json.JSONDecodeError):
        translate_jsonl(translator, source, output, unit_batch_size=1)
    partial = tmp_path / "translations.partial.jsonl"; saved = partial.read_bytes()
    source.write_text('{"text":"Different sentence"}\n')
    with pytest.raises(ValueError, match="prefix differs"):
        translate_jsonl(translator, source, output)
    assert partial.read_bytes() == saved
    translator.close()


def test_partial_segment_failure_keeps_batch_outputs_without_unit_retries(tmp_path):
    from scripts.local_translate_cases import TranslationBatchError

    class PartialTranslator(FakeTranslator):
        def _infer(self, texts):
            self.calls.append(list(texts))
            results = [None if "BROKEN" in text else "Tradução: " + text for text in texts]
            if None in results:
                raise TranslationBatchError(results)
            return results

    translator = PartialTranslator(tmp_path / "cache.sqlite3", batch_size=96)
    texts = [f"Good sentence {index}" for index in range(190)]
    texts[12] = "BROKEN sentence"
    source, output = tmp_path / "in.jsonl", tmp_path / "out.jsonl"
    source.write_text("".join(json.dumps({"id": digest(text), "text": text}) + "\n" for text in texts))
    result = translate_jsonl(translator, source, output, unit_batch_size=192)
    assert result["errors"] == 1
    assert len(translator.calls) == 2
    assert sum(map(len, translator.calls)) == len(texts)
    translator.calls.clear()
    assert len(translator.translate_many([text for text in texts if "BROKEN" not in text])) == 189
    assert translator.calls == []
    translator.close()


def test_failed_multisegment_unit_retains_other_segments_for_later_recovery(tmp_path):
    translator = FakeTranslator(tmp_path / "cache.sqlite3", batch_size=96)
    text = "A useful good segment. BROKEN bad segment. Another helpful good segment."
    assert translator.translate_records([text], allow_errors=True) == [None]
    translator.calls.clear()
    assert translator.translate_records([text], allow_errors=True) == [None]
    assert translator.calls == [["BROKEN bad segment."]]
    translator.close()


def test_raw_invalid_eos_keeps_other_outputs_and_counts_inference():
    from scripts.local_translate_cases import TranslationBatchError
    from types import SimpleNamespace

    class Tokenizer:
        eos_token_id, pad_token_id = 1, 0
        def convert_ids_to_tokens(self, value): return value
        def convert_tokens_to_ids(self, value): return value
        def batch_decode(self, outputs, **kwargs): return ["Válido", "Incompleto", "Também válido"]
    translator = object.__new__(LocalTranslator)
    translator.backend = "ct2"; translator.beam_size = 1; translator.batch_size = 96
    translator.max_source_tokens = 320; translator.stats = Counter()
    translator.tokenizer = Tokenizer()
    translator._encoded = lambda text: [3, 1]
    translator.model = SimpleNamespace(translate_batch=lambda *args, **kwargs: [
        SimpleNamespace(hypotheses=[[3, 1]]), SimpleNamespace(hypotheses=[[4, 5]]),
        SimpleNamespace(hypotheses=[[6, 1]])])
    with pytest.raises(TranslationBatchError) as error:
        translator._infer_raw(["one", "two", "three"])
    assert error.value.results == ["Válido", None, "Também válido"]
    assert translator.stats["batches"] == 1 and translator.stats["translatedSegments"] == 3


def test_unicode_fractions_are_single_literal_quantities_without_cache_version_change():
    from scripts.local_translate_cases import VERSION
    source = 'A 2½-year history; 7½ months; ¾ mg and ⅓ tablet.'
    protected, literals = protect_quantities(source)
    assert list(literals.values()) == ['2½','7½','¾ mg','⅓']
    assert restore_quantities(protected, literals) == source
    assert 'marian-clinical-segments-v3' in VERSION


def test_new_queue_uses_cached_text_even_when_unit_identity_changes(tmp_path):
    translator=FakeTranslator(tmp_path/'cache.sqlite3')
    first=tmp_path/'first.jsonl';second=tmp_path/'canonical.jsonl'
    first.write_text(canonical_for_test({'id':'old','text':'A cached clinical sentence.'})+'\n')
    translate_jsonl(translator,first,tmp_path/'first-output.jsonl')
    translator.calls.clear()
    second.write_text(canonical_for_test({'id':'canonical','text':'A cached clinical sentence.','language':'en'})+'\n')
    result=translate_jsonl(translator,second,tmp_path/'canonical-output.jsonl')
    assert result['errors']==0 and translator.calls==[]
    row=json.loads((tmp_path/'canonical-output.jsonl').read_text())
    assert row['id']=='canonical' and row['translation']['cacheHit'] is True
    translator.close()


def canonical_for_test(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'))
