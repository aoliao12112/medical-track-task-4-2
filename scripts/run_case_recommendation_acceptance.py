"""以 SQLite 当前结构化病例运行 LLM 意图、真实联网发现和 V2 排序验收。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from medical_claims.models import ClaimCase
from medical_claims.care_models import CareGuideReport
from medical_claims.external_model_policy import (
    ExternalModelPurpose,
    require_external_model_authorization,
)
from medical_claims.quality_phase1_models import (
    ClinicianRecommendationRequestV2,
    HospitalRecommendationRequestV2,
    PatientRecommendationContext,
)
from medical_claims.quality_phase1_service import (
    build_default_discovery_service,
    run_clinician_recommendation,
    run_hospital_recommendation,
)
from medical_claims.storage import CaseStorage
from web.backend.app import recommendation_intent_with_deepseek


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--city", required=True)
    parser.add_argument("--province", default=None)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument(
        "--cache", type=Path,
        default=ROOT / "outputs" / "case_recommendation_acceptance_cache.json",
    )
    parser.add_argument(
        "--output", type=Path,
        default=ROOT / "outputs" / "case_recommendation_acceptance.json",
    )
    args = parser.parse_args()
    load_dotenv(ROOT / "web" / "backend" / ".env")

    storage = CaseStorage()
    require_external_model_authorization(
        storage.get_case(args.case_id),
        ExternalModelPurpose.RECOMMENDATION_INTENT,
    )
    saved_results = storage.get_results(args.case_id)
    raw = saved_results.get("structured_result")
    if raw is None:
        raise RuntimeError("案件尚无结构化结果")
    case = ClaimCase.model_validate(raw)
    context = PatientRecommendationContext(
        case_id=case.case_id,
        disease_group="pending_llm_intent",
        city=args.city,
        province=args.province,
    )
    saved_care = saved_results.get("care_result")
    upstream_primary_disease = (
        CareGuideReport.model_validate(saved_care).guide.disease_topic
        if saved_care else None
    )
    intent = recommendation_intent_with_deepseek(
        case,
        context,
        upstream_primary_disease=upstream_primary_disease,
    )
    report = run_hospital_recommendation(
        HospitalRecommendationRequestV2(
            context=context,
            case=case,
            recommendation_intent=intent,
            top_k=args.top_k,
        ),
        historical_records=storage.load_profile_records(),
        discovery_service=build_default_discovery_service(cache_path=args.cache),
    )
    clinician_recommendations = run_clinician_recommendation(ClinicianRecommendationRequestV2(
        disease_group=report.context.disease_group,
        profiles=report.candidate_clinician_profiles,
        allowed_hospital_ids=[item.hospital_id for item in report.recommendations],
        recommendation_intent=report.recommendation_intent,
        top_k=args.top_k,
    ))
    payload = report.model_dump(mode="json")
    payload["clinician_recommendations"] = [item.model_dump(mode="json") for item in clinician_recommendations]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    diagnostics = report.discovery_diagnostics
    print(json.dumps({
        "primary_disease": intent.primary_disease,
        "primary_specialty": intent.primary_specialty,
        "query_count": len(diagnostics.generated_queries) if diagnostics else 0,
        "search_result_count": diagnostics.search_result_count if diagnostics else 0,
        "page_read_count": diagnostics.page_read_count if diagnostics else 0,
        "hospital_count": len(report.recommendations),
        "clinician_count": len(clinician_recommendations),
        "output": str(args.output),
    }, ensure_ascii=False))
    return 0 if report.recommendations else 2


if __name__ == "__main__":
    raise SystemExit(main())
