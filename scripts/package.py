"""Build the portable plugin ZIP from its tracked source files."""
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "xyquant"


def build(out):
    manifest = json.loads((PLUGIN / "plugin.json").read_text(encoding="utf-8"))
    files = subprocess.check_output(["git", "ls-files", "-z", "--", "plugins/xyquant"], cwd=ROOT)
    sources = sorted(ROOT / name.decode("utf-8") for name in files.split(b"\0") if name)
    if PLUGIN / "plugin.json" not in sources:
        raise RuntimeError("No tracked plugin manifest")
    out.mkdir(parents=True, exist_ok=True)
    target = out / f"{manifest['name']}-{manifest['version']}.zip"
    with zipfile.ZipFile(target, "w") as archive:
        for path in sources:
            if path.is_symlink() or not path.is_file():
                raise ValueError(f"Expected a regular file: {path.relative_to(ROOT)}")
            name = path.relative_to(PLUGIN.parent).as_posix()
            record = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            record.create_system = 3
            record.external_attr = 0o100644 << 16
            archive.writestr(record, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED)
    checksum = hashlib.sha256(target.read_bytes()).hexdigest()
    target.with_suffix(".zip.sha256").write_bytes(f"{checksum}  {target.name}\n".encode("ascii"))
    return target


if __name__ == "__main__":
    print(build(ROOT / "dist"))
