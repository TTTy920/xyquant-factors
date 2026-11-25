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
                login_skill = pkg / meta["skills"] / "xyquant-login" / "SKILL.md"
                self.assertTrue(login_skill.is_file(), f"{host} cannot discover xyquant-login")
                servers = json.loads((pkg / meta["mcpServers"]).read_text())["mcpServers"]
                self.assertEqual(list(servers), ["xyquant"])
                self.assertEqual(servers["xyquant"]["url"], "https://47.103.215.251/mcp")
                self.assertNotIn("command", servers["xyquant"])
            portable = json.loads((pkg / "mcp.json").read_text())["mcpServers"]["xyquant"]
            self.assertEqual(portable["type"], "streamable-http")
            self.assertEqual(portable["url"], servers["xyquant"]["url"])
            skills = {s.parent.name for s in (pkg / "skills").glob("*/SKILL.md")}
            self.assertEqual(skills, {"factor-download", "portfolio-attribution", "xyquant-login", "factor-rankic", "stock-holding-diagnosis", "factor-synthesis"})
            skill_source = ROOT / "plugins/xyquant/skills/portfolio-attribution"
            skill_package = pkg / "skills/portfolio-attribution"
            for source in skill_source.rglob("*"):
                if source.is_file() and "__pycache__" not in source.parts:
                    self.assertEqual((skill_package / source.relative_to(skill_source)).read_bytes(), source.read_bytes())
            for name in ("xyquant_data.py", "xyquant_catalog.py", "xyquant_flat.py",
                         "shared/xyquant_download.py", "shared/mcp_receive.py",
                         "skills/factor-rankic/scripts/rankic.py", "skills/factor-synthesis/scripts/synthesis.py"):
                self.assertTrue((pkg / name).is_file(), name)
            for name in ("SKILL.md", "requirements.txt", "scripts/diagnosis.py", "scripts/sources.py",
                         "scripts/mcp_transfer.py", "scripts/news_context.py", "scripts/xyquant_cache.py",
                         "references/host-transfer.md", "references/holdings.md", "references/news.md"):
                relative = Path("skills/stock-holding-diagnosis") / name
                self.assertEqual((pkg / relative).read_bytes(), (ROOT / "plugins/xyquant" / relative).read_bytes())
            self.assertFalse(any(name.endswith((".pqt", ".parquet")) for name in archive.namelist()))
            self.assertFalse(any("barra-attribution" in name or "strategy-attribution" in name for name in archive.namelist()))
            script = ROOT / "disabled-skills/barra-attribution/scripts/attribute.py"
            factors = ("Size", "Beta", "Momentum", "ResidualVolatility", "NonlinearSize", "BookToPrice", "Liquidity", "EarningsYield", "Growth", "Leverage")
            data = dict(holdings=[{"日期":"2026-09", "股票代码":"000001.SZ", "持仓比例":0.5}],
                        stock_exposures=[{"月份":"2026-09", "股票代码":"000001.SZ", **dict.fromkeys(factors, 2)}],
                        factor_returns=[{"月份":"2026-10", **dict.fromkeys(factors, .01)}])
            sample = Path(tmp) / "portfolio.json"
            sample.write_text(json.dumps(data), encoding="utf-8")
            result = json.loads(subprocess.check_output([sys.executable, "-X", "utf8", str(script), str(sample)], encoding="utf-8"))
            self.assertEqual(result["月末组合暴露"][0]["Size"], 1)
            self.assertEqual(result["月度风格收益贡献"][0]["Size"], .01)

    def test_packaged_login_entry_points(self):
        with tempfile.TemporaryDirectory() as tmp:
            with zipfile.ZipFile(build(Path(tmp))) as archive:
                archive.extractall(tmp)
            relative = Path("skills/xyquant-login/scripts")
            scripts = Path(tmp) / "xyquant" / relative
            source = ROOT / "plugins/xyquant" / relative
            for name in (
                "login.py", "claude_login.py", "codex_direct.py", "xyquant_network.py",
                "workbuddy_login.py", "workbuddy_probe.py", "workbuddy_oauth.json",
            ):
                with self.subTest(helper=name):
                    self.assertEqual((scripts / name).read_bytes(), (source / name).read_bytes())

            python = [sys.executable, "-X", "utf8"]
            help_text = subprocess.check_output(
                [*python, str(scripts / "login.py"), "--help"], encoding="utf-8", timeout=10,
            )
            for client in ("codex", "claude", "workbuddy"):
                self.assertIn(client, help_text)
            native = subprocess.run(
                [*python, str(scripts / "workbuddy_login.py"), "--native"],
                capture_output=True, encoding="utf-8", timeout=10,
            )
            self.assertEqual(native.returncode, 3, native.stderr)
            self.assertIn("https://47.103.215.251/mcp", native.stdout)
