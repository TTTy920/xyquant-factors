#!/usr/bin/env python3
"""Isolated launcher for this distribution's fixed RankIC entry points."""
import sys

_ERROR_JSON = ('{"status":"error","error_type":"LauncherError",'
               '"message":"Trusted isolated RankIC launcher could not start."}\n')


def _failure():
    # This path must also work before any non-builtin import is safe.
    sys.stdout.write(_ERROR_JSON)
    return 2


# Without -I, even importing a standard-library name can load customer code.
if not sys.flags.isolated:
    raise SystemExit(_failure())

sys.dont_write_bytecode = True

import importlib.util
from pathlib import Path
import stat

TARGETS = {'rankic': 'rankic', 'workflow': 'rankic_workflow', 'data': 'rankic_data', 'factor': 'rankic_factor', 'download': 'rankic_download'}
_HELP = """Usage: python -I rankic_entry.py <target> [arguments]

Fixed targets:
  rankic    Local catalog inventory and RankIC calculation (inventory/run).
  workflow  Non-secret task preparation and downloaded-data preflight.
  data      Check or download an authorized RankIC data bundle (status/save).
  download  Save one authorized factor-library export (status/save).
  factor    Prepare a user-supplied or factor-library input locally (prepare).

Use <target> --help for that target's command options.
"""


def _check_code_path(path, *, directory):
    """Inspect the lexical path, root first; resolving would hide links."""
    for component in reversed((path, *path.parents)):
        info = component.lstat()
        if (stat.S_ISLNK(info.st_mode)
                or getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
            raise ValueError('unsafe code path')
        expected = stat.S_ISDIR if directory or component != path else stat.S_ISREG
        if not expected(info.st_mode):
            raise ValueError('invalid code path type')


def main(argv=None):
    try:
        if hasattr(sys.stdout, 'reconfigure'):
            sys.stdout.reconfigure(encoding='utf-8')
        # absolute() does not resolve symlinks. Check before adding any code path.
        entry = Path(__file__).absolute()
        _check_code_path(entry, directory=False)
        scripts = entry.parent / 'scripts'
        _check_code_path(scripts, directory=True)

        arguments = list(sys.argv[1:] if argv is None else argv)
        if arguments in (['-h'], ['--help']):
            sys.stdout.write(_HELP)
            return 0
        if not arguments or arguments[0] not in TARGETS:
            return _failure()

        module_name = TARGETS[arguments[0]]
        module_path = scripts / (module_name + '.py')
        _check_code_path(module_path, directory=False)
        # Never add the caller's cwd, project, config, data or downloads directory.
        sys.path.insert(0, str(scripts))
        # Load this exact file: no fallback to an unrelated installed module.
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        if spec is None or spec.loader is None:
            return _failure()
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module.main(arguments[1:])
    except Exception:
        # Preserve business SystemExit and return codes; hide unexpected failures.
        return _failure()


if __name__ == '__main__':
    raise SystemExit(main())
