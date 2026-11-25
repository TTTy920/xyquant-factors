import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys
import tempfile
import unittest
import numpy as np
import pandas as pd

SCRIPTS=Path(__file__).resolve().parents[1]/"scripts"
sys.path.insert(0,str(SCRIPTS))
import barra
import brinson
import attribute
import merge_legacy_data
from data_store import Store, DataError, FACTORS, schedule


def fixture(root):
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    dates=pd.bdate_range("2024-12-30","2025-02-07")
    stocks=[f"S{i:03}" for i in range(120)]
    rng=np.random.default_rng(765)
    x=rng.normal(size=(120,10))
    coef=np.arange(1,11)*.00001
    daily=.0002+x@coef
    def save(f,name):
        f.to_parquet(root/name,index=False)
        return name
    calendar=save(pd.DataFrame({"date":dates}),"calendar.parquet")
    n=len(dates)
    market=pd.DataFrame({"date":np.repeat(dates,120),"stock_code":stocks*n,
                         "return":np.tile(daily,n),"float_cap":np.tile(np.arange(1,121)*1e8,n),"eligible":1})
    save(market,"market.parquet")
    exposures=pd.DataFrame(np.tile(x,(n,1)),columns=FACTORS)
    exposures.insert(0,"stock_code",stocks*n);exposures.insert(0,"date",np.repeat(dates,120))
    save(exposures,"exposures.parquet")
    snapshots=[]
    for kind,weights in [("portfolio",[.6,.4]),("benchmark",[.5,.5])]:
        w=pd.DataFrame({"date":np.repeat(dates,2),"stock_code":["S000","S001"]*n,"weight":weights*n})
        save(w,kind+".parquet")
        snapshots.extend({"date":d,"kind":kind,"name":"P" if kind=="portfolio" else "B","valid":True,"total":1.,"cash":0.} for d in dates)
    save(pd.DataFrame(snapshots),"snapshots.parquet")
    industry=pd.DataFrame({"date":pd.to_datetime(["2024-12-31","2024-12-31","2025-01-31","2025-01-31"]),
                           "stock_code":["S000","S001"]*2,"industry":["旧行业","其他","新行业","其他"]})
    save(industry,"industry.parquet")
    cat={"schema_version":1,"calendar":calendar,"portfolios":{"P":{"path":"portfolio.parquet","frequencies":["daily","weekly","monthly"],"aliases":["组合"]}},
         "benchmarks":{"B":{"path":"benchmark.parquet","frequencies":["daily","weekly","monthly"],"aliases":["基准"]}},
         "snapshots":"snapshots.parquet","market":["market.parquet"],"exposures":["exposures.parquet"],"industry":"industry.parquet"}
    (root/"catalog.json").write_text(json.dumps(cat,ensure_ascii=False),encoding="utf-8")
    return dates,stocks,x,coef,daily


class CalculationTests(unittest.TestCase):
    def test_wls_recovers_known_coefficients(self):
        rng=np.random.default_rng(11);x=pd.DataFrame(rng.normal(size=(130,10)),columns=FACTORS)
        f=np.arange(10)*.001;y=pd.Series(.003+x.to_numpy()@f)
        actual,diag=barra.regress(x,y,pd.Series(np.arange(1,131)*1e8),pd.Series(1,index=x.index))
        np.testing.assert_allclose(actual,f,rtol=0,atol=1e-14)
        self.assertAlmostEqual(diag["intercept"],.003)
        self.assertEqual(diag["rank"],11)

    def test_wls_checks_rank_and_sample_drop(self):
        x=pd.DataFrame(1.,index=range(120),columns=FACTORS)
        with self.assertRaises(DataError):barra.regress(x,pd.Series(.01,index=x.index),pd.Series(1.,index=x.index),pd.Series(1,index=x.index))
        rng=np.random.default_rng(2);x[:]=rng.normal(size=(120,10));x.iloc[:20,0]=np.nan
        with self.assertRaises(DataError):barra.regress(x,pd.Series(.01,index=x.index),pd.Series(1.,index=x.index),pd.Series(1,index=x.index))

    def test_new_barra_sample_is_selected_without_eligible_flag(self):
        rng=np.random.default_rng(33)
        x=pd.DataFrame(rng.normal(size=(130,10)),columns=FACTORS)
        caps=pd.Series(1e8,index=x.index)
        y=.002+x@np.arange(10)*.001
        x.loc[0,FACTORS[0]]=np.nan
        caps.loc[1]=0
        y.loc[2]=np.nan
        coeff,diag=barra.regress(x,y,caps,None)
        self.assertEqual((diag["eligible_count"],diag["sample_count"],diag["dropped_count"]),(128,127,1))
        np.testing.assert_allclose(coeff,np.arange(10)*.001,atol=1e-14)

    def test_brinson_known_allocation_selection(self):
        wp=pd.Series({"A":.3,"B":.7});wb=pd.Series({"A":.2,"C":.8})
        g=pd.Series({"A":"科技","B":"其他","C":"其他"});r=pd.Series({"A":.1,"B":.06,"C":.04})
        f=brinson.attribute(wp,wb,g,r,.052)
        self.assertAlmostEqual(f.allocation.sum(),.006)
        self.assertAlmostEqual(f.selection_including_interaction.sum(),.014)
        self.assertAlmostEqual(f.total.sum(),.02)

    def test_off_benchmark_convention(self):
        f=brinson.attribute(pd.Series({"A":1.}),pd.Series({"B":1.}),pd.Series({"A":"新","B":"旧"}),pd.Series({"A":.2,"B":.1}),.1)
        self.assertAlmostEqual(f.total.sum(),.1)
        self.assertAlmostEqual(f.set_index("industry").loc["新","allocation"],0)
        self.assertAlmostEqual(f.set_index("industry").loc["旧","selection"],0)

    def test_linking_closes_with_volatile_multi_period_returns(self):
        periods=pd.DataFrame({"date":["1","2","3"],"portfolio_return":[.2,-.3,.1],"benchmark_return":[-.1,.2,.01]})
        active=periods.portfolio_return-periods.benchmark_return
        frame=pd.DataFrame({"A":active.values*.4,"B":active.values*.6},index=periods.date)
        values,rp,rb=attribute.link(frame,periods)
        self.assertAlmostEqual(values.sum(),rp-rb)
        self.assertAlmostEqual(rp,1.2*.7*1.1-1)

    def test_calendar_holiday_shortened_month_and_partial_week(self):
        d=pd.bdate_range("2024-12-20","2025-02-07")
        d=d[~((d>="2025-01-28")&(d<="2025-02-04"))]
        p=schedule(d,pd.Timestamp("2025-01-01"),pd.Timestamp("2025-01-27"),"monthly")
        self.assertEqual(p,[(pd.Timestamp("2024-12-31"),pd.Timestamp("2025-01-27"))])
        weeks=schedule(d,pd.Timestamp("2025-01-01"),pd.Timestamp("2025-01-24"),"weekly")
        self.assertEqual(weeks[0][1],pd.Timestamp("2025-01-10"))


class DataAndWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)/"data"
        self.dates,self.stocks,self.x,self.coef,self.daily=fixture(self.root)

    def tearDown(self):self.temp.cleanup()

    def store(self,**flags):
        s=Store(self.root);s.prepare("组合","基准",pd.Timestamp("2025-01-01"),pd.Timestamp("2025-01-31"),"monthly",**flags);return s

    def test_daily_returns_compound_and_never_fill_missing(self):
        s=self.store();t0,t1=s.periods[0]
        expected=(1+self.daily)**len(s.calendar[(s.calendar>t0)&(s.calendar<=t1)])-1
        np.testing.assert_allclose(s.period_return(t0,t1).reindex(self.stocks),expected,atol=1e-14)
        s.daily_returns.loc["2025-01-03","S000"]=np.nan
        self.assertTrue(pd.isna(s.period_return(t0,t1).loc["S000"]))

    def test_industry_forward_fill_no_lookahead(self):
        s=Store(self.root);s.prepare("P","B",pd.Timestamp("2025-01-06"),pd.Timestamp("2025-01-24"),"weekly",brinson=True)
        self.assertTrue((s.industry_aligned.S000=="旧行业").all())
        g=pd.read_parquet(self.root/"industry.parquet");g=g[g.date>"2025-01-01"];g.to_parquet(self.root/"industry.parquet",index=False)
        s=self.store(brinson=True)
        self.assertTrue(pd.isna(s.industry_aligned.loc["2024-12-31","S000"]))

    def test_sparse_weights_require_valid_snapshot(self):
        s=self.store();w,diag=s.weights("portfolio",pd.Timestamp("2024-12-31"));self.assertAlmostEqual(w.sum(),1)
        s.snapshots.loc[(s.snapshots.kind=="portfolio"),"valid"]=False
        with self.assertRaises(DataError):s.weights("portfolio",pd.Timestamp("2024-12-31"))

    def test_path_escape_and_source_change(self):
        s=Store(self.root)
        with self.assertRaises(DataError):s.path("../other.parquet")
        s.read("calendar.parquet")
        f=pd.read_parquet(self.root/"calendar.parquet");f=f.iloc[:-1];f.to_parquet(self.root/"calendar.parquet",index=False)
        with self.assertRaises(DataError):s.verify_unchanged()

    def test_invalid_frequency_and_negative_dates(self):
        with self.assertRaises(DataError):schedule(self.dates,pd.Timestamp("2025-02-01"),pd.Timestamp("2025-01-01"),"monthly")
        cat=json.loads((self.root/"catalog.json").read_text());cat["portfolios"]["P"]["frequencies"]=["monthly"]
        (self.root/"catalog.json").write_text(json.dumps(cat))
        with self.assertRaises(DataError):Store(self.root).resolve("portfolios","P","weekly")

    def cli(self,method="both"):
        cmd=[sys.executable,str(SCRIPTS/"attribute.py"),"run","--legacy-replay","--data-root",str(self.root),"--output-root",str(Path(self.temp.name)/"outputs"),
             "--method",method,"--portfolio","P","--benchmark","B","--start","2025-01-01","--end","2025-01-31","--frequency","monthly"]
        return subprocess.run(cmd,capture_output=True,encoding="utf-8",env=dict(os.environ,PYTHONUTF8="1"))

    def test_official_cli_only_uses_flat_market_and_will_not_read_legacy_market(self):
        project=Path(self.temp.name).resolve()
        shell=project/"xyquant-data"
        weights=shell/"inputs/holdings/preset"
        shutil.copytree(self.root,weights)
        datasets=shell/"inputs/datasets"
        setup={"trading-calendar":("trading-calendar-v1","calendar.parquet","calendar.parquet"),
               "industry-classification":("industry-monthly-v1","industry.parquet","monthly.parquet"),
               "attribution-market":("attribution-market-no-eligible-v1","market.parquet",None),
               "barra-exposures":("barra-exposures-v1","exposures.parquet",None)}
        for ds,(contract,raw,name) in setup.items():
            folder=datasets/ds
            folder.mkdir(parents=True)
            frame=pd.read_parquet(self.root/raw).drop(columns="eligible",errors="ignore")
            groups=[(name,frame)] if name else [(f"{y}.parquet",f) for y,f in frame.groupby(frame.date.dt.year)]
            files={}
            for filename,rows in groups:
                parquet=folder/filename
                rows.to_parquet(parquet,index=False)
                files[filename]={"bytes":parquet.stat().st_size,"sha256":sha256(parquet.read_bytes()).hexdigest()}
            manifest={"schema_version":1,"dataset_id":ds,"version":"test-v1","contract":contract,"source":"fixture",
                      "known_gaps":[],"published_date":"2025-02-10","files":files,
                      "coverage":{"start":frame.date.min().date().isoformat(),"end":frame.date.max().date().isoformat()}}
            (folder/"manifest.json").write_text(json.dumps(manifest),encoding="utf-8")
        cmd=[sys.executable,"-B",str(SCRIPTS/"entry.py"),"run","--project",str(project),"--data-root",str(weights),
             "--source","preset","--portfolio","P","--benchmark","B","--start","2025-01-01","--end","2025-01-31",
             "--frequency","monthly","--method","both"]
        result=subprocess.run(cmd,capture_output=True,encoding="utf-8",timeout=30)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        report=json.loads(result.stdout)
        self.assertEqual(set(report["successful_methods"]),{"barra","brinson"})
        m=json.loads((Path(report["output_dir"])/"metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(set(m["foundation_datasets"]),set(setup))
        outside={Path(p["path"]).name for p in m["source_files"] if not Path(p["path"]).is_relative_to(datasets)}
        self.assertEqual(outside,{"catalog.json","portfolio.parquet","benchmark.parquet","snapshots.parquet"})
        self.assertEqual(m["barra_sample_policy"],"full_market_valid_start_exposures_and_cap")
        (datasets/"attribution-market/2025.parquet").unlink()
        missing=subprocess.run(cmd,capture_output=True,encoding="utf-8",timeout=30)
        self.assertEqual(missing.returncode,2)
        self.assertIn("inputs/datasets/attribution-market",json.loads(missing.stderr)["message"])

    def test_cli_both_excel_and_consistent_reconciliation(self):
        p=self.cli();self.assertEqual(p.returncode,0,p.stderr);result=json.loads(p.stdout)
        self.assertEqual(result["status"],"success")
        out=Path(result["output_dir"]);meta=json.loads((out/"metadata.json").read_text(encoding="utf-8"))
        report=(out/"report.md").read_text(encoding="utf-8")
        self.assertIn(f"{meta['compound_return_difference']*100:+.3f}个百分点",report)
        self.assertIn("平均期初相对暴露",report)
        self.assertIn("行业内选股（含交互）",report)
        self.assertIn("不能据此认定长期选股能力",report)
        self.assertEqual(meta["successful_methods"],["barra","brinson"])
        for m in meta["linked"].values():self.assertLess(abs(m["gap"]),1e-12)
        with pd.ExcelFile(out/"归因结果.xlsx") as book:self.assertIn("Barra累计贡献",book.sheet_names);self.assertIn("Brinson累计贡献",book.sheet_names)

    def test_br_only_needs_no_exposure_file(self):
        (self.root/"exposures.parquet").unlink()
        p=self.cli("brinson");self.assertEqual(p.returncode,0,p.stderr)
        self.assertEqual(json.loads(p.stdout)["successful_methods"],["brinson"])

    def test_both_reports_partial_success_without_fake_barra(self):
        (self.root/"exposures.parquet").unlink()
        p=self.cli();self.assertEqual(p.returncode,0,p.stderr);r=json.loads(p.stdout)
        self.assertEqual(r["status"],"partial_success");self.assertIn("barra",r["method_errors"])
        report=(Path(r["output_dir"])/"report.md").read_text(encoding="utf-8")
        self.assertIn("部分方法未完成：barra",report)
        self.assertNotIn("## 3. Barra",report)
        with pd.ExcelFile(Path(r["output_dir"])/"归因结果.xlsx") as book:self.assertNotIn("Barra累计贡献",book.sheet_names)

    def test_barra_only_needs_no_industry_file(self):
        (self.root/"industry.parquet").unlink()
        p=self.cli("barra");self.assertEqual(p.returncode,0,p.stderr)

    def test_missing_common_return_fails_structured(self):
        p=self.root/"market.parquet";f=pd.read_parquet(p);f.loc[(f.date=="2025-01-03")&(f.stock_code=="S000"),"return"]=np.nan;f.to_parquet(p,index=False)
        r=self.cli();self.assertEqual(r.returncode,2);e=json.loads(r.stderr)
        self.assertIn("S000",e["details"]["stocks"])


class MigrationTests(unittest.TestCase):
    def legacy(self,root):
        ba=root/"barra";br=root/"brinson"
        for folder in [ba/"benchmarks",ba/"portfolios",ba/"market",ba/"exposures",br/"benchmarks",br/"portfolios",br/"returns",br/"industry"]:
            folder.mkdir(parents=True)
        dates=pd.to_datetime(["2024-12-31","2025-01-03"])
        f=pd.DataFrame([[.12345678901234566,.8765432109876543]]*2,index=dates,columns=["000001.SZ","600000.SH"])
        for name in ["沪深300","中证500","中证1000","中证2000"]:
            f.to_csv(ba/"benchmarks"/(name+".csv"));f.to_csv(br/"benchmarks"/(name+"_daily.csv"))
        for name in ["300增强","xgb500增强","xgb1000增强","xgb2000增强"]:
            f.to_csv(ba/"portfolios"/(name+".csv"));f.to_csv(br/"portfolios"/("300增强持仓.csv" if name=="300增强" else name+".csv"))
        f.to_csv(ba/"market/returns_daily.csv");f.to_csv(br/"returns/returns_daily.csv")
        f.to_csv(ba/"market/float_cap.csv");(f*0+1).to_csv(ba/"market/eligible.csv")
        for name in FACTORS:f.to_csv(ba/"exposures"/(name+".csv"))
        pd.DataFrame({"date":dates}).to_csv(ba/"trading_calendar.csv",index=False)
        pd.DataFrame([["银行","银行"]]*2,index=dates,columns=f.columns).to_csv(br/"industry/中信行业_monthly.csv")
        return ba,br,f

    def test_lossless_precision_dedup_and_input_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);ba,br,f=self.legacy(root)
            before=(ba/"market/returns_daily.csv").read_bytes()
            report=merge_legacy_data.migrate(ba,br,root/"new")
            self.assertTrue(report["lossless_roundtrip"])
            parts=[pd.read_parquet(root/"new"/p["path"]) for p in report["files"] if p["path"].startswith("market/")]
            result=pd.concat(parts).pivot(index="date",columns="stock_code",values="return").reindex(columns=f.columns)
            self.assertTrue(np.array_equal(result.to_numpy(),f.to_numpy()))
            self.assertEqual(before,(ba/"market/returns_daily.csv").read_bytes())
            self.assertEqual(len(list((root/"new").rglob("*.csv"))),0)

    def test_conflicting_sources_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);ba,br,f=self.legacy(root);f.iloc[0,0]=.2
            f.to_csv(br/"benchmarks/沪深300_daily.csv")
            with self.assertRaises(DataError):merge_legacy_data.migrate(ba,br,root/"new")


if __name__=="__main__":unittest.main()
