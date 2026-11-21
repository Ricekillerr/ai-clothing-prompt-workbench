"""Isolated fault injection for the Windows candidate archive gate."""
import hashlib
import subprocess
import tempfile
import zipfile
from pathlib import Path

gate = Path(__file__).with_name("check_candidate_archive.ps1")
with tempfile.TemporaryDirectory(prefix="candidate_gate_") as tmp:
    root = Path(tmp) / "中文 空格"
    root.mkdir()
    (root / "runtime").mkdir()
    file = root / "runtime" / "program.bin"
    file.write_bytes(b"original")
    archive = Path(tmp) / "candidate.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.write(file, "runtime/program.bin")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    def run(expected):
        result = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                               str(gate), str(root), str(archive), expected],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        return result
    valid = run(digest)
    assert valid.returncode == 0, valid.stderr.decode(errors="replace")
    assert run("0" * 64).returncode != 0, "incorrect ZIP digest was accepted"
    file.write_bytes(b"tampered")
    assert run(digest).returncode != 0, "modified extracted file was accepted"
    file.write_bytes(b"original")
    (root / "extra.bin").write_bytes(b"extra")
    assert run(digest).returncode != 0, "extra extracted file was accepted"
    archive = Path(tmp) / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("../escape.bin", b"unsafe")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    assert run(digest).returncode != 0, "ZIP path traversal was accepted"
print("Candidate archive gate: 5 checks passed")
