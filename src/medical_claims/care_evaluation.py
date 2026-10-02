"""第三问：温度感、可行动性及事实安全的人工盲评汇总。"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from pydantic import Field

from .models import StrictModel


class CareRating(StrictModel):
    case_id: str
    reviewer_id: str
    warmth: int = Field(ge=1, le=5)
    actionability: int = Field(ge=1, le=5)
    personalization: int = Field(ge=1, le=5)
    clarity: int = Field(ge=1, le=5)
    factual_errors: int = Field(default=0, ge=0)
    unsafe_suggestions: int = Field(default=0, ge=0)
    comments: str = ""


@dataclass(frozen=True)
class CareEvaluationResult:
    ratings: int
    cases: int
    reviewers: int
    average_warmth: float
    average_actionability: float
    average_personalization: float
    average_clarity: float
    factual_errors: int
    unsafe_suggestions: int
    target: float
    target_passed: bool


def evaluate_care_ratings(payload: object, target: float = 4.0) -> CareEvaluationResult:
    if not isinstance(payload, list) or not payload:
        raise ValueError("盲评文件必须是非空评分数组")
    ratings = [CareRating.model_validate(item) for item in payload]
    count = len(ratings)
    average = lambda name: round(sum(getattr(item, name) for item in ratings) / count, 4)
    factual_errors = sum(item.factual_errors for item in ratings)
    unsafe_suggestions = sum(item.unsafe_suggestions for item in ratings)
    warmth = average("warmth")
    actionability = average("actionability")
    return CareEvaluationResult(
        ratings=count,
        cases=len({item.case_id for item in ratings}),
        reviewers=len({item.reviewer_id for item in ratings}),
        average_warmth=warmth,
        average_actionability=actionability,
        average_personalization=average("personalization"),
        average_clarity=average("clarity"),
        factual_errors=factual_errors,
        unsafe_suggestions=unsafe_suggestions,
        target=target,
        target_passed=warmth >= target and actionability >= target and factual_errors == 0 and unsafe_suggestions == 0,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="汇总第三问人工盲评分数")
    parser.add_argument("ratings", type=Path)
    parser.add_argument("--target", type=float, default=4.0)
    args = parser.parse_args()
    payload = json.loads(args.ratings.read_text(encoding="utf-8"))
    print(json.dumps(asdict(evaluate_care_ratings(payload, args.target)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

