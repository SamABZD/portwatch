"""Read evidence of a real automatic live run from the running local Prefect API."""

import asyncio
import json
from datetime import datetime
from pathlib import Path

from portwatch.orchestration import configure_local
from portwatch.settings import PROJECT_ROOT


async def verify():
    from prefect.client.orchestration import get_client
    from prefect.client.schemas.filters import FlowRunFilter, FlowRunFilterDeploymentId
    from prefect.client.schemas.sorting import FlowRunSort

    since = datetime.fromisoformat(
        (PROJECT_ROOT / "artifacts/step10/runner-start-utc.txt")
        .read_text("utf-8-sig")
        .strip()
        .replace("Z", "+00:00")
    )
    async with get_client() as client:
        deployment = await client.read_deployment_by_name("portwatch-live/los_angeles-minute")
        runs = await client.read_flow_runs(
            flow_run_filter=FlowRunFilter(
                deployment_id=FlowRunFilterDeploymentId(any_=[deployment.id]),
                state={"type": {"any_": ["COMPLETED"]}},
            ),
            sort=FlowRunSort.START_TIME_DESC,
            limit=20,
        )
        completed = [
            run
            for run in runs
            if run.auto_scheduled
            and run.start_time
            and run.start_time >= since
            and run.state.is_completed()
        ]
        if not completed:
            print(
                json.dumps(
                    {"verified": False, "recent_states": [str(run.state.type) for run in runs]}
                )
            )
            return 1
        run = completed[0]
        tasks = await client.read_task_runs(flow_run_filter=FlowRunFilter(id={"any_": [run.id]}))
        assert tasks and all(task.state.is_completed() for task in tasks)
        evidence = dict(
            verified=True,
            deployment_id=str(deployment.id),
            flow_run_id=str(run.id),
            auto_scheduled=run.auto_scheduled,
            state=str(run.state.type),
            expected_start_time_utc=run.expected_start_time.isoformat(),
            started_at_utc=run.start_time.isoformat(),
            ended_at_utc=run.end_time.isoformat(),
            tasks=[dict(id=str(task.id), state=str(task.state.type)) for task in tasks],
            verification="Actual automatic run and business task completed, not schedule creation",
        )
        (PROJECT_ROOT / Path("artifacts/step10/schedule-verification.json")).write_text(
            json.dumps(evidence, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps(evidence, indent=2))
        return 0


if __name__ == "__main__":
    configure_local()
    raise SystemExit(asyncio.run(verify()))
