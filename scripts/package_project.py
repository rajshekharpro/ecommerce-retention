"""Create a portable project archive without caches or machine-specific logs."""
from pathlib import Path
import zipfile


def package_project(root=None):
    root = Path(root or Path(__file__).resolve().parents[1]).resolve()
    destination = root.parent / f"{root.name}-project.zip"
    ignored = {".venv", ".git", "__pycache__", ".pytest_cache", "node_modules"}
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for source in sorted(root.rglob("*")):
            if not source.is_file() or any(part in ignored for part in source.relative_to(root).parts):
                continue
            if source.suffix in {".pyc", ".log"} or source.name == "CONTRACT.md":
                continue
            archive.write(source, Path(root.name) / source.relative_to(root))
    with zipfile.ZipFile(destination) as archive:
        broken = archive.testzip()
        if broken:
            raise RuntimeError(f"Archive integrity failed for {broken}")
        for required in ["README.md", "dashboard/index.html", "dashboard/data.js", "dashboard/app.js",
                         "retention/pipeline.py", "tableau/Retention_Analytics.twbx"]:
            if f"{root.name}/{required}" not in archive.namelist():
                raise RuntimeError(f"Missing deliverable: {required}")
        print(f"Packaged {len(archive.namelist())} files: {destination} ({destination.stat().st_size:,} bytes)")
    return destination


if __name__ == "__main__":
    package_project()
