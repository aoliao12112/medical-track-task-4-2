"""真实运行第四问 V2 联网发现，并保存可复核诊断结果。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

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


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--disease", required=True, help="明确疾病名称，如：白内障")
    parser.add_argument("--disease-group", default=None, help="稳定病种标识；默认自动生成")
    parser.add_argument("--city", required=True)
    parser.add_argument("--province", default=None)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument(
        "--output", type=Path,
        default=ROOT / "outputs" / "provider_discovery_diagnostic.json",
    )
    parser.add_argument(
        "--cache", type=Path,
        default=ROOT / "outputs" / "provider_discovery_diagnostic_cache.json",
        help="诊断专用可信缓存，避免覆盖正在使用的案件缓存",
    )
    args = parser.parse_args()
    group = args.disease_group or "diagnosis:" + "-".join(args.disease.split())
    report = run_hospital_recommendation(HospitalRecommendationRequestV2(
        context=PatientRecommendationContext(
            case_id="DISCOVERY-DIAGNOSTIC",
            disease_group=group,
            disease_label=args.disease,
            city=args.city,
            province=args.province,
        ),
        top_k=args.top_k,
    ), discovery_service=build_default_discovery_service(cache_path=args.cache))
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
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    diagnostics = report.discovery_diagnostics
    print(json.dumps({
        "disease": args.disease,
        "city": args.city,
        "query_count": len(diagnostics.generated_queries) if diagnostics else 0,
        "search_result_count": diagnostics.search_result_count if diagnostics else 0,
        "page_read_count": diagnostics.page_read_count if diagnostics else 0,
        "hospital_identified_count": diagnostics.hospital_identified_count if diagnostics else 0,
        "admitted_hospital_count": diagnostics.admitted_hospital_count if diagnostics else 0,
        "recommendation_count": len(report.recommendations),
        "clinician_recommendation_count": len(clinician_recommendations),
        "output": str(args.output),
    }, ensure_ascii=False))
    return 0 if report.recommendations else 2


if __name__ == "__main__":
    raise SystemExit(main())
