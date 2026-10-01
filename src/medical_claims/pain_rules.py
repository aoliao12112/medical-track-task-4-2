"""第二问的确定性痛点规则。

规则只使用第一问已经结构化且带证据的字段。它们输出风险提示，不输出
"医生错误"、"不合理诊疗"等责任性结论。
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from decimal import Decimal, InvalidOperation
from typing import Iterable

from .models import (
    AbnormalFlag,
    Evidence,
    ExaminationCategory,
    FieldStatus,
    FieldValue,
    ReconciliationStatus,
)
from .pain_models import (
    DetectorInfo,
    DetectorMethod,
    PainDimension,
    PainPoint,
    PainPointType,
    PainSeverity,
)


RULE_VERSION = "pain-rules-v3"
MIN_RULE_CONFIDENCE = 0.72

PATIENT_INSTRUCTION_MARKERS = {
    "出院",
    "带药",
    "医嘱",
    "处方",
    "门诊病历",
    "用法用量",
    # 常见 UTF-8/GBK 文件名乱码片段，兼容历史样本。
    "鍑洪櫌",
    "闂ㄨ瘖",
    "澶勬柟",
}
NON_MEDICATION_MARKERS = {"敷料", "材料", "导管", "缝线", "引流器", "固定器"}


def _stable_id(point_type: PainPointType, identity: str) -> str:
    digest = hashlib.sha1(identity.encode("utf-8")).hexdigest()[:8]
    return f"PP-{point_type.value}-{digest}"


def _evidence(*fields: FieldValue[object]) -> list[Evidence]:
    result: list[Evidence] = []
    seen: set[tuple[str, int | None, str]] = set()
    for field in fields:
        for item in field.evidence:
            key = (item.document_id, item.page, item.text)
            if item.text and key not in seen:
                seen.add(key)
                result.append(item)
    return result[:4]


def _confidence(*fields: FieldValue[object], reliability: float = 0.95) -> float:
    source = [field.confidence for field in fields if field.confidence is not None]
    upstream = min(source) if source else 0.8
    return round(min(1.0, upstream * reliability), 4)


def _field_text(*fields: FieldValue[object]) -> str:
    parts: list[str] = []
    for field in fields:
        if field.value is not None:
            parts.append(str(field.value))
        for item in field.evidence:
            parts.extend([item.document_id, item.text])
    return " ".join(parts)


def _medication_fields(medication) -> list[FieldValue[object]]:
    return [
        medication.name,
        medication.dose,
        medication.frequency,
        medication.route,
        medication.duration,
        medication.usage_condition,
        medication.max_daily_dose,
        medication.additional_instructions,
    ]


def _is_patient_instruction_medication(medication) -> bool:
    """只对处方、出院带药或明确医嘱检查用户可执行的用药说明。

    费用清单中的药品项目不是医嘱，天然不会包含完整频次和疗程；把它们逐条
    判为说明缺失会制造大量误报。
    """

    name = str(medication.name.normalized_value or medication.name.value or "")
    if any(marker in name for marker in NON_MEDICATION_MARKERS):
        return False
    context = _field_text(*_medication_fields(medication))
    return any(marker in context for marker in PATIENT_INSTRUCTION_MARKERS)


def _decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _ratio(
    ratio_field: FieldValue[float],
    numerator_field: FieldValue[object],
    total_field: FieldValue[object],
) -> tuple[Decimal | None, list[FieldValue[object]]]:
    explicit = _decimal(ratio_field.value)
    if explicit is not None:
        # 兼容 0.25 与 25 两种常见百分比存储方式。
        sources: list[FieldValue[object]] = [ratio_field]
        if not ratio_field.evidence:
            sources.extend([numerator_field, total_field])
        return (explicit / 100 if explicit > 1 else explicit), sources
    numerator = _decimal(numerator_field.value)
    total = _decimal(total_field.value)
    if numerator is None or total is None or total <= 0:
        return None, []
    return numerator / total, [numerator_field, total_field]


def _severity_for_ratio(
    ratio: Decimal,
    medium_at: Decimal,
    high_at: Decimal,
) -> tuple[PainSeverity, int]:
    if ratio >= high_at:
        return PainSeverity.HIGH, 85
    if ratio >= medium_at:
        return PainSeverity.MEDIUM, 62
    return PainSeverity.LOW, 38


def _cost_ratio_points(case) -> Iterable[PainPoint]:
    costs = case.costs
    outside_ratio, outside_fields = _ratio(
        costs.ratios.outside_policy_self_expense_ratio,
        costs.personal_burden.outside_policy_self_expense,
        costs.total_amount,
    )
    if outside_ratio is not None and outside_ratio >= Decimal("0.10"):
        evidence = _evidence(*outside_fields)
        if evidence:
            severity, score = _severity_for_ratio(
                outside_ratio, Decimal("0.20"), Decimal("0.40")
            )
            percent = float(outside_ratio * 100)
            yield PainPoint(
                pain_point_id=_stable_id(
                    PainPointType.HIGH_OUTSIDE_POLICY_RATIO, case.case_id
                ),
                dimension=PainDimension.COST,
                type=PainPointType.HIGH_OUTSIDE_POLICY_RATIO,
                title="目录外个人自费比例偏高",
                severity=severity,
                severity_score=score,
                confidence=_confidence(*outside_fields),
                description=(
                    f"根据当前材料计算，目录外个人自费约占总费用的{percent:.1f}%。"
                    "该阈值用于原型筛查，后续应使用官方数据分位数校准。"
                ),
                evidence=evidence,
                related_fields=[
                    "costs.personal_burden.outside_policy_self_expense",
                    "costs.total_amount",
                ],
                recommended_action="核对目录外项目明细，并在下次就医前询问可替代的医保目录内方案。",
                review_required=True,
                detector=DetectorInfo(
                    method=DetectorMethod.RULE,
                    version=RULE_VERSION,
                    rule_id="COST-OUTSIDE-RATIO-001",
                ),
            )

    burden_field = costs.personal_burden.total_personal_burden
    burden_sources: list[FieldValue[object]] = []
    burden = _decimal(burden_field.value)
    within_value = _decimal(costs.personal_burden.within_policy_self_pay.value)
    outside_value = _decimal(costs.personal_burden.outside_policy_self_expense.value)
    invalid_derived_burden = (
        burden_field.status == FieldStatus.DERIVED
        and (within_value is None or outside_value is None)
    )
    burden_evidence_text = " ".join(item.text for item in burden_field.evidence)
    channel_only_burden = (
        burden_field.status == FieldStatus.EXTRACTED
        and any(
            marker in burden_evidence_text
            for marker in ("现金支付", "个人账户支付", "家庭账户支付", "共济账户支付")
        )
        and not any(
            marker in burden_evidence_text
            for marker in ("个人负担", "个人自付", "个人自费", "个人承担")
        )
    )
    if burden is not None and not invalid_derived_burden and not channel_only_burden:
        burden_sources = [burden_field]
    else:
        burden = None
        if within_value is not None and outside_value is not None:
            burden = within_value + outside_value
            burden_sources = [
                costs.personal_burden.within_policy_self_pay,
                costs.personal_burden.outside_policy_self_expense,
            ]
    total = _decimal(costs.total_amount.value)
    burden_ratio = burden / total if burden is not None and total and total > 0 else None
    burden_sources.append(costs.total_amount)
    if burden_ratio is not None and burden_ratio >= Decimal("0.20"):
        evidence = _evidence(*burden_sources)
        if evidence:
            severity, score = _severity_for_ratio(
                burden_ratio, Decimal("0.35"), Decimal("0.50")
            )
            percent = float(burden_ratio * 100)
            yield PainPoint(
                pain_point_id=_stable_id(
                    PainPointType.HIGH_PERSONAL_BURDEN_RATIO, case.case_id
                ),
                dimension=PainDimension.COST,
                type=PainPointType.HIGH_PERSONAL_BURDEN_RATIO,
                title="个人医疗费用负担比例偏高",
                severity=severity,
                severity_score=score,
                confidence=_confidence(*burden_sources),
                description=f"个人自付与个人自费合计约占总费用的{percent:.1f}%。",
                evidence=evidence,
                related_fields=[
                    "costs.personal_burden",
                    "costs.total_amount",
                ],
                recommended_action="核对医保结算口径，并了解复诊、用药和检查项目的报销范围。",
                review_required=True,
                detector=DetectorInfo(
                    method=DetectorMethod.RULE,
                    version=RULE_VERSION,
                    rule_id="COST-BURDEN-RATIO-001",
                ),
            )


def _reconciliation_point(case) -> PainPoint | None:
    reconciliation = case.costs.reconciliation
    difference = _decimal(reconciliation.difference)
    abnormal = reconciliation.status in {
        ReconciliationStatus.WARNING,
        ReconciliationStatus.FAILED,
    }
    if not abnormal:
        return None
    cost_fields: list[FieldValue[object]] = [
        case.costs.total_amount,
        case.costs.fund_payments.total_fund_payment,
        case.costs.personal_burden.within_policy_self_pay,
        case.costs.personal_burden.outside_policy_self_expense,
    ]
    evidence = _evidence(*cost_fields)
    if not evidence:
        return None
    absolute = abs(difference) if difference is not None else None
    severity = PainSeverity.HIGH if absolute is not None and absolute > 100 else PainSeverity.MEDIUM
    score = 78 if severity == PainSeverity.HIGH else 58
    difference_text = f"，当前差额为{absolute}元" if absolute is not None else ""
    return PainPoint(
        pain_point_id=_stable_id(
            PainPointType.COST_RECONCILIATION_ANOMALY, case.case_id
        ),
        dimension=PainDimension.COST,
        type=PainPointType.COST_RECONCILIATION_ANOMALY,
        title="费用构成存在勾稽差异",
        severity=severity,
        severity_score=score,
        confidence=_confidence(*cost_fields, reliability=0.98),
        description=f"总费用与基金支付、个人自付及个人自费暂未完全对应{difference_text}。",
        evidence=evidence,
        related_fields=["costs.reconciliation"],
        recommended_action="核对结算单各支付项目和费用口径；在确认前不要据此判断收费错误。",
        review_required=True,
        detector=DetectorInfo(
            method=DetectorMethod.RULE,
            version=RULE_VERSION,
            rule_id="COST-RECONCILIATION-001",
        ),
    )


def _missing_medication_instructions(case) -> Iterable[PainPoint]:
    fields = ("dose", "frequency", "route", "duration")
    labels = {"dose": "剂量", "frequency": "频次", "route": "途径", "duration": "疗程"}
    for index, medication in enumerate(case.medications):
        if not _is_patient_instruction_medication(medication):
            continue
        missing = [name for name in fields if getattr(medication, name).value is None]
        # 按需使用且已给出使用条件/日剂量上限的药品，不强行要求固定疗程。
        if (
            "duration" in missing
            and medication.usage_condition.value is not None
            and medication.max_daily_dose.value is not None
        ):
            missing.remove("duration")
        if not missing or not medication.name.evidence:
            continue
        drug_name = medication.name.normalized_value or medication.name.value or "该药品"
        score = 62 if len(missing) >= 2 else 38
        severity = PainSeverity.MEDIUM if len(missing) >= 2 else PainSeverity.LOW
        yield PainPoint(
            pain_point_id=_stable_id(
                PainPointType.MEDICATION_INSTRUCTION_INCOMPLETE,
                f"{case.case_id}:{medication.medication_id}",
            ),
            dimension=PainDimension.EXPERIENCE,
            type=PainPointType.MEDICATION_INSTRUCTION_INCOMPLETE,
            title=f"{drug_name}的用药说明不完整",
            severity=severity,
            severity_score=score,
            confidence=_confidence(medication.name, reliability=0.98),
            description="当前材料中未发现明确的" + "、".join(labels[item] for item in missing) + "。",
            evidence=_evidence(*_medication_fields(medication)),
            related_fields=[f"medications[{index}].{item}" for item in missing],
            recommended_action="下次用药或复诊前向医生或药师确认，不能自行补全剂量和疗程。",
            review_required=True,
            detector=DetectorInfo(
                method=DetectorMethod.RULE,
                version=RULE_VERSION,
                rule_id="EXP-MED-INSTRUCTION-001",
            ),
        )


def _normalized_medication_name(value: str) -> str:
    value = value.lower().replace(" ", "")
    value = re.sub(r"\d+(?:\.\d+)?\s*(?:mg|g|ml|mL|片|粒|支|袋)", "", value, flags=re.I)
    return re.sub(r"[（）()，,、]", "", value)


def _duplicate_medications(case) -> Iterable[PainPoint]:
    groups: dict[str, list[tuple[int, object]]] = {}
    for index, medication in enumerate(case.medications):
        name = medication.name.normalized_value or medication.name.value
        if not name or not _is_patient_instruction_medication(medication):
            continue
        groups.setdefault(_normalized_medication_name(name), []).append((index, medication))
    for normalized, entries in groups.items():
        if not normalized or len(entries) < 2:
            continue
        fields = [medication.name for _, medication in entries]
        evidence = _evidence(*fields)
        if not evidence:
            continue
        display = fields[0].normalized_value or fields[0].value or "同名药品"
        yield PainPoint(
            pain_point_id=_stable_id(
                PainPointType.DUPLICATE_MEDICATION_REVIEW,
                f"{case.case_id}:{normalized}",
            ),
            dimension=PainDimension.CLINICAL,
            type=PainPointType.DUPLICATE_MEDICATION_REVIEW,
            title=f"发现重复记录的药品：{display}",
            severity=PainSeverity.MEDIUM,
            severity_score=55,
            confidence=_confidence(*fields, reliability=0.92),
            description="同一药品在当前材料中出现多次，可能是分次医嘱、重复记录或重复用药，需要结合时间和医嘱目的复核。",
            evidence=evidence,
            related_fields=[f"medications[{index}]" for index, _ in entries],
            recommended_action="由医疗人员核对用药时间、剂量和目的，不要自行停药。",
            review_required=True,
            detector=DetectorInfo(
                method=DetectorMethod.RULE,
                version=RULE_VERSION,
                rule_id="CLINICAL-DUPLICATE-MED-001",
            ),
        )


LAB_GROUPS = (
    ("血常规", {"白细胞", "红细胞", "血红蛋白", "血小板", "中性粒", "淋巴细胞", "单核细胞", "嗜酸", "嗜碱"}),
    ("肝功能", {"丙氨酸", "天门冬氨酸", "转氨酶", "胆红素", "白蛋白", "球蛋白", "碱性磷酸酶", "谷氨酰"}),
    ("肾功能", {"肌酐", "尿素", "尿酸", "肾小球滤过", "egfr"}),
    ("电解质", {"钠", "钾", "氯", "钙", "镁", "磷", "碳酸氢"}),
    ("凝血功能", {"凝血", "凝血酶", "纤维蛋白", "d-二聚体", "inr", "aptt", "pt"}),
    ("肿瘤标志物", {"糖类抗原", "ca19-9", "癌胚抗原", "cea", "甲胎蛋白", "afp", "细胞角蛋白", "scc"}),
    ("感染相关检查", {"c反应蛋白", "降钙素原", "病原抗原", "培养", "病毒", "细菌", "炎症"}),
    ("血气与酸碱", {"二氧化碳分压", "二氧化碳总量", "碱剩余", "剩余碱", "超碱", "血气"}),
    ("尿液检查", {"尿常规", "管型", "尿蛋白", "尿潜血", "尿白细胞", "尿红细胞"}),
    ("心肌相关", {"肌钙蛋白", "肌酸激酶", "乳酸脱氢酶", "bnp", "心肌"}),
)


def _examination_group(examination, result) -> str:
    if examination.category == ExaminationCategory.IMAGING:
        return "影像检查"
    if examination.category == ExaminationCategory.PATHOLOGY:
        return "病理检查"
    item = str(result.item_name.normalized_value or result.item_name.value or "").lower()
    examination_name = str(examination.name.normalized_value or examination.name.value or "").lower()
    text = f"{examination_name} {item}"
    for group, markers in LAB_GROUPS:
        if any(marker in text for marker in markers):
            return group
    return "其他检验"


def _first_number(value: object) -> Decimal | None:
    if value is None:
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", str(value).replace(",", ""))
    return _decimal(match.group(0)) if match else None


def _numeric_reference_range(value: object) -> tuple[Decimal, Decimal] | None:
    if value is None:
        return None
    match = re.fullmatch(
        r"\s*(-?\d+(?:\.\d+)?)\s*(?:-|~|—|–|至)\s*(-?\d+(?:\.\d+)?)\s*",
        str(value).replace(",", ""),
    )
    if not match:
        return None
    lower = _decimal(match.group(1))
    upper = _decimal(match.group(2))
    if lower is None or upper is None or lower > upper:
        return None
    return lower, upper


def _is_verified_abnormal(examination, result) -> bool:
    """复核模型的异常标签，避免把正常数值或预测值表误报为临床痛点。"""

    if examination.category == ExaminationCategory.IMAGING:
        return True
    value = _first_number(result.value.normalized_value or result.value.value)
    reference = _numeric_reference_range(
        result.reference_range.normalized_value or result.reference_range.value
    )
    if value is not None and reference is not None:
        lower, upper = reference
        if result.abnormal_flag == AbnormalFlag.HIGH:
            return value > upper
        if result.abnormal_flag == AbnormalFlag.LOW:
            return value < lower
        return value < lower or value > upper

    source_text = " ".join(
        item.text
        for field in (result.item_name, result.value, result.reference_range)
        for item in field.evidence
    )
    if result.abnormal_flag == AbnormalFlag.POSITIVE:
        return bool(re.search(r"阳性|\+", source_text, flags=re.IGNORECASE))
    # 没有可计算参考范围时，只接受原文明确写出的异常或升降标记。
    return bool(re.search(r"异常|↑|↓|(?:^|\s)[HL](?:\s|$)", source_text, flags=re.IGNORECASE))


def _abnormal_examinations(case) -> Iterable[PainPoint]:
    """将逐项异常归并为临床主题，避免几十个检验值淹没真正行动建议。"""

    relevant = {
        AbnormalFlag.HIGH,
        AbnormalFlag.LOW,
        AbnormalFlag.POSITIVE,
        AbnormalFlag.ABNORMAL,
    }
    groups: dict[str, list[tuple[int, int, object]]] = {}
    for examination_index, examination in enumerate(case.examinations):
        for result_index, result in enumerate(examination.results):
            if result.abnormal_flag not in relevant:
                continue
            item_name = str(result.item_name.normalized_value or result.item_name.value or "")
            if (
                examination.category == ExaminationCategory.PATHOLOGY
                and item_name.strip() in {"大体描述", "肉眼所见", "镜下描述", "病理所见"}
            ):
                # 这些是报告章节标题，不是“异常”本身；应依据病理诊断/结论判断。
                continue
            if not _is_verified_abnormal(examination, result):
                continue
            confidence = _confidence(result.item_name, result.value, reliability=0.97)
            if confidence < MIN_RULE_CONFIDENCE:
                continue
            if not _evidence(result.item_name, result.value):
                continue
            groups.setdefault(_examination_group(examination, result), []).append(
                (examination_index, result_index, result)
            )

    for group, entries in groups.items():
        ranked = sorted(
            entries,
            key=lambda entry: (
                entry[2].abnormal_flag
                not in {AbnormalFlag.POSITIVE, AbnormalFlag.ABNORMAL},
                -_confidence(entry[2].item_name, entry[2].value, reliability=0.97),
            ),
        )
        selected = ranked[:6]
        evidence: list[Evidence] = []
        seen_evidence: set[tuple[str, int | None, str]] = set()
        item_names: list[str] = []
        confidences: list[float] = []
        for _, _, result in selected:
            name = str(result.item_name.normalized_value or result.item_name.value or "异常指标")
            if name not in item_names:
                item_names.append(name)
            confidences.append(_confidence(result.item_name, result.value, reliability=0.97))
            for item in _evidence(result.item_name, result.value):
                key = (item.document_id, item.page, item.text)
                if key not in seen_evidence and len(evidence) < 4:
                    seen_evidence.add(key)
                    evidence.append(item)
        high_attention = any(
            result.abnormal_flag in {AbnormalFlag.POSITIVE, AbnormalFlag.ABNORMAL}
            for _, _, result in entries
        )
        extra = len(entries) - len(item_names)
        item_text = "、".join(item_names[:5])
        if extra > 0:
            item_text += f"等共{len(entries)}项"
        title = {
            "影像检查": "影像检查发现需要结合临床随访",
            "病理检查": "病理检查结果需要医学复核",
        }.get(group, f"{group}存在异常指标")
        yield PainPoint(
            pain_point_id=_stable_id(
                PainPointType.ABNORMAL_EXAMINATION_FOLLOWUP,
                f"{case.case_id}:{group}",
            ),
            dimension=PainDimension.CLINICAL,
            type=PainPointType.ABNORMAL_EXAMINATION_FOLLOWUP,
            title=title,
            severity=PainSeverity.MEDIUM if high_attention else PainSeverity.LOW,
            severity_score=65 if high_attention else 42,
            confidence=round(sum(confidences) / len(confidences), 4),
            description=(
                f"当前材料在{group}中标记了{item_text}。系统已将同类结果归并；"
                "仅凭理赔材料不能确定临床意义。"
            ),
            evidence=evidence,
            related_fields=[
                f"examinations[{examination_index}].results[{result_index}]"
                for examination_index, result_index, _ in entries[:12]
            ],
            recommended_action="请结合症状和经治医生意见，确认是否需要复查、随访或进一步解释。",
            review_required=True,
            detector=DetectorInfo(
                method=DetectorMethod.RULE,
                version=RULE_VERSION,
                rule_id=f"CLINICAL-ABNORMAL-{group}-V2",
            ),
        )


JARGON_MARKERS = {
    "动脉粥样硬化",
    "椎间盘突出",
    "纤维化",
    "浸润",
    "占位",
    "结节",
    "增生",
    "静脉曲张",
    "代偿",
    "梗死",
    "病灶",
    "综合征",
    "功能不全",
}


def _collapse_near_duplicate_terms(values: list[str]) -> list[str]:
    """合并只差一个字符的同长术语，优先保留在病历中重复更多的写法。

    这可避免单页 OCR 错字（如一个解剖部位字误识别）进入用户可见说明，同时
    不会把“肺结节”和“肺下叶结节”这类长度不同、含义不同的术语强行合并。
    """

    counts = Counter(values)
    order = {value: index for index, value in enumerate(values)}
    ranked = sorted(counts, key=lambda value: (-counts[value], order[value]))
    retained: list[str] = []
    for value in ranked:
        if any(
            len(value) == len(existing)
            and sum(left != right for left, right in zip(value, existing)) <= 1
            for existing in retained
        ):
            continue
        retained.append(value)
    return sorted(retained, key=lambda value: order[value])


def _jargon_points(case) -> Iterable[PainPoint]:
    groups: dict[str, list[tuple[int, object, str, list[str]]]] = {}
    for index, diagnosis in enumerate(case.diagnoses):
        name = diagnosis.name.normalized_value or diagnosis.name.value
        if not name or not diagnosis.name.evidence:
            continue
        matched = sorted(marker for marker in JARGON_MARKERS if marker in name)
        if not matched and len(name) < 12:
            continue
        # 相同概念即使来自多张检查单，也只形成一个用户痛点。
        group = matched[0] if matched else "复杂术语"
        groups.setdefault(group, []).append((index, diagnosis, str(name), matched))

    for group, entries in groups.items():
        fields = [entry[1].name for entry in entries]
        evidence = _evidence(*fields)
        if not evidence:
            continue
        names = _collapse_near_duplicate_terms([entry[2] for entry in entries])
        markers = sorted({marker for entry in entries for marker in entry[3]})
        reason = "、".join(markers) if markers else "较长的专业诊断术语"
        examples = "、".join(f"“{name}”" for name in names[:3])
        if len(names) > 3:
            examples += f"等{len(names)}种表述"
        yield PainPoint(
            pain_point_id=_stable_id(
                PainPointType.DIAGNOSIS_TERM_HARD_TO_UNDERSTAND,
                f"{case.case_id}:{group}",
            ),
            dimension=PainDimension.EXPERIENCE,
            type=PainPointType.DIAGNOSIS_TERM_HARD_TO_UNDERSTAND,
            title=f"{group}相关诊断术语可能不易理解",
            severity=PainSeverity.LOW,
            severity_score=30,
            confidence=_confidence(*fields, reliability=0.85),
            description=f"当前材料出现{examples}，包含{reason}，普通用户可能需要通俗解释。",
            evidence=evidence,
            related_fields=[f"diagnoses[{index}].name" for index, *_ in entries[:12]],
            recommended_action="在温度指引中提供不夸大、不替代医生诊断的通俗解释。",
            review_required=False,
            detector=DetectorInfo(
                method=DetectorMethod.RULE,
                version=RULE_VERSION,
                rule_id="EXP-JARGON-001",
            ),
        )


def detect_rule_pain_points(case) -> list[PainPoint]:
    """执行所有确定性规则并返回稳定顺序的痛点列表。"""

    points: list[PainPoint] = []
    points.extend(_cost_ratio_points(case))
    reconciliation = _reconciliation_point(case)
    if reconciliation:
        points.append(reconciliation)
    points.extend(_missing_medication_instructions(case))
    points.extend(_duplicate_medications(case))
    points.extend(_abnormal_examinations(case))
    points.extend(_jargon_points(case))
    return sorted(points, key=lambda point: (point.dimension.value, point.type.value, point.pain_point_id))
