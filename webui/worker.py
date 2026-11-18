import asyncio

from app import execute_task
from task_queue import worker_main


if __name__ == "__main__":
    asyncio.run(worker_main(execute_task))
