"""One-shot, loopback-only handoff for an authorized MCP download job."""
from hmac import compare_digest
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import secrets
import time


MAX_JOB_BYTES = 64 * 1024


def receive_job(job_id, wait_seconds, transfer):
    local_token = secrets.token_hex(32).encode("ascii")

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            self.request.settimeout(3)
            super().setup()

        def log_message(self, *_):
            pass

        def reply(self, status, data):
            raw = json.dumps(data, ensure_ascii=True).encode("ascii")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self):
            if (self.path != "/mcp-job" or self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}"
                    or "Origin" in self.headers or "Transfer-Encoding" in self.headers
                    or not compare_digest(self.headers.get("X-XYQuant-Local", "").encode("latin-1"), local_token)):
                self.reply(403, {"status": "refused"})
                return
            self.server.result = {"status": "failed", "code": "invalid_request"}
            lengths = self.headers.get_all("Content-Length", [])
            if len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdigit() or len(lengths[0]) > 6:
                self.reply(400, self.server.result)
                return
            size = int(lengths[0])
            if self.headers.get_content_type() != "application/json" or not 0 < size <= MAX_JOB_BYTES:
                self.reply(400, self.server.result)
                return
            try:
                raw = self.rfile.read(size)
            except OSError:
                return
            if len(raw) != size:
                return
            try:
                job = json.loads(raw)
                if not isinstance(job, dict) or job.get("id") != job_id or job.get("status") != "ready":
                    raise ValueError
            except (ValueError, RecursionError):
                self.server.result = {"status": "failed", "code": "invalid_job"}
                self.reply(422, self.server.result)
                return
            try:
                self.reply(202, {"status": "accepted"})  # completion is the local process result
            except OSError:
                pass
            self.server.result = transfer(raw)

    class LocalServer(HTTPServer):
        def handle_error(self, *_):
            pass  # never dump request-derived exceptions

    with LocalServer(("127.0.0.1", 0), Handler) as server:
        server.timeout = 0.5
        server.result = None
        print(json.dumps({"status": "listening", "port": server.server_port, "local_nonce": local_token.decode()}), flush=True)
        deadline = time.monotonic() + wait_seconds
        while server.result is None and time.monotonic() < deadline:
            server.handle_request()
        return server.result or {"status": "failed", "code": "receive_timeout"}
