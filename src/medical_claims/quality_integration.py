"""Case wiring around existing profiling/recommendation functions."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import secrets

from .models import ClaimCase
from .pain_models import PainPointReport
from .profile_records import ProfileRecordInput, ConfirmedProviderContext, build_profile_case_record, resolve_disease_group
from .provider_extraction import build_safe_context
from .provider_models import CaseProviderContext
from .providers import OcrDocument, OcrBlock
from .quality_engine import build_quality_portfolio, recommend_profiles
from .quality_models import ProfileEntityType, MetricStatus, RecommendationReport
from .storage import CaseStorage, WorkflowConflict


def provider_context_from_saved_ocr(case_id, documents):
    """Reuse the audit HMAC key; return safe candidates, never auto-confirm them."""
    key_path = Path(os.getenv("PROVIDER_HMAC_KEY_PATH") or
                    Path(__file__).resolve().parents[2] / "work/provider_audit/private/hmac.key")
    key_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with key_path.open("x", encoding="ascii") as handle:
            handle.write(secrets.token_bytes(32).hex())
    except FileExistsError:
        pass
    secret = bytes.fromhex(key_path.read_text(encoding="ascii").strip())
    ocr_documents = []
    for doc in documents or []:
        safe_id = "IMG-" + hashlib.sha256(doc["document_id"].encode()).hexdigest()[:12].upper()
        blocks = doc.get("blocks", []) if not doc.get("human_corrected") else []
        if not blocks:
            blocks = [{"text": doc.get("full_text", ""), "confidence": None}]
        ocr_documents.append(OcrDocument(document_id=safe_id, blocks=[
            OcrBlock(text=block["text"], page=doc["page"], confidence=block.get("confidence"), bbox=block.get("bbox"))
            for block in blocks]))
    context, _private_mapping = build_safe_context(case_id, ocr_documents, secret)
    return context


def current_profile_input(storage: CaseStorage, case_id: str, provider=None):
    metadata = storage.get_case(case_id)
    results = storage.get_results(case_id)
    effective_state = storage.get_effective_case_state(case_id)
    if effective_state["effective"] is None:
        raise WorkflowConflict("请先保存结构化病例")
    case = ClaimCase.model_validate(effective_state["effective"])
    settings = storage.get_profile_context(case_id)
    context = provider or (ConfirmedProviderContext.model_validate(settings["provider"]) if settings else
                          ConfirmedProviderContext(context=CaseProviderContext(case_id=case_id)))
    return ProfileRecordInput(
        case=case, provider=context, data_source=metadata["data_source"],
        pain_report=PainPointReport.model_validate(results["pain_result"]) if results["pain_result"] else None,
        stable_patient_token=settings.get("stable_patient_token") if settings else None,
        readmitted_within_30_days=settings.get("readmitted_within_30_days") if settings else None,
        patient_feedback_score=settings.get("patient_feedback_score") if settings else None,
    )


def build_saved_portfolio(storage: CaseStorage, minimum_sample_size=3):
    # Reassemble workflow rows from their current saved medical results.
    for case_id in storage.profile_context_case_ids():
        payload = current_profile_input(storage, case_id)
        assembly = build_profile_case_record(payload)
        storage.save_profile_record(payload, assembly, source_kind="workflow")
    records = storage.load_profile_records()
    portfolio = build_quality_portfolio(records, minimum_sample_size)
    storage.save_portfolio(portfolio, records)
    return portfolio


def case_recommendations(storage: CaseStorage, case_id: str):
    metadata = storage.get_case(case_id)
    saved = storage.get_results(case_id)
    effective = storage.get_effective_case_state(case_id)["effective"]
    case = ClaimCase.model_validate(effective) if effective else None
    disease = resolve_disease_group(case) if case else "unavailable"
    portfolio = storage.load_portfolio()
    assembly = build_profile_case_record(current_profile_input(storage, case_id)) if case else None
    context_warnings = assembly.warnings if assembly else ["尚未结构化"]
    reports = []
    care_ready = saved["care_result"] is not None and metadata["status"] != "PROCESSING"
    for entity_type in ProfileEntityType:
        reasons = []
        if not care_ready:
            reasons.append("当前案件尚未完成前三问并保存温度指引")
        if disease == "unavailable":
            reasons.append("Disease group unavailable：无法可靠确定当前病种")
        if not assembly or assembly.institution_id == "unavailable":
            reasons.append("Current provider context unconfirmed：请提供已确认医院来源上下文")
        if portfolio is None:
            reasons.append("Not Evaluated：尚未构建当前历史数据的 portfolio")
        if reasons:
            status = "not_evaluated" if portfolio is None or not care_ready else "insufficient_evidence"
            report = RecommendationReport(disease_group=disease, entity_type=entity_type,
                                          eligible_candidates=0, excluded_candidates=0, warnings=reasons)
        else:
            report = recommend_profiles(portfolio, disease, entity_type)
            status = "available" if report.generated else "insufficient_evidence"
            if not report.generated:
                relevant = [p for p in portfolio.profiles if p.disease_group == disease and p.entity_type == entity_type]
                report.warnings.append(f"Insufficient historical data：{len(relevant)}个同病种候选；每主体最低样本{portfolio.minimum_sample_size}")
                for profile in relevant:
                    available = sum(m.status == MetricStatus.CALCULATED and m.score is not None for m in profile.metrics)
                    report.warnings.append(f"{profile.entity_id}: samples={profile.sample_size}, calculated dimensions={available}/minimum 3, confidence={profile.composite_confidence}")
        reports.append({"status": status, "report": report.model_dump(mode="json")})
    dataset = storage.profile_dataset_summary()
    if dataset["contains_non_real_data"]:
        for entry in reports:
            entry["report"]["warnings"].append("包含synthetic_test或未知来源，仅用于接线验证，不得用于真实医疗推荐/KR4证明")
    return {"case_id": case_id, "disease_group": disease, "dataset": dataset,
            "provider_context": assembly.model_dump(mode="json", exclude={"record"}) if assembly else None,
            "context_warnings": context_warnings, "reports": reports,
            "portfolio_evaluated": portfolio is not None}
