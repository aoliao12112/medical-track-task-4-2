"""字段级微平均 Precision / Recall / F1 评测。"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


IGNORED_OBJECT_KEYS = {"evidence", "warnings", "derivation", "confidence", "status"}


def normalize_value(value: Any) -> str:
    if value is None:
        return ""
    normalized = re.sub(r"\s+", "", str(value).strip().lower())
    normalized = re.sub(r"[，,；;。]", "", normalized)
    aliases = {
        "qd": "每日1次",
        "1次/日": "每日1次",
        "bid": "每日2次",
        "2次/日": "每日2次",
        "tid": "每日3次",
        "3次/日": "每日3次",
    }
    return aliases.get(normalized, normalized)


def comparable_fields(payload: Any) -> Counter[str]:
    counter: Counter[str] = Counter()

    def visit(node: Any, path: str) -> None:
        if isinstance(node, dict):
            if "status" in node and "value" in node:
                if node.get("status") != "not_applicable" and node.get("value") is not None:
                    value = node.get("normalized_value", node["value"])
                    if value is None:
                        value = node["value"]
                    counter[f"{path}:{normalize_value(value)}"] += 1
                return
            for key, value in node.items():
                if key not in IGNORED_OBJECT_KEYS and key not in {"case_id", "quality_flags", "events"}:
                    visit(value, f"{path}.{key}" if path else key)
        elif isinstance(node, list):
            for value in node:
                visit(value, f"{path}[]")

    cases = payload if isinstance(payload, list) else [payload]
    for case in cases:
        visit(case, "")
    return counter


@dataclass(frozen=True)
class EvaluationResult:
    predicted_fields: int
    gold_fields: int
    correct_fields: int
    precision: float
    recall: float
    f1: float
    missing_rate: float


def evaluate(prediction: Any, gold: Any) -> EvaluationResult:
    predicted = comparable_fields(prediction)
    expected = comparable_fields(gold)
    correct = sum((predicted & expected).values())
    predicted_count = sum(predicted.values())
    gold_count = sum(expected.values())
    precision = correct / predicted_count if predicted_count else 0.0
    recall = correct / gold_count if gold_count else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return EvaluationResult(
        predicted_fields=predicted_count,
        gold_fields=gold_count,
        correct_fields=correct,
        precision=precision,
        recall=recall,
        f1=f1,
        missing_rate=(gold_count - correct) / gold_count if gold_count else 0.0,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="评测 ClaimCase 字段级微平均 F1")
    parser.add_argument("prediction", type=Path)
    parser.add_argument("gold", type=Path)
    args = parser.parse_args()
    prediction = json.loads(args.prediction.read_text(encoding="utf-8"))
    gold = json.loads(args.gold.read_text(encoding="utf-8"))
    print(json.dumps(asdict(evaluate(prediction, gold)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
