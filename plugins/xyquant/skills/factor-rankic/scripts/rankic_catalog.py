"""Bind RankIC market inputs to the active flat datasets, without fallback."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from xyquant_catalog import require
from xyquant_flat import rankic_datasets
sys.path.pop(0)


def resolve(project, parameters, data):
    require(isinstance(data, dict), "RankIC 数据配置必须为 JSON 对象")
    require(data.get("origin") == "local_reference",
            "server RankIC data contract is not verified; market inputs must come from local inputs/datasets")
    datasets = rankic_datasets(project, parameters.pool == "all_a")
    resolved = {k: v for k, v in data.items() if k not in ("price", "calendar", "listed")}
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
