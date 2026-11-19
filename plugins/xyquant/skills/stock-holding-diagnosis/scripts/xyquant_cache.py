"""Reuse the plugin's existing authenticated download and cache contracts."""
import sys
from pathlib import Path


SKILLS = Path(__file__).resolve().parents[2]
DOWNLOADER_DIRS = [str(SKILLS / skill / "scripts") for skill in ("factor-download", "portfolio-attribution")]
sys.path[:0] = DOWNLOADER_DIRS
try:
    import download as factor_download
    import download_original as attribution_download
finally:
    del sys.path[:len(DOWNLOADER_DIRS)]
