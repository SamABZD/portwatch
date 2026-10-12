"""Real PostgreSQL, isolated test schemas; all stored bytes are synthetic."""

import os
from pathlib import Path
from uuid import uuid4

import pytest
from psycopg import sql
from psycopg.errors import UndefinedFunction

from portwatch.bookkeeping import (
    begin_batch_processing,
    finish_batch_processing,
    reconcile,
    register_batch,
)
from portwatch.db import connect_db, migrate
from portwatch.settings import Settings
from portwatch.storage import RawStore
from tests.test_storage import SYNTHETIC

pytestmark = pytest.mark.integration


@pytest.fixture
def connection():
    if os.environ.get("PORTWATCH_TEST_DB") != "1":
        pytest.skip("set PORTWATCH_TEST_DB=1 to test against configured local PostgreSQL")
    schema = "step02_test_" + uuid4().hex
    with connect_db(Settings()) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        conn.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema)))
        try:
            migrate(conn)
            yield conn
        finally:
            # This schema was created by this fixture, never a preexisting schema.
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def test_double_registration_and_migration_preserve_data(connection, tmp_path, spec):
    store = RawStore(tmp_path)
    manifest = store.write([SYNTHETIC], spec)
    assert register_batch(connection, store, manifest)
    assert not register_batch(connection, store, manifest)
    assert migrate(connection) == []
    assert migrate(connection) == []
    assert connection.execute("SELECT count(*) FROM ingestion_batches").fetchone() == (1,)
    assert connection.execute("SELECT count(*) FROM schema_migrations").fetchone() == (4,)


def test_reconcile_recovers_after_rename_before_registration(connection, tmp_path, spec):
    store = RawStore(tmp_path)
    manifest = store.write([SYNTHETIC], spec)
    assert connection.execute("SELECT count(*) FROM ingestion_batches").fetchone() == (0,)
    recovered = reconcile(connection, store)
    assert recovered.registered == 1
    assert reconcile(connection, store).existing == 1
    assert connection.execute("SELECT batch_id FROM ingestion_batches").fetchone() == (
        manifest.batch_id,
    )
    assert connection.execute(
        "SELECT status, output_count FROM pipeline_runs ORDER BY id"
    ).fetchall() == [("succeeded", 1), ("succeeded", 0)]


def test_revision_and_processing_attempts(connection, tmp_path, spec):
    store = RawStore(tmp_path)
    first = store.write([SYNTHETIC], spec)
    revised = store.write([SYNTHETIC + b"synthetic revision"], spec)
    register_batch(connection, store, first)
    register_batch(connection, store, revised)
    begin_batch_processing(connection, first.batch_id)
    finish_batch_processing(connection, first.batch_id, OSError("synthetic_api_secret"))
    assert connection.execute(
        "SELECT error_summary FROM ingestion_batches WHERE batch_id = %s", (first.batch_id,)
    ).fetchone() == ("OSError",)
    begin_batch_processing(connection, first.batch_id)
    finish_batch_processing(connection, first.batch_id)
    assert not register_batch(connection, store, first)
    assert connection.execute(
        "SELECT status, attempt_count FROM ingestion_batches WHERE batch_id = %s", (first.batch_id,)
    ).fetchone() == ("processed", 2)
    with pytest.raises(ValueError):
        begin_batch_processing(connection, first.batch_id)


def test_corrupt_batch_never_registered(connection, tmp_path, spec):
    store = RawStore(tmp_path)
    manifest = store.write([SYNTHETIC], spec)
    (tmp_path / manifest.local_path).write_bytes(b"synthetic corruption")
    result = reconcile(connection, store)
    assert result.invalid == 1 and result.registered == 0
    assert connection.execute("SELECT count(*) FROM ingestion_batches").fetchone() == (0,)
    assert connection.execute("SELECT status, error_summary FROM pipeline_runs").fetchone() == (
        "failed",
        "ValueError",
    )


def test_edited_migration_rejected_and_new_bad_migration_rolled_back(connection, tmp_path: Path):
    directory = Path(__file__).parents[1] / "src/portwatch/migrations"
    original = directory / "001_raw_bookkeeping.sql"
    for migration in directory.glob("*.sql"):
        (tmp_path / migration.name).write_text(migration.read_text())
    (tmp_path / original.name).write_text(original.read_text() + "\n-- synthetic tamper\n")
    with pytest.raises(ValueError, match="changed"):
        migrate(connection, tmp_path)
    for migration in directory.glob("*.sql"):
        (tmp_path / migration.name).write_text(migration.read_text())
    (tmp_path / "005_synthetic_failure.sql").write_text(
        "CREATE TABLE synthetic_rollback (id bigint); SELECT nonexistent_synthetic_function();"
    )
    with pytest.raises(UndefinedFunction):
        migrate(connection, tmp_path)
    assert connection.execute("SELECT to_regclass('synthetic_rollback')").fetchone() == (None,)
    assert connection.execute("SELECT count(*) FROM schema_migrations").fetchone() == (4,)
