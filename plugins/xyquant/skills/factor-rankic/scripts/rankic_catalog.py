"""Bind RankIC market inputs to the active flat datasets, without fallback."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from xyquant_catalog import require
from xyquant_flat import rankic_datasets
from xyquant_dataset_download import verify_source
sys.path.pop(0)


def resolve(project, parameters, data):
    require(isinstance(data, dict), "RankIC 数据配置必须为 JSON 对象")
    require(data.get("origin") in ("local_reference", "xyquant_server"), "RankIC 基础数据来源无效")
    datasets = rankic_datasets(project, parameters.pool == "all_a")
    resolved = {k: v for k, v in data.items() if k not in ("price", "calendar", "listed")}
    if data["origin"] == "xyquant_server":
        bundle = verify_source(project, "adjusted-prices")
        require(bundle["bundle_group"] == "rankic", "RankIC 价格必须来自已授权的 RankIC 数据包")
        for role, dataset in datasets.items():
            verify_source(project, dataset.name)
            require(dataset.digest == bundle["dataset_manifests"].get(dataset.name), "RankIC 基础数据版本不一致")
        resolved["authorized_codes"] = bundle["scope"]["codes"]
    pinned, paths = {}, {}
    for role, dataset in datasets.items():
        require(dataset.start <= parameters.start <= parameters.end <= dataset.end,
                f"inputs/datasets/{dataset.name} 未覆盖完整观察窗口")
        path, sig = dataset.file()
        source = {"path": str(path), "format": "parquet", "sha256": sig["sha256"]}
        if role == "price":
            source.update(price_through=dataset.end, adjustment="adjusted_close")
        elif role == "calendar":
            source.update(complete_from=dataset.start, complete_through=dataset.end)
        resolved[role] = source
        pinned[role] = dataset.pin()
        paths[(role, path)] = dataset.parts + (dataset.single,)
    resolved["snapshot_id"] = "flat-" + "-".join(ds.digest[:16] for ds in datasets.values())
    resolved["adjustment_id"] = "flat-price-" + datasets["price"].digest
    return resolved, pinned, paths
