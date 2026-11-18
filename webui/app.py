from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4
from hashlib import sha256
import json
import os
from contextlib import asynccontextmanager

from task_queue import TaskQueue, SkipTask

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import fitz
from pypdf import PdfReader

from metadata_service import extract_paper_metadata, read_metadata_source
from reference_service import ReferenceEntry, extract_references, read_reference_source
from ocr import read_ocr_summary, recognize_paper
from markdown_service import generate_markdown, render_markdown, translate_markdown, write_markdown


UPLOAD_DIR = Path(__file__).parent / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

metadata_in_progress: set[str] = set()
reference_in_progress: set[str] = set()
ocr_in_progress: set[str] = set()
markdown_in_progress: set[str] = set()
translation_in_progress: set[str] = set()
MarkdownLanguage = Literal["original", "zh"]

@asynccontextmanager
async def lifespan(app):
    workers = int(os.environ.get("TASK_WORKERS", "3"))
    if not 1 <= workers <= 32:
        raise ValueError("TASK_WORKERS must be between 1 and 32")
    queue = TaskQueue(workers=workers)
    await queue.redis.ping()
    app.state.task_queue = queue
    try:
        yield
    finally:
        app.state.task_queue = None
        await queue.redis.aclose()


app = FastAPI(title="论文库", lifespan=lifespan)

def task_queue():
    queue = getattr(app.state, "task_queue", None)
    if queue is None:
        raise HTTPException(status_code=503, detail="后台任务服务尚未启动")
    return queue


app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")


class PaperMetadata(BaseModel):
    title: str
    venue: str = ""
    abstract: str = ""
    reference: str = ""
    references: list[ReferenceEntry] | None = None


class PaperStar(BaseModel):
    starred: bool


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(Path(__file__).parent / "static" / "index.html")


@app.get("/read/{filename}")
async def read_paper(filename: str) -> FileResponse:
    get_paper_path(filename)
    return FileResponse(Path(__file__).parent / "static" / "reader.html")


@app.get("/markdown/{filename}")
async def read_paper_markdown(filename: str) -> FileResponse:
    get_paper_path(filename)
    return FileResponse(Path(__file__).parent / "static" / "markdown.html")


@app.get("/api/papers")
async def list_papers(
    page: int = Query(1, ge=1),
    query: str = Query("", alias="q"),
    starred: bool = False,
) -> dict[str, int | list[dict[str, str | int | bool]]]:
    papers = sorted(UPLOAD_DIR.glob("*.pdf"), key=lambda path: path.stat().st_mtime, reverse=True)
    matches = []
    normalized_query = query.strip().casefold()
    for paper in papers:
        metadata = read_paper_metadata(paper)
        if normalized_query and normalized_query not in str(metadata["title"]).casefold():
            continue
        if starred and not metadata["starred"]:
            continue
        matches.append({
            "name": metadata["title"],
            "venue": metadata["venue"],
            "starred": metadata["starred"],
            "filename": paper.name,
            "size_kb": round(paper.stat().st_size / 1024),
            **read_ocr_summary(paper),
            "markdown_available": paper.with_suffix(".md").is_file(),
            "references_count": len(metadata["references"]),
            "references_processing": paper.name in reference_in_progress,
            **({"ocr_status": "processing"} if paper.name in ocr_in_progress else {}),
            **({"markdown_processing": True} if paper.name in markdown_in_progress else {}),
        })
    queue = getattr(app.state, "task_queue", None)
    active = await queue.active() if queue else []
    for paper in matches:
        tasks = [task for task in active if task["filename"] == paper["filename"]]
        actions = {task["action"] for task in tasks}
        if "ocr" in actions:
            paper["ocr_status"] = "processing"
        paper["references_processing"] = paper["references_processing"] or "references" in actions
        paper["markdown_processing"] = bool(paper.get("markdown_processing")) or bool(actions & {"markdown", "translation"})
    papers_per_page = 20
    total = len(matches)
    page_count = max(1, (total + papers_per_page - 1) // papers_per_page)
    page = min(page, page_count)
    page_start = (page - 1) * papers_per_page
    return {
        "papers": matches[page_start:page_start + papers_per_page],
        "total": total,
        "page": page,
        "page_count": page_count,
    }


def read_paper_metadata(paper: Path) -> dict:
    metadata_path = paper.with_suffix(".json")
    if not metadata_path.is_file():
        return {"title": paper.stem, "venue": "", "abstract": "", "reference": "", "references": [], "starred": False}
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"title": paper.stem, "venue": "", "abstract": "", "reference": "", "references": [], "starred": False}
    if not isinstance(metadata, dict):
        metadata = {}
    return {
        "title": metadata.get("title") or metadata.get("name") or paper.stem,
        "venue": metadata.get("venue", ""),
        "abstract": metadata.get("abstract", ""),
        "reference": metadata.get("reference", ""),
        "references": metadata.get("references", []) if isinstance(metadata.get("references", []), list) else [],
        "starred": metadata.get("starred", False),
    }


def get_paper_path(filename: str) -> Path:
    if Path(filename).name != filename or not filename.endswith(".pdf"):
        raise HTTPException(status_code=404, detail="论文不存在")
    path = UPLOAD_DIR / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="论文不存在")
    return path


async def run_paper_ocr(path: Path, original_filename: str) -> dict[str, str | bool]:
    if path.name in metadata_in_progress:
        raise HTTPException(status_code=409, detail="正在提取标题和摘要，请完成后再 OCR")
    if path.name in reference_in_progress:
        raise HTTPException(status_code=409, detail="正在提取参考文献，请完成后再 OCR")
    if path.name in translation_in_progress:
        raise HTTPException(status_code=409, detail="这篇论文正在翻译，请等待完成后再 OCR")
    if path.name in markdown_in_progress:
        raise HTTPException(status_code=409, detail="这篇论文正在生成 Markdown，请等待完成后再 OCR")
    if path.name in ocr_in_progress:
        raise HTTPException(status_code=409, detail="这篇论文正在 OCR，请等待识别完成")
    ocr_in_progress.add(path.name)
    try:
        summary = await recognize_paper(path, original_filename)
        path.with_suffix(".md").unlink(missing_ok=True)
        path.with_suffix(".zh.md").unlink(missing_ok=True)
        return summary
    finally:
        ocr_in_progress.discard(path.name)


@app.post("/upload")
async def upload(files: Annotated[list[UploadFile], File()], background: bool = Query(True)) -> dict:
    if not files:
        raise HTTPException(status_code=400, detail="请至少选择一个 PDF 文件")

    headers: list[bytes] = []
    for file in files:
        if not file.filename or Path(file.filename).suffix.lower() != ".pdf":
            raise HTTPException(status_code=400, detail="请只上传 PDF 文件")
        header = await file.read(5)
        if header != b"%PDF-":
            raise HTTPException(status_code=400, detail="文件内容不是有效的 PDF")
        headers.append(header)

    hashes: set[bytes] = set()
    for paper in UPLOAD_DIR.glob("*.pdf"):
        digest = sha256()
        with paper.open("rb") as existing:
            while chunk := existing.read(1024 * 1024):
                digest.update(chunk)
        hashes.add(digest.digest())

    filenames: list[str] = []
    duplicates: list[str] = []
    ocr_results = []
    tasks = []
    for file, header in zip(files, headers):
        destination = UPLOAD_DIR / f"{uuid4().hex}.pdf"
        digest = sha256()
        digest.update(header)
        with destination.open("wb") as output:
            output.write(header)
            while chunk := await file.read(1024 * 1024):
                output.write(chunk)
                digest.update(chunk)
        if digest.digest() in hashes:
            destination.unlink()
            duplicates.append(file.filename)
            continue
        hashes.add(digest.digest())
        try:
            title = (PdfReader(destination).metadata.title or "").strip()
        except Exception:
            title = ""
        fallback_name = Path(file.filename).stem
        destination.with_suffix(".json").write_text(
            json.dumps(
                {"title": title or fallback_name, "venue": "", "abstract": "", "reference": ""},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        filenames.append(destination.name)
        if background and getattr(app.state, "task_queue", None):
            tasks.append(await enqueue_task(TaskRequest(filename=destination.name, action="ocr", skip_existing=True)))
        else:
            summary = await run_paper_ocr(destination, file.filename)
            ocr_results.append({"filename": destination.name, "name": file.filename, **summary})
    return {"filenames": filenames, "duplicates": duplicates, "ocr_results": ocr_results, "tasks": tasks}


@app.get("/api/papers/{filename}")
async def get_paper_metadata(filename: str) -> dict:
    return read_paper_metadata(get_paper_path(filename))


@app.post("/api/papers/{filename}/metadata/extract")
async def extract_paper_details(filename: str) -> dict:
    path = get_paper_path(filename)
    if filename in ocr_in_progress:
        raise HTTPException(status_code=409, detail="OCR 尚未完成，请稍后提取标题和摘要")
    if filename in metadata_in_progress:
        raise HTTPException(status_code=409, detail="标题和摘要正在提取，请稍候")
    try:
        source = read_metadata_source(path)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    metadata_in_progress.add(filename)
    try:
        result = await extract_paper_metadata(source)
        return {**result, "warnings": source["warnings"],
                "source_pages": [page["page"] for page in source["pages"]]}
    except ValueError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    finally:
        metadata_in_progress.discard(filename)


@app.post("/api/papers/{filename}/reference", include_in_schema=False)
@app.post("/api/papers/{filename}/references")
async def create_paper_references(filename: str) -> dict:
    path = get_paper_path(filename)
    if filename in ocr_in_progress:
        raise HTTPException(status_code=409, detail="OCR 尚未完成，请稍后提取参考文献")
    if filename in reference_in_progress:
        raise HTTPException(status_code=409, detail="参考文献正在提取，请稍候")
    try:
        source = read_reference_source(path)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    reference_in_progress.add(filename)
    try:
        references = await extract_references(source)
        return {"references": references, "count": len(references),
                "warnings": source["warnings"], "total_pages": source["total_pages"]}
    except ValueError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    finally:
        reference_in_progress.discard(filename)


@app.put("/api/papers/{filename}")
async def update_paper_metadata(filename: str, metadata: PaperMetadata) -> dict:
    path = get_paper_path(filename)
    existing = read_paper_metadata(path)
    data = metadata.model_dump()
    if metadata.references is None:
        data["references"] = existing["references"]
    data["starred"] = existing["starred"]
    path.with_suffix(".json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


@app.put("/api/papers/{filename}/star")
async def update_paper_star(filename: str, star: PaperStar) -> dict[str, bool]:
    path = get_paper_path(filename)
    data = read_paper_metadata(path)
    data["starred"] = star.starred
    path.with_suffix(".json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return {"starred": star.starred}


@app.delete("/api/papers/{filename}", status_code=204)
async def delete_paper(filename: str) -> None:
    path = get_paper_path(filename)
    queue = getattr(app.state, "task_queue", None)
    if queue and await queue.active(filename):
        raise HTTPException(status_code=409, detail="论文有排队或运行中的后台任务，请等待完成或取消排队任务")
    if filename in metadata_in_progress:
        raise HTTPException(status_code=409, detail="正在提取标题和摘要，请完成后再删除")
    if filename in reference_in_progress:
        raise HTTPException(status_code=409, detail="正在提取参考文献，请完成后再删除")
    if filename in ocr_in_progress:
        raise HTTPException(status_code=409, detail="这篇论文正在 OCR，请等待识别完成后再删除")
    if filename in translation_in_progress:
        raise HTTPException(status_code=409, detail="这篇论文正在翻译，请等待完成后再删除")
    if filename in markdown_in_progress:
        raise HTTPException(status_code=409, detail="这篇论文正在生成 Markdown，请等待完成后再删除")
    path.unlink()
    path.with_suffix(".json").unlink(missing_ok=True)
    path.with_suffix(".thumb.png").unlink(missing_ok=True)
    path.with_suffix(".ocr.json").unlink(missing_ok=True)
    path.with_suffix(".md").unlink(missing_ok=True)
    path.with_suffix(".zh.md").unlink(missing_ok=True)


@app.post("/api/papers/{filename}/ocr")
async def retry_paper_ocr(filename: str) -> dict[str, str | bool]:
    path = get_paper_path(filename)
    if read_ocr_summary(path)["ocr_status"] == "completed":
        raise HTTPException(status_code=409, detail="这篇论文已完成 OCR")
    original_filename = f"{read_paper_metadata(path)['title']}.pdf"
    try:
        document = json.loads(path.with_suffix(".ocr.json").read_text(encoding="utf-8"))
        original = document.get("source", {}).get("original_filename")
        if isinstance(original, str) and original.strip():
            original_filename = original
    except (OSError, ValueError, AttributeError):
        pass
    return await run_paper_ocr(path, original_filename)


@app.get("/api/papers/{filename}/ocr")
async def download_paper_ocr(filename: str) -> FileResponse:
    path = get_paper_path(filename)
    result = path.with_suffix(".ocr.json")
    if not result.is_file():
        raise HTTPException(status_code=404, detail="这篇论文还没有 OCR JSON")
    return FileResponse(result, media_type="application/json", filename=result.name)


def read_paper_ocr_result(path: Path) -> dict:
    result_path = path.with_suffix(".ocr.json")
    if not result_path.is_file():
        raise HTTPException(status_code=409, detail="请先完成 OCR，再生成 Markdown")
    try:
        document = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise HTTPException(status_code=422, detail="OCR JSON 无法读取") from error
    if not isinstance(document, dict):
        raise HTTPException(status_code=422, detail="OCR JSON 格式无效")
    if document.get("status") not in {"completed", "partial"}:
        raise HTTPException(status_code=409, detail="OCR 未成功，请先重新 OCR")
    if not isinstance(document.get("result"), dict):
        raise HTTPException(status_code=422, detail="OCR JSON 中没有可用的原始识别结果")
    return document["result"]


def render_paper_markdown(content: str) -> str:
    try:
        return render_markdown(content)
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


def paper_markdown_path(path: Path, language: MarkdownLanguage) -> Path:
    return path.with_suffix(".zh.md" if language == "zh" else ".md")


@app.get("/api/papers/{filename}/markdown")
async def get_paper_markdown(
    filename: str, language: MarkdownLanguage = Query("original"),
) -> dict[str, str | bool]:
    path = get_paper_path(filename)
    markdown_path = paper_markdown_path(path, language)
    try:
        content = markdown_path.read_text(encoding="utf-8") if markdown_path.is_file() else ""
    except OSError as error:
        raise HTTPException(status_code=500, detail="无法读取 Markdown 文件") from error
    in_progress = translation_in_progress if language == "zh" else markdown_in_progress
    queue = getattr(app.state, "task_queue", None)
    action = "translation" if language == "zh" else "markdown"
    latest = await queue.latest(filename, action) if queue else None
    if not content and latest and latest["status"] in {"failed", "interrupted", "cancelled"}:
        task_status = "failed"
    else:
        task_status = "generating" if (filename in in_progress or (queue and await queue.active(filename, action))) else "ready" if content else "not_generated"
    return {
        "title": str(read_paper_metadata(path)["title"]),
        "language": language,
        "status": task_status,
        "error": latest["error"] if task_status == "failed" else "",
        "content": content,
        "html": render_paper_markdown(content) if content else "",
    }


@app.post("/api/papers/{filename}/markdown")
async def create_paper_markdown(
    filename: str, force: bool = Query(False), language: MarkdownLanguage = Query("original"),
    background: bool = Query(False),
) -> dict[str, str | bool]:
    path = get_paper_path(filename)
    if background:
        action = "translation" if language == "zh" else "markdown"
        queued = await enqueue_task(TaskRequest(filename=filename, action=action, force=force, skip_existing=not force))
        return {"title": str(read_paper_metadata(path)["title"]), "language": language,
                "status": "generating", "content": "", "html": "", "task_id": queued["id"]}
    if filename in ocr_in_progress:
        raise HTTPException(status_code=409, detail="OCR 尚未完成，请稍后生成 Markdown")
    if filename in markdown_in_progress:
        raise HTTPException(status_code=409, detail="Markdown 正在生成，请稍候")
    if filename in translation_in_progress:
        raise HTTPException(status_code=409, detail="中文正在翻译，请稍候")
    markdown_path = paper_markdown_path(path, language)
    if markdown_path.is_file() and not force:
        return await get_paper_markdown(filename, language)
    if language == "zh":
        source_path = paper_markdown_path(path, "original")
        try:
            source = source_path.read_text(encoding="utf-8") if source_path.is_file() else ""
        except OSError as error:
            raise HTTPException(status_code=500, detail="无法读取 Markdown 原文") from error
        if not source.strip():
            raise HTTPException(status_code=409, detail="请先生成 Markdown 原文，再翻译为中文")
    else:
        ocr_result = read_paper_ocr_result(path)
    in_progress = translation_in_progress if language == "zh" else markdown_in_progress
    in_progress.add(filename)
    try:
        content = await translate_markdown(source) if language == "zh" else await generate_markdown(ocr_result)
        html = render_paper_markdown(content)
        write_markdown(path, content, suffix=".zh.md" if language == "zh" else ".md")
        if language == "original":
            paper_markdown_path(path, "zh").unlink(missing_ok=True)
        return {
            "title": str(read_paper_metadata(path)["title"]),
            "language": language,
            "status": "ready",
            "content": content,
            "html": html,
        }
    except ValueError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    finally:
        in_progress.discard(filename)


@app.get("/api/papers/{filename}/markdown/download")
async def download_paper_markdown(
    filename: str, language: MarkdownLanguage = Query("original"),
) -> FileResponse:
    path = paper_markdown_path(get_paper_path(filename), language)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="中文译文尚未生成" if language == "zh" else "Markdown 尚未生成")
    return FileResponse(path, media_type="text/markdown; charset=utf-8", filename=path.name)


@app.get("/api/papers/{filename}/preview")
def preview_paper(filename: str) -> FileResponse:
    path = get_paper_path(filename)
    thumbnail = path.with_suffix(".thumb.png")
    if not thumbnail.is_file():
        try:
            with fitz.open(path) as document:
                if not document.page_count:
                    raise HTTPException(status_code=404, detail="PDF 没有页面")
                page = document[0]
                scale = 280 / page.rect.width
                image = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False).tobytes("png")
            temporary = path.with_name(f"{path.stem}.{uuid4().hex}.tmp")
            temporary.write_bytes(image)
            temporary.replace(thumbnail)
        except HTTPException:
            raise
        except Exception as error:
            raise HTTPException(status_code=422, detail="无法生成 PDF 预览") from error
    return FileResponse(thumbnail, media_type="image/png")


@app.get("/papers/{filename}")
async def get_paper(filename: str) -> FileResponse:
    path = get_paper_path(filename)
    return FileResponse(path, media_type="application/pdf", filename=path.name)


TaskAction = Literal["ocr", "references", "metadata", "markdown", "translation"]


class TaskRequest(BaseModel):
    filename: str
    action: TaskAction
    skip_existing: bool = True
    save_result: bool = False
    force: bool = False


@app.post("/api/tasks", status_code=202)
async def enqueue_task(request: TaskRequest) -> dict:
    path = get_paper_path(request.filename)
    if request.save_result and request.action != "references":
        raise HTTPException(status_code=422, detail="仅参考文献任务支持自动保存")
    if request.force and request.skip_existing:
        raise HTTPException(status_code=422, detail="强制重新处理不能同时跳过已有结果")
    try:
        return await task_queue().submit(path.name, str(read_paper_metadata(path)["title"]), request.action,
                                         request.model_dump(exclude={"filename", "action"}))
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.get("/api/tasks")
async def list_tasks(limit: int = Query(100, ge=1, le=2000)) -> dict:
    return await task_queue().listing(limit)


@app.get("/api/tasks/{task_id}")
async def get_task(task_id: str) -> dict:
    task = await task_queue().get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task


@app.post("/api/tasks/{task_id}/cancel")
async def cancel_task(task_id: str) -> dict:
    await get_task(task_id)
    if not await task_queue().cancel(task_id):
        raise HTTPException(status_code=409, detail="只能取消尚未开始的排队任务")
    return await get_task(task_id)


@app.post("/api/tasks/{task_id}/retry", status_code=202)
async def retry_task(task_id: str) -> dict:
    task = await get_task(task_id)
    if task["status"] not in {"failed", "interrupted", "cancelled"}:
        raise HTTPException(status_code=409, detail="只有失败、中断或取消的任务可重试")
    return await enqueue_task(TaskRequest(filename=task["filename"], action=task["action"], **task["options"]))


async def execute_task(task: dict) -> dict:
    filename, action, options = task["filename"], task["action"], task["options"]
    path = get_paper_path(filename)
    if action == "ocr":
        if options["skip_existing"] and read_ocr_summary(path)["ocr_status"] in {"completed", "partial"}:
            raise SkipTask("已有 OCR 结果")
        result = await retry_paper_ocr(filename)
        if result["ocr_status"] != "completed":
            raise ValueError(result.get("ocr_error") or "OCR 未完整完成，请检查 OCR JSON")
        return result
    if action == "references":
        if options["skip_existing"] and read_paper_metadata(path)["references"]:
            raise SkipTask("已有结构化参考文献")
        result = await create_paper_references(filename)
        if not result["references"]:
            raise SkipTask("未识别到参考文献，原数据未改变")
        if options["save_result"]:
            # Re-read after the long model call, preserving edits/star state.
            latest = read_paper_metadata(path)
            if options["skip_existing"] and latest["references"]:
                raise SkipTask("提取期间已有参考文献被保存，保留现有数据")
            latest["references"] = result["references"]
            await update_paper_metadata(filename, PaperMetadata(**latest))
        return result
    if action == "metadata":
        return await extract_paper_details(filename)
    language = "zh" if action == "translation" else "original"
    if options["skip_existing"] and paper_markdown_path(path, language).is_file():
        raise SkipTask("已有中文译文" if language == "zh" else "已有 Markdown")
    return await create_paper_markdown(filename, force=options["force"], language=language, background=False)
