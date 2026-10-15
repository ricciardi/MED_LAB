#!/usr/bin/env python3
"""Translate-complete cases flow directly into the definitive database.

Only local journals are consumed. Each case requires every selected translation
unit, is validated against its frozen package, then commits independently using
the resumable integrator. No model or external provider is called here.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.local_corpus import (DEFAULT_OUTPUT, DEFAULT_PROCESSED, build,
                                 checksum, now, package_paths, save_json, text_id)
from scripts.local_translate_cases import needs_recovery


class TranslationTail:
    def __init__(self):
        self.positions = {}
        self.values = {}
        self.priorities = {}
        self.metadata = None
        self.failed = set()

    def read(self, path):
        """Read complete appended records; follow atomic partial→final rename."""
        path = Path(path)
        partial = path.with_name(path.stem + ".partial" + path.suffix)
        actual = path if path.exists() else partial
        if not actual.exists():
            return set()
        try:
            stat = actual.stat()
            handle = actual.open("rb")
        except FileNotFoundError:
            # The producer atomically renames partial→final after its last fsync.
            # Read it on the next pass without advancing the saved cursor.
            return set()
        previous = self.positions.get(str(path))
        offset = previous[1] if previous and previous[0] == stat.st_ino else 0
        if stat.st_size < offset:
            handle.close()
            raise ValueError("O diário de tradução foi truncado durante a leitura.")
        added = set()
        with handle:
            handle.seek(offset)
            while True:
                start = handle.tell()
                line = handle.readline()
                if not line:
                    break
                if not line.endswith(b"\n"):
                    if actual == path and ".partial." not in path.name:
                        raise ValueError("Diário final com registro incompleto.")
                    handle.seek(start)
                    break
                if not line.strip():
                    continue
                item = json.loads(line)
                if item.get("id") != text_id(item["text"]):
                    raise ValueError("Identidade da tradução inválida.")
                identifier = item["id"]
                if not item.get("translationError") and (not isinstance(item.get("textPt"), str) or not item["textPt"].strip()):
                    raise ValueError("Tradução vazia.")
                if needs_recovery(item):
                    if identifier not in self.values:
                        self.failed.add(identifier)
                    continue
                translated, current = item.get("textPt"), item.get("translation", {})
                if not isinstance(translated, str) or not translated.strip():
                    raise ValueError("Tradução vazia.")
                if not isinstance(current, dict) or not current.get("model") or not current.get("revision"):
                    raise ValueError("Tradução sem modelo ou revisão.")
                identity = {key: current[key] for key in ("model", "revision", "target", "backend", "device", "beamSize") if key in current}
                if self.metadata is not None and self.metadata != identity:
                    raise ValueError("Diários misturam configurações de tradução.")
                priority = int(isinstance(item.get("recoverySource"), dict))
                if identifier in self.values and self.values[identifier] != translated:
                    if self.priorities[identifier] == priority:
                        raise ValueError("Traduções conflitantes da mesma unidade.")
                    if self.priorities[identifier] > priority:
                        continue
                self.metadata = identity
                if identifier not in self.values:
                    added.add(identifier)
                self.values[identifier] = translated
                self.priorities[identifier] = priority
                self.failed.discard(identifier)
            self.positions[str(path)] = (stat.st_ino, handle.tell())
        return added


class ReadyCases:
    def __init__(self, output):
        self.waiting = defaultdict(set)
        self.remaining = {}
        self.package_hashes = {}
        self.ready = set()
        for path in package_paths(output):
            package = json.loads(path.read_text())
            if package.get("pmcid") != path.stem or package.get("patientSpecies", {}).get("kind") == "nonhuman":
                raise ValueError("Pacote inválido ou veterinário no plano ativo: " + path.stem)
            units = {text_id(unit["text"]) for unit in package["translationUnits"]}
            if not units:
                raise ValueError("Ficha sem unidades de tradução.")
            self.remaining[path.stem] = len(units)
            self.package_hashes[path.stem] = checksum(path)
            for unit in units:
                self.waiting[unit].add(path.stem)

    def accept(self, units):
        for unit in units:
            for pmcid in self.waiting.pop(unit, ()):
                self.remaining[pmcid] -= 1
                if self.remaining[pmcid] == 0:
                    self.ready.add(pmcid)


def stream(output, processed, journals, *, authorization_reference, apply=False,
           watch=False, interval=2, batch_size=32, operation_id="local-human-corpus-20261015",
           recovery_glob="translations-final-recovery-*.jsonl"):
    from medlab import create_app
    from scripts.integrate_local_corpus import integrate_corpus
    output, processed = Path(output), Path(processed)
    plan_path = output / "plan.json"
    plan_sha = checksum(plan_path)
    plan = json.loads(plan_path.read_text())
    application = create_app()
    index, tail = ReadyCases(output), TranslationTail()
    integrated, rejected = set(), {}
    latest = {}

    def integrate(identifiers):
        with application.app_context():
            return integrate_corpus(output, processed, apply=apply, ready_only=True,
                pmcids=identifiers, operation_id=operation_id,
                authorization_reference=authorization_reference)

    def report():
        value = {"updatedAt": now(), "operationId": operation_id, "planSha256": plan_sha,
            "apply": apply, "translatedUnits": len(tail.values), "failedTranslationUnits": len(tail.failed),
            "expected": len(plan["activeIds"]), "completed": latest.get("completed", 0),
            "pending": latest.get("pending", len(plan["activeIds"])),
            "incorporatedThisRun": len(integrated), "readyToBuild": len(index.ready),
            "awaitingTranslation": sum(count > 0 for count in index.remaining.values()),
            "buildErrors": list(rejected.values()), "providerCalls": 0}
        save_json(output / "stream-state.json", value)
        print(json.dumps(value, ensure_ascii=False), flush=True)
        return value

    if plan["preservedIds"]:
        latest = integrate(plan["preservedIds"])
        integrated.update(plan["preservedIds"])
    report()
    while True:
        if checksum(plan_path) != plan_sha:
            raise ValueError("Plano alterado durante a incorporação; reinicie com o inventário atualizado.")
        paths = list(dict.fromkeys([Path(path) for path in journals] + sorted(output.glob(recovery_glob))))
        new = set()
        for path in paths:
            new.update(tail.read(path))
        index.accept(new)
        while index.ready:
            identifiers = sorted(index.ready)[:batch_size]
            for pmcid in identifiers:
                if checksum(output / "extracted" / (pmcid + ".json")) != index.package_hashes[pmcid]:
                    raise ValueError("Pacote mudou após indexação: " + pmcid)
            result = build(output, authorization_reference=authorization_reference, pmcids=identifiers,
                           translated_cache=(tail.values, tail.metadata))
            completed = result["completedIds"]
            if completed:
                latest = integrate(completed)
                integrated.update(completed)
            for error in result["errors"]:
                rejected[error["pmcid"]] = error
            index.ready.difference_update(identifiers)
            report()
        if new:
            report()
        if len(integrated) == len(plan["activeIds"]):
            return report()
        if not watch:
            return report()
        time.sleep(interval)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--processed", type=Path, default=DEFAULT_PROCESSED)
    parser.add_argument("--journal", type=Path, action="append", required=True)
    parser.add_argument("--authorization-reference", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval", type=float, default=2)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--operation-id", default="local-human-corpus-20261015")
    args = parser.parse_args(argv)
    if args.interval < 0.1 or args.batch_size < 1:
        parser.error("Intervalo e tamanho do lote devem ser positivos.")
    stream(args.output, args.processed, args.journal, authorization_reference=args.authorization_reference,
           apply=args.apply, watch=args.watch, interval=args.interval, batch_size=args.batch_size,
           operation_id=args.operation_id)


if __name__ == "__main__":
    main()
