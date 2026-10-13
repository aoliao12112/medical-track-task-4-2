"""Convert the official page-level KR1 extraction package into ClaimCase gold.

The official package contains one JSON document per page.  The KR1 evaluator
expects one ClaimCase per case, so this module performs a conservative,
deterministic format conversion.  It never uses model predictions and never
fills a field that is absent from the official package.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sys
from typing import Any, Iterable
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from medical_claims.models import ClaimCase  # noqa: E402


CONVERTER_VERSION = "official-kr1-page-to-claimcase-v2"
DEFAULT_ARCHIVE = ROOT / "关于基于理赔医护记录的“医护洞察与温度指引”引擎 KR1补充材料.zip"
DEFAULT_OUTPUT = ROOT / "outputs" / "official_kr1_gold_v2"
INPATIENT_TYPES = {"住院费用清单", "出院小结", "医保结算单", "入院记录", "病案首页", "住院诊断证明"}
EMPTY_VALUES = {"", "null", "none", "未知", "未提取", "未识别", "不详", "无记录"}


@dataclass(frozen=True)
class OfficialPage:
    case_id: str
    entry_name: str
    material_type: str
    image_type: str
    original_filename: str
    fields: dict[str, Any]

    @property
    def page_number(self) -> int | None:
        for key in ("pageNumber", "thisPage"):
            value = self.fields.get(key)
            try:
                number = int(str(value).strip())
            except (TypeError, ValueError):
                continue
            if number >= 1:
                return number
        return None


@dataclass(frozen=True)
class Observation:
    value: Any
    page: OfficialPage
    field_name: str


def readable_name(name: str) -> str:
    """Repair UTF-8 ZIP names stored without the UTF-8 flag."""

    try:
        return name.encode("cp437").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return name


def nonempty(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() not in EMPTY_VALUES
    if isinstance(value, (list, tuple, dict)):
        return bool(value)
    return True


def scalar_text(value: Any) -> str | None:
    if not nonempty(value):
        return None
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, Decimal)):
        return str(value)
    return None


def evidence(page: OfficialPage, value: Any) -> list[dict[str, Any]]:
    text = scalar_text(value)
    if text is None:
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return [{
        "document_id": page.original_filename or PurePosixPath(page.entry_name).name,
        "page": page.page_number,
        "text": text,
    }]


def extracted(value: Any, page: OfficialPage, *, warnings: Iterable[str] = ()) -> dict[str, Any]:
    return {
        "value": value,
        "normalized_value": value,
        "confidence": 1.0,
        "status": "extracted",
        "evidence": evidence(page, value),
        "warnings": list(warnings),
    }


def derived(value: Any, sources: Iterable[Observation], derivation: str) -> dict[str, Any]:
    source_list = list(sources)
    source_evidence: list[dict[str, Any]] = []
    for source in source_list:
        source_evidence.extend(evidence(source.page, source.value))
    return {
        "value": value,
        "normalized_value": value,
        "confidence": 1.0,
        "status": "derived",
        "evidence": source_evidence,
        "warnings": [],
        "derivation": derivation,
    }


def observations(pages: list[OfficialPage], field_name: str) -> list[Observation]:
    return [
        Observation(page.fields[field_name], page, field_name)
        for page in pages
        if nonempty(page.fields.get(field_name))
    ]


def choose(
    pages: list[OfficialPage],
    field_names: Iterable[str],
    *,
    material_priority: Iterable[str] = (),
) -> Observation | None:
    priorities = {name: index for index, name in enumerate(material_priority)}
    candidates: list[Observation] = []
    for field_name in field_names:
        candidates.extend(observations(pages, field_name))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (
        priorities.get(item.page.material_type, len(priorities)),
        item.page.entry_name,
        item.field_name,
    ))
    return candidates[0]


def conflict_warnings(
    pages: list[OfficialPage], field_names: Iterable[str], selected: Any,
) -> list[str]:
    values = {
        str(item.value).strip()
        for field_name in field_names
        for item in observations(pages, field_name)
        if nonempty(item.value)
    }
    if len(values) <= 1:
        return []
    return [f"官方分页结果存在 {len(values)} 个非空取值；按文档优先级选择当前值"]


def parse_date(value: Any) -> date | None:
    text = scalar_text(value)
    if not text:
        return None
    match = re.fullmatch(r"\s*(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})(?:日)?\s*", text)
    if not match:
        return None
    try:
        return date(*(int(part) for part in match.groups()))
    except ValueError:
        return None


def parse_decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip().replace(",", "").replace("￥", "").replace("¥", "")
    if not text or text.lower() in EMPTY_VALUES:
        return None
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    return number if number >= 0 else None


def split_top_level(text: str) -> list[str]:
    """Split bracketed official lists without splitting dosage parentheses."""

    value = text.strip()
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1].strip()
    if not value:
        return []
    items: list[str] = []
    start = 0
    depth = 0
    pairs = {"(": ")", "（": "）", "[": "]", "【": "】"}
    closers = set(pairs.values())
    for index, character in enumerate(value):
        if character in pairs:
            depth += 1
        elif character in closers and depth:
            depth -= 1
        elif character in {",", "，", ";", "；", "\n"} and depth == 0:
            item = value[start:index].strip()
            if item:
                items.append(item)
            start = index + 1
    final = value[start:].strip()
    if final:
        items.append(final)
    return items


def list_values(value: Any) -> list[str]:
    if isinstance(value, list):
        return [text for item in value if (text := scalar_text(item))]
    text = scalar_text(value)
    if not text:
        return []
    if text.startswith("[") and text.endswith("]"):
        return split_top_level(text)
    return [text]


def clean_entity_name(value: str) -> str | None:
    text = re.sub(r"^\s*(?:\d+[.、)]|[（(]\d+[）)])\s*", "", value).strip(" []【】,，;；")
    if text.lower() in EMPTY_VALUES or text in {"无", "否认"}:
        return None
    return text or None


def evaluation_key(value: str) -> str:
    """Use the evaluator's exact string normalization for entity de-duplication."""

    text = re.sub(r"\s+", "", value.strip().lower())
    return re.sub(r"[，,；;。]", "", text)


def medication_name(value: str) -> str | None:
    text = value.strip(" ,，;；")
    text = re.sub(r"^\[(?:西药|中药|中成药|药品|自费)\]", "", text).strip()
    text = re.sub(r"^\(\)", "", text).strip()
    match = re.search(r"[（(]", text)
    if match and match.start() > 0:
        text = text[:match.start()].strip()
    return clean_entity_name(text)


def stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:16]
    return f"{prefix}-{digest}"


def encounter_type(pages: list[OfficialPage]) -> str:
    material_types = {page.material_type for page in pages}
    if material_types & INPATIENT_TYPES:
        return "inpatient"
    treatments = {
        scalar_text(page.fields.get("treatmentType"))
        for page in pages
        if scalar_text(page.fields.get("treatmentType"))
    }
    if "急诊" in treatments:
        return "emergency"
    if treatments or material_types & {"门急诊病历", "门诊费用清单", "门诊诊断证明", "处方笺"}:
        return "outpatient"
    return "unknown"


def build_hospitalization(pages: list[OfficialPage]) -> tuple[dict[str, Any], list[str]]:
    result: dict[str, Any] = {}
    notes: list[str] = []
    priorities = ("病案首页", "出院小结", "医保结算单", "入院记录")
    for target, fields in (
        ("admission_date", ("admissionDate",)),
        ("discharge_date", ("dischargeDate",)),
    ):
        source = choose(pages, fields, material_priority=priorities)
        parsed = parse_date(source.value) if source else None
        if source and parsed:
            warnings = conflict_warnings(pages, fields, source.value)
            result[target] = extracted(parsed.isoformat(), source.page, warnings=warnings)
            notes.extend(warnings)
        elif source:
            notes.append(f"{source.field_name} 不是完整日期，未写入病例级金标准")

    department = choose(pages, ("department",), material_priority=("病案首页", "出院小结", "入院记录"))
    if department and (text := scalar_text(department.value)):
        warnings = conflict_warnings(pages, ("department",), department.value)
        result["department"] = extracted(text, department.page, warnings=warnings)
        notes.extend(warnings)

    inpatient_days = choose(pages, ("inpatientDays",), material_priority=("医保结算单",))
    if inpatient_days:
        try:
            days = int(inpatient_days.value)
        except (TypeError, ValueError):
            days = 0
        if days >= 0:
            result["length_of_stay_days"] = extracted(days, inpatient_days.page)
    if "length_of_stay_days" not in result and result.get("admission_date") and result.get("discharge_date"):
        start = date.fromisoformat(result["admission_date"]["value"])
        end = date.fromisoformat(result["discharge_date"]["value"])
        if end >= start:
            sources = [
                choose(pages, ("admissionDate",), material_priority=priorities),
                choose(pages, ("dischargeDate",), material_priority=priorities),
            ]
            result["length_of_stay_days"] = derived(
                (end - start).days,
                [item for item in sources if item],
                "由官方入院日期与出院日期相减得到",
            )
    return result, notes


def build_diagnoses(case_id: str, pages: list[OfficialPage]) -> list[dict[str, Any]]:
    field_types = (
        ("primaryDiagnosis", "primary"),
        ("dischargeDiagnosis", "discharge"),
        ("admissionDiagnosis", "admission"),
        ("secondaryDiagnosis", "secondary"),
        ("diagnosis", "unknown"),
    )
    rank = {"primary": 0, "discharge": 1, "admission": 2, "secondary": 3, "unknown": 4}
    found: dict[str, dict[str, Any]] = {}
    for field_name, diagnosis_type in field_types:
        for source in observations(pages, field_name):
            for raw_name in list_values(source.value):
                name = clean_entity_name(raw_name)
                if not name:
                    continue
                key = evaluation_key(name)
                candidate = {
                    "diagnosis_id": stable_id("diag", case_id, key),
                    "name": extracted(name, source.page),
                    "diagnosis_type": diagnosis_type,
                    "certainty": "confirmed",
                }
                previous = found.get(key)
                if previous is None or rank[diagnosis_type] < rank[previous["diagnosis_type"]]:
                    found[key] = candidate
    return sorted(found.values(), key=lambda item: (rank[item["diagnosis_type"]], item["name"]["value"]))


def build_medications(case_id: str, pages: list[OfficialPage]) -> list[dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for source in observations(pages, "medication"):
        for item in split_top_level(str(source.value)):
            name = medication_name(item)
            if not name:
                continue
            key = evaluation_key(name)
            found.setdefault(key, {
                "medication_id": stable_id("med", case_id, key),
                "name": extracted(name, source.page, warnings=["官方处方字段未拆分剂量、频次与途径"]),
            })
    return sorted(found.values(), key=lambda item: item["name"]["value"])


def build_examinations(case_id: str, pages: list[OfficialPage]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[tuple[OfficialPage, Observation, Observation | None, str]]] = defaultdict(list)
    for page in pages:
        if page.material_type not in {"检查报告", "病理报告"}:
            continue
        conclusion_source = next((
            Observation(page.fields[key], page, key)
            for key in ("report_result", "pathological", "pathological_content")
            if nonempty(page.fields.get(key))
        ), None)
        if conclusion_source is None:
            continue
        body_part = scalar_text(page.fields.get("body_part"))
        name = body_part or ("病理检查" if page.material_type == "病理报告" else "检查报告")
        performed = next((
            Observation(page.fields[key], page, key)
            for key in ("report_time", "visitDate", "post_time")
            if parse_date(page.fields.get(key))
        ), None)
        category = "pathology" if page.material_type == "病理报告" else "other"
        groups[(evaluation_key(name), category)].append((page, conclusion_source, performed, name))

    results: list[dict[str, Any]] = []
    for (name_key, category), items in sorted(groups.items()):
        first_page, first_conclusion, _, name = items[0]
        conclusions: list[str] = []
        conclusion_sources: list[Observation] = []
        for _, source, _, _ in items:
            text = scalar_text(source.value)
            if text and text not in conclusions:
                conclusions.append(text)
                conclusion_sources.append(source)
        conclusion_field = (
            extracted(conclusions[0], first_conclusion.page)
            if len(conclusions) == 1
            else derived("；".join(conclusions), conclusion_sources, "合并同一病例中同名官方检查报告结论")
        )
        dates = sorted({
            parsed.isoformat()
            for _, _, performed, _ in items
            if performed and (parsed := parse_date(performed.value))
        })
        record: dict[str, Any] = {
            "examination_id": stable_id("exam", case_id, name_key, category),
            "category": category,
            "name": (
                extracted(name, first_page)
                if scalar_text(first_page.fields.get("body_part"))
                else derived(name, [first_conclusion], "由官方材料类型生成检查名称")
            ),
            "conclusion": conclusion_field,
        }
        if len(dates) == 1:
            performed = next(
                source for _, _, source, _ in items
                if source and parse_date(source.value) and parse_date(source.value).isoformat() == dates[0]
            )
            record["performed_date"] = extracted(dates[0], performed.page)
        results.append(record)
    return results


def decimal_observation(pages: list[OfficialPage], field_name: str) -> tuple[Observation, Decimal] | None:
    source = choose(pages, (field_name,), material_priority=("医保结算单",))
    parsed = parse_decimal(source.value) if source else None
    return (source, parsed) if source and parsed is not None else None


def build_costs(pages: list[OfficialPage]) -> dict[str, Any]:
    result: dict[str, Any] = {"currency": "CNY"}
    mapping = {
        "total_amount": "totalMedicalExpense",
        "policy_scope_amount": "insuranceRange",
        "outside_policy_amount": "amountSelfTotal",
    }
    for target, official in mapping.items():
        selected = decimal_observation(pages, official)
        if selected:
            source, value = selected
            result[target] = extracted(value, source.page)

    nested_mappings = {
        "fund_payments": {
            "basic_pool_payment": "poolingPaymentTotal",
            "medical_assistance_payment": "medicalRescue",
            "civil_servant_subsidy": "civilServantSubsidy",
            "supplementary_insurance_payment": "supplementaryFundPayment",
            "other_payment": "otherPayment",
            "total_fund_payment": "insurancePayment",
        },
        "personal_burden": {
            "within_policy_self_pay": "personnelExpense",
            "outside_policy_self_expense": "amountSelfTotal",
            "total_personal_burden": "totalPersonalBurden",
        },
        "payment_channels": {
            "personal_account_payment": "personalAccountPayment",
            "family_account_payment": "mutualAidAccountPayment",
            "cash_payment": "personalCashPayment",
        },
    }
    for group, group_mapping in nested_mappings.items():
        values: dict[str, Any] = {}
        for target, official in group_mapping.items():
            selected = decimal_observation(pages, official)
            if selected:
                source, value = selected
                values[target] = extracted(value, source.page)
        if values:
            result[group] = values

    breakdown_sources: dict[str, list[Observation]] = defaultdict(list)
    for page in pages:
        for block in page.fields.get("blocks") or []:
            if not isinstance(block, dict):
                continue
            for detail in block.get("costDetailList") or []:
                if not isinstance(detail, dict):
                    continue
                amount = parse_decimal(detail.get("feeAmount"))
                if amount is None:
                    continue
                category = scalar_text(detail.get("itemCategoryName")) \
                    or scalar_text(detail.get("itemCategoryCode")) \
                    or "未分类费用"
                breakdown_sources[category].append(Observation(amount, page, "feeAmount"))
    breakdown: list[dict[str, Any]] = []
    for category, sources in sorted(breakdown_sources.items()):
        amount = sum((Decimal(str(source.value)) for source in sources), Decimal("0"))
        breakdown.append({
            "category": extracted(category, sources[0].page),
            "amount": derived(amount, sources, "汇总官方费用明细中的 feeAmount"),
        })
    if breakdown:
        result["breakdown"] = breakdown
    if "total_amount" not in result and breakdown_sources:
        all_sources = [source for sources in breakdown_sources.values() for source in sources]
        total = sum((Decimal(str(source.value)) for source in all_sources), Decimal("0"))
        result["total_amount"] = derived(total, all_sources, "汇总官方费用明细中的 feeAmount")

    total_value = parse_decimal(result.get("total_amount", {}).get("value"))
    if total_value and total_value > 0:
        ratios: dict[str, Any] = {}
        ratio_sources = {
            "within_policy_self_pay_ratio": result.get("personal_burden", {}).get("within_policy_self_pay"),
            "outside_policy_self_expense_ratio": result.get("personal_burden", {}).get("outside_policy_self_expense"),
            "total_personal_burden_ratio": result.get("personal_burden", {}).get("total_personal_burden"),
        }
        for target, field in ratio_sources.items():
            amount = parse_decimal(field.get("value")) if field else None
            if amount is not None:
                ratios[target] = {
                    "value": float(amount / total_value),
                    "normalized_value": float(amount / total_value),
                    "confidence": 1.0,
                    "status": "derived",
                    "evidence": field.get("evidence", []) + result["total_amount"].get("evidence", []),
                    "warnings": [],
                    "derivation": "对应官方金额除以官方总医疗费用",
                }
        if ratios:
            result["ratios"] = ratios
    return result


def build_case(case_id: str, pages: list[OfficialPage]) -> tuple[ClaimCase, dict[str, Any]]:
    hospitalization, notes = build_hospitalization(pages)
    payload = {
        "case_id": case_id,
        "encounter_type": encounter_type(pages),
        "hospitalization": hospitalization,
        "diagnoses": build_diagnoses(case_id, pages),
        "procedures": [],
        "medications": build_medications(case_id, pages),
        "examinations": build_examinations(case_id, pages),
        "costs": build_costs(pages),
        "events": [],
        "quality_flags": [
            {
                "code": "OFFICIAL_GOLD_FORMAT_CONVERSION",
                "severity": "info",
                "message": "由官方 KR1 分页提取结果确定性转换为 ClaimCase；未使用模型预测补值。",
            },
            *(
                [{
                    "code": "OFFICIAL_GOLD_SOURCE_CONFLICT",
                    "severity": "warning",
                    "message": note,
                } for note in sorted(set(notes))]
            ),
        ],
    }
    model = ClaimCase.model_validate(payload)
    mapped_fields = {
        "admissionDate", "dischargeDate", "department", "inpatientDays",
        "primaryDiagnosis", "secondaryDiagnosis", "admissionDiagnosis", "dischargeDiagnosis",
        "diagnosis", "medication", "body_part", "report_result", "pathological",
        "pathological_content", "report_time", "visitDate", "post_time", "blocks",
        "totalMedicalExpense", "insuranceRange", "amountSelfTotal", "poolingPaymentTotal",
        "medicalRescue", "civilServantSubsidy", "supplementaryFundPayment", "otherPayment",
        "insurancePayment", "personnelExpense", "totalPersonalBurden",
        "personalAccountPayment", "mutualAidAccountPayment", "personalCashPayment",
        "treatmentType",
    }
    present_fields = {key for page in pages for key in page.fields}
    provenance = {
        "case_id": case_id,
        "source_entries": [page.entry_name for page in pages],
        "material_types": sorted({page.material_type for page in pages}),
        "mapped_official_fields": sorted(present_fields & mapped_fields),
        "unmapped_official_fields": sorted(present_fields - mapped_fields),
        "conversion_notes": sorted(set(notes)),
    }
    return model, provenance


def load_official_pages(archive_path: Path) -> dict[str, list[OfficialPage]]:
    grouped: dict[str, list[OfficialPage]] = defaultdict(list)
    with ZipFile(archive_path) as archive:
        for entry in archive.infolist():
            if entry.is_dir():
                continue
            parts = PurePosixPath(readable_name(entry.filename)).parts
            case_id = next((part for part in parts if re.fullmatch(r"CL\d+", part)), None)
            if not case_id:
                raise ValueError(f"官方 JSON 路径缺少 case_id：{entry.filename}")
            try:
                payload = json.loads(archive.read(entry).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError(f"官方 JSON 无法读取：{entry.filename}") from exc
            if payload.get("status") != "success":
                raise ValueError(f"官方 JSON 状态不是 success：{entry.filename}")
            fields = payload.get("fields")
            if not isinstance(fields, dict):
                raise ValueError(f"官方 JSON fields 不是对象：{entry.filename}")
            grouped[case_id].append(OfficialPage(
                case_id=case_id,
                entry_name=readable_name(entry.filename),
                material_type=str(payload.get("materialType") or "其他"),
                image_type=str(payload.get("imageType") or ""),
                original_filename=str(payload.get("originalFilename") or PurePosixPath(entry.filename).name),
                fields=fields,
            ))
    if not grouped:
        raise ValueError("官方 ZIP 中没有可转换的病例 JSON")
    for pages in grouped.values():
        pages.sort(key=lambda page: page.entry_name)
    return dict(sorted(grouped.items()))


def convert_archive(archive_path: Path) -> tuple[list[ClaimCase], list[dict[str, Any]], int]:
    grouped = load_official_pages(archive_path)
    cases: list[ClaimCase] = []
    provenance: list[dict[str, Any]] = []
    for case_id, pages in grouped.items():
        model, source = build_case(case_id, pages)
        cases.append(model)
        provenance.append(source)
    return cases, provenance, sum(len(pages) for pages in grouped.values())


def archive_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_results(
    output_dir: Path,
    archive_path: Path,
    cases: list[ClaimCase],
    provenance: list[dict[str, Any]],
    page_count: int,
) -> None:
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"输出目录非空，拒绝覆盖：{output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    payloads = [case.model_dump(mode="json") for case in cases]
    for payload, source in zip(payloads, provenance, strict=True):
        case_dir = output_dir / payload["case_id"] / "gold"
        case_dir.mkdir(parents=True, exist_ok=True)
        (case_dir / "gold.final.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        (case_dir / "provenance.json").write_text(
            json.dumps(source, ensure_ascii=False, indent=2), encoding="utf-8",
        )
    aggregate_path = output_dir / "official-kr1.gold.final.json"
    aggregate_path.write_text(
        json.dumps(payloads, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    manifest = {
        "schema_version": "official-kr1-gold-manifest-v1",
        "converter_version": CONVERTER_VERSION,
        "source_archive": archive_path.name,
        "source_sha256": archive_sha256(archive_path),
        "source_authority": "official_competition_provider",
        "annotation_status": "official_source_format_converted",
        "prediction_used_for_conversion": False,
        "case_count": len(cases),
        "page_json_count": page_count,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "aggregate_gold": aggregate_path.name,
        "limitations": [
            "官方包是分页提取结果；本文件仅做确定性病例级格式转换。",
            "官方包未提供独立手术字段，转换结果不会从自由文本臆造手术。",
            "处方 medication 字段仅可靠拆出药名，剂量、频次和途径不作猜测。",
            "患者姓名、性别、年龄等非 ClaimCase 评测字段不会写入转换后的金标准。",
        ],
    }
    (output_dir / "official-kr1.manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, default=DEFAULT_ARCHIVE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--official-source-confirmed",
        action="store_true",
        help="确认该 ZIP 由赛事官方提供，允许在 manifest 中标记为官方来源",
    )
    args = parser.parse_args()
    if not args.official_source_confirmed:
        raise SystemExit("必须显式传入 --official-source-confirmed，拒绝把未确认来源标成官方金标准")
    archive_path = args.archive.resolve()
    if not archive_path.is_file():
        raise SystemExit(f"找不到官方 KR1 ZIP：{archive_path}")
    cases, provenance, page_count = convert_archive(archive_path)
    write_results(args.output.resolve(), archive_path, cases, provenance, page_count)
    print(json.dumps({
        "status": "converted",
        "case_count": len(cases),
        "page_json_count": page_count,
        "output": str(args.output.resolve()),
        "aggregate_gold": str((args.output.resolve() / "official-kr1.gold.final.json")),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
