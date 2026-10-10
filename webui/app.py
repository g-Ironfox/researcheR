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


UPLOAD_DIR = Path(__file__).parent / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

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


@app.post("/upload")
async def upload(files: Annotated[list[UploadFile], File()]) -> dict[str, list[str]]:
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
    return {"filenames": filenames, "duplicates": duplicates}


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
    path.unlink()
    path.with_suffix(".json").unlink(missing_ok=True)
    path.with_suffix(".thumb.png").unlink(missing_ok=True)


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