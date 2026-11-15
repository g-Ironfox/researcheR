import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import fitz
import httpx

import app as papers_app


class OcrRetryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.upload_dir = Path(self.directory.name)
        self.directory_patch = patch.object(papers_app, "UPLOAD_DIR", self.upload_dir)
        self.directory_patch.start()
        self.addCleanup(self.directory.cleanup)
        self.addCleanup(self.directory_patch.stop)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=papers_app.app), base_url="http://test"
        )
        self.addAsyncCleanup(self.client.aclose)
        self.pdf = self.upload_dir / "paper.pdf"
        with fitz.open() as pdf:
            pdf.new_page()
            pdf.save(self.pdf)
        self.metadata = {"title": "Test paper", "venue": "Conference", "starred": True}
        self.pdf.with_suffix(".json").write_text(json.dumps(self.metadata), encoding="utf-8")
        self.original_client = httpx.AsyncClient

    def provider(self, handler):
        return patch(
            "ocr.httpx.AsyncClient",
            side_effect=lambda **kwargs: self.original_client(
                **kwargs, transport=httpx.MockTransport(handler)
            ),
        )

    def previous_result(self, status):
        self.pdf.with_suffix(".ocr.json").write_text(
            json.dumps({
                "status": status, "error": "Previous error", "result": None,
                "source": {"original_filename": "original.pdf"},
            }), encoding="utf-8",
        )

    @staticmethod
    def success(request):
        return httpx.Response(200, json={
            "page_count": 1,
            "pages": [{"index": 0, "text": "Recognized text", "finish_reason": "stop"}],
        })

    async def test_retry_missing_failed_partial_unconfigured_or_corrupt_result(self):
        for status in (None, "failed", "partial", "not_configured", "corrupt"):
            with self.subTest(status=status):
                self.pdf.with_suffix(".ocr.json").unlink(missing_ok=True)
                if status == "corrupt":
                    self.pdf.with_suffix(".ocr.json").write_text("{broken", encoding="utf-8")
                elif status:
                    self.previous_result(status)
                with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-key"}), self.provider(self.success):
                    response = await self.client.post("/api/papers/paper.pdf/ocr")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["ocr_status"], "completed")
                self.assertTrue(response.json()["ocr_downloadable"])
                saved = (await self.client.get("/api/papers/paper.pdf/ocr")).json()
                self.assertEqual(saved["result"]["pages"][0]["text"], "Recognized text")
                self.assertEqual(saved["source"]["original_filename"],
                                 "original.pdf" if status in {"failed", "partial", "not_configured"} else "Test paper.pdf")
                self.assertTrue(self.pdf.is_file())
                self.assertEqual(json.loads(self.pdf.with_suffix(".json").read_text()), self.metadata)
                self.assertNotIn("paper.pdf", papers_app.ocr_in_progress)
                listing = (await self.client.get("/api/papers")).json()["papers"]
                self.assertEqual(listing[0]["ocr_status"], "completed")

    async def test_completed_result_is_not_submitted_again(self):
        self.previous_result("completed")
        with patch.object(papers_app, "recognize_paper") as recognize:
            response = await self.client.post("/api/papers/paper.pdf/ocr")
        self.assertEqual(response.status_code, 409)
        recognize.assert_not_called()

    async def test_missing_paper_returns_404(self):
        with patch.object(papers_app, "recognize_paper") as recognize:
            response = await self.client.post("/api/papers/missing.pdf/ocr")
        self.assertEqual(response.status_code, 404)
        recognize.assert_not_called()

    async def test_missing_configuration_keeps_pdf_and_allows_another_retry(self):
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": ""}):
            for _ in range(2):
                response = await self.client.post("/api/papers/paper.pdf/ocr")
                self.assertEqual(response.json()["ocr_status"], "not_configured")
                self.assertNotIn("paper.pdf", papers_app.ocr_in_progress)
        self.assertTrue(self.pdf.is_file())

    async def test_provider_failure_records_error_and_can_be_retried(self):
        def failure(request):
            return httpx.Response(503, json={"error": {"message": "Service unavailable"}})
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-key"}), self.provider(failure):
            response = await self.client.post("/api/papers/paper.pdf/ocr")
        self.assertEqual(response.json()["ocr_status"], "failed")
        self.assertIn("Service unavailable", response.json()["ocr_error"])
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-key"}), self.provider(self.success):
            response = await self.client.post("/api/papers/paper.pdf/ocr")
        self.assertEqual(response.json()["ocr_status"], "completed")

    async def test_incomplete_result_still_allows_retry(self):
        def partial(request):
            return httpx.Response(200, json={
                "page_count": 1,
                "pages": [{"index": 0, "text": "Truncated", "finish_reason": "length"}],
            })
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-key"}), self.provider(partial):
            response = await self.client.post("/api/papers/paper.pdf/ocr")
        self.assertEqual(response.json()["ocr_status"], "partial")
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-key"}), self.provider(self.success):
            response = await self.client.post("/api/papers/paper.pdf/ocr")
        self.assertEqual(response.json()["ocr_status"], "completed")

    async def test_concurrent_retry_and_delete_are_blocked_while_processing(self):
        started = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def slow_provider(request):
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            return self.success(request)

        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-key"}), self.provider(slow_provider):
            task = asyncio.create_task(self.client.post("/api/papers/paper.pdf/ocr"))
            try:
                await asyncio.wait_for(started.wait(), timeout=3)
                listing = (await self.client.get("/api/papers")).json()["papers"]
                self.assertEqual(listing[0]["ocr_status"], "processing")
                retry = await self.client.post("/api/papers/paper.pdf/ocr")
                self.assertEqual(retry.status_code, 409)
                delete = await self.client.delete("/api/papers/paper.pdf")
                self.assertEqual(delete.status_code, 409)
                self.assertEqual(calls, 1)
            finally:
                release.set()
                result = await task
        self.assertEqual(result.json()["ocr_status"], "completed")
        self.assertNotIn("paper.pdf", papers_app.ocr_in_progress)

    async def test_unexpected_failure_releases_processing_guard(self):
        with patch.object(papers_app, "recognize_paper", side_effect=OSError("Disk unavailable")):
            with self.assertRaises(OSError):
                await self.client.post("/api/papers/paper.pdf/ocr")
        self.assertNotIn("paper.pdf", papers_app.ocr_in_progress)
        self.assertTrue(self.pdf.is_file())

    async def test_upload_uses_the_same_processing_guard(self):
        started = asyncio.Event()
        release = asyncio.Event()

        async def slow_provider(request):
            started.set()
            await release.wait()
            return self.success(request)

        with fitz.open() as pdf:
            pdf.new_page(width=300)
            data = pdf.tobytes()
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-key"}), self.provider(slow_provider):
            task = asyncio.create_task(self.client.post("/upload", files={"files": ("upload.pdf", data, "application/pdf")}))
            try:
                await asyncio.wait_for(started.wait(), timeout=3)
                listing = (await self.client.get("/api/papers")).json()["papers"]
                uploaded = next(paper for paper in listing if paper["filename"] != "paper.pdf")
                self.assertEqual(uploaded["ocr_status"], "processing")
                retry = await self.client.post(f"/api/papers/{uploaded['filename']}/ocr")
                self.assertEqual(retry.status_code, 409)
            finally:
                release.set()
                result = await task
        self.assertEqual(result.json()["ocr_results"][0]["ocr_status"], "completed")


if __name__ == "__main__":
    unittest.main()
