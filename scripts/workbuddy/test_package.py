import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import package


class WorkBuddyPackageTests(unittest.TestCase):
    def test_shipped_catalog_contains_complete_existing_payloads(self):
        self.assertEqual(package.check(), {"plugins": 2, "metadata_files": 8})

    def test_missing_payload_is_rejected_before_marketplace_installation(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for relative, value in package.render().items():
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(value), encoding="utf-8")
            for source in [".agents/plugins/marketplace.json"] + [
                "plugins/" + name + "/.codex-plugin/plugin.json" for name in package.DESCRIPTIONS
            ]:
                path = root / source
                path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(package.ROOT / source, path)
            with self.assertRaisesRegex(ValueError, "Incomplete plugin payload"):
                package.check(root)

    def test_marketplace_cannot_reference_an_external_plugin(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = root / ".agents/plugins/marketplace.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({"name": "invalid", "plugins": [
                {"name": "xyquant-factors", "source": {"path": "../outside"}}
            ]}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "inside the marketplace"):
                package.render(root)

    def test_native_launcher_resolves_from_an_unrelated_directory_with_spaces(self):
        name = "xyquant-factors" if os.name == "nt" else "xyquant-factors-macos"
        plugin = package.ROOT / "plugins" / name
        config = package.render()[Path("plugins") / name / ".mcp.workbuddy.json"]["mcpServers"]["xyquant_factors"]
        resolve = lambda value: value.replace(package.HOST_ROOT, str(plugin))
        args = [resolve(config["command"])] + [resolve(value) for value in config["args"]]
        args[-1] = "--version"
        with tempfile.TemporaryDirectory(prefix="xyquant workbuddy project ") as project:
            result = subprocess.run(args, cwd=project, capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        version = json.loads((plugin / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))["version"]
        self.assertEqual(result.stdout.strip(), version)


if __name__ == "__main__":
    unittest.main()
