"""Official KR1 page-result to ClaimCase gold conversion tests."""

from __future__ import annotations

import json
from zipfile import ZipFile

import pytest

from medical_claims.models import ClaimCase
from scripts.convert_official_kr1_gold import convert_archive, write_results


def _page(material_type: str, fields: dict, filename: str) -> dict:
    return {
        "materialType": material_type,
        "imageType": "test",
        "status": "success",
        "fields": fields,
        "originalFilename": filename,
    }


def _archive(tmp_path):
    path = tmp_path / "official-kr1.zip"
    entries = {
        "命题材料提取结果/CL0001/discharge.json": _page("出院小结", {
            "admissionDate": "2026-01-02",
            "dischargeDate": "2026-01-06",
            "department": "呼吸科",
            "dischargeDiagnosis": ["肺炎", "高血压"],
            "patientName": "不应进入金标准",
            "pageNumber": 1,
        }, "出院小结.png"),
        "命题材料提取结果/CL0001/prescription.json": _page("处方笺", {
            "medication": "[阿莫西林（0.5g*12粒）,布洛芬]",
            "diagnosis": ["肺炎"],
            "pageNumber": 1,
        }, "处方.png"),
        "命题材料提取结果/CL0001/settlement.json": _page("医保结算单", {
            "inpatientDays": 4,
            "totalMedicalExpense": 1000.50,
            "insuranceRange": 800,
            "amountSelfTotal": 200.50,
            "poolingPaymentTotal": 600,
            "insurancePayment": 600,
            "totalPersonalBurden": 400.50,
            "personalCashPayment": 400.50,
        }, "结算单.png"),
        "命题材料提取结果/CL0001/cost.json": _page("住院费用清单", {
            "blocks": [{
                "block_index": 1,
                "costDetailList": [
                    {"detailName": "检查费项目", "feeAmount": 300, "itemCategoryName": "检查费"},
                    {"detailName": "药品项目", "feeAmount": 700.5, "itemCategoryName": "药品费"},
                ],
            }],
        }, "费用清单.png"),
        "命题材料提取结果/CL0001/exam.json": _page("检查报告", {
            "body_part": "胸部CT",
            "report_result": "肺部炎症影",
            "report_time": "2026-01-03",
        }, "检查报告.png"),
    }
    with ZipFile(path, "w") as archive:
        for name, payload in entries.items():
            archive.writestr(name, json.dumps(payload, ensure_ascii=False))
    return path


def test_official_pages_convert_to_valid_case_gold(tmp_path):
    archive = _archive(tmp_path)
    cases, provenance, page_count = convert_archive(archive)
    assert page_count == 5
    assert len(cases) == 1
    case = ClaimCase.model_validate(cases[0])
    assert case.case_id == "CL0001"
    assert case.encounter_type.value == "inpatient"
    assert str(case.hospitalization.admission_date.value) == "2026-01-02"
    assert case.hospitalization.length_of_stay_days.value == 4
    assert {item.name.value for item in case.diagnoses} == {"肺炎", "高血压"}
    assert {item.name.value for item in case.medications} == {"阿莫西林", "布洛芬"}
    assert case.examinations[0].name.value == "胸部CT"
    assert case.costs.total_amount.value == 1000.50
    assert {item.category.value for item in case.costs.breakdown} == {"检查费", "药品费"}
    assert "patientName" in provenance[0]["unmapped_official_fields"]
    assert "不应进入金标准" not in json.dumps(case.model_dump(mode="json"), ensure_ascii=False)


def test_writer_creates_aggregate_per_case_and_refuses_overwrite(tmp_path):
    archive = _archive(tmp_path)
    cases, provenance, page_count = convert_archive(archive)
    output = tmp_path / "official_gold"
    write_results(output, archive, cases, provenance, page_count)
    aggregate = json.loads((output / "official-kr1.gold.final.json").read_text(encoding="utf-8"))
    manifest = json.loads((output / "official-kr1.manifest.json").read_text(encoding="utf-8"))
    assert [item["case_id"] for item in aggregate] == ["CL0001"]
    assert manifest["source_authority"] == "official_competition_provider"
    assert manifest["prediction_used_for_conversion"] is False
    assert (output / "CL0001" / "gold" / "gold.final.json").is_file()
    with pytest.raises(FileExistsError):
        write_results(output, archive, cases, provenance, page_count)
