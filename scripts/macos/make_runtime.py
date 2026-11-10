"""Build both native Mac runtimes from verified public inputs; never execute the client."""
import concurrent.futures
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tarfile
import tempfile
import time
from urllib.request import Request, urlopen
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SITE = "lib/python3.12/site-packages/"
CLIENT_MODULES = (
    "auth_async", "auth_client", "configuration", "diagnostics", "downloads", "factor_bridge",
    "login_tasks", "remote", "research_arguments", "runtime",
)


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def acquire(cache, name, spec):
    target = cache / name
    if target.is_file() and digest(target) == spec["sha256"]:
        return
    for attempt in range(3):
        try:
            with tempfile.NamedTemporaryFile(dir=cache, delete=False) as out:
                tmp = Path(out.name)
                try:
                    with urlopen(Request(spec["url"], headers={"User-Agent": "XYQuant-Mac-Packager"}), timeout=90) as src:
                        shutil.copyfileobj(src, out)
                    out.flush()
                    if digest(tmp) != spec["sha256"]:
                        raise ValueError("Upstream SHA-256 mismatch: " + name)
                    os.replace(tmp, target)
                finally:
                    tmp.unlink(missing_ok=True)
            return
        except OSError:
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)


def safe_path(name):
    p = PurePosixPath(name)
    if not p.parts or p.is_absolute() or ".." in p.parts or "\\" in name:
        raise ValueError("Invalid member: " + name)
    return p


def put(root, name, stream):
    path = root / safe_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as output:
        shutil.copyfileobj(stream, output)


def wheels(root, cache, names):
    for name in names:
        with ZipFile(cache / name) as wheel:
            for member in wheel.infolist():
                if member.is_dir():
                    continue
                parts = safe_path(member.filename).parts
                if parts[0].endswith(".data"):
                    if parts[1] not in ("purelib", "platlib"):
                        raise ValueError("New wheel layout requires review: " + member.filename)
                    parts = parts[2:]
                with wheel.open(member) as stream:
                    put(root, SITE + "/".join(parts), stream)


def conda(root, archive, package):
    # The Intel cryptography extension resolves @rpath/lib{ssl,crypto}.3.dylib
    # via @loader_path/../../../../../, which is the embedded Python's lib/.
    with ZipFile(archive) as outer:
        for filename in sorted(outer.namelist()):
            if not filename.endswith(".tar.zst"):
                continue
            with tempfile.TemporaryFile() as compressed, tempfile.TemporaryFile() as plain:
                with outer.open(filename) as src:
                    shutil.copyfileobj(src, compressed)
                compressed.seek(0)
                subprocess.run(["zstd", "-qdc"], stdin=compressed, stdout=plain, check=True)
                plain.seek(0)
                with tarfile.open(fileobj=plain, mode="r:") as inner:
                    for member in inner:
                        name = member.name.removeprefix("./")
                        if not member.isfile():
                            continue
                        if name.startswith("info/licenses/"):
                            dest = "share/licenses/" + package + "/" + name[len("info/licenses/"):]
                        elif package == "cryptography" and name.startswith(SITE + "cryptography") and not name.endswith(".pyc"):
                            dest = name
                        elif package == "openssl" and name in ("lib/libcrypto.3.dylib", "lib/libssl.3.dylib"):
                            dest = name
                        else:
                            continue
                        with inner.extractfile(member) as src:
                            put(root, dest, src)


def dependency_archive(folder, dest):
    with dest.open("wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0, compresslevel=6) as zipped:
        with tarfile.open(fileobj=zipped, mode="w|", format=tarfile.PAX_FORMAT) as tar:
            for p in sorted(folder.rglob("*")):
                if p.is_file():
                    item = tarfile.TarInfo(p.relative_to(folder).as_posix())
                    item.size = p.stat().st_size
                    item.mode = 0o755 if p.suffix in {".so", ".dylib"} else 0o644
                    with p.open("rb") as src:
                        tar.addfile(item, src)


def bridge(dest):
    with tempfile.TemporaryDirectory() as tmp:
        stage = Path(tmp)
        for module in CLIENT_MODULES:
            name = module + ".py"
            (stage / name).write_bytes((ROOT / "client" / name).read_text(encoding="utf-8").encode("utf-8"))
        subprocess.run(["git", "apply", "--no-index", "--whitespace=nowarn", "-p1", str(HERE / "client.patch")],
                               cwd=stage, check=True)
        shutil.copyfile(HERE / "macos_platform.py", stage / "macos_platform.py")
        shutil.copyfile(HERE / "bridge_main.py", stage / "__main__.py")
        with ZipFile(dest, "w") as bundle:
            for src in sorted(stage.glob("*.py")):
                info = ZipInfo(src.name)
                info.create_system = 3
                info.external_attr = 0o100644 << 16
                bundle.writestr(info, src.read_bytes(), compress_type=ZIP_DEFLATED)


def build(directory, cache):
    lock = json.loads((HERE / "runtime-lock.json").read_text(encoding="utf-8"))
    cache.mkdir(parents=True, exist_ok=True)
    directory.mkdir(parents=True, exist_ok=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(lambda name: acquire(cache, name, lock["artifacts"][name]), lock["artifacts"]))
    bridge(directory / "bridge.pyz")
    files = {"bridge.pyz"}
    for arch, spec in lock["architectures"].items():
        python, packages, sums = "python-" + arch + ".tgz", "packages-" + arch + ".tgz", arch + ".sha256"
        shutil.copyfile(cache / spec["python"], directory / python)
        with tempfile.TemporaryDirectory() as tmp:
            stage = Path(tmp)
            wheels(stage, cache, spec["wheels"])
            for extra in ("cryptography", "openssl"):
                if extra in spec:
                    conda(stage, cache / spec[extra], extra)
            dependency_archive(stage, directory / packages)
        (directory / sums).write_text("".join(digest(directory / n) + "  " + n + "\n" for n in (python, packages, "bridge.pyz")), encoding="ascii")
        files.update((python, packages, sums))
    return sorted("runtime/" + n for n in files)
