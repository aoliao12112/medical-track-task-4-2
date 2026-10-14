"""Read-only planning for revalidating persisted OCR evidence metadata."""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, Iterable

from .evidence_grounding import iter_fields, normalize_evidence_text, verify_case_evidence
from .models import ClaimCase, FieldStatus


EVIDENCE_WARNING_PREFIX = "Evidence verification failed"
LOW_CONFIDENCE_THRESHOLD = 0.90
MANUAL_DECISIONS = {"CONFIRMED", "CORRECTED", "UNRESOLVED"}
MEDICATION_BINDING_FIELDS = {
    "dose", "frequency", "route", "duration", "usage_condition",
    "additional_instructions",
}
FEE_DOCUMENT_MARKERS = ("费用清单", "结算清单", "收费项目", "收费清单")
MEDICATION_FIELD_PATH = re.compile(
    r"^medications\[(?P<index>\d+)\]\.(?P<attribute>[^.]+)$"
)


def _is_evidence_flag(flag: dict[str, Any]) -> bool:
    return str(flag.get("code") or "").startswith("EVIDENCE_")


def _is_evidence_warning(warning: Any) -> bool:
    return str(warning or "").startswith(EVIDENCE_WARNING_PREFIX)


def _fingerprint(structured: dict[str, Any]) -> str:
    payload = json.dumps(structured, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _flags_by_path(flags: Iterable[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for flag in flags:
        grouped[str(flag.get("field_path") or "<case>")].append(flag)
    return grouped


def _field_type(path: str) -> str:
    return path.split(".", 1)[0].split("[", 1)[0]


def _manual_decision(review: dict[str, Any] | None) -> str | None:
    decision = str((review or {}).get("decision") or "")
    return decision if decision in MANUAL_DECISIONS else None


def _medication_restoration_blocker(
    path: str, field: Any, case: ClaimCase,
) -> str | None:
    """Return why a medication attribute cannot be safely auto-restored.

    Historical revalidation is intentionally stricter than ordinary evidence
    validation: it may only release a field when its saved excerpt explicitly
    binds one unique medication name to the value.
    """

    match = MEDICATION_FIELD_PATH.match(path)
    if not match or match.group("attribute") not in MEDICATION_BINDING_FIELDS:
        return None
    medication = case.medications[int(match.group("index"))]
    owner = normalize_evidence_text(medication.name.value or "")
    value = normalize_evidence_text(field.value or "")
    if not owner or not value:
        return "药名或字段值为空，不能证明唯一药品绑定"

    same_name_count = sum(
        normalize_evidence_text(item.name.value or "") == owner
        for item in case.medications
    )
    if same_name_count > 1:
        return "存在重复同名 medication，无法唯一对应 medication_id"

    evidence = list(field.evidence or [])
    if match.group("attribute") == "dose":
        non_fee_evidence = [
            item for item in evidence
            if not any(marker in str(item.document_id) for marker in FEE_DOCUMENT_MARKERS)
        ]
        if not non_fee_evidence:
            return "费用/结算/收费清单只能证明商品规格，不能证明实际给药剂量"
        evidence = non_fee_evidence

    if not any(
        owner in normalize_evidence_text(item.text)
        and value in normalize_evidence_text(item.text)
        for item in evidence
    ):
        return "原 evidence 未同时包含当前药名和字段值，不能用于自动恢复"
    return None


def revalidate_case(
    structured: dict[str, Any],
    documents: list[dict[str, Any]],
    manual_reviews: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a revalidation proposal without mutating persisted input data."""

    manual_reviews = manual_reviews or {}
    original = ClaimCase.model_validate(structured)
    recalculated = deepcopy(original)
    new_flags = [item.model_dump(mode="json") for item in verify_case_evidence(
        recalculated, documents,
    )]
    old_flags = [item for item in (structured.get("quality_flags") or []) if _is_evidence_flag(item)]
    old_evidence_by_path = _flags_by_path(old_flags)
    new_evidence_by_path = _flags_by_path(new_flags)
    non_evidence_by_path = _flags_by_path(
        item for item in (structured.get("quality_flags") or []) if not _is_evidence_flag(item)
    )
    fields = {path: field for path, field in iter_fields(original)}

    relevant_paths = set(old_evidence_by_path) | set(new_evidence_by_path)
    relevant_paths.update(
        path for path, field in fields.items()
        if any(_is_evidence_warning(item) for item in field.warnings)
    )
    proposals = []
    for path in sorted(relevant_paths):
        field = fields.get(path)
        old_codes = sorted({item["code"] for item in old_evidence_by_path.get(path, [])})
        new_codes = sorted({item["code"] for item in new_evidence_by_path.get(path, [])})
        old_warnings = list(field.warnings) if field is not None else []
        evidence_warnings = [item for item in old_warnings if _is_evidence_warning(item)]
        other_warnings = [item for item in old_warnings if not _is_evidence_warning(item)]
        manual_decision = _manual_decision(manual_reviews.get(path))
        old_status = field.status.value if field is not None else None
        proposed_status = old_status
        warnings_removed: list[str] = []
        warnings_retained = list(other_warnings)

        if manual_decision:
            action = "PROTECT_MANUAL_OVERLAY"
            reason = f"当前结构化版本已有人工 {manual_decision} 决策，dry-run 不覆盖"
            warnings_retained.extend(evidence_warnings)
        elif new_codes:
            action = "KEEP_EVIDENCE_REVIEW"
            reason = "当前 verifier 仍检出 evidence 问题"
            warnings_retained.extend(evidence_warnings)
            if field is not None and old_status == FieldStatus.EXTRACTED.value:
                proposed_status = FieldStatus.REVIEW_REQUIRED.value
        else:
            warnings_removed = evidence_warnings
            if field is None:
                action = "REMOVE_STALE_EVIDENCE_METADATA"
                reason = "旧 evidence flag 已消失；该路径不是可恢复状态的 FieldValue"
            elif old_status == FieldStatus.CONFLICT.value:
                action = "KEEP_NON_EVIDENCE_REVIEW"
                reason = "字段为 conflict，不能由 evidence 重验证放行"
            elif field.confidence is None or field.confidence < LOW_CONFIDENCE_THRESHOLD:
                action = "KEEP_NON_EVIDENCE_REVIEW"
                reason = "字段仍受低置信度或缺少置信度约束"
            elif other_warnings or non_evidence_by_path.get(path):
                action = "KEEP_NON_EVIDENCE_REVIEW"
                reason = "字段仍有非 evidence warning 或质量标记"
            elif blocker := _medication_restoration_blocker(path, field, original):
                action = "KEEP_NON_EVIDENCE_REVIEW"
                reason = blocker
                warnings_removed = []
                warnings_retained.extend(evidence_warnings)
            elif old_status == FieldStatus.REVIEW_REQUIRED.value and (old_codes or evidence_warnings):
                proposed_status = FieldStatus.EXTRACTED.value
                action = "PROPOSE_RESTORE_EXTRACTED"
                reason = "旧 evidence 原因已消失，且没有人工或其他待复核原因"
            else:
                action = "REMOVE_STALE_EVIDENCE_METADATA"
                reason = "旧 evidence 元数据已失效，字段状态无需改变"

        proposals.append({
            "field_path": path,
            "field_type": _field_type(path),
            "old_status": old_status,
            "new_proposed_status": proposed_status,
            "old_evidence_codes": old_codes,
            "new_evidence_codes": new_codes,
            "warnings_removed": warnings_removed,
            "warnings_retained": list(dict.fromkeys(warnings_retained)),
            "manual_overlay": manual_decision,
            "proposed_action": action,
            "reason": reason,
        })

    return {
        "old_evidence_flags": old_flags,
        "new_evidence_flags": new_flags,
        "proposals": proposals,
    }


def _latest_manual_reviews(
    db: sqlite3.Connection, case_id: str, structured: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    rows = db.execute(
        "SELECT record_json FROM reviews WHERE case_id=? AND content_fingerprint=? "
        "AND target_type LIKE 'structured_field:%' ORDER BY rowid DESC",
        (case_id, _fingerprint(structured)),
    ).fetchall()
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        record = json.loads(row[0])
        latest.setdefault(record["field_path"], record)
    return latest


def build_revalidation_report(
    database: str | Path,
    *,
    case_ids: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Read the database in query-only mode and return an audit-only report."""

    database = Path(database).resolve()
    selected = set(case_ids or [])
    uri = f"file:{database.as_posix()}?mode=ro"
    db = sqlite3.connect(uri, uri=True)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only = ON")
    try:
        rows = db.execute(
            "SELECT case_id, ocr_result_json, structured_result_json FROM case_results "
            "WHERE structured_result_json IS NOT NULL ORDER BY case_id"
        ).fetchall()
        if selected:
            rows = [row for row in rows if row["case_id"] in selected]

        case_reports = []
        old_codes: Counter[str] = Counter()
        new_codes: Counter[str] = Counter()
        action_counts: Counter[str] = Counter()
        field_types: Counter[str] = Counter()
        protected_decisions: Counter[str] = Counter()
        active_manual_decisions: Counter[str] = Counter()
        old_field_types: Counter[str] = Counter()
        new_field_types: Counter[str] = Counter()
        proposed_removals = 0
        proposed_additions = 0
        removed_warnings = 0
        retained_warnings = 0
        skipped_cases = []
        for row in rows:
            if not row["ocr_result_json"]:
                skipped_cases.append({"case_id": row["case_id"], "reason": "OCR 结果不存在"})
                continue
            structured = json.loads(row["structured_result_json"])
            documents = json.loads(row["ocr_result_json"])
            manual_reviews = _latest_manual_reviews(db, row["case_id"], structured)
            active_manual_decisions.update(
                decision for review in manual_reviews.values()
                if (decision := _manual_decision(review))
            )
            result = revalidate_case(structured, documents, manual_reviews)
            old_codes.update(item["code"] for item in result["old_evidence_flags"])
            new_codes.update(item["code"] for item in result["new_evidence_flags"])
            old_field_types.update(
                _field_type(str(item.get("field_path") or "<case>"))
                for item in result["old_evidence_flags"]
            )
            new_field_types.update(
                _field_type(str(item.get("field_path") or "<case>"))
                for item in result["new_evidence_flags"]
            )
            old_identities = Counter(
                (item["code"], item.get("field_path"), item.get("document_id"))
                for item in result["old_evidence_flags"]
            )
            new_identities = Counter(
                (item["code"], item.get("field_path"), item.get("document_id"))
                for item in result["new_evidence_flags"]
            )
            proposed_removals += sum((old_identities - new_identities).values())
            proposed_additions += sum((new_identities - old_identities).values())
            action_counts.update(item["proposed_action"] for item in result["proposals"])
            field_types.update(
                item["field_type"] for item in result["proposals"]
                if item["proposed_action"] == "PROPOSE_RESTORE_EXTRACTED"
            )
            protected_decisions.update(
                item["manual_overlay"] for item in result["proposals"]
                if item["manual_overlay"]
            )
            removed_warnings += sum(len(item["warnings_removed"]) for item in result["proposals"])
            retained_warnings += sum(len(item["warnings_retained"]) for item in result["proposals"])
            case_reports.append({
                "case_id": row["case_id"],
                "old_evidence_flag_count": len(result["old_evidence_flags"]),
                "new_evidence_flag_count": len(result["new_evidence_flags"]),
                "proposed_status_restorations": sum(
                    item["proposed_action"] == "PROPOSE_RESTORE_EXTRACTED"
                    for item in result["proposals"]
                ),
                "protected_manual_overlays": sum(bool(item["manual_overlay"])
                                                   for item in result["proposals"]),
                "active_manual_overlays": len(manual_reviews),
                "fields": result["proposals"],
            })
    finally:
        db.close()

    return {
        "mode": "DRY_RUN_READ_ONLY",
        "database": str(database),
        "summary": {
            "case_count": len(case_reports),
            "skipped_case_count": len(skipped_cases),
            "old_evidence_flag_count": sum(old_codes.values()),
            "new_evidence_flag_count": sum(new_codes.values()),
            "proposed_evidence_flag_removals": proposed_removals,
            "new_evidence_flags_detected": proposed_additions,
            "proposed_status_restorations": action_counts["PROPOSE_RESTORE_EXTRACTED"],
            "still_requires_review": action_counts["KEEP_EVIDENCE_REVIEW"]
            + action_counts["KEEP_NON_EVIDENCE_REVIEW"],
            "protected_manual_overlays": sum(protected_decisions.values()),
            "active_manual_overlays": sum(active_manual_decisions.values()),
            "warnings_proposed_removed": removed_warnings,
            "warnings_retained": retained_warnings,
            "old_flags_by_code": dict(sorted(old_codes.items())),
            "new_flags_by_code": dict(sorted(new_codes.items())),
            "old_flags_by_field_type": dict(sorted(old_field_types.items())),
            "new_flags_by_field_type": dict(sorted(new_field_types.items())),
            "proposals_by_action": dict(sorted(action_counts.items())),
            "restorations_by_field_type": dict(sorted(field_types.items())),
            "protected_manual_decisions": dict(sorted(protected_decisions.items())),
            "active_manual_decisions": dict(sorted(active_manual_decisions.items())),
        },
        "skipped_cases": skipped_cases,
        "cases": case_reports,
    }
