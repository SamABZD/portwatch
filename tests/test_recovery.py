"""Recovery never transitions a current attempt or a foreign deployment."""

import asyncio
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from prefect.states import Running, Scheduled, StateType

from portwatch.recovery import recover
from portwatch.settings import Settings


def test_recovery_only_transitions_old_owned_attempts_and_restores_pause(monkeypatch):
    import portwatch.recovery as recovery

    cutoff = datetime.now(UTC)
    old = cutoff - timedelta(minutes=2)
    states = [Running(timestamp=old), Scheduled(timestamp=old), Running(timestamp=cutoff)]
    runs = [SimpleNamespace(id=uuid4(), state=state, state_type=state.type) for state in states]
    deployments = [
        SimpleNamespace(id=uuid4(), flow_id=uuid4(), parameters={"port": "oakland"}, paused=True)
        for _ in range(3)
    ]
    names = ["portwatch-live", "portwatch-weather", "portwatch-news"]

    class Client:
        transitions = []
        pauses = []
        index = -1

        async def read_deployment_by_name(self, name):
            self.index += 1
            assert name.startswith(names[self.index])
            return deployments[self.index]

        async def read_flow(self, flow_id):
            return SimpleNamespace(name=names[self.index])

        async def set_deployment_paused_state(self, identity, paused):
            self.pauses.append(paused)

        async def read_flow_runs(self, **kwargs):
            assert kwargs["limit"] == 200
            return runs if self.index == 0 else []

        async def read_flow_run(self, identity):
            return next(run for run in runs if run.id == identity)

        async def set_flow_run_state(self, identity, state):
            self.transitions.append((identity, state.type))
            return SimpleNamespace(state=state)

    monkeypatch.setattr(recovery, "connect_db", lambda _: nullcontext(object()))
    monkeypatch.setattr(recovery, "owned_lock", lambda *args: nullcontext())
    client = Client()
    report = asyncio.run(recover(client, Settings(), ["oakland"], cutoff))
    assert report["crashed"] == report["cancelled"] == 1
    assert client.transitions == [
        (runs[0].id, StateType.CRASHED),
        (runs[1].id, StateType.CANCELLED),
    ]
    assert client.pauses == [True] * 6

    client.index = -1
    deployments[0].parameters = {"port": "los_angeles"}
    with pytest.raises(RuntimeError, match="unexpected_deployment_ownership"):
        asyncio.run(recover(client, Settings(), ["oakland"], cutoff))
