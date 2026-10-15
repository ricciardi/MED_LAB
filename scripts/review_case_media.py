#!/usr/bin/env python3
"""Selecionar e revisar imagens com DeepSeek; salvar fichas privadas, sem banco/publicação."""
import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
from dotenv import load_dotenv
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from medlab.case_schema import validate_case
from medlab.media_review import MediaReviewer
from medlab.source_media import SourceMediaError, SourceMediaStore, _save_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--cases', type=Path, help='Lista JSON de fichas completas privadas.')
    source.add_argument('--draft-dir', type=Path, help='Pasta com uma ficha completa por arquivo PMC.json.')
    parser.add_argument('--pmcid', action='append')
    parser.add_argument('--limit', type=int, default=50)
    parser.add_argument('--workers', type=int, choices=range(1, 9), default=4)
    parser.add_argument('--refresh', action='store_true', help='Reavaliar mesmo uma revisão concluída; pode cobrar novamente.')
    parser.add_argument('--media-dir', type=Path, default=ROOT / 'instance/imports/MedCaseReasoning/media')
    parser.add_argument('--cache-dir', type=Path, default=ROOT / 'instance/imports/MedCaseReasoning/processed/pmc-cache')
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'instance/imports/MedCaseReasoning/media-prepared-cases')
    parser.add_argument('--report', type=Path, default=ROOT / 'instance/imports/MedCaseReasoning/media-review-report.json')
    args = parser.parse_args()
    if not 1 <= args.limit <= 20000:
        parser.error('--limit deve estar entre 1 e 20000.')
    if args.draft_dir and args.output_dir.resolve() == args.draft_dir.resolve():
        parser.error('Use uma pasta de saída separada; os rascunhos originais serão preservados.')
    try:
        if (ROOT / '.env').is_symlink():
            raise SourceMediaError('O .env deve ser um arquivo local regular.')
        load_dotenv(ROOT / '.env')
        key = os.getenv('DEEPSEEK_API_KEY', '')
        demo = os.getenv('DEMO_MODE', '').lower() in {'1', 'true', 'yes', 'on'}
        if not key or demo:
            raise SourceMediaError('Configure DeepSeek e DEMO_MODE=false antes da revisão automática.')
        values = json.loads(args.cases.read_text()) if args.cases else (json.loads(p.read_text()) for p in sorted(args.draft_dir.glob('PMC*.json')))
        selected = {}
        reusable = 0
        reviewer = MediaReviewer(SourceMediaStore(args.media_dir, args.cache_dir), key, demo_mode=demo)
        requested = set(args.pmcid or [])
        found = set()
        for value in values:
            if args.pmcid and value.get('provenance', {}).get('pmcid') not in args.pmcid:
                continue
            case = validate_case(value)
            if case.get('catalogType') != 'published_case' or case.get('reviewStatus') != 'review_required':
                raise SourceMediaError('A rotina recebe somente casos reais privados pendentes de revisão.')
            found.add(case['provenance']['pmcid'])
            if not args.refresh and not requested:
                cached = args.media_dir / 'reviews' / f'{case["provenance"]["pmcid"]}.json'
                if cached.is_file():
                    prior = json.loads(cached.read_text())
                    _, catalog, _ = reviewer.prepare(case)
                    if reviewer.can_reuse(case, catalog, prior):
                        reusable += 1
                        continue
            selected[case['caseId']] = case
            if len(selected) >= args.limit:
                break
        if args.pmcid and not set(args.pmcid) <= {c['provenance']['pmcid'] for c in selected.values()}:
            raise SourceMediaError('Uma fonte solicitada não está entre as fichas selecionadas; confira IDs e limite.')
        def progress(result):
            case = selected[result['caseId']]
            if result['status'] == 'completed':
                output = deepcopy(case)
                entries = list(output.get('examMedia', []))
                known = {(e['assetId'], e['group'], e['examName']) for e in entries}
                for item in result['examMedia']:
                    identity = (item['assetId'], item['group'], item['examName'])
                    if identity not in known:
                        entries.append(item)
                        known.add(identity)
                if len(entries) > 12:
                    raise SourceMediaError('Mais de 12 imagens selecionadas; revisão manual necessária.')
                if entries:
                    output['examMedia'] = entries
                output = validate_case(output)
                _save_json(args.output_dir / f'{case["provenance"]["pmcid"]}.json', output)
            print(json.dumps({'caseId': result['caseId'], 'status': result['status'],
                'images': len(result['examMedia']), 'pending': len(result.get('pending', [])),
                'reused': result.get('reused', False)}, ensure_ascii=False), flush=True)
        report = reviewer.run(selected.values(), workers=args.workers, refresh=args.refresh, on_progress=progress)
        _save_json(args.report, report)
        print(json.dumps({'processed': len(report['cases']), 'interrupted': report['interrupted'],
            'wallSeconds': report['wallSeconds'], 'usage': report['usage'], 'skippedCompleted': reusable,
            'outputDir': str(args.output_dir)}, ensure_ascii=False, indent=2))
        return 1 if report['interrupted'] or any(c['status'] == 'error' for c in report['cases']) else 0
    except (SourceMediaError, OSError, ValueError, KeyError, TypeError):
        print('Não foi possível revisar as mídias. Confira configuração, fichas, catálogo e snapshots privados.', file=sys.stderr)
        return 1

if __name__ == '__main__':
    raise SystemExit(main())
