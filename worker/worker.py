"""Run background tasks using the shared paper-processing services."""
import asyncio
import logging
import os
import signal

from app import execute_task
from task_queue import TaskQueue


async def worker_main(handler):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    workers = int(os.environ.get("TASK_WORKERS", "3"))
    if not 1 <= workers <= 32:
        raise ValueError("TASK_WORKERS must be between 1 and 32")
    queue = TaskQueue(workers=workers)
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signum, stopped.set)
    await queue.start_workers(handler)
    stopper = asyncio.create_task(stopped.wait())
    try:
        done, _ = await asyncio.wait([*queue.worker_tasks, stopper], return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            if task is not stopper: task.result()
    finally:
        stopper.cancel()
        await asyncio.gather(stopper, return_exceptions=True)
        await queue.stop_workers()


if __name__ == "__main__":
    asyncio.run(worker_main(execute_task))
