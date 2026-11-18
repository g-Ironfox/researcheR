import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import app as papers_app
from task_queue import TaskQueue, SkipTask


class QueueTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.queue = TaskQueue(os.environ.get("TEST_REDIS_URL", "redis://redis:6379/0"), workers=3,
                               prefix="researcher:test:" + uuid4().hex + ":")
        try:
            await self.queue.redis.ping()
        except Exception:
            await self.queue.redis.aclose()
            self.skipTest("Redis unavailable; set TEST_REDIS_URL to run queue integration tests")
        self.addAsyncCleanup(self.cleanup_queue)
        self.options = {"skip_existing": True, "save_result": False, "force": False}

    async def cleanup_queue(self):
        for task in self.queue.worker_tasks: task.cancel()
        await asyncio.gather(*self.queue.worker_tasks, return_exceptions=True)
        keys = [key async for key in self.queue.redis.scan_iter(self.queue.prefix + "*")]
        if keys: await self.queue.redis.delete(*keys)
        await self.queue.redis.aclose()

    async def submit(self, filename="paper.pdf", action="ocr", **options):
        return await self.queue.submit(filename, filename, action, self.options | options)

    async def wait_status(self, task_id, statuses):
        async with asyncio.timeout(5):
            while True:
                task = await self.queue.get(task_id)
                if task["status"] in statuses: return task
                await asyncio.sleep(0.01)

    async def test_duplicate_submission_cancel_and_resubmit(self):
        first, second = await asyncio.gather(self.submit(), self.submit())
        self.assertEqual(first["id"], second["id"])
        with self.assertRaises(ValueError): await self.submit(skip_existing=False)
        self.assertTrue(await self.queue.cancel(first["id"]))
        self.assertFalse(await self.queue.cancel(first["id"]))
        self.assertEqual(await self.queue.active(), [])
        replacement = await self.submit()
        self.assertNotEqual(first["id"], replacement["id"])

    async def test_workers_bound_parallelism_and_serialize_each_paper(self):
        started = []
        release = asyncio.Event()
        async def handler(task):
            started.append((task["filename"], task["action"]))
            await release.wait()
            return {"ok": True}
        tasks = [await self.submit("one.pdf"), await self.submit("one.pdf", "references"),
                 await self.submit("two.pdf"), await self.submit("three.pdf"), await self.submit("four.pdf")]
        await self.queue.start_workers(handler)
        async with asyncio.timeout(5):
            while len(started) < 3: await asyncio.sleep(0.01)
        self.assertEqual(len(started), 3)
        self.assertEqual(len({name for name, _ in started}), 3)
        self.assertNotIn(("one.pdf", "references"), started)
        self.assertFalse(await self.queue.cancel(tasks[0]["id"]))
        release.set()
        for task in tasks: await self.wait_status(task["id"], {"completed"})
        self.assertLess(started.index(("one.pdf", "ocr")), started.index(("one.pdf", "references")))
        self.assertEqual((await self.queue.listing())["counts"]["completed"], 5)

    async def test_cancelled_task_cannot_be_claimed(self):
        task = await self.submit()
        await self.queue.redis.brpoplpush(self.queue.pending_key, self.queue.processing_key, timeout=1)
        self.assertTrue(await self.queue.cancel(task["id"]))
        self.assertIsNone(await self.queue.claim(task["id"], "worker-1"))
        self.assertEqual((await self.queue.get(task["id"]))["status"], "cancelled")

    async def test_failures_and_skips_do_not_stop_workers(self):
        async def handler(task):
            if task["filename"] == "bad.pdf": raise ValueError("provider failure")
            if task["filename"] == "skip.pdf": raise SkipTask("already exists")
            return {"content": "result"}
        tasks = [await self.submit(name) for name in ("bad.pdf", "skip.pdf", "good.pdf")]
        await self.queue.start_workers(handler)
        for task, expected in zip(tasks, ("failed", "skipped", "completed")):
            result = await self.wait_status(task["id"], {expected})
            self.assertEqual(result["status"], expected)
        self.assertEqual((await self.queue.get(tasks[-1]["id"]))["result"], {"content": "result"})
        self.assertNotIn("result", (await self.queue.listing())["tasks"][0])

    async def test_restart_interrupts_running_but_preserves_unstarted_task(self):
        running = await self.submit("running.pdf")
        await self.queue.redis.brpoplpush(self.queue.pending_key, self.queue.processing_key, timeout=1)
        await self.queue.claim(running["id"], "dead-worker")
        pending = await self.submit("pending.pdf")
        await self.queue.redis.brpoplpush(self.queue.pending_key, self.queue.processing_key, timeout=1)
        handler = AsyncMock(return_value={"ok": True})
        await self.queue.start_workers(handler)
        self.assertEqual((await self.queue.get(running["id"]))["status"], "interrupted")
        await self.wait_status(pending["id"], {"completed"})
        handler.assert_awaited_once()
        self.assertFalse(await self.queue.redis.exists(self.queue.lock_key("running.pdf")))

    async def test_sse_receives_cross_client_state_changes_and_reconnect_snapshot(self):
        publisher = TaskQueue(os.environ.get("TEST_REDIS_URL", "redis://redis:6379/0"), prefix=self.queue.prefix)
        self.addAsyncCleanup(publisher.redis.aclose)
        stream = self.queue.events(heartbeat=0.05)
        self.addAsyncCleanup(stream.aclose)
        self.assertEqual((await anext(stream))["tasks"], [])

        async def change(status):
            async with asyncio.timeout(3):
                while True:
                    snapshot = await anext(stream)
                    if snapshot is not None:
                        self.assertEqual(snapshot["changed"]["status"], status)
                        self.assertNotIn("result", snapshot["changed"])
                        return snapshot

        task = await publisher.submit("events.pdf", "Events", "ocr", self.options)
        self.assertEqual((await change("queued"))["changed"]["id"], task["id"])
        await publisher.redis.brpoplpush(publisher.pending_key, publisher.processing_key, timeout=1)
        running = await publisher.claim(task["id"], "other-container")
        self.assertEqual((await change("running"))["changed"]["worker"], "other-container")
        await publisher.finish(running, "completed", result={"large": "private result"})
        self.assertEqual((await change("completed"))["counts"]["completed"], 1)
        cancelled = await publisher.submit("cancel.pdf", "Cancel", "ocr", self.options)
        await change("queued")
        await publisher.cancel(cancelled["id"])
        await change("cancelled")
        await stream.aclose()
        self.assertEqual((await self.queue.redis.pubsub_numsub(self.queue.events_key))[0][1], 0)

        reconnect = self.queue.events()
        self.addAsyncCleanup(reconnect.aclose)
        snapshot = await anext(reconnect)
        self.assertEqual(snapshot["counts"]["completed"], 1)
        self.assertEqual(snapshot["counts"]["cancelled"], 1)

    async def test_sse_heartbeat_does_not_poll_task_storage(self):
        with patch.object(self.queue, "listing", wraps=self.queue.listing) as listing:
            stream = self.queue.events(heartbeat=0.01)
            try:
                await anext(stream)
                self.assertIsNone(await anext(stream))
                self.assertIsNone(await anext(stream))
                self.assertEqual(listing.await_count, 1)
            finally:
                await stream.aclose()

    async def test_actual_markdown_handler_executes_in_worker_without_requeue(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "paper.pdf"
            path.write_bytes(b"%PDF-1.4")
            path.with_suffix(".ocr.json").write_text(json.dumps({"status": "completed", "result": {"pages": []}}))
            with patch.object(papers_app, "UPLOAD_DIR", Path(directory)), patch.object(papers_app, "generate_markdown", new=AsyncMock(return_value="# Saved")), patch.object(papers_app, "render_paper_markdown", return_value="<h1>Saved</h1>"):
                queued = await self.submit(action="markdown")
                await self.queue.start_workers(papers_app.execute_task)
                result = await self.wait_status(queued["id"], {"completed", "failed"})
                self.assertEqual(result["status"], "completed", result.get("error"))
                self.assertEqual(path.with_suffix(".md").read_text(), "# Saved")
                self.assertEqual(result["result"]["status"], "ready")


if __name__ == "__main__":
    unittest.main()
