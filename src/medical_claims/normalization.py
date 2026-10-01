"""对模型输出执行可复现的医学字段规范化，不引入外部医学事实。"""

from __future__ import annotations

import re

from .models import ClaimCase, Examination, ExaminationCategory, FieldStatus, FieldValue


FREQUENCY_NORMALIZATION = {
    "qd": "每日1次",
    "q.d.": "每日1次",
    "bid": "每日2次",
    "b.i.d.": "每日2次",
    "tid": "每日3次",
    "t.i.d.": "每日3次",
    "qid": "每日4次",
    "q.i.d.": "每日4次",
}


def _normalized_frequency(value: str) -> str | None:
    key = re.sub(r"\s+", "", value).lower()
    if key in FREQUENCY_NORMALIZATION:
        return FREQUENCY_NORMALIZATION[key]
    as_needed = re.fullmatch(r"必要时(?:每)?(\d+(?:-\d+)?)小时可重复(?:用药)?", key)
    if as_needed:
        return f"必要时每{as_needed.group(1)}小时可重复"
    return None


def _normalize_medications(case: ClaimCase) -> None:
    for medication in case.medications:
        if medication.frequency.value:
            normalized = _normalized_frequency(medication.frequency.value)
            if normalized:
                medication.frequency.normalized_value = normalized

        if medication.usage_condition.value:
            condition = re.sub(r"\s+", "", medication.usage_condition.value)
            fever = re.fullmatch(r"(?:发热)?体温([≥>]\s*\d+(?:\.\d+)?℃)时", condition)
            if fever:
                medication.usage_condition.normalized_value = f"体温{fever.group(1).replace(' ', '')}时使用"

        source_fields = [
            medication.name,
            medication.dose,
            medication.frequency,
            medication.route,
            medication.duration,
            medication.usage_condition,
        ]

        if (
            medication.usage_condition.value is None
            and medication.name.value
            and "氯化钠注射液" in medication.name.value
        ):
            infusion_evidence = next(
                (
                    item
                    for field in source_fields
                    for item in field.evidence
                    if "+" in item.text and "注射液" in item.text
                ),
                None,
            )
            if infusion_evidence:
                paired = re.search(r"\+\s*([^，,；;]+?注射液)", infusion_evidence.text)
                paired_name = paired.group(1).strip() if paired else "其他注射药物"
                medication.usage_condition = FieldValue[str](
                    value=f"与{paired_name}配伍",
                    normalized_value="作为静脉输注溶媒",
                    confidence=0.95,
                    status=FieldStatus.EXTRACTED,
                    evidence=[infusion_evidence],
                )

        instruction_evidence = next(
            (
                item
                for field in source_fields
                for item in field.evidence
                if re.search(r"必要时.*?\d+\s*-\s*\d+\s*小时.*?重复", item.text)
            ),
            None,
        )
        if medication.additional_instructions.value is None and instruction_evidence:
            repeat = re.search(r"必要时\s*(\d+)\s*-\s*(\d+)\s*小时.*?重复(?:用药)?", instruction_evidence.text)
            limit = re.search(r"(?:24\s*(?:小时|小)|每日|一天).*?不超过\s*([0-9]+(?:\.[0-9]+)?\s*(?:mg|g|ml|mL))", instruction_evidence.text, flags=re.IGNORECASE)
            if repeat:
                repeat_text = f"必要时{repeat.group(1)}-{repeat.group(2)}小时可重复用药"
                normalized_repeat = f"必要时每{repeat.group(1)}-{repeat.group(2)}小时可重复"
                if limit:
                    amount = re.sub(r"\s+", "", limit.group(1))
                    repeat_text += f"，24小时内不超过{amount}"
                    normalized_repeat += f"；24小时内不超过{amount}"
                medication.additional_instructions = FieldValue[str](
                    value=repeat_text,
                    normalized_value=normalized_repeat,
                    confidence=0.90,
                    status=FieldStatus.EXTRACTED,
                    evidence=[instruction_evidence],
                )

        if medication.max_daily_dose.value is not None:
            continue
        evidence = next(
            (
                item
                for field in source_fields
                for item in field.evidence
                if re.search(r"(?:24\s*(?:小时|小)|每日|一天).*?不超过\s*\d", item.text)
            ),
            None,
        )
        if evidence is None:
            continue

        match = re.search(
            r"(?:24\s*(?:小时|小)|每日|一天).*?不超过\s*([0-9]+(?:\.[0-9]+)?\s*(?:mg|g|ml|mL))",
            evidence.text,
            flags=re.IGNORECASE,
        )
        if not match:
            continue

        amount = re.sub(r"\s+", "", match.group(1))
        uncertain_interval = bool(re.search(r"24\s*小内", evidence.text))
        medication.max_daily_dose = FieldValue[str](
            value=amount,
            normalized_value=f"{amount}/24h",
            confidence=0.85 if uncertain_interval else 0.95,
            status=FieldStatus.REVIEW_REQUIRED if uncertain_interval else FieldStatus.EXTRACTED,
            evidence=[evidence],
            warnings=["OCR疑似漏识别“时”字，请核对24小时剂量上限"] if uncertain_interval else [],
        )


def _normalize_examinations(case: ClaimCase) -> None:
    for examination in case.examinations:
        for result in examination.results:
            raw_value = result.value.value
            raw_unit = result.unit.value
            if raw_value and raw_unit and raw_value.endswith(raw_unit):
                numeric_part = raw_value[: -len(raw_unit)].strip()
                if numeric_part:
                    result.value.value = numeric_part
                    result.value.normalized_value = numeric_part

            if raw_unit and re.search(r"[＊*?？]", raw_unit):
                result.unit.status = FieldStatus.REVIEW_REQUIRED
                warning = "检验单位包含疑似OCR缺失或乱码，必须人工复核"
                if warning not in result.unit.warnings:
                    result.unit.warnings.append(warning)


def _reclassify_examination_only_procedures(case: ClaimCase) -> None:
    """纠正模型把纯检查项目误放入手术操作的情况，保留CT引导穿刺等真实操作。"""

    examination_pattern = re.compile(
        r"(?:PET\s*/?\s*CT|MRI|MR|CT|磁共振|超声|B超|X线|胸片|心电图|显像|影像|病理检查)",
        flags=re.IGNORECASE,
    )
    procedure_action_pattern = re.compile(
        r"(?:手术|切除|穿刺|植入|活检|置管|治疗|注射|采血|探查|缝合|引流|消融|吻合)"
    )
    retained = []
    for procedure in case.procedures:
        name = procedure.name.value or ""
        if examination_pattern.search(name) and not procedure_action_pattern.search(name):
            category = (
                ExaminationCategory.PATHOLOGY
                if "病理" in name
                else ExaminationCategory.FUNCTIONAL
                if "心电" in name
                else ExaminationCategory.IMAGING
            )
            name_field = procedure.name.model_copy(deep=True)
            warning = "该项目属于辅助检查，已从手术/操作自动归入 examinations"
            if warning not in name_field.warnings:
                name_field.warnings.append(warning)
            case.examinations.append(
                Examination(
                    examination_id=f"exam_reclassified_{len(case.examinations) + 1}",
                    category=category,
                    name=name_field,
                    performed_date=procedure.performed_date.model_copy(deep=True),
                )
            )
        else:
            retained.append(procedure)
    case.procedures = retained


def normalize_case(case: ClaimCase) -> ClaimCase:
    """就地规范化后重新校验，确保输出仍符合 ClaimCase Schema。"""

    _normalize_medications(case)
    _reclassify_examination_only_procedures(case)
    _normalize_examinations(case)
    return ClaimCase.model_validate(case.model_dump())
