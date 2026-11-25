"""Run the RankIC Skill against verified historical jobs before selecting weights."""
import json
from pathlib import Path
import subprocess
import sys

from synthesis_core import SynthesisError, iso_date, stock_pool
from synthesis_source import download, read_local

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from xyquant_data import RANKIC_INPUTS, RANKIC_RESULTS, data_path
sys.path.pop(0)


RANKIC = Path(__file__).resolve().parents[2] / "factor-rankic/scripts/rankic.py"
MAX_CONFIG_BYTES = 2 * 1024**2


def load_config(project, path, score_date, pool):
    path = data_path(project, RANKIC_INPUTS, path, directory=False)
    try:
        config = json.loads(read_local(path, MAX_CONFIG_BYTES))
        params, data = config["parameters"], config["data"]
        if not isinstance(params, dict) or not isinstance(data, dict):
            raise ValueError
    except (OSError, ValueError, TypeError, KeyError, RecursionError, download.DownloadError):
        raise SynthesisError("无法读取训练配置；须使用项目内的 RankIC 本地配置") from None
    if data.get("origin") not in ("local_reference", "xyquant_server"):
        raise SynthesisError("自动定权需要已校验的复权价、交易日历及适用的历史在市数据")
    if not iso_date(params.get("start")) <= iso_date(params.get("end")) < score_date:
        raise SynthesisError("训练观察窗口必须结束于合成日之前")
    if params.get("pool") != ("custom" if pool else "all_a") or stock_pool(params.get("codes", ())) != pool:
        raise SynthesisError("RankIC 训练配置必须与本次合成使用同一股票池")
    if params.get("frequency") not in ("daily", "weekly", "monthly", "quarterly"):
        raise SynthesisError("RankIC 训练配置须明确日、周、月或季度频率")
    return path


def generate_reports(project, specs, pool, score_date, config, exports):
    if set(exports) != {spec.name for spec in specs}:
        raise SynthesisError("须为每个指定因子提供一个已校验的历史 MCP 导出任务")
    jobs = {}
    for name, ref in exports.items():
        jid, sep, digest = ref.partition(":")
        if not sep or not download.JOB.fullmatch(jid) or not download.HASH.fullmatch(digest):
            raise SynthesisError("历史任务须使用 --ic-export 因子ID=任务ID:SHA-256")
        jobs[name] = (jid, digest)
    if len({jid for jid, _ in jobs.values()}) != len(jobs):
        raise SynthesisError("每个因子必须使用独立的单因子历史导出任务")
    path = load_config(project, config, score_date, pool)
    reports = {}
    for spec in specs:
        jid, digest = jobs[spec.name]
        proc = subprocess.run(
            [sys.executable, "-B", str(RANKIC), "--project", str(project), "--config", str(path),
             "--factor", spec.name, "--factor-job", jid, "--factor-sha256", digest],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8", errors="replace")
        try:
            result = json.loads(proc.stdout)
            status = result.get("status")
            ok = (status in ("success", "partial_success") and proc.returncode == 0
                  or status == "no_result" and proc.returncode == 2)
            if ok:
                report = Path(result["output_dir"]) / "metadata.json"
                reports[spec.name] = data_path(project, RANKIC_RESULTS, report, directory=False)
                continue
        except (ValueError, AttributeError, KeyError, TypeError, RecursionError):
            pass
        raise SynthesisError(f"因子 {spec.name} 的 RankIC 测试未完成；请核对历史导出、训练配置及 RankIC 依赖")
    return reports
