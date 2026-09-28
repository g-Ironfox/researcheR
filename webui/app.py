from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


UPLOAD_DIR = Path(__file__).parent / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="论文库")
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(Path(__file__).parent / "static" / "index.html")


@app.get("/api/papers")
async def list_papers() -> list[dict[str, str | int]]:
    papers = sorted(UPLOAD_DIR.glob("*.pdf"), key=lambda path: path.stat().st_mtime, reverse=True)
    return [
        {
            "name": paper.stem,
            "filename": paper.name,
            "size_kb": round(paper.stat().st_size / 1024),
        }
        for paper in papers
    ]


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
        filenames.append(destination.name)
    return {"filenames": filenames}


@app.get("/papers/{filename}")
async def get_paper(filename: str) -> FileResponse:
    if Path(filename).name != filename or not filename.endswith(".pdf"):
        raise HTTPException(status_code=404, detail="论文不存在")
    path = UPLOAD_DIR / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="论文不存在")
    return FileResponse(path, media_type="application/pdf", filename=path.name)