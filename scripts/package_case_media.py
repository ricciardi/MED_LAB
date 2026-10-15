#!/usr/bin/env python3
"""Exportar/restaurar originais e metadados privados de mídia, sem alterar banco."""
import argparse
import json
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from medlab.media_bundle import export_media, restore_media
from medlab.source_media import SourceMediaError


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    exp = sub.add_parser('export')
    exp.add_argument('--media-dir', type=Path, default=ROOT / 'instance/imports/MedCaseReasoning/media')
    exp.add_argument('--output', type=Path, required=True)
    res = sub.add_parser('restore')
    res.add_argument('--archive', type=Path, required=True)
    res.add_argument('--sha256', required=True)
    res.add_argument('--media-dir', type=Path, default=ROOT / 'instance/imports/MedCaseReasoning/media')
    args = parser.parse_args()
    try:
        result = export_media(args.media_dir, args.output) if args.command == 'export' else restore_media(args.archive, args.media_dir, expected_sha256=args.sha256)
    except (SourceMediaError, OSError) as exc:
        print(str(exc) if isinstance(exc, SourceMediaError) else 'Falha de acesso aos arquivos privados.', file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
