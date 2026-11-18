import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import fitz
import httpx

import app as papers_app
from reference_service import make_chunks, merge_entries, parse_entries, read_reference_source


RAW_ONE = "[1] Alice Smith. First referenced work. Example Journal, 2024."
RAW_TWO = "[2] Bob Lee and Carol Lin. Second referenced work. Conference, 2023."


def entry(raw=RAW_ONE, number="1", title="First referenced work", page=5):
    return {"number": number, "title": title, "authors": ["Alice Smith"],
            "year": "2024", "venue": "Example Journal", "raw": raw,
            "source_pages": [page]}


class ReferenceTests(unittest.IsolatedAsyncioTestCase):
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
            for index in range(4):
                pdf.new_page().insert_text((72, 72), f"Body page {index + 1}. The uploaded paper itself.")
            pdf.new_page().insert_text((72, 72), "References\n" + RAW_ONE + "\n" + RAW_TWO)
            pdf.save(self.pdf)
        self.metadata = {"title": "Existing title", "reference": "Legacy BibTeX text", "starred": True,
                         "references": [entry(raw="Existing raw reference", title="Existing reference")]}
        self.pdf.with_suffix(".json").write_text(json.dumps(self.metadata), encoding="utf-8")
        self.original_client = httpx.AsyncClient

    def provider(self, handler):
        return patch("markdown_service.httpx.AsyncClient", side_effect=lambda **kwargs:
                     self.original_client(**kwargs, transport=httpx.MockTransport(handler)))

    @staticmethod
    def completion(references=None, *, content=None, complete=True, finish_reason="stop"):
        if content is None:
            content = json.dumps({"complete": complete, "references": references if references is not None else [
                entry(), entry(RAW_TWO, "2", "Second referenced work"),
            ]})
        return httpx.Response(200, json={"choices": [{
            "message": {"content": content}, "finish_reason": finish_reason,
        }]})

    async def test_extract_all_bibliography_entries_from_late_page_preview_then_save(self):
        captured = {}

        def handler(request):
            captured.update(json.loads(request.content))
            return self.completion()

        before = self.pdf.with_suffix(".json").read_bytes()
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-key"}), self.provider(handler):
            response = await self.client.post("/api/papers/paper.pdf/references")
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["count"], 2)
        self.assertEqual(result["total_pages"], 5)
        self.assertEqual(result["warnings"], [])
        self.assertEqual([r["title"] for r in result["references"]], ["First referenced work", "Second referenced work"])
        self.assertEqual(result["references"][0]["source_pages"], [5])
        self.assertEqual(result["references"][0]["doi"], "")
        sent = json.loads(captured["messages"][1]["content"])
        self.assertEqual([p["page"] for p in sent["pages"]], [1, 2, 3, 4, 5])
        self.assertIn(RAW_TWO, sent["pages"][-1]["text"])
        self.assertEqual(self.pdf.with_suffix(".json").read_bytes(), before)
        saved = await self.client.put("/api/papers/paper.pdf", json={
            "title": "Existing title", "reference": "Legacy BibTeX text", "references": result["references"],
        })
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(saved.json()["references"], result["references"])
        self.assertTrue(saved.json()["starred"])
        loaded = await self.client.get("/api/papers/paper.pdf")
        self.assertEqual(loaded.json()["references"], result["references"])
        starred = await self.client.put("/api/papers/paper.pdf/star", json={"starred": False})
        self.assertEqual(starred.status_code, 200)
        self.assertEqual((await self.client.get("/api/papers/paper.pdf")).json()["references"], result["references"])
        self.assertNotIn("paper.pdf", papers_app.reference_in_progress)

    async def test_omitted_references_preserve_list_and_explicit_empty_clears_it(self):
        response = await self.client.put("/api/papers/paper.pdf", json={"title": "Updated"})
        self.assertEqual(len(response.json()["references"]), 1)
        response = await self.client.put("/api/papers/paper.pdf", json={"title": "Updated", "references": []})
        self.assertEqual(response.json()["references"], [])

    async def test_old_reference_text_remains_separate_from_structured_list(self):
        self.pdf.with_suffix(".json").write_text(json.dumps({"title": "Old", "reference": "@misc{old}"}))
        response = await self.client.get("/api/papers/paper.pdf")
        self.assertEqual(response.json()["reference"], "@misc{old}")
        self.assertEqual(response.json()["references"], [])

    async def test_library_reports_batch_skip_and_in_progress_flags(self):
        self.metadata["references"] = [entry()]
        self.pdf.with_suffix(".json").write_text(json.dumps(self.metadata), encoding="utf-8")
        self.pdf.with_suffix(".md").write_text("# Existing markdown", encoding="utf-8")
        papers_app.reference_in_progress.add("paper.pdf")
        papers_app.markdown_in_progress.add("paper.pdf")
        try:
            response = await self.client.get("/api/papers")
        finally:
            papers_app.reference_in_progress.discard("paper.pdf")
            papers_app.markdown_in_progress.discard("paper.pdf")
        paper = response.json()["papers"][0]
        self.assertEqual(paper["references_count"], 1)
        self.assertTrue(paper["references_processing"])
        self.assertTrue(paper["markdown_available"])
        self.assertTrue(paper["markdown_processing"])

    def test_all_ocr_pages_used_untruncated_and_pdf_fills_missing_pages(self):
        long_text = "References\n" + ("Long reference text.\n" * 1000)
        self.pdf.with_suffix(".ocr.json").write_text(json.dumps({
            "status": "partial", "result": {"page_count": 5, "pages": [
                {"index": 4, "text": long_text}, {"index": 0, "text": "OCR first page"},
            ]},
        }), encoding="utf-8")
        source = read_reference_source(self.pdf)
        self.assertEqual([p["page"] for p in source["pages"]], [1, 2, 3, 4, 5])
        self.assertEqual(source["pages"][0]["text"], "OCR first page")
        self.assertEqual(source["pages"][-1]["text"], long_text)
        self.assertIn("Body page 2", source["pages"][1]["text"])
        self.assertTrue(source["warnings"])

    def test_chunks_cover_every_character_with_overlap_and_provenance(self):
        text = "".join(f"unique source line {index:05d}\n" for index in range(4000))
        pages = [{"page": 1, "text": text[:20000]}, {"page": 2, "text": text[20000:]}]
        chunks = make_chunks(pages)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(sum(len(p["text"]) for p in chunk), 24000)
        for line in text.splitlines():
            self.assertTrue(any(line in "".join(p["text"] for p in chunk) for chunk in chunks), line)
        self.assertTrue(any(p["page"] == 2 for p in chunks[-1]))

    def test_malformed_ocr_falls_back_to_pdf(self):
        for raw in ("{broken", "[]", '{"status":"completed","result":{"pages":null}}',
                    '{"status":"completed","result":{"pages":[null,{"text":12}]}}'):
            with self.subTest(raw=raw):
                self.pdf.with_suffix(".ocr.json").write_text(raw)
                self.assertEqual(read_reference_source(self.pdf)["total_pages"], 5)

    async def test_scanned_pdf_requires_ocr_and_missing_pages_warn(self):
        blank = self.upload_dir / "blank.pdf"
        with fitz.open() as pdf:
            pdf.new_page()
            pdf.new_page()
            pdf.save(blank)
        with patch.object(papers_app, "extract_references") as extract:
            response = await self.client.post("/api/papers/blank.pdf/references")
        self.assertEqual(response.status_code, 422)
        extract.assert_not_called()
        blank.with_suffix(".ocr.json").write_text(json.dumps({
            "status": "partial", "result": {"pages": [{"index": 1, "text": RAW_ONE, "finish_reason": "length"}]},
        }))
        source = read_reference_source(blank)
        self.assertEqual(source["pages"][0]["page"], 2)
        self.assertGreaterEqual(len(source["warnings"]), 3)

    async def test_model_failures_preserve_saved_references_and_release_lock(self):
        replies = [self.completion(content="invalid"), self.completion(content="[]"),
                   self.completion(complete=False), self.completion([{"title": "No raw"}]),
                   self.completion([entry(raw="Fabricated source text")]),
                   self.completion([{**entry(), "authors": "Not an array"}]),
                   self.completion([{**entry(), "partial": True}]),
                   self.completion(finish_reason="length"),
                   httpx.Response(500, json={"error": {"message": "provider error"}})]
        before = self.pdf.with_suffix(".json").read_bytes()
        for reply in replies:
            with self.subTest(reply=reply.text):
                with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-key"}), self.provider(lambda r: reply):
                    response = await self.client.post("/api/papers/paper.pdf/references")
                self.assertEqual(response.status_code, 502, response.text)
                self.assertEqual(self.pdf.with_suffix(".json").read_bytes(), before)
                self.assertNotIn("paper.pdf", papers_app.reference_in_progress)

    async def test_missing_configuration_is_reported(self):
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": ""}):
            response = await self.client.post("/api/papers/paper.pdf/references")
        self.assertEqual(response.status_code, 502)
        self.assertIn("DEEPSEEK_API_KEY", response.json()["detail"])
        self.assertNotIn("paper.pdf", papers_app.reference_in_progress)

    async def test_empty_result_does_not_erase_saved_entries(self):
        before = self.pdf.with_suffix(".json").read_bytes()
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-key"}), self.provider(lambda r: self.completion([])):
            response = await self.client.post("/api/papers/paper.pdf/references")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["references"], [])
        self.assertEqual(self.pdf.with_suffix(".json").read_bytes(), before)

    async def test_duplicate_requests_ocr_and_delete_are_blocked(self):
        started, release = asyncio.Event(), asyncio.Event()
        async def extract(source):
            started.set()
            await release.wait()
            return []
        with patch.object(papers_app, "extract_references", side_effect=extract):
            first = asyncio.create_task(self.client.post("/api/papers/paper.pdf/references"))
            try:
                await asyncio.wait_for(started.wait(), 3)
                for method, url in (("POST", "/references"), ("DELETE", ""), ("POST", "/ocr")):
                    # OCR normally rejects completed results separately; here there is no OCR cache.
                    response = await self.client.request(method, "/api/papers/paper.pdf" + url)
                    self.assertEqual(response.status_code, 409, response.text)
            finally:
                release.set()
                result = await first
            self.assertEqual(result.status_code, 200)
        papers_app.ocr_in_progress.add("paper.pdf")
        try:
            response = await self.client.post("/api/papers/paper.pdf/references")
            self.assertEqual(response.status_code, 409)
        finally:
            papers_app.ocr_in_progress.discard("paper.pdf")

    async def test_missing_paper_and_invalid_structured_save(self):
        response = await self.client.post("/api/papers/missing.pdf/references")
        self.assertEqual(response.status_code, 404)
        response = await self.client.put("/api/papers/paper.pdf", json={"title": "T", "references": [{"title": "No raw"}]})
        self.assertEqual(response.status_code, 422)

    def test_entries_are_ordered_by_source_not_model_order(self):
        chunk = [{"page": 5, "text": RAW_ONE + "\n" + RAW_TWO}]
        result = parse_entries(json.dumps({"complete": True, "references": [
            entry(RAW_TWO, "2", "Second referenced work"), entry(),
        ]}), chunk)
        self.assertEqual([item["number"] for item in result], ["1", "2"])

    def test_merge_overlaps_keeps_distinct_numbers_and_unparsed_entries(self):
        chunk = [{"page": 5, "text": RAW_ONE + "\n" + RAW_TWO}]
        full = parse_entries(json.dumps({"complete": True, "references": [entry()]}), chunk)[0]
        fragment = {**full, "raw": "First referenced work.", "partial": True}
        second = {**full, "raw": RAW_TWO, "number": "2", "title": ""}
        result = merge_entries([fragment, full, full, second])
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["raw"], RAW_ONE)
        self.assertEqual(result[1]["number"], "2")
        self.assertEqual(result[1]["title"], "")
        self.assertNotIn("partial", result[0])

    def test_cross_page_provenance_is_derived_even_when_model_pages_are_wrong(self):
        chunk = [{"page": 5, "text": "[3] Author. A long"}, {"page": 6, "text": "reference title. 2024."}]
        item = entry(raw="[3] Author. A long reference title. 2024.", page=5)
        item["source_pages"] = [5, 6]
        data = {"complete": True, "references": [item]}
        self.assertEqual(parse_entries(json.dumps(data), chunk)[0]["source_pages"], [5, 6])
        item["source_pages"] = [99]
        self.assertEqual(parse_entries(json.dumps(data), chunk)[0]["source_pages"], [5, 6])


    async def test_multiple_chunks_merge_overlaps_and_keep_all_late_entries(self):
        long_source = {
            "pages": [{"page": 1, "text": "Body text.\n" * 2600},
                      {"page": 2, "text": "References\n" + RAW_ONE + "\n" + RAW_TWO}],
            "warnings": [], "total_pages": 2,
        }
        calls = []
        def handler(request):
            payload = json.loads(request.content)
            source = json.loads(payload["messages"][1]["content"])
            calls.append(source)
            text = "\n".join(page["text"] for page in source["pages"])
            items = []
            if RAW_ONE in text:
                items.append(entry(page=2))
            if RAW_TWO in text:
                items.append(entry(RAW_TWO, "2", "Second referenced work", page=2))
            return self.completion(items)
        with patch.object(papers_app, "read_reference_source", return_value=long_source), \
             patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-key"}), self.provider(handler):
            response = await self.client.post("/api/papers/paper.pdf/references")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertGreater(len(calls), 1)
        self.assertEqual(response.json()["count"], 2)
        self.assertEqual(response.json()["references"][-1]["number"], "2")

    async def test_later_chunk_failure_is_atomic(self):
        source = {"pages": [{"page": 1, "text": RAW_ONE + "\n" + "Body. " * 8000}],
                  "warnings": [], "total_pages": 1}
        before = self.pdf.with_suffix(".json").read_bytes()
        calls = []
        def handler(request):
            calls.append(request)
            return self.completion([entry(page=1)]) if len(calls) == 1 else self.completion(complete=False)
        with patch.object(papers_app, "read_reference_source", return_value=source), \
             patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-key"}), self.provider(handler):
            response = await self.client.post("/api/papers/paper.pdf/references")
        self.assertEqual(response.status_code, 502)
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.pdf.with_suffix(".json").read_bytes(), before)
        self.assertNotIn("paper.pdf", papers_app.reference_in_progress)


if __name__ == "__main__":
    unittest.main()
