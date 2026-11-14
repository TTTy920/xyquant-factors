"""A parsed manifest can still omit the endpoint from the desktop inventory."""
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("codex"), "requires Codex")
class CodexDiscoveryTest(unittest.TestCase):
    def test_installed_marketplace_exposes_skills_and_remote_mcp(self):
        with tempfile.TemporaryDirectory() as profile:
            env = dict(os.environ, CODEX_HOME=profile)
            for cmd in (["marketplace", "add", str(ROOT)], ["add", "xyquant@xyquant"]):
                subprocess.run(["codex", "plugin", *cmd, "--json"], env=env, capture_output=True,
                               check=True, timeout=20)
            proc = subprocess.Popen(["codex", "app-server"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=subprocess.DEVNULL, env=env, encoding="utf-8")
            messages = queue.Queue()
            def reader():
                for line in proc.stdout:
                    messages.put(json.loads(line))
            thread = threading.Thread(target=reader, daemon=True)
            thread.start()
            def send(method, params, seq=None):
                msg = dict(method=method, params=params)
                if seq is not None:
                    msg["id"] = seq
                proc.stdin.write(json.dumps(msg) + "\n")
                proc.stdin.flush()
                if seq is not None:
                    while True:
                        reply = messages.get(timeout=15)
                        if reply.get("id") == seq:
                            self.assertNotIn("error", reply)
                            return reply["result"]
            try:
                send("initialize", {"clientInfo":{"name":"xyquant-tests", "version":"1"},
                                    "capabilities":{"experimentalApi":True}}, 1)
                send("initialized", {})
                result = send("plugin/read", {"pluginName":"xyquant", "marketplacePath":
                              str(ROOT / ".agents/plugins/marketplace.json")}, 2)["plugin"]
                self.assertEqual(result["mcpServers"], ["xyquant"])
                self.assertEqual({"xyquant:factor-download"}, {s["name"] for s in result["skills"]})
            finally:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
                thread.join(timeout=2)
                proc.stdin.close()
                proc.stdout.close()
