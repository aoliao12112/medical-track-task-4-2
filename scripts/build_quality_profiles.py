"""从第四问标准病例记录批量生成品质画像和可解释推荐。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from medical_claims.quality_engine import build_quality_portfolio, recommend_profiles  # noqa: E402
from medical_claims.quality_models import (  # noqa: E402
    ProfileCaseRecord,
    ProfileEntityType,
    QualityPortfolioRequest,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="ProfileCaseRecord 数组或 QualityPortfolioRequest JSON")
    parser.add_argument("output", type=Path, help="输出目录")
    parser.add_argument("--minimum-sample-size", type=int, default=3)
    parser.add_argument("--top-k", type=int, default=5)
    return parser.parse_args()


def dump(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    args = parse_args()
    payload = json.loads(args.input.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        records = [ProfileCaseRecord.model_validate(item) for item in payload]
        minimum = args.minimum_sample_size
    else:
        request = QualityPortfolioRequest.model_validate(payload)
        records = request.records
        minimum = request.minimum_sample_size

    portfolio = build_quality_portfolio(records, minimum)
    dump(args.output / "quality-portfolio.json", portfolio.model_dump(mode="json"))

    recommendation_reports = []
    disease_groups = sorted({profile.disease_group for profile in portfolio.profiles})
    for disease in disease_groups:
        for entity_type in (
            ProfileEntityType.INSTITUTION,
            ProfileEntityType.CLINICIAN,
            ProfileEntityType.TREATMENT_PLAN,
        ):
            report = recommend_profiles(portfolio, disease, entity_type, args.top_k)
            recommendation_reports.append(report.model_dump(mode="json"))
    dump(args.output / "quality-recommendations.json", recommendation_reports)

    eligible = sum(profile.eligible_for_recommendation for profile in portfolio.profiles)
    print(
        f"已生成 {len(portfolio.profiles)} 个画像，{eligible} 个通过推荐准入；"
        f"结果目录：{args.output.resolve()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

