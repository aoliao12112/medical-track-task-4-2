"""第一问网页的本地后端。

医疗图片只在本机进入 RapidOCR；DeepSeek 仅接收 OCR 文本。
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from medical_claims.json_utils import normalize_json_output
from medical_claims.models import ClaimCase


app = FastAPI(title="Medical Claims Structure API", version="0.1.0")
BACKEND_REVISION = "2026-09-29-examinations-v1"
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:8765",
        "http://localhost:8765",
        "https://medical-claims-workbench.zhengwenxi251.chatgpt.site",
    ],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

_ocr_engine: Any | None = None


def get_ocr_engine() -> Any:
    global _ocr_engine
    if _ocr_engine is None:
        try:
            from rapidocr import RapidOCR
        except ImportError as exc:
            raise RuntimeError("未安装 RapidOCR，请安装项目的 api 依赖") from exc
        _ocr_engine = RapidOCR()
    return _ocr_engine


def recognize_image(path: Path, document_id: str, page: int) -> dict[str, Any]:
    result = get_ocr_engine()(str(path))
    texts = list(result.txts) if result.txts is not None else []
    scores = list(result.scores) if result.scores is not None else []
    boxes = list(result.boxes) if result.boxes is not None else []
    blocks: list[dict[str, Any]] = []
    for index, text in enumerate(texts):
        polygon = boxes[index].tolist() if index < len(boxes) else None
        score = float(scores[index]) if index < len(scores) else None
        bbox = None
        if polygon:
            xs = [point[0] for point in polygon]
            ys = [point[1] for point in polygon]
            bbox = [min(xs), min(ys), max(xs), max(ys)]
        blocks.append({"text": text, "confidence": score, "bbox": bbox})
    return {
        "document_id": document_id,
        "page": page,
        "blocks": blocks,
        "full_text": "\n".join(texts),
    }


def extract_output_text(payload: dict[str, Any]) -> str:
    if isinstance(payload.get("output_text"), str):
        return payload["output_text"]
    for item in payload.get("output", []):
        for content in item.get("content", []):
            if content.get("type") == "output_text" and content.get("text"):
                return content["text"]
    raise RuntimeError("DeepSeek 响应中没有结构化文本")


def structure_with_deepseek(case_id: str, documents: list[dict[str, Any]]) -> ClaimCase:
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        raise RuntimeError("未配置 DEEPSEEK_API_KEY")

    compact_documents = [
        {
            "document_id": document["document_id"],
            "page": document["page"],
            "ocr_text": document["full_text"],
            "ocr_blocks": document["blocks"],
        }
        for document in documents
    ]
    instructions = (
        "你是医疗理赔病历结构化引擎。只能提取输入证据中明确出现的事实，禁止补写或猜测。"
        "每个已提取字段必须带 evidence；缺失字段使用 not_present_in_document；"
        "明确无手术等情况使用 not_applicable。日期统一 YYYY-MM-DD，金额单位为人民币元。"
        "辅助检查必须写入 examinations：检验、影像、病理等分别记录，检验结果逐项提取名称、值、单位、"
        "参考范围和异常标记；原文没有参考范围时保持缺失，不得用医学常识补写。"
        "门急诊就诊事件使用 visit，不能写成 admission。输液医嘱中明确出现的溶媒也作为独立药品提取。"
        "不要为姓名、年龄等不在输出Schema中的脱敏字段创建缺失告警。"
        "字段置信度应综合OCR置信度、文本明确程度和跨文档一致性，低于0.90时使用 review_required。"
    )
    body = {
        "model": os.getenv("DEEPSEEK_MODEL", "deepseek-flash"),
        "instructions": instructions,
        "input": json.dumps(
            {"case_id": case_id, "documents": compact_documents},
            ensure_ascii=False,
        ),
        "reasoning": {"effort": "none"},
        "temperature": 0,
        "max_output_tokens": 12000,
        "text": {
            "format": {
                "type": "json_schema",
                "name": "claim_case",
                "schema": ClaimCase.model_json_schema(),
            }
        },
    }
    with httpx.Client(timeout=180) as client:
        response = client.post(
            "https://api.deepseek.com/responses",
            headers={"Authorization": f"Bearer {api_key}"},
            json=body,
        )
        response.raise_for_status()
    output_text = extract_output_text(response.json())
    result = ClaimCase.model_validate_json(normalize_json_output(output_text))
    if result.case_id != case_id:
        raise RuntimeError("模型返回的 case_id 与请求不一致")
    return result


@app.get("/api/health")
def health() -> dict[str, str | bool]:
    try:
        import rapidocr  # noqa: F401
        ocr = "rapidocr-ready"
    except ImportError:
        ocr = "rapidocr-missing"
    llm = "deepseek-ready" if os.getenv("DEEPSEEK_API_KEY") else "deepseek-key-missing"
    return {
        "status": "ok",
        "ocr": ocr,
        "llm": llm,
        "revision": BACKEND_REVISION,
        "examinations_schema": "examinations" in ClaimCase.model_fields,
    }


@app.post("/api/cases/process", response_model=ClaimCase)
async def process_case(
    case_id: str = Form(...),
    files: list[UploadFile] = File(...),
) -> ClaimCase:
    if not files:
        raise HTTPException(status_code=400, detail="一个案件至少需要一份材料")
    try:
        with tempfile.TemporaryDirectory(prefix="medical-claim-") as tmp:
            tmp_path = Path(tmp)
            documents: list[dict[str, Any]] = []
            for page, upload in enumerate(files, start=1):
                suffix = Path(upload.filename or "image.jpg").suffix.lower()
                if suffix not in {".jpg", ".jpeg", ".png", ".bmp", ".webp"}:
                    raise ValueError(f"当前OCR后端暂不处理 {suffix or '未知格式'}，请先转为图片")
                target = tmp_path / f"{page:04d}{suffix}"
                target.write_bytes(await upload.read())
                documents.append(recognize_image(target, upload.filename or target.name, page))
            return structure_with_deepseek(case_id, documents)
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=502, detail=f"DeepSeek API 调用失败：{exc.response.status_code}") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
