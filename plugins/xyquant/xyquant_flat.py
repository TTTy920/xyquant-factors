"""Strict resolver for non-holdings, non-factor inputs in inputs/datasets."""
import re

from xyquant_catalog import (calendar_date, ident, load_json, managed_file, measure,
                             require, validate_manifest)


BASE = ("inputs", "datasets")
SPECS = {
    "adjusted-prices": ("adjusted-close-v1", "prices.parquet"),
    "trading-calendar": ("trading-calendar-v1", "calendar.parquet"),
    "listed-history": ("listed-history-v1", "listed.parquet"),
    "attribution-market": ("attribution-market-no-eligible-v1", None),
    "barra-exposures": ("barra-exposures-v1", None),
    "industry-classification": ("industry-monthly-v1", "monthly.parquet"),
}
RANKIC_ROLES = {"price": "adjusted-prices", "calendar": "trading-calendar", "listed": "listed-history"}


class Dataset:
    def __init__(self, project, name):
        self.project, self.name = project, name
        contract, single = SPECS[name]
        self.parts = (*BASE, name)
        path = managed_file(project, (*self.parts, "manifest.json"))
        require(path.is_file(), f"缺少 inputs/datasets/{name}/manifest.json；不回退旧源")
        digest = measure(path)["sha256"]
        manifest = load_json(path, digest)
        require(isinstance(manifest, dict), f"{name} 清单不是对象")
        version = ident(manifest.get("version"), "数据版本")
        validate_manifest(manifest, name, version)
        require(manifest["contract"] == contract, f"inputs/datasets/{name} 数据契约不符")
        self.files = manifest["files"]
        require(set(self.files) == {single} if single else all(re.fullmatch(r"[0-9]{4}\.parquet", n) for n in self.files),
                f"inputs/datasets/{name} 文件集合不符")
        self.single, self.version, self.digest = single, version, digest
        self.start, self.end = manifest["coverage"]["start"], manifest["coverage"]["end"]
        self.gaps = manifest["known_gaps"]

    def file(self, name=None, *, verify=False):
        name = name if name is not None else self.single
        require(name in self.files, f"inputs/datasets/{self.name} 缺少所需文件")
        path = managed_file(self.project, (*self.parts, name))
        sig = self.files[name]
        require(path.is_file() and path.stat().st_size == sig["bytes"],
                f"inputs/datasets/{self.name}/{name} 缺失或大小不符")
        if verify:
            require(measure(path) == sig, f"inputs/datasets/{self.name}/{name} SHA-256 不符")
        return path, sig

    def years(self, start, end):
        require(self.start <= start <= end <= self.end, f"inputs/datasets/{self.name} 未覆盖请求区间")
        return [f"{year:04d}.parquet" for year in range(calendar_date(start).year, calendar_date(end).year + 1)]

    def pin(self):
        result = {"dataset": self.name, "version": self.version, "manifest_sha256": self.digest,
                  "known_gaps": self.gaps}
        if self.single:
            result["file_sha256"] = self.files[self.single]["sha256"]
        return result


def rankic_datasets(project, all_a):
    return {role: Dataset(project, name) for role, name in RANKIC_ROLES.items() if all_a or role != "listed"}
