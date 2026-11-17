import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import fitz
import httpx

import app as papers_app
from metadata_service import read_metadata_source


TITLE = "A Paper About Reliable Extraction"
ABSTRACT = "We present a reliable method. Experiments demonstrate its effectiveness."


class MetadataExtractionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.upload_dir = Path(directory.name)
        directory_patch = patch.object(papers_app, "UPLOAD_DIR", self.upload_dir)
        directory_patch.start()
        self.addCleanup(directory_patch.stop)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=papers_app.app), base_url="http://test"
        )
        self.addAsyncCleanup(self.client.aclose)
        self.pdf = self.upload_dir / "paper.pdf"
        with fitz.open() as pdf:
            pdf.new_page().insert_text((72, 72), TITLE + "\nAbstract\n" + ABSTRACT)
            pdf.new_page().insert_text((72, 72), "Introduction and methods.")
            pdf.new_page().insert_text((72, 72), "Results.")
            pdf.new_page().insert_text((72, 72), "References: exclude this fourth page.")
            pdf.save(self.pdf)
        self.metadata = {"title": "Old title", "abstract": "Old abstract", "venue": "My journal",
                         "reference": "Legacy text", "references": [], "starred": True}
        self.pdf.with_suffix(".json").write_text(json.dumps(self.metadata), encoding="utf-8")
        self.original_client = httpx.AsyncClient

    def provider(self, handler):
        return patch("markdown_service.httpx.AsyncClient", side_effect=lambda **kwargs:
                     self.original_client(**kwargs, transport=httpx.MockTransport(handler)))

    @staticmethod
    def completion(data=None, *, content=None, finish_reason="stop"):
        if content is None:
            content = json.dumps(data if data is not None else {
                "complete": True, "title": TITLE, "abstract": ABSTRACT,
            })
        return httpx.Response(200, json={"choices": [{
            "message": {"content": content}, "finish_reason": finish_reason,
        }]})

    async def test_extract_title_and_full_abstract_preview_then_save(self):
        captured = {}
        def handler(request):
            captured.update(json.loads(request.content))
            return self.completion()
        before = self.pdf.with_suffix(".json").read_bytes()
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-key"}), self.provider(handler):
            response = await self.client.post("/api/papers/paper.pdf/metadata/extract")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["title"], TITLE)
        self.assertEqual(response.json()["abstract"], ABSTRACT)
        self.assertEqual(response.json()["source_pages"], [1, 2, 3])
        self.assertEqual(self.pdf.with_suffix(".json").read_bytes(), before)
        sent = json.loads(captured["messages"][1]["content"])
        self.assertEqual(len(sent["pages"]), 3)
        self.assertNotIn("exclude this fourth page", captured["messages"][1]["content"])
        payload = {**self.metadata, "title": TITLE, "abstract": ABSTRACT}
        saved = await self.client.put("/api/papers/paper.pdf", json=payload)
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json()["abstract"], ABSTRACT)
        self.assertEqual(saved.json()["venue"], "My journal")
        self.assertTrue(saved.json()["starred"])
        self.assertEqual((await self.client.get("/api/papers/paper.pdf")).json()["title"], TITLE)
        self.assertNotIn("paper.pdf", papers_app.metadata_in_progress)

    def test_ocr_preferred_and_pdf_fills_gaps_without_truncating_abstract(self):
        long_text = "原标题\n摘要\n" + "很长的摘要内容。" * 1500
        self.pdf.with_suffix(".ocr.json").write_text(json.dumps({
            "status": "partial", "result": {"pages": [{"index": 0, "text": long_text}]},
        }), encoding="utf-8")
        source = read_metadata_source(self.pdf)
        self.assertEqual(source["pages"][0]["text"], long_text)
        self.assertIn("Introduction", source["pages"][1]["text"])
        self.assertTrue(source["warnings"])

    async def test_chinese_title_abstract_and_whitespace_are_preserved(self):
        self.pdf.with_suffix(".ocr.json").write_text(json.dumps({
            "status": "completed", "result": {"pages": [{"index": 0,
            "text": "可靠提取方法研究\n摘要\n我们提出一种可靠方法。\n实验验证了有效性。"}]},
        }), encoding="utf-8")
        result = {"complete": True, "title": "可靠提取方法研究", "abstract": "我们提出一种可靠方法。 实验验证了有效性。"}
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-key"}), self.provider(lambda r: self.completion(result)):
            response = await self.client.post("/api/papers/paper.pdf/metadata/extract")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["abstract"], result["abstract"])

    async def test_absent_fields_return_empty_without_touching_saved_values(self):
        before = self.pdf.with_suffix(".json").read_bytes()
        for result in ({"complete": True, "title": TITLE, "abstract": ""}, {"complete": True}):
            with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-key"}), self.provider(lambda r: self.completion(result)):
                response = await self.client.post("/api/papers/paper.pdf/metadata/extract")
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["abstract"], "")
            self.assertEqual(self.pdf.with_suffix(".json").read_bytes(), before)

    async def test_invalid_fabricated_and_incomplete_results_preserve_existing_values(self):
        before = self.pdf.with_suffix(".json").read_bytes()
        replies = [self.completion(content="invalid"), self.completion(content="[]"),
                   self.completion({"complete": False, "title": TITLE}),
                   self.completion({"complete": True, "title": [TITLE]}),
                   self.completion({"complete": True, "abstract": None}),
                   self.completion({"complete": True, "title": "Invented paper"}),
                   self.completion({"complete": True, "title": TITLE, "abstract": "Invented summary"}),
                   self.completion(finish_reason="length"),
                   httpx.Response(500, json={"error": {"message": "Provider failed"}})]
        for reply in replies:
            with self.subTest(reply=reply.text):
                with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-key"}), self.provider(lambda r: reply):
                    response = await self.client.post("/api/papers/paper.pdf/metadata/extract")
                self.assertEqual(response.status_code, 502, response.text)
                self.assertEqual(self.pdf.with_suffix(".json").read_bytes(), before)
                self.assertNotIn("paper.pdf", papers_app.metadata_in_progress)

    async def test_missing_key_and_timeout_release_processing_guard(self):
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": ""}):
            response = await self.client.post("/api/papers/paper.pdf/metadata/extract")
        self.assertEqual(response.status_code, 502)
        self.assertIn("DEEPSEEK_API_KEY", response.json()["detail"])
        def timeout(request):
            raise httpx.ReadTimeout("Timeout", request=request)
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-key"}), self.provider(timeout):
            response = await self.client.post("/api/papers/paper.pdf/metadata/extract")
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("paper.pdf", papers_app.metadata_in_progress)

    async def test_no_readable_opening_pages_requires_ocr(self):
        blank = self.upload_dir / "blank.pdf"
        with fitz.open() as pdf:
            for i in range(3):
                pdf.new_page()
            pdf.new_page().insert_text((72, 72), "Readable text only on fourth page")
            pdf.save(blank)
        with patch.object(papers_app, "extract_paper_metadata") as extract:
            response = await self.client.post("/api/papers/blank.pdf/metadata/extract")
        self.assertEqual(response.status_code, 422)
        self.assertIn("OCR", response.json()["detail"])
        extract.assert_not_called()

    async def test_concurrent_extraction_ocr_and_delete_are_guarded(self):
        started, release = asyncio.Event(), asyncio.Event()
        async def extract(source):
            started.set()
            await release.wait()
            return {"title": TITLE, "abstract": ABSTRACT}
        with patch.object(papers_app, "extract_paper_metadata", side_effect=extract):
            first = asyncio.create_task(self.client.post("/api/papers/paper.pdf/metadata/extract"))
            try:
                await asyncio.wait_for(started.wait(), 3)
                for method, suffix in (("POST", "/metadata/extract"), ("DELETE", ""), ("POST", "/ocr")):
                    response = await self.client.request(method, "/api/papers/paper.pdf" + suffix)
                    self.assertEqual(response.status_code, 409, response.text)
            finally:
                release.set()
                response = await first
            self.assertEqual(response.status_code, 200)
        papers_app.ocr_in_progress.add("paper.pdf")
        try:
            response = await self.client.post("/api/papers/paper.pdf/metadata/extract")
            self.assertEqual(response.status_code, 409)
        finally:
            papers_app.ocr_in_progress.discard("paper.pdf")

    async def test_missing_paper_is_404(self):
        response = await self.client.post("/api/papers/missing.pdf/metadata/extract")
        self.assertEqual(response.status_code, 404)

    async def test_fenced_json_is_accepted(self):
        content = json.dumps({"complete": True, "title": TITLE, "abstract": ABSTRACT})
        fence = chr(96) * 3
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-key"}), self.provider(
            lambda r: self.completion(content=fence + "json\n" + content + "\n" + fence)
        ):
            response = await self.client.post("/api/papers/paper.pdf/metadata/extract")
        self.assertEqual(response.status_code, 200, response.text)


if __name__ == "__main__":
    unittest.main()
