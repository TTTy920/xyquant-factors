#!/usr/bin/env python3
"""Catalog-based local RankIC: inventory declarations or run a configured test."""
import argparse
import json
from pathlib import Path
import sys

from rankic_engine import Parameters, calculate
from rankic_inputs import DataContractError
from rankic_report import write_reports
from rankic_store import Store


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    inventory = commands.add_parser('inventory', help='inspect catalog declarations, not server acceptance')
    inventory.add_argument('--data-root', required=True, type=Path)
    run = commands.add_parser('run', help='calculate locally from an explicit catalog')
    run.add_argument('--config', required=True, type=Path)
    run.add_argument('--project', required=True, type=Path, help='existing project; outputs under results/rankic/<uuid>')
    args = parser.parse_args(argv)
    try:
        if args.command == 'inventory':
            store = Store(args.data_root)
            payload = store.inventory()
            store.assert_unchanged()
            print(json.dumps(payload, ensure_ascii=False, allow_nan=False))
            return 0
        if not args.project.is_dir():
            raise DataContractError('project must be an existing directory')
        if args.config.stat().st_size > 1024 * 1024:
            raise DataContractError('configuration exceeds size limit')
        with args.config.open(encoding='utf-8-sig') as handle:
            config = json.load(handle)
        if not isinstance(config.get('data_root'), str) or not config['data_root'] or 'data' in config:
            raise DataContractError('task configuration requires parameters and data_root, not an inline data manifest')
        p = Parameters(**config['parameters'])
        store = Store(args.config.absolute().parent / config['data_root'])
        with store.bundle(p) as source:
            result = calculate(p, source.calendar, source, calendar_through=source.calendar_through,
                               price_through=source.price_through)
            source.assert_unchanged()
            result['warnings'].extend(source.warnings)
            folder = write_reports(args.project, p, result, source.metadata())
        print(json.dumps(dict(status=result['status'], output_dir=str(folder), summary=result['summary']),
                         ensure_ascii=False, allow_nan=False))
        return 2 if result['status'] == 'no_result' else 0
    except Exception as exc:
        # Never echo raw rows, configuration values, URLs, tickets or tokens.
        message = str(exc) if isinstance(exc, DataContractError) else 'Invalid configuration or input data; check required fields, dates, schemas and local file access.'
        print(json.dumps(dict(status='error', error_type=type(exc).__name__, message=message), ensure_ascii=False))
        return 2


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    raise SystemExit(main())
