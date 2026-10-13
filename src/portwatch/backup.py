"""Consistent pg_dump archives and verified restore into a newly owned test database."""

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from psycopg import sql

from portwatch.db import connect_db
from portwatch.events import emit
from portwatch.settings import Settings
from portwatch.storage import checksum_file


class BackupError(RuntimeError):
    """Trusted reason code; subprocess stderr and connection strings stay private."""


def tool_path(settings, name):
    native = settings.project_path(Path("pg-runtime/bin") / (name + ".exe"))
    path = native if native.is_file() else shutil.which(name)
    if not path:
        raise BackupError("postgres_client_tools_missing")
    return str(path)


def pg_command(settings, name, arguments):
    env = os.environ.copy()
    env.update(
        PGPASSWORD=settings.db_password.get_secret_value(),
        PGSSLMODE=settings.db_sslmode,
        PGCONNECT_TIMEOUT="5",
        PGOPTIONS="-c timezone=UTC",
    )
    env.pop("PORTWATCH_AIS_API_KEY", None)
    command = [
        tool_path(settings, name),
        "-h",
        settings.db_host,
        "-p",
        str(settings.db_port),
        "-U",
        settings.db_user,
        "-w",
        *arguments,
    ]
    try:
        result = subprocess.run(command, env=env, capture_output=True, timeout=300, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise BackupError("postgres_client_execution_failed") from None
    if result.returncode:
        raise BackupError("postgres_client_nonzero_exit")


def inventory(connection):
    """Stream deterministic table data plus column contracts in the caller's snapshot."""
    tables = connection.execute(
        """SELECT n.nspname,c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE c.relkind='r' AND n.nspname<>'information_schema' AND n.nspname NOT LIKE 'pg_%'
        AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.classid='pg_class'::regclass
            AND d.objid=c.oid AND d.deptype='e') ORDER BY n.nspname,c.relname"""
    ).fetchall()
    result = {}
    for index, (schema, table) in enumerate(tables):
        names = connection.execute(
            """SELECT a.attname FROM pg_index i JOIN pg_class c ON c.oid=i.indrelid
            JOIN pg_namespace n ON n.oid=c.relnamespace
            CROSS JOIN LATERAL unnest(i.indkey) WITH ORDINALITY k(attnum,sequence)
            JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum=k.attnum
            WHERE i.indisprimary AND n.nspname=%s AND c.relname=%s ORDER BY k.sequence""",
            (schema, table),
        ).fetchall()
        if not names:
            raise BackupError("verification_requires_table_primary_keys")
        columns = connection.execute(
            "SELECT column_name,data_type,udt_schema,udt_name,is_nullable,is_generated "
            "FROM information_schema.columns WHERE table_schema=%s AND table_name=%s "
            "ORDER BY ordinal_position",
            (schema, table),
        ).fetchall()
        digest, count = hashlib.sha256(), 0
        with connection.cursor(name=f"backup_inventory_{index}") as cursor:
            cursor.execute(
                sql.SQL("SELECT to_jsonb(t)::text FROM {}.{} t ORDER BY {}").format(
                    sql.Identifier(schema),
                    sql.Identifier(table),
                    sql.SQL(",").join(sql.Identifier(row[0]) for row in names),
                )
            )
            while rows := cursor.fetchmany(4096):
                for (row,) in rows:
                    digest.update(row.encode())
                    digest.update(b"\n")
                    count += 1
        result[f"{schema}.{table}"] = dict(rows=count, sha256=digest.hexdigest(), columns=columns)
    return result


@contextmanager
def owned_database(settings: Settings):
    name = "pw_step11_" + uuid4().hex
    if not re.fullmatch(r"pw_step11_[0-9a-f]{32}", name) or name == settings.db_name:
        raise BackupError("unsafe_test_database_name")
    created = False
    try:
        with connect_db(settings) as admin:
            admin.execute(
                sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(name))
            )
            created = True
        yield settings.model_copy(update={"db_name": name, "ais_api_key": None})
    finally:
        # Only the exact database successfully created by this invocation can be dropped.
        if created:
            if not re.fullmatch(r"pw_step11_[0-9a-f]{32}", name) or name == settings.db_name:
                raise BackupError("unsafe_test_database_cleanup")
            with connect_db(settings) as admin:
                admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


def verify_backup(settings, path):
    path = settings.project_path(Path(path))
    try:
        manifest = json.loads(Path(str(path) + ".manifest.json").read_text("utf-8"))
        if manifest["schema_version"] != 1 or checksum_file(path) != (
            manifest["content_sha256"],
            manifest["byte_count"],
        ):
            raise ValueError
    except (OSError, ValueError, KeyError, TypeError):
        raise BackupError("backup_manifest_or_checksum_invalid") from None
    started = time.perf_counter()
    with owned_database(settings) as restored:
        pg_command(
            restored,
            "pg_restore",
            ["--exit-on-error", "--no-owner", "--no-acl", "--dbname", restored.db_name, str(path)],
        )
        with connect_db(restored) as conn, conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            actual = inventory(conn)
            postgis = conn.execute("SELECT public.postgis_lib_version()").fetchone()[0]
        normalized = json.loads(json.dumps(actual))
        if normalized != manifest["inventory"]:
            raise BackupError("restored_table_contract_or_data_mismatch")
    result = dict(
        verified=True,
        tables_verified=len(actual),
        rows_verified=sum(table["rows"] for table in actual.values()),
        restore_seconds=time.perf_counter() - started,
        postgis=postgis,
        owned_test_database_removed=True,
        content_sha256=manifest["content_sha256"],
    )
    emit(
        "backup_restore_verified",
        tables_verified=len(actual),
        restore_seconds=result["restore_seconds"],
    )
    return result


def create_backup(settings, path, *, verify=True):
    path = settings.project_path(Path(path))
    if path.suffix != ".dump" or path.exists() or Path(str(path) + ".manifest.json").exists():
        raise BackupError("select_new_dot_dump_path")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / (".portwatch-backup-" + uuid4().hex + ".part")
    started = time.perf_counter()
    try:
        with connect_db(settings) as conn, conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            snapshot = conn.execute("SELECT pg_export_snapshot()").fetchone()[0]
            expected = inventory(conn)
            pg_command(
                settings,
                "pg_dump",
                [
                    "--format=custom",
                    "--snapshot",
                    snapshot,
                    "--file",
                    str(temporary),
                    "--dbname",
                    settings.db_name,
                ],
            )
        digest, size = checksum_file(temporary)
        os.link(temporary, path)  # Fail if another invocation created this target; never overwrite.
        manifest = dict(
            schema_version=1,
            created_at_utc=datetime.now(UTC).isoformat(),
            content_sha256=digest,
            byte_count=size,
            inventory=expected,
            snapshot_semantics="pg_dump_shared_exported_repeatable_read_snapshot",
            includes="database_application_tables_not_raw_files_or_private_dotenv",
        )
        with Path(str(path) + ".manifest.json").open("x", encoding="utf-8") as stream:
            json.dump(manifest, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        result = dict(
            archive_path=str(path),
            byte_count=size,
            content_sha256=digest,
            backup_seconds=time.perf_counter() - started,
            verified=False,
        )
        emit("database_backup_created", byte_count=size, backup_seconds=result["backup_seconds"])
        if verify:
            result.update(verify_backup(settings, path))
        return result
    finally:
        temporary.unlink(missing_ok=True)
