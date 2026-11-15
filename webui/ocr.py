"""DeepSeek OCR integration; retain the unmodified provider response for inspection."""

import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from uuid import uuid4

import fitz
import httpx


OCR_URL = "https://api.deepseek.com/v1/tools/ocr"
OCR_PURPOSE = "rich_layout_recognition"
MAX_FILE_BYTES = 100 * 1024 * 1024
MAX_PAGES = 1000


def read_ocr_summary(path: Path) -> dict[str, str | bool]:
    result_path = path.with_suffix(".ocr.json")
    if not result_path.is_file():
        return {"ocr_status": "not_started", "ocr_error": "", "ocr_downloadable": False}
    try:
        document = json.loads(result_path.read_text(encoding="utf-8"))
        return {
            "ocr_status": document["status"],
            "ocr_error": document.get("error") or "",
            "ocr_downloadable": True,
        }
    except (OSError, ValueError, KeyError, TypeError):
        return {"ocr_status": "failed", "ocr_error": "OCR JSON 无法读取", "ocr_downloadable": False}


def validate_result(result: dict, page_count: int) -> str | None:
    pages = result.get("pages")
    if not isinstance(pages, list):
        raise ValueError("OCR 响应缺少 pages 数组")
    for page in pages:
        if (
            not isinstance(page, dict)
            or type(page.get("index")) is not int
            or not isinstance(page.get("text"), str)
            or page.get("finish_reason") not in {"stop", "length"}
        ):
            raise ValueError("OCR 响应中的页面格式无效")
    warnings = []
    if (
        result.get("page_count") != len(pages)
        or [page["index"] for page in pages] != list(range(page_count))
    ):
        warnings.append(f"返回页码或页数与原 PDF 不符（原文 {page_count} 页，返回 {len(pages)} 页）")
    truncated = [str(page["index"] + 1) for page in pages if page["finish_reason"] == "length"]
    if truncated:
        warnings.append(f"第 {', '.join(truncated)} 页输出被截断")
    return "；".join(warnings) or None


async def recognize_paper(path: Path, original_filename: str) -> dict[str, str | bool]:
    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    document = {
        "provider": "deepseek",
        "source": {"filename": path.name, "original_filename": original_filename, "page_count": None},
        "purpose": OCR_PURPOSE,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "failed",
        "result": None,
        "error": None,
    }
    try:
        if not api_key:
            document["status"] = "not_configured"
            raise ValueError("未配置 DEEPSEEK_API_KEY，PDF 已保存，未进行 OCR")
        if path.stat().st_size > MAX_FILE_BYTES:
            raise ValueError("PDF 超过 OCR 接口的 100 MiB 文件上限")
        with fitz.open(path) as pdf:
            if pdf.needs_pass:
                raise ValueError("PDF 受密码保护，请解密后重新上传")
            page_count = pdf.page_count
        document["source"]["page_count"] = page_count
        if not 1 <= page_count <= MAX_PAGES:
            raise ValueError("OCR 仅支持 1 至 1000 页的 PDF")

        # The provider sends whitespace keepalives. json() accepts that whitespace.
        # Bound total time as well as socket reads; never retry a possibly billed request automatically.
        async with asyncio.timeout(200):
            async with httpx.AsyncClient(timeout=httpx.Timeout(190, connect=20, write=120, pool=20)) as client:
                with path.open("rb") as source:
                    response = await client.post(
                        OCR_URL,
                        headers={"Authorization": f"Bearer {api_key}"},
                        data={"purpose": OCR_PURPOSE},
                        files={"file": (path.name, source, "application/pdf")},
                    )
        try:
            result = response.json()
        except ValueError as error:
            raise ValueError(f"OCR 接口返回非 JSON 内容（HTTP {response.status_code}）") from error
        document["result"] = result
        if not isinstance(result, dict):
            raise ValueError("OCR 接口返回的 JSON 格式无效")
        # HTTP 200 can still contain an OCR failure instead of a result.
        if not response.is_success or "error" in result:
            error = result.get("error")
            detail = error.get("message") if isinstance(error, dict) else error
            raise ValueError(f"OCR 请求失败（HTTP {response.status_code}）：{detail or '服务未返回错误详情'}")
        document["error"] = validate_result(result, page_count)
        document["status"] = "partial" if document["error"] else "completed"
    except (TimeoutError, httpx.TimeoutException):
        document["error"] = "OCR 请求超时，PDF 已保存"
    except httpx.RequestError:
        document["error"] = "无法连接 DeepSeek OCR 服务，PDF 已保存"
    except Exception as error:
        document["error"] = str(error)[:1000]

    # Strip credentials even if an upstream error happens to echo them.
    if api_key:
        document = json.loads(json.dumps(document, ensure_ascii=False).replace(api_key, "[REDACTED]"))
    result_path = path.with_suffix(".ocr.json")
    temporary = path.with_name(f"{path.stem}.{uuid4().hex}.ocr.tmp")
    try:
        temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(result_path)
    finally:
        temporary.unlink(missing_ok=True)
    return read_ocr_summary(path)
