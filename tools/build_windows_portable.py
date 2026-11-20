"""Build a Windows candidate in a caller-supplied empty staging directory."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def build(destination):
    if sys.platform != "win32" or sys.version_info[:2] != (3, 12):
        raise RuntimeError("Windows Python 3.12 is required")
    destination = Path(destination).resolve()
    if destination.exists():
        raise RuntimeError("staging directory must not already exist")
    destination.mkdir(parents=True)
    runtime = destination / "runtime"
    runtime.mkdir()
    base = Path(sys.base_prefix)
    for filename in ("python.exe", "pythonw.exe", "python3.dll", "python312.dll", "vcruntime140.dll", "vcruntime140_1.dll", "LICENSE.txt"):
        source = base / filename
        if not source.is_file():
            raise RuntimeError(f"missing runtime component: {filename}")
        shutil.copy2(source, runtime / filename)
    shutil.copytree(base / "DLLs", runtime / "DLLs")
    shutil.copytree(base / "Lib", runtime / "Lib",
                    ignore=shutil.ignore_patterns("site-packages", "__pycache__", "test", "tests", "ensurepip"))
    site = runtime / "Lib" / "site-packages"
    site.mkdir()
    subprocess.run([sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
                    "--no-compile", "--target", str(site), "-r", str(ROOT / "requirements.txt")], check=True)
    manifest = json.loads((ROOT / "update_manifest.json").read_text(encoding="utf-8"))
    for filename in manifest["files"]:
        source = ROOT / filename
        if source.parent != ROOT or source.is_symlink() or not source.is_file():
            raise RuntimeError(f"invalid program file: {filename}")
        actual = hashlib.sha256(source.read_bytes()).hexdigest()
        if actual != manifest.get("sha256", {}).get(filename):
            raise RuntimeError(f"program file hash mismatch: {filename}")
        shutil.copy2(source, destination / filename)
    shutil.copy2(ROOT / "update_manifest.json", destination / "update_manifest.json")
    metadata = subprocess.check_output(
        [str(runtime / "python.exe"), "-I", "-c",
         "import json,sys,PIL,imageio_ffmpeg; print(json.dumps({'python':sys.version.split()[0],"
         "'pillow':PIL.__version__,'imageio_ffmpeg':imageio_ffmpeg.__version__,"
         "'ffmpeg':imageio_ffmpeg.get_ffmpeg_version()}))"],
        cwd=destination, text=True, env={**os.environ, "PYTHONNOUSERSITE": "1", "IMAGEIO_FFMPEG_EXE": ""})
    (destination / "dependency-versions.json").write_text(metadata, encoding="utf-8")


if __name__ == "__main__":
    build(sys.argv[1])
