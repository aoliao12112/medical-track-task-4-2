"""从 OCR 文本中保守提取医院、带角色医生和目标病种。

本模块不读取患者身份，不调用外部模型，也不会把“报告医师”等角色误当成
经治医生。候选原文仅用于本地审计，公开结果必须经过 ``build_safe_context``。
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import hashlib
import hmac
import re
from typing import Iterable

from .provider_models import (
    CaseProviderContext,
    ClinicianReference,
    ClinicianRole,
    InstitutionReference,
    ProviderSource,
)
from .providers import OcrBlock, OcrDocument


@dataclass(frozen=True)
class RawCandidate:
    value: str
    document_id: str
    page: int
    confidence: float
    role: ClinicianRole | None = None


_INSTITUTION_PATTERN = re.compile(
    r"[\u4e00-\u9fffA-Za-z0-9·（）()\-]{2,48}?"
    r"(?:妇幼保健院|中医医院|中心医院|人民医院|卫生院|中医院|医院|医疗中心|门诊部)"
)
_INSTITUTION_PREFIXES = (
    "医院名称",
    "医疗机构名称",
    "定点医疗机构",
    "机构名称",
    "就诊医院",
    "hospitalName",
    "收款单位（章）",
    "收款单位(章)",
)
_GENERIC_INSTITUTIONS = {
    "医院",
    "本院",
    "人民医院",
    "中心医院",
    "送检医院",
    "定点医院",
}

_ROLE_LABELS: tuple[tuple[str, ClinicianRole], ...] = (
    ("主治医师", ClinicianRole.ATTENDING_PHYSICIAN),
    ("主管医师", ClinicianRole.TREATING_PHYSICIAN),
    ("经治医师", ClinicianRole.TREATING_PHYSICIAN),
    ("住院医师", ClinicianRole.RESIDENT_PHYSICIAN),
    ("主刀医师", ClinicianRole.SURGEON),
    ("手术医师", ClinicianRole.SURGEON),
    ("术者", ClinicianRole.SURGEON),
    ("申请医师", ClinicianRole.ORDERING_PHYSICIAN),
    ("送检医师", ClinicianRole.ORDERING_PHYSICIAN),
    ("报告医师", ClinicianRole.REPORTING_PHYSICIAN),
    ("诊断医师", ClinicianRole.DIAGNOSTIC_PHYSICIAN),
    ("检验者", ClinicianRole.LABORATORY_OPERATOR),
    ("审核医师", ClinicianRole.REVIEWER),
    ("复核医师", ClinicianRole.REVIEWER),
    ("审核者", ClinicianRole.REVIEWER),
    ("医生签名", ClinicianRole.SIGNATURE_UNKNOWN),
    ("医师签名", ClinicianRole.SIGNATURE_UNKNOWN),
)
_RANKING_ELIGIBLE = {
    ClinicianRole.ATTENDING_PHYSICIAN,
    ClinicianRole.TREATING_PHYSICIAN,
    ClinicianRole.SURGEON,
}
_INVALID_PERSON_NAMES = {
    "签名",
    "医师",
    "医生",
    "审核",
    "报告",
    "申请",
    "检验",
    "未签名",
    "打印",
}

_DISEASE_PATTERNS: dict[str, re.Pattern[str]] = {
    "pneumonia": re.compile(r"(?:社区获得性|支气管|大叶性|间质性|病毒性|细菌性)?肺炎"),
    "fracture": re.compile(r"骨折"),
    "diabetes": re.compile(r"(?:1型|2型|Ⅰ型|Ⅱ型|妊娠期)?糖尿病"),
    "hypertension": re.compile(r"(?:原发性|继发性|妊娠期)?高血压"),
    "coronary_heart_disease": re.compile(r"冠心病|冠状动脉粥样硬化性心脏病"),
    "cerebral_infarction": re.compile(r"脑梗死|脑梗塞"),
    "lung_nodule": re.compile(r"肺(?:部)?结节"),
    "influenza": re.compile(r"甲型流感|乙型流感|流行性感冒|流感"),
    "urinary_calculus": re.compile(r"肾结石|输尿管结石|尿路结石"),
    "appendicitis": re.compile(r"阑尾炎"),
    "cholecystitis": re.compile(r"胆囊炎"),
    "copd": re.compile(r"慢性阻塞性肺疾病|慢阻肺"),
    "asthma": re.compile(r"支气管哮喘|哮喘"),
    "lumbar_disc_herniation": re.compile(r"腰椎间盘突出"),
    "cataract": re.compile(r"白内障"),
}
_NEGATION_PATTERN = re.compile(r"(?:未见|无|否认|排除|除外|不支持|待排)[^，。；;]{0,8}$")


def _compact(text: str) -> str:
    return re.sub(r"[\s:：,，。;；|]+", "", text or "")


def normalize_institution_name(value: str) -> str:
    normalized = _compact(value).strip("-—_()（）")
    normalized = re.sub(r"^[0-9]{4}-?[0-9]{1,2}-?[0-9]{1,2}[0-9:：-]*", "", normalized)
    for prefix in _INSTITUTION_PREFIXES:
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix) :]
    return normalized


def extract_institution_candidates(documents: Iterable[OcrDocument]) -> list[RawCandidate]:
    candidates: list[RawCandidate] = []
    for document in documents:
        for block in document.blocks:
            compact = _compact(block.text)
            for match in _INSTITUTION_PATTERN.finditer(compact):
                value = normalize_institution_name(match.group(0))
                if value in _GENERIC_INSTITUTIONS or len(value) < 4:
                    continue
                candidates.append(
                    RawCandidate(
                        value=value,
                        document_id=document.document_id,
                        page=block.page,
                        confidence=block.confidence if block.confidence is not None else 0.70,
                    )
                )
    return candidates


def _valid_person_name(value: str) -> bool:
    if value in _INVALID_PERSON_NAMES:
        return False
    if not re.fullmatch(r"[\u4e00-\u9fff·]{2,4}", value):
        return False
    return not any(word in value for word in ("医师", "医生", "医院", "科室"))


def extract_clinician_candidates(documents: Iterable[OcrDocument]) -> list[RawCandidate]:
    candidates: list[RawCandidate] = []
    seen: set[tuple[str, ClinicianRole, str, int]] = set()
    for document in documents:
        for block in document.blocks:
            compact = _compact(block.text)
            for label, role in _ROLE_LABELS:
                patterns = (
                    re.compile(re.escape(label) + r"(?:签字|签名)?([\u4e00-\u9fff·]{2,4})"),
                    re.compile(r"([\u4e00-\u9fff·]{2,4})" + re.escape(label)),
                )
                for pattern in patterns:
                    match = pattern.search(compact)
                    if not match:
                        continue
                    name = match.group(1)
                    if not _valid_person_name(name):
                        continue
                    key = (name, role, document.document_id, block.page)
                    if key in seen:
                        continue
                    seen.add(key)
                    candidates.append(
                        RawCandidate(
                            value=name,
                            document_id=document.document_id,
                            page=block.page,
                            confidence=block.confidence if block.confidence is not None else 0.65,
                            role=role,
                        )
                    )
    return candidates


def detect_disease_groups(documents: Iterable[OcrDocument]) -> list[str]:
    groups: set[str] = set()
    for document in documents:
        for block in document.blocks:
            text = _compact(block.text)
            for group, pattern in _DISEASE_PATTERNS.items():
                for match in pattern.finditer(text):
                    prefix = text[max(0, match.start() - 12) : match.start()]
                    if _NEGATION_PATTERN.search(prefix):
                        continue
                    groups.add(group)
                    break
    return sorted(groups)


def pseudonymous_id(prefix: str, value: str, secret: bytes) -> str:
    digest = hmac.new(secret, value.encode("utf-8"), hashlib.sha256).hexdigest()[:12].upper()
    return f"{prefix}-{digest}"


def _sources(candidates: Iterable[RawCandidate]) -> list[ProviderSource]:
    unique: dict[tuple[str, int], ProviderSource] = {}
    for candidate in candidates:
        key = (candidate.document_id, candidate.page)
        current = unique.get(key)
        source = ProviderSource(
            document_id=candidate.document_id,
            page=candidate.page,
            confidence=candidate.confidence,
        )
        if current is None or (source.confidence or 0) > (current.confidence or 0):
            unique[key] = source
    return list(unique.values())


def build_safe_context(
    case_id: str,
    documents: list[OcrDocument],
    secret: bytes,
) -> tuple[CaseProviderContext, dict[str, dict[str, str]]]:
    """返回公开安全上下文和仅供本地保存的真实名称映射。"""

    institution_candidates = extract_institution_candidates(documents)
    clinician_candidates = extract_clinician_candidates(documents)
    private_mapping: dict[str, dict[str, str]] = {"institutions": {}, "clinicians": {}}
    warnings: list[str] = []

    institution_ref: InstitutionReference | None = None
    institution_name: str | None = None
    institution_id: str | None = None
    if institution_candidates:
        counts = Counter(candidate.value for candidate in institution_candidates)
        weighted = defaultdict(float)
        for candidate in institution_candidates:
            weighted[candidate.value] += candidate.confidence
        institution_name = max(counts, key=lambda name: (counts[name], weighted[name], len(name)))
        selected = [c for c in institution_candidates if c.value == institution_name]
        institution_id = pseudonymous_id("HOSP", institution_name, secret)
        private_mapping["institutions"][institution_id] = institution_name
        competing = [name for name, count in counts.items() if name != institution_name and count > 0]
        review_required = bool(competing)
        if review_required:
            warnings.append("检测到多个机构名称候选，主机构需人工复核")
        confidence = min(0.99, sum(c.confidence for c in selected) / len(selected) + min(0.12, 0.03 * (len(selected) - 1)))
        institution_ref = InstitutionReference(
            institution_id=institution_id,
            confidence=round(confidence, 4),
            sources=_sources(selected),
            review_required=review_required,
        )
    else:
        warnings.append("未从 OCR 文本中识别出机构名称")

    grouped_clinicians: dict[tuple[str, ClinicianRole], list[RawCandidate]] = defaultdict(list)
    for candidate in clinician_candidates:
        assert candidate.role is not None
        grouped_clinicians[(candidate.value, candidate.role)].append(candidate)

    clinician_refs: list[ClinicianReference] = []
    for (name, role), candidates in sorted(grouped_clinicians.items(), key=lambda item: (item[0][1].value, item[0][0])):
        identity_basis = "|".join((institution_name or "unknown-institution", role.value, name))
        clinician_id = pseudonymous_id("DOC", identity_basis, secret)
        private_mapping["clinicians"][clinician_id] = name
        confidence = min(0.99, sum(c.confidence for c in candidates) / len(candidates) + min(0.08, 0.02 * (len(candidates) - 1)))
        clinician_refs.append(
            ClinicianReference(
                clinician_id=clinician_id,
                institution_id=institution_id,
                role=role,
                confidence=round(confidence, 4),
                ranking_eligible=role in _RANKING_ELIGIBLE,
                sources=_sources(candidates),
                review_required=confidence < 0.80,
            )
        )

    return (
        CaseProviderContext(
            case_id=case_id,
            institution=institution_ref,
            clinicians=clinician_refs,
            disease_groups=detect_disease_groups(documents),
            warnings=warnings,
        ),
        private_mapping,
    )
