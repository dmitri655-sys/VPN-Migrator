"""Reproducible stdlib-only release builder. Run from any directory."""
import hashlib
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT.parent
VERSION = "0.2.0"


def entry(archive, name, data, executable=False):
    item = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
    item.create_system = 3
    item.external_attr = (0o100755 if executable else 0o100644) << 16
    item.compress_type = zipfile.ZIP_DEFLATED
    archive.writestr(item, data)


def build():
    files = sorted(p for p in ROOT.rglob("*") if p.is_file()
                   and not any(part in (".git", ".venv", "__pycache__", "build", "dist", ".test-tmp")
                               or part.endswith(".egg-info") for part in p.relative_to(ROOT).parts))
    bundle = OUT / ("vps-migrator-" + VERSION + ".zip")
    with zipfile.ZipFile(bundle, "w") as archive:
        for p in files:
            entry(archive, "vps-migrator/" + p.relative_to(ROOT).as_posix(), p.read_bytes(),
                  p.name in ("install.sh", "vps-migrator"))
    runnable = OUT / ("vps-migrator-" + VERSION + ".pyz")
    with zipfile.ZipFile(runnable, "w") as archive:
        entry(archive, "__main__.py", b"from vps_migrator.cli import main\nmain()\n")
        for p in sorted((ROOT / "vps_migrator").glob("*.py")):
            entry(archive, "vps_migrator/" + p.name, p.read_bytes())
        entry(archive, "LICENSE", (ROOT / "LICENSE").read_bytes())
    checksums = []
    for p in (bundle, runnable):
        checksums.append(hashlib.sha256(p.read_bytes()).hexdigest() + "  " + p.name)
        print(p.name, p.stat().st_size)
    (OUT / "SHA256SUMS.txt").write_text("\n".join(checksums) + "\n", encoding="ascii")
    (OUT / ("SHA256SUMS-" + VERSION + ".txt")).write_text("\n".join(checksums) + "\n", encoding="ascii")


if __name__ == "__main__":
    build()
