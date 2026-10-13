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
from typing import Any, Literal
from uuid import uuid4

import httpx
from fastapi import (
    Depends, FastAPI, File, Form, Header, HTTPException, Query, Request,
    Response, UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import Field, field_validator

from medical_claims.json_utils import normalize_json_output
from medical_claims.long_case import chunk_documents, document_text_size, merge_claim_cases
from medical_claims.models import ClaimCase, Costs, Medication, StrictModel
from medical_claims.normalization import normalize_case
from medical_claims.care_engine import (
    build_fallback_draft,
    diagnosis_names,
    finalize_care_guide,
    infer_disease_topic,
    validate_care_guide,
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
from medical_claims.auth import SessionStore, hash_password, verify_password
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
from medical_claims.quality_phase1_models import (
    CapabilityCacheWriteRequest,
    ClinicianRecommendation,
    ClinicianRecommendationRequestV2,
    CurrentTreatmentEvaluation,
    DiseaseProviderCatalog,
    HospitalRecommendationReportV2,
    HospitalRecommendationRequestV2,
    ProviderDiscoveryQuery,
    ProviderDiscoveryResult,
    RecommendationIntent,
    PatientRecommendationContext,
    TreatmentEvaluationRequestV2,
)
from medical_claims.quality_phase1_service import (
    DEFAULT_CATALOG_PATH,
    run_clinician_recommendation,
    run_hospital_recommendation,
    run_provider_discovery,
    run_treatment_evaluation,
)
from medical_claims.recommendation_intent import (
    build_recommendation_fact_pack,
    disease_group_from_intent,
    validate_recommendation_intent,
)
from medical_claims.recommendation_prompt import build_recommendation_planner_body
from medical_claims.external_model_policy import (
    ExternalModelAuthorizationError,
    ExternalModelPurpose,
    require_external_model_authorization,
)
from medical_claims.disease_provider_catalog import load_catalog
from medical_claims.provider_discovery import LocalCapabilityCache
from medical_claims.delivery import prepare_delivery_content, SendDeliveryRequest
from medical_claims.review_models import (
    FieldReviewRequest,
    ReviewDecision,
    ReviewRequest,
    ReviewTarget,
)
from medical_claims.contracts import (
    CareRevisionRequestV1,
    CaseTaskCategoryV1,
    CaseTaskRoleV1,
    PainItemReviewRequestV1,
)
from medical_claims.medical_orchestration import MedicalModule, eligibility, primary_disease
from medical_claims.feedback_models import (
    FeedbackCategory, FeedbackRequest, FollowUpPriority, ResolveFollowUpRequest,
)
from urllib.parse import urlencode


app = FastAPI(title="Medical Claims Structure API", version="0.6.0")
BACKEND_REVISION = "2026-10-11-medical-review-mobile-preview"
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
    allow_methods=["GET", "POST", "PATCH"],
    allow_headers=["*"],
    allow_credentials=True,
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
_auth_sessions = SessionStore(ttl_hours=int(os.getenv("AUTH_SESSION_TTL_HOURS", "12")))
AUTH_COOKIE_NAME = "medical_claims_session"
WEB_DIST = Path(__file__).resolve().parents[1] / "dist"


def private_document_root() -> Path:
    """Private medical materials are never mounted as static web content."""
    return Path(os.getenv(
        "PRIVATE_DOCUMENT_ROOT",
        str(Path(__file__).resolve().parents[2] / "outputs" / "private_case_documents"),
    ))


def get_case_storage() -> CaseStorage:
    return CaseStorage()


class LoginRequest(StrictModel):
    username: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=1, max_length=500)


class UpdateOwnProfileRequest(StrictModel):
    display_name: str | None = Field(default=None, max_length=100)
    phone: str | None = Field(default=None, max_length=50)
    email: str | None = Field(default=None, max_length=254)

    @field_validator("display_name")
    @classmethod
    def validate_display_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("显示名称不能为空")
        return value

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        value = value.strip()
        if not re.fullmatch(r"[+0-9()\-./ ]+", value) or len(re.sub(r"\D", "", value)) < 5:
            raise ValueError("手机号格式不正确")
        return value

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        value = value.strip()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
            raise ValueError("邮箱格式不正确")
        return value


class ChangePasswordRequest(StrictModel):
    current_password: str = Field(min_length=1, max_length=500)
    new_password: str = Field(min_length=8, max_length=500)
    confirm_new_password: str = Field(min_length=1, max_length=500)


class MedicalReviewRequest(StrictModel):
    decision: str = Field(pattern=r"^(APPROVED|RETURNED|NEEDS_CONFIRMATION)$")
    comment: str = Field(default="", max_length=4000)

    @field_validator("comment")
    @classmethod
    def normalize_comment(cls, value: str) -> str:
        return value.strip()

class MedicalAnalysisPrepareRequest(StrictModel):
    modules: list[MedicalModule] = Field(default_factory=lambda: list(MedicalModule))
    use_llm: bool = True
    city: str | None = Field(default=None, max_length=100)
    province: str | None = Field(default=None, max_length=100)
    nearby_major_cities: list[str] = Field(default_factory=list, max_length=5)

    @field_validator("city", "province")
    @classmethod
    def normalize_region(cls, value: str | None) -> str | None:
        return value.strip() if value and value.strip() else None

class MedicalModuleReviewRequest(MedicalReviewRequest):
    module: MedicalModule
    item_decisions: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    care_revision: dict[str, Any] | None = None


class ClaimCaseCreateRequest(StrictModel):
    claim_event_id: str = Field(min_length=1, max_length=200)
    customer_username: str = Field(min_length=1, max_length=200)
    data_source: Literal[
        "phone_report", "offline_material", "historical_migration",
        "customer_service_entry", "user_upload",
    ] = "phone_report"
    consent_status: str = Field(default="pending", min_length=1, max_length=100)
    deidentification_status: str = Field(default="pending", min_length=1, max_length=100)


class CustomerCaseCreateRequest(StrictModel):
    claim_event_id: str | None = Field(default=None, max_length=200)

    @field_validator("claim_event_id")
    @classmethod
    def normalize_claim_event_id(cls, value: str | None) -> str | None:
        return value.strip() if value and value.strip() else None


class AssignCustomerRequest(StrictModel):
    username: str = Field(min_length=1, max_length=200)


class ClaimComplianceUpdateRequest(StrictModel):
    consent_status: Literal["pending", "authorized"]
    deidentification_status: Literal["pending", "confirmed"]


class AssignMedicalReviewerRequest(StrictModel):
    username: str = Field(min_length=1, max_length=200)


class MedicalAnalysisRequest(StrictModel):
    use_llm: bool = True


class MedicalCareRequest(MedicalAnalysisRequest):
    disease_topic: str | None = Field(default=None, max_length=200)


class MedicalDeliveryReviewRequest(StrictModel):
    decision: ReviewDecision
    target_type: ReviewTarget = ReviewTarget.COMBINED_DELIVERY
    note: str = Field(default="", max_length=4000)
    content_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")


class ServiceConsultationCreateRequest(StrictModel):
    case_id: str = Field(min_length=1, max_length=200)
    channel: Literal["WECHAT", "INBOUND_CALL", "ONLINE", "APP_CHAT"]
    deidentified_text: str = Field(min_length=1, max_length=8000)
    category: FeedbackCategory
    priority: FollowUpPriority = FollowUpPriority.MEDIUM
    consent_confirmed: bool
    deidentification_confirmed: bool

    @field_validator("deidentified_text")
    @classmethod
    def consultation_text_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("脱敏咨询摘要不能为空")
        return value


class ServiceConsultationRouteRequest(StrictModel):
    target_role: CaseTaskRoleV1
    category: CaseTaskCategoryV1
    priority: Literal["LOW", "MEDIUM", "HIGH", "URGENT"] = "MEDIUM"
    note: str = Field(min_length=1, max_length=4000)

    @field_validator("note")
    @classmethod
    def trim_route_note(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("分流说明不能为空")
        return value


def _public_identity(identity: dict[str, Any]) -> dict[str, Any]:
    return {key: identity.get(key) for key in (
        "user_id", "username", "display_name", "phone", "email", "status",
        "role_code", "role_name", "permissions", "last_login_at",
    )}


def current_user(request: Request) -> dict[str, Any]:
    user_id = _auth_sessions.resolve(request.cookies.get(AUTH_COOKIE_NAME))
    identity = get_case_storage().get_user_identity(user_id) if user_id else None
    if identity is None or identity["status"] != "ACTIVE":
        raise HTTPException(status_code=401, detail="登录已失效，请重新登录")
    return identity


def current_claim_user(identity: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    if identity["role_code"] != "CLAIM":
        raise HTTPException(status_code=403, detail="当前账号无权进入理赔工作区")
    return identity


def current_customer_user(identity: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    if identity["role_code"] != "CUSTOMER":
        raise HTTPException(status_code=403, detail="当前账号无权进入客户工作区")
    return identity


def current_medical_user(identity: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    if identity["role_code"] != "MEDICAL":
        raise HTTPException(status_code=403, detail="当前账号无权进入医疗复核工作区")
    return identity


def current_service_user(identity: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    if identity["role_code"] != "SERVICE":
        raise HTTPException(status_code=403, detail="当前账号无权进入客服工作区")
    return identity


def require_permission(identity: dict[str, Any], permission: str) -> None:
    if permission not in identity["permissions"]:
        raise HTTPException(status_code=403, detail=f"缺少操作权限：{permission}")


def require_case_permission(identity: dict[str, Any], case_id: str, permission: str) -> None:
    require_permission(identity, permission)
    if not get_case_storage().user_can_access_case(identity["user_id"], case_id, permission):
        # Do not disclose whether an unassigned case exists.
        raise HTTPException(status_code=404, detail="案件不存在或当前账号无权访问")


@app.post("/api/auth/login")
def login(request: LoginRequest, response: Response):
    storage = get_case_storage()
    user = storage.get_user_by_username(request.username.strip())
    if user is None or user["status"] != "ACTIVE" or not verify_password(request.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="账号或密码错误")
    storage.mark_login(user["user_id"])
    token = _auth_sessions.create(user["user_id"])
    response.set_cookie(
        AUTH_COOKIE_NAME,
        token,
        httponly=True,
        samesite="lax",
        secure=os.getenv("AUTH_COOKIE_SECURE", "false").lower() in {"1", "true", "yes"},
        max_age=int(_auth_sessions.ttl.total_seconds()),
        path="/",
    )
    return _public_identity(storage.get_user_identity(user["user_id"]))


@app.post("/api/auth/logout")
def logout(request: Request, response: Response):
    _auth_sessions.revoke(request.cookies.get(AUTH_COOKIE_NAME))
    response.delete_cookie(AUTH_COOKIE_NAME, path="/")
    return {"logged_out": True}


@app.get("/api/auth/me")
def auth_me(identity: dict[str, Any] = Depends(current_user)):
    return _public_identity(identity)


@app.patch("/api/auth/me")
def update_auth_me(
    request: UpdateOwnProfileRequest,
    identity: dict[str, Any] = Depends(current_user),
):
    changed_fields = request.model_fields_set
    if not changed_fields:
        raise HTTPException(status_code=400, detail="请至少提交一项可修改资料")
    if "display_name" in changed_fields and request.display_name is None:
        raise HTTPException(status_code=400, detail="显示名称不能为空")
    updated = get_case_storage().update_own_profile(
        identity["user_id"],
        display_name=request.display_name if "display_name" in changed_fields else identity["display_name"],
        phone=request.phone if "phone" in changed_fields else identity["phone"],
        email=request.email if "email" in changed_fields else identity["email"],
    )
    return _public_identity(updated)


@app.post("/api/auth/change-password")
def change_own_password(
    request: ChangePasswordRequest,
    identity: dict[str, Any] = Depends(current_user),
):
    storage = get_case_storage()
    current_hash = storage.get_user_password_hash(identity["user_id"])
    if current_hash is None:
        raise HTTPException(status_code=401, detail="当前账号不可用")
    if not verify_password(request.current_password, current_hash):
        raise HTTPException(status_code=400, detail="当前密码不正确")
    if request.new_password != request.confirm_new_password:
        raise HTTPException(status_code=400, detail="两次输入的新密码不一致")
    if verify_password(request.new_password, current_hash):
        raise HTTPException(status_code=400, detail="新密码不能与当前密码相同")
    new_hash = hash_password(request.new_password)
    if not storage.update_own_password_hash(
        identity["user_id"], expected_hash=current_hash, new_hash=new_hash,
    ):
        raise HTTPException(status_code=409, detail="密码已被更新，请重新操作")
    return {"changed": True, "message": "密码已修改"}


@app.get("/", include_in_schema=False)
def portal_root():
    return RedirectResponse(url="/login", status_code=302)


@app.get("/login", include_in_schema=False)
def login_page():
    return FileResponse(WEB_DIST / "login.html")


@app.get("/claim", include_in_schema=False)
def claim_page():
    return FileResponse(WEB_DIST / "claim.html")


@app.get("/customer", include_in_schema=False)
def customer_page():
    return FileResponse(WEB_DIST / "customer.html")


@app.get("/medical", include_in_schema=False)
def medical_page():
    return FileResponse(WEB_DIST / "medical.html")


@app.get("/service", include_in_schema=False)
def service_page():
    return FileResponse(WEB_DIST / "service.html")


@app.get("/legacy", include_in_schema=False)
def legacy_workbench():
    return FileResponse(WEB_DIST / "index.html")


@app.get("/{asset_name}", include_in_schema=False)
def portal_asset(asset_name: str):
    allowed = {
        "portal.css", "login.js", "claim.js", "customer.js", "medical.js", "service.js",
        "styles.css", "app.js",
    }
    if asset_name not in allowed:
        raise HTTPException(status_code=404, detail="资源不存在")
    return FileResponse(WEB_DIST / asset_name)


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


class OcrOnlyProcessingResult(StrictModel):
    case_id: str
    documents: list[OcrDocumentPayload]
    external_model_called: bool = False


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
    deidentification_confirmed: bool = False
    manual_override_confirmed: bool = False
    sensitive_pattern_findings: list[str] = Field(default_factory=list)


class OcrReviewSaveRequest(StrictModel):
    case_id: str
    documents: list[OcrDocumentPayload] = Field(min_length=1)


SENSITIVE_OCR_PATTERNS = (
    ("身份证号", re.compile(r"身份证(?:号|号码)?\s*[:：]?\s*\d{6,18}[0-9Xx]?")),
    ("18位证件号码", re.compile(r"(^|\D)\d{17}[0-9Xx](?=\D|$)", re.MULTILINE)),
    ("手机号", re.compile(r"(^|\D)1\d{10}(?=\D|$)", re.MULTILINE)),
    ("联系电话", re.compile(r"(?:联系|手机|电话)(?:方式|号码)?\s*[:：]?\s*\d{7,}")),
    ("住址或地址", re.compile(r"(?:家庭|详细|联系|通讯|医院)?(?:住址|地址)\s*[:：]?\s*[^\n]{4,}")),
    ("个人医疗编号", re.compile(r"(?:病历号|病例号|门诊号|住院号|影像号|标本号|ID号)\s*[:：]?\s*[A-Za-z0-9-]{4,}", re.IGNORECASE)),
)


def sensitive_ocr_findings(documents: list[dict[str, Any]]) -> list[str]:
    """Return warning categories only; a human may explicitly override false positives."""

    text = "\n".join(str(document.get("full_text") or "") for document in documents)
    return [label for label, pattern in SENSITIVE_OCR_PATTERNS if pattern.search(text)]


@app.get("/api/claim/dashboard")
def claim_dashboard(identity: dict[str, Any] = Depends(current_claim_user)):
    require_permission(identity, "CASE_VIEW")
    return get_case_storage().claim_dashboard(identity["user_id"])


@app.get("/api/claim/cases")
def claim_cases(
    search: str | None = Query(default=None, max_length=200),
    claim_status: str | None = Query(default=None, max_length=100),
    consent_status: str | None = Query(default=None, max_length=100),
    care_status: str | None = Query(default=None, max_length=100),
    identity: dict[str, Any] = Depends(current_claim_user),
):
    require_permission(identity, "CASE_VIEW")
    return get_case_storage().list_claim_cases(
        identity["user_id"], search=search, claim_status=claim_status,
        consent_status=consent_status, care_status=care_status,
    )


@app.post("/api/claim/cases", status_code=201)
def create_claim_case(
    request: ClaimCaseCreateRequest,
    identity: dict[str, Any] = Depends(current_claim_user),
):
    require_permission(identity, "CASE_CREATE")
    payload = request.model_dump(exclude={"customer_username"})
    return get_case_storage().create_claim_case_for_customer(
        CreateCaseRequest(**payload),
        identity["user_id"],
        request.customer_username.strip(),
    )


@app.get("/api/claim/customers")
def claim_search_customers(
    search: str = Query(min_length=1, max_length=200),
    identity: dict[str, Any] = Depends(current_claim_user),
):
    require_permission(identity, "CASE_VIEW")
    return get_case_storage().search_active_users_by_role("CUSTOMER", search)


@app.get("/api/claim/cases/{case_id}")
def claim_case_detail(case_id: str, identity: dict[str, Any] = Depends(current_claim_user)):
    require_case_permission(identity, case_id, "CASE_VIEW")
    return get_case_storage().get_claim_case_detail(identity["user_id"], case_id)


@app.post("/api/claim/cases/{case_id}/accept")
def claim_accept_case(case_id: str, identity: dict[str, Any] = Depends(current_claim_user)):
    require_case_permission(identity, case_id, "CLAIM_STATUS_UPDATE")
    return get_case_storage().accept_claim_case(identity["user_id"], case_id)


@app.post("/api/claim/cases/{case_id}/customer")
def claim_assign_customer(
    case_id: str,
    request: AssignCustomerRequest,
    identity: dict[str, Any] = Depends(current_claim_user),
):
    require_case_permission(identity, case_id, "CASE_UPDATE")
    customer = get_case_storage().assign_case_customer(case_id, request.username.strip())
    return {"case_id": case_id, "customer": customer, "message": "客户已绑定"}


@app.patch("/api/claim/cases/{case_id}/compliance")
def claim_update_compliance(
    case_id: str,
    request: ClaimComplianceUpdateRequest,
    identity: dict[str, Any] = Depends(current_claim_user),
):
    require_case_permission(identity, case_id, "CONSENT_CONFIRM")
    return get_case_storage().update_claim_compliance(
        identity["user_id"],
        case_id,
        consent_status=request.consent_status,
        deidentification_status=request.deidentification_status,
    )


@app.get("/api/claim/tasks")
def claim_tasks(identity: dict[str, Any] = Depends(current_claim_user)):
    require_permission(identity, "CASE_VIEW")
    return get_case_storage().claim_tasks(identity["user_id"])


@app.post("/api/claim/cases/{case_id}/care/trigger")
def claim_trigger_care(case_id: str, identity: dict[str, Any] = Depends(current_claim_user)):
    require_case_permission(identity, case_id, "CARE_TRIGGER")
    return get_case_storage().trigger_care(identity["user_id"], case_id)


@app.post("/api/claim/cases/{case_id}/medical-reviewer")
def claim_assign_medical_reviewer(
    case_id: str,
    request: AssignMedicalReviewerRequest,
    identity: dict[str, Any] = Depends(current_claim_user),
):
    require_case_permission(identity, case_id, "CASE_UPDATE")
    reviewer = get_case_storage().assign_medical_reviewer(case_id, request.username.strip())
    return {"case_id": case_id, "reviewer": reviewer, "message": "医疗复核人员已分配"}


@app.post("/api/claim/cases/{case_id}/documents/process")
async def claim_process_documents(
    case_id: str,
    files: list[UploadFile] = File(...),
    identity: dict[str, Any] = Depends(current_claim_user),
):
    require_case_permission(identity, case_id, "DOCUMENT_UPLOAD")
    if not files:
        raise HTTPException(status_code=400, detail="一个案件至少需要一份材料")
    storage = get_case_storage()
    claim_metadata = storage.get_claim_case_detail(identity["user_id"], case_id)
    if claim_metadata["claim_status"] == "PENDING_ACCEPTANCE":
        raise HTTPException(status_code=409, detail="请先受理案件，再补录或处理医疗材料")
    case_metadata = storage.get_case(case_id)
    if case_metadata["consent_status"] != "authorized" or \
            case_metadata["deidentification_status"] != "confirmed":
        raise HTTPException(status_code=409, detail="仅可处理已获授权并确认脱敏的医疗材料")
    storage.begin_processing(case_id)
    try:
        with tempfile.TemporaryDirectory(prefix="medical-claim-") as tmp:
            documents = await recognize_uploaded_documents(files, Path(tmp), case_id=case_id)
            storage.save_ocr(case_id, documents)
            case = structure_case_with_long_document_support(case_id, documents)
            storage.save_structured(case_id, case)
            return {
                "case_id": case_id,
                "material_status": "completed",
                "analysis_ready": True,
                "message": "材料处理完成，医护分析准备已就绪",
            }
    except httpx.HTTPStatusError as exc:
        logger.exception("DeepSeek API returned an HTTP error")
        raise HTTPException(status_code=502, detail="材料处理失败，请稍后重试或补充材料") from exc
    except httpx.TimeoutException as exc:
        logger.exception("DeepSeek API timed out")
        raise HTTPException(status_code=504, detail="材料处理超时，请稍后重试") from exc
    except Exception as exc:
        logger.exception("Claim document processing failed")
        raise HTTPException(status_code=500, detail="材料处理失败，请重新上传或补充材料") from exc
    finally:
        if storage.get_case(case_id)["status"] == "PROCESSING":
            storage.finish_failed_processing(case_id)


@app.get("/api/medical/dashboard")
def medical_dashboard(identity: dict[str, Any] = Depends(current_medical_user)):
    require_permission(identity, "MEDICAL_CASE_VIEW")
    return get_case_storage().medical_dashboard(identity["user_id"])


@app.get("/api/medical/tasks")
def medical_tasks(identity: dict[str, Any] = Depends(current_medical_user)):
    require_permission(identity, "MEDICAL_CASE_VIEW")
    return get_case_storage().list_case_tasks(identity["user_id"], "MEDICAL")


@app.get("/api/medical/cases")
def medical_cases(
    pending_only: bool = False,
    identity: dict[str, Any] = Depends(current_medical_user),
):
    require_permission(identity, "MEDICAL_CASE_VIEW")
    return get_case_storage().list_medical_cases(identity["user_id"], pending_only=pending_only)


@app.get("/api/medical/cases/{case_id}")
def medical_case_detail(case_id: str, identity: dict[str, Any] = Depends(current_medical_user)):
    require_case_permission(identity, case_id, "MEDICAL_CASE_VIEW")
    storage = get_case_storage()
    detail = storage.get_medical_case_detail(identity["user_id"], case_id)
    metadata = storage.get_case(case_id)
    detail["compliance_ready"] = (
        metadata["consent_status"] == "authorized"
        and metadata["deidentification_status"] == "confirmed"
    )
    # Keep the previous aggregate recommendation view as a compatibility field;
    # the formal MEDICAL page consumes the persisted V2 report in `recommendation`.
    detail["recommendations"] = case_recommendations(storage, case_id)
    try:
        prepared = prepare_delivery_content(storage, case_id)
        detail["review_gate"] = {
            "decision": prepared["gate"].decision.value,
            "reasons": prepared["gate"].reasons,
            "review_targets": [item.value for item in prepared["gate"].review_targets],
            "content_fingerprint": prepared["content_fingerprint"],
        }
    except WorkflowConflict as exc:
        detail["review_gate"] = {"decision": "NOT_READY", "reasons": [str(exc)],
                                 "review_targets": [], "content_fingerprint": None}
    detail["final_gate"] = storage.medical_final_gate(detail).model_dump(mode="json")
    detail["final_review_matrix"] = _medical_review_matrix(detail)
    return detail


def _medical_pain_result(case: ClaimCase, use_llm: bool) -> PainPointReport:
    case = normalize_case(case.model_copy(deep=True))
    semantic_points = []
    warnings: list[str] = []
    if use_llm:
        try:
            semantic_points = semantic_pain_points_with_deepseek(case).pain_points
        except Exception as exc:
            logger.exception("MEDICAL semantic pain analysis failed; using deterministic rules")
            warnings.append(f"语义痛点分析暂不可用，已使用确定性规则：{type(exc).__name__}")
    else:
        warnings.append("本次未调用外部模型，仅运行确定性痛点规则。")
    return analyze_pain_points(case, semantic_points, warnings)


def _medical_care_result(case: ClaimCase, pain: PainPointReport, use_llm: bool) -> CareGuideReport:
    case = normalize_case(case.model_copy(deep=True))
    warnings: list[str] = []
    card = match_knowledge_card(diagnosis_names(case), None)
    disease_topic = infer_disease_topic(case, None, card)
    method = GenerationMethod.RULES_FALLBACK
    if use_llm:
        try:
            require_external_model_authorization(
                get_case_storage().get_case(case.case_id), ExternalModelPurpose.CARE_GUIDE,
            )
            draft = care_guide_with_deepseek(case, pain, disease_topic, card)
            method = GenerationMethod.LLM
        except Exception as exc:
            logger.exception("MEDICAL Care generation failed; using safe fallback")
            warnings.append(f"温度指引外部模型暂不可用，已生成安全降级草稿：{type(exc).__name__}")
            draft = build_fallback_draft(case, pain, card, disease_topic)
    else:
        warnings.append("本次未调用外部模型，仅生成安全降级草稿。")
        draft = build_fallback_draft(case, pain, card, disease_topic)
    return finalize_care_guide(case, draft, card, method, warnings)


@app.post("/api/medical/cases/{case_id}/analysis/prepare")
def prepare_medical_analysis(
    case_id: str,
    request: MedicalAnalysisPrepareRequest,
    identity: dict[str, Any] = Depends(current_medical_user),
):
    """Generate every eligible MEDICAL result from the field-review overlay.

    Failures are isolated per module.  Existing results remain persisted and
    viewable; a failed refresh therefore cannot erase the last reviewable result.
    """
    require_case_permission(identity, case_id, "MEDICAL_CASE_VIEW")
    storage = get_case_storage()
    state = storage.get_effective_case_state(case_id)
    if not state["effective"] or not state["raw"]:
        raise HTTPException(status_code=409, detail="结构化病例尚未生成")
    effective_case = ClaimCase.model_validate(state["effective"])
    raw_case = ClaimCase.model_validate(state["raw"])
    effective_fingerprint = state["effective_fingerprint"]
    results = storage.get_results(case_id)
    pain = PainPointReport.model_validate(results["pain_result"]) if results["pain_result"] else None
    selected = list(dict.fromkeys(request.modules))
    generation_permissions = {
        MedicalModule.PAIN: "PAIN_REVIEW",
        MedicalModule.CARE: "CARE_REVIEW",
        MedicalModule.TREATMENT_QUALITY: "CARE_REVIEW",
        MedicalModule.RECOMMENDATION: "RECOMMEND_REVIEW",
    }
    for module in selected:
        require_permission(identity, generation_permissions[module])
    outcomes: dict[str, dict[str, Any]] = {}

    def unavailable(module: MedicalModule, blockers: list[str], warnings: list[str]):
        outcomes[module.value] = {"status": "INPUT_INSUFFICIENT", "blockers": blockers,
                                  "warnings": warnings}

    for module in selected:
        allowed, blockers, warnings = eligibility(
            module, effective_case, pain_available=pain is not None, city=request.city,
        )
        if not allowed:
            unavailable(module, blockers, warnings)
            continue
        try:
            if module == MedicalModule.PAIN:
                pain = _medical_pain_result(effective_case, request.use_llm)
                storage.save_medical_pain(
                    case_id, pain, raw_case, effective_fingerprint, identity["user_id"],
                )
            elif module == MedicalModule.CARE:
                if pain is None:
                    unavailable(module, ["痛点分析尚未生成"], warnings)
                    continue
                care = _medical_care_result(effective_case, pain, request.use_llm)
                storage.save_medical_care(
                    case_id, care, raw_case, effective_fingerprint, identity["user_id"],
                )
            elif module == MedicalModule.TREATMENT_QUALITY:
                disease_group = resolve_disease_group(effective_case)
                if disease_group == "unavailable":
                    label = primary_disease(effective_case) or "未确认病种"
                    disease_group = "diagnosis:" + re.sub(r"[^\w\u3400-\u9fff-]", "", label).lower()
                evaluation = run_treatment_evaluation(TreatmentEvaluationRequestV2(
                    case=effective_case, disease_group=disease_group, pain_report=pain,
                ))
                storage.save_treatment_evaluation(
                    case_id, evaluation, disease_group, effective_fingerprint, identity["user_id"],
                )
            elif module == MedicalModule.RECOMMENDATION:
                require_external_model_authorization(
                    storage.get_case(case_id), ExternalModelPurpose.RECOMMENDATION_INTENT,
                )
                label = primary_disease(effective_case)
                context = PatientRecommendationContext(
                    case_id=case_id,
                    disease_group=resolve_disease_group(effective_case),
                    disease_label=label,
                    province=request.province,
                    city=request.city,
                    nearby_major_cities=request.nearby_major_cities,
                )
                saved_care = storage.get_results(case_id).get("care_result")
                upstream = None
                if saved_care:
                    upstream = CareGuideReport.model_validate(saved_care).guide.disease_topic
                intent = recommendation_intent_with_deepseek(
                    effective_case, context, upstream_primary_disease=upstream,
                )
                context = context.model_copy(update={
                    "disease_group": disease_group_from_intent(intent),
                    "disease_label": intent.primary_disease,
                })
                report = run_hospital_recommendation(
                    HospitalRecommendationRequestV2(
                        context=context, recommendation_intent=intent, top_k=5,
                    ),
                    historical_records=storage.load_profile_records(),
                )
                clinicians = run_clinician_recommendation(ClinicianRecommendationRequestV2(
                    disease_group=context.disease_group,
                    profiles=report.candidate_clinician_profiles,
                    allowed_hospital_ids=[item.hospital_id for item in report.recommendations],
                    recommendation_intent=intent,
                    top_k=5,
                ))
                saved_recommendation = storage.save_recommendation_report(
                    case_id, report, clinicians, status="DRAFT",
                    effective_fingerprint=effective_fingerprint,
                    user_id=identity["user_id"],
                )
                outcomes[module.value] = {
                    "status": "GENERATED", "blockers": [], "warnings": warnings,
                    "report_version": saved_recommendation["report_version"],
                    "generation_id": saved_recommendation["generation_id"],
                }
                continue
            outcomes[module.value] = {"status": "GENERATED", "blockers": [], "warnings": warnings}
        except Exception as exc:
            logger.exception("MEDICAL module generation failed: %s", module.value)
            outcomes[module.value] = {
                "status": "FAILED", "blockers": [str(exc)], "warnings": warnings,
            }
    return {"case_id": case_id, "outcomes": outcomes,
            "case": storage.get_medical_case_detail(identity["user_id"], case_id)}


def _path_has_value(source: Any, path: str) -> bool:
    current = source
    for name, index in re.findall(r"([^.\[\]]+)(?:\[(\d+)\])?", path):
        if not isinstance(current, dict) or name not in current:
            return False
        current = current[name]
        if index:
            if not isinstance(current, list) or int(index) >= len(current):
                return False
            current = current[int(index)]
    if isinstance(current, dict) and "value" in current:
        return current.get("normalized_value") is not None or current.get("value") is not None
    return current is not None


def _pain_review_payload(result: dict[str, Any], request: MedicalModuleReviewRequest) -> dict[str, Any]:
    known = {item.get("pain_point_id") for item in (result.get("content", result) or {}).get("pain_points", [])}
    decisions = []
    seen = set()
    for raw in request.item_decisions:
        item_id = str(raw.get("pain_point_id") or "").strip()
        decision = str(raw.get("decision") or "").upper()
        if item_id not in known or item_id in seen:
            raise HTTPException(status_code=422, detail="痛点条目已变化，请刷新后重新复核")
        if decision not in {"CONFIRM", "HIDE", "EDIT"}:
            raise HTTPException(status_code=422, detail="痛点复核操作无效")
        item = {"pain_point_id": item_id, "decision": decision}
        if decision == "EDIT":
            for key, label_text in (("title", "标题"), ("description", "说明"),
                                    ("recommended_action", "建议")):
                value = str(raw.get(key) or "").strip()
                if not value:
                    raise HTTPException(status_code=422, detail=f"修改后展示的{label_text}不能为空")
                item[key] = value[:2000]
        decisions.append(item)
        seen.add(item_id)
    return {"item_decisions": decisions}


def _care_review_payload(case_id: str, content: dict[str, Any], effective: dict[str, Any],
                         request: MedicalModuleReviewRequest) -> dict[str, Any]:
    report_data = content.get("content", content)
    guide_data = (request.care_revision or {}).get("guide") or report_data.get("guide")
    try:
        guide = CareGuideDraft.model_validate(guide_data)
    except Exception as exc:
        raise HTTPException(status_code=422, detail="温度指引修订内容不完整，请检查各段文字") from exc
    if guide.case_id != case_id:
        raise HTTPException(status_code=422, detail="温度指引与当前案件不一致")
    structured = ClaimCase.model_validate(effective)
    serialized = structured.model_dump(mode="json")
    invalid = []
    for section_name in ("diagnosis_explanation", "treatment_review", "cost_explanation"):
        section = getattr(guide, section_name)
        if not section.evidence_fields:
            invalid.append(f"{section.title}尚未选择病例依据")
        invalid.extend(f"{section.title}引用不存在：{path}" for path in section.evidence_fields
                       if not _path_has_value(serialized, path))
    for section_name in ("opening", "psychological_support"):
        section = getattr(guide, section_name)
        invalid.extend(f"{section.title}引用不存在：{path}" for path in section.evidence_fields
                       if not _path_has_value(serialized, path))
    for action in [*guide.recovery_plan, *guide.follow_up_plan]:
        invalid.extend(f"行动建议引用不存在：{path}" for path in action.evidence_fields
                       if not _path_has_value(serialized, path))
    card = match_knowledge_card(diagnosis_names(structured), None)
    checks, _, _ = validate_care_guide(structured, guide, card)
    blocked = [item.message for item in checks if item.status.value == "blocked"]
    if request.decision == "APPROVED" and (invalid or blocked):
        raise HTTPException(status_code=409, detail="；".join([*invalid, *blocked]))
    return {"care_revision": {"guide": guide.model_dump(mode="json")},
            "validation": {"invalid_evidence": invalid, "blocked_reasons": blocked}}


def _medical_review_matrix(detail: dict[str, Any]) -> dict[str, Any]:
    fields = detail.get("field_review_summary") or {}
    modules = detail.get("module_states") or {}
    rows = [{
        "module": "structured", "label": "结构化关键字段", "tab": "structured",
        "generation_status": "GENERATED" if detail.get("structured_result") else "NOT_GENERATED",
        "review_status": "REVIEW_REQUIRED" if fields.get("pending") or fields.get("unresolved") else "APPROVED",
        "required_for_delivery": False,
    }]
    labels = {"pain": "痛点分析", "care": "温度指引",
              "treatment_quality": "本次就医品质评价", "recommendation": "推荐"}
    for module in ("pain", "care", "treatment_quality", "recommendation"):
        state = modules.get(module) or {}
        available = bool(state.get("result_available"))
        rows.append({
            "module": module, "label": labels[module],
            "tab": "quality" if module == "treatment_quality" else module,
            "generation_status": state.get("generation_status", "NOT_GENERATED"),
            "review_status": state.get("review_status", "NOT_REVIEWED"),
            "required_for_delivery": module in {"pain", "care"} or available,
        })
    blockers = []
    for row in rows:
        if not row["required_for_delivery"]:
            continue
        if row["generation_status"] == "STALE":
            blockers.append(f"{row['label']}结果已过期")
        elif row["generation_status"] != "GENERATED":
            blockers.append(f"{row['label']}尚未生成")
        elif row["review_status"] != "APPROVED":
            blockers.append(f"{row['label']}尚未通过医疗复核")
    return {"modules": rows, "can_final_approve": not blockers, "blockers": blockers}


@app.post("/api/medical/cases/{case_id}/module-reviews", status_code=201)
def submit_medical_module_review(
    case_id: str,
    request: MedicalModuleReviewRequest,
    identity: dict[str, Any] = Depends(current_medical_user),
):
    require_case_permission(identity, case_id, "MEDICAL_CASE_VIEW")
    if request.decision != "APPROVED" and not request.comment:
        raise HTTPException(status_code=422, detail="退回修改时必须填写复核意见")
    target = {
        MedicalModule.PAIN: ReviewTarget.PAIN_REPORT,
        MedicalModule.CARE: ReviewTarget.CARE_GUIDE,
        MedicalModule.TREATMENT_QUALITY: ReviewTarget.TREATMENT_QUALITY,
        MedicalModule.RECOMMENDATION: ReviewTarget.RECOMMENDATION,
    }[request.module]
    require_permission(identity, {
        MedicalModule.PAIN: "PAIN_REVIEW",
        MedicalModule.CARE: "CARE_REVIEW",
        MedicalModule.TREATMENT_QUALITY: "CARE_REVIEW",
        MedicalModule.RECOMMENDATION: "RECOMMEND_REVIEW",
    }[request.module])
    storage = get_case_storage()
    content = storage.module_review_content(case_id, request.module.value)
    detail = storage.get_medical_case_detail(identity["user_id"], case_id)
    payload = {}
    if request.module == MedicalModule.PAIN:
        payload = _pain_review_payload(content, request)
    elif request.module == MedicalModule.CARE:
        payload = _care_review_payload(
            case_id, content, detail["structured_effective_result"], request,
        )
    record = storage.save_module_review(
        identity["user_id"], case_id, target.value,
        {"APPROVED": ReviewDecision.APPROVED, "RETURNED": ReviewDecision.REJECTED,
         "NEEDS_CONFIRMATION": ReviewDecision.NEEDS_CONFIRMATION}[request.decision],
        request.comment, content, payload,
    )
    return record.model_dump(mode="json")


@app.post("/api/medical/cases/{case_id}/pain-points/{pain_point_id}/review", status_code=201)
def submit_medical_pain_item_review(
    case_id: str,
    pain_point_id: str,
    request: PainItemReviewRequestV1,
    identity: dict[str, Any] = Depends(current_medical_user),
):
    require_case_permission(identity, case_id, "PAIN_REVIEW")
    record = get_case_storage().save_pain_item_review(
        identity["user_id"], case_id, pain_point_id, request,
    )
    return record.model_dump(mode="json")


@app.put("/api/medical/cases/{case_id}/care/revision", status_code=200)
def revise_medical_care_guide(
    case_id: str,
    request: CareRevisionRequestV1,
    identity: dict[str, Any] = Depends(current_medical_user),
):
    require_case_permission(identity, case_id, "CARE_REVIEW")
    storage = get_case_storage()
    state = storage.get_effective_case_state(case_id)
    if not state["effective"]:
        raise WorkflowConflict("结构化病例尚未生成")
    detail = storage.get_medical_case_detail(identity["user_id"], case_id)
    if not detail["care_result"]:
        raise WorkflowConflict("温度指引尚未生成")
    if request.guide.case_id != case_id:
        raise WorkflowConflict("修订稿 case_id 与当前案件不一致")
    case = ClaimCase.model_validate(state["effective"])
    original = CareGuideReport.model_validate(detail["care_result"])
    card = match_knowledge_card(diagnosis_names(case), None)
    revised = finalize_care_guide(
        case, request.guide, card, original.generation_method, [],
    )
    record = storage.save_care_revision(
        identity["user_id"], case_id, request, revised,
    )
    return record.model_dump(mode="json")


@app.post("/api/medical/cases/{case_id}/review", status_code=201)
def submit_medical_review(
    case_id: str,
    request: MedicalReviewRequest,
    identity: dict[str, Any] = Depends(current_medical_user),
):
    require_case_permission(identity, case_id, "MEDICAL_CASE_VIEW")
    require_permission(identity, "DELIVERY_APPROVE" if request.decision == "APPROVED" else "DELIVERY_BLOCK")
    if request.decision != "APPROVED" and not request.comment:
        raise HTTPException(status_code=422, detail="退回修改时必须填写复核意见")
    storage = get_case_storage()
    if request.decision == "APPROVED":
        detail = storage.get_medical_case_detail(identity["user_id"], case_id)
        final_gate = storage.medical_final_gate(detail)
        if final_gate.decision != "READY_FOR_FINAL_REVIEW":
            raise WorkflowConflict("最终发布门禁未通过：" + "；".join(final_gate.reasons))
        matrix = _medical_review_matrix(detail)
        if not matrix["can_final_approve"]:
            raise HTTPException(status_code=409, detail="；".join(matrix["blockers"]))
    prepared = prepare_delivery_content(storage, case_id)
    if request.decision == "APPROVED" and prepared["gate"].decision.value == "BLOCKED":
        raise HTTPException(status_code=409, detail="安全门禁已阻断，不能批准发布")
    decision = {
        "APPROVED": ReviewDecision.APPROVED,
        "RETURNED": ReviewDecision.REJECTED,
        "NEEDS_CONFIRMATION": ReviewDecision.NEEDS_CONFIRMATION,
    }[request.decision]
    record = storage.save_review(
        prepared,
        ReviewRequest(
            target_type=ReviewTarget.COMBINED_DELIVERY,
            reviewer=identity["display_name"],
            decision=decision,
            note=request.comment,
            content_fingerprint=prepared["content_fingerprint"],
        ),
        reviewer_user_id=identity["user_id"],
        allow_low_risk_combined=True,
    )
    return {"review": record.model_dump(mode="json"),
            "review_status": storage.get_medical_case_detail(identity["user_id"], case_id)["review_status"]}


@app.post("/api/medical/cases/{case_id}/field-reviews", status_code=201)
def submit_medical_field_review(
    case_id: str,
    request: FieldReviewRequest,
    identity: dict[str, Any] = Depends(current_medical_user),
):
    require_case_permission(identity, case_id, "MEDICAL_CASE_VIEW")
    record = get_case_storage().save_field_review(identity["user_id"], case_id, request)
    return record.model_dump(mode="json")


@app.get("/api/medical/cases/{case_id}/documents/{resource_id}")
def medical_document_preview(
    case_id: str,
    resource_id: str,
    identity: dict[str, Any] = Depends(current_medical_user),
):
    require_case_permission(identity, case_id, "MEDICAL_CASE_VIEW")
    try:
        resource = get_case_storage().get_medical_document_resource(
            identity["user_id"], case_id, resource_id
        )
    except (CaseNotFound, WorkflowItemNotFound):
        raise HTTPException(status_code=404, detail="医疗材料不存在或当前账号无权访问")
    case_dir = (private_document_root() / case_id).resolve()
    path = (case_dir / resource["stored_filename"]).resolve()
    if case_dir not in path.parents or not path.is_file():
        raise HTTPException(status_code=404, detail="医疗材料文件未保留")
    return FileResponse(path, media_type=resource["media_type"],
                        headers={"Cache-Control": "private, no-store"})


@app.patch("/api/medical/cases/{case_id}/structured")
def medical_update_structured_case(
    case_id: str,
    request: ClaimCase,
    identity: dict[str, Any] = Depends(current_medical_user),
):
    require_case_permission(identity, case_id, "STRUCTURED_EDIT")
    if request.case_id != case_id:
        raise HTTPException(status_code=422, detail="结构化结果 case_id 与当前案件不一致")
    detail = get_case_storage().get_medical_case_detail(identity["user_id"], case_id)
    if not detail["compliance_ready"]:
        raise HTTPException(status_code=409, detail="案件授权或脱敏状态未通过，不能保存结构化结果")
    get_case_storage().save_structured(case_id, request)
    return {"case_id": case_id, "message": "第一问结构化修正已保存；下游结果已按规则失效"}


@app.post("/api/medical/cases/{case_id}/pain/analyze")
def medical_analyze_pain(
    case_id: str,
    request: MedicalAnalysisRequest,
    identity: dict[str, Any] = Depends(current_medical_user),
):
    require_case_permission(identity, case_id, "PAIN_REVIEW")
    saved = get_case_storage().get_medical_case_detail(identity["user_id"], case_id)
    if saved["structured_result"] is None:
        raise HTTPException(status_code=409, detail="请先完成并保存第一问结构化结果")
    return analyze_case_pain_points(PainAnalysisRequest(
        case=ClaimCase.model_validate(saved["structured_result"]), use_llm=request.use_llm,
    ))


@app.post("/api/medical/cases/{case_id}/care/generate")
def medical_generate_care(
    case_id: str,
    request: MedicalCareRequest,
    identity: dict[str, Any] = Depends(current_medical_user),
):
    require_case_permission(identity, case_id, "CARE_REVIEW")
    saved = get_case_storage().get_medical_case_detail(identity["user_id"], case_id)
    if saved["structured_result"] is None:
        raise HTTPException(status_code=409, detail="请先完成并保存第一问结构化结果")
    if saved["pain_result"] is None:
        raise HTTPException(status_code=409, detail="请先完成第二问痛点分析")
    return generate_case_care_guide(CareGuideRequest(
        case=ClaimCase.model_validate(saved["structured_result"]),
        pain_report=PainPointReport.model_validate(saved["pain_result"]),
        disease_topic=request.disease_topic.strip() if request.disease_topic else None,
        use_llm=request.use_llm,
    ))


@app.post("/api/medical/cases/{case_id}/delivery/prepare")
def medical_prepare_delivery(
    case_id: str,
    identity: dict[str, Any] = Depends(current_medical_user),
):
    require_case_permission(identity, case_id, "DELIVERY_APPROVE")
    storage = get_case_storage()
    return storage.prepare_delivery(prepare_delivery_content(storage, case_id))


@app.post("/api/medical/cases/{case_id}/mobile-preview", status_code=201)
def medical_create_mobile_preview(
    case_id: str,
    request: MobilePreviewCreateRequest,
    identity: dict[str, Any] = Depends(current_medical_user),
):
    require_case_permission(identity, case_id, "DELIVERY_APPROVE")
    return _create_mobile_preview_payload(case_id, request)


@app.post("/api/medical/cases/{case_id}/delivery/review", status_code=201)
def medical_review_delivery(
    case_id: str,
    request: MedicalDeliveryReviewRequest,
    identity: dict[str, Any] = Depends(current_medical_user),
):
    permission = "DELIVERY_APPROVE" if request.decision == ReviewDecision.APPROVED else "DELIVERY_BLOCK"
    require_case_permission(identity, case_id, permission)
    storage = get_case_storage()
    prepared = prepare_delivery_content(storage, case_id)
    return storage.save_review(prepared, ReviewRequest(
        reviewer=identity["display_name"], decision=request.decision,
        target_type=request.target_type, note=request.note,
        content_fingerprint=request.content_fingerprint,
    ), reviewer_user_id=identity["user_id"], allow_low_risk_combined=True)


@app.post("/api/medical/cases/{case_id}/delivery/send")
def medical_send_delivery(
    case_id: str,
    request: SendDeliveryRequest | None = None,
    identity: dict[str, Any] = Depends(current_medical_user),
):
    require_case_permission(identity, case_id, "DELIVERY_APPROVE")
    storage = get_case_storage()
    detail = storage.get_medical_case_detail(identity["user_id"], case_id)
    final_gate = storage.medical_final_gate(detail)
    if final_gate.decision != "READY_FOR_FINAL_REVIEW":
        raise WorkflowConflict("最终发布门禁未通过：" + "；".join(final_gate.reasons))
    if detail["review_status"] != "APPROVED":
        raise WorkflowConflict("请先完成案件级医疗发布审核")
    matrix = _medical_review_matrix(detail)
    if not matrix["can_final_approve"]:
        raise WorkflowConflict("拒绝发送：" + "；".join(matrix["blockers"]))
    prepared = prepare_delivery_content(storage, case_id)
    if prepared["gate"].decision.value == "BLOCKED":
        raise WorkflowConflict("拒绝发送：安全门禁已阻断")
    if storage.get_medical_case_detail(identity["user_id"], case_id)["review_status"] != "APPROVED":
        raise WorkflowConflict("拒绝发送：最终医疗复核尚未批准")
    if request and request.content_fingerprint and request.content_fingerprint != prepared["content_fingerprint"]:
        raise WorkflowConflict("触达页面内容已过时，请重新准备")
    return storage.send_delivery(prepared)


@app.get("/api/customer/dashboard")
def customer_dashboard(identity: dict[str, Any] = Depends(current_customer_user)):
    require_permission(identity, "OWN_CASE_VIEW")
    return get_case_storage().customer_dashboard(identity["user_id"])


@app.get("/api/customer/cases")
def customer_cases(identity: dict[str, Any] = Depends(current_customer_user)):
    require_permission(identity, "OWN_CASE_VIEW")
    return get_case_storage().list_customer_cases(identity["user_id"])


@app.post("/api/customer/cases", status_code=201)
def customer_create_case(
    request: CustomerCaseCreateRequest,
    identity: dict[str, Any] = Depends(current_customer_user),
):
    require_permission(identity, "OWN_CASE_VIEW")
    claim_event_id = request.claim_event_id or f"CUS-{uuid4().hex[:12].upper()}"
    return get_case_storage().create_customer_case(
        CreateCaseRequest(
            claim_event_id=claim_event_id,
            data_source="customer_self_service",
            consent_status="pending",
            deidentification_status="pending",
        ),
        identity["user_id"],
    )


@app.get("/api/customer/cases/{case_id}")
def customer_case_detail(
    case_id: str,
    identity: dict[str, Any] = Depends(current_customer_user),
):
    require_case_permission(identity, case_id, "OWN_CASE_VIEW")
    return get_case_storage().get_customer_case_detail(identity["user_id"], case_id)


@app.get("/api/customer/feedback")
def customer_feedback(identity: dict[str, Any] = Depends(current_customer_user)):
    require_permission(identity, "OWN_CASE_VIEW")
    return get_case_storage().list_customer_feedback(identity["user_id"])


@app.post("/api/customer/deliveries/{delivery_id}/feedback", status_code=201)
def customer_submit_feedback(
    delivery_id: str,
    request: FeedbackRequest,
    identity: dict[str, Any] = Depends(current_customer_user),
):
    require_permission(identity, "FEEDBACK_SUBMIT")
    return get_case_storage().save_customer_feedback(identity["user_id"], delivery_id, request)


@app.get("/api/service/dashboard")
def service_dashboard(identity: dict[str, Any] = Depends(current_service_user)):
    require_permission(identity, "SERVICE_CASE_SUMMARY_VIEW")
    return get_case_storage().service_dashboard(identity["user_id"])


@app.get("/api/service/cases")
def service_cases(identity: dict[str, Any] = Depends(current_service_user)):
    require_permission(identity, "SERVICE_CASE_SUMMARY_VIEW")
    return get_case_storage().list_service_cases(identity["user_id"])


@app.get("/api/service/consultations")
def service_consultations(identity: dict[str, Any] = Depends(current_service_user)):
    require_permission(identity, "SERVICE_SESSION_VIEW")
    return get_case_storage().list_service_consultations(identity["user_id"])


@app.post("/api/service/consultations", status_code=201)
def create_service_consultation(
    request: ServiceConsultationCreateRequest,
    identity: dict[str, Any] = Depends(current_service_user),
):
    require_permission(identity, "SERVICE_SESSION_CREATE")
    if not request.consent_confirmed:
        raise HTTPException(status_code=400, detail="请先确认客户已授权记录本次咨询")
    if not request.deidentification_confirmed:
        raise HTTPException(status_code=400, detail="请先确认咨询内容已完成脱敏")
    return get_case_storage().create_service_consultation(
        identity["user_id"], case_id=request.case_id, channel=request.channel,
        deidentified_text=request.deidentified_text.strip(), category=request.category.value,
        priority=request.priority.value,
    )


@app.post("/api/service/consultations/{analysis_id}/route")
def route_service_consultation(
    analysis_id: str,
    request: ServiceConsultationRouteRequest,
    identity: dict[str, Any] = Depends(current_service_user),
):
    require_permission(identity, "FOLLOWUP_ASSIGN")
    return get_case_storage().route_service_consultation(
        identity["user_id"], analysis_id,
        target_role=request.target_role.value,
        category=request.category.value,
        priority=request.priority,
        note=request.note,
    )


@app.get("/api/service/follow-ups")
def service_follow_ups(
    status: Literal["OPEN", "PROCESSING", "RESOLVED", "CLOSED"] | None = Query(default=None),
    identity: dict[str, Any] = Depends(current_service_user),
):
    require_permission(identity, "FOLLOWUP_VIEW")
    return get_case_storage().list_service_follow_ups(identity["user_id"], status=status)


@app.post("/api/service/follow-ups/{task_id}/claim")
def claim_service_follow_up(
    task_id: str,
    identity: dict[str, Any] = Depends(current_service_user),
):
    require_permission(identity, "FOLLOWUP_ASSIGN")
    return get_case_storage().claim_service_follow_up(identity["user_id"], task_id)


@app.post("/api/service/follow-ups/{task_id}/resolve")
def resolve_service_follow_up(
    task_id: str,
    request: ResolveFollowUpRequest,
    identity: dict[str, Any] = Depends(current_service_user),
):
    require_permission(identity, "FOLLOWUP_RESOLVE")
    return get_case_storage().resolve_service_follow_up(
        identity["user_id"], task_id, request.resolution,
    )


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
    files: list[UploadFile], tmp_path: Path, *, case_id: str | None = None
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
        content = await upload.read()
        target.write_bytes(content)
        original_name = normalize_document_id(upload.filename or target.name)
        document_id = (
            original_name
            if filename_counts[original_name] == 1
            else f"{page:04d}-{original_name}"
        )
        document = recognize_image(target, document_id, page)
        if case_id is not None:
            resource_id = uuid4().hex
            case_dir = private_document_root() / case_id
            case_dir.mkdir(parents=True, exist_ok=True)
            stored_filename = f"{resource_id}{suffix}"
            (case_dir / stored_filename).write_bytes(content)
            document.update({
                "resource_id": resource_id,
                "stored_filename": stored_filename,
                "media_type": {
                    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
                    ".bmp": "image/bmp", ".webp": "image/webp",
                }[suffix],
            })
        documents.append(document)
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


def recommendation_intent_with_deepseek(
    case: ClaimCase,
    context,
    upstream_primary_disease: str | None = None,
) -> RecommendationIntent:
    """LLM 只理解病例和规划检索，不生成医院能力结论或最终推荐。"""
    body = build_recommendation_planner_body(
        case,
        context,
        upstream_primary_disease,
        model=os.getenv("DEEPSEEK_MODEL", "deepseek-flash"),
        max_output_tokens=int(os.getenv("DEEPSEEK_RECOMMENDATION_INTENT_MAX_OUTPUT_TOKENS", "6000")),
    )
    output_text = extract_output_text(post_deepseek(body))
    intent = RecommendationIntent.model_validate_json(normalize_json_output(output_text))
    return validate_recommendation_intent(intent, case, context, upstream_primary_disease)


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
    metadata = storage.get_case(case_id)
    if metadata["consent_status"] != "authorized" or metadata["deidentification_status"] != "confirmed":
        raise WorkflowConflict("OCR文本进入外部模型前必须确认已授权并完成脱敏")
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
    metadata = storage.get_case(case_id)
    if metadata["consent_status"] != "authorized" or metadata["deidentification_status"] != "confirmed":
        raise WorkflowConflict("OCR文本进入外部模型前必须确认已授权并完成脱敏")
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


@app.post("/api/cases/ocr-only", response_model=OcrOnlyProcessingResult)
async def run_local_ocr_only(
    case_id: str = Form(...),
    files: list[UploadFile] = File(...),
) -> OcrOnlyProcessingResult:
    """Run local OCR and persist its result without calling the external LLM."""

    if not files:
        raise HTTPException(status_code=400, detail="一个案件至少需要一份材料")
    storage = get_case_storage()
    metadata = storage.get_case(case_id)
    if metadata["consent_status"] != "authorized":
        raise WorkflowConflict("运行本地OCR前必须先确认材料使用授权")
    storage.begin_processing(case_id)
    try:
        with tempfile.TemporaryDirectory(prefix="medical-claim-ocr-") as tmp:
            documents = await recognize_uploaded_documents(files, Path(tmp))
            storage.save_ocr(case_id, documents)
            storage.finish_failed_processing(case_id)
            return OcrOnlyProcessingResult(
                case_id=case_id,
                documents=[OcrDocumentPayload.model_validate(item) for item in documents],
            )
    except Exception as exc:
        logger.exception("Local OCR-only processing failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        if storage.get_case(case_id)["status"] == "PROCESSING":
            storage.finish_failed_processing(case_id)


@app.post("/api/cases/restructure-from-ocr", response_model=ClaimCase)
def restructure_case_from_ocr(request: OcrRestructureRequest) -> ClaimCase:
    """使用人工校正后的逐页OCR文本重新结构化，不再次识别图片。"""

    storage = get_case_storage()
    documents = [document.model_dump() for document in request.documents]
    server_findings = sensitive_ocr_findings(documents)
    if request.deidentification_confirmed:
        if server_findings and not request.manual_override_confirmed:
            raise WorkflowConflict(
                "脱敏规则仍检测到疑似身份格式：" + "、".join(server_findings)
                + "；请继续修改，或完成逐项人工复核后明确确认规则覆核"
            )
        fingerprint_source = json.dumps(
            documents, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        storage.confirm_deidentification(
            request.case_id,
            review_method="manual_override" if server_findings else "rule_clear",
            findings=server_findings,
            document_fingerprint=hashlib.sha256(fingerprint_source.encode("utf-8")).hexdigest(),
        )
    metadata = storage.get_case(request.case_id)
    if metadata["consent_status"] != "authorized" or metadata["deidentification_status"] != "confirmed":
        raise WorkflowConflict("OCR文本进入外部模型前必须确认已授权并完成脱敏")
    storage.begin_processing(request.case_id)
    try:
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


@app.post("/api/cases/save-reviewed-ocr")
def save_reviewed_ocr(request: OcrReviewSaveRequest) -> dict[str, Any]:
    """Persist edited OCR locally without confirming deidentification or calling an LLM."""

    storage = get_case_storage()
    documents = [document.model_dump() for document in request.documents]
    storage.save_ocr_review_draft(request.case_id, documents)
    return storage.get_case(request.case_id)


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
            require_external_model_authorization(
                storage.get_case(case.case_id),
                ExternalModelPurpose.CARE_GUIDE,
            )
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


@app.get("/api/quality/v2/catalog", response_model=DiseaseProviderCatalog)
def get_phase1_provider_catalog() -> DiseaseProviderCatalog:
    """读取已核验比赛基准目录；该目录不是病种支持白名单。"""
    try:
        return load_catalog(DEFAULT_CATALOG_PATH)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=503, detail="Phase 1 候选医院目录不可用") from exc


@app.post("/api/quality/v2/provider-discovery", response_model=ProviderDiscoveryResult)
def discover_phase1_providers(request: ProviderDiscoveryQuery) -> ProviderDiscoveryResult:
    """按任意明确病种查询可信缓存，并在部署层可用时调用动态发现 provider。"""
    try:
        return run_provider_discovery(request)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=503, detail="医疗机构能力发现服务不可用") from exc


@app.post("/api/quality/v2/provider-cache/verified")
def write_verified_phase1_provider_cache(request: CapabilityCacheWriteRequest) -> dict[str, int]:
    """仅接收已经通过统一证据门槛的能力画像，供后续病例复用。"""
    try:
        return LocalCapabilityCache().write_verified(request)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=503, detail="本地可信能力缓存不可用") from exc


@app.post(
    "/api/quality/v2/recommendations/hospitals",
    response_model=HospitalRecommendationReportV2,
)
def get_phase1_hospital_recommendations(
    request: HospitalRecommendationRequestV2,
) -> HospitalRecommendationReportV2:
    """能力证据主导的新医院推荐；旧记录只作为可选历史事实补充。"""
    try:
        intent = request.recommendation_intent
        if request.case is not None:
            if request.case.case_id != request.context.case_id:
                raise ValueError("推荐案件与当前上下文 case_id 不一致")
            storage = get_case_storage()
            storage.require_current(request.context.case_id, request.case)
            require_external_model_authorization(
                storage.get_case(request.context.case_id),
                ExternalModelPurpose.RECOMMENDATION_INTENT,
            )
            saved_results = storage.get_results(request.context.case_id)
            saved_care = saved_results.get("care_result")
            upstream_primary_disease = None
            if saved_care:
                care_report = CareGuideReport.model_validate(saved_care)
                if care_report.case_id == request.context.case_id:
                    upstream_primary_disease = care_report.guide.disease_topic
            intent = recommendation_intent_with_deepseek(
                request.case,
                request.context,
                upstream_primary_disease=upstream_primary_disease,
            )
            request = request.model_copy(update={"recommendation_intent": intent})
        records = get_case_storage().load_profile_records()
        return run_hospital_recommendation(request, historical_records=records)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="RecommendationIntent 外部模型调用失败") from exc
    except ExternalModelAuthorizationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=503, detail="Phase 1 推荐所需目录不可用") from exc


@app.post(
    "/api/quality/v2/recommendations/clinicians",
    response_model=list[ClinicianRecommendation],
)
def get_phase1_clinician_recommendations(
    request: ClinicianRecommendationRequestV2,
) -> list[ClinicianRecommendation]:
    return run_clinician_recommendation(request)


@app.post(
    "/api/quality/v2/treatment-evaluation",
    response_model=CurrentTreatmentEvaluation,
)
def get_phase1_treatment_evaluation(
    request: TreatmentEvaluationRequestV2,
) -> CurrentTreatmentEvaluation:
    return run_treatment_evaluation(request)


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
    effective = storage.get_effective_case_state(case_id)["effective"]
    case = ClaimCase.model_validate(effective) if effective else None
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

    return _create_mobile_preview_payload(case_id, request)


def _create_mobile_preview_payload(case_id: str, request: MobilePreviewCreateRequest):
    storage = get_case_storage()
    prepared = prepare_delivery_content(storage, case_id)
    readiness = storage.prepare_delivery(prepared)
    if readiness["decision"] == "BLOCKED":
        raise WorkflowConflict("当前内容被安全门禁阻断，不能生成手机预览")

    created = storage.create_mobile_preview(
        case_id,
        ttl_minutes=request.expires_in_minutes,
        delivery_content=prepared["content"],
        content_fingerprint=prepared["content_fingerprint"],
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
