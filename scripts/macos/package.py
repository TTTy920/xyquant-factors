"""Assemble the macOS source tree and package only its public plugin files."""

import argparse
import json
from pathlib import Path
from shutil import copyfile
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo


ROOT = Path(__file__).resolve().parents[2]
PLUGIN = ROOT / "plugins" / "xyquant-factors-macos"
SHARED = ROOT / "plugins" / "xyquant-factors" / "skills"
# Each shared Skill has one editable source. List the files meant for release.
SHARED_FILES = (
    "barra-attribution/SKILL.md",
    "barra-attribution/agents/openai.yaml",
    "barra-attribution/scripts/attribute.py",
)
MAC_FILES = ("README.md", "plugin.json", "mcp.json", "skills/factor-download/SKILL.md")
TOOLS = ("list_factors", "get_factor_info", "preview_factor", "create_export", "get_export", "list_exports")


def write_json(relative, value):
    target = PLUGIN / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return relative


def package(output_dir):
    manifest = json.loads((PLUGIN / "plugin.json").read_text(encoding="utf-8"))
    connections = json.loads((PLUGIN / "mcp.json").read_text(encoding="utf-8"))["mcpServers"]
    files = list(MAC_FILES)
    # Some Codex clients read the earlier filenames. Render both from one source.
    overlay = {key: manifest[key] for key in ("name", "version", "description", "author", "license")}
    overlay.update(skills="./skills/", mcpServers="./.mcp.json",
                   interface=manifest["extensions"]["com.openai"]["interface"])
    files.append(write_json(".codex-plugin/plugin.json", overlay))
    legacy = {name: {"url": server["url"], "enabled_tools": list(TOOLS),
                     "scopes": server["extensions"]["com.openai"]["auth"]["defaultScopes"]}
              for name, server in connections.items()}
    files.append(write_json(".mcp.json", {"mcpServers": legacy}))

    for relative in SHARED_FILES:
        target = PLUGIN / "skills" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        copyfile(SHARED / relative, target)
        files.append("skills/" + relative)
    copyfile(ROOT / "LICENSE", PLUGIN / "LICENSE")
    files.append("LICENSE")

    output_dir.mkdir(parents=True, exist_ok=True)
    archive = output_dir / (manifest["name"] + "-" + manifest["version"] + ".zip")
    with ZipFile(archive, "w") as bundle:
        for relative in sorted(files):
            info = ZipInfo(relative)
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            bundle.writestr(info, (PLUGIN / relative).read_bytes(), compress_type=ZIP_DEFLATED)
    return archive


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / ".work" / "macos")
    print(package(parser.parse_args().out))
