"""第一问网页的本地后端。

医疗图片只在本机进入 RapidOCR；DeepSeek 仅接收 OCR 文本。
"""

from __future__ import annotations

import base64
import io
import ipaddress
import json
import hashlib
import logging
import os
import re
import socket
import tempfile
import time
from collections import Counter
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import Field

from medical_claims.json_utils import normalize_json_output
from medical_claims.long_case import chunk_documents, document_text_size, merge_claim_cases
from medical_claims.models import ClaimCase, Costs, Medication, StrictModel
from medical_claims.normalization import normalize_case
from medical_claims.care_engine import (
    build_fallback_draft,
    diagnosis_names,
    finalize_care_guide,
    infer_disease_topic,
)
from medical_claims.care_knowledge import match_knowledge_card
from medical_claims.care_models import (
    CareGuideDraft,
    CareGuideReport,
    CareGuideRequest,
    GenerationMethod,
)
from medical_claims.care_prompt import build_care_generation_context
from medical_claims.pain_engine import analyze_pain_points
from medical_claims.pain_models import (
    PainAnalysisRequest,
    PainPointReport,
    SemanticPainPointBatch,
)
from medical_claims.quality_engine import build_quality_portfolio, recommend_profiles
from medical_claims.quality_models import (
    QualityPortfolio,
    QualityPortfolioRequest,
    RecommendationReport,
    RecommendationRequest,
)
from medical_claims.validation import validate_case
from medical_claims.evidence_grounding import apply_evidence_grounding
from medical_claims.evaluation import evaluate
from dataclasses import asdict
from medical_claims.storage import CaseStorage, CaseNotFound, WorkflowConflict, WorkflowItemNotFound
from medical_claims.workflow_models import CreateCaseRequest, WorkflowCase
from medical_claims.profile_records import (
    HistoricalProfileImport, CaseProfileContextRequest, StoredPortfolioBuildRequest,
    ConfirmedProviderContext, build_profile_case_record, resolve_disease_group,
)
from medical_claims.quality_integration import (
    current_profile_input, build_saved_portfolio, case_recommendations, provider_context_from_saved_ocr,
)
from medical_claims.quality_coverage import audit_quality_coverage
from medical_claims.multisource_quality import (
    MultiSourceCandidatePools,
    build_multisource_candidate_pools,
    load_multisource_inputs,
)
from medical_claims.delivery import prepare_delivery_content, SendDeliveryRequest
from medical_claims.review_models import ReviewRequest
from medical_claims.feedback_models import FeedbackRequest, ResolveFollowUpRequest
from fastapi.responses import JSONResponse
from urllib.parse import urlencode


app = FastAPI(title="Medical Claims Structure API", version="0.5.0")
BACKEND_REVISION = "2026-10-07-mobile-preview-v1"
# 本次升级只增加OCR回显与人工修正通道；未修改页面的结构化提示词与Schema兼容旧缓存。
# 人工修正会改变OCR文本哈希，因此对应分块仍会自动失效并重新处理。
LONG_CASE_CACHE_REVISION = "2026-10-02-care-guide-v1"
logger = logging.getLogger(__name__)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:8765",
        "http://localhost:8765",
        "https://medical-claims-workbench.zhengwenxi251.chatgpt.site",
    ],
    allow_origin_regex=r"^http://(?:localhost|127\.0\.0\.1|(?:\d{1,3}\.){3}\d{1,3})(?::\d+)?$",
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


def _is_loopback_client(host: str | None) -> bool:
    if not host or host == "testclient":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@app.middleware("http")
async def limit_remote_mobile_access(request: Request, call_next):
    """When bound to the LAN, expose only the token-protected mobile preview."""

    remote = request.client.host if request.client else None
    allowed_remote_paths = {"/api/mobile-preview"}
    if (
        not _is_loopback_client(remote)
        and request.method != "OPTIONS"
        and request.url.path not in allowed_remote_paths
    ):
        return JSONResponse(
            status_code=403,
            content={"detail": "局域网设备仅允许访问临时手机预览"},
        )
    return await call_next(request)

_ocr_engine: Any | None = None


def get_case_storage() -> CaseStorage:
    return CaseStorage()


@app.exception_handler(CaseNotFound)
async def case_not_found_handler(request, exc):
    return JSONResponse(status_code=404, content={"detail": "案件不存在"})


@app.exception_handler(WorkflowConflict)
async def workflow_conflict_handler(request, exc):
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(WorkflowItemNotFound)
async def workflow_item_not_found_handler(request, exc):
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.post("/api/workflow/cases", response_model=WorkflowCase, status_code=201)
def create_workflow_case(request: CreateCaseRequest):
    """Competition prototype simulated claim trigger, not an insurer integration."""
    return get_case_storage().create_case(request)


@app.get("/api/workflow/cases/{case_id}")
def get_workflow_case(case_id: str):
    return get_case_storage().get_case(case_id)


@app.get("/api/workflow/cases/{case_id}/result")
def get_workflow_results(case_id: str):
    return get_case_storage().get_results(case_id)


class OcrBlockPayload(StrictModel):
    text: str
    confidence: float | None = Field(default=None, ge=0, le=1)
    bbox: tuple[float, float, float, float] | None = None


class OcrDocumentPayload(StrictModel):
    document_id: str
    page: int = Field(ge=1)
    full_text: str = ""
    blocks: list[OcrBlockPayload] = Field(default_factory=list)
    human_corrected: bool = False


class DetailedCaseProcessingResult(StrictModel):
    case: ClaimCase
    documents: list[OcrDocumentPayload]


class MobilePreviewCreateRequest(StrictModel):
    expires_in_minutes: int = Field(default=30, ge=5, le=240)


def _discover_mobile_preview_host() -> str:
    configured = os.getenv("MOBILE_PREVIEW_HOST", "").strip()
    if configured:
        return configured
    candidates: list[str] = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 80))
            candidates.append(probe.getsockname()[0])
    except OSError:
        pass
    try:
        candidates.extend(
            item[4][0]
            for item in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
        )
    except OSError:
        pass
    for candidate in candidates:
        try:
            address = ipaddress.ip_address(candidate)
        except ValueError:
            continue
        if not address.is_loopback and not address.is_link_local and not address.is_unspecified:
            return candidate
    raise RuntimeError("未找到可供手机访问的局域网 IPv4 地址")


def _qr_svg_data_url(value: str) -> str:
    try:
        import qrcode
        from qrcode.image.svg import SvgPathImage
    except ImportError as exc:
        raise RuntimeError("缺少 qrcode 依赖，无法生成二维码") from exc
    image = qrcode.make(value, image_factory=SvgPathImage, box_size=8, border=3)
    output = io.BytesIO()
    image.save(output)
    encoded = base64.b64encode(output.getvalue()).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"


class OcrRestructureRequest(StrictModel):
    case_id: str
    documents: list[OcrDocumentPayload] = Field(min_length=1)


def normalize_document_id(value: str) -> str:
    """修复压缩包中常见的 UTF-8 文件名被按 GBK 解码后的乱码。"""

    try:
        source = b"".join(
            bytes([0x80]) if character == "€" else character.encode("gbk")
            for character in value
        )
        candidate = source.decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return value
    medical_markers = {
        "住院",
        "费用",
        "清单",
        "检查",
        "报告",
        "出院",
        "记录",
        "医保",
        "结算",
        "门诊",
        "其它",
    }
    return candidate if any(marker in candidate for marker in medical_markers) else value


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
        "human_corrected": False,
    }


def document_ocr_confidence(document: dict[str, Any]) -> float | None:
    """人工确认后的整页文本优先；否则返回有效 OCR 块的平均置信度。"""

    if document.get("human_corrected"):
        return 1.0
    scores = [
        float(block["confidence"])
        for block in document.get("blocks", [])
        if block.get("confidence") is not None
    ]
    return sum(scores) / len(scores) if scores else None


async def recognize_uploaded_documents(
    files: list[UploadFile], tmp_path: Path
) -> list[dict[str, Any]]:
    filenames = [
        normalize_document_id(upload.filename or "image.jpg") for upload in files
    ]
    filename_counts = Counter(filenames)
    documents: list[dict[str, Any]] = []
    for page, upload in enumerate(files, start=1):
        suffix = Path(upload.filename or "image.jpg").suffix.lower()
        if suffix not in {".jpg", ".jpeg", ".png", ".bmp", ".webp"}:
            raise ValueError(f"当前OCR后端暂不处理 {suffix or '未知格式'}，请先转为图片")
        target = tmp_path / f"{page:04d}{suffix}"
        target.write_bytes(await upload.read())
        original_name = normalize_document_id(upload.filename or target.name)
        document_id = (
            original_name
            if filename_counts[original_name] == 1
            else f"{page:04d}-{original_name}"
        )
        documents.append(recognize_image(target, document_id, page))
    return documents


def extract_output_text(payload: dict[str, Any]) -> str:
    if isinstance(payload.get("output_text"), str):
        return payload["output_text"]
    for item in payload.get("output", []):
        for content in item.get("content", []):
            if content.get("type") == "output_text" and content.get("text"):
                return content["text"]
    raise RuntimeError("DeepSeek 响应中没有结构化文本")


def post_deepseek(body: dict[str, Any]) -> dict[str, Any]:
    """对限流、服务端错误和瞬时超时执行有上限的重试。"""

    retries = int(os.getenv("DEEPSEEK_MAX_RETRIES", "2"))
    timeout = float(os.getenv("DEEPSEEK_TIMEOUT_SECONDS", "360"))
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        raise RuntimeError("未配置 DEEPSEEK_API_KEY")
    for attempt in range(retries + 1):
        try:
            with httpx.Client(timeout=timeout) as client:
                response = client.post(
                    "https://api.deepseek.com/responses",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json=body,
                )
            if response.status_code in {429, 500, 502, 503, 504} and attempt < retries:
                delay = min(8, 2 ** (attempt + 1))
                logger.warning(
                    "DeepSeek transient status %s; retrying in %ss",
                    response.status_code,
                    delay,
                )
                time.sleep(delay)
                continue
            response.raise_for_status()
            return response.json()
        except httpx.TimeoutException:
            if attempt >= retries:
                raise
            delay = min(8, 2 ** (attempt + 1))
            logger.warning("DeepSeek timeout; retrying in %ss", delay)
            time.sleep(delay)
    raise RuntimeError("DeepSeek重试次数耗尽")


def structure_with_deepseek(
    case_id: str,
    documents: list[dict[str, Any]],
    *,
    finalize: bool = True,
    scope_note: str | None = None,
) -> ClaimCase:
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        raise RuntimeError("未配置 DEEPSEEK_API_KEY")

    compact_documents = [
        {
            "document_id": document["document_id"],
            "page": document["page"],
            "ocr_text": document["full_text"],
            "ocr_confidence": document_ocr_confidence(document),
            "human_corrected": bool(document.get("human_corrected")),
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
        "若页面标记 human_corrected=true，说明OCR文本已经人工对照原图修正，应优先采用修正文本；"
        "但仍不得补写修正文本中没有出现的事实。"
        "为控制输出长度，每个字段的evidence最多保留1条最直接、最短且足以证明该字段的证据；"
        "不要在多个字段中复制整段病历，只截取包含目标值的最小原文片段。"
    )
    if scope_note:
        instructions += (
            scope_note
            + "这是长病例的一部分，只提取本批次页面明确出现的事实。"
            "其他页面未在本批次出现不代表整个案件缺失，不得仅因本批次缺页生成缺失或冲突告警。"
            "费用总额、分类小计和支付金额只提取原文明确给出的数值；不得根据不完整批次自行汇总全案金额。"
            "实体ID只需在本批次内唯一，案件级系统随后会重新编号、去重和校验。"
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
        "max_output_tokens": int(
            os.getenv(
                "DEEPSEEK_MAX_OUTPUT_TOKENS" if finalize else "DEEPSEEK_CHUNK_MAX_OUTPUT_TOKENS",
                "30000",
            )
        ),
        "text": {
            "format": {
                "type": "json_schema",
                "name": "claim_case",
                "schema": ClaimCase.model_json_schema(),
            }
        },
    }
    output_text = extract_output_text(post_deepseek(body))
    result = ClaimCase.model_validate_json(normalize_json_output(output_text))
    result = normalize_case(result)
    if finalize:
        existing_flags = {
            (flag.code, flag.field_path, flag.document_id, flag.message)
            for flag in result.quality_flags
        }
        result.quality_flags.extend(
            flag
            for flag in validate_case(result)
            if (flag.code, flag.field_path, flag.document_id, flag.message) not in existing_flags
        )
    if finalize:
        result = apply_evidence_grounding(result, documents)
    if result.case_id != case_id:
        raise RuntimeError("模型返回的 case_id 与请求不一致")
    return ClaimCase.model_validate(result.model_dump())


def _structure_chunk_with_fallback(
    case_id: str,
    documents: list[dict[str, Any]],
    *,
    label: str,
) -> list[ClaimCase]:
    """分块输出仍过长或超时时，递归减半，不让整案从头失败。"""

    first_page = documents[0]["page"]
    last_page = documents[-1]["page"]
    cache_path = _chunk_cache_path(case_id, documents)
    if cache_path is not None and cache_path.exists():
        try:
            cached = ClaimCase.model_validate_json(cache_path.read_text(encoding="utf-8"))
            if cached.case_id == case_id:
                logger.info("Reusing long-case chunk cache %s", cache_path.name)
                return [cached]
        except Exception:
            logger.exception("Ignoring invalid long-case chunk cache %s", cache_path)
    try:
        result = structure_with_deepseek(
            case_id,
            documents,
            finalize=False,
            scope_note=f"当前为{label}，覆盖全案第{first_page}至{last_page}页。",
        )
        if cache_path is not None:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = cache_path.with_suffix(".tmp")
            temporary.write_text(result.model_dump_json(indent=2), encoding="utf-8")
            temporary.replace(cache_path)
        return [result]
    except httpx.HTTPStatusError:
        raise
    except Exception:
        if len(documents) <= 1:
            raise
        logger.exception(
            "Long-case chunk failed; retrying two smaller chunks (pages %s-%s)",
            first_page,
            last_page,
        )
        midpoint = len(documents) // 2
        return _structure_chunk_with_fallback(
            case_id, documents[:midpoint], label=f"{label}-A"
        ) + _structure_chunk_with_fallback(
            case_id, documents[midpoint:], label=f"{label}-B"
        )


def _chunk_cache_path(case_id: str, documents: list[dict[str, Any]]) -> Path | None:
    """仅缓存结构化分块，不保存原始图片；代码版本变化会自动失效。"""

    if os.getenv("LONG_CASE_CACHE_ENABLED", "true").lower() in {"0", "false", "no"}:
        return None
    root = Path(os.getenv("LONG_CASE_CACHE_DIR", "outputs/_long_case_cache"))
    safe_case_id = re.sub(r"[^0-9A-Za-z_.-]+", "_", case_id).strip("._") or "case"
    signature = {
        "revision": LONG_CASE_CACHE_REVISION,
        "case_id": case_id,
        "documents": [
            {
                "document_id": document["document_id"],
                "page": document["page"],
                "ocr_sha256": hashlib.sha256(
                    str(document.get("full_text") or "").encode("utf-8")
                ).hexdigest(),
            }
            for document in documents
        ],
    }
    fingerprint = hashlib.sha256(
        json.dumps(signature, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:24]
    return root / safe_case_id / f"chunk-{fingerprint}.json"


def structure_case_with_long_document_support(
    case_id: str, documents: list[dict[str, Any]]
) -> ClaimCase:
    """短病例保持单次调用；长病例自动分块、合并并执行全局校验。"""

    page_threshold = int(os.getenv("LONG_CASE_PAGE_THRESHOLD", "8"))
    character_threshold = int(os.getenv("LONG_CASE_CHARACTER_THRESHOLD", "24000"))
    total_characters = sum(document_text_size(document) for document in documents)
    if len(documents) <= page_threshold and total_characters <= character_threshold:
        return apply_evidence_grounding(structure_with_deepseek(case_id, documents), documents)

    max_pages = int(os.getenv("LONG_CASE_CHUNK_MAX_PAGES", "6"))
    max_characters = int(os.getenv("LONG_CASE_CHUNK_MAX_CHARACTERS", "18000"))
    max_ocr_blocks = int(os.getenv("LONG_CASE_CHUNK_MAX_OCR_BLOCKS", "600"))
    planned_chunks = chunk_documents(
        documents,
        max_pages=max_pages,
        max_characters=max_characters,
        max_ocr_blocks=max_ocr_blocks,
    )
    logger.info(
        "Processing long case %s: %s pages, %s OCR chars, %s planned chunks",
        case_id,
        len(documents),
        total_characters,
        len(planned_chunks),
    )
    partials: list[ClaimCase] = []
    for index, chunk in enumerate(planned_chunks, start=1):
        partials.extend(
            _structure_chunk_with_fallback(
                case_id,
                chunk,
                label=f"计划分块{index}/{len(planned_chunks)}",
            )
        )
    result = merge_claim_cases(
        case_id,
        partials,
        documents,
        chunk_count=len(partials),
    )
    result = normalize_case(result)
    existing_flags = {
        (flag.code, flag.field_path, flag.document_id, flag.message)
        for flag in result.quality_flags
    }
    result.quality_flags.extend(
        flag
        for flag in validate_case(result)
        if (flag.code, flag.field_path, flag.document_id, flag.message) not in existing_flags
    )
    result = apply_evidence_grounding(result, documents)
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
        "不得仅因为药品数量多、存在常见围手术期麻醉镇痛药或抗凝药就输出用药与诊断不匹配。"
        "MEDICATION_DIAGNOSIS_REVIEW必须定位到一个具体medications[数字]和一个具体diagnoses[数字]，"
        "related_fields必须写出这两个索引路径，并至少引用药品与诊断两条不同证据；否则不要输出。"
        "费用清单中的药品项目不是面向患者的用药医嘱，不能因其缺少频次、途径或疗程而判断指导不完整。"
        "CARE_GUIDANCE_INCOMPLETE只有在材料明确出现笼统随访要求、但缺少时间或行动信息时才能输出；"
        "不能仅根据ClaimCase没有某字段就推断医生未告知。"
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
    output_text = extract_output_text(post_deepseek(body))
    result = SemanticPainPointBatch.model_validate_json(normalize_json_output(output_text))
    if result.case_id != case.case_id:
        raise RuntimeError("痛点模型返回的 case_id 与请求不一致")
    return result


def care_guide_with_deepseek(
    case: ClaimCase,
    pain_report: PainPointReport,
    disease_topic: str,
    card,
) -> CareGuideDraft:
    """DeepSeek 仅依据精简事实包和知识卡生成第三问草稿。"""

    instructions = (
        "你是理赔后医护关怀指引生成器。你不是医生，不得新增诊断、处方、剂量、疗程或复查日期。"
        "只能使用case_fact_pack中的病例事实；knowledge_card只用于通俗解释、一般性行动建议和危险信号。"
        "每个病例事实陈述必须在evidence_fields中引用存在且有值的字段路径。"
        "必须区分个人自付、个人自费与现金/个人账户等付款渠道；缺少金额时明确说材料未提供，禁止推算。"
        "内容必须包含诊断通俗解释、治疗过程回顾、费用解读、康复计划、心理安抚和下次就医建议。"
        "开场和心理安抚必须结合本病例情境，禁止只写祝早日康复等套话。"
        "行动建议要具体但不越权；不得建议自行停药、换药、增减剂量或擅自训练。"
        "不知道的内容明确说明需要向经治医生确认，不得用医学常识补齐病例事实。"
        "limitations必须原样包含："
        "“本指引仅依据本次提交的理赔医疗材料生成，不能替代医生诊断。”和"
        "“不要据此自行停药、换药或调整剂量。”"
    )
    body = {
        "model": os.getenv("DEEPSEEK_MODEL", "deepseek-flash"),
        "instructions": instructions,
        "input": json.dumps(
            build_care_generation_context(case, pain_report, card, disease_topic),
            ensure_ascii=False,
        ),
        "reasoning": {"effort": "none"},
        "temperature": 0.2,
        "max_output_tokens": int(os.getenv("DEEPSEEK_CARE_MAX_OUTPUT_TOKENS", "12000")),
        "text": {
            "format": {
                "type": "json_schema",
                "name": "care_guide_draft",
                "schema": CareGuideDraft.model_json_schema(),
            }
        },
    }
    output_text = extract_output_text(post_deepseek(body))
    result = CareGuideDraft.model_validate_json(normalize_json_output(output_text))
    if result.case_id != case.case_id:
        raise RuntimeError("温度指引模型返回的 case_id 与请求不一致")
    return result


@app.get("/api/health")
def health() -> dict[str, str | bool | int]:
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
        "care_guide_engine": True,
        "patient_experience_page": True,
        "expiring_mobile_preview": True,
        "feedback_loop": True,
        "quality_profile_engine": True,
        "explainable_recommendation_engine": True,
        "ocr_review_workflow": True,
        "case_persistence_workflow": True,
        "case_quality_integration": True,
        "risk_delivery_feedback_loop": True,
        "long_case_chunking": True,
        "long_case_page_threshold": int(os.getenv("LONG_CASE_PAGE_THRESHOLD", "8")),
        "long_case_chunk_max_pages": int(os.getenv("LONG_CASE_CHUNK_MAX_PAGES", "6")),
        "long_case_chunk_max_ocr_blocks": int(
            os.getenv("LONG_CASE_CHUNK_MAX_OCR_BLOCKS", "600")
        ),
        "long_case_resume_cache": os.getenv("LONG_CASE_CACHE_ENABLED", "true").lower()
        not in {"0", "false", "no"},
    }


@app.post("/api/cases/process", response_model=ClaimCase)
async def process_case(
    case_id: str = Form(...),
    files: list[UploadFile] = File(...),
) -> ClaimCase:
    if not files:
        raise HTTPException(status_code=400, detail="一个案件至少需要一份材料")
    storage = get_case_storage()
    storage.begin_processing(case_id)
    try:
        with tempfile.TemporaryDirectory(prefix="medical-claim-") as tmp:
            tmp_path = Path(tmp)
            documents = await recognize_uploaded_documents(files, tmp_path)
            storage.save_ocr(case_id, documents)
            case = structure_case_with_long_document_support(case_id, documents)
            storage.save_structured(case_id, case)
            return case
    except httpx.HTTPStatusError as exc:
        logger.exception("DeepSeek API returned an HTTP error")
        raise HTTPException(status_code=502, detail=f"DeepSeek API 调用失败：{exc.response.status_code}") from exc
    except httpx.TimeoutException as exc:
        logger.exception("DeepSeek API timed out")
        raise HTTPException(
            status_code=504,
            detail="DeepSeek分块处理超时；系统已尝试自动缩小分块，请稍后重试失败批次",
        ) from exc
    except Exception as exc:
        logger.exception("Case processing failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        if storage.get_case(case_id)["status"] == "PROCESSING":
            storage.finish_failed_processing(case_id)


@app.post(
    "/api/cases/process-detailed",
    response_model=DetailedCaseProcessingResult,
)
async def process_case_detailed(
    case_id: str = Form(...),
    files: list[UploadFile] = File(...),
) -> DetailedCaseProcessingResult:
    """运行OCR和结构化，并把逐页OCR文本返回给人工复核界面。"""

    if not files:
        raise HTTPException(status_code=400, detail="一个案件至少需要一份材料")
    storage = get_case_storage()
    storage.begin_processing(case_id)
    try:
        with tempfile.TemporaryDirectory(prefix="medical-claim-") as tmp:
            documents = await recognize_uploaded_documents(files, Path(tmp))
            storage.save_ocr(case_id, documents)
            case = structure_case_with_long_document_support(case_id, documents)
            storage.save_structured(case_id, case)
            return DetailedCaseProcessingResult(
                case=case,
                documents=[OcrDocumentPayload.model_validate(item) for item in documents],
            )
    except httpx.HTTPStatusError as exc:
        logger.exception("DeepSeek API returned an HTTP error")
        raise HTTPException(
            status_code=502,
            detail=f"DeepSeek API 调用失败：{exc.response.status_code}",
        ) from exc
    except httpx.TimeoutException as exc:
        logger.exception("DeepSeek API timed out")
        raise HTTPException(
            status_code=504,
            detail="DeepSeek分块处理超时；OCR文本已生成，但结构化尚未完成，请稍后重试",
        ) from exc
    except Exception as exc:
        logger.exception("Detailed case processing failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        if storage.get_case(case_id)["status"] == "PROCESSING":
            storage.finish_failed_processing(case_id)


@app.post("/api/cases/restructure-from-ocr", response_model=ClaimCase)
def restructure_case_from_ocr(request: OcrRestructureRequest) -> ClaimCase:
    """使用人工校正后的逐页OCR文本重新结构化，不再次识别图片。"""

    storage = get_case_storage()
    storage.begin_processing(request.case_id)
    try:
        documents = [document.model_dump() for document in request.documents]
        storage.save_ocr(request.case_id, documents)
        case = structure_case_with_long_document_support(request.case_id, documents)
        storage.save_structured(request.case_id, case)
        return case
    except httpx.HTTPStatusError as exc:
        logger.exception("DeepSeek API returned an HTTP error during OCR correction")
        raise HTTPException(
            status_code=502,
            detail=f"DeepSeek API 调用失败：{exc.response.status_code}",
        ) from exc
    except httpx.TimeoutException as exc:
        logger.exception("DeepSeek API timed out during OCR correction")
        raise HTTPException(
            status_code=504,
            detail="修正文本重新结构化超时；系统会保留已完成分块缓存，请稍后重试",
        ) from exc
    except Exception as exc:
        logger.exception("OCR correction restructuring failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        if storage.get_case(request.case_id)["status"] == "PROCESSING":
            storage.finish_failed_processing(request.case_id)


@app.post("/api/cases/analyze-pain-points", response_model=PainPointReport)
def analyze_case_pain_points(request: PainAnalysisRequest) -> PainPointReport:
    """对第一问的 ClaimCase 执行第二问多维痛点识别。"""

    storage = get_case_storage()
    storage.require_current(request.case.case_id, request.case)
    # 兼容浏览器中仍保留的旧版第一问结果：分析前重新执行确定性规范化，
    # 修正费用语义、证据文件名等问题，且不会重新 OCR 或调用结构化模型。
    case = normalize_case(request.case.model_copy(deep=True))
    semantic_points = []
    warnings: list[str] = []
    if request.use_llm:
        try:
            semantic_points = semantic_pain_points_with_deepseek(case).pain_points
        except Exception as exc:
            logger.exception("Semantic pain-point analysis failed; returning rule-only result")
            warnings.append(
                f"DeepSeek语义痛点分析暂不可用，已返回确定性规则结果：{type(exc).__name__}"
            )
    else:
        warnings.append("本次请求关闭了DeepSeek语义分析，仅运行确定性规则。")
    report = analyze_pain_points(case, semantic_points, warnings)
    storage.save_pain(case.case_id, report, request.case)
    return report


@app.post("/api/cases/generate-care-guide", response_model=CareGuideReport)
def generate_case_care_guide(request: CareGuideRequest) -> CareGuideReport:
    """基于第一问病例、第二问痛点和已登记知识卡生成第三问温度指引。"""

    storage = get_case_storage()
    storage.require_current(request.case.case_id, request.case, request.pain_report)
    if request.pain_report is None:
        raise WorkflowConflict("请先保存痛点分析结果")
    case = normalize_case(request.case.model_copy(deep=True))
    warnings: list[str] = []
    pain_report = request.pain_report
    if pain_report is None:
        pain_report = analyze_pain_points(
            case,
            [],
            ["请求未附第二问结果，本次仅使用确定性规则痛点。"],
        )
        warnings.append("未附第二问完整结果，建议先完成痛点分析后重新生成。")

    card = match_knowledge_card(diagnosis_names(case), request.disease_topic)
    disease_topic = infer_disease_topic(case, request.disease_topic, card)
    method = GenerationMethod.RULES_FALLBACK
    if request.use_llm:
        try:
            draft = care_guide_with_deepseek(case, pain_report, disease_topic, card)
            method = GenerationMethod.LLM
        except Exception as exc:
            logger.exception("Care-guide generation failed; returning safe fallback")
            warnings.append(f"DeepSeek温度指引暂不可用，已返回安全降级草稿：{type(exc).__name__}")
            draft = build_fallback_draft(case, pain_report, card, disease_topic)
    else:
        warnings.append("本次关闭DeepSeek，仅生成安全降级草稿。")
        draft = build_fallback_draft(case, pain_report, card, disease_topic)
    report = finalize_care_guide(case, draft, card, method, warnings)
    storage.save_care(case.case_id, report, request.case, request.pain_report)
    return report


@app.get("/api/quality/audit-summary")
def get_quality_audit_summary() -> dict[str, Any]:
    """返回本地机构覆盖审计的安全汇总；绝不读取 private 目录。"""

    configured = os.getenv("PROVIDER_AUDIT_REPORT")
    report_path = (
        Path(configured)
        if configured
        else Path(__file__).resolve().parents[2]
        / "work"
        / "provider_audit"
        / "provider-coverage-report.json"
    )
    if not report_path.exists():
        raise HTTPException(
            status_code=404,
            detail="尚未生成机构覆盖审计，请先运行 scripts/audit_provider_coverage.py",
        )
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail="机构覆盖审计报告无法读取") from exc
    if report.get("scope", {}).get("manifest_used") is not False:
        raise HTTPException(status_code=409, detail="审计报告未通过 manifest 隔离检查")
    return report


@app.post("/api/quality/profiles/build", response_model=QualityPortfolio)
def build_quality_profiles(request: QualityPortfolioRequest | StoredPortfolioBuildRequest) -> QualityPortfolio:
    """按病种和脱敏主体编号聚合品质画像。"""

    if isinstance(request, StoredPortfolioBuildRequest):
        return build_saved_portfolio(get_case_storage(), request.minimum_sample_size)
    # Retain the legacy explicitly supplied-record preview; it is not a
    # confirmed historical dataset and cannot replace the persisted portfolio.
    records = [
        record.model_copy(
            update={"case": normalize_case(record.case.model_copy(deep=True))}
        )
        for record in request.records
    ]
    portfolio = build_quality_portfolio(records, request.minimum_sample_size)
    portfolio.warnings.append("PREVIEW ONLY：此请求自行提供的记录未经过确认上下文入库，不替换持久化portfolio或正式KR4覆盖")
    return portfolio


@app.post("/api/quality/recommendations", response_model=RecommendationReport)
def get_quality_recommendations(request: RecommendationRequest) -> RecommendationReport:
    """只对通过样本量和可计算维度门槛的画像执行可解释推荐。"""

    report = recommend_profiles(
        request.portfolio,
        request.disease_group,
        request.entity_type,
        request.top_k,
    )
    report.warnings.append("PREVIEW ONLY：此请求自行提供portfolio；正式案件推荐请使用案件级接口和已保存数据")
    return report


@app.get("/api/quality/multisource-candidate-pools", response_model=MultiSourceCandidatePools)
def get_multisource_candidate_pools() -> MultiSourceCandidatePools:
    """返回肺炎、流感和高血压的多源医院画像候选池；不生成排名或最终推荐。"""

    root = Path(__file__).resolve().parents[2]
    try:
        public_audit, dataset_audit = load_multisource_inputs(root)
        return build_multisource_candidate_pools(
            public_audit,
            dataset_audit,
            get_case_storage().load_portfolio(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/api/quality/records/import")
def import_historical_profile_records(request: HistoricalProfileImport):
    storage = get_case_storage()
    assemblies = []
    for payload in request.records:
        assembly = build_profile_case_record(payload)
        storage.save_profile_record(payload, assembly)
        assemblies.append(assembly.model_dump(mode="json", exclude={"record"}))
    return {"records": assemblies, "dataset": storage.profile_dataset_summary()}


@app.get("/api/quality/portfolio")
def get_persisted_quality_portfolio():
    storage = get_case_storage()
    portfolio = storage.load_portfolio()
    return {"status": "available" if portfolio and any(p.eligible_for_recommendation for p in portfolio.profiles) else
            "insufficient_evidence" if portfolio else "not_evaluated",
            "portfolio": portfolio.model_dump(mode="json") if portfolio else None,
            "dataset": storage.profile_dataset_summary()}


@app.get("/api/quality/coverage")
def get_quality_coverage():
    storage = get_case_storage()
    return audit_quality_coverage(storage.load_portfolio(), storage.profile_dataset_summary())


@app.get("/api/workflow/cases/{case_id}/quality-context")
def get_case_quality_context(case_id: str):
    storage = get_case_storage()
    saved = storage.get_results(case_id)
    settings = storage.get_profile_context(case_id)
    candidates = provider_context_from_saved_ocr(case_id, saved["ocr_result"]) if saved["ocr_result"] else None
    case = ClaimCase.model_validate(saved["structured_result"]) if saved["structured_result"] else None
    assembly = build_profile_case_record(current_profile_input(storage, case_id)) if case else None
    return {"case_id": case_id, "disease_group": resolve_disease_group(case) if case else "unavailable",
            "candidates": candidates.model_dump(mode="json") if candidates else None,
            "saved_context": settings,
            "assembly": assembly.model_dump(mode="json", exclude={"record"}) if assembly else None}


@app.post("/api/workflow/cases/{case_id}/quality-context")
def save_case_quality_context(case_id: str, request: CaseProfileContextRequest):
    if request.provider.context.case_id != case_id:
        raise HTTPException(status_code=422, detail="上下文 case_id 与当前案件不一致")
    storage = get_case_storage()
    metadata = storage.get_case(case_id)
    if metadata["consent_status"] != "authorized" or metadata["deidentification_status"] != "confirmed":
        raise WorkflowConflict("画像数据入库需要已声明授权且已确认脱敏")
    payload = current_profile_input(storage, case_id, request.provider)
    payload.stable_patient_token = request.stable_patient_token
    payload.readmitted_within_30_days = request.readmitted_within_30_days
    payload.patient_feedback_score = request.patient_feedback_score
    storage.save_profile_context(case_id, request.model_dump(mode="json"), payload.case, payload.pain_report)
    assembly = build_profile_case_record(payload)
    storage.save_profile_record(payload, assembly, source_kind="workflow")
    return assembly.model_dump(mode="json", exclude={"record"})


@app.get("/api/workflow/cases/{case_id}/recommendations")
def get_current_case_recommendations(case_id: str):
    return case_recommendations(get_case_storage(), case_id)


@app.post("/api/workflow/cases/{case_id}/delivery/prepare")
def prepare_case_delivery(case_id: str):
    storage = get_case_storage()
    return storage.prepare_delivery(prepare_delivery_content(storage, case_id))


@app.post("/api/workflow/cases/{case_id}/mobile-preview", status_code=201)
def create_case_mobile_preview(case_id: str, request: MobilePreviewCreateRequest):
    """Create an internal, expiring mobile preview without bypassing delivery safety."""

    created = get_case_storage().create_mobile_preview(
        case_id, ttl_minutes=request.expires_in_minutes
    )
    host = _discover_mobile_preview_host()
    gateway_base = os.getenv(
        "MOBILE_PREVIEW_GATEWAY_BASE_URL",
        f"http://{host}:{os.getenv('MOBILE_PREVIEW_GATEWAY_PORT', '8766')}",
    ).rstrip("/")
    fragment = urlencode(
        {"preview_token": created["token"], "api_base": gateway_base}
    )
    preview_url = f"{gateway_base}/patient.html#{fragment}"
    return {
        "publication_id": created["publication_id"],
        "preview_url": preview_url,
        "qr_code_data_url": _qr_svg_data_url(preview_url),
        "expires_at": created["expires_at"],
        "expires_in_minutes": request.expires_in_minutes,
        "network_host": host,
        "notice": "仅供内部预览；链接到期自动失效，不代表已通过发送安全审核。",
    }


@app.get("/api/mobile-preview")
def get_mobile_preview(x_preview_token: str = Header(min_length=20, max_length=200)):
    """Read the minimal patient-facing snapshot using a non-logged header token."""

    return get_case_storage().get_mobile_preview(x_preview_token)


class StructuredEvaluationRequest(StrictModel):
    prediction: dict[str, Any] | list[dict[str, Any]]
    gold: dict[str, Any] | list[dict[str, Any]]


@app.post("/api/evaluation/structured")
def evaluate_structured_records(request: StructuredEvaluationRequest):
    try:
        return asdict(evaluate(request.prediction, request.gold))
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/workflow/cases/{case_id}/delivery/send")
def send_case_delivery(case_id: str, request: SendDeliveryRequest | None = None):
    storage = get_case_storage()
    prepared = prepare_delivery_content(storage, case_id)
    if request and request.content_fingerprint and request.content_fingerprint != prepared["content_fingerprint"]:
        raise WorkflowConflict("触达页面内容已过时，请重新准备")
    return storage.send_delivery(prepared)


@app.get("/api/workflow/cases/{case_id}/deliveries")
def get_case_deliveries(case_id: str):
    return get_case_storage().list_deliveries(case_id)


@app.get("/api/deliveries/{delivery_id}")
def get_mock_inbox_delivery(delivery_id: str):
    return get_case_storage().get_delivery(delivery_id)


@app.post("/api/workflow/cases/{case_id}/reviews", status_code=201)
def review_case_delivery(case_id: str, request: ReviewRequest):
    storage = get_case_storage()
    return storage.save_review(prepare_delivery_content(storage, case_id), request)


@app.get("/api/workflow/cases/{case_id}/reviews")
def get_case_reviews(case_id: str):
    return get_case_storage().list_reviews(case_id)


@app.post("/api/deliveries/{delivery_id}/feedback", status_code=201)
def submit_delivery_feedback(delivery_id: str, request: FeedbackRequest):
    return get_case_storage().save_feedback(delivery_id, request)


@app.get("/api/workflow/cases/{case_id}/feedback")
def get_case_feedback(case_id: str):
    return get_case_storage().list_feedback(case_id)


@app.get("/api/follow-ups")
def get_follow_up_tasks():
    return get_case_storage().list_follow_ups()


@app.get("/api/workflow/cases/{case_id}/follow-ups")
def get_case_follow_ups(case_id: str):
    return get_case_storage().list_follow_ups(case_id)


@app.post("/api/follow-ups/{task_id}/resolve")
def resolve_follow_up_task(task_id: str, request: ResolveFollowUpRequest):
    return get_case_storage().resolve_follow_up(task_id, request.resolution)
