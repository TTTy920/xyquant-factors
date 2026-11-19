"""Exercise the original diagnosis rules with the plugin's current data contracts."""
from datetime import date
import hashlib
from io import BytesIO
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from zipfile import ZipFile

import pyarrow as pa
import pyarrow.parquet as pq

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import diagnosis
import mcp_transfer
import news_context
import sources
from xyquant_cache import attribution_download, factor_download

DAY = date(2025, 1, 31)
PREVIOUS = date(2025, 1, 24)
STOCK = "600519.SH"
VERSION = "original-20261119-0123456789ab"


def parquet(rows):
    stream = BytesIO()
    table = pa.Table.from_pylist(rows)
    # Match live server dates and dictionary-encoded portfolio stock codes.
    table = table.set_column(table.schema.get_field_index("date"), "date", table["date"].cast(pa.timestamp("ns")))
    if "stock_code" in table.column_names:
        table = table.set_column(table.schema.get_field_index("stock_code"), "stock_code",
                                 table["stock_code"].dictionary_encode())
    pq.write_table(table, stream)
    return stream.getvalue()


def sha(content):
    return hashlib.sha256(content).hexdigest()


class DiagnosisTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        # macOS /var is a symlink; the downloader requires a resolved project.
        self.project = Path(temporary.name).resolve()
        self.expiry = time.time() + 3600

    def factors(self, overrides=None):
        out = BytesIO()
        with ZipFile(out, "w") as archive:
            for factor, direction in diagnosis.FACTORS.items():
                rows = [{"date": DAY, "stock_cd": STOCK if i == 0 else f"600{i:03}.SH",
                         "alpha": float(10 - i if direction == "descending" else i + 1)}
                        for i in range(10)]
                archive.writestr(factor + ".parquet", parquet(rows))
        content = out.getvalue()
        job = {"id": "1" * 32, "status": "ready", "filename": "factors.zip", "extension": "zip",
               "bytes": len(content), "sha256": sha(content),
               "selection": {"factors": list(diagnosis.FACTORS), "codes": [],
                             "start": DAY.isoformat(), "end": DAY.isoformat()},
               "download": {"url": factor_download.ORIGIN + "/mcp/downloads/" + "1" * 32,
                            "ticket": "synthetic-test-ticket", "expires_at": self.expiry}}
        job.update(overrides or {})
        def transfer(grant, path, size, checksum, progress):
            path.write_bytes(content)
            progress(len(content))
        with patch.object(factor_download, "transfer", side_effect=transfer):
            result = mcp_transfer.transfer("factors", self.project, job["id"], json.dumps(job).encode())
        return job, result

    def holdings(self, expiry=None, unrelated_missing=False):
        catalogue = {"schema_version": 1, "snapshots": "weights/snapshots.parquet", "portfolios": {}}
        files, snapshots = {}, []
        for name in diagnosis.STRATEGIES:
            path = "weights/portfolio_" + name + ".parquet"
            catalogue["portfolios"][name] = {"path": path}
            rows = []
            for day, target in ((PREVIOUS, 20.0), (DAY, 30.0)):
                rows.extend([{"date": day, "stock_code": STOCK, "weight": target},
                             {"date": day, "stock_code": "000001.SZ", "weight": 100.0 - target}])
                snapshots.append({"date": day, "kind": "portfolio", "name": name,
                                  "valid": True, "total": 100.0, "cash": 0.0})
            files[path] = parquet(rows)
        files[catalogue["snapshots"]] = parquet(snapshots)
        files["catalog.json"] = json.dumps(catalogue, ensure_ascii=False).encode()
        manifest = {"origin": "xyquant_server", "dataset": "original_attribution", "data_version": VERSION,
                    "catalog": "catalog.json", "expires_at": self.expiry if expiry is None else expiry,
                    "files": {name: {"bytes": len(raw), "sha256": sha(raw)} for name, raw in files.items()}}
        if unrelated_missing:
            manifest["files"]["market/missing.parquet"] = {"bytes": 5, "sha256": sha(b"other")}
        raw = json.dumps(manifest, ensure_ascii=False).encode()
        folder = self.project / "downloads/attribution-original" / VERSION
        folder.mkdir(parents=True, exist_ok=True)
        archive_path = folder / "attribution-data.zip"
        with ZipFile(archive_path, "w") as archive:
            for name, content in files.items():
                archive.writestr(name, content)
            archive.writestr("original-data.json", raw)
        job = {"id": "2" * 32, "status": "ready", "tool": "query_stock_data",
               "datasets": ["original_attribution"], "data_version": VERSION,
               "bytes": archive_path.stat().st_size, "sha256": sha(archive_path.read_bytes()),
               "manifest_sha256": sha(raw), "expires_at": manifest["expires_at"],
               "download": {"url": attribution_download.ORIGIN + "/factors/api/attribution-data/" + "2" * 32,
                            "ticket": "x" * 40, "expires_at": self.expiry}}
        if unrelated_missing or expiry is not None:
            root = folder / "data"
            for name, content in {**files, "original-data.json": raw}.items():
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
        else:
            result = mcp_transfer.transfer("holdings", self.project, job["id"], json.dumps(job).encode())
            self.assertEqual(result["status"], "ready", result)
        return job

    def test_current_downloaders_feed_complete_cli_report(self):
        factor, saved = self.factors()
        self.assertEqual(saved["status"], "ready", saved)
        holding = self.holdings()
        news = {"stock": STOCK, "status": "searched", "items": []}
        cmd = [sys.executable, "-B", str(SCRIPTS / "diagnosis.py"), "--stock", STOCK,
               "--project", str(self.project), "--as-of", "2025-02-03",
               "--factor-job", factor["id"], "--factor-sha256", factor["sha256"],
               "--factor-date", DAY.isoformat(), "--attribution-version", VERSION,
               "--manifest-sha256", holding["manifest_sha256"], "--news-stdin"]
        # Exercise normal interpreter shutdown, including Arrow's native cleanup.
        # A report written to stdout is insufficient if the process never exits.
        for _ in range(3):
            result = subprocess.run(cmd, input=json.dumps(news), capture_output=True, encoding="utf-8", timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        data = json.loads(result.stdout)
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["holding_decision"]["status"], "supports_holding")
        self.assertEqual(len(data["ranks"]), 4)
        self.assertTrue(all(row["rank"] == 1 and row["valid_count"] == 10 for row in data["ranks"].values()))
        self.assertEqual(data["holdings_summary"]["two_verified_snapshots"], 4)
        for row in data["holdings"].values():
            self.assertEqual(row["change"], "increased")
            self.assertAlmostEqual(row["current"]["weight"], 0.3)
            self.assertAlmostEqual(row["change_pp"], 10.0)
        self.assertEqual(data["news"]["status"], "no_verified_news")
        for token in (factor["download"]["ticket"], holding["download"]["ticket"]):
            self.assertNotIn(token, result.stdout + result.stderr)
            for path in self.project.rglob("*.json"):
                self.assertNotIn(token, path.read_text(encoding="utf-8"))

    def test_code_filtered_factor_export_cannot_be_ranked(self):
        selection = {"factors": list(diagnosis.FACTORS), "codes": [STOCK],
                     "start": DAY.isoformat(), "end": DAY.isoformat()}
        _, result = self.factors({"selection": selection})
        self.assertEqual(result["status"], "failed")
        self.assertFalse((self.project / "downloads").exists())

    def test_factor_scope_and_checksum_are_checked_before_reading(self):
        job, result = self.factors()
        self.assertEqual(result["status"], "ready")
        root = factor_download.job_folder(self.project, job["id"], False)
        (root / ".xyquant-diagnosis-scope.json").write_text("{}")
        with self.assertRaises(diagnosis.DataError):
            sources.factor_archive(self.project, job["id"], job["sha256"], DAY.isoformat())

    def test_holdings_cache_is_reusable_only_before_expiry(self):
        job = self.holdings()
        with patch.object(sources.time, "time", return_value=self.expiry - 1):
            cache = sources.AttributionCache(self.project, VERSION, job["manifest_sha256"])
            self.assertEqual(len(cache.strategy(diagnosis.STRATEGIES[0])[1]), 4)
        with patch.object(sources.time, "time", return_value=self.expiry):
            with self.assertRaisesRegex(diagnosis.DataError, "已过期"):
                sources.AttributionCache(self.project, VERSION, job["manifest_sha256"])

    def test_expired_job_reports_current_downloader_error(self):
        job = self.holdings()
        job["expires_at"] = time.time() - 1
        result = mcp_transfer.transfer("holdings", self.project, job["id"], json.dumps(job).encode())
        self.assertEqual(result["code"], "holdings_expired")

    def test_missing_unrelated_market_file_does_not_hide_verified_holdings(self):
        job = self.holdings(unrelated_missing=True)
        cache = sources.AttributionCache(self.project, VERSION, job["manifest_sha256"])
        result = sources.holding_changes(*cache.strategy(diagnosis.STRATEGIES[0]), STOCK, DAY)
        self.assertEqual(result["status"], "compared")

    def test_modified_weight_file_fails_its_manifest_check(self):
        job = self.holdings()
        cache = sources.AttributionCache(self.project, VERSION, job["manifest_sha256"])
        target = cache.root / cache.catalog["portfolios"][diagnosis.STRATEGIES[0]]["path"]
        target.write_bytes(b"corrupted")
        with self.assertRaises(diagnosis.DataError):
            cache.strategy(diagnosis.STRATEGIES[0])
        self.assertTrue(cache.strategy(diagnosis.STRATEGIES[1])[1])

    def test_incomplete_snapshot_cannot_mean_not_held(self):
        snapshots = [{"date": DAY, "valid": True, "total": 100.0}]
        rows = [{"date": DAY, "stock_code": "000001.SZ", "weight": 50.0}]
        result = sources.holding_changes(snapshots, rows, STOCK, DAY)
        self.assertEqual(result["status"], "no_verified_snapshot")
        self.assertIsNone(result["current"])

    def test_incomplete_positive_record_remains_raw(self):
        snapshots = [{"date": DAY, "valid": True, "total": 100.0}]
        rows = [{"date": DAY, "stock_code": STOCK, "weight": 50.0}]
        result = sources.holding_changes(snapshots, rows, STOCK, DAY)
        self.assertEqual(result["status"], "reported_only")
        self.assertEqual(result["reported_observations"][0]["reported_raw_weight"], 50.0)
        self.assertIsNone(result["change_pp"])

    def test_historical_cutoff_selects_only_past_holdings(self):
        job = self.holdings()
        cache = sources.AttributionCache(self.project, VERSION, job["manifest_sha256"])
        result = sources.holding_changes(*cache.strategy(diagnosis.STRATEGIES[0]), STOCK, PREVIOUS)
        self.assertEqual(result["status"], "no_comparable_previous")
        self.assertEqual(result["current"]["date"], PREVIOUS.isoformat())
        self.assertIsNone(result["change_pp"])

    def test_tied_ranking_uses_midpoint(self):
        rows = [{"date": DAY, "stock_cd": code, "alpha": value} for code, value in
                [("600001.SH", 3.0), (STOCK, 2.0), ("600002.SH", 2.0), ("600003.SH", 1.0)]]
        result = diagnosis.rank_panel(pa.Table.from_pylist(rows), STOCK, DAY, "descending")
        self.assertEqual((result["rank"], result["tie_count"], result["top_position_pct"], result["band"]),
                         (2, 2, 50.0, "middle"))

    def test_thirty_percent_boundary_is_inclusive(self):
        for target, expected in ((3, "top"), (7, "bottom")):
            rows = [{"date": DAY, "stock_cd": STOCK if i == target else f"600{i:03}.SH", "alpha": float(i)}
                    for i in range(11)]
            result = diagnosis.rank_panel(pa.Table.from_pylist(rows), STOCK, DAY, "ascending")
            self.assertEqual(result["band"], expected)

    def test_opposite_tail_blocks_positive_verdict_and_stale_data_blocks_direction(self):
        ranks = {name: {"band": "top" if i < 3 else "bottom"} for i, name in enumerate(diagnosis.FACTORS)}
        self.assertEqual(diagnosis.decision(ranks, DAY, DAY)["status"], "review")
        for row in ranks.values():
            row["band"] = "top"
        result = diagnosis.decision(ranks, DAY, date(2025, 2, 8))
        self.assertEqual(result["reason"], "stale_factor_date")

    def test_news_filters_future_dates_and_deduplicates_events(self):
        def item(published, source_type, event):
            return {"published": published, "source_type": source_type, "event_key": event,
                    "title": "Synthetic fixture", "source": "Test publisher", "summary": "Test only",
                    "url": f"https://example.com/{source_type}/{event}", "material_risk": True}
        data = {"stock": STOCK, "status": "searched", "items": [
            item("2025-01-30", "official", "event-one"),
            item("2025-01-31", "financial_media", "event-one"),
            item("2025-02-01", "official", "future-event"),
            item("2025-01-20", "official", "old-event")]}
        result = news_context.recent_news(data, STOCK, DAY)
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["source_type"], "official")
        self.assertTrue(result["requires_risk_review"])
        self.assertFalse(result["used_in_factor_decision"])


if __name__ == "__main__":
    unittest.main()
