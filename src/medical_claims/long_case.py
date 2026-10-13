"""长病例分块、确定性合并与案件级完整性检查。"""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Iterable, TypeVar

from pydantic import BaseModel
from .evidence_grounding import verify_case_evidence

from .models import (
    ClaimCase,
    CostItem,
    Costs,
    Diagnosis,
    EncounterType,
    EventType,
    Evidence,
    Examination,
    ExaminationResult,
    FieldStatus,
    FieldValue,
    FlagSeverity,
    FundPayments,
    Hospitalization,
    MedicalEvent,
    Medication,
    PaymentChannels,
    PersonalBurden,
    Procedure,
    QualityFlag,
    ReconciliationStatus,
)


ModelT = TypeVar("ModelT", bound=BaseModel)

_STATUS_PRIORITY = {
    FieldStatus.EXTRACTED: 6,
    FieldStatus.REVIEW_REQUIRED: 5,
    FieldStatus.CONFLICT: 4,
    FieldStatus.DERIVED: 3,
    FieldStatus.NOT_APPLICABLE: 2,
    FieldStatus.NOT_PRESENT: 1,
}


def document_text_size(document: dict[str, Any]) -> int:
    """估算送入模型的主要OCR字符量。"""

    return len(str(document.get("full_text") or ""))


def document_block_count(document: dict[str, Any]) -> int:
    """OCR块数可近似反映检验表格会产生多少结构化字段。"""

    return len(document.get("blocks") or [])


def chunk_documents(
    documents: list[dict[str, Any]],
    *,
    max_pages: int = 6,
    max_characters: int = 18_000,
    max_ocr_blocks: int = 600,
) -> list[list[dict[str, Any]]]:
    """按页数和OCR字符数切块，始终保持原始页序。"""

    if max_pages < 1 or max_characters < 1 or max_ocr_blocks < 1:
        raise ValueError("分块阈值必须为正整数")
    chunks: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_characters = 0
    current_blocks = 0
    for document in documents:
        size = document_text_size(document)
        blocks = document_block_count(document)
        would_overflow = current and (
            len(current) >= max_pages
            or current_characters + size > max_characters
            or current_blocks + blocks > max_ocr_blocks
        )
        if would_overflow:
            chunks.append(current)
            current = []
            current_characters = 0
            current_blocks = 0
        current.append(document)
        current_characters += size
        current_blocks += blocks
    if current:
        chunks.append(current)
    return chunks


def _normalized_text(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"[\s，,。；;：:（）()\[\]【】]+", "", str(value)).lower()


def _value_identity(field: FieldValue[Any]) -> str:
    value = field.normalized_value if field.normalized_value is not None else field.value
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _evidence_key(evidence: Any) -> tuple[Any, ...]:
    return (evidence.document_id, evidence.page, evidence.text, evidence.bbox)


def _merge_evidence(fields: Iterable[FieldValue[Any]], identity: str) -> list[Any]:
    result = []
    seen: set[tuple[Any, ...]] = set()
    for field in fields:
        if field.value is not None and _value_identity(field) != identity:
            continue
        for evidence in field.evidence:
            key = _evidence_key(evidence)
            if key not in seen:
                seen.add(key)
                result.append(evidence.model_copy(deep=True))
    return result[:3]


def _field_score(field: FieldValue[Any]) -> tuple[int, float, int]:
    return (
        _STATUS_PRIORITY.get(field.status, 0),
        field.confidence if field.confidence is not None else -1.0,
        len(field.evidence),
    )


def merge_field_values(
    fields: Iterable[FieldValue[Any]],
    *,
    field_path: str,
    flags: list[QualityFlag],
) -> FieldValue[Any]:
    """选取证据最强的字段；跨块出现不同非空值时强制转人工复核。"""

    items = [field for field in fields if field is not None]
    if not items:
        return FieldValue[Any]()
    valued = [field for field in items if field.value is not None]
    candidates = valued or items
    chosen = max(candidates, key=_field_score).model_copy(deep=True)
    if valued:
        chosen_identity = _value_identity(chosen)
        chosen.evidence = _merge_evidence(valued, chosen_identity)
        identities = {_value_identity(field) for field in valued}
        if field_path.endswith((".name", ".category")) and all(
            isinstance(field.value, str) for field in valued
        ):
            semantic_identities = {_normalized_text(field.value) for field in valued}
            if len(semantic_identities) == 1:
                identities = {chosen_identity}
        if len(identities) > 1:
            message = f"长病例不同分块对 {field_path} 提取出不同值，已保留高置信度候选并要求复核"
            if message not in chosen.warnings:
                chosen.warnings.append(message)
            chosen.status = FieldStatus.REVIEW_REQUIRED
            flags.append(
                QualityFlag(
                    code="LONG_CASE_FIELD_CONFLICT",
                    severity=FlagSeverity.WARNING,
                    message=message,
                    field_path=field_path,
                )
            )
    return chosen


def _compatible_text_fields(left: FieldValue[str], right: FieldValue[str]) -> bool:
    if left.value is None or right.value is None:
        return True
    return _normalized_text(left.value) == _normalized_text(right.value)


def _merge_diagnoses(partials: list[ClaimCase], flags: list[QualityFlag]) -> list[Diagnosis]:
    groups: dict[tuple[str, str, str], list[Diagnosis]] = {}
    for case in partials:
        for item in case.diagnoses:
            key = (_normalized_text(item.name.value), item.diagnosis_type.value, item.certainty.value)
            groups.setdefault(key, []).append(item)
    merged: list[Diagnosis] = []
    for index, items in enumerate(groups.values(), start=1):
        first = items[0]
        merged.append(
            Diagnosis(
                diagnosis_id=f"diag_{index}",
                name=merge_field_values(
                    [item.name for item in items], field_path="diagnoses[].name", flags=flags
                ),
                code=merge_field_values(
                    [item.code for item in items], field_path="diagnoses[].code", flags=flags
                ),
                diagnosis_type=first.diagnosis_type,
                certainty=first.certainty,
            )
        )
    return merged


def _find_compatible_procedure(groups: list[list[Procedure]], item: Procedure) -> list[Procedure] | None:
    for group in groups:
        head = group[0]
        known_dates = {entry.performed_date.value for entry in group if entry.performed_date.value is not None}
        if (
            _normalized_text(head.name.value) == _normalized_text(item.name.value)
            and _compatible_text_fields(head.body_site, item.body_site)
            and (item.performed_date.value is None or not known_dates or item.performed_date.value in known_dates)
            and head.is_historical == item.is_historical
        ):
            return group
    return None


def _merge_procedures(partials: list[ClaimCase], flags: list[QualityFlag]) -> list[Procedure]:
    groups: list[list[Procedure]] = []
    for case in partials:
        for item in case.procedures:
            group = _find_compatible_procedure(groups, item)
            if group is None:
                groups.append([item])
            else:
                group.append(item)
    result: list[Procedure] = []
    for index, items in enumerate(groups, start=1):
        result.append(
            Procedure(
                procedure_id=f"proc_{index}",
                name=merge_field_values([x.name for x in items], field_path="procedures[].name", flags=flags),
                performed_date=merge_field_values(
                    [x.performed_date for x in items], field_path="procedures[].performed_date", flags=flags
                ),
                body_site=merge_field_values(
                    [x.body_site for x in items], field_path="procedures[].body_site", flags=flags
                ),
                is_historical=all(x.is_historical for x in items),
            )
        )
    return result


def _find_compatible_medication(groups: list[list[Medication]], item: Medication) -> list[Medication] | None:
    for group in groups:
        head = group[0]
        if _normalized_text(head.name.value) != _normalized_text(item.name.value):
            continue
        if all(
            _compatible_text_fields(getattr(head, attr), getattr(item, attr))
            for attr in ("dose", "frequency", "route")
        ):
            return group
    return None


def _merge_medications(partials: list[ClaimCase], flags: list[QualityFlag]) -> list[Medication]:
    groups: list[list[Medication]] = []
    for case in partials:
        for item in case.medications:
            group = _find_compatible_medication(groups, item)
            if group is None:
                groups.append([item])
            else:
                group.append(item)
    result: list[Medication] = []
    attrs = (
        "name",
        "dose",
        "frequency",
        "route",
        "duration",
        "usage_condition",
        "max_daily_dose",
        "additional_instructions",
    )
    for index, items in enumerate(groups, start=1):
        values = {
            attr: merge_field_values(
                [getattr(item, attr) for item in items],
                field_path=f"medications[].{attr}",
                flags=flags,
            )
            for attr in attrs
        }
        result.append(Medication(medication_id=f"med_{index}", **values))
    return result


def _merge_examination_results(
    examinations: list[Examination], flags: list[QualityFlag]
) -> list[ExaminationResult]:
    groups: dict[str, list[ExaminationResult]] = {}
    for examination in examinations:
        for result in examination.results:
            groups.setdefault(_normalized_text(result.item_name.value), []).append(result)
    merged: list[ExaminationResult] = []
    for items in groups.values():
        abnormal_values = [item.abnormal_flag for item in items if item.abnormal_flag.value != "unknown"]
        abnormal = Counter(abnormal_values).most_common(1)[0][0] if abnormal_values else items[0].abnormal_flag
        merged.append(
            ExaminationResult(
                item_name=merge_field_values(
                    [x.item_name for x in items], field_path="examinations[].results[].item_name", flags=flags
                ),
                value=merge_field_values(
                    [x.value for x in items], field_path="examinations[].results[].value", flags=flags
                ),
                unit=merge_field_values(
                    [x.unit for x in items], field_path="examinations[].results[].unit", flags=flags
                ),
                reference_range=merge_field_values(
                    [x.reference_range for x in items],
                    field_path="examinations[].results[].reference_range",
                    flags=flags,
                ),
                abnormal_flag=abnormal,
            )
        )
    return merged


def _find_compatible_examination(groups: list[list[Examination]], item: Examination) -> list[Examination] | None:
    for group in groups:
        head = group[0]
        known_dates = {entry.performed_date.value for entry in group if entry.performed_date.value is not None}
        if _normalized_text(head.name.value) != _normalized_text(item.name.value):
            continue
        if item.performed_date.value is None or not known_dates or item.performed_date.value in known_dates:
            return group
    return None


def _merge_examinations(partials: list[ClaimCase], flags: list[QualityFlag]) -> list[Examination]:
    groups: list[list[Examination]] = []
    for case in partials:
        for item in case.examinations:
            group = _find_compatible_examination(groups, item)
            if group is None:
                groups.append([item])
            else:
                group.append(item)
    merged: list[Examination] = []
    for index, items in enumerate(groups, start=1):
        categories = [item.category for item in items]
        category = Counter(categories).most_common(1)[0][0]
        merged.append(
            Examination(
                examination_id=f"exam_{index}",
                category=category,
                name=merge_field_values(
                    [x.name for x in items], field_path="examinations[].name", flags=flags
                ),
                performed_date=merge_field_values(
                    [x.performed_date for x in items],
                    field_path="examinations[].performed_date",
                    flags=flags,
                ),
                results=_merge_examination_results(items, flags),
                conclusion=merge_field_values(
                    [x.conclusion for x in items], field_path="examinations[].conclusion", flags=flags
                ),
            )
        )
    return merged


def _merge_model_fields(
    model_type: type[ModelT],
    models: list[ModelT],
    *,
    prefix: str,
    flags: list[QualityFlag],
) -> ModelT:
    values = {}
    for name in model_type.model_fields:
        fields = [getattr(model, name) for model in models]
        values[name] = merge_field_values(fields, field_path=f"{prefix}.{name}", flags=flags)
    return model_type(**values)


def _merge_costs(partials: list[ClaimCase], flags: list[QualityFlag]) -> Costs:
    costs = [case.costs for case in partials]
    result = Costs(
        currency=Counter(cost.currency for cost in costs).most_common(1)[0][0],
        total_amount=merge_field_values(
            [cost.total_amount for cost in costs], field_path="costs.total_amount", flags=flags
        ),
        policy_scope_amount=merge_field_values(
            [cost.policy_scope_amount for cost in costs], field_path="costs.policy_scope_amount", flags=flags
        ),
        outside_policy_amount=merge_field_values(
            [cost.outside_policy_amount for cost in costs], field_path="costs.outside_policy_amount", flags=flags
        ),
        fund_payments=_merge_model_fields(
            FundPayments,
            [cost.fund_payments for cost in costs],
            prefix="costs.fund_payments",
            flags=flags,
        ),
        personal_burden=_merge_model_fields(
            PersonalBurden,
            [cost.personal_burden for cost in costs],
            prefix="costs.personal_burden",
            flags=flags,
        ),
        payment_channels=_merge_model_fields(
            PaymentChannels,
            [cost.payment_channels for cost in costs],
            prefix="costs.payment_channels",
            flags=flags,
        ),
    )
    result.ratios = _merge_model_fields(
        type(result.ratios),
        [cost.ratios for cost in costs],
        prefix="costs.ratios",
        flags=flags,
    )

    seen: dict[tuple[str, str], CostItem] = {}
    category_amounts: dict[str, set[Decimal]] = {}
    for cost in costs:
        for item in cost.breakdown:
            category = _normalized_text(item.category.value)
            amount = item.amount.value
            key = (category, str(amount))
            if key not in seen:
                seen[key] = item.model_copy(deep=True)
            else:
                existing = seen[key]
                existing.category = merge_field_values(
                    [existing.category, item.category],
                    field_path="costs.breakdown[].category",
                    flags=flags,
                )
                existing.amount = merge_field_values(
                    [existing.amount, item.amount],
                    field_path="costs.breakdown[].amount",
                    flags=flags,
                )
            if amount is not None:
                category_amounts.setdefault(category, set()).add(amount)
    result.breakdown = list(seen.values())
    for category, amounts in category_amounts.items():
        if category and len(amounts) > 1:
            flags.append(
                QualityFlag(
                    code="LONG_CASE_COST_SUBTOTAL_REVIEW",
                    severity=FlagSeverity.WARNING,
                    message="长病例不同页面出现同一费用类别的不同小计，已分别保留，禁止自动相加",
                    field_path="costs.breakdown",
                )
            )
            break
    result.reconciliation.status = ReconciliationStatus.NOT_CHECKED
    result.reconciliation.checked_equations = []
    result.reconciliation.difference = None
    result.reconciliation.warnings = []
    return result


def _merge_events(partials: list[ClaimCase]) -> list[MedicalEvent]:
    groups: dict[tuple[str, str, str], list[MedicalEvent]] = {}
    for case in partials:
        for event in case.events:
            key = (event.event_type.value, str(event.event_time or ""), _normalized_text(event.description))
            groups.setdefault(key, []).append(event)
    result: list[MedicalEvent] = []
    for index, items in enumerate(groups.values(), start=1):
        chosen = max(items, key=lambda item: (item.confidence, len(item.evidence))).model_copy(deep=True)
        evidence = []
        seen = set()
        for item in items:
            for entry in item.evidence:
                key = _evidence_key(entry)
                if key not in seen:
                    seen.add(key)
                    evidence.append(entry.model_copy(deep=True))
        chosen.event_id = f"event_{index}"
        chosen.evidence = evidence[:3]
        chosen.related_entity_ids = []
        result.append(chosen)
    result.sort(key=lambda item: (item.event_time is None, str(item.event_time or ""), item.event_id))
    for index, event in enumerate(result, start=1):
        event.event_id = f"event_{index}"
    return result


def _rebuild_event_links(case: ClaimCase) -> None:
    entities = [
        *((item.diagnosis_id, item.name.value) for item in case.diagnoses),
        *((item.procedure_id, item.name.value) for item in case.procedures),
        *((item.medication_id, item.name.value) for item in case.medications),
        *((item.examination_id, item.name.value) for item in case.examinations),
    ]
    for event in case.events:
        description = _normalized_text(event.description)
        event.related_entity_ids = [
            entity_id
            for entity_id, name in entities
            if name and _normalized_text(name) in description
        ]


def _event_date(field: FieldValue) -> date | datetime | None:
    value = field.normalized_value if field.normalized_value is not None else field.value
    return value if isinstance(value, (date, datetime)) else None


def _event_confidence(document: dict[str, Any]) -> float:
    values = [
        float(block["confidence"])
        for block in document.get("blocks") or []
        if isinstance(block.get("confidence"), (int, float))
    ]
    if values:
        return round(sum(values) / len(values), 4)
    return 0.95 if document.get("human_corrected") else 0.85


def _longest_common_substring(left: str, right: str) -> int:
    previous = [0] * (len(right) + 1)
    longest = 0
    for left_char in left:
        current = [0]
        for index, right_char in enumerate(right, start=1):
            value = previous[index - 1] + 1 if left_char == right_char else 0
            current.append(value)
            longest = max(longest, value)
        previous = current
    return longest


def _safe_event_links(case: ClaimCase, text: str) -> list[str]:
    """Link only entities directly named, plus one unambiguous surgical name variant."""

    normalized = _normalized_text(text)
    links = []
    entities = [
        *((item.diagnosis_id, item.name.value) for item in case.diagnoses),
        *((item.procedure_id, item.name.value) for item in case.procedures),
        *((item.medication_id, item.name.value) for item in case.medications),
        *((item.examination_id, item.name.value) for item in case.examinations),
    ]
    for entity_id, name in entities:
        candidate = _normalized_text(name or "")
        if candidate and candidate in normalized:
            links.append(entity_id)

    if any(word in normalized for word in ("手术", "术后", "切除术", "vats", "胸腔镜")):
        scored = []
        for item in case.procedures:
            name = _normalized_text(item.name.value or "")
            if not name:
                continue
            scored.append((_longest_common_substring(name, normalized), item.procedure_id))
        scored.sort(reverse=True)
        if scored and scored[0][0] >= 6 and (len(scored) == 1 or scored[0][0] > scored[1][0]):
            links.append(scored[0][1])
    return list(dict.fromkeys(links))


def _discharge_document(document: dict[str, Any]) -> bool:
    name = str(document.get("document_id") or "")
    text = str(document.get("full_text") or "")
    return "出院小结" in name or "出院记录" in name or "出院小结" in text[:200]


def _matched_snippet(text: str, pattern: str) -> str | None:
    match = re.search(pattern, re.sub(r"\s+", " ", text), flags=re.IGNORECASE)
    return match.group(0).strip(" 。；;") if match else None


def enrich_discharge_summary_events(
    case: ClaimCase, documents: list[dict[str, Any]],
) -> ClaimCase:
    """Recover explicit discharge-summary facts without inferring missing medicine details."""

    existing = [_normalized_text(item.description) for item in case.events]
    admission_date = _event_date(case.hospitalization.admission_date)
    discharge_date = _event_date(case.hospitalization.discharge_date)

    def add_event(
        event_type: EventType,
        description: str,
        snippet: str,
        document: dict[str, Any],
        event_time: date | datetime | None = None,
    ) -> None:
        normalized = _normalized_text(description)
        duplicate = any(
            normalized == item
            or (
                min(len(normalized), len(item)) >= 8
                and (normalized in item or item in normalized)
            )
            for item in existing
        )
        if not normalized or duplicate:
            return
        evidence = Evidence(
            document_id=str(document["document_id"]),
            page=int(document["page"]),
            text=snippet,
        )
        case.events.append(MedicalEvent(
            event_id=f"event_{len(case.events) + 1}",
            event_type=event_type,
            event_time=event_time,
            description=description,
            confidence=_event_confidence(document),
            evidence=[evidence],
            related_entity_ids=_safe_event_links(case, f"{description} {snippet}"),
        ))
        existing.append(normalized)

    rules = [
        (EventType.ADMISSION, r"入院时主要症状及体征\s*(.{2,100}?)(?=主要化验结果|特殊检查及重要会诊|病程与治疗结果|$)", admission_date),
        (EventType.TEST, r"(?:CT|ct)示[^\u3002；;]{2,120}", None),
        (EventType.DIAGNOSIS, r"术中冰冻[^\u3002；;]{2,160}?(?:待石蜡[^\u3002；;]*)?", None),
        (EventType.PROCEDURE, r"无明显手术禁忌(?:症)?[^\u3002；;]{0,120}?(?:手术|切除术)", None),
        (EventType.PROCEDURE, r"(?:于[^\u3002；;]{0,120}?(?:手术|切除术)[，,])?术后给予.{2,100}?(?=术后恢复|嘱出院|[。；;])", None),
        (EventType.OTHER, r"术后恢复(?:良好|佳|尚可|平稳)", None),
        (EventType.DISCHARGE, r"治疗结果\s*(?:好转|治愈|稳定|未愈|转院|死亡)", discharge_date),
        (EventType.DISCHARGE, r"术后约?[^。；;]{0,60}?门诊复诊", discharge_date),
        (EventType.DISCHARGE, r"复查时[^。；;]{0,160}?正式病理报告[^。；;]{0,160}?(?:随访计划|治疗方案|用药治疗)", discharge_date),
        (EventType.DISCHARGE, r"出院后每[^。；;]{0,120}?伤口换药[^。；;]{0,120}?伤口拆线[^。；;]*", discharge_date),
        (EventType.DISCHARGE, r"止咳化痰等出院带药", discharge_date),
        (EventType.DISCHARGE, r"饮食无禁忌[^。；;]{0,40}", discharge_date),
        (EventType.DISCHARGE, r"加强营养[^。；;]{0,100}?(?:避免感冒|[。；;])", discharge_date),
        (EventType.DISCHARGE, r"如有[^。；;]{0,100}?(?:及时)?急诊就诊", discharge_date),
        (EventType.DISCHARGE, r"出院去向[:：]\s*[^\s。；;]+", discharge_date),
    ]
    for document in documents:
        if not _discharge_document(document):
            continue
        text = str(document.get("full_text") or "")
        for event_type, pattern, event_time in rules:
            snippet = _matched_snippet(text, pattern)
            if snippet:
                description = re.sub(r"\s+", "", snippet)
                add_event(event_type, description, snippet, document, event_time)

        pending_pathology = _matched_snippet(
            text, r"出院诊断\s*.{1,100}?具体见石蜡报告",
        )
        if pending_pathology:
            add_event(
                EventType.DIAGNOSIS,
                "出院诊断需结合正式石蜡病理报告确认",
                pending_pathology,
                document,
                discharge_date,
            )

    for document in documents:
        text = str(document.get("full_text") or "")
        if "病理" not in str(document.get("document_id") or "") and "病理检查报告" not in text[:200]:
            continue
        histology = _matched_snippet(text, r"组织学类型[:：]\s*[^\n。]{2,160}[。]?")
        if not histology:
            continue
        report_date = _matched_snippet(text, r"报告日期[:：]\s*\d{4}[-/.年]\d{1,2}[-/.月]\d{1,2}日?")
        parsed_date = None
        if report_date:
            digits = [int(value) for value in re.findall(r"\d+", report_date)]
            if len(digits) >= 3:
                try:
                    parsed_date = date(digits[0], digits[1], digits[2])
                except ValueError:
                    parsed_date = None
        add_event(
            EventType.DIAGNOSIS,
            "正式病理报告：" + re.sub(r"^组织学类型[:：]\s*", "", histology).rstrip("。"),
            histology,
            document,
            parsed_date,
        )

    case.events.sort(key=lambda item: (
        item.event_time is None,
        item.event_time.isoformat() if item.event_time else "",
        item.event_id,
    ))
    for index, event in enumerate(case.events, start=1):
        event.event_id = f"event_{index}"
    return case


def _dedupe_flags(flags: Iterable[QualityFlag]) -> list[QualityFlag]:
    result = []
    seen = set()
    for flag in flags:
        key = (flag.code, flag.field_path, flag.document_id, flag.message)
        if key not in seen:
            seen.add(key)
            result.append(flag.model_copy(deep=True))
    return result


def _remove_resolved_partial_flags(case: ClaimCase) -> None:
    """分块缺页产生的告警不能冒充全案告警；全案校验结果优先。"""

    always_recomputed = {
        "COST_RECONCILIATION_WARNING",
        "DOCUMENT_ID_MISMATCH",
    }
    retained = []
    for flag in case.quality_flags:
        if flag.code in always_recomputed:
            continue
        if flag.code in {"MISSING_DIAGNOSIS", "DIAGNOSIS_MISSING"} and case.diagnoses:
            continue
        if flag.code == "MISSING_COST_INFO" and any(
            field.value is not None
            for field in (
                case.costs.total_amount,
                case.costs.policy_scope_amount,
                case.costs.outside_policy_amount,
                case.costs.fund_payments.total_fund_payment,
                case.costs.personal_burden.total_personal_burden,
            )
        ):
            continue
        retained.append(flag)
    case.quality_flags = retained


def validate_long_case_integrity(
    case: ClaimCase, documents: list[dict[str, Any]]
) -> list[QualityFlag]:
    """校验证据页码、文件名、实体ID以及OCR空页。"""

    flags: list[QualityFlag] = []
    page_by_document = {str(document["document_id"]): int(document["page"]) for document in documents}
    for document in documents:
        if not str(document.get("full_text") or "").strip():
            flags.append(
                QualityFlag(
                    code="OCR_EMPTY_PAGE",
                    severity=FlagSeverity.WARNING,
                    message=f"第{document['page']}页没有识别出文字",
                    document_id=str(document["document_id"]),
                )
            )
    flags.extend(verify_case_evidence(case, documents))
    entity_ids = [
        *(item.diagnosis_id for item in case.diagnoses),
        *(item.procedure_id for item in case.procedures),
        *(item.medication_id for item in case.medications),
        *(item.examination_id for item in case.examinations),
    ]
    if len(entity_ids) != len(set(entity_ids)):
        flags.append(
            QualityFlag(
                code="DUPLICATE_ENTITY_ID",
                severity=FlagSeverity.ERROR,
                message="合并结果存在重复实体ID",
                field_path=None,
            )
        )
    known_ids = set(entity_ids)
    for index, event in enumerate(case.events):
        if any(entity_id not in known_ids for entity_id in event.related_entity_ids):
            flags.append(
                QualityFlag(
                    code="BROKEN_EVENT_REFERENCE",
                    severity=FlagSeverity.ERROR,
                    message="医疗事件引用了不存在的实体ID",
                    field_path=f"events[{index}].related_entity_ids",
                )
            )
    return _dedupe_flags(flags)


def merge_claim_cases(
    case_id: str,
    partials: list[ClaimCase],
    documents: list[dict[str, Any]],
    *,
    chunk_count: int,
) -> ClaimCase:
    """将分块结果确定性合并为单一案件，禁止让模型自由覆盖已抽取证据。"""

    if not partials:
        raise ValueError("至少需要一个分块结果")
    flags: list[QualityFlag] = []
    encounter_values = [case.encounter_type for case in partials if case.encounter_type != EncounterType.UNKNOWN]
    if encounter_values:
        encounter_type = Counter(encounter_values).most_common(1)[0][0]
        if len(set(encounter_values)) > 1:
            flags.append(
                QualityFlag(
                    code="LONG_CASE_ENCOUNTER_CONFLICT",
                    severity=FlagSeverity.WARNING,
                    message="不同分块识别出的就诊类型不一致，已保留多数结果并要求复核",
                    field_path="encounter_type",
                )
            )
    else:
        encounter_type = EncounterType.UNKNOWN

    hospitalization = Hospitalization(
        admission_date=merge_field_values(
            [case.hospitalization.admission_date for case in partials],
            field_path="hospitalization.admission_date",
            flags=flags,
        ),
        discharge_date=merge_field_values(
            [case.hospitalization.discharge_date for case in partials],
            field_path="hospitalization.discharge_date",
            flags=flags,
        ),
        length_of_stay_days=merge_field_values(
            [case.hospitalization.length_of_stay_days for case in partials],
            field_path="hospitalization.length_of_stay_days",
            flags=flags,
        ),
        department=merge_field_values(
            [case.hospitalization.department for case in partials],
            field_path="hospitalization.department",
            flags=flags,
        ),
    )
    result = ClaimCase(
        case_id=case_id,
        encounter_type=encounter_type,
        hospitalization=hospitalization,
        diagnoses=_merge_diagnoses(partials, flags),
        procedures=_merge_procedures(partials, flags),
        medications=_merge_medications(partials, flags),
        examinations=_merge_examinations(partials, flags),
        costs=_merge_costs(partials, flags),
        events=_merge_events(partials),
        quality_flags=_dedupe_flags(
            [flag for case in partials for flag in case.quality_flags]
            + flags
            + [
                QualityFlag(
                    code="LONG_CASE_CHUNKED",
                    severity=FlagSeverity.INFO,
                    message=f"案件共{len(documents)}页，已分为{chunk_count}个结构化分块后自动合并",
                    field_path=None,
                )
            ]
        ),
    )
    _rebuild_event_links(result)
    _remove_resolved_partial_flags(result)
    result.quality_flags = _dedupe_flags(
        result.quality_flags + validate_long_case_integrity(result, documents)
    )
    return ClaimCase.model_validate(result.model_dump())
