"""第二问：痛点标签与专家金标准的一致率评测。"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .pain_models import PainPointReport


@dataclass(frozen=True)
class PainEvaluationResult:
    predicted_labels: int
    gold_labels: int
    matched_labels: int
    false_positive: int
    false_negative: int
    precision: float
    recall: float
    f1: float
    expert_agreement: float
    severity_exact_agreement: float
    target: float
    target_passed: bool


def _reports(payload: Any) -> list[PainPointReport]:
    items = payload if isinstance(payload, list) else [payload]
    return [PainPointReport.model_validate(item) for item in items]


def _label_counter(reports: list[PainPointReport]) -> Counter[str]:
    return Counter(
        f"{report.case_id}:{point.type.value}"
        for report in reports
        for point in report.pain_points
    )


def _severity_counter(reports: list[PainPointReport]) -> Counter[str]:
    return Counter(
        f"{report.case_id}:{point.type.value}:{point.severity.value}"
        for report in reports
        for point in report.pain_points
    )


def evaluate_pain_points(
    prediction: Any,
    gold: Any,
    target: float = 0.85,
) -> PainEvaluationResult:
    predicted_reports = _reports(prediction)
    gold_reports = _reports(gold)
    predicted = _label_counter(predicted_reports)
    expected = _label_counter(gold_reports)
    matched = sum((predicted & expected).values())
    predicted_count = sum(predicted.values())
    gold_count = sum(expected.values())
    false_positive = predicted_count - matched
    false_negative = gold_count - matched
    precision = matched / predicted_count if predicted_count else (1.0 if not gold_count else 0.0)
    recall = matched / gold_count if gold_count else (1.0 if not predicted_count else 0.0)
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    union = matched + false_positive + false_negative
    expert_agreement = matched / union if union else 1.0
    severity_matches = sum(
        (_severity_counter(predicted_reports) & _severity_counter(gold_reports)).values()
    )
    severity_agreement = severity_matches / matched if matched else (1.0 if not union else 0.0)
    return PainEvaluationResult(
        predicted_labels=predicted_count,
        gold_labels=gold_count,
        matched_labels=matched,
        false_positive=false_positive,
        false_negative=false_negative,
        precision=precision,
        recall=recall,
        f1=f1,
        expert_agreement=expert_agreement,
        severity_exact_agreement=severity_agreement,
        target=target,
        target_passed=expert_agreement >= target,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="评测痛点标签与专家标注的一致率")
    parser.add_argument("prediction", type=Path)
    parser.add_argument("gold", type=Path)
    parser.add_argument("--target", type=float, default=0.85)
    args = parser.parse_args()
    prediction = json.loads(args.prediction.read_text(encoding="utf-8"))
    gold = json.loads(args.gold.read_text(encoding="utf-8"))
    result = evaluate_pain_points(prediction, gold, args.target)
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

