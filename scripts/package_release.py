"""Build a source-only archive; user jobs, printers, credentials and binaries are excluded."""
from pathlib import Path
import hashlib
import json
import os
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def source_archive(output: Path | None = None) -> dict:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text())
    version = config["project"]["version"]
    target = output or ROOT / "dist" / f"justbuild-{version}-source.zip"
    target.parent.mkdir(parents=True, exist_ok=True)
    allowed = {".py", ".js", ".mjs", ".css", ".html", ".md", ".json", ".toml", ".sh", ".ps1", ".txt"}
    files = [ROOT / name for name in ("README.md", "LICENSE", "pyproject.toml", "Dockerfile", ".dockerignore", "AGENTS.md", "justbuild") if (ROOT / name).is_file()]
    for folder in ("src", "tests", "docs", "tasks", "schemas", "scripts"):
        files.extend(p for p in (ROOT / folder).rglob("*") if p.is_file() and not p.is_symlink()
                     and p.suffix in allowed and "__pycache__" not in p.parts
                     and not any(part.startswith(".") for part in p.relative_to(ROOT).parts))
    prefix = f"justbuild-{version}"
    temporary = target.with_suffix(".zip.next")
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for path in sorted(set(files)):
                info = zipfile.ZipInfo(prefix + "/" + str(path.relative_to(ROOT)), date_time=(2026,10,24,0,0,0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = (0o100755 if (path.suffix == ".sh" or path == ROOT / "justbuild") else 0o100644) << 16
                archive.writestr(info, path.read_bytes())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return {"path": str(target), "bytes": target.stat().st_size,
            "sha256": hashlib.sha256(target.read_bytes()).hexdigest(), "files": len(set(files))}


if __name__ == "__main__":
    print(json.dumps(source_archive(), indent=2))
