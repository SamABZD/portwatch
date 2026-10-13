"""Recover only this singleton cloud runtime's abandoned Prefect deployments."""

import argparse
import asyncio
import json
import os
from datetime import UTC, datetime

import psutil

from portwatch.db import connect_db
from portwatch.pipeline import owned_lock
from portwatch.settings import Settings


async def recover(client, settings, ports, cutoff):
    from prefect.client.schemas.filters import (
        FlowRunFilter,
        FlowRunFilterDeploymentId,
        FlowRunFilterExpectedStartTime,
        FlowRunFilterState,
        FlowRunFilterStateType,
    )
    from prefect.exceptions import ObjectNotFound
    from prefect.states import Cancelled, Crashed, StateType

    report = dict(cutoff_utc=cutoff.isoformat(), cancelled=0, crashed=0)
    for port in ports:
        with connect_db(settings) as connection, owned_lock(connection, "mutation", port):
            for name, suffix in (
                ("portwatch-live", "minute"),
                ("portwatch-weather", "hourly"),
                ("portwatch-news", "hourly"),
            ):
                try:
                    deployment = await client.read_deployment_by_name(f"{name}/{port}-{suffix}")
                except ObjectNotFound:
                    continue
                flow = await client.read_flow(deployment.flow_id)
                if flow.name != name or deployment.parameters != {"port": port}:
                    raise RuntimeError("unexpected_deployment_ownership")
                await client.set_deployment_paused_state(deployment.id, True)
                try:
                    for _ in range(10):
                        runs = await client.read_flow_runs(
                            flow_run_filter=FlowRunFilter(
                                deployment_id=FlowRunFilterDeploymentId(any_=[deployment.id]),
                                expected_start_time=FlowRunFilterExpectedStartTime(before_=cutoff),
                                state=FlowRunFilterState(
                                    type=FlowRunFilterStateType(
                                        any_=[
                                            StateType.SCHEDULED,
                                            StateType.RUNNING,
                                            StateType.PENDING,
                                            StateType.CANCELLING,
                                        ]
                                    )
                                ),
                            ),
                            limit=200,
                        )
                        changed = 0
                        for run in runs:
                            current = await client.read_flow_run(run.id)
                            # Re-read immediately before transition; never touch a new attempt.
                            if not current.state or current.state.timestamp >= cutoff:
                                continue
                            if current.state_type == StateType.SCHEDULED:
                                state, label = Cancelled(), "cancelled"
                            elif current.state_type in {
                                StateType.RUNNING,
                                StateType.PENDING,
                                StateType.CANCELLING,
                            }:
                                state, label = Crashed(), "crashed"
                            else:
                                continue
                            state.message = (
                                "Singleton container replaced; "
                                "preserved batches replay on the next run."
                            )
                            result = await client.set_flow_run_state(run.id, state)
                            if result.state and result.state.type == state.type:
                                report[label] += 1
                                changed += 1
                        if len(runs) < 200 or not changed:
                            break
                    else:
                        raise RuntimeError("recovery_bound_reached")
                finally:
                    await client.set_deployment_paused_state(deployment.id, deployment.paused)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ports", required=True)
    parser.add_argument("--cutoff", required=True)
    args = parser.parse_args()
    # Runtime calls this before launching its runner, under the exclusive volume OS lock.
    # Cloud uses a single mounted volume and zero deployment overlap. Fail closed elsewhere.
    parent = psutil.Process(os.getppid())
    if os.environ.get("PORTWATCH_CLOUD_LIVE") != "1" or "portwatch.runtime" not in parent.cmdline():
        raise RuntimeError("recovery_requires_owned_cloud_runtime")
    cutoff = datetime.fromisoformat(args.cutoff)
    if cutoff.tzinfo is None or cutoff > datetime.now(UTC):
        raise ValueError("invalid_recovery_cutoff")
    from portwatch.orchestration import configure_local

    configure_local()
    from prefect.client.orchestration import get_client

    settings = Settings()

    async def execute():
        async with get_client() as client:
            return await recover(client, settings, args.ports.split(","), cutoff)

    report = asyncio.run(execute())
    path = settings.project_path(settings.live_status_dir) / "schedule-recovery.json"
    path.write_text(json.dumps(report, indent=2), "utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
