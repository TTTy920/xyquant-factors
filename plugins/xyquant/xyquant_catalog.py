"""Versioned local dataset catalog, shared by maintenance and installed Skills."""
from datetime import date
from hashlib import sha256
import json
from pathlib import PurePosixPath
import re

from xyquant_data import DataPathError, check_kind, data_path, data_root


ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,79}")
HASH = re.compile(r"[0-9a-f]{64}")
WINDOWS_DEVICE = re.compile(r"(?:con|prn|aux|nul|com[1-9¹²³]|lpt[1-9¹²³])(?: *\.|$)", re.IGNORECASE)
MAX_JSON_BYTES = 4 * 1024**2
STATES = {"ready", "reference_only", "retired"}
MARKET_CONTRACTS = {
    "price": ("adjusted-close-v1", ("date", "stock_code", "adj_close")),
    "calendar": ("trading-calendar-v1", ("date",)),
    "listed": ("listed-history-v1", ("date", "stock_code", "listed")),
}


class CatalogError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise CatalogError(message)


def fields(value, names, label):
    require(isinstance(value, dict) and set(value) == set(names.split()), f"{label} 字段不符合 v1 契约")


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "JSON 存在重复字段")
        result[key] = value
    return result


def load_json(path, expected=None):
    with path.open("rb") as stream:
        raw = stream.read(MAX_JSON_BYTES + 1)
    require(len(raw) <= MAX_JSON_BYTES, "JSON 超过 4 MiB")
    if expected is not None:
        require(sha256(raw).hexdigest() == expected, "清单 SHA-256 不一致")
    try:
        return json.loads(raw, object_pairs_hook=unique_object)
    except (json.JSONDecodeError, UnicodeError, RecursionError) as exc:
        raise CatalogError("不是有效的 JSON") from exc


def relative(value):
    require(isinstance(value, str) and bool(value), "路径必须是非空的相对路径")
    path = PurePosixPath(value)
    require(path.parts and not path.is_absolute() and path.as_posix() == value
            and all(part != ".." and not part.endswith((".", " ")) and not WINDOWS_DEVICE.match(part)
                    and not any(c in '\\:<>"|?*' or ord(c) < 32 for c in part)
                    for part in path.parts), "路径须使用 / 分隔，不得越界或包含非法字符")
    return path.parts


def ident(value, label):
    require(isinstance(value, str) and ID.fullmatch(value) and not WINDOWS_DEVICE.match(value),
            f"{label} 不是有效的小写标识")
    return value


def checksum(value):
    require(isinstance(value, str) and HASH.fullmatch(value), "缺少有效的 SHA-256")
    return value


def managed_file(project, parts):
    return data_path(project, parts[:-1], data_root(project).joinpath(*parts), directory=False)


def calendar_date(value):
    require(isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value), "日期须为 YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise CatalogError("日期值无效") from exc


def measure(path):
    digest, size = sha256(), 0
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024**2), b""):
            digest.update(block)
            size += len(block)
    return {"bytes": size, "sha256": digest.hexdigest()}


def verify_files(project, parent, entries):
    seen = set()
    for name, expected, size in entries:
        parts = relative(name)
        expected = checksum(expected)
        key = tuple(part.casefold() for part in parts)
        require(key not in seen, "清单中有重复的文件路径")
        seen.add(key)
        path = managed_file(project, parent + parts)
        require(path.is_file(), f"缺少文件：{name}")
        actual = measure(path)
        require(size is None or size == actual["bytes"], f"文件大小不一致：{name}")
        require(actual["sha256"] == expected, f"文件 SHA-256 不一致：{name}")
    return len(seen)


def read_manifest(project, dataset, version, entry):
    parts = relative(entry["manifest"])
    kind = entry["manifest_kind"]
    if kind == "dataset-v1":
        require(parts == ("inputs", "datasets", dataset, version, "manifest.json"),
                "正式清单须位于 inputs/datasets/<dataset-id>/<version>/manifest.json")
    elif kind == "rankic-local-config-v1":
        require(len(parts) >= 4 and parts[:2] == ("inputs", "rankic") and parts[-1] == "rankic-config.json",
                "RankIC 兼容清单须位于 inputs/rankic/<快照>/rankic-config.json")
        require(entry["status"] != "ready", "旧版 RankIC 快照不得登记为 ready")
    else:
        raise CatalogError("未知清单类型")
    path = managed_file(project, parts)
    require(path.is_file(), "登记的清单不存在")
    return parts[:-1], load_json(path, checksum(entry["manifest_sha256"]))


def validate_manifest(manifest, dataset, version):
    fields(manifest, "schema_version dataset_id version source published_date coverage contract known_gaps files", "数据清单")
    require(type(manifest["schema_version"]) is int and manifest["schema_version"] == 1
            and (manifest["dataset_id"], manifest["version"]) == (dataset, version), "数据清单版本或身份不匹配")
    ident(manifest["contract"], "数据契约")
    require(isinstance(manifest["source"], str) and bool(manifest["source"].strip()), "来源不能为空")
    calendar_date(manifest["published_date"])
    coverage = manifest["coverage"]
    fields(coverage, "start end", "覆盖范围")
    require(calendar_date(coverage["start"]) <= calendar_date(coverage["end"]), "覆盖起止日期颠倒")
    gaps = manifest["known_gaps"]
    require(isinstance(gaps, list) and all(isinstance(gap, str) and gap.strip() for gap in gaps), "已知缺口须为文字列表")
    files = manifest["files"]
    require(isinstance(files, dict) and bool(files), "数据清单须列出文件")
    entries, seen = [], set()
    for name, info in files.items():
        key = tuple(part.casefold() for part in relative(name))
        require(key != ("manifest.json",) and key not in seen, "数据文件清单包含自身或重复路径")
        seen.add(key)
        fields(info, "bytes sha256", "文件证据")
        require(type(info["bytes"]) is int and info["bytes"] >= 0, "文件字节数无效")
        entries.append((name, checksum(info["sha256"]), info["bytes"]))
    return entries


def verify_version(project, dataset, version, entry):
    parent, manifest = read_manifest(project, dataset, version, entry)
    kind = entry["manifest_kind"]
    if kind == "rankic-local-config-v1":
        require(isinstance(manifest, dict) and isinstance(manifest.get("data"), dict)
                and isinstance(manifest.get("parameters"), dict), "RankIC 兼容清单缺少数据或参数")
        data, params = manifest["data"], manifest["parameters"]
        require(data.get("origin") == "local_reference" and data.get("snapshot_id") == version
                and params.get("pool") in ("all_a", "custom"), "RankIC 快照身份、来源或池不符")
        roles = ("factor", "price", "calendar", "listed") if params["pool"] == "all_a" else ("factor", "price", "calendar")
        entries = []
        for role in roles:
            spec = data.get(role)
            require(isinstance(spec, dict), f"RankIC 兼容清单缺少 {role}")
            entries.append((spec.get("path"), spec.get("sha256"), None))
    else:
        entries = validate_manifest(manifest, dataset, version)
    return verify_files(project, parent, entries)


def read_catalog(project, *, missing_ok=False):
    path = managed_file(project, ("catalog.json",))
    if missing_ok and not path.exists():
        return {"schema_version": 1, "datasets": []}
    require(path.is_file(), "缺少 xyquant-data/catalog.json；可复制 docs/xyquant-data-catalog.example.json 初始化")
    catalog = load_json(path)
    fields(catalog, "schema_version datasets", "目录索引")
    require(type(catalog["schema_version"]) is int and catalog["schema_version"] == 1
            and isinstance(catalog["datasets"], list), "目录索引版本或数据集列表无效")
    seen_datasets = set()
    for dataset in catalog["datasets"]:
        fields(dataset, "id recommended_version versions", "数据集条目")
        name = ident(dataset["id"], "数据集 ID")
        require(name not in seen_datasets, f"目录中重复登记数据集：{name}")
        seen_datasets.add(name)
        entries = dataset["versions"]
        require(isinstance(entries, list) and bool(entries), f"{name} 未登记版本")
        states = {}
        for entry in entries:
            fields(entry, "id status manifest_kind manifest manifest_sha256", "版本条目")
            version, state = ident(entry["id"], "版本 ID"), entry["status"]
            require(version not in states and isinstance(state, str) and state in STATES, f"{name} 的版本重复或状态无效")
            states[version] = state
        recommended = dataset["recommended_version"]
        require(recommended is None or (isinstance(recommended, str) and states.get(recommended) == "ready"),
                f"{name} 的推荐版本必须指向已登记且状态为 ready 的版本")
    return catalog


def find_version(catalog, dataset, version):
    group = next((row for row in catalog["datasets"] if row["id"] == dataset), None)
    entry = next((row for row in group["versions"] if row["id"] == version), None) if group else None
    return group, entry


def check(project):
    require(check_kind(data_root(project), directory=True), "找不到项目的 xyquant-data 目录")
    for parts in [("inputs", "incoming"), ("inputs", "holdings"), ("inputs", "rankic"), ("inputs", "datasets"),
                  ("mcp-cache",), ("results",), ("archive",), (".scratch",)]:
        data_path(project, parts)
    catalog = read_catalog(project)
    versions, errors, file_count = [], [], 0
    for dataset in catalog["datasets"]:
        name = dataset["id"]
        for entry in dataset["versions"]:
            version = entry["id"]
            try:
                count = verify_version(project, name, version, entry)
            except (CatalogError, DataPathError) as exc:
                errors.append({"dataset": name, "version": version, "message": str(exc)})
            except OSError:
                errors.append({"dataset": name, "version": version, "message": "无法读取清单或其数据文件"})
            else:
                file_count += count
                versions.append({"dataset": name, "version": version, "state": entry["status"], "files": count})
    return {"status": "error" if errors else "ok", "registered_datasets": len(catalog["datasets"]),
            "verified_files": file_count, "verified_versions": versions, "errors": errors}
