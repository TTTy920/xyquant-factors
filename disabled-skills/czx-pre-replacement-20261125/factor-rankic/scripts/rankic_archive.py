"""Verify and extract opaque ZIP data without interpreting a factor schema.

Only the data extensions in DATA_SUFFIXES are accepted; their contents are
never imported, executed, or normalized here. Archives and extracted data must
stay separate from scripts. Run Python callers with -I when reading downloads.
The destination must be an absolute, nonexistent directory with an existing
local, non-linked parent. Existing directories (even empty ones) are refused.
"""
from contextlib import contextmanager
import hashlib
import math
import os
from pathlib import Path
import re
import shutil
import stat
import unicodedata
import zipfile
import zlib

CHUNK = 1024 * 1024
MAX_ARCHIVE_BYTES = 1024 ** 3
DATA_SUFFIXES = frozenset({
    ".parquet", ".pqt", ".csv", ".tsv", ".json", ".jsonl", ".ndjson",
    ".arrow", ".feather", ".txt",
})
HASH = re.compile(r"[0-9a-f]{64}")
FORBIDDEN = re.compile(r'[\\:<>"|?*\x00-\x1f\x7f-\x9f]')
DEVICE = re.compile(r"(?i)^(con|prn|aux|nul|com[1-9¹²³]|lpt[1-9¹²³]|conin\$|conout\$|clock\$)$")


class ArchiveError(ValueError):
    """A ZIP, manifest, or destination failed a security check."""


def _reject(message):
    # Deliberately never include untrusted names, data, or credentials.
    raise ArchiveError(message)


def _local_path(value):
    path = Path(value)
    if (not path.is_absolute() or ".." in path.parts
            or str(path).startswith(("\\\\", "//"))):
        _reject("An existing absolute local path is required.")
    return path


def _regular(path, directory=False):
    info = path.lstat()
    predicate = stat.S_ISDIR if directory else stat.S_ISREG
    if getattr(info, "st_file_attributes", 0) & 0x400 or not predicate(info.st_mode):
        _reject("Linked or non-regular paths are not permitted.")
    return info


def _parents(path):
    for parent in reversed(path.parents):
        _regular(parent, directory=True)


@contextmanager
def _verified_source(path, expected_sha256, expected_bytes):
    _parents(path)
    _regular(path)
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as source:
        if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
            _reject("The archive must be a regular file.")
        if os.fstat(source.fileno()).st_size != expected_bytes:
            _reject("Archive byte count does not match the expected value.")
        digest = hashlib.sha256()
        received = 0
        for block in iter(lambda: source.read(CHUNK), b""):
            received += len(block)
            if received > expected_bytes:
                _reject("Archive byte count changed during verification.")
            digest.update(block)
        if received != expected_bytes or digest.hexdigest() != expected_sha256:
            _reject("Archive SHA-256 or byte count does not match the expected value.")
        source.seek(0)
        yield source


def _member_path(info):
    name = info.filename
    if (not name or name != info.orig_filename or name.startswith("/")
            or FORBIDDEN.search(name)):
        _reject("Unsafe archive member path.")
    directory = info.is_dir()
    parts = (name[:-1] if directory else name).split("/")
    for part in parts:
        if (not part or part in (".", "..") or part.endswith((".", " "))
                or DEVICE.fullmatch(part.split(".", 1)[0].rstrip(" ."))):
            _reject("Unsafe or nonportable archive member path.")
    mode = info.external_attr >> 16
    kind = stat.S_IFMT(mode)
    allowed_kind = stat.S_IFDIR if directory else stat.S_IFREG
    if (kind not in (0, allowed_kind) or info.external_attr & 0x400
            or (not directory and mode & 0o111)):
        _reject("Links, special files, and executable members are not permitted.")
    if not directory and Path(parts[-1]).suffix.lower() not in DATA_SUFFIXES:
        _reject("Only allowlisted data file extensions may be extracted.")
    return parts, directory


def _plan(zipped, max_total_size, max_members, max_compression_ratio):
    members = zipped.infolist()
    if not members or len(members) > max_members:
        _reject("Archive member count is empty or exceeds the limit.")
    seen, directories, files = set(), set(), set()
    total, compressed = 0, 0
    plan = []
    for info in members:
        parts, directory = _member_path(info)
        key = tuple(unicodedata.normalize("NFC", part).casefold() for part in parts)
        if key in seen or (not directory and key in directories):
            _reject("Archive members have duplicate or conflicting destinations.")
        for index in range(1, len(key)):
            parent = key[:index]
            if parent in files:
                _reject("An archive file conflicts with a directory destination.")
            directories.add(parent)
        seen.add(key)
        (directories if directory else files).add(key)
        if info.flag_bits & 1 or info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            _reject("Encrypted or unsupported ZIP compression is not permitted.")
        if info.file_size < 0 or info.compress_size < 0 or (directory and info.file_size):
            _reject("Invalid archive member size.")
        if info.file_size and (not info.compress_size
                               or info.file_size / info.compress_size > max_compression_ratio):
            _reject("Archive member compression ratio exceeds the limit.")
        total += info.file_size
        compressed += info.compress_size
        if total > max_total_size:
            _reject("Archive uncompressed size exceeds the limit.")
        plan.append((info, parts, directory))
    if total and (not compressed or total / compressed > max_compression_ratio):
        _reject("Archive compression ratio exceeds the limit.")
    return plan


def extract_zip(archive, destination, expected_sha256, expected_bytes, *,
                max_total_size=1024 ** 3, max_members=4096, max_compression_ratio=200):
    """Return extracted data Paths after hash/size and complete ZIP preflight.

    No server ZIP layout, manifest keys, table columns, or normalization is
    assumed. Pass the exact expected SHA-256 and byte count from the authorized
    download descriptor. Any failed extraction removes its newly created output
    directory, leaving the input archive untouched. Output never replaces an
    existing file or directory. Only stored/deflated, non-encrypted ZIPs qualify.
    """
    if (not isinstance(expected_sha256, str) or not HASH.fullmatch(expected_sha256)
            or type(expected_bytes) is not int or not 0 < expected_bytes <= MAX_ARCHIVE_BYTES):
        _reject("Invalid expected archive SHA-256 or byte count.")
    if (type(max_total_size) is not int or max_total_size <= 0
            or type(max_members) is not int or max_members <= 0
            or type(max_compression_ratio) not in (int, float)
            or not math.isfinite(max_compression_ratio) or max_compression_ratio <= 0):
        _reject("Archive limits must be finite positive numbers.")
    created = False
    complete = False
    try:
        source_path = _local_path(archive)
        output = _local_path(destination)
        _parents(output)
        if output == Path.cwd():
            _reject("The working directory cannot be an extraction destination.")
        # lexists also detects a dangling symlink, without following it.
        if os.path.lexists(output):
            _reject("The extraction destination must not already exist.")
        with _verified_source(source_path, expected_sha256, expected_bytes) as source:
            with zipfile.ZipFile(source, "r") as zipped:
                plan = _plan(zipped, max_total_size, max_members, max_compression_ratio)
                output.mkdir(mode=0o700)
                created = True
                paths = []
                received = 0
                for info, parts, directory in plan:
                    target = output.joinpath(*parts)
                    folder = output
                    _regular(folder, directory=True)
                    for component in parts[:-1]:
                        # Create only directories inside this private new root.
                        folder = folder / component
                        folder.mkdir(mode=0o700, exist_ok=True)
                        _regular(folder, directory=True)
                    if directory:
                        target.mkdir(mode=0o700, exist_ok=True)
                        _regular(target, directory=True)
                        continue
                    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                                 | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0), 0o600)
                    with os.fdopen(fd, "wb") as dest, zipped.open(info, "r") as member:
                        count = 0
                        for block in iter(lambda: member.read(CHUNK), b""):
                            count += len(block)
                            received += len(block)
                            if count > info.file_size or received > max_total_size:
                                _reject("Extracted data exceeds its declared size or configured limit.")
                            dest.write(block)
                        if count != info.file_size:
                            _reject("Extracted member byte count does not match its declaration.")
                    paths.append(target)
                complete = True
                return paths
    except (OSError, ValueError, RuntimeError, EOFError, zipfile.BadZipFile,
            zipfile.LargeZipFile, NotImplementedError, zlib.error) as error:
        if isinstance(error, ArchiveError):
            raise
        raise ArchiveError("Archive verification or extraction failed.") from None
    finally:
        if created and not complete:
            shutil.rmtree(output)
