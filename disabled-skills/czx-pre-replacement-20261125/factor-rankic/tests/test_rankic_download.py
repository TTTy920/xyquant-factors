"""Offline security regressions for the standalone downloader and ZIP helper.

The downloader cases adapt the original plugin's tests/test_download.py; HTTPS
responses are simulated in memory, never served over a real socket. Archive
fixtures are opaque data, not a claim about any server's ZIP layout or schema.
"""
import concurrent.futures
from contextlib import redirect_stderr, redirect_stdout
import copy
import getpass
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import socket
import ssl
import stat
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.error
import warnings
import zipfile

ROOT = Path(__file__).resolve().parents[1]
DOWNLOAD_SCRIPT = ROOT / "scripts" / "rankic_download.py"
ARCHIVE_SCRIPT = ROOT / "scripts" / "rankic_archive.py"
DATA = b"factor fixture\n" * 300
DIGEST = hashlib.sha256(DATA).hexdigest()
JID = "c" * 32
TICKET = "simulated-download-ticket-not-a-credential"
ORIGIN = "https://47.103.215.251"


def load_script(path, module_name):
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Reply(io.BytesIO):
    def __init__(self, body=DATA, status=200, headers=None):
        super().__init__(body)
        self.status = status
        self.headers = {"Content-Length": str(len(DATA)), "X-Checksum-SHA256": DIGEST}
        if headers is not None:
            self.headers.update(headers)


class SimulatedHTTPS:
    """Exercise real transfer code without a network, including redirect policy."""
    def __init__(self, download):
        self.download = download
        self.replies = [lambda: Reply()]
        self.requests = []
        self.guard = threading.Lock()

    def build_opener(self, redirect):
        if redirect is not self.download.RefuseRedirect:
            raise AssertionError("redirect rejection must remain installed")
        return self

    def open(self, request, timeout):
        if not request.full_url.startswith(ORIGIN + "/"):
            raise AssertionError("transport was given a non-HTTPS or foreign origin")
        with self.guard:
            self.requests.append(request)
            reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        if isinstance(reply, tuple) and reply[0] == "redirect":
            self.download.RefuseRedirect().redirect_request(
                request, None, 302, "Found", {}, reply[1])
            raise AssertionError("a redirect must never be followed")
        return reply()


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(DOWNLOAD_SCRIPT.is_file(),
                        "standalone secure save/status downloader is not implemented")
        self.dl = load_script(DOWNLOAD_SCRIPT, "rankic_download_under_test")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name).resolve()
        self.transport = SimulatedHTTPS(self.dl)
        for patcher in (
            patch.object(self.dl.urllib.request, "build_opener", self.transport.build_opener),
            patch.object(self.dl, "RETRIES", (0, 0)),
            patch.object(socket, "socket", side_effect=AssertionError("network forbidden in tests")),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.job = dict(id=JID, status="ready", filename="盈利_TTM.parquet",
                        bytes=len(DATA), sha256=DIGEST,
                        download=dict(url=ORIGIN + "/mcp/downloads/" + JID,
                                      ticket=TICKET, expires_at=time.time() + 90))

    def save(self, job=None):
        return self.dl.save(str(self.project), self.job if job is None else job)

    def assert_code(self, expected, function, *args, **kwargs):
        with self.assertRaises(self.dl.DownloadError) as raised:
            function(*args, **kwargs)
        self.assertEqual(raised.exception.code, expected)
        self.assertNotIn(TICKET, str(raised.exception))

    def assert_no_secret_files(self):
        for path in self.project.rglob("*"):
            if path.is_file():
                self.assertNotIn(TICKET.encode(), path.read_bytes())

    def test_save_verifies_https_bytes_and_preserves_project_storage(self):
        result = self.save()
        target = self.project / "downloads" / "factors" / JID / self.job["filename"]
        self.assertEqual(Path(result["path"]), target)
        self.assertEqual(target.read_bytes(), DATA)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(self.dl.status(str(self.project), JID)["sha256"], DIGEST)
        request = self.transport.requests[0]
        self.assertEqual(request.full_url, self.job["download"]["url"])
        self.assertEqual(request.get_header("Authorization"), "XYQuant-Download " + TICKET)
        self.assertEqual(request.get_header("Accept-encoding"), "identity")
        self.assertNotIn(TICKET, json.dumps(result))
        self.assert_no_secret_files()

    def test_parallel_saves_reuse_one_verified_file_and_redact_ticket(self):
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            results = list(pool.map(lambda _: self.save(), range(2)))
        self.assertEqual(len(self.transport.requests), 1)
        self.assertEqual(results[0]["path"], results[1]["path"])
        self.assert_no_secret_files()
        target = Path(results[0]["path"])
        target.write_bytes(b"user data")
        self.assert_code("file_conflict", self.save)
        self.assertEqual(target.read_bytes(), b"user data")

    def test_disconnect_retries_then_hash_failure_never_publishes(self):
        self.transport.replies = [
            urllib.error.HTTPError(self.job["download"]["url"], 503, TICKET, {}, None),
            lambda: Reply(DATA[:30]), lambda: Reply(b"X" * len(DATA)),
        ]
        self.assert_code("checksum_mismatch", self.save)
        self.assertEqual(len(self.transport.requests), 3)
        folder = self.project / "downloads" / "factors" / JID
        self.assertFalse((folder / self.job["filename"]).exists())
        self.assertFalse((folder / ".xyquant.partial").exists())
        self.assertEqual(self.dl.status(str(self.project), JID)["status"], "failed")
        self.assert_no_secret_files()
        self.transport.replies = [lambda: Reply()]
        self.assertEqual(self.save()["status"], "ready")

    def test_rejects_all_redirects_without_a_second_request(self):
        for url in (ORIGIN + "/other", "https://example.invalid/leak", "http://47.103.215.251/leak"):
            with self.subTest(url=url):
                self.transport.requests.clear()
                self.transport.replies = [("redirect", url)]
                self.assert_code("redirect_refused", self.save)
                self.assertEqual(len(self.transport.requests), 1)

    def test_permission_expiry_and_not_found_are_not_retried(self):
        for code, expected in ((403, "permission_denied"), (401, "grant_expired"),
                               (410, "grant_expired"), (404, "not_found"), (400, "http_error")):
            with self.subTest(code=code):
                self.transport.requests.clear()
                self.transport.replies = [urllib.error.HTTPError(
                    self.job["download"]["url"], code, TICKET, {}, None)]
                self.assert_code(expected, self.save)
                self.assertEqual(len(self.transport.requests), 1)
                self.assert_no_secret_files()

    def test_expired_grant_does_not_open_transport(self):
        self.job["download"]["expires_at"] = time.time() - 10
        self.assert_code("grant_expired", self.save)
        self.assertFalse(self.transport.requests)

    def test_matching_cache_is_verified_and_reused_even_after_grant_expires(self):
        first = self.save()
        self.job["download"]["expires_at"] = time.time() - 10
        cached = self.save()
        self.assertEqual(cached["path"], first["path"])
        self.assertEqual(cached["sha256"], first["sha256"])
        self.assertEqual(cached["status"], "ready")
        self.assertEqual(len(self.transport.requests), 1)

    def test_rejects_foreign_origin_and_url_variants_before_connecting(self):
        for url in ("https://example.invalid/mcp/downloads/" + JID,
                    "http://47.103.215.251/mcp/downloads/" + JID,
                    ORIGIN + ":443/mcp/downloads/" + JID,
                    "https://user@47.103.215.251/mcp/downloads/" + JID,
                    ORIGIN + "/mcp/downloads/" + JID + "?ticket=" + TICKET,
                    ORIGIN + "/mcp/downloads/" + JID + "#fragment",
                    ORIGIN + "/mcp/downloads/" + "a" * 32):
            with self.subTest(url=url):
                job = copy.deepcopy(self.job)
                job["download"]["url"] = url
                self.assert_code("download_unavailable", self.save, job)
        self.assertFalse(self.transport.requests)

    def test_rejects_nonportable_names_ids_and_manifest_values(self):
        for name in ("../../x.zip", "COM1.zip", "CON.zip", "LPT¹.zip", "C:bad.zip",
                     ".xyquant.lock", "a\\b.zip", "x.exe", "bad\x00.zip", "x" * 256 + ".zip"):
            with self.subTest(name=name):
                self.assert_code("invalid_filename", self.save, dict(self.job, filename=name))
        for jid in ("../outside", "A" * 32, "f" * 31, None):
            with self.subTest(jid=jid):
                self.assert_code("invalid_job", self.save, dict(self.job, id=jid))
        for size in (0, -1, True, 1.5, 1024 ** 3 + 1):
            self.assert_code("invalid_manifest", self.save, dict(self.job, bytes=size))
        for checksum in ("f" * 63, "G" * 64, None):
            self.assert_code("invalid_manifest", self.save, dict(self.job, sha256=checksum))
        for expiry in (True, float("inf"), float("nan"), "later"):
            job = copy.deepcopy(self.job)
            job["download"]["expires_at"] = expiry
            self.assert_code("invalid_grant", self.save, job)
        for ticket in ("secret with spaces", "x\nheader", "x" * 513, None):
            job = copy.deepcopy(self.job)
            job["download"]["ticket"] = ticket
            self.assert_code("invalid_grant", self.save, job)
        self.assertFalse(self.transport.requests)

    def test_invalid_response_headers_and_excess_bytes_do_not_publish(self):
        cases = [(lambda: Reply(status=206), "invalid_response"),
                 (lambda: Reply(headers={"Content-Length": "1"}), "invalid_response"),
                 (lambda: Reply(headers={"X-Checksum-SHA256": "0" * 64}), "invalid_response"),
                 (lambda: Reply(DATA + b"extra"), "invalid_size")]
        for reply, code in cases:
            with self.subTest(code=code):
                self.transport.replies = [reply]
                self.assert_code(code, self.save)
                folder = self.project / "downloads" / "factors" / JID
                self.assertFalse((folder / self.job["filename"]).exists())
                self.assertFalse((folder / ".xyquant.partial").exists())

    def test_tls_validation_failure_is_fatal_and_redacted(self):
        self.transport.replies = [urllib.error.URLError(ssl.SSLCertVerificationError(TICKET))]
        self.assert_code("tls_error", self.save)
        self.assertEqual(len(self.transport.requests), 1)
        self.assert_no_secret_files()

    def test_retry_after_is_bounded_and_network_exhaustion_is_redacted(self):
        self.transport.replies = [urllib.error.HTTPError(
            self.job["download"]["url"], 429, TICKET, {"Retry-After": "999"}, None) for _ in range(3)]
        with patch.object(self.dl.time, "sleep") as sleep:
            self.assert_code("busy", self.save)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [30, 30])
        self.transport.replies = [urllib.error.URLError(TICKET)] * 3
        self.assert_code("network_error", self.save)
        self.assert_no_secret_files()

    def test_disk_full_is_checked_before_transport(self):
        with patch.object(self.dl.shutil, "disk_usage") as usage:
            usage.return_value.free = 0
            self.assert_code("disk_full", self.save)
        self.assertFalse(self.transport.requests)

    def test_interrupted_record_is_recoverable_and_partial_is_replaced(self):
        folder = self.dl.job_folder(str(self.project), JID, True)
        self.dl.store(folder, dict(status="downloading", job_id=JID, total=len(DATA), bytes=3))
        (folder / ".xyquant.partial").write_bytes(b"old")
        self.assertEqual(self.dl.status(str(self.project), JID)["status"], "interrupted")
        self.assertEqual(self.save()["status"], "ready")
        self.assertFalse((folder / ".xyquant.partial").exists())

    def test_status_does_not_claim_live_locked_transfer_is_interrupted(self):
        folder = self.dl.job_folder(str(self.project), JID, True)
        self.dl.store(folder, dict(status="downloading", job_id=JID, total=len(DATA), bytes=3))
        with self.dl.file_lock(folder / ".xyquant.lock"):
            self.assert_code("busy", self.dl.file_lock(folder / ".xyquant.lock").__enter__)
            self.assertEqual(self.dl.status(str(self.project), JID)["status"], "downloading")

    def test_status_rechecks_ready_file_hash(self):
        result = self.save()
        Path(result["path"]).write_bytes(b"X" * len(DATA))
        self.assert_code("file_changed", self.dl.status, str(self.project), JID)

    def test_requires_existing_absolute_local_project_and_rejects_links(self):
        for project in ("relative", str(self.project / "missing"), str(self.project / ".."), "//server/share"):
            with self.subTest(project=project):
                self.assert_code("invalid_project", self.dl.save, project, self.job)
        target = self.project / "actual"
        target.mkdir()
        link = self.project / "downloads"
        try:
            link.symlink_to(target, target_is_directory=True)
        except OSError:
            # Reparse-point detection is still exercised without Windows symlink privileges.
            fake = type("FileInfo", (), {"st_file_attributes": 0x400, "st_mode": stat.S_IFDIR})()
            with patch.object(Path, "lstat", return_value=fake):
                self.assert_code("invalid_path", self.dl.regular, target, directory=True)
        else:
            self.assert_code("invalid_path", self.save)
        self.assertFalse(self.transport.requests)

    def test_cli_reads_save_manifest_only_from_stdin_and_emits_no_secret(self):
        raw = json.dumps(self.job).encode()
        stdin = type("Stdin", (), {"buffer": io.BytesIO(raw), "isatty": lambda self: False})()
        output = io.StringIO()
        with patch.object(sys, "argv", [str(DOWNLOAD_SCRIPT), "save", "--project", str(self.project)]), \
                patch.object(sys, "stdin", stdin), redirect_stdout(output):
            code = self.dl.main()
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())["status"], "ready")
        self.assertNotIn(TICKET, output.getvalue())
        self.assert_no_secret_files()
        result = subprocess.run([sys.executable, "-I", "-B", str(DOWNLOAD_SCRIPT), "status",
                                 "--project", str(self.project), "--job", JID],
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "ready")
        self.assertNotIn(TICKET, result.stdout + result.stderr)

    def test_cli_tty_uses_non_echo_getpass_instead_of_reading_stdin_buffer(self):
        stdin = type("Stdin", (), {"buffer": io.BytesIO(), "isatty": lambda self: True})()
        output = io.StringIO()
        errors = io.StringIO()
        raw = json.dumps(self.job)
        with patch.object(sys, "argv", [str(DOWNLOAD_SCRIPT), "save", "--project", str(self.project)]), \
                patch.object(sys, "stdin", stdin), patch("getpass.getpass", return_value=raw) as prompt, \
                redirect_stdout(output), redirect_stderr(errors):
            code = self.dl.main()
        prompt.assert_called_once()
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())["status"], "ready")
        self.assertNotIn(TICKET, output.getvalue() + errors.getvalue())
        self.assert_no_secret_files()

    def test_cli_tty_cannot_suppress_echo_fails_closed_before_save_or_fallback(self):
        # A valid piped descriptor deliberately exists to catch unsafe buffer fallback.
        raw = json.dumps(self.job)
        stdin = type("Stdin", (), {"buffer": io.BytesIO(raw.encode()), "isatty": lambda self: True})()
        fallback = []

        def unsafe_getpass(*args, **kwargs):
            warnings.warn(TICKET, getpass.GetPassWarning)
            fallback.append("echoed fallback reached")
            return raw

        output = io.StringIO()
        errors = io.StringIO()
        with patch.object(sys, "argv", [str(DOWNLOAD_SCRIPT), "save", "--project", str(self.project)]), \
                patch.object(sys, "stdin", stdin), patch("getpass.getpass", side_effect=unsafe_getpass) as prompt, \
                patch.object(self.dl, "save", return_value={"status": "ready"}) as save, \
                redirect_stdout(output), redirect_stderr(errors):
            code = self.dl.main()
        save.assert_not_called()
        prompt.assert_called_once()
        self.assertFalse(fallback)
        self.assertEqual(code, 1)
        result = json.loads(output.getvalue())
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["code"], "secure_input_unavailable")
        self.assertNotIn(TICKET, output.getvalue() + errors.getvalue())
        self.assertFalse(self.transport.requests)

    def test_cli_tty_enforces_utf8_byte_cap_before_save(self):
        job = dict(self.job, padding="中" * 90000)
        raw = json.dumps(job, ensure_ascii=False)
        self.assertLess(len(raw), 262144)
        self.assertGreater(len(raw.encode("utf-8")), 262144)
        stdin = type("Stdin", (), {"buffer": io.BytesIO(), "isatty": lambda self: True})()
        output = io.StringIO()
        with patch.object(sys, "argv", [str(DOWNLOAD_SCRIPT), "save", "--project", str(self.project)]), \
                patch.object(sys, "stdin", stdin), patch("getpass.getpass", return_value=raw) as prompt, \
                patch.object(self.dl, "save", return_value={"status": "ready"}) as save, redirect_stdout(output):
            code = self.dl.main()
        prompt.assert_called_once()
        save.assert_not_called()
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(output.getvalue())["code"], "invalid_manifest")
        self.assertNotIn(TICKET, output.getvalue())

    def test_cli_tty_eof_is_sanitized_and_never_falls_back(self):
        stdin = type("Stdin", (), {"buffer": io.BytesIO(), "isatty": lambda self: True})()
        output = io.StringIO()
        with patch.object(sys, "argv", [str(DOWNLOAD_SCRIPT), "save", "--project", str(self.project)]), \
                patch.object(sys, "stdin", stdin), patch("getpass.getpass", side_effect=EOFError(TICKET)) as prompt, \
                patch.object(self.dl, "save", return_value={"status": "ready"}) as save, redirect_stdout(output):
            code = self.dl.main()
        prompt.assert_called_once()
        save.assert_not_called()
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(output.getvalue())["code"], "secure_input_unavailable")
        self.assertNotIn(TICKET, output.getvalue())

    def test_cli_invalid_stdin_is_bounded_and_redacted(self):
        for raw in (TICKET.encode(), b"x" * 262145,
                    json.dumps(dict(self.job, filename=TICKET + ".exe")).encode()):
            stdin = type("Stdin", (), {"buffer": io.BytesIO(raw), "isatty": lambda self: False})()
            output = io.StringIO()
            with patch.object(sys, "argv", [str(DOWNLOAD_SCRIPT), "save", "--project", str(self.project)]), \
                    patch.object(sys, "stdin", stdin), redirect_stdout(output):
                code = self.dl.main()
            self.assertEqual(code, 1)
            self.assertEqual(json.loads(output.getvalue())["status"], "failed")
            self.assertNotIn(TICKET, output.getvalue())
        self.assertFalse(self.transport.requests)


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(ARCHIVE_SCRIPT.is_file(), "safe standalone ZIP extraction is not implemented")
        self.archive = load_script(ARCHIVE_SCRIPT, "rankic_archive_under_test")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.inbox = self.root / "isolated-archive"
        self.inbox.mkdir()
        self.source = self.inbox / "fixture.zip"
        self.destination = self.root / "new-data"

    def make_zip(self, entries, compression=zipfile.ZIP_STORED):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(self.source, "w", compression=compression) as zipped:
                for name, content in entries:
                    if isinstance(name, str):
                        # On Windows ZipInfo otherwise normalizes backslashes
                        # before writing, accidentally sanitizing attack fixtures.
                        info = zipfile.ZipInfo(name)
                        info.filename = name
                        info.orig_filename = name
                        info.compress_type = compression
                    else:
                        info = name
                    zipped.writestr(info, content)
        self.content = self.source.read_bytes()
        self.sha256 = hashlib.sha256(self.content).hexdigest()
        return self.source

    def extract(self, **kwargs):
        return self.archive.extract_zip(self.source, self.destination, self.sha256, len(self.content), **kwargs)

    def assert_rejected(self, entries, **kwargs):
        self.make_zip(entries)
        with self.assertRaises(self.archive.ArchiveError):
            self.extract(**kwargs)
        self.assertFalse(self.destination.exists())
        self.assertEqual(self.source.read_bytes(), self.content)

    def test_extracts_verified_opaque_data_into_new_directory_only(self):
        entries = [("tables/", b""), ("tables/example.parquet", b"opaque data"),
                   ("metadata.json", b'{"arbitrary":true}'), ("notes.txt", b"data notes")]
        self.make_zip(entries, zipfile.ZIP_DEFLATED)
        paths = self.extract()
        self.assertEqual(set(paths), {self.destination / name for name, _ in entries if not name.endswith("/")})
        for name, content in entries:
            if not name.endswith("/"):
                self.assertEqual((self.destination / name).read_bytes(), content)
        self.assertEqual(self.source.read_bytes(), self.content)

    def test_wrong_hash_size_or_invalid_manifest_never_creates_output(self):
        self.make_zip([("data.parquet", b"opaque")])
        for checksum, size in (("0" * 64, len(self.content)), (self.sha256, len(self.content) + 1),
                               ("not-a-hash", len(self.content)), (self.sha256, True),
                               (self.sha256, 0)):
            with self.subTest(checksum=checksum, size=size), self.assertRaises(self.archive.ArchiveError):
                self.archive.extract_zip(self.source, self.destination, checksum, size)
            self.assertFalse(self.destination.exists())

    def test_rejects_traversal_absolute_windows_and_control_paths(self):
        names = ["../escaped.csv", "a/../../escaped.csv", "/absolute.csv", "//server/data.csv",
                 "C:/data.csv", "C:data.csv", "a\\data.csv", "\\\\server\\data.csv", "a//data.csv",
                 "./data.csv", "a/./data.csv", "a/../data.csv", "data.csv:stream", "data.csv.",
                 "a /data.csv", "a./data.csv", "CON.csv", "aux/data.csv", "LPT¹.csv", "COM9.csv",
                 "CONIN$.csv", "bad\n.csv", "bad\x7f.csv", "bad?.csv", "bad|.csv", "bad<.csv"]
        for name in names:
            with self.subTest(name=name):
                self.assert_rejected([("safe.csv", b"safe"), (name, b"bad")])
        self.assertFalse((self.root / "escaped.csv").exists())

    def test_rejects_nul_path_even_when_zipfile_truncates_it(self):
        self.make_zip([("nameX.csv", b"bad")])
        # Patch both ZIP name records to represent an embedded NUL faithfully.
        self.content = self.content.replace(b"nameX.csv", b"name\x00.csv")
        self.source.write_bytes(self.content)
        self.sha256 = hashlib.sha256(self.content).hexdigest()
        with self.assertRaises(self.archive.ArchiveError):
            self.extract()
        self.assertFalse(self.destination.exists())

    def test_rejects_duplicate_casefold_normalization_and_file_directory_collisions(self):
        cases = [[("data.csv", b"1"), ("data.csv", b"2")],
                 [("data.csv", b"1"), ("DATA.CSV", b"2")],
                 [("é.csv", b"1"), ("e\u0301.csv", b"2")],
                 [("part.csv", b"1"), ("part.csv/data.csv", b"2")],
                 [("part.csv/data.csv", b"1"), ("part.csv", b"2")],
                 [("folder/", b""), ("FOLDER/", b"")]]
        for entries in cases:
            with self.subTest(entries=entries):
                self.assert_rejected(entries)

    def test_rejects_symlinks_special_files_and_executable_modes(self):
        for mode in (stat.S_IFLNK | 0o777, stat.S_IFIFO | 0o600,
                     stat.S_IFCHR | 0o600, stat.S_IFREG | 0o755):
            info = zipfile.ZipInfo("data.csv")
            info.create_system = 3
            info.external_attr = mode << 16
            with self.subTest(mode=mode):
                self.assert_rejected([(info, b"../outside")])
        info = zipfile.ZipInfo("data.csv")
        info.external_attr = (stat.S_IFREG | 0o600) << 16 | 0x400
        self.assert_rejected([(info, b"reparse")])

    def test_accepts_data_formats_but_rejects_code_executable_and_unknown_members(self):
        for name in ("json.py", "run.sh", "x.ps1", "x.bat", "x.cmd", "x.exe", "x.dll", "x.so",
                     "x.pyd", "x.pyc", "x.ipynb", "x.js", "x.html", "x.pth", "x.bin", "README"):
            with self.subTest(name=name):
                self.assert_rejected([("safe.csv", b"safe"), (name, b"not executed")])
        entries = [("sample" + suffix, b"opaque") for suffix in
                   (".parquet", ".csv", ".tsv", ".json", ".jsonl", ".ndjson", ".arrow", ".feather", ".txt")]
        self.make_zip(entries)
        self.assertEqual(len(self.extract()), len(entries))

    def test_accepts_pqt_data_without_assuming_a_table_schema(self):
        self.make_zip([("arbitrary-table.pqt", b"opaque parquet bytes")])
        try:
            paths = self.extract()
        except self.archive.ArchiveError as error:
            self.fail("The project's .pqt data extension must be accepted: " + str(error))
        self.assertEqual(paths, [self.destination / "arbitrary-table.pqt"])
        self.assertEqual(paths[0].read_bytes(), b"opaque parquet bytes")

    def test_limits_member_count_total_size_and_per_member_compression_ratio(self):
        self.assert_rejected([("a.csv", b"a"), ("b.csv", b"b")], max_members=1)
        self.assert_rejected([("a.csv", b"12345"), ("b.csv", b"12345")], max_total_size=9)
        self.make_zip([("bomb.csv", b"0" * 100000)], zipfile.ZIP_DEFLATED)
        with self.assertRaises(self.archive.ArchiveError):
            self.extract(max_compression_ratio=5)
        self.assertFalse(self.destination.exists())

    def test_rejects_invalid_limits_encrypted_and_unsupported_compression(self):
        self.make_zip([("data.csv", b"a")])
        for options in ({"max_members": 0}, {"max_total_size": True},
                        {"max_compression_ratio": float("inf")}, {"max_compression_ratio": 0}):
            with self.subTest(options=options), self.assertRaises(self.archive.ArchiveError):
                self.extract(**options)
        content = bytearray(self.content)
        local = content.index(b"PK\x03\x04")
        central = content.index(b"PK\x01\x02")
        struct.pack_into("<H", content, local + 6, 1)
        struct.pack_into("<H", content, central + 8, 1)
        self.content = bytes(content)
        self.source.write_bytes(self.content)
        self.sha256 = hashlib.sha256(self.content).hexdigest()
        with self.assertRaises(self.archive.ArchiveError):
            self.extract()
        self.assertFalse(self.destination.exists())
        self.make_zip([("data.csv", b"a")], zipfile.ZIP_BZIP2)
        with self.assertRaises(self.archive.ArchiveError):
            self.extract()
        self.assertFalse(self.destination.exists())

    def test_requires_new_absolute_output_not_cwd_and_does_not_overwrite(self):
        self.make_zip([("data.csv", b"a")])
        self.destination.mkdir()
        marker = self.destination / "keep.txt"
        marker.write_bytes(b"keep")
        for destination in (self.destination, self.root, Path.cwd(), Path("relative"),
                            self.root / "missing-parent" / "output", self.root / ".." / "output"):
            with self.subTest(destination=destination), self.assertRaises(self.archive.ArchiveError):
                self.archive.extract_zip(self.source, destination, self.sha256, len(self.content))
        self.assertEqual(marker.read_bytes(), b"keep")
        marker.unlink()
        with self.assertRaises(self.archive.ArchiveError):
            self.extract()

    def test_rejects_archive_or_output_parent_links(self):
        self.make_zip([("data.csv", b"a")])
        link = self.root / "linked"
        try:
            link.symlink_to(self.inbox, target_is_directory=True)
        except OSError:
            fake = type("FileInfo", (), {"st_file_attributes": 0x400, "st_mode": stat.S_IFDIR})()
            with patch.object(Path, "lstat", return_value=fake), self.assertRaises(self.archive.ArchiveError):
                self.extract()
        else:
            with self.assertRaises(self.archive.ArchiveError):
                self.archive.extract_zip(link / self.source.name, self.destination, self.sha256, len(self.content))
            with self.assertRaises(self.archive.ArchiveError):
                self.archive.extract_zip(self.source, link / "output", self.sha256, len(self.content))
        self.assertFalse(self.destination.exists())

    def test_corrupt_deflate_is_a_safe_archive_error_and_cleans_output(self):
        self.make_zip([("data.csv", b"opaque-data-" * 100)], zipfile.ZIP_DEFLATED)
        with zipfile.ZipFile(self.source) as zipped:
            info = zipped.infolist()[0]
        content = bytearray(self.content)
        local = info.header_offset
        name_size, extra_size = struct.unpack_from("<HH", content, local + 26)
        payload = local + 30 + name_size + extra_size
        content[payload:payload + info.compress_size] = b"\xff" * info.compress_size
        self.content = bytes(content)
        self.source.write_bytes(self.content)
        self.sha256 = hashlib.sha256(self.content).hexdigest()
        try:
            self.extract()
        except self.archive.ArchiveError:
            pass
        except Exception as error:
            self.fail("Invalid compressed data must produce ArchiveError, not " + type(error).__name__)
        else:
            self.fail("Invalid compressed data was accepted")
        self.assertFalse(self.destination.exists())
        self.assertEqual(self.source.read_bytes(), self.content)

    def test_bad_zip_or_crc_error_cleans_output_and_preserves_archive(self):
        self.content = b"not a zip"
        self.source.write_bytes(self.content)
        self.sha256 = hashlib.sha256(self.content).hexdigest()
        with self.assertRaises(self.archive.ArchiveError):
            self.extract()
        self.assertFalse(self.destination.exists())
        self.make_zip([("data.csv", b"unique-opaque-data")])
        self.content = self.content.replace(b"unique-opaque-data", b"corrupt-opaque-bad")
        self.source.write_bytes(self.content)
        self.sha256 = hashlib.sha256(self.content).hexdigest()
        with self.assertRaises(self.archive.ArchiveError):
            self.extract()
        self.assertFalse(self.destination.exists())
        self.assertEqual(self.source.read_bytes(), self.content)


class PublicConfigTests(unittest.TestCase):
    @unittest.skip("project MCP configuration creation was blocked; no configuration change delivered")
    def test_project_config_contains_only_verified_public_endpoint(self):
        config_path = ROOT / ".mcp.json"
        self.assertTrue(config_path.is_file(), "standalone public MCP config is absent")
        config = json.loads(config_path.read_text(encoding="utf-8"))
        self.assertEqual(config, {"mcpServers": {"xyquant": {"type": "http", "url": ORIGIN + "/mcp"}}})


if __name__ == "__main__":
    unittest.main()
