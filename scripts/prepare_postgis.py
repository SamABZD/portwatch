"""Prepare an isolated PG17/PostGIS runtime; never alter or restart existing servers."""

import hashlib
import json
import shutil
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PG_HOME = Path("C:/Program Files/PostgreSQL/17")
RUNTIME = ROOT / "pg-runtime"
BUNDLE = "postgis-bundle-pg17-3.6.2x64"
URL = f"https://download.osgeo.org/postgis/windows/pg17/{BUNDLE}.zip"
# SHA-256 of the official HTTPS bundle inspected during Step05, 2026-10-11.
EXPECTED_SHA256 = "7ba180ee2a352987b9a2f194673652c59483b55852295ccf401dceccd8765425"


def main():
    if not (PG_HOME / "bin/postgres.exe").is_file():
        raise SystemExit("Native PostgreSQL17 required at the documented PG_HOME.")
    ready = RUNTIME / "portwatch-runtime.json"
    if RUNTIME.exists():
        if ready.is_file() and json.loads(ready.read_text())["bundle_sha256"] == EXPECTED_SHA256:
            print("Private PostGIS runtime already prepared.")
            return
        raise SystemExit("Incomplete runtime retained; inspect pg-runtime before retrying.")
    archive = ROOT / "downloads" / f"{BUNDLE}.zip"
    archive.parent.mkdir(exist_ok=True)
    if not archive.exists():
        urllib.request.urlretrieve(URL, archive)
    with archive.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != EXPECTED_SHA256:
        raise SystemExit("Bundle checksum differs from the inspected official release.")
    RUNTIME.mkdir()
    for directory in ("bin", "lib", "share"):
        shutil.copytree(PG_HOME / directory, RUNTIME / directory)
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.infolist():
            parts = Path(member.filename).parts
            if not parts or parts[0] != BUNDLE or member.is_dir():
                continue
            destination = RUNTIME.joinpath(*parts[1:]).resolve()
            if not destination.is_relative_to(RUNTIME.resolve()):
                raise ValueError("Unsafe archive member")
            destination.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(member) as source, destination.open("wb") as output:
                shutil.copyfileobj(source, output)
    ready.write_text(
        json.dumps(
            {
                "bundle_url": URL,
                "bundle_sha256": digest,
                "base_runtime": str(PG_HOME),
                "postgres_major": 17,
            },
            indent=2,
        )
    )
    print(f"Private runtime prepared: {RUNTIME}")
    print("Restart only PortWatch's existing pgdata/portwatch with pg-runtime/bin/pg_ctl.exe.")


if __name__ == "__main__":
    main()
