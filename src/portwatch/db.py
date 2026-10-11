"""PostgreSQL connections and immutable numbered SQL migrations."""

import hashlib
import re
from importlib.resources import files
from pathlib import Path

import psycopg

from portwatch.settings import Settings


def connect_db(settings: Settings) -> psycopg.Connection:
    return psycopg.connect(
        host=settings.db_host,
        port=settings.db_port,
        dbname=settings.db_name,
        user=settings.db_user,
        password=settings.db_password.get_secret_value(),
        sslmode=settings.db_sslmode,
        connect_timeout=5,
        options="-c timezone=UTC",
        autocommit=True,
    )


def migrate(connection: psycopg.Connection, directory: Path | None = None) -> list[str]:
    """Serialize migration runners; reject edits to already applied SQL."""
    directory = directory or files("portwatch").joinpath("migrations")
    migrations = []
    for path in sorted(directory.iterdir(), key=lambda item: item.name):
        match = re.fullmatch(r"(\d{3})_[a-z0-9_]+\.sql", path.name)
        if match:
            body = path.read_text(encoding="utf-8")
            checksum = hashlib.sha256(body.encode()).hexdigest()
            migrations.append((int(match[1]), path.name, checksum, body))
    if not migrations or len({item[0] for item in migrations}) != len(migrations):
        raise ValueError("missing or duplicate migration numbers")
    applied_names = []
    with connection.transaction():
        connection.execute("SELECT pg_advisory_xact_lock(734920021)")
        connection.execute(
            """CREATE TABLE IF NOT EXISTS schema_migrations (
                version integer PRIMARY KEY,
                name text NOT NULL UNIQUE,
                content_sha256 text NOT NULL,
                applied_at timestamptz NOT NULL DEFAULT now()
            )"""
        )
        applied = {
            row[0]: (row[1], row[2])
            for row in connection.execute(
                "SELECT version, name, content_sha256 FROM schema_migrations"
            )
        }
        known = {item[0] for item in migrations}
        if set(applied) - known:
            raise ValueError("database contains migrations absent from this package")
        for version, name, checksum, body in migrations:
            if version in applied:
                if applied[version] != (name, checksum):
                    raise ValueError("applied migration was changed")
                continue
            if applied and version < max(applied):
                raise ValueError("new migration must follow applied versions")
            connection.execute(body, prepare=False)
            connection.execute(
                "INSERT INTO schema_migrations (version, name, content_sha256) VALUES (%s, %s, %s)",
                (version, name, checksum),
            )
            applied_names.append(name)
    return applied_names
