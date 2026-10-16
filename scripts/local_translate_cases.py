#!/usr/bin/env python3
"""Private, local EN→PT-BR translation. No Flask, API keys or remote inference.

Use the separate preparation environment, never the cPanel application runtime.
The model must already exist locally. A single long-lived translator batches
segments and commits its cache after each successful batch. ND source selection
is the extractor's responsibility: this module only translates explicit units.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
from fractions import Fraction
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
MODEL_ID = "Helsinki-NLP/opus-mt-tc-big-en-pt"
MODEL_REVISION = "9f2863d807ecf91a374bdbecb8d01e402e90622e"
DEFAULT_ROOT = ROOT / "instance/local-preparation"
VERSION = "marian-clinical-segments-v3-protected-values-references"
_FRACTIONS = "¼½¾⅐⅑⅒⅓⅔⅕⅖⅗⅘⅙⅚⅛⅜⅝⅞"
_EXPONENT = r'(?:\^[+−-]?[0-9]+|[⁰¹²³⁴⁵⁶⁷⁸⁹⁻⁺]+)'
_NUMBER = rf"(?:\d+(?:[.,]\d+)*(?:[{_FRACTIONS}])?|[{_FRACTIONS}])(?:{_EXPONENT})?"
_QUANTITY = re.compile(_NUMBER + r"(?:\s*(?:[×x/–—−-])\s*" + _NUMBER +
                       r")*(?:\s+\d+)?(?:\s*(?:/\s*)?(?:mmHg|mmol|mEq|mcg|mg|kg|ng|pg|cm|mm|mL|ml|dL|µL|μL|L|IU|U|g|%)(?![A-Za-z])(?:\s*\d+)?(?:\s*/\s*(?:[A-Za-zμµ]+)(?:\s*\d+)?)*)?")
_PROTECTED = re.compile(r"\[\s*\d+(?:\s*[,–—-]\s*\d+)*\s*\]|\(\s*(?i:fig(?:ure)?s?|table)\.\s*$|" + _QUANTITY.pattern)
_FRACTION_VALUES = dict(zip(_FRACTIONS, (Fraction(n, d) for n, d in (
    (1, 4), (1, 2), (3, 4), (1, 7), (1, 9), (1, 10), (1, 3), (2, 3),
    (1, 5), (2, 5), (3, 5), (4, 5), (1, 6), (5, 6), (1, 8), (3, 8), (5, 8), (7, 8)))))
_SCRIPT_DIGITS = str.maketrans('₀₁₂₃₄₅₆₇₈₉⁰¹²³⁴⁵⁶⁷⁸⁹', '01234567890123456789')
_NUMERIC_ATOM = re.compile(rf"[0-9]+(?:[.,][0-9]+)?(?:[{_FRACTIONS}])?|[{_FRACTIONS}]")
_SMALL_NUMBERS = dict(zip(('zero one two three four five six seven eight nine ten eleven twelve '
                           'thirteen fourteen fifteen sixteen seventeen eighteen nineteen').split(), range(20)))
_TENS = dict(zip('twenty thirty forty fifty sixty seventy eighty ninety'.split(), range(20, 100, 10)))
_ORDINALS = dict(zip(('first second third fourth fifth sixth seventh eighth ninth tenth eleventh twelfth '
                       'thirteenth fourteenth fifteenth sixteenth seventeenth eighteenth nineteenth').split(),
                      list(_SMALL_NUMBERS)[1:]))
_ORDINALS.update(dict(zip('twentieth thirtieth fortieth fiftieth sixtieth seventieth eightieth ninetieth hundredth thousandth'.split(),
                         list(_TENS) + ['hundred', 'thousand'])))
# Keep unsupported numeric modifiers in the candidate phrase so that parsing
# "one half" cannot silently grant an allowance for the value one alone.
_NUMBER_WORDS = set(_SMALL_NUMBERS) | set(_TENS) | set(_ORDINALS) | {
    'hundred', 'thousand', 'and', 'million', 'billion', 'trillion',
    'point', 'decimal', 'half', 'halves', 'quarter', 'quarters',
}
RECOVERY_WARNINGS = frozenset({'numeric_tokens_changed', 'clinical_units_changed'})
_UNIT_GROUPS = {
    'pg': 'pg picogram picograms picograma picogramas',
    'ng': 'ng nanogram nanograms nanograma nanogramas',
    'µg': 'µg μg mcg microgram micrograms micrograma microgramas',
    'mg': 'mg milligram milligrams miligrama miligramas',
    'g': 'g gm gram grams grama gramas',
    'kg': 'kg kilogram kilograms quilograma quilogramas',
    'lb': 'lb lbs pound pounds libra libras',
    'oz': 'oz ounce ounces onça onças',
    'µL': 'µl μl ul microliter microliters microlitre microlitres microlitro microlitros',
    'mL': 'ml milliliter milliliters millilitre millilitres mililitro mililitros',
    'dL': 'dl deciliter deciliters decilitre decilitres decilitro decilitros',
    'L': 'l liter liters litre litres litro litros',
    'µmol': 'µmol μmol umol micromole micromoles micromol micromols',
    'mmol': 'mmol millimole millimoles milimol milimols',
    'mol': 'mol mole moles',
    'mEq': 'meq miliequivalente miliequivalentes milliequivalent milliequivalents',
    'IU': 'iu ui', 'U': 'u', 'mmHg': 'mmhg',
    'µm': 'µm μm um micrometer micrometers micrometre micrometres micrômetro micrômetros',
    'mm': 'mm millimeter millimeters millimetre millimetres milímetro milímetros',
    'cm': 'cm centimeter centimeters centimetre centimetres centímetro centímetros',
    'm': 'm meter meters metre metres metro metros',
    'km': 'km kilometer kilometers kilometre kilometres quilômetro quilômetros',
    'year': 'year years yr yrs ano anos',
    'month': 'month months mês meses', 'week': 'week weeks semana semanas',
    'day': 'day days dia dias', 'hour': 'hour hours hr hrs hora horas',
    'minute': 'minute minutes min minuto minutos', 'second': 'second seconds sec s segundo segundos',
    'degree': 'degree degrees grau graus °', '%': '%',
}
_UNIT_ALIASES = {alias.casefold(): unit for unit, aliases in _UNIT_GROUPS.items() for alias in aliases.split()}
_UNIT_NAME = '(?:' + '|'.join(re.escape(name) for name in sorted(_UNIT_ALIASES, key=len, reverse=True)) + ')'
_UNIT_COMPONENT = _UNIT_NAME + r'(?![A-Za-zÀ-ÿµμ])(?:[²³⁻¹⁰¹²³⁴⁵⁶⁷⁸⁹]|\^?[23])?'
_UNIT_SUFFIX = re.compile(r'\s*[-‐‑–—−]?\s*(?P<unit>' + _UNIT_COMPONENT + r'(?:\s*/\s*' + _UNIT_COMPONENT + r')*)', re.I)
_UNIT_PIECE = re.compile('(?P<name>' + _UNIT_NAME + r')(?P<power>.*)', re.I)
_COMPARATOR_FORMS = {
    '<=': r'(?:<=|≤|⩽|less\s+than\s+or\s+equal\s+to|menor\s+ou\s+igual\s+a|at\s+most|no\s+máximo)',
    '>=': r'(?:>=|≥|⩾|greater\s+than\s+or\s+equal\s+to|maior\s+ou\s+igual\s+a|at\s+least|pelo\s+menos|ao\s+menos)',
    '<': r'(?:<|less\s+than|below|menor\s+que|menor\s+do\s+que|menos\s+de|inferior\s+a|abaixo\s+de)',
    '>': r'(?:>|greater\s+than|more\s+than|above|maior\s+que|maior\s+do\s+que|mais\s+de|superior\s+a|acima\s+de)',
}
_COMPARATORS = [(key, re.compile(pattern + r'\s*$', re.I)) for key, pattern in _COMPARATOR_FORMS.items()]


def _unit_after(text, end):
    match = _UNIT_SUFFIX.match(text, end)
    if not match:
        return None
    parts = []
    for part in re.split(r'\s*/\s*', match['unit']):
        piece = _UNIT_PIECE.fullmatch(part)
        power = piece['power'].translate(_SCRIPT_DIGITS).replace('^', '').replace('⁻', '-')
        parts.append(_UNIT_ALIASES[piece['name'].casefold()] + (f'^{power}' if power else ''))
    return '/'.join(parts)


def _qualifier_before(text, start):
    prefix = text[max(0, start - 100):start]
    # An explicit interval connector is distinct from two independent values.
    if re.search(r'[0-9' + _FRACTIONS + r']\s*(?:[-–—−]|\bto\b|\ba\b|\baté\b)\s*$', prefix, re.I):
        return ('range_end', '')
    sign = re.search(r'(?<![\w])(?P<sign>[-−±+])\s*$', prefix)
    polarity = ''
    if sign:
        polarity = {'-': 'negative', '−': 'negative', '±': 'plus_minus', '+': ''}[sign['sign']]
        prefix = prefix[:sign.start()]
    if prefix.rstrip().endswith('^'):
        return ('exponent', polarity)
    for qualifier, pattern in _COMPARATORS:
        if pattern.search(prefix):
            return (qualifier, polarity)
    return ('', polarity)


def _clinical_atoms(text, *, written=False):
    text = _normalized_digits(text)
    occurrences = (_english_number_occurrences(text) if written else
                   ((_atom_value(match.group()), match.start(), match.end()) for match in _NUMERIC_ATOM.finditer(text)))
    result = Counter()
    for value, start, end in occurrences:
        unit = _unit_after(text, end)
        if unit is None and re.search(r'\b(?:age|aged)\s+(?:of\s+)?$', text[:start], re.I):
            unit = 'year'
        result[(value, unit, _qualifier_before(text, start))] += 1
    return result


# Copy only symbols, not English unit words such as "pounds" or "years".
# Their translated aliases are checked above, while their prose stays translatable.
_SYMBOL_NAMES = 'pg ng µg μg mcg mg g gm kg lb lbs oz µl μl ul ml dl l µmol μmol umol mmol mol meq iu ui u mmhg µm μm um mm cm m km s %'.split()
_SYMBOL_BASE = '(?:' + '|'.join(re.escape(name) for name in sorted(_SYMBOL_NAMES, key=len, reverse=True)) + r')(?![A-Za-zÀ-ÿµμ])'
_SYMBOL_UNIT = _SYMBOL_BASE + '(?:' + _EXPONENT + ')?'
_CLINICAL_PROTECTED = (r'(?:(?:<=|>=|[<>≤≥⩽⩾])\s*)?(?:[-−±+])?' + _NUMBER +
                      r'(?:\s*(?:[×x/–—−-])\s*' + _NUMBER + r')*(?:\s+\d+)?' +
                      r'(?:\s*(?:/\s*)?' + _SYMBOL_UNIT + r'(?:\s*\d+)?(?:\s*/\s*' + _SYMBOL_UNIT + r'(?:\s*\d+)?)*)?')
_PROTECTED = re.compile(r'\[\s*\d+(?:\s*[,–—-]\s*\d+)*\s*\]|\(\s*(?i:fig(?:ure)?s?|table)\.\s*$|' +
                        _CLINICAL_PROTECTED + '|' + _SYMBOL_BASE + _EXPONENT + r'(?:\s*/\s*' + _SYMBOL_UNIT + r')*|' + _QUANTITY.pattern, re.I)


def _normalized_digits(text):
    # A power after an ordinary digit is a separate numeric atom: 10⁹ is not 109.
    text = re.sub(r'(?<=[0-9])(?=[⁰¹²³⁴⁵⁶⁷⁸⁹])', '^', text)
    return text.translate(_SCRIPT_DIGITS)


def _numeric_atoms(text):
    return Counter(_atom_value(match.group()) for match in _NUMERIC_ATOM.finditer(_normalized_digits(text)))


def _atom_value(token):
    if token[-1] in _FRACTION_VALUES:
        return Fraction(token[:-1].replace(',', '.') or '0') + _FRACTION_VALUES[token[-1]]
    return Fraction(token.replace(',', '.'))


def _english_number(words):
    """A narrow grammar; ambiguous word sequences grant no numeric allowance."""
    words = list(words)
    if any(word in _ORDINALS for word in words[:-1]):
        return None
    if words and words[-1] in _ORDINALS:
        words[-1] = _ORDINALS[words[-1]]

    def under_hundred(parts):
        if len(parts) == 1:
            return _SMALL_NUMBERS.get(parts[0], _TENS.get(parts[0]))
        if len(parts) == 2 and parts[0] in _TENS and 0 < _SMALL_NUMBERS.get(parts[1], 0) < 10:
            return _TENS[parts[0]] + _SMALL_NUMBERS[parts[1]]
        return None

    def under_thousand(parts):
        if len(parts) >= 2 and parts[1] == 'hundred' and 0 < _SMALL_NUMBERS.get(parts[0], 0) < 10:
            remainder = parts[2:]
            if remainder[:1] == ['and']:
                remainder = remainder[1:]
                if not remainder:
                    return None
            tail = under_hundred(remainder) if remainder else 0
            return None if tail is None else 100 * _SMALL_NUMBERS[parts[0]] + tail
        return under_hundred(parts)

    if words.count('thousand') == 1:
        index = words.index('thousand')
        lead, tail = under_thousand(words[:index]), words[index + 1:]
        if tail[:1] == ['and']:
            tail = tail[1:]
            if not tail:
                return None
        remainder = under_thousand(tail) if tail else 0
        return None if not lead or remainder is None else lead * 1000 + remainder
    return under_thousand(words)


def _english_number_occurrences(text):
    """Return supported EN values with their source boundaries for unit checks."""
    text = text.casefold()
    occurrences, group, previous = [], [], 0

    def flush():
        value = _english_number([item[0] for item in group])
        if value is not None:
            occurrences.append((Fraction(value), group[0][1], group[-1][2]))
        group.clear()

    for match in re.finditer(r'[A-Za-z]+', text):
        word = match.group()
        # "1 second" is a duration, not an extra ordinal value of two.
        if word == 'second' and re.search(r'(?:[0-9]|\b(?:a|an))\s+$', text[:match.start()]):
            flush()
            previous = match.end()
            continue
        if word not in _NUMBER_WORDS or (group and not re.fullmatch(r'[\s\-‐‑–]+', text[previous:match.start()])):
            flush()
        if word in _NUMBER_WORDS and (word != 'and' or group):
            group.append((word, match.start(), match.end()))
        previous = match.end()
    flush()
    return occurrences


def _english_number_pool(text):
    """Written EN numbers may explain added digits, never missing literal ones."""
    return Counter(value for value, _, _ in _english_number_occurrences(text))


def protect_quantities(text):
    """Keep source values, ranges, powers and common medical units byte-for-byte."""
    protected, cursor, pieces = {}, 9871, []
    previous = 0
    for match in _PROTECTED.finditer(text):
        while str(cursor) in text or str(cursor) in protected:
            cursor -= 1
        marker = str(cursor); cursor -= 1
        protected[marker] = match.group(0)
        pieces.extend((text[previous:match.start()], marker))
        previous = match.end()
    pieces.append(text[previous:])
    return "".join(pieces), protected


def restore_quantities(text, protected):
    result = text
    for marker, literal in protected.items():
        # Some models format a marker as 9,871 or 9.871; it still needs to
        # occur exactly once. Missing/duplicated markers require fragmentation.
        pattern = re.compile(r"(?<!\d)" + "[.,]?".join(marker) + r"(?!\d)")
        if len(pattern.findall(result)) != 1:
            return None
        result = pattern.sub(lambda _: literal, result)
    return result


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def numeric_warnings(source, translated):
    """Observable review hints, not automatic correction or semantic approval."""
    original, rendered = _numeric_atoms(source), _numeric_atoms(translated)
    warnings = []
    # Every literal value (including its multiplicity) must survive. Only the
    # surplus digits can be justified by a recognized written source number.
    if original - rendered or (rendered - original) - _english_number_pool(source):
        warnings.append("numeric_tokens_changed")
    original_atoms, rendered_atoms = _clinical_atoms(source), _clinical_atoms(translated)
    if original_atoms - rendered_atoms or (rendered_atoms - original_atoms) - _clinical_atoms(source, written=True):
        warnings.append('clinical_units_changed')
    if len(translated) > max(100, len(source) * 4):
        warnings.append("unusually_long_translation")
    if translated.strip().casefold() == source.strip().casefold() and len(source.split()) > 3:
        warnings.append("unchanged_text")
    return warnings


def needs_recovery(row):
    """Re-evaluate immutable journal text with today's guard, not old warnings."""
    if row.get('translationError'):
        return True
    source, translated = row.get('text'), row.get('textPt')
    if not isinstance(source, str) or not source.strip() or not isinstance(translated, str) or not translated.strip():
        return True
    return bool(RECOVERY_WARNINGS.intersection(numeric_warnings(source, translated)))


class TranslationBatchError(ValueError):
    """Aligned results retain completed items when another item is invalid."""

    def __init__(self, results):
        super().__init__("Some local translation segments failed validation.")
        self.results = results


class LocalTranslator:
    """One persistent model and private SQLite cache; not shared across threads."""

    def __init__(self, *, model_dir=None, cache_path=None, backend="ct2", device="cpu",
                 batch_size=24, beam_size=2, max_source_tokens=320, threads=6):
        if backend not in {"ct2", "torch"} or device not in {"cpu", "mps"}:
            raise ValueError("Unsupported local translation backend/device.")
        if backend == "ct2" and device != "cpu":
            raise ValueError("CTranslate2 uses CPU on Apple Silicon; MPS requires torch.")
        if not 1 <= batch_size <= 128 or not 1 <= beam_size <= 8 or not 32 <= max_source_tokens <= 480:
            raise ValueError("Invalid batch, beam or segment limit.")
        self.model_dir = Path(model_dir or DEFAULT_ROOT / "models/opus-mt-tc-big-en-pt")
        acquisition = json.loads((self.model_dir / "acquisition.json").read_text())
        if acquisition.get("model") != MODEL_ID or acquisition.get("revision") != MODEL_REVISION:
            raise ValueError("Local model provenance does not match the pinned model.")
        self.batch_size, self.max_source_tokens = batch_size, max_source_tokens
        self.backend, self.device, self.beam_size = backend, device, beam_size
        self.identity = {"version": VERSION, "model": MODEL_ID, "revision": MODEL_REVISION,
                         "target": ">>pob<<", "backend": backend, "device": device,
                         "computeType": "int8" if backend == "ct2" else "float32",
                         "beamSize": beam_size, "maxSourceTokens": max_source_tokens,
                         "maxOutputTokens": 512, "batchSize": batch_size,
                         "runtime": {name: importlib.metadata.version(name) for name in
                                     ("transformers", "sentencepiece", "ctranslate2" if backend == "ct2" else "torch")}}
        cache_path = Path(cache_path or DEFAULT_ROOT / "translations.sqlite3")
        cache_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if cache_path.is_symlink():
            raise ValueError("Cache must not be a symbolic link.")
        self.cache = sqlite3.connect(cache_path)
        cache_path.chmod(0o600)
        self.cache.execute("PRAGMA journal_mode=WAL")
        self.cache.execute("CREATE TABLE IF NOT EXISTS translation (key TEXT PRIMARY KEY, source_sha256 TEXT NOT NULL, result TEXT NOT NULL, result_sha256 TEXT NOT NULL, created_at TEXT NOT NULL)")
        self.cache.commit()
        for suffix in ("-wal", "-shm"):
            sidecar = Path(str(cache_path) + suffix)
            if sidecar.exists():
                sidecar.chmod(0o600)
        self.stats = Counter()
        self.stats["inferenceSeconds"] = 0.0
        self._inference_warnings = {}
        from transformers import MarianTokenizer
        self.tokenizer = MarianTokenizer.from_pretrained(self.model_dir, local_files_only=True)
        started = time.perf_counter()
        if backend == "ct2":
            import ctranslate2
            self.model = ctranslate2.Translator(str(self.model_dir / "ct2-int8"), device="cpu",
                                               compute_type="int8", intra_threads=threads, inter_threads=1)
        else:
            import torch
            from transformers import MarianMTModel
            if device == "mps" and not torch.backends.mps.is_available():
                raise ValueError("MPS is not available in this environment.")
            torch.set_num_threads(threads)
            self.model = MarianMTModel.from_pretrained(self.model_dir, local_files_only=True,
                                                       torch_dtype=torch.float32).to(device).eval()
        self.stats["modelLoadSeconds"] = time.perf_counter() - started

    def close(self):
        self.cache.close()

    def _key(self, text):
        return digest(canonical({"configuration": self.identity, "text": text}))

    def _cached(self, text):
        key = self._key(text)
        row = self.cache.execute("SELECT source_sha256,result,result_sha256 FROM translation WHERE key=?", (key,)).fetchone()
        if row is None:
            return None
        if row[0] != digest(text) or row[2] != digest(row[1]):
            raise ValueError("Translation cache checksum mismatch.")
        value = json.loads(row[1])
        if not isinstance(value.get("textPt"), str) or not value["textPt"].strip():
            raise ValueError("Invalid cached translation.")
        return value

    def _put(self, text, result):
        raw = canonical(result)
        self.cache.execute("INSERT OR REPLACE INTO translation VALUES (?,?,?,?,?)",
                           (self._key(text), digest(text), raw, digest(raw), datetime.now(timezone.utc).isoformat()))

    def _encoded(self, text):
        return self.tokenizer.encode(">>pob<< " + text, add_special_tokens=True)

    def _segments(self, text):
        # Split on sentence/paragraph boundaries first; long sentences split at
        # original whitespace. Never truncate input or decode a partial token.
        sentences = re.split(r"(?<=[.!?;])\s+(?=[A-Z0-9])|\n+", text.strip())
        segments = []
        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue
            if len(self._encoded(protect_quantities(sentence)[0])) <= self.max_source_tokens:
                segments.append(sentence)
                continue
            current = []
            for word in sentence.split():
                candidate = " ".join([*current, word])
                if len(self._encoded(protect_quantities(candidate)[0])) > self.max_source_tokens:
                    if not current:
                        raise ValueError("An indivisible source word exceeds the token limit.")
                    segments.append(" ".join(current)); current = [word]
                    if len(self._encoded(protect_quantities(word)[0])) > self.max_source_tokens:
                        raise ValueError("An indivisible source word exceeds the token limit.")
                else:
                    current.append(word)
            if current:
                segments.append(" ".join(current))
        return segments

    def _infer_raw(self, texts):
        started = time.perf_counter()
        encoded = [self._encoded(text) for text in texts]
        if any(len(value) > self.max_source_tokens for value in encoded):
            raise ValueError("Source token limit exceeded; truncation is forbidden.")
        if self.backend == "ct2":
            tokens = [self.tokenizer.convert_ids_to_tokens(value) for value in encoded]
            results = self.model.translate_batch(tokens, beam_size=self.beam_size, max_decoding_length=512,
                                                 return_end_token=True, max_batch_size=self.batch_size)
            outputs = [self.tokenizer.convert_tokens_to_ids(item.hypotheses[0]) for item in results]
        else:
            import torch
            inputs = self.tokenizer([">>pob<< " + text for text in texts], return_tensors="pt", padding=True,
                                    truncation=False).to(self.device)
            with torch.inference_mode():
                outputs = self.model.generate(**inputs, num_beams=self.beam_size, max_new_tokens=512,
                                              do_sample=False).cpu().tolist()
        elapsed = time.perf_counter() - started
        translated = self.tokenizer.batch_decode(outputs, skip_special_tokens=True)
        if len(outputs) != len(texts) or len(translated) != len(texts):
            raise RuntimeError("Local model returned an unexpected number of outputs.")
        self.stats["batches"] += 1
        self.stats["translatedSegments"] += len(texts)
        self.stats["sourceTokens"] += sum(map(len, encoded))
        self.stats["outputTokens"] += sum(sum(token != self.tokenizer.pad_token_id for token in value) for value in outputs)
        self.stats["inferenceSeconds"] += elapsed
        results = [text.strip() if self.tokenizer.eos_token_id in output and text.strip() else None
                   for output, text in zip(outputs, translated)]
        if any(value is None for value in results):
            raise TranslationBatchError(results)
        return results

    def _infer(self, texts):
        prepared = [protect_quantities(text) for text in texts]
        try:
            generated = self._infer_raw([text for text, _ in prepared])
        except TranslationBatchError as error:
            generated = error.results
        results = []
        for source, output, (_, quantities) in zip(texts, generated, prepared):
            if output is None:
                results.append(None)
                continue
            restored = restore_quantities(output, quantities)
            if restored is None:
                # Preserve every quantitative atom literally and translate only
                # the surrounding prose when a model loses a marker. This is
                # explicitly recorded because phrasing can become less natural.
                parts, previous = [], 0
                for match in _PROTECTED.finditer(source):
                    parts.append((False, source[previous:match.start()]))
                    parts.append((True, match.group(0))); previous = match.end()
                parts.append((False, source[previous:]))
                prose = [part for literal, part in parts if not literal and re.search(r"[A-Za-z]", part)]
                translated = []
                try:
                    for offset in range(0, len(prose), self.batch_size):
                        translated.extend(self._infer_raw(prose[offset:offset + self.batch_size]))
                except (ValueError, RuntimeError):
                    # Only this source needs separate recovery. Other outputs
                    # from the original batch remain valid and are retained.
                    results.append(None)
                    continue
                iterator = iter(translated); pieces = []
                for literal, part in parts:
                    if literal or not re.search(r"[A-Za-z]", part):
                        pieces.append(part)
                    else:
                        value = next(iterator)
                        pieces.append((" " if part[:1].isspace() else "") + value + (" " if part[-1:].isspace() else ""))
                restored = "".join(pieces).strip()
                self._inference_warnings[source] = ["quantities_preserved_with_fragment_translation"]
                self.stats["quantityFragmentFallbacks"] += 1
            results.append(restored)
        if any(value is None for value in results):
            raise TranslationBatchError(results)
        return results

    def _infer_independent(self, texts):
        """Keep successful outputs; bisect only an opaque whole-batch failure."""
        try:
            return self._infer(texts)
        except TranslationBatchError as error:
            if len(error.results) != len(texts):
                raise RuntimeError("Invalid partial local translation results.") from error
            self.stats["failedSegments"] += sum(value is None for value in error.results)
            return error.results
        except (ValueError, RuntimeError):
            if len(texts) == 1:
                self.stats["failedSegments"] += 1
                return [None]
            self.stats["batchRecoverySplits"] += 1
            midpoint = len(texts) // 2
            return self._infer_independent(texts[:midpoint]) + self._infer_independent(texts[midpoint:])

    def translate_records(self, texts, *, allow_errors=False):
        if any(not isinstance(text, str) or not text.strip() for text in texts):
            raise ValueError("Translation units must contain nonempty strings.")
        unique = list(dict.fromkeys(texts))
        complete, segments_by_text, pending = {}, {}, {}
        for text in unique:
            saved = self._cached(text)
            if saved is not None:
                complete[text] = {**saved, "cacheHit": True}
                self.stats["cacheHits"] += 1
                continue
            try:
                parts = self._segments(text)
            except ValueError:
                complete[text] = None
                continue
            segments_by_text[text] = parts
            for part in parts:
                cached = self._cached(part)
                if cached is not None:
                    pending[part] = cached["textPt"]
                    self.stats["segmentCacheHits"] += 1
                else:
                    pending.setdefault(part, None)
        missing = [part for part, translated in pending.items() if translated is None]
        missing.sort(key=lambda part: len(self._encoded(protect_quantities(part)[0])), reverse=True)
        for offset in range(0, len(missing), self.batch_size):
            batch = missing[offset:offset + self.batch_size]
            for part, translation in zip(batch, self._infer_independent(batch)):
                pending[part] = translation
                if translation is None:
                    continue
                self._put(part, {"textPt": translation, "warnings": numeric_warnings(part, translation) + getattr(self, "_inference_warnings", {}).get(part, []), "segments": 1})
            self.cache.commit()
        for text, parts in segments_by_text.items():
            if any(pending[part] is None for part in parts):
                complete[text] = None
                continue
            result = {"textPt": " ".join(pending[part] for part in parts), "segments": len(parts)}
            result["warnings"] = sorted(set(numeric_warnings(text, result["textPt"]) +
                [warning for part in parts for warning in (self._cached(part) or {}).get("warnings", [])]))
            self._put(text, result)
            complete[text] = {**result, "cacheHit": False}
        self.cache.commit()
        self.stats["requestedUnits"] += len(texts)
        results = [complete[text].copy() if complete[text] is not None else None for text in texts]
        if not allow_errors and any(value is None for value in results):
            raise TranslationBatchError(results)
        return results

    def translate_many(self, texts):
        return [value["textPt"] for value in self.translate_records(texts)]


def _resume_journal(path, identity):
    """Read completed rows; remove only an interrupted final line, never history."""
    completed = []
    if not path.exists():
        return completed
    if path.is_symlink():
        raise ValueError("Partial output cannot be a symbolic link.")
    with path.open("r+b") as handle:
        while True:
            offset = handle.tell(); line = handle.readline()
            if not line:
                break
            try:
                row = json.loads(line)
            except (ValueError, UnicodeDecodeError):
                if line.endswith(b"\n") or handle.read(1):
                    raise ValueError("A completed partial-output row is corrupt.") from None
                handle.seek(offset); handle.truncate(); break
            metadata = row.get("translation", {})
            if metadata.get("configurationSha256") != digest(canonical(identity)):
                raise ValueError("Partial output uses another model/configuration; choose a new output.")
            if not isinstance(metadata.get("unitSha256"), str):
                raise ValueError("Partial output lacks source identity.")
            completed.append((metadata["unitSha256"], bool(row.get("translationError"))))
            if not line.endswith(b"\n"):
                handle.seek(0, os.SEEK_END); handle.write(b"\n")
    return completed


def translate_jsonl(translator, source, output, *, unit_batch_size=48):
    """Stable append-only partial journal; atomic final export and batch cache."""
    import fcntl
    source, output = Path(source), Path(output)
    if source.resolve() == output.resolve():
        raise ValueError("Input and output must differ.")
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    partial = output.with_name(output.stem + ".partial" + output.suffix)
    lock = output.with_name(output.name + ".lock")
    if partial.is_symlink() or lock.is_symlink():
        raise ValueError("Output journal and lock cannot be symbolic links.")
    lock_fd = os.open(lock, os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        completed = _resume_journal(partial, translator.identity)
        count, errors = len(completed), sum(error for _, error in completed)
        fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        os.chmod(partial, 0o600)
        with source.open(encoding="utf-8") as inputs, os.fdopen(fd, "w", encoding="utf-8") as target:
            batch = []; seen = 0
            def flush():
                nonlocal count, errors
                values = translator.translate_records([row["text"] for row in batch], allow_errors=True)
                for row, value in zip(batch, values):
                    metadata = {**translator.identity, "inputSha256": digest(row["text"]),
                                "unitSha256": digest(canonical(row)),
                                "configurationSha256": digest(canonical(translator.identity))}
                    if value is None:
                        result = {**row, "translationError": "local_translation_failed", "translation": metadata}; errors += 1
                    else:
                        result = {**row, "textPt": value.pop("textPt"), "translation": {**metadata, **value}}
                    target.write(canonical(result) + "\n"); count += 1
                target.flush(); os.fsync(target.fileno())
                print(canonical({"units": count, "errors": errors, "resumedRows": len(completed),
                                 "partialOutputPath": str(partial), "stats": dict(translator.stats)}), flush=True)
                batch.clear()
            for line in inputs:
                if not line.strip():
                    continue
                row = json.loads(line)
                if not isinstance(row, dict) or not isinstance(row.get("text"), str) or not row["text"].strip():
                    raise ValueError("Each JSONL unit needs nonempty text.")
                if seen < len(completed):
                    if digest(canonical(row)) != completed[seen][0]:
                        raise ValueError("Input prefix differs from the completed partial output.")
                else:
                    batch.append(row)
                    if len(batch) >= unit_batch_size:
                        flush()
                seen += 1
            if seen < len(completed):
                raise ValueError("Input is shorter than the completed partial output.")
            if batch:
                flush()
        os.replace(partial, output); output.chmod(0o600)
        return {"units": count, "errors": errors, "resumedRows": len(completed),
                "output": str(output), "stats": dict(translator.stats)}
    finally:
        os.close(lock_fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path)
    parser.add_argument("--cache", type=Path)
    parser.add_argument("--backend", choices=("ct2", "torch"), default="ct2")
    parser.add_argument("--device", choices=("cpu", "mps"), default="cpu")
    parser.add_argument("--batch-size", type=int, default=24)
    parser.add_argument("--beam-size", type=int, default=2)
    parser.add_argument("--threads", type=int, default=6)
    args = parser.parse_args()
    translator = LocalTranslator(model_dir=args.model_dir, cache_path=args.cache, backend=args.backend,
                                 device=args.device, batch_size=args.batch_size, beam_size=args.beam_size, threads=args.threads)
    try:
        print(canonical(translate_jsonl(translator, args.input, args.output,
                                      unit_batch_size=max(48, args.batch_size * 2))))
    finally:
        translator.close()


if __name__ == "__main__":
    main()
