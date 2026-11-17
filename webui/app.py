from pathlib import Path
from typing import Annotated
from uuid import uuid4
from hashlib import sha256
import json

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import fitz
from pypdf import PdfReader

from ocr import read_ocr_summary, recognize_paper
from markdown_service import generate_markdown, render_markdown, write_markdown


UPLOAD_DIR = Path(__file__).parent / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

ocr_in_progress: set[str] = set()
markdown_in_progress: set[str] = set()

app = FastAPI(title="论文库")
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")


class PaperMetadata(BaseModel):
    title: str
    venue: str = ""
    abstract: str = ""
    reference: str = ""


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
            **({"ocr_status": "processing"} if paper.name in ocr_in_progress else {}),
        })
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


def read_paper_metadata(paper: Path) -> dict[str, str | bool]:
    metadata_path = paper.with_suffix(".json")
    if not metadata_path.is_file():
        return {"title": paper.stem, "venue": "", "abstract": "", "reference": "", "starred": False}
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"title": paper.stem, "venue": "", "abstract": "", "reference": "", "starred": False}
    return {
        "title": metadata.get("title") or metadata.get("name") or paper.stem,
        "venue": metadata.get("venue", ""),
        "abstract": metadata.get("abstract", ""),
        "reference": metadata.get("reference", ""),
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
    if path.name in markdown_in_progress:
        raise HTTPException(status_code=409, detail="这篇论文正在生成 Markdown，请等待完成后再 OCR")
    if path.name in ocr_in_progress:
        raise HTTPException(status_code=409, detail="这篇论文正在 OCR，请等待识别完成")
    ocr_in_progress.add(path.name)
    try:
        summary = await recognize_paper(path, original_filename)
        path.with_suffix(".md").unlink(missing_ok=True)
        return summary
    finally:
        ocr_in_progress.discard(path.name)


@app.post("/upload")
async def upload(files: Annotated[list[UploadFile], File()]) -> dict:
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
        summary = await run_paper_ocr(destination, file.filename)
        ocr_results.append({"filename": destination.name, "name": file.filename, **summary})
    return {"filenames": filenames, "duplicates": duplicates, "ocr_results": ocr_results}


@app.get("/api/papers/{filename}")
async def get_paper_metadata(filename: str) -> dict[str, str | bool]:
    return read_paper_metadata(get_paper_path(filename))


@app.put("/api/papers/{filename}")
async def update_paper_metadata(filename: str, metadata: PaperMetadata) -> dict[str, str | bool]:
    path = get_paper_path(filename)
    data = metadata.model_dump()
    data["starred"] = read_paper_metadata(path)["starred"]
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
    if filename in ocr_in_progress:
        raise HTTPException(status_code=409, detail="这篇论文正在 OCR，请等待识别完成后再删除")
    if filename in markdown_in_progress:
        raise HTTPException(status_code=409, detail="这篇论文正在生成 Markdown，请等待完成后再删除")
    path.unlink()
    path.with_suffix(".json").unlink(missing_ok=True)
    path.with_suffix(".thumb.png").unlink(missing_ok=True)
    path.with_suffix(".ocr.json").unlink(missing_ok=True)
    path.with_suffix(".md").unlink(missing_ok=True)


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


@app.get("/api/papers/{filename}/markdown")
async def get_paper_markdown(filename: str) -> dict[str, str | bool]:
    path = get_paper_path(filename)
    markdown_path = path.with_suffix(".md")
    try:
        content = markdown_path.read_text(encoding="utf-8") if markdown_path.is_file() else ""
    except OSError as error:
        raise HTTPException(status_code=500, detail="无法读取 Markdown 文件") from error
    metadata = read_paper_metadata(path)
    return {
        "title": str(metadata["title"]),
        "status": "generating" if filename in markdown_in_progress else "ready" if content else "not_generated",
        "content": content,
        "html": render_paper_markdown(content) if content else "",
    }


@app.post("/api/papers/{filename}/markdown")
async def create_paper_markdown(filename: str, force: bool = Query(False)) -> dict[str, str | bool]:
    path = get_paper_path(filename)
    if filename in ocr_in_progress:
        raise HTTPException(status_code=409, detail="OCR 尚未完成，请稍后生成 Markdown")
    if filename in markdown_in_progress:
        raise HTTPException(status_code=409, detail="Markdown 正在生成，请稍候")
    markdown_path = path.with_suffix(".md")
    if markdown_path.is_file() and not force:
        return await get_paper_markdown(filename)
    ocr_result = read_paper_ocr_result(path)
    markdown_in_progress.add(filename)
    try:
        content = await generate_markdown(ocr_result)
        html = render_paper_markdown(content)
        write_markdown(path, content)
        return {
            "title": str(read_paper_metadata(path)["title"]),
            "status": "ready",
            "content": content,
            "html": html,
        }
    except ValueError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    finally:
        markdown_in_progress.discard(filename)


@app.get("/api/papers/{filename}/markdown/download")
async def download_paper_markdown(filename: str) -> FileResponse:
    path = get_paper_path(filename).with_suffix(".md")
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Markdown 尚未生成")
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
