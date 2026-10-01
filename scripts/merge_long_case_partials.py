"""合并通过旧后端逐块产生的真实病例结果，用于长病例联调。"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from medical_claims.long_case import merge_claim_cases
from medical_claims.models import ClaimCase, FieldValue
from medical_claims.normalization import normalize_case
from medical_claims.validation import validate_case


def repair_evidence_pages(value: Any, page_by_document: dict[str, int]) -> None:
    if isinstance(value, FieldValue):
        for evidence in value.evidence:
            if evidence.document_id in page_by_document:
                evidence.page = page_by_document[evidence.document_id]
        return
    if isinstance(value, BaseModel):
        if hasattr(value, "evidence") and not isinstance(value, FieldValue):
            for evidence in getattr(value, "evidence", []):
                if evidence.document_id in page_by_document:
                    evidence.page = page_by_document[evidence.document_id]
        for name in value.__class__.model_fields:
            repair_evidence_pages(getattr(value, name), page_by_document)
    elif isinstance(value, list):
        for item in value:
            repair_evidence_pages(item, page_by_document)


def main() -> None:
    parser = argparse.ArgumentParser(description="合并长病例真实分块输出")
    parser.add_argument("case_id")
    parser.add_argument("plan_json", type=Path)
    parser.add_argument("partial_directory", type=Path)
    parser.add_argument("output_json", type=Path)
    args = parser.parse_args()

    plan = __import__("json").loads(args.plan_json.read_text(encoding="utf-8"))
    documents = plan["documents"]
    page_by_document = {
        str(document["document_id"]): int(document["page"]) for document in documents
    }
    partial_paths = sorted(args.partial_directory.glob("partial-*.json"))
    if not partial_paths:
        raise SystemExit("没有找到分块JSON")
    partials = [
        ClaimCase.model_validate_json(path.read_text(encoding="utf-8-sig"))
        for path in partial_paths
    ]
    for partial in partials:
        repair_evidence_pages(partial, page_by_document)

    result = merge_claim_cases(
        args.case_id,
        partials,
        documents,
        chunk_count=len(partials),
    )
    result = normalize_case(result)
    existing = {
        (flag.code, flag.field_path, flag.document_id, flag.message)
        for flag in result.quality_flags
    }
    result.quality_flags.extend(
        flag
        for flag in validate_case(result)
        if (flag.code, flag.field_path, flag.document_id, flag.message) not in existing
    )
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    print(
        f"DONE partials={len(partials)} diagnoses={len(result.diagnoses)} "
        f"procedures={len(result.procedures)} medications={len(result.medications)} "
        f"examinations={len(result.examinations)} flags={len(result.quality_flags)}"
    )


if __name__ == "__main__":
    main()
