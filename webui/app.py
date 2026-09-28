from pathlib import Path
from typing import Annotated
from uuid import uuid4
import json

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
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


@app.get("/api/papers")
async def list_papers() -> list[dict[str, str | int]]:
    papers = sorted(UPLOAD_DIR.glob("*.pdf"), key=lambda path: path.stat().st_mtime, reverse=True)
    return [
        {
            "name": read_paper_name(paper),
            "filename": paper.name,
            "size_kb": round(paper.stat().st_size / 1024),
        }
        for paper in papers
    ]


def read_paper_name(paper: Path) -> str:
    return read_paper_metadata(paper)["title"]


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


@app.get("/api/papers/{filename}/preview")
async def preview_paper(filename: str) -> Response:
    path = get_paper_path(filename)
    try:
        with fitz.open(path) as document:
            if not document.page_count:
                raise HTTPException(status_code=404, detail="PDF 没有页面")
            image = document[0].get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False).tobytes("png")
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(status_code=422, detail="无法生成 PDF 预览") from error
    return Response(content=image, media_type="image/png")


@app.get("/papers/{filename}")
async def get_paper(filename: str) -> FileResponse:
    path = get_paper_path(filename)
    return FileResponse(path, media_type="application/pdf", filename=path.name)