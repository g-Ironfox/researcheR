from pathlib import Path
from typing import Annotated
from uuid import uuid4
import json

from fastapi import FastAPI, File, HTTPException, UploadFile
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


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(Path(__file__).parent / "static" / "index.html")


@app.get("/read/{filename}")
async def read_paper(filename: str) -> FileResponse:
    get_paper_path(filename)
    return FileResponse(Path(__file__).parent / "static" / "reader.html")


@app.get("/api/papers")
async def list_papers() -> list[dict[str, str | int]]:
    papers = sorted(UPLOAD_DIR.glob("*.pdf"), key=lambda path: path.stat().st_mtime, reverse=True)
    result = []
    for paper in papers:
        metadata = read_paper_metadata(paper)
        result.append({
            "name": metadata["title"],
            "venue": metadata["venue"],
            "filename": paper.name,
            "size_kb": round(paper.stat().st_size / 1024),
        })
    return result


def read_paper_metadata(paper: Path) -> dict[str, str]:
    metadata_path = paper.with_suffix(".json")
    if not metadata_path.is_file():
        return {"title": paper.stem, "venue": "", "abstract": "", "reference": ""}
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"title": paper.stem, "venue": "", "abstract": "", "reference": ""}
    return {
        "title": metadata.get("title") or metadata.get("name") or paper.stem,
        "venue": metadata.get("venue", ""),
        "abstract": metadata.get("abstract", ""),
        "reference": metadata.get("reference", ""),
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

    filenames: list[str] = []
    for file, header in zip(files, headers):
        destination = UPLOAD_DIR / f"{uuid4().hex}.pdf"
        with destination.open("wb") as output:
            output.write(header)
            while chunk := await file.read(1024 * 1024):
                output.write(chunk)
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
    return {"filenames": filenames}


@app.get("/api/papers/{filename}")
async def get_paper_metadata(filename: str) -> dict[str, str]:
    return read_paper_metadata(get_paper_path(filename))


@app.put("/api/papers/{filename}")
async def update_paper_metadata(filename: str, metadata: PaperMetadata) -> dict[str, str]:
    path = get_paper_path(filename)
    data = metadata.model_dump()
    path.with_suffix(".json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


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