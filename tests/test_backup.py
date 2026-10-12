"""Safety checks reject damaged archives and existing targets before database work."""

import json
from contextlib import contextmanager

import pytest

from portwatch.backup import BackupError, create_backup, verify_backup
from portwatch.settings import Settings
from portwatch.storage import checksum_file


def test_modified_dump_is_rejected_before_creating_any_database(tmp_path, monkeypatch):
    path = tmp_path / "synthetic.dump"
    path.write_bytes(b"synthetic archive")
    sha, size = checksum_file(path)
    tmp_path.joinpath("synthetic.dump.manifest.json").write_text(
        json.dumps(dict(schema_version=1, content_sha256=sha, byte_count=size, inventory={}))
    )
    path.write_bytes(b"synthetic truncation")

    @contextmanager
    def forbidden(_):
        pytest.fail("damaged archive must not create a database")
        yield

    monkeypatch.setattr("portwatch.backup.owned_database", forbidden)
    with pytest.raises(BackupError, match="checksum"):
        verify_backup(Settings(), path)


def test_existing_backup_never_overwritten_and_invalid_extension_never_runs_pg(
    tmp_path, monkeypatch
):
    path = tmp_path / "synthetic.dump"
    path.write_bytes(b"synthetic protected backup")
    monkeypatch.setattr(
        "portwatch.backup.pg_command", lambda *_: pytest.fail("must not invoke pg tools")
    )
    for target in (path, tmp_path / "synthetic.txt"):
        with pytest.raises(BackupError, match="new_dot_dump"):
            create_backup(Settings(), target)
    assert path.read_bytes() == b"synthetic protected backup"
