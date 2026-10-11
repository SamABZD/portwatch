"""Shared local/CI checks. Never ingest provider data or use an AIS credential."""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from importlib.resources import files
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


def run(command: list[str], env: dict[str, str], *, cwd: Path = ROOT) -> None:
    display = command[:]
    if "-c" in display:
        display[display.index("-c") + 1] = "<Python verification>"
    print("+ " + " ".join(display), flush=True)
    subprocess.run(command, cwd=cwd, env=env, check=True, timeout=600)


def assert_tests_ran(report: Path) -> None:
    """A skipped-only or empty suite must never satisfy a required check."""
    suites = list(ET.parse(report).getroot().iter("testsuite"))
    count = sum(int(suite.get("tests", "0")) for suite in suites)
    skipped = sum(int(suite.get("skipped", "0")) for suite in suites)
    failures = sum(
        int(suite.get("failures", "0")) + int(suite.get("errors", "0")) for suite in suites
    )
    if count == 0 or skipped or failures:
        raise ValueError("validation requires executed tests with no skips, failures, or errors")


def check_migrations() -> None:
    """Migrate a new owned schema twice; never clear the user's application tables."""
    from psycopg import sql

    from portwatch.db import connect_db, migrate
    from portwatch.settings import Settings

    schema = "validate_" + uuid4().hex
    with connect_db(Settings(ais_api_key=None)) as connection:
        postgres_major = connection.info.server_version // 10000
        postgis = connection.execute("SELECT public.postgis_lib_version()").fetchone()[0]
        if postgres_major != 17 or not postgis.startswith("3.6."):
            raise ValueError("validation requires PostgreSQL 17 and PostGIS 3.6")
        print(f"Database: PostgreSQL {postgres_major}, PostGIS {postgis}", flush=True)
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            connection.execute(
                sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema))
            )
            expected = sorted(
                path.name
                for path in files("portwatch").joinpath("migrations").iterdir()
                if path.name.endswith(".sql")
            )
            if not expected or migrate(connection) != expected or migrate(connection) != []:
                raise ValueError("fresh migration or repeat migration check failed")
            if connection.execute("SELECT count(*) FROM schema_migrations").fetchone()[0] != len(
                expected
            ):
                raise ValueError("migration ledger is incomplete")
            print(f"Migrations: {len(expected)} applied; repeat applied zero", flush=True)
        finally:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def check_package(uv: str, python: str, scratch: Path, env: dict[str, str]) -> None:
    output = scratch / "dist"
    run([uv, "build", "--out-dir", str(output)], env)
    wheels = list(output.glob("*.whl"))
    if len(wheels) != 1 or len(list(output.glob("*.tar.gz"))) != 1:
        raise ValueError("build must produce one wheel and one source distribution")
    # Isolated Python omits the checkout from sys.path. Put the actual pure-Python
    # wheel first and assert its origin, rather than importing the editable install.
    probe = """
import importlib.resources
import runpy
import sys
wheel = sys.argv.pop(1)
sys.path.insert(0, wheel)
import portwatch
import portwatch.cli
assert str(portwatch.__file__).startswith(wheel), 'editable source shadowed the wheel'
resources = importlib.resources.files('portwatch').joinpath('migrations')
assert sorted(p.name for p in resources.iterdir() if p.name.endswith('.sql')) == sys.argv[1:]
for name in sys.argv[1:]:
    assert resources.joinpath(name).read_text(encoding='utf-8').strip()
print('Built wheel imports and includes every committed SQL migration', flush=True)
sys.argv = ['portwatch', '--help']
runpy.run_module('portwatch', run_name='__main__')
"""
    migrations = sorted(path.name for path in (ROOT / "src/portwatch/migrations").glob("*.sql"))
    run([python, "-I", "-c", probe, str(wheels[0]), *migrations], env, cwd=scratch)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--group", choices=["all", "quality", "integration"], default="all")
    args = parser.parse_args()
    uv = shutil.which("uv")
    if uv is None:
        bundled = ROOT / ".bootstrap-venv/Scripts/uv.exe"
        uv = str(bundled) if bundled.is_file() else None
    if uv is None:
        parser.error("uv is required; install it or use the existing .bootstrap-venv")
    env = os.environ.copy()
    env.update(PORTWATCH_AIS_API_KEY="", PORTWATCH_TEST_DB="0")
    with tempfile.TemporaryDirectory(prefix="portwatch-validation-") as directory:
        scratch = Path(directory)
        for name in ("RAW", "DERIVED", "QUARANTINE", "LIVE_SPOOL", "LIVE_STATUS"):
            env[f"PORTWATCH_{name}_DIR"] = str(scratch / name.lower())
        run([uv, "sync", "--locked", "--extra", "dev"], env)
        python = str(ROOT / (".venv/Scripts/python.exe" if os.name == "nt" else ".venv/bin/python"))
        if args.group in {"all", "quality"}:
            run([python, "-m", "ruff", "check", "."], env)
            run([python, "-m", "ruff", "format", "--check", "."], env)
            report = scratch / "unit.xml"
            run(
                [
                    python,
                    "-m",
                    "pytest",
                    "-q",
                    "--maxfail=1",
                    "-m",
                    "not integration",
                    "-o",
                    "faulthandler_timeout=30",
                    f"--junitxml={report}",
                ],
                env,
            )
            assert_tests_ran(report)
            check_package(uv, python, scratch, env)
        if args.group in {"all", "integration"}:
            # This subprocess inherits sanitized paths/key and the configured DB.
            run(
                [python, "-c", "from scripts.validate import check_migrations; check_migrations()"],
                env,
            )
            env["PORTWATCH_TEST_DB"] = "1"
            report = scratch / "integration.xml"
            run(
                [
                    python,
                    "-m",
                    "pytest",
                    "-q",
                    "--maxfail=1",
                    "-m",
                    "integration",
                    f"--junitxml={report}",
                ],
                env,
            )
            assert_tests_ran(report)
    print(f"PASS: {args.group} validation", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError) as error:
        print(f"FAIL: {type(error).__name__}", file=sys.stderr)
        raise SystemExit(1) from None
