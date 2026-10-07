"""KR1 case alignment -> conservative entity alignment -> field comparison."""
from __future__ import annotations
import argparse
import json
import re
from collections import defaultdict
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

EXCLUDED = {"case_id", "quality_flags", "events", "evidence", "confidence", "warnings",
            "derivation", "status", "normalized_value", "reconciliation"}
ATTRIBUTES = {"diagnosis_type", "certainty", "is_historical", "category", "abnormal_flag", "encounter_type"}
COMPETITION_KEY_FIELDS = (
    "diagnoses[].name", "diagnoses[].code", "procedures[].name", "procedures[].performed_date",
    "procedures[].body_site", "medications[].name", "medications[].dose", "medications[].frequency",
    "medications[].route", "medications[].duration", "hospitalization.admission_date",
    "hospitalization.discharge_date", "hospitalization.length_of_stay_days", "costs.*",
)

def normalize_value(value: Any) -> str:
    if value is None:
        return ""
    value = re.sub(r"\s+", "", str(value).strip().lower())
    value = re.sub(r"[，,；;。]", "", value)
    aliases = {"qd": "每日1次", "1次/日": "每日1次", "bid": "每日2次", "2次/日": "每日2次",
               "tid": "每日3次", "3次/日": "每日3次"}
    if value in aliases:
        return aliases[value]
    try:
        return str(Decimal(value).normalize())
    except InvalidOperation:
        return value

def value(node):
    if isinstance(node, dict) and "value" in node:
        if node.get("value") is None or node.get("status") in {"not_applicable", "not_present_in_document"}:
            return ""
        return normalize_value(node.get("normalized_value") if node.get("normalized_value") is not None else node["value"])
    return normalize_value(node)

def fields(node, prefix=""):
    """Within one aligned entity only; nested arrays are matched separately."""
    result = {}
    if not isinstance(node, dict):
        return result
    for key, child in node.items():
        if key in EXCLUDED or key.endswith("_id"):
            continue
        path = prefix + "." + key if prefix else key
        if isinstance(child, dict) and "value" in child:
            if value(child):
                result[path] = value(child)
        elif isinstance(child, dict):
            result.update(fields(child, path))
        elif key in ATTRIBUTES and child is not None and str(child) != "unknown":
            result[path] = value(child)
    return result

def compatible(pred, gold, kind):
    name_key = {"breakdown": "category", "results": "item_name"}.get(kind, "name")
    pn, gn = value(pred.get(name_key)), value(gold.get(name_key))
    same = bool(pn and gn and pn == gn)
    if kind == "diagnoses":
        pc, gc = value(pred.get("code")), value(gold.get("code"))
        same = same or bool(pc and gc and pc == gc)
    if not same:
        return False
    if kind in {"procedures", "examinations"}:
        keys = ("performed_date", "body_site", "is_historical") if kind == "procedures" else ("performed_date", "category")
        for key in keys:
            pv, gv = value(pred.get(key)), value(gold.get(key))
            if pv and gv and pv != gv:
                return False
    return True

def align_entities(pred, gold, kind):
    # Mutual unique candidates only. Never optimize matching using dose/frequency.
    pc = {i: [j for j, g in enumerate(gold) if compatible(p, g, kind)] for i, p in enumerate(pred)}
    gc = {j: [i for i in pc if j in pc[i]] for j in range(len(gold))}
    pairs = [(i, candidates[0]) for i, candidates in pc.items()
             if len(candidates) == 1 and len(gc[candidates[0]]) == 1]
    pi, gi = {p for p, _ in pairs}, {g for _, g in pairs}
    return [(pred[p], gold[g]) for p, g in pairs] + [(p, {}) for i, p in enumerate(pred) if i not in pi] + [({}, g) for j, g in enumerate(gold) if j not in gi]

def is_key(path):
    return path in COMPETITION_KEY_FIELDS or path.startswith("costs.")

def counts():
    return {"predicted_fields": 0, "gold_fields": 0, "correct_fields": 0,
            "gold_key_fields": 0, "missing_key_fields": 0}

def metrics(c):
    p, g, t = c["predicted_fields"], c["gold_fields"], c["correct_fields"]
    return {**c, "precision": t / p if p else 0., "recall": t / g if g else 0.,
            "f1": 2*t/(p+g) if p+g else 0.,
            "missing_rate": c["missing_key_fields"] / c["gold_key_fields"] if c["gold_key_fields"] else None,
            "hallucination_rate": (p-t)/p if p else 0.}

@dataclass(frozen=True)
class EvaluationResult:
    predicted_fields: int
    gold_fields: int
    correct_fields: int
    precision: float
    recall: float
    f1: float
    micro_precision: float
    micro_recall: float
    micro_f1: float
    macro_f1: float
    missing_rate: float | None
    hallucination_rate: float
    per_field_metrics: dict
    per_case_metrics: dict
    gold_key_fields: int
    missing_key_fields: int
    competition_key_fields: tuple = COMPETITION_KEY_FIELDS
    evaluator_version: str = "kr1-entity-aligned-v2"
    evaluation_status: str = "EVALUATED"

def case_map(payload):
    cases = payload if isinstance(payload, list) else [payload]
    mapped = {}
    for case in cases:
        if not isinstance(case, dict) or not isinstance(case.get("case_id"), str) or not case["case_id"].strip():
            raise ValueError("每个评测病例必须具有非空 case_id")
        if case["case_id"] in mapped:
            raise ValueError("评测输入包含重复 case_id")
        mapped[case["case_id"]] = case
    return mapped

def evaluate(prediction: Any, gold: Any) -> EvaluationResult:
    predictions, expected = case_map(prediction), case_map(gold)
    per_field, per_case = defaultdict(counts), {}
    for case_id in sorted(predictions.keys() | expected.keys()):
        pred, truth = predictions.get(case_id, {}), expected.get(case_id, {})
        current = counts()
        def compare(p, g, prefix):
            pf, gf = fields(p, prefix), fields(g, prefix)
            for path in pf.keys() | gf.keys():
                delta = {"predicted_fields": int(path in pf), "gold_fields": int(path in gf),
                         "correct_fields": int(path in pf and path in gf and pf[path] == gf[path]),
                         "gold_key_fields": int(path in gf and is_key(path)),
                         "missing_key_fields": int(path in gf and is_key(path) and path not in pf)}
                for key, amount in delta.items():
                    current[key] += amount
                    per_field[path][key] += amount
        kinds = {"diagnoses", "procedures", "medications", "examinations"}
        compare({k: v for k, v in pred.items() if k not in kinds}, {k: v for k, v in truth.items() if k not in kinds}, "")
        for kind in sorted(kinds):
            for p, g in align_entities(pred.get(kind, []), truth.get(kind, []), kind):
                compare(p, g, kind+"[]")
                if kind == "examinations":
                    for pr, gr in align_entities(p.get("results", []), g.get("results", []), "results"):
                        compare(pr, gr, "examinations[].results[]")
        for p, g in align_entities(pred.get("costs", {}).get("breakdown", []), truth.get("costs", {}).get("breakdown", []), "breakdown"):
            compare(p, g, "costs.breakdown[]")
        per_case[case_id] = metrics(current)
    total = counts()
    for c in per_case.values():
        for key in total:
            total[key] += c[key]
    m = metrics(total)
    return EvaluationResult(**total, precision=m["precision"], recall=m["recall"], f1=m["f1"],
        micro_precision=m["precision"], micro_recall=m["recall"], micro_f1=m["f1"],
        macro_f1=sum(c["f1"] for c in per_case.values())/len(per_case) if per_case else 0.,
        missing_rate=m["missing_rate"], hallucination_rate=m["hallucination_rate"],
        per_field_metrics={p: metrics(c) for p, c in sorted(per_field.items())}, per_case_metrics=per_case,
        evaluation_status="EVALUATED" if total["gold_fields"] else "NOT_EVALUABLE")

def main():
    parser = argparse.ArgumentParser(description="KR1 病例/实体对齐字段评测")
    parser.add_argument("prediction", type=Path)
    parser.add_argument("gold", type=Path)
    args = parser.parse_args()
    result = evaluate(json.loads(args.prediction.read_text(encoding="utf-8")), json.loads(args.gold.read_text(encoding="utf-8")))
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
