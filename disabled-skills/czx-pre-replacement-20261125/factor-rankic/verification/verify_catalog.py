"""Replay all four sample frequencies, then repeat after relocation with access guards.

Run using this standalone project's interpreter with -I -B. No network or HDF.
This verifies workbook Python caches, NOT independent spreadsheet recalculation.
"""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
FREQUENCIES = ('daily', 'weekly', 'monthly', 'quarterly')
FIELDS = ('status', 'summary', 'periods', 'return_range')

CHILD = r'''
import contextlib
import importlib.abc
import io
import json
import math
import os
from pathlib import Path
import runpy
import sys

root, frequency = Path(sys.argv[1]), sys.argv[2]
forbidden = json.loads(sys.argv[3])
environment = os.path.normcase(os.path.abspath(sys.prefix))
attempts = dict(hdf_imports=0, forbidden_reads=0, network_attempts=0)

def inside(path, parent):
    return path == parent or path.startswith(parent + os.sep)

class NoHDF(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'h5py' or fullname.startswith('h5py.'):
            attempts['hdf_imports'] += 1
            raise ImportError('HDF imports are forbidden during Parquet replay')

sys.meta_path.insert(0, NoHDF())
def guard(event, args):
    if event in ('socket.connect', 'socket.getaddrinfo'):
        attempts['network_attempts'] += 1
        raise RuntimeError('network is forbidden during sample replay')
    if event == 'open' and isinstance(args[0], (str, bytes, os.PathLike)):
        path = os.path.normcase(os.path.abspath(os.fsdecode(args[0])))
        if path.lower().endswith(('.h5', '.hdf5')) or (
                not inside(path, environment) and any(inside(path, os.path.normcase(os.path.abspath(p))) for p in forbidden)):
            attempts['forbidden_reads'] += 1
            raise RuntimeError('original sources are forbidden during sample replay')
sys.addaudithook(guard)
sys.argv = [str(root / 'rankic_entry.py'), 'rankic', 'run', '--config',
            str(root / f'examples/floatcap-{frequency}.json'), '--project', str(root)]
output_bytes = io.BytesIO()
output = io.TextIOWrapper(output_bytes, encoding='utf-8', write_through=True)
with contextlib.redirect_stdout(output):
    try:
        runpy.run_path(str(root / 'rankic_entry.py'), run_name='__main__')
    except SystemExit as exc:
        code = exc.code
payload = json.loads(output_bytes.getvalue().decode('utf-8'))
assert code == 0 and payload['status'] == 'partial_success', payload
folder = Path(payload['output_dir'])
assert {p.name for p in folder.iterdir()} == {'report.md', 'rankic.xlsx', 'metadata.json'}
metadata = json.loads((folder / 'metadata.json').read_text(encoding='utf-8'))
assert all(row['unknown_universe'] == 348 for row in metadata['periods'])
assert metadata['data']['origin'] == 'local_reference'
assert metadata['data']['scope_kind'] == 'local_reference'
assert metadata['data']['catalog']['factor_id'] == 'FloatCap'
from openpyxl import load_workbook
book = load_workbook(folder / 'rankic.xlsx', data_only=True)
try:
    cached = dict(book['Summary'].values)
    for key, expected in metadata['summary'].items():
        actual = cached[key]
        assert actual is None if expected is None else math.isclose(actual, expected, rel_tol=1e-12, abs_tol=1e-15), key
finally:
    book.close()
assert not any(attempts.values()), attempts
print(json.dumps(dict(payload=payload, reference={k:metadata[k] for k in ('status','summary','periods','return_range')},
                      catalog_sha256=metadata['data']['catalog']['sha256'],
                      guards=attempts, workbook_python_cache_checked=True), ensure_ascii=True, allow_nan=False))
'''


def replay(root, baseline, relocated=False):
    forbidden = ['D:/XYQuantPlugin', 'D:/HDF5Data'] + ([str(ROOT)] if relocated else [])
    results = {}
    for frequency in FREQUENCIES:
        process = subprocess.run([sys.executable, '-I', '-B', '-X', 'utf8', '-c', CHILD,
                                  str(root), frequency, json.dumps(forbidden)], cwd=root, capture_output=True,
                                 text=True, encoding='utf-8', timeout=120)
        if process.returncode:
            raise RuntimeError(f'{frequency} replay failed: {process.stdout}\n{process.stderr}')
        result = json.loads(process.stdout)
        if result['reference'] != {key: baseline[frequency][key] for key in FIELDS}:
            raise AssertionError(f'{frequency} differs from original reference baseline')
        results[frequency] = dict(status=result['payload']['status'], summary=result['payload']['summary'],
                                  guards=result['guards'], workbook_python_cache_checked=True,
                                  catalog_sha256=result['catalog_sha256'], baseline_exact_match=True)
        if not relocated:
            results[frequency]['output_dir'] = result['payload']['output_dir']
    return results


def copy_selected_data(root, target):
    """Copy only the self-contained data roots explicitly used by the four examples."""
    root = Path(root).resolve()
    selected = set()
    for frequency in FREQUENCIES:
        config_path = root / f'examples/floatcap-{frequency}.json'
        config = json.loads(config_path.read_text(encoding='utf-8'))
        value = config.get('data_root')
        if not isinstance(value, str) or not value:
            raise ValueError('example data_root is required')
        data = (config_path.parent / value).resolve()
        if data == root or not data.is_relative_to(root) or not data.is_dir():
            raise ValueError('relocation requires an existing project-local data root')
        selected.add(data)
    copied = []
    for data in sorted(selected, key=lambda p: (len(p.parts), str(p))):
        if any(data.is_relative_to(parent) for parent in copied):
            continue
        shutil.copytree(data, Path(target) / data.relative_to(root))
        copied.append(data)
    return [data.relative_to(root).as_posix() for data in copied]


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence', type=Path, default=Path('verification/latest-replay-check.json'),
                        help='Separate evidence path; historical catalog/migration records stay unchanged.')
    return parser.parse_args(argv)


def main():
    args = parse_args()
    baseline = json.loads((ROOT / 'verification/hdf-reference-baseline.json').read_text(encoding='utf-8'))
    native = replay(ROOT, baseline)
    with tempfile.TemporaryDirectory(prefix='rankic-catalog-relocation-') as temporary:
        target = Path(temporary) / 'independent'
        target.mkdir()
        shutil.copyfile(ROOT / 'rankic_entry.py', target / 'rankic_entry.py')
        for name in ('scripts', 'examples'):
            shutil.copytree(ROOT / name, target / name, ignore=shutil.ignore_patterns('__pycache__'))
        data_roots = copy_selected_data(ROOT, target)
        relocated = replay(target, baseline, relocated=True)
    evidence = dict(schema_version=1, native=native, relocated=relocated, data_roots=data_roots,
                    independent_spreadsheet_recalculation=False,
                    server_to_report_verified=False, project_mcp_config_changed=False)
    evidence_path = ROOT / args.evidence
    evidence_path.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    print(json.dumps(dict(all_eight_runs_exact_match=True, evidence=str(evidence_path),
                          native_reports={f: native[f]['output_dir'] for f in FREQUENCIES},
                          valid_periods={f: native[f]['summary']['valid_periods'] for f in FREQUENCIES},
                          all_access_guards_zero=True,
                          independent_spreadsheet_recalculation=False,
                          server_to_report_verified=False), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
