#!/usr/bin/env python3
"""Recover failed or numerically flagged units with a separate local cache.

Uses the same pinned Marian model in a separate cache. Quantities, identifiers,
line breaks and table separators bypass inference; only English prose fragments
are translated. This is an explicitly marked recovery, not clinical validation.
The source journal is read-only. A low-thread recovery worker can run alongside
the main worker without opening its cache or writing to its journal.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
import tempfile

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.local_translate_cases import (
    DEFAULT_ROOT, LocalTranslator, RECOVERY_WARNINGS, _PROTECTED, canonical, digest, needs_recovery, numeric_warnings, translate_jsonl,
)

RECOVERY_VERSION = "literal-atoms-prose-fragments-v1"
# Preserve gene/protein IDs, conventional capitals, scientific units and source
# layout. Never infer table cells/rows from a flattened sentence.
_LITERAL = re.compile(
    r"\r\n|\r|\n|\t|\b[A-Za-z][A-Za-z0-9]*[0-9][A-Za-z0-9]*\b|"
    r"\b[A-Z][A-Z0-9-]+\b|"
    r"\b(?:mmHg|mmol|mEq|mcg|mg|kg|ng|pg|cm|mm|mL|ml|dL|dl|µL|μL|μl|µl|IU|gm|mIU|m|g|L|l|y)"
    r"(?:/(?:[A-Za-zμµ]+))*(?![A-Za-z])|" + _PROTECTED.pattern)
_WORD = re.compile(r"[A-Za-zÀ-ÿ]+(?:['’-][A-Za-zÀ-ÿ]+)*")


def recovery_parts(text):
    """Return ordered (literal,text) parts, preserving every boundary byte."""
    parts, cursor = [], 0
    for match in _LITERAL.finditer(text):
        parts.extend(((False, text[cursor:match.start()]), (True, match.group(0))))
        cursor = match.end()
    parts.append((False, text[cursor:]))
    bounded = []
    for literal, part in parts:
        words = list(_WORD.finditer(part))
        if literal or not words:
            bounded.append((True, part))
            continue
        start, stop = words[0].start(), words[-1].end()
        bounded.append((True, part[:start]))
        # Short fragments avoid marker loss/EOS stalls. Preserve punctuation and
        # whitespace between chunks rather than rebuilding a flattened table.
        for index in range(0, len(words), 12):
            group = words[index:index + 12]
            a, b = group[0].start(), group[-1].end()
            bounded.append((False, part[a:b]))
            following = words[index + 12].start() if index + 12 < len(words) else stop
            bounded.append((True, part[b:following]))
        bounded.append((True, part[stop:]))
    return [(literal, part) for literal, part in bounded if part]


class RecoveryTranslator(LocalTranslator):
    def __init__(self, *, cache_path=None, **kwargs):
        location = Path(cache_path or DEFAULT_ROOT / 'recovery-translations.sqlite3')
        if location.resolve() == (DEFAULT_ROOT / 'translations.sqlite3').resolve():
            raise ValueError('Recovery must use a separate cache, never the active primary cache.')
        super().__init__(cache_path=location, **kwargs)
        self.identity = {**self.identity, 'recovery': RECOVERY_VERSION}

    def translate_records(self, texts, *, allow_errors=False):
        if any(not isinstance(text, str) or not text.strip() for text in texts):
            raise ValueError('Recovery units must contain nonempty text.')
        results = []
        for text in texts:
            saved = self._cached(text)
            if saved is not None and not needs_recovery({'text': text, 'textPt': saved.get('textPt')}):
                results.append({**saved, 'cacheHit': True}); self.stats['recoveryCacheHits'] += 1
                continue
            parts = recovery_parts(text)
            prose = list(dict.fromkeys(part for literal, part in parts if not literal))
            translated = super().translate_records(prose, allow_errors=True)
            mapping = dict(zip(prose, translated))
            if any(value is None for value in translated):
                results.append(None); continue
            output = ''.join(part if literal else mapping[part]['textPt'] for literal, part in parts)
            warnings = numeric_warnings(text, output)
            if RECOVERY_WARNINGS.intersection(warnings):
                results.append(None); continue
            if re.findall(r'\r\n|\r|\n|\t', text) != re.findall(r'\r\n|\r|\n|\t', output):
                results.append(None); continue
            result = {'textPt': output, 'warnings': sorted(set(warnings + ['local_fragment_recovery'])),
                      'segments': len(prose)}
            self._put(text, result); self.cache.commit()
            results.append({**result, 'cacheHit': False}); self.stats['recoveredUnits'] += 1
        if not allow_errors and any(value is None for value in results):
            raise ValueError('Some units still need an explicit recovery; no source text was substituted.')
        return results


def failed_units(journal):
    """Ignore only an interrupted last line; refuse corrupt completed history."""
    seen = set()
    with Path(journal).open(encoding='utf-8') as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except ValueError:
                if line.endswith('\n'):
                    raise ValueError('Completed journal row is corrupt.') from None
                continue
            if not needs_recovery(row):
                continue
            if not isinstance(row.get('text'), str) or not row['text'].strip():
                raise ValueError('Failed unit lacks its original text.')
            original = {key: value for key, value in row.items() if key not in {'textPt', 'translation', 'translationError'}}
            key = (original.get('id'), digest(original['text']))
            if key in seen:
                continue
            seen.add(key)
            original['recoverySource'] = {'version': RECOVERY_VERSION,
                'failedConfigurationSha256': row.get('translation', {}).get('configurationSha256'),
                'sourceSha256': digest(row['text']), 'originalLayoutPreserved': True,
                'clinicalReviewPerformed': False,
                'reason': 'translation_failed' if row.get('translationError') else 'translation_guard_failed'}
            yield original


def completed_unit_keys(paths):
    """Reuse only complete successful units with their original source hash."""
    complete = set()
    for path in paths:
        with Path(path).open(encoding='utf-8') as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except ValueError:
                    if line.endswith('\n'):
                        raise ValueError('Completed recovery history is corrupt.') from None
                    continue
                if needs_recovery(row):
                    continue
                source = row.get('text')
                if not isinstance(source, str) or row.get('translation', {}).get('inputSha256') != digest(source):
                    raise ValueError('Completed translation lacks a matching source hash.')
                complete.add((row.get('id'), digest(source)))
    return complete


def select_failed_units(journal, *, completed=(), limit=None):
    if limit is not None and (isinstance(limit, bool) or not 1 <= limit <= 10000):
        raise ValueError('Recovery limit must be between 1 and 10000 units.')
    keys = completed_unit_keys(completed)
    selected, skipped, pending = [], 0, 0
    for row in failed_units(journal):
        if (row.get('id'), digest(row['text'])) in keys:
            skipped += 1
            continue
        pending += 1
        if limit is None or len(selected) < limit:
            selected.append(row)
    return selected, {'pendingFailedUnits': pending, 'alreadyRecoveredUnits': skipped,
                      'selectedUnits': len(selected)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--journal', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cache', type=Path)
    parser.add_argument('--model-dir', type=Path)
    parser.add_argument('--threads', type=int, default=6)
    parser.add_argument('--limit', type=int, help='Maximum failed or numerically flagged units in this recovery batch.')
    parser.add_argument('--completed', type=Path, action='append', default=[], help='Previous successful output; repeat for multiple batches.')
    parser.add_argument('--list-only', action='store_true', help='Count pending recovery units without loading a model.')
    args = parser.parse_args()
    if args.journal.resolve() == args.output.resolve():
        parser.error('Recovery output must differ from the original journal.')
    rows, selection = select_failed_units(args.journal, completed=args.completed, limit=args.limit)
    if args.list_only or not rows:
        print(canonical({**selection, 'failedUnits': len(rows), 'sourceCharacters': sum(len(row['text']) for row in rows),
                         'modelLoaded': False})); return
    if args.output.exists():
        parser.error('Choose a new output for each batch; use --completed to reuse previous successes.')
    args.output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', suffix='.jsonl',
                                     prefix='.recovery-input-', dir=args.output.parent) as source:
        for row in rows:
            source.write(canonical(row) + '\n')
        source.flush()
        translator = RecoveryTranslator(cache_path=args.cache, model_dir=args.model_dir,
                                         batch_size=96, beam_size=1, threads=args.threads)
        try:
            result = translate_jsonl(translator, source.name, args.output, unit_batch_size=1)
            print(canonical({**result, **selection}))
        finally:
            translator.close()


if __name__ == '__main__':
    main()
