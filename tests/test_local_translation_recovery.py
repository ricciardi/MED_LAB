"""Explicit recovery preserves source atoms without loading any model."""
from collections import Counter
import json
import sqlite3

import pytest

from scripts.local_translate_cases import canonical, digest
from scripts.retry_local_translation import RecoveryTranslator, failed_units, recovery_parts


class FakeRecovery(RecoveryTranslator):
    def __init__(self, path):
        self.cache = sqlite3.connect(path)
        self.cache.execute('CREATE TABLE translation (key TEXT PRIMARY KEY, source_sha256 TEXT NOT NULL, result TEXT NOT NULL, result_sha256 TEXT NOT NULL, created_at TEXT NOT NULL)')
        self.identity = {'model': 'synthetic', 'recovery': 'literal-atoms-test'}
        self.batch_size = 96; self.max_source_tokens = 320; self.stats = Counter(); self.calls = []
    def _encoded(self, text): return text.split()
    def _infer(self, texts):
        self.calls.extend(texts)
        # Preserve positions: adding a fake preamble next to a protected dose
        # would correctly invalidate its adjacent unit under the stricter guard.
        return [text.replace('history', 'história').replace('treatment', 'tratamento') for text in texts]


def test_table_values_identifiers_units_and_lines_never_enter_inference(tmp_path):
    text = 'Table 1\tDay 1\tDay 3\nWBC 5.6 6.5 4.5–11 (× 10 3 per μl)\nHb 12.4 11.3 gm/dl\nSMARCB1 loss; PMS2 expression.'
    parts = recovery_parts(text)
    assert ''.join(value for _, value in parts) == text
    translator = FakeRecovery(tmp_path / 'recovery.sqlite3')
    output = translator.translate_records([text])[0]
    assert not any(any(char.isdigit() for char in source) for source in translator.calls)
    assert not any(term in source for source in translator.calls for term in ['SMARCB1', 'PMS2', 'WBC', 'μl'])
    assert output['textPt'].count('\n') == text.count('\n') and output['textPt'].count('\t') == 2
    for literal, part in parts:
        if literal: assert part in output['textPt']
    translator.calls.clear()
    assert translator.translate_records([text])[0]['cacheHit'] is True and not translator.calls
    translator.close()


def test_recovery_refuses_model_added_numbers_instead_of_faking_success(tmp_path):
    translator = FakeRecovery(tmp_path / 'recovery.sqlite3')
    translator._infer = lambda texts: ['Inventado 99 ' + text for text in texts]
    assert translator.translate_records(['A patient aged 35 years.'], allow_errors=True) == [None]
    translator.close()


def test_failed_selection_preserves_input_identity_but_not_old_output(tmp_path):
    source = tmp_path / 'journal.jsonl'
    row = {'id': 'same-id', 'text': 'Source 3 mg/dL', 'translationError': 'local_translation_failed',
           'translation': {'configurationSha256': 'a' * 64}}
    source.write_text(canonical({'id': 'okay', 'text': 'Okay', 'textPt': 'Certo'}) + '\n' +
                      canonical(row) + '\n' + canonical(row) + '\n' + '{"interrupted')
    units = list(failed_units(source))
    assert len(units) == 1 and units[0]['id'] == row['id'] and units[0]['text'] == row['text']
    assert 'translation' not in units[0] and 'translationError' not in units[0]
    assert units[0]['recoverySource']['sourceSha256'] == digest(row['text'])
    source.write_text('corrupt\n')
    with pytest.raises(ValueError, match='corrupt'): list(failed_units(source))


def test_recovery_cannot_open_primary_cache():
    from scripts.local_translate_cases import DEFAULT_ROOT
    with pytest.raises(ValueError, match='separate cache'):
        RecoveryTranslator(cache_path=DEFAULT_ROOT / 'translations.sqlite3')


def test_incremental_recovery_skips_exact_successes_and_bounds_each_batch(tmp_path):
    from scripts.retry_local_translation import select_failed_units
    journal = tmp_path / 'errors.jsonl'; previous = tmp_path / 'successful.jsonl'
    rows = [{'id':str(index),'text':f'Clinical source {index}','translationError':'local_translation_failed',
             'translation':{'configurationSha256':'a'*64}} for index in range(5)]
    journal.write_text(''.join(canonical(row)+'\n' for row in rows))
    previous.write_text(canonical({'id':'0','text':rows[0]['text'],'textPt':'Fonte clínica 0',
                                   'translation':{'inputSha256':digest(rows[0]['text'])}})+'\n')
    selected, counts = select_failed_units(journal,completed=[previous],limit=2)
    assert [row['id'] for row in selected] == ['1','2']
    assert counts == {'pendingFailedUnits':4,'alreadyRecoveredUnits':1,'selectedUnits':2}
    # A completed ID with another source cannot silently satisfy the current unit.
    previous.write_text(canonical({'id':'0','text':'Changed source','textPt':'Texto diferente',
                                   'translation':{'inputSha256':digest('Changed source')}})+'\n')
    selected, _ = select_failed_units(journal,completed=[previous],limit=1)
    assert selected[0]['id']=='0'


def test_numeric_warning_is_pending_and_cannot_be_marked_completed(tmp_path):
    from scripts.retry_local_translation import select_failed_units
    row={'id':'fraction','text':'A 2½-year history.','textPt':'Uma história de 212 anos.',
         'translation':{'inputSha256':digest('A 2½-year history.'),'warnings':['numeric_tokens_changed']}}
    source=tmp_path/'journal.jsonl';source.write_text(canonical(row)+'\n')
    selected,counts=select_failed_units(source,completed=[source])
    assert len(selected)==1 and counts['alreadyRecoveredUnits']==0
    assert selected[0]['recoverySource']['reason']=='translation_guard_failed'
    assert 'textPt' not in selected[0]


def test_recovery_preserves_unicode_mixed_fractions_as_atoms(tmp_path):
    text='A 2½-year history; treatment for 7½ months; ⅓ tablet.'
    translator=FakeRecovery(tmp_path/'cache.sqlite3')
    result=translator.translate_records([text])[0]
    assert '2½' in result['textPt'] and '7½' in result['textPt'] and '⅓' in result['textPt']
    assert not any('½' in part or '⅓' in part for part in translator.calls)
    assert 'numeric_tokens_changed' not in result['warnings']
    translator.close()


def test_recovery_selection_rechecks_legitimate_written_number_warning(tmp_path):
    from scripts.retry_local_translation import select_failed_units
    row = {'id': 'written-number', 'text': 'Thirty-six patients.', 'textPt': '36 pacientes.',
           'translation': {'inputSha256': digest('Thirty-six patients.'), 'warnings': ['numeric_tokens_changed']}}
    source = tmp_path / 'journal.jsonl'; source.write_text(canonical(row) + '\n')
    selected, counts = select_failed_units(source, completed=[source])
    assert selected == [] and counts['pendingFailedUnits'] == 0


def test_recovery_selection_detects_unflagged_fraction_loss(tmp_path):
    row = {'id': 'fraction', 'text': '⅓ tablet.', 'textPt': 'Comprimido.', 'translation': {'warnings': []}}
    source = tmp_path / 'journal.jsonl'; source.write_text(canonical(row) + '\n')
    assert len(list(failed_units(source))) == 1


def test_recovery_does_not_loop_on_old_cached_unit_loss(tmp_path):
    text = 'Level <6 mg/dL and 73 µmol/L.'
    translator = FakeRecovery(tmp_path / 'cache.sqlite3')
    translator._put(text, {'textPt': 'Level 6 mg/dL and 73 mol/L.', 'warnings': [], 'segments': 1})
    translator.cache.commit()
    result = translator.translate_records([text])[0]
    assert result['cacheHit'] is False
    assert '<6 mg/dL' in result['textPt'] and '73 µmol/L' in result['textPt']
    translator.close()


def test_recovery_keeps_compact_ascii_powers_out_of_inference(tmp_path):
    text = 'Value 10^9/L; diffusivity 10^−3mm^2/s.'
    translator = FakeRecovery(tmp_path / 'cache.sqlite3')
    result = translator.translate_records([text])[0]
    assert '10^9/L' in result['textPt'] and '10^−3mm^2/s' in result['textPt']
    assert not any('^' in value for value in translator.calls)
    translator.close()
