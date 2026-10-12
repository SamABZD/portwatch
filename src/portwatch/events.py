"""Opt-in structured operational events; callers provide only public IDs/counts."""

import json
import logging
import sys
from datetime import UTC, datetime

LOGGER = logging.getLogger("portwatch.events")
LOGGER.addHandler(logging.NullHandler())
LOGGER.propagate = False


def enable_event_logs():
    previous_level = LOGGER.level
    added = None
    if not any(getattr(handler, "portwatch_owned", False) for handler in LOGGER.handlers):
        handler = logging.StreamHandler(sys.stderr)
        handler.portwatch_owned = True
        handler.setFormatter(logging.Formatter("%(message)s"))
        LOGGER.addHandler(handler)
        added = handler
    LOGGER.setLevel(logging.INFO)

    def cleanup():
        if added is not None:
            LOGGER.removeHandler(added)
            added.close()
        LOGGER.setLevel(previous_level)

    return cleanup


def emit(event, **fields):
    LOGGER.info(
        json.dumps(
            dict(at_utc=datetime.now(UTC).isoformat(), event=event, **fields),
            sort_keys=True,
            default=str,
        )
    )
