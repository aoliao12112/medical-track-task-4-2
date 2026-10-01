"""第一问网页的本地后端。

医疗图片只在本机进入 RapidOCR；DeepSeek 仅接收 OCR 文本。
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from medical_claims.json_utils import normalize_json_output
from medical_claims.models import ClaimCase, Costs, Medication
from medical_claims.normalization import normalize_case
from medical_claims.pain_engine import analyze_pain_points
from medical_claims.pain_models import (
    PainAnalysisRequest,
    PainPointReport,
    SemanticPainPointBatch,
)
from medical_claims.validation import validate_case


app = FastAPI(title="Medical Claims Structure API", version="0.2.0")
BACKEND_REVISION = "2026-10-01-pain-engine-v1"
logger = logging.getLogger(__name__)
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
        "参考范围和异常标记；结果数值不得重复包含单位，例如40%必须拆成value=40、unit=%。"
        "原文没有参考范围时保持缺失，不得用医学常识补写。"
        "MRI、CT、超声、检验等检查及检查申请只能写入examinations，不得同时写入procedures；"
        "费用清单或缴费记录中的缴费日期不等于检查实施日期，除非原文明确写明检查日期，否则performed_date保持缺失。"
        "门急诊就诊事件使用 visit，不能写成 admission。输液医嘱中明确出现的溶媒也作为独立药品提取。"
        "用药频次的value保留原文缩写，normalized_value将qd/bid/tid/qid分别规范为每日1/2/3/4次；"
        "最大日剂量写入max_daily_dose，其他重复用药限制写入additional_instructions。"
        "费用字段必须区分三个层级：费用口径、责任属性、付款渠道。个人自付是医保目录内由个人承担的金额，"
        "个人自费是医保目录外由个人全额承担的金额，两者严禁合并成个人支付；个人账户与现金属于付款渠道，"
        "不得当作个人自付或个人自费。基金支付来源也必须分别提取。只有原文明确出现的值才能标记 extracted；"
        "不得用个人账户支付、现金支付等付款渠道反推个人自付或个人自费；原文未拆分责任属性时保持缺失。"
        "计算结果标记 derived 并在 derivation 中写明算式，缺少任一计算项时不得把缺失项当成0。"
        "本任务只做忠实结构化与证据一致性检查，不得判断医生诊断不合理、医生错误或治疗不当。"
        "若不同文档中的诊断或证据确有冲突，只能添加 DIAGNOSIS_DOCUMENT_CONFLICT 或 "
        "DIAGNOSIS_EVIDENCE_REVIEW 质量标记并提示医学复核，不得给出临床责任结论。"
        "不要为姓名、年龄等不在输出Schema中的脱敏字段创建缺失告警。"
        "字段置信度应综合OCR置信度、文本明确程度和跨文档一致性，低于0.90时使用 review_required。"
        "为控制输出长度，每个字段的evidence最多保留1条最直接、最短且足以证明该字段的证据；"
        "不要在多个字段中复制整段病历，只截取包含目标值的最小原文片段。"
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
        "max_output_tokens": int(os.getenv("DEEPSEEK_MAX_OUTPUT_TOKENS", "30000")),
        "text": {
            "format": {
                "type": "json_schema",
                "name": "claim_case",
                "schema": ClaimCase.model_json_schema(),
            }
        },
    }
    with httpx.Client(timeout=float(os.getenv("DEEPSEEK_TIMEOUT_SECONDS", "360"))) as client:
        response = client.post(
            "https://api.deepseek.com/responses",
            headers={"Authorization": f"Bearer {api_key}"},
            json=body,
        )
        response.raise_for_status()
    output_text = extract_output_text(response.json())
    result = ClaimCase.model_validate_json(normalize_json_output(output_text))
    result = normalize_case(result)
    existing_flag_codes = {flag.code for flag in result.quality_flags}
    result.quality_flags.extend(
        flag for flag in validate_case(result) if flag.code not in existing_flag_codes
    )
    if result.case_id != case_id:
        raise RuntimeError("模型返回的 case_id 与请求不一致")
    return ClaimCase.model_validate(result.model_dump())


def semantic_pain_points_with_deepseek(case: ClaimCase) -> SemanticPainPointBatch:
    """只让 LLM 提供需要语义理解的候选，证据和标签随后仍由本地引擎校验。"""

    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        raise RuntimeError("未配置 DEEPSEEK_API_KEY")
    instructions = (
        "你是理赔后医护关怀中的语义痛点候选生成器，不是医疗裁决系统。"
        "输入是已经结构化的ClaimCase，只能使用输入字段及其evidence，禁止补写病例事实或外部医学结论。"
        "你只能输出以下三种类型：MEDICATION_DIAGNOSIS_REVIEW、"
        "DIAGNOSIS_TERM_HARD_TO_UNDERSTAND、CARE_GUIDANCE_INCOMPLETE。"
        "不得输出费用类、检查异常类或重复用药类标签，这些由本地确定性规则处理。"
        "用药与诊断只能表述为对应关系需要医学复核，不能声称医生用药错误、不合理或存在医疗事故。"
        "缺少充分证据时不输出；没有痛点时返回空数组。"
        "每条evidence必须逐字复制输入中已有evidence的最短原文，document_id和page必须一致。"
        "严重程度最高只能为high；confidence不得高于0.90；所有候选review_required必须为true。"
        "recommended_action必须是安全、可执行的复核或沟通建议，不得建议自行停药或改变治疗。"
    )
    body = {
        "model": os.getenv("DEEPSEEK_MODEL", "deepseek-flash"),
        "instructions": instructions,
        "input": case.model_dump_json(exclude_none=False),
        "reasoning": {"effort": "none"},
        "temperature": 0,
        "max_output_tokens": int(os.getenv("DEEPSEEK_PAIN_MAX_OUTPUT_TOKENS", "10000")),
        "text": {
            "format": {
                "type": "json_schema",
                "name": "semantic_pain_point_batch",
                "schema": SemanticPainPointBatch.model_json_schema(),
            }
        },
    }
    with httpx.Client(timeout=float(os.getenv("DEEPSEEK_TIMEOUT_SECONDS", "360"))) as client:
        response = client.post(
            "https://api.deepseek.com/responses",
            headers={"Authorization": f"Bearer {api_key}"},
            json=body,
        )
        response.raise_for_status()
    output_text = extract_output_text(response.json())
    result = SemanticPainPointBatch.model_validate_json(normalize_json_output(output_text))
    if result.case_id != case.case_id:
        raise RuntimeError("痛点模型返回的 case_id 与请求不一致")
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
        "cost_semantics_schema": "personal_burden" in Costs.model_fields,
        "medication_instruction_schema": "max_daily_dose" in Medication.model_fields,
        "pain_point_engine": True,
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
        logger.exception("DeepSeek API returned an HTTP error")
        raise HTTPException(status_code=502, detail=f"DeepSeek API 调用失败：{exc.response.status_code}") from exc
    except httpx.TimeoutException as exc:
        logger.exception("DeepSeek API timed out")
        raise HTTPException(status_code=504, detail="DeepSeek处理超时，请减少页面或稍后重试") from exc
    except Exception as exc:
        logger.exception("Case processing failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/cases/analyze-pain-points", response_model=PainPointReport)
def analyze_case_pain_points(request: PainAnalysisRequest) -> PainPointReport:
    """对第一问的 ClaimCase 执行第二问多维痛点识别。"""

    semantic_points = []
    warnings: list[str] = []
    if request.use_llm:
        try:
            semantic_points = semantic_pain_points_with_deepseek(request.case).pain_points
        except Exception as exc:
            logger.exception("Semantic pain-point analysis failed; returning rule-only result")
            warnings.append(
                f"DeepSeek语义痛点分析暂不可用，已返回确定性规则结果：{type(exc).__name__}"
            )
    else:
        warnings.append("本次请求关闭了DeepSeek语义分析，仅运行确定性规则。")
    return analyze_pain_points(request.case, semantic_points, warnings)
