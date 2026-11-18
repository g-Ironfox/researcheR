import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import httpx

import app as papers_app


class TaskEventEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_standalone_task_page_and_library_result_link(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=papers_app.app), base_url="http://test") as client:
            page = await client.get("/tasks")
            script = await client.get("/static/tasks.js")
            library = await client.get("/?task=some-task")
        self.assertEqual(page.status_code, 200)
        self.assertTrue(page.headers["content-type"].startswith("text/html"))
        self.assertIn('id="task-list"', page.text)
        self.assertIn('/static/tasks.js', page.text)
        self.assertEqual(script.status_code, 200)
        self.assertEqual(library.status_code, 200)
        self.assertIn('href="/tasks"', library.text)
        self.assertNotIn('id="task-list"', library.text)
        self.assertIn('id="apply-task-result"', library.text)

    async def test_stream_route_headers_snapshot_heartbeat_and_update(self):
        snapshot = {"tasks": [], "counts": {"running": 0}, "workers": 3}

        async def events(limit):
            self.assertEqual(limit, 7)
            yield snapshot
            yield None
            yield snapshot | {"changed": {"id": "one", "status": "completed", "title": "论文"}}

        queue = SimpleNamespace(events=events)
        with patch.object(papers_app.app.state, "task_queue", queue, create=True):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=papers_app.app), base_url="http://test") as client:
                response = await client.get("/api/tasks/events?limit=7")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-type"].startswith("text/event-stream"))
        self.assertEqual(response.headers["cache-control"], "no-cache")
        self.assertEqual(response.headers["x-accel-buffering"], "no")
        frames = response.text.strip().split("\n\n")
        self.assertEqual(json.loads(frames[0].split("data: ", 1)[1]), snapshot)
        self.assertEqual(frames[1], ": keepalive")
        self.assertEqual(json.loads(frames[2].split("data: ", 1)[1])["changed"]["title"], "论文")

    async def test_stream_without_queue_returns_503(self):
        with patch.object(papers_app.app.state, "task_queue", None, create=True):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=papers_app.app), base_url="http://test") as client:
                response = await client.get("/api/tasks/events")
        self.assertEqual(response.status_code, 503)
