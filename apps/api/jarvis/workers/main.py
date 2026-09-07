"""The ``worker`` entrypoint: every background loop except the scheduler, in one process.

PLAN.md §6 promised one container with three entrypoints — ``api``, ``worker``,
``scheduler``. This is the second. Four loops share the process because each spends its
life sleeping on a poll interval; a crash in one is logged by its own loop and does not
take the others down, and ``asyncio.gather`` ends the process only if a loop returns —
which none of them do.

    uv run python -m jarvis.workers.main
"""

from __future__ import annotations

import asyncio

from jarvis.core.config import get_settings
from jarvis.core.logging import configure_logging, get_logger
from jarvis.workers import agent, connector, heartbeat, notify

log = get_logger(__name__)


async def main() -> None:
    log.info("worker_starting", loops=["agent", "connector", "notify", "heartbeat"])
    await asyncio.gather(
        agent.run_forever(),
        connector.run_forever(),
        notify.run_forever(),
        heartbeat.run_forever(),
    )


if __name__ == "__main__":
    configure_logging(level=get_settings().log_level)
    asyncio.run(main())
