"""Generate WorkBuddy-compatible metadata around the existing plugin payloads."""
import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HOST_ROOT = "${CLAUDE_PLUGIN_ROOT}"
DESCRIPTIONS = {
    "xyquant-factors": "XYQuant 因子与研究资料 · Windows x64：账号授权、因子查询与完整数据下载。",
    "xyquant-factors-macos": "XYQuant 因子与研究资料 · macOS：因子下载、本地策略选股与 Barra 归因。",
}


def render(root=ROOT):
    root = Path(root).resolve()
    catalog = json.loads((root / ".agents/plugins/marketplace.json").read_text(encoding="utf-8"))
    outputs = {}
    entries = []
    for entry in catalog["plugins"]:
        name = entry["name"]
        source = entry["source"]["path"]
        plugin = (root / source).resolve()
        if not source.startswith("./") or root not in plugin.parents:
            raise ValueError("Plugin source must stay inside the marketplace root.")
        native = json.loads((plugin / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))
        if name != native["name"] or name not in DESCRIPTIONS:
            raise ValueError("Unknown or mismatched plugin identity: " + name)
        manifest = {key: native[key] for key in ("name", "version", "author", "license")}
        manifest.update(description=DESCRIPTIONS[name],
                        repository="https://github.com/TTTy920/xyquant-factors",
                        skills="./skills/", mcpServers="./.mcp.workbuddy.json")
        if name == "xyquant-factors-macos":
            server = dict(command="/bin/sh", args=[HOST_ROOT + "/bin/factor-bridge", "--stdio"])
        else:
            server = dict(command=HOST_ROOT + "/bin/FactorBridge.exe", args=["--stdio"])
        server["cwd"] = HOST_ROOT
        relative = plugin.relative_to(root)
        for directory in (".codebuddy-plugin", ".claude-plugin"):
            outputs[relative / directory / "plugin.json"] = manifest
        outputs[relative / ".mcp.workbuddy.json"] = {"mcpServers": {"xyquant_factors": server}}
        entries.append(dict(name=name, source=source, version=native["version"],
                            description=DESCRIPTIONS[name], category="productivity"))
    marketplace = dict(name=catalog["name"], owner={"name": "XYQuant"}, plugins=entries)
    for directory in (".codebuddy-plugin", ".claude-plugin"):
        outputs[Path(directory) / "marketplace.json"] = marketplace
    remote_entries = [dict(entry, source={
        "source": "git-subdir", "url": "https://github.com/TTTy920/xyquant-factors.git",
        "ref": "main", "path": entry["source"],
    }) for entry in entries]
    outputs[Path("docs/workbuddy-marketplace.json")] = dict(marketplace, plugins=remote_entries)
    return outputs


def check(root=ROOT):
    root = Path(root)
    outputs = render(root)
    for relative, expected in outputs.items():
        path = root / relative
        if not path.is_file() or json.loads(path.read_text(encoding="utf-8")) != expected:
            raise ValueError("Missing or stale WorkBuddy metadata: " + relative.as_posix())
    catalog = outputs[Path(".codebuddy-plugin/marketplace.json")]
    for entry in catalog["plugins"]:
        plugin = root / entry["source"]
        required = [plugin / "skills/factor-download/SKILL.md"]
        if entry["name"].endswith("-macos"):
            required += [plugin / "bin/factor-bridge", plugin / "runtime/bridge.pyz"]
            required += [plugin / "runtime" / (name + ".sha256") for name in ("arm64", "x86_64")]
            required += [plugin / "runtime" / (kind + "-" + arch + ".tgz")
                         for kind in ("python", "packages") for arch in ("arm64", "x86_64")]
        else:
            required.append(plugin / "bin/FactorBridge.exe")
        for path in required:
            if not path.is_file() or path.stat().st_size == 0:
                raise ValueError("Incomplete plugin payload: " + path.relative_to(root).as_posix())
    return {"plugins": len(catalog["plugins"]), "metadata_files": len(outputs)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Validate without changing files")
    args = parser.parse_args()
    if not args.check:
        for relative, value in render().items():
            path = ROOT / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(check(), ensure_ascii=False))


if __name__ == "__main__":
    main()
