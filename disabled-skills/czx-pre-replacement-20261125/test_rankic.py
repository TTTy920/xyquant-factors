"""Run RankIC regressions with a canonical temporary path on every platform."""
from pathlib import Path
import sys
import tempfile
import unittest

tempfile.tempdir=str(Path(tempfile.gettempdir()).resolve())
root=Path(__file__).resolve().parents[1]
suite=unittest.defaultTestLoader.discover(str(root/'plugins/xyquant/skills/factor-rankic/tests'))
result=unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(0 if result.wasSuccessful() else 1)
