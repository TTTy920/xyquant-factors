#!/usr/bin/env python3
"""Local RankIC runner with optional scope-pinned MCP factor values."""
import argparse
from contextlib import nullcontext
import json
from pathlib import Path
import sys

from rankic_catalog import resolve
from rankic_engine import Parameters, calculate
from rankic_inputs import DataBundle, DataContractError
from rankic_mcp import factor_input
from rankic_report import write_reports

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from xyquant_catalog import CatalogError, managed_file
from xyquant_data import DataPathError, RANKIC_INPUTS, data_path
sys.path.pop(0)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--project', required=True, type=Path, help='customer project; outputs under xyquant-data/results/rankic/<uuid>')
    parser.add_argument('--factor', '--factor-name', dest='factor', help='the exact MCP catalog factor ID')
    parser.add_argument('--factor-job', help='verified historical single-factor export job')
    parser.add_argument('--factor-sha256', help='SHA-256 of the factor export')
    args = parser.parse_args(argv)
    try:
        if not args.project.is_dir():
            raise DataContractError('project must be an existing directory')
        mcp = (args.factor, args.factor_job, args.factor_sha256)
        if any(x is not None for x in mcp) and not all(mcp):
            raise DataContractError('MCP factor mode requires --factor, --factor-job and --factor-sha256 together')
        config_path = data_path(args.project, RANKIC_INPUTS, args.config, directory=False)
        with config_path.open(encoding='utf-8-sig') as handle:
            config = json.load(handle)
        parameters = dict(config['parameters'])
        if args.factor is not None:
            parameters['factor_name'] = args.factor
        p = Parameters(**parameters)
        data, pinned, bound_paths = resolve(args.project, p, config['data'])

        def check_source(role, path):
            if (role, path) in bound_paths:
                return managed_file(args.project, bound_paths[(role, path)])
            if role != 'factor':
                raise DataContractError('market input is not bound to inputs/datasets')
            return data_path(args.project, RANKIC_INPUTS, path, directory=False)

        scratch = data_path(args.project, ('.scratch', 'rankic'), create=True)
        factor = factor_input(args.project, p, args.factor_job, args.factor_sha256, scratch) if args.factor else nullcontext()
        with factor as exported:
            with DataBundle(data, p, config_path.parent, scratch=scratch, mcp_factor=exported,
                            check_source=check_source, verified_server=data['origin']=='xyquant_server') as source:
                result = calculate(p, source.calendar, source, calendar_through=source.calendar_through,
                                   price_through=source.price_through)
                source.assert_unchanged()
                result['warnings'].extend(source.warnings)
                metadata = source.metadata()
                if pinned:
                    metadata['catalog_inputs'] = pinned
                    for role, info in pinned.items():
                        result['warnings'].append(f'inputs/datasets/{info["dataset"]} 版本：{info["version"]}')
                        result['warnings'].extend(f'{role} 已声明覆盖限制：{gap}' for gap in info['known_gaps'])
                folder = write_reports(args.project, p, result, metadata)
        print(json.dumps(dict(status=result['status'], output_dir=str(folder), summary=result['summary']),
                         ensure_ascii=False, allow_nan=False))
        return 2 if result['status'] == 'no_result' else 0
    except Exception as exc:
        # Never echo config values, raw rows, URLs with tickets or file contents.
        message = str(exc) if isinstance(exc, (CatalogError, DataContractError, DataPathError)) else 'Invalid configuration or input data; check required fields, dates, schemas and local file access.'
        print(json.dumps(dict(status='error', error_type=type(exc).__name__, message=message), ensure_ascii=False))
        return 2


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    raise SystemExit(main())
