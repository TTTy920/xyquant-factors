import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from package import build


class PackageTest(unittest.TestCase):
    def test_all_marketplaces_select_one_directory_and_packaged_endpoints_agree(self):
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = build(Path(tmp))
            expected = zip_path.with_suffix(".zip.sha256").read_text().split()[0]
            self.assertEqual(hashlib.sha256(zip_path.read_bytes()).hexdigest(), expected)
            with zipfile.ZipFile(zip_path) as archive:
                archive.extractall(tmp)
            pkg = Path(tmp) / "xyquant"
            manifest = json.loads((pkg / "plugin.json").read_text(encoding="utf-8"))
            for market, host in ((".agents/plugins", ".codex-plugin"), (".codebuddy-plugin", ".codebuddy-plugin"), (".claude-plugin", ".claude-plugin")):
                entries = json.loads((ROOT / market / "marketplace.json").read_text(encoding="utf-8"))["plugins"]
                self.assertEqual(len(entries), 1)
                source = entries[0]["source"]
                self.assertEqual(source if isinstance(source, str) else source["path"], "./plugins/xyquant")
                meta = json.loads((pkg / host / "plugin.json").read_text(encoding="utf-8"))
                self.assertEqual((meta["name"], meta["version"]), (entries[0]["name"], manifest["version"]))
                servers = json.loads((pkg / meta["mcpServers"]).read_text())["mcpServers"]
                self.assertEqual(list(servers), ["xyquant"])
                self.assertEqual(servers["xyquant"]["url"], "https://47.103.215.251/mcp")
                self.assertNotIn("command", servers["xyquant"])
            portable = json.loads((pkg / "mcp.json").read_text())["mcpServers"]["xyquant"]
            self.assertEqual(portable["type"], "streamable-http")
            self.assertEqual(portable["extensions"]["com.openai"]["auth"]["client"]["mode"], "dcr")
            skills = {s.parent.name for s in (pkg / "skills").glob("*/SKILL.md")}
            self.assertTrue({"factor-download", "barra-attribution", "strategy-attribution"} <= skills)
            script = pkg / "skills/barra-attribution/scripts/attribute.py"
            factors = ("Size", "Beta", "Momentum", "ResidualVolatility", "NonlinearSize", "BookToPrice", "Liquidity", "EarningsYield", "Growth", "Leverage")
            data = dict(holdings=[{"日期":"2026-09", "股票代码":"000001.SZ", "持仓比例":0.5}],
                        stock_exposures=[{"月份":"2026-09", "股票代码":"000001.SZ", **dict.fromkeys(factors, 2)}],
                        factor_returns=[{"月份":"2026-10", **dict.fromkeys(factors, .01)}])
            sample = Path(tmp) / "portfolio.json"
            sample.write_text(json.dumps(data), encoding="utf-8")
            result = json.loads(subprocess.check_output([sys.executable, "-X", "utf8", str(script), str(sample)], encoding="utf-8"))
            self.assertEqual(result["月末组合暴露"][0]["Size"], 1)
            self.assertEqual(result["月度风格收益贡献"][0]["Size"], .01)
