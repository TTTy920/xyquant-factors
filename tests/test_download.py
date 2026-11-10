import concurrent.futures
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

PLUGIN = Path(__file__).resolve().parents[1] / "plugins" / "xyquant"
spec = importlib.util.spec_from_file_location("download", PLUGIN / "skills/factor-download/scripts/download.py")
dl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dl)
DATA = b"factor fixture\n" * 300
DIGEST = hashlib.sha256(DATA).hexdigest()
JID = "c" * 32


class Server(BaseHTTPRequestHandler):
    def do_GET(self):
        self.server.requests.append((self.path, self.headers.get("Authorization")))
        status, content = self.server.replies.pop(0)
        self.send_response(status)
        if status == 302:
            self.send_header("Location", "/outside")
        self.send_header("Content-Length", str(len(DATA)))
        self.send_header("X-Checksum-SHA256", DIGEST)
        self.end_headers()
        if content:
            self.wfile.write(content)

    def log_message(self, *unused):
        pass


class DownloadTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = str(Path(self.tmp.name).resolve())
        server = ThreadingHTTPServer(("127.0.0.1", 0), Server)
        server.requests, server.replies = [], [(200, DATA)]
        self.server = server
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.origin = f"http://127.0.0.1:{server.server_port}"
        for patcher in (patch.object(dl, "ORIGIN", self.origin), patch.object(dl, "RETRIES", (0, 0))):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.job = dict(id=JID, status="ready", filename="盈利_TTM.parquet", bytes=len(DATA), sha256=DIGEST,
                        download=dict(url=self.origin + "/mcp/downloads/" + JID, ticket="unit-test-file-only",
                                      expires_at=time.time() + 90))

    def save(self):
        return dl.save(self.project, self.job)

    def test_parallel_saves_reuse_one_verified_file_and_do_not_persist_ticket(self):
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            results = list(pool.map(lambda _: self.save(), range(2)))
        self.assertEqual(len(self.server.requests), 1)
        self.assertEqual(self.server.requests[0][1], "XYQuant-Download unit-test-file-only")
        self.assertEqual(results[0]["path"], results[1]["path"])
        target = Path(results[0]["path"])
        self.assertEqual(target.read_bytes(), DATA)
        self.assertEqual(dl.status(self.project, JID)["sha256"], DIGEST)
        for path in Path(self.project).rglob("*"):
            if path.is_file():
                self.assertNotIn(self.job["download"]["ticket"].encode(), path.read_bytes())
        target.write_bytes(b"user data")
        with self.assertRaisesRegex(dl.DownloadError, "内容不同"):
            self.save()
        self.assertEqual(target.read_bytes(), b"user data")

    def test_disconnect_restarts_same_download_and_mismatched_hash_never_publishes(self):
        self.server.replies = [(503, b""), (200, DATA[:30]), (200, b"X" * len(DATA))]
        with self.assertRaises(dl.DownloadError) as raised:
            self.save()
        self.assertEqual(raised.exception.code, "checksum_mismatch")
        self.assertEqual(len(self.server.requests), 3)
        self.assertEqual(dl.status(self.project, JID)["status"], "failed")
        folder = Path(self.project, "downloads", "factors", JID)
        self.assertFalse((folder / self.job["filename"]).exists())
        self.assertFalse((folder / ".xyquant.partial").exists())
        self.server.replies = [(200, DATA)]
        self.assertEqual(self.save()["status"], "ready")

    def test_refuses_redirect_and_permission_or_expiry_are_not_retried(self):
        for code, expected in ((302, "redirect_refused"), (403, "permission_denied"), (401, "grant_expired")):
            self.server.requests.clear()
            self.server.replies = [(code, b"")]
            with self.assertRaises(dl.DownloadError) as error:
                self.save()
            self.assertEqual(error.exception.code, expected)
            self.assertEqual(len(self.server.requests), 1)
        self.job["download"]["expires_at"] = time.time() - 10
        with self.assertRaises(dl.DownloadError) as error:
            self.save()
        self.assertEqual(error.exception.code, "grant_expired")
        self.assertEqual(len(self.server.requests), 1)

    def test_rejects_foreign_host_and_nonportable_paths_before_connecting(self):
        for name in ("../../x.zip", "COM1.zip", "C:bad.zip", ".xyquant.lock", "a\\b.zip"):
            with self.subTest(name=name), self.assertRaises(dl.DownloadError):
                dl.save(self.project, dict(self.job, filename=name))
        self.job["download"]["url"] = "https://example.org/mcp/downloads/" + JID
        with self.assertRaises(dl.DownloadError):
            self.save()
        self.assertFalse(self.server.requests)

    def test_interrupted_record_is_recoverable(self):
        folder = dl.job_folder(self.project, JID, True)
        dl.store(folder, dict(status="downloading", job_id=JID, total=len(DATA), bytes=3))
        (folder / ".xyquant.partial").write_bytes(b"old")
        self.assertEqual(dl.status(self.project, JID)["status"], "interrupted")
        self.assertEqual(self.save()["status"], "ready")
        self.assertFalse((folder / ".xyquant.partial").exists())
