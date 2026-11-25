"""Credential-free client preparation. Downloaded data is checked before local execution."""
import argparse
import json
import os
from pathlib import Path
import re
import sys
import uuid

from rankic_engine import Parameters
from rankic_inputs import SourceFiles, _check_links

MAX_JSON_BYTES = 262144
PARAMETER_FIELDS = {'factor_name', 'start', 'end', 'frequency', 'pool', 'codes', 'min_stocks'}
REQUIRED_FIELDS = ('factor_name', 'start', 'end', 'frequency', 'pool')
TASK_FIELDS = {'schema_version', 'request_id', 'origin', 'parameters'}
SERVER_BLOCKERS = ('rankic_data_required',)


class WorkflowError(ValueError):
    """Messages contain no input values, paths, or provider credentials."""


def normalize_parameters(value):
    """Validate partial Parameters without persisting inferred placeholders."""
    try:
        if not isinstance(value, dict) or set(value) - PARAMETER_FIELDS:
            raise WorkflowError('unsupported task parameters')
        clean = dict(value)
        if 'factor_name' in clean:
            name = clean['factor_name']
            if (not isinstance(name, str) or not name.strip() or len(name) > 200
                    or '://' in name
                    or re.match(r'^[A-Za-z][A-Za-z0-9+.-]*:', name.strip())
                    or name.strip().startswith(('//', '\\\\'))
                    or any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in name)):
                raise WorkflowError('invalid factor name')
            clean['factor_name'] = name.strip()
        if 'codes' in clean and (not isinstance(clean['codes'], list) or len(clean['codes']) > 10000):
            raise WorkflowError('invalid custom stock list')
        # These values exist only to reuse the engine's validator for missing fields.
        # They must never be copied into the customer's partial parameter object.
        validating = dict(factor_name='validation-only', start=clean.get('end', '2000-01-01'),
                          end=clean.get('start', '2000-01-01'), frequency='daily',
                          pool='custom' if clean.get('codes') else 'all_a')
        validating.update(clean)
        if validating['pool'] == 'custom' and not validating.get('codes'):
            validating['codes'] = ['000001.SZ']
        checked = Parameters(**validating)
        clean['min_stocks'] = checked.min_stocks
        if 'codes' in clean:
            # An empty custom list is still missing, not the validator's placeholder.
            clean['codes'] = list(checked.codes) if clean['codes'] else []
        missing = [field for field in REQUIRED_FIELDS if field not in clean]
        if clean.get('pool') == 'custom' and not clean.get('codes'):
            missing.append('codes')
        return clean, missing
    except (ValueError, TypeError, KeyError, OverflowError):
        raise WorkflowError('invalid or unsupported task parameters') from None


def assessment(parameters, data_root=None):
    """A fixed release gate, not a caller-supplied capability/authorization check."""
    clean, missing = normalize_parameters(parameters)
    result = dict(status='blocked', origin='xyquant_server', parameters=clean,
                missing_parameters=missing, factor_resolution='pending_catalog',
                can_create_export=False, can_run=False,
                blockers=(['parameters_incomplete'] if missing else []) + list(SERVER_BLOCKERS))
    if data_root is not None and not missing:
        import time
        from rankic_store import Store
        store = Store(data_root)
        if store.catalog['origin'] != 'xyquant_server' or store.catalog.get('cache_expires_at', 0) <= time.time():
            raise WorkflowError('download a current RankIC server snapshot')
        factor = store.resolve(clean['factor_name']);store.assert_unchanged()
        result.update(status='ready', can_run=True, factor_resolution=factor['id'], blockers=[])
    return result


def _directory(path):
    path = Path(path)
    if (not path.is_absolute() or '..' in path.parts or str(path).startswith(('\\\\', '//'))
            or not path.is_dir()):
        raise WorkflowError('an existing absolute local project directory is required')
    _check_links(path)
    return path


def _task_parent(project, create):
    folder = _directory(project)
    for name in ('downloads', 'rankic-tasks'):
        folder = folder / name
        if create:
            folder.mkdir(mode=0o700, exist_ok=True)
        _directory(folder)
    return folder


def _task_id(value):
    if not isinstance(value, str) or str(uuid.UUID(value)) != value:
        raise WorkflowError('invalid task identity')
    return value


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise WorkflowError('duplicate JSON keys are not permitted')
        result[key] = value
    return result


def _nonfinite(_value):
    raise WorkflowError('non-finite JSON numbers are not permitted')


def _read_document(path):
    path = Path(path).absolute()
    _check_links(path)
    if not path.is_file() or not 0 < path.stat().st_size <= MAX_JSON_BYTES:
        raise WorkflowError('invalid or oversized task document')
    sources = SourceFiles()
    sources.verify(path)
    with path.open('rb') as stream:
        raw = stream.read(MAX_JSON_BYTES + 1)
    if len(raw) > MAX_JSON_BYTES:
        raise WorkflowError('oversized task document')
    result = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=_unique_object, parse_constant=_nonfinite)
    sources.assert_unchanged()
    return result


def prepare(project, request):
    """Save only a fresh parameter request; success is not permission to export/run."""
    try:
        if not isinstance(request, dict) or set(request) != {'parameters'}:
            raise WorkflowError('request must contain only parameters')
        result = assessment(request['parameters'])
        ident = str(uuid.uuid4())
        folder = _task_parent(project, create=True) / ident
        folder.mkdir(mode=0o700, exist_ok=False)
        temporary = folder / '.request.tmp'
        target = folder / 'request.json'
        try:
            _directory(folder)
            document = dict(schema_version=1, request_id=ident, origin='xyquant_server',
                            parameters=result['parameters'])
            with temporary.open('x', encoding='utf-8') as stream:
                json.dump(document, stream, ensure_ascii=False, allow_nan=False)
                stream.write('\n')
                stream.flush()
                os.fsync(stream.fileno())
            # No previous task is replaced: this directory was created exclusively.
            if target.exists() or target.is_symlink():
                raise WorkflowError('task destination already exists')
            temporary.rename(target)
        except BaseException:
            temporary.unlink(missing_ok=True)
            folder.rmdir()
            raise
        result.update(status='needs_parameters' if result['missing_parameters'] else 'parameters_complete',
                      request_id=ident, task_file=str(target))
        return result
    except (OSError, ValueError, TypeError, KeyError, OverflowError):
        raise WorkflowError('client task could not be prepared') from None


def preflight(project, task_id, data_root=None):
    """Read-only check. No MCP, network, downloads, cache selection or calculation."""
    try:
        ident = _task_id(task_id)
        folder = _task_parent(project, create=False) / ident
        _directory(folder)
        document = _read_document(folder / 'request.json')
        if (not isinstance(document, dict) or set(document) != TASK_FIELDS
                or type(document['schema_version']) is not int or document['schema_version'] != 1
                or document['request_id'] != ident or document['origin'] != 'xyquant_server'):
            raise WorkflowError('invalid task identity or document')
        result = assessment(document['parameters'], data_root)
        result['request_id'] = ident
        return result
    except (OSError, ValueError, TypeError, KeyError, OverflowError):
        raise WorkflowError('client task preflight failed') from None


class _Parser(argparse.ArgumentParser):
    def error(self, _message):
        raise WorkflowError('invalid command arguments')


def main(argv=None):
    try:
        parser = _Parser(description=__doc__)
        commands = parser.add_subparsers(dest='command', required=True, parser_class=_Parser)
        prepare_parser = commands.add_parser('prepare', help='save a credential-free parameter request')
        prepare_parser.add_argument('--request', type=Path, required=True)
        prepare_parser.add_argument('--project', type=Path, required=True)
        preflight_parser = commands.add_parser('preflight', help='check parameters and a downloaded data catalog')
        preflight_parser.add_argument('--task', required=True)
        preflight_parser.add_argument('--project', type=Path, required=True)
        preflight_parser.add_argument('--data-root', type=Path)
        args = parser.parse_args(argv)
        result = (prepare(args.project, _read_document(args.request)) if args.command == 'prepare'
                  else preflight(args.project, args.task, args.data_root))
        print(json.dumps(result, ensure_ascii=True, allow_nan=False))
        return 2 if result['status'] == 'blocked' else 0
    except Exception:
        print(json.dumps(dict(status='error', code='invalid_client_task',
                              message='Check command arguments, parameter fields and local task access.')))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
