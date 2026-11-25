"""Shared layout for on-disk Skill inputs, caches and results."""
import os
from pathlib import Path
import stat


DATA_DIR = "xyquant-data"
FACTOR_CACHE = ("mcp-cache", "factors")
ORIGINAL_CACHE = ("mcp-cache", "attribution", "original")
PUBLIC_CACHE = ("mcp-cache", "attribution", "public")
RANKIC_INPUTS = ("inputs", "rankic")
HOLDINGS_INPUTS = ("inputs", "holdings")
RANKIC_RESULTS = ("results", "rankic")
SYNTHESIS_RESULTS = ("results", "factor-synthesis")
ATTRIBUTION_RESULTS = ("results", "portfolio-attribution")


class DataPathError(ValueError):
    pass


def data_root(project):
    project = Path(project).expanduser().resolve(strict=True)
    if not project.is_dir() or str(project).startswith(("\\\\", "//")):
        raise DataPathError("请指定已存在的本地项目目录")
    return project / DATA_DIR


def check_kind(path, *, directory):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    if getattr(info, "st_file_attributes", 0) & 0x400 or not kind(info.st_mode):
        raise DataPathError("数据路径含链接或文件类型不匹配")
    return True


def data_path(project, parts, path=None, *, directory=True, create=False):
    """Check the full managed path, including existing parents. Never follow links."""
    root = data_root(project)
    base = root.joinpath(*parts)
    target = base if path is None else Path(os.path.abspath(Path(path).expanduser()))
    if not target.is_relative_to(base) or (target == base and not directory):
        raise DataPathError("请将数据放在项目的 " + "/".join((DATA_DIR, *parts)) + "/ 中")
    current = root.parent
    for part in target.relative_to(current).parts:
        current /= part
        is_dir = directory or current != target
        exists = check_kind(current, directory=is_dir)
        if not exists and create and is_dir:
            current.mkdir(mode=0o700, exist_ok=True)
            check_kind(current, directory=True)
    return target
