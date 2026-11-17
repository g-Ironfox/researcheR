import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import fitz
import httpx

import app as papers_app
import markdown_service


class MarkdownTests(unittest.IsolatedAsyncioTestCase):
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
        self.pdf.with_suffix(".json").write_text(
            json.dumps({"title": "Test paper", "venue": "Conference"}), encoding="utf-8"
        )
        self.ocr = {
            "page_count": 2,
            "pages": [
                {"index": 0, "text": "First page full text", "layout": {"bbox": [1, 2, 3, 4]}},
                {"index": 1, "text": "Second page full text", "finish_reason": "stop"},
            ],
            "provider_extension": {"preserve": "all OCR fields"},
        }
        self.save_ocr()
        self.original_client = httpx.AsyncClient

    def save_ocr(self, status="completed"):
        self.pdf.with_suffix(".ocr.json").write_text(json.dumps({
            "status": status,
            "result": self.ocr,
            "error": None,
            "source": {"filename": "paper.pdf"},
        }), encoding="utf-8")

    def mock_model(self, handler):
        return patch(
            "markdown_service.httpx.AsyncClient",
            side_effect=lambda **kwargs: self.original_client(
                **kwargs, transport=httpx.MockTransport(handler)
            ),
        )

    @staticmethod
    def completion(content="# Test paper\n\nComplete Markdown.\n", finish_reason="stop"):
        return httpx.Response(200, json={"choices": [{
            "message": {"content": content}, "finish_reason": finish_reason,
        }]})

    async def test_generation_sends_entire_raw_ocr_and_keeps_source_json(self):
        captured = {}

        def handler(request):
            payload = json.loads(request.content)
            captured["payload"] = payload
            return self.completion()

        raw_before = self.pdf.with_suffix(".ocr.json").read_bytes()
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-key"}), self.mock_model(handler):
            response = await self.client.post("/api/papers/paper.pdf/markdown")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(captured["payload"]["model"], "deepseek-flash")
        user_content = captured["payload"]["messages"][1]["content"]
        for text in ("First page full text", "Second page full text", "provider_extension", "all OCR fields"):
            self.assertIn(text, user_content)
        self.assertEqual(self.pdf.with_suffix(".ocr.json").read_bytes(), raw_before)
        self.assertEqual(self.pdf.with_suffix(".md").read_text(encoding="utf-8"), "# Test paper\n\nComplete Markdown.\n")
        self.assertEqual(response.json()["status"], "ready")
        listing = (await self.client.get("/api/papers")).json()["papers"][0]
        self.assertTrue(listing["markdown_available"])

    async def test_existing_markdown_is_not_regenerated_without_force(self):
        self.pdf.with_suffix(".md").write_text("# Existing\n", encoding="utf-8")
        with patch.object(papers_app, "generate_markdown") as generate:
            response = await self.client.post("/api/papers/paper.pdf/markdown")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["content"], "# Existing\n")
        generate.assert_not_called()

    async def test_force_regenerate_preserves_previous_markdown_on_truncation(self):
        previous = "# Keep previous output\n"
        self.pdf.with_suffix(".md").write_text(previous, encoding="utf-8")
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-key"}), self.mock_model(
            lambda request: self.completion("# Truncated", "length")
        ):
            response = await self.client.post("/api/papers/paper.pdf/markdown?force=true")
        self.assertEqual(response.status_code, 502)
        self.assertEqual(self.pdf.with_suffix(".md").read_text(encoding="utf-8"), previous)
        self.assertTrue(self.pdf.with_suffix(".ocr.json").is_file())
        self.assertNotIn("paper.pdf", papers_app.markdown_in_progress)

    async def test_missing_renderer_returns_json_error_and_download_remains_available(self):
        previous = "# Saved Markdown\n"
        self.pdf.with_suffix(".md").write_text(previous, encoding="utf-8")
        with patch.dict("sys.modules", {"bleach": None}):
            response = await self.client.get("/api/papers/paper.pdf/markdown")
        self.assertEqual(response.status_code, 503)
        self.assertIn("detail", response.json())
        self.assertEqual(self.pdf.with_suffix(".md").read_text(encoding="utf-8"), previous)
        download = await self.client.get("/api/papers/paper.pdf/markdown/download")
        self.assertEqual(download.text, previous)

    async def test_render_failure_does_not_overwrite_existing_markdown(self):
        previous = "# Previous Markdown\n"
        self.pdf.with_suffix(".md").write_text(previous, encoding="utf-8")
        raw_ocr = self.pdf.with_suffix(".ocr.json").read_bytes()
        with patch.object(papers_app, "generate_markdown", return_value="# New Markdown\n"), patch.dict("sys.modules", {"bleach": None}):
            response = await self.client.post("/api/papers/paper.pdf/markdown?force=true")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.pdf.with_suffix(".md").read_text(encoding="utf-8"), previous)
        self.assertEqual(self.pdf.with_suffix(".ocr.json").read_bytes(), raw_ocr)
        self.assertNotIn("paper.pdf", papers_app.markdown_in_progress)

    async def test_missing_or_failed_ocr_is_rejected(self):
        self.pdf.with_suffix(".ocr.json").unlink()
        response = await self.client.post("/api/papers/paper.pdf/markdown")
        self.assertEqual(response.status_code, 409)
        self.save_ocr("failed")
        response = await self.client.post("/api/papers/paper.pdf/markdown")
        self.assertEqual(response.status_code, 409)

    async def test_missing_api_key_returns_error_without_creating_markdown(self):
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": ""}):
            response = await self.client.post("/api/papers/paper.pdf/markdown")
        self.assertEqual(response.status_code, 502)
        self.assertFalse(self.pdf.with_suffix(".md").exists())
        self.assertTrue(self.pdf.with_suffix(".ocr.json").exists())
        self.assertNotIn("paper.pdf", papers_app.markdown_in_progress)

    async def test_html_renderer_strips_script_tags_and_unsafe_links(self):
        html = markdown_service.render_markdown(
            "# Title\n\n<script>alert(1)</script>\n\n[bad](javascript:alert(2))\n\n| A | B |\n| - | - |\n| 1 | 2 |"
        )
        self.assertNotIn("<script>", html)
        self.assertNotIn("javascript:", html)
        self.assertIn("<h1", html)
        self.assertIn("<table>", html)

    async def test_markdown_and_pdf_readers_are_wired_and_delete_removes_markdown(self):
        self.pdf.with_suffix(".md").write_text("# Saved\n", encoding="utf-8")
        page = await self.client.get("/markdown/paper.pdf")
        self.assertEqual(page.status_code, 200)
        self.assertIn("markdown-prose", page.text)
        download = await self.client.get("/api/papers/paper.pdf/markdown/download")
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download.text, "# Saved\n")
        deleted = await self.client.delete("/api/papers/paper.pdf")
        self.assertEqual(deleted.status_code, 204)
        self.assertFalse(self.pdf.with_suffix(".md").exists())
        self.assertFalse(self.pdf.with_suffix(".ocr.json").exists())

    async def test_generation_is_single_flight_and_deletion_is_blocked(self):
        started = asyncio.Event()
        release = asyncio.Event()

        async def handler(request):
            started.set()
            await release.wait()
            return self.completion()

        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-key"}), self.mock_model(handler):
            task = asyncio.create_task(self.client.post("/api/papers/paper.pdf/markdown"))
            try:
                await asyncio.wait_for(started.wait(), timeout=3)
                status = (await self.client.get("/api/papers/paper.pdf/markdown")).json()
                self.assertEqual(status["status"], "generating")
                duplicate = await self.client.post("/api/papers/paper.pdf/markdown")
                self.assertEqual(duplicate.status_code, 409)
                deleted = await self.client.delete("/api/papers/paper.pdf")
                self.assertEqual(deleted.status_code, 409)
            finally:
                release.set()
                result = await task
        self.assertEqual(result.status_code, 200)
        self.assertNotIn("paper.pdf", papers_app.markdown_in_progress)


if __name__ == "__main__":
    unittest.main()
