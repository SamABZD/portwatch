"""Synthetic local live capture with actual PostgreSQL in a temporary test schema."""

import asyncio
from contextlib import nullcontext

import pytest
from pydantic import SecretStr
from typer.testing import CliRunner
from websockets.asyncio.server import serve

from portwatch.cli import app
from portwatch.live import LiveCollector
from portwatch.settings import Settings
from portwatch.storage import RawStore
from tests.test_bookkeeping import connection as connection
from tests.test_live import BOUNDS, CONFIRMATION, POSITION_FRAME, synthetic_spec

pytestmark = pytest.mark.integration


def test_cli_live_registers_real_db_run_without_source_or_key_leak(
    connection,
    tmp_path,
    monkeypatch,
):
    settings = Settings(
        _env_file=None,
        ais_api_key=SecretStr("synthetic_step04_secret"),
        raw_dir=tmp_path / "raw",
        live_spool_dir=tmp_path / "active",
        live_status_dir=tmp_path / "status",
    )
    monkeypatch.setattr("portwatch.cli.Settings", lambda: settings)
    monkeypatch.setattr("portwatch.cli.live_spec", lambda *_: (synthetic_spec(), BOUNDS))
    monkeypatch.setattr("portwatch.cli.connect_db", lambda *_: nullcontext(connection))

    async def scenario():
        async def handler(socket):
            await socket.recv()
            await socket.send(CONFIRMATION)
            await socket.send(POSITION_FRAME)
            await socket.wait_closed()

        async with serve(handler, "127.0.0.1", 0) as server:
            uri = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"
            monkeypatch.setattr(
                "portwatch.cli.LiveCollector",
                lambda *args, **kwargs: LiveCollector(*args, uri=uri, **kwargs),
            )
            return await asyncio.to_thread(
                # Allow Windows scheduling/fsync before the finite capture deadline.
                CliRunner().invoke, app, ["ingest", "live", "--duration", "2"]
            )

    result = asyncio.run(scenario())
    assert result.exit_code == 0, result.output
    assert "synthetic_step04_secret" not in result.output
    assert connection.execute(
        "SELECT stage, status, input_count, output_count FROM pipeline_runs"
    ).fetchall() == [("live_collect", "succeeded", 2, 2)]
    assert connection.execute(
        "SELECT source_id, status, record_count FROM ingestion_batches"
    ).fetchall() == [("synthetic_step04", "registered", 2)]
    assert len(RawStore(tmp_path / "raw").inspect()) == 1
