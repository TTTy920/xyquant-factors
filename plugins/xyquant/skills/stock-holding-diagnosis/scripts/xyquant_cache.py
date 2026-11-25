"""Reuse the plugin's existing authenticated download and cache contracts."""
import sys
from pathlib import Path


SKILLS = Path(__file__).resolve().parents[2]
DOWNLOADER_DIRS = [str(SKILLS.parent / "shared"), str(SKILLS / "portfolio-attribution/scripts")]
sys.path[:0] = DOWNLOADER_DIRS
try:
    import xyquant_download as factor_download
    import download_original as attribution_download
    from mcp_receive import MAX_JOB_BYTES, receive_job
finally:
    del sys.path[:len(DOWNLOADER_DIRS)]
