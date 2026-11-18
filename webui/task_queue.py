"""Redis-backed durable task queue shared by the API and worker containers."""
import asyncio
from datetime import datetime, timezone
import json
import logging
import os
from uuid import uuid4



ACTIVE = {"queued", "running"}
TERMINAL = {"completed", "skipped", "failed", "cancelled", "interrupted"}
PREFIX = "researcher:tasks:"


def now():
    return datetime.now(timezone.utc).isoformat()


class SkipTask(Exception):
    pass


class TaskQueue:
    def __init__(self, url=None, workers=3, prefix=PREFIX):
        from redis.asyncio import Redis
        self.redis = Redis.from_url(url or os.environ.get("REDIS_URL", "redis://redis:6379/0"), decode_responses=True)
        self.prefix = prefix
        self.worker_count = workers
        self.worker_tasks = []

    @property
    def pending_key(self): return self.prefix + "pending"

    @property
    def processing_key(self): return self.prefix + "processing"

    @property
    def order_key(self): return self.prefix + "order"

    @property
    def active_key(self): return self.prefix + "active"

    @property
    def events_key(self): return self.prefix + "events"

    async def events(self, limit=100, heartbeat=15):
        # Subscribe before reading the snapshot so concurrent changes are not lost.
        # Pub/Sub blocks on messages; the timeout only sends an SSE keepalive.
        async with self.redis.pubsub() as pubsub:
            await pubsub.subscribe(self.events_key)
            await pubsub.get_message(timeout=None)  # Wait for the subscription acknowledgement.
            yield await self.listing(limit)
            while True:
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=heartbeat)
                if message is None:
                    yield None
                    continue
                changed = await self.get(message["data"])
                if changed:
                    changed.pop("result", None)
                snapshot = await self.listing(limit)
                snapshot["changed"] = changed
                yield snapshot

    def status_key(self, status): return self.prefix + "status:" + status

    def task_key(self, task_id): return self.prefix + "item:" + task_id

    def dedupe_key(self, filename, action): return self.prefix + "active:" + filename + ":" + action

    def lock_key(self, filename): return self.prefix + "paper-lock:" + filename

    def paper_key(self, filename): return self.prefix + "paper:" + filename

    async def get(self, task_id):
        raw = await self.redis.get(self.task_key(task_id))
        return json.loads(raw) if raw else None

    async def active(self, filename=None, action=None):
        ids = await self.redis.smembers(self.active_key)
        tasks = []
        for task_id in ids:
            task = await self.get(task_id)
            if task and task["status"] in ACTIVE and (filename is None or task["filename"] == filename) and (action is None or task["action"] == action):
                tasks.append(task)
        return tasks

    async def latest(self, filename, action):
        task_id = await self.redis.get(self.prefix + "latest:" + filename + ":" + action)
        return await self.get(task_id) if task_id else None

    async def listing(self, limit=100):
        active_ids = await self.redis.smembers(self.active_key)
        terminal_ids = await self.redis.zrevrange(self.order_key, 0, max(0, limit - 1))
        ids = list(active_ids) + [task_id for task_id in terminal_ids if task_id not in active_ids]
        all_tasks = []
        counts = {status: await self.redis.scard(self.status_key(status)) for status in (*ACTIVE, *TERMINAL)}
        for task_id in ids:
            task = await self.get(task_id)
            if not task: continue
            all_tasks.append(task)
        active = [task for task in all_tasks if task["status"] in ACTIVE]
        terminal = [task for task in all_tasks if task["status"] not in ACTIVE]
        visible = active + terminal[:max(0, limit - len(active))]
        for task in visible: task.pop("result", None)
        return {"tasks": visible, "counts": counts, "workers": self.worker_count}

    async def submit(self, filename, title, action, options):
        task_id = uuid4().hex
        task = {"id": task_id, "filename": filename, "title": title, "action": action,
                "options": options, "status": "queued", "created_at": now(),
                "started_at": None, "finished_at": None, "worker": None, "error": "", "result": None}
        key = self.dedupe_key(filename, action)
        script = self.redis.register_script("""
            local old = redis.call('GET', KEYS[1])
            if old then
                local raw = redis.call('GET', ARGV[1] .. old)
                if raw then
                    local task = cjson.decode(raw)
                    if task.status == 'queued' or task.status == 'running' then return old end
                end
            end
            if redis.call('LLEN', KEYS[2]) >= 2000 then return redis.error_reply('QUEUE_FULL') end
            redis.call('SET', KEYS[1], ARGV[2])
            redis.call('SET', ARGV[1] .. ARGV[2], ARGV[3])
            redis.call('ZADD', KEYS[3], ARGV[4], ARGV[2])
            redis.call('LPUSH', KEYS[2], ARGV[2])
            redis.call('SADD', ARGV[5], ARGV[2])
            redis.call('SADD', ARGV[6], ARGV[2])
            redis.call('ZADD', ARGV[7], ARGV[4], ARGV[2])
            redis.call('SET', ARGV[8], ARGV[2])
            redis.call('PUBLISH', ARGV[9], ARGV[2])
            return ARGV[2]
        """)
        try:
            result = await script(keys=[key, self.pending_key, self.order_key], args=[self.prefix + "item:", task_id,
                                   json.dumps(task, ensure_ascii=False), datetime.now().timestamp(), self.active_key, self.status_key("queued"), self.paper_key(filename), self.prefix + "latest:" + filename + ":" + action, self.events_key])
        except Exception as error:
            if "QUEUE_FULL" in str(error): raise ValueError("任务队列已满（2000），请稍后重试") from error
            raise
        if result != task_id:
            existing = await self.get(result)
            if existing and existing["options"] != options:
                raise ValueError("同类任务已在队列中，请等待完成后再提交不同选项")
            return existing
        return task

    async def cancel(self, task_id):
        key = self.task_key(task_id)
        task = await self.get(task_id)
        if not task: return False
        script = self.redis.register_script("""
            local raw = redis.call('GET', KEYS[1])
            if not raw then return 0 end
            local task = cjson.decode(raw)
            if task.status ~= 'queued' then return 0 end
            task.status = 'cancelled'; task.finished_at = ARGV[1]
            redis.call('SET', KEYS[1], cjson.encode(task))
            redis.call('LREM', KEYS[2], 0, ARGV[2])
            if redis.call('GET', KEYS[3]) == ARGV[2] then redis.call('DEL', KEYS[3]) end
            redis.call('SREM', KEYS[4], ARGV[2]); redis.call('SREM', KEYS[5], ARGV[2]); redis.call('SADD', KEYS[6], ARGV[2])
            redis.call('ZREM', KEYS[7], ARGV[2])
            redis.call('PUBLISH', ARGV[3], ARGV[2])
            return 1
        """)
        return bool(await script(keys=[key, self.pending_key, self.dedupe_key(task["filename"], task["action"]), self.active_key, self.status_key("queued"), self.status_key("cancelled"), self.paper_key(task["filename"])], args=[now(), task_id, self.events_key]))

    async def finish(self, task, status, result=None, error=""):
        task.update(status=status, finished_at=now(), result=result, error=error)
        pipe = self.redis.pipeline(transaction=True)
        pipe.set(self.task_key(task["id"]), json.dumps(task, ensure_ascii=False))
        pipe.lrem(self.processing_key, 1, task["id"])
        pipe.delete(self.lock_key(task["filename"]))
        pipe.delete(self.dedupe_key(task["filename"], task["action"]))
        pipe.srem(self.active_key, task["id"])
        pipe.srem(self.status_key("running"), task["id"])
        pipe.sadd(self.status_key(status), task["id"])
        pipe.zrem(self.paper_key(task["filename"]), task["id"])
        pipe.publish(self.events_key, task["id"])
        await pipe.execute()
        await self.trim_history()

    async def trim_history(self, maximum=2000):
        total = await self.redis.zcard(self.order_key)
        if total <= maximum: return
        ids = await self.redis.zrange(self.order_key, 0, total - maximum - 1)
        pipe = self.redis.pipeline(transaction=True)
        for task_id in ids:
            task = await self.get(task_id)
            if task and task["status"] not in ACTIVE:
                pipe.delete(self.task_key(task_id))
                pipe.zrem(self.order_key, task_id)
                pipe.srem(self.status_key(task["status"]), task_id)
        await pipe.execute()

    async def start_workers(self, handler):
        await self.redis.ping()
        # One worker container owns this queue. N coroutines run within it.
        for task_id in await self.redis.lrange(self.processing_key, 0, -1):
            task = await self.get(task_id)
            if task and task["status"] == "running":
                await self.finish(task, "interrupted", error="Worker 停止时任务中断；未自动重试，请检查后手动重试")
            else:
                if task and task["status"] == "queued":
                    pipe = self.redis.pipeline(transaction=True)
                    pipe.lrem(self.processing_key, 0, task_id)
                    pipe.lpush(self.pending_key, task_id)
                    await pipe.execute()
                else:
                    await self.redis.lrem(self.processing_key, 0, task_id)
                if task and await self.redis.get(self.lock_key(task["filename"])) == task_id:
                    await self.redis.delete(self.lock_key(task["filename"]))
        self.worker_tasks = [asyncio.create_task(self.run_worker("worker-" + str(index + 1), handler))
                             for index in range(self.worker_count)]
        logging.info("Started %s workers", self.worker_count)

    async def stop_workers(self):
        for task in self.worker_tasks: task.cancel()
        await asyncio.gather(*self.worker_tasks, return_exceptions=True)
        self.worker_tasks.clear()
        await self.redis.aclose()

    async def claim(self, task_id, name):
        task = await self.get(task_id)
        if not task:
            await self.redis.lrem(self.processing_key, 1, task_id)
            return None
        script = self.redis.register_script("""
            local raw = redis.call('GET', KEYS[1])
            if not raw then redis.call('LREM', KEYS[2], 1, ARGV[1]); return 0 end
            local task = cjson.decode(raw)
            if task.status ~= 'queued' then
                redis.call('LREM', KEYS[2], 1, ARGV[1]); return 0
            end
            local first = redis.call('ZRANGE', KEYS[7], 0, 0)
            if redis.call('EXISTS', KEYS[3]) == 1 or (#first > 0 and first[1] ~= ARGV[1]) then
                redis.call('LREM', KEYS[2], 1, ARGV[1])
                redis.call('LPUSH', KEYS[4], ARGV[1]); return 0
            end
            redis.call('SET', KEYS[3], ARGV[1])
            task.status = 'running'; task.worker = ARGV[2]; task.started_at = ARGV[3]
            redis.call('SET', KEYS[1], cjson.encode(task))
            redis.call('SREM', KEYS[5], ARGV[1]); redis.call('SADD', KEYS[6], ARGV[1])
            redis.call('PUBLISH', ARGV[4], ARGV[1])
            return 1
        """)
        claimed = await script(keys=[self.task_key(task_id), self.processing_key, self.lock_key(task["filename"]),
                                     self.pending_key, self.status_key("queued"), self.status_key("running"), self.paper_key(task["filename"])],
                               args=[task_id, name, now(), self.events_key])
        return await self.get(task_id) if claimed else None

    async def run_worker(self, name, handler):
        while True:
            task_id = await self.redis.brpoplpush(self.pending_key, self.processing_key, timeout=2)
            if not task_id:
                continue
            task = await self.claim(task_id, name)
            if task is None:
                await asyncio.sleep(0.1)
                continue
            logging.info("%s started %s %s", name, task["action"], task_id)
            try:
                result = await handler(task)
                await self.finish(task, "completed", result=result)
            except SkipTask as error:
                await self.finish(task, "skipped", error=str(error))
            except asyncio.CancelledError:
                await self.finish(task, "interrupted", error="Worker 停止时任务中断；未自动重试，请检查后手动重试")
                raise
            except Exception as error:
                logging.exception("Task %s failed", task_id)
                detail = getattr(error, "detail", None) or str(error) or type(error).__name__
                await self.finish(task, "failed", error=str(detail))
            logging.info("%s finished %s (%s)", name, task_id, task["status"])
