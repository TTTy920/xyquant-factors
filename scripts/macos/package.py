"""Assemble the macOS plugin directory and its stand-alone ZIP for publication."""
import argparse
import json
import runpy
from pathlib import Path
from shutil import copyfile
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile, ZipInfo

from make_runtime import ROOT, build, digest

PLUGIN = ROOT / "plugins" / "xyquant-factors-macos"
SHARED = ROOT / "plugins" / "xyquant-factors" / "skills"
SHARED_FILES = (
    "factor-download/SKILL.md",
    "strategy-attribution/SKILL.md",
    "barra-attribution/SKILL.md",
    "barra-attribution/agents/openai.yaml",
    "barra-attribution/scripts/attribute.py",
)
MAC_FILES = ("README.md", "THIRD_PARTY_NOTICES.md", "plugin.json", "mcp.json", "bin/factor-bridge")


def write_json(relative, value):
    path = PLUGIN / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return relative


def package(output_dir, cache):
    manifest = json.loads((PLUGIN / "plugin.json").read_text(encoding="utf-8"))
    servers = json.loads((PLUGIN / "mcp.json").read_text(encoding="utf-8"))["mcpServers"]
    files = list(MAC_FILES)
    overlay = {key: manifest[key] for key in ("name", "version", "description", "author", "license")}
    overlay.update(skills="./skills/", mcpServers="./.mcp.json", interface=manifest["extensions"]["com.openai"]["interface"])
    files.append(write_json(".codex-plugin/plugin.json", overlay))
    legacy = {name: {"command": server["command"],
                     "args": [s.replace("${PLUGIN_ROOT}/", "./") for s in server["args"]],
                     "cwd": ".", "env_vars": ["HOME", "TMPDIR", "CODEX_HOME", "LANG", "LC_CTYPE"],
                     "startup_timeout_sec": 120, "tool_timeout_sec": 60} for name, server in servers.items()}
    files.append(write_json(".mcp.json", {"mcpServers": legacy}))
    compatibility = runpy.run_path(str(ROOT / "scripts/workbuddy/package.py"))["render"](ROOT)
    for path, value in compatibility.items():
        if path.parts[:2] == ("plugins", PLUGIN.name):
            files.append(write_json(Path(*path.parts[2:]).as_posix(), value))
    for relative in SHARED_FILES:
        target = PLUGIN / "skills" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        copyfile(SHARED / relative, target)
        files.append("skills/" + relative)
    copyfile(ROOT / "LICENSE", PLUGIN / "LICENSE")
    files.append("LICENSE")
    files.extend(build(PLUGIN / "runtime", cache))
    output_dir.mkdir(parents=True, exist_ok=True)
    archive = output_dir / (manifest["name"] + "-" + manifest["version"] + ".zip")
    with ZipFile(archive, "w") as bundle:
        for relative in sorted(files):
            info = ZipInfo(relative)
            info.create_system = 3
            info.external_attr = (0o100755 if relative == "bin/factor-bridge" else 0o100644) << 16
            compression = ZIP_STORED if relative.endswith((".tgz", ".pyz")) else ZIP_DEFLATED
            bundle.writestr(info, (PLUGIN / relative).read_bytes(), compress_type=compression)
    archive.with_suffix(".zip.sha256").write_text(digest(archive) + "  " + archive.name + "\n", encoding="ascii")
    return archive


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / ".work" / "macos")
    parser.add_argument("--cache", type=Path, default=ROOT / ".work" / "macos-inputs")
    args = parser.parse_args()
    print(package(args.out, args.cache))
