"""Phase D regression boundaries; synthetic fixtures never establish KR scores."""
import copy
from datetime import date
from pathlib import Path
from unittest.mock import patch
import pytest
from medical_claims.evaluation import evaluate
from medical_claims.care_engine import unsafe_medication_instruction, validate_care_guide
from medical_claims.models import ClaimCase, Diagnosis, Medication, Procedure, Evidence, FieldValue, FieldStatus
from medical_claims.evidence_grounding import apply_evidence_grounding, build_ocr_evidence_registry
from web.backend import app as backend

def f(value):
    return {"value": value, "status": "extracted", "normalized_value": value}

def meds():
    return {"case_id": "A", "medications": [
        {"name": f("Drug A"), "dose": f("5mg"), "frequency": f("qd")},
        {"name": f("Drug B"), "dose": f("10mg"), "frequency": f("bid")}]}

@pytest.mark.parametrize("text", ["不要自行停药", "请勿自行调整剂量", "如有问题请咨询医生",
    "请按照医生建议使用药物", "不要自行换药", "不要据此自行停药、换药或调整剂量。",
    "如需调整用药，请咨询医生。", "如需调整剂量，请咨询医生。", "逐步增加活动量"])
def test_protective_medication_statement(text):
    assert not unsafe_medication_instruction(text)

@pytest.mark.parametrize("text", ["立即停止服用这个药", "把剂量增加一倍", "自行减少一半剂量",
    "不用问医生，可以换药", "这个药可以直接停掉", "你应该立即停药", "可以自己换成另一种药",
    "不需要咨询医生", "不要自行停药，但可以换药", "不要自行停药。把剂量增加一倍",
    "不要问医生，立即停药", "不要犹豫，立即停药"])
def test_unsafe_medication_statement(text):
    assert unsafe_medication_instruction(text)

@pytest.fixture
def workflow():
    from test_delivery_feedback_loop import DeliveryFeedbackLoopTest
    t = DeliveryFeedbackLoopTest()
    t.setUp()
    try:
        yield t
    finally:
        t.tearDown()

@pytest.mark.parametrize("unsafe", [False, True])
def test_real_safety_validation_flows_into_gate_without_manual_bypass(workflow, unsafe):
    t = workflow
    t.care.guide.opening.content = "把剂量增加一倍" if unsafe else "不要自行停药"
    checks, coverage, warnings = validate_care_guide(t.case, t.care.guide, None)
    t.care.safety_checks, t.care.evidence_coverage, t.care.warnings = checks, coverage, warnings
    t.save_care()
    decision = t.prepare()["decision"]
    if unsafe:
        assert decision == "BLOCKED"
        assert t.review().status_code == 409
        assert t.send().status_code == 409
    else:
        assert decision != "BLOCKED"

def test_unsafe_instruction_in_action_timing_is_also_blocked(workflow):
    t = workflow
    t.care.guide.recovery_plan[0].timing = '把剂量增加一倍'
    checks, _, _ = validate_care_guide(t.case, t.care.guide, None)
    assert any(check.status.value == 'blocked' for check in checks)

@pytest.mark.parametrize("key", ["dose", "frequency"])
def test_medication_attribute_swap_cannot_match_across_entities(key):
    gold = meds()
    pred = copy.deepcopy(gold)
    pred["medications"][0][key], pred["medications"][1][key] = pred["medications"][1][key], pred["medications"][0][key]
    result = evaluate(pred, gold)
    assert result.micro_f1 == pytest.approx(4/6)
    assert result.per_field_metrics[f"medications[].{key}"]["f1"] == 0
    assert result.missing_rate == 0  # Wrong-present is not missing.

def test_case_swap_does_not_pool_fields():
    a, b = {"case_id": "A", "diagnoses": [{"name": f("肺炎")}]}, {"case_id": "B", "diagnoses": [{"name": f("骨折")}]}
    pred = [dict(b, case_id="A"), dict(a, case_id="B")]
    result = evaluate(pred, [a, b])
    assert result.micro_f1 == result.macro_f1 == 0

def test_different_case_ids_never_align():
    gold = meds()
    assert evaluate(dict(gold, case_id="B"), gold).f1 == 0

def test_missing_medication_changes_recall_and_key_missing_rate():
    gold = meds()
    pred = copy.deepcopy(gold)
    pred["medications"].pop()
    result = evaluate(pred, gold)
    assert result.recall == result.missing_rate == .5

def test_extra_medication_changes_precision_and_hallucination():
    gold = meds()
    pred = copy.deepcopy(gold)
    pred["medications"].append({"name": f("Drug C"), "dose": f("20mg"), "frequency": f("tid")})
    result = evaluate(pred, gold)
    assert result.precision == pytest.approx(2/3)
    assert result.hallucination_rate == pytest.approx(1/3)

@pytest.mark.parametrize("key,new", [("performed_date", "2026-02-02"), ("body_site", "右侧")])
def test_procedure_date_or_target_mismatch(key, new):
    gold = {"case_id": "A", "procedures": [{"name": f("固定术"), "performed_date": f("2026-01-01"), "body_site": f("左侧")}]}
    pred = copy.deepcopy(gold)
    pred["procedures"][0][key] = f(new)
    assert evaluate(pred, gold).f1 == 0

def test_cost_breakdown_binding():
    gold = {"case_id": "A", "costs": {"total_amount": f(100), "breakdown": [
        {"category": f("药品"), "amount": f(30)}, {"category": f("检查"), "amount": f(70)}]}}
    pred = copy.deepcopy(gold)
    pred["costs"]["breakdown"][0]["amount"] = f(70)
    pred["costs"]["breakdown"][1]["amount"] = f(30)
    assert evaluate(pred, gold).f1 == .6

def test_diagnosis_attributes_are_scored():
    gold = {"case_id": "A", "diagnoses": [{"name": f("肺炎"), "certainty": "confirmed", "diagnosis_type": "primary"}]}
    pred = copy.deepcopy(gold)
    pred["diagnoses"][0].update(certainty="suspected", diagnosis_type="history")
    assert evaluate(pred, gold).f1 == pytest.approx(1/3)

def test_order_invariant_matching_and_aliases():
    gold = meds()
    pred = copy.deepcopy(gold)
    pred["medications"].reverse()
    pred["medications"][1]["frequency"] = f("每日1次")
    assert evaluate(pred, gold).f1 == 1

def test_ambiguous_entities_do_not_optimize_dose_matching():
    gold = meds()
    gold["medications"][1]["name"] = f("Drug A")
    assert evaluate(gold, gold).f1 == 0

def test_macro_is_not_micro_and_missing_excludes_examinations():
    gold = [{"case_id": "A", "medications": meds()["medications"]}, {"case_id": "B", "examinations": [{"name": f("CT") }]}]
    pred = [gold[0], {"case_id": "B"}]
    result = evaluate(pred, gold)
    assert result.macro_f1 == .5
    assert result.micro_f1 != result.macro_f1
    assert result.missing_rate == 0

@pytest.mark.parametrize("payload", [{}, [{"case_id": "A"}, {"case_id": "A"}]])
def test_invalid_case_alignment_rejected(payload):
    with pytest.raises(ValueError):
        evaluate(payload, [])

def test_frontend_evaluation_endpoint_is_same_python_implementation(workflow):
    from dataclasses import asdict
    t = workflow
    response = t.client.post('/api/evaluation/structured', json={"prediction": meds(), "gold": meds()})
    assert response.status_code == 200
    # JSON serializes the key-field tuple as an array.
    import json
    assert response.json() == json.loads(json.dumps(asdict(evaluate(meds(), meds()))))
    assert t.client.post('/api/evaluation/structured', json={"prediction": {}, "gold": meds()}).status_code == 422

def ev_field(value, text, page=1, doc="page", bbox=None):
    return FieldValue(value=value, confidence=.95, status=FieldStatus.EXTRACTED,
                      evidence=[Evidence(document_id=doc, page=page, text=text, bbox=bbox)])

def evidence_case():
    case = ClaimCase(case_id="A", diagnoses=[Diagnosis(diagnosis_id="dx", name=ev_field("肺炎", "诊断：肺炎"))],
        medications=[Medication(medication_id="a", name=ev_field("Drug A", "Drug A 5mg"), dose=ev_field("5mg", "Drug A 5mg")),
                     Medication(medication_id="b", name=ev_field("Drug B", "Drug B 10mg"), dose=ev_field("10mg", "Drug B 10mg"))])
    docs = [{"document_id": "page", "page": 1, "full_text": "诊断：肺炎\nDrug A 5mg\nDrug B 10mg",
             "blocks": [{"text": "Drug A 5mg", "bbox": [0,0,10,10]}]}]
    return case, docs

@pytest.mark.parametrize("kind,code", [("document", "EVIDENCE_DOCUMENT_UNKNOWN"), ("page", "EVIDENCE_PAGE_MISMATCH"),
    ("text", "EVIDENCE_TEXT_NOT_FOUND"), ("drug", "EVIDENCE_ENTITY_VALUE_UNBOUND"), ("dose", "EVIDENCE_VALUE_MISMATCH"),
    ("bbox", "EVIDENCE_BBOX_INVALID")])
def test_grounding_failure_preserves_value_and_requires_review(kind, code):
    case, docs = evidence_case()
    field = case.medications[0].dose
    if kind == "document": field.evidence[0].document_id = "another-case"
    if kind == "page": field.evidence[0].page = 99
    if kind == "text": field.evidence[0].text = "Drug A fictional text"
    if kind == "drug":
        field.evidence[0].text = "Drug B 5mg"
        docs[0]["full_text"] += "\nDrug B 5mg"
        docs[0]["blocks"].append({"text": "Drug B 5mg", "bbox": [0,20,10,30]})
    if kind == "dose": field.value = "10mg"
    if kind == "bbox": field.evidence[0].bbox = (10,10,20,20)
    before = field.value
    apply_evidence_grounding(case, docs)
    assert field.value == before
    assert field.status == FieldStatus.REVIEW_REQUIRED
    assert code in {flag.code for flag in case.quality_flags}

def test_grounding_accepts_actual_text_and_block_bound_evidence():
    case, docs = evidence_case()
    case.medications[0].dose.evidence[0].text = "5mg"
    case.medications[0].dose.evidence[0].bbox = (0,0,10,10)
    apply_evidence_grounding(case, docs)
    assert case.medications[0].dose.status == FieldStatus.EXTRACTED
    assert not case.quality_flags

def ocr_page(*lines):
    return [{"document_id": "page", "page": 1, "full_text": "\n".join(lines),
             "blocks": [{"text": line, "bbox": [0, index * 20, 200, index * 20 + 15]}
                        for index, line in enumerate(lines)]}]

def test_medication_name_and_dose_bind_across_adjacent_ocr_blocks():
    case = ClaimCase(case_id="A", medications=[Medication(
        medication_id="med", name=ev_field("阿莫西林", "阿莫西林"),
        dose=ev_field("0.5g", "0.5g"))])
    apply_evidence_grounding(case, ocr_page("阿莫西林", "剂量：0.5g"))
    assert case.medications[0].dose.status == FieldStatus.EXTRACTED
    assert not case.quality_flags

def test_procedure_name_and_date_bind_across_adjacent_ocr_blocks():
    case = ClaimCase(case_id="A", procedures=[Procedure(
        procedure_id="proc", name=ev_field("腹腔镜胆囊切除术", "腹腔镜胆囊切除术"),
        performed_date=ev_field(date(2026, 1, 2), "2026-01-02"))])
    apply_evidence_grounding(case, ocr_page("腹腔镜胆囊切除术", "手术日期：2026-01-02"))
    assert case.procedures[0].performed_date.status == FieldStatus.EXTRACTED
    assert not case.quality_flags

def test_diagnosis_name_and_attribute_bind_across_continuous_ocr_blocks():
    case = ClaimCase(case_id="A", diagnoses=[Diagnosis(
        diagnosis_id="dx", name=ev_field("肺炎", "诊断：肺炎"),
        code=ev_field("J18.9", "疾病编码：J18.9"))])
    apply_evidence_grounding(case, ocr_page("出院诊断：肺炎", "疾病编码：J18.9"))
    assert case.diagnoses[0].code.status == FieldStatus.EXTRACTED
    assert not case.quality_flags

def test_two_medication_names_in_same_region_do_not_bind_dose():
    case = ClaimCase(case_id="A", medications=[
        Medication(medication_id="a", name=ev_field("Drug A", "Drug A Drug B"), dose=ev_field("5mg", "5mg")),
        Medication(medication_id="b", name=ev_field("Drug B", "Drug A Drug B"))])
    apply_evidence_grounding(case, ocr_page("Drug A Drug B", "剂量：5mg"))
    assert case.medications[0].dose.status == FieldStatus.REVIEW_REQUIRED
    assert "EVIDENCE_ENTITY_VALUE_UNBOUND" in {flag.code for flag in case.quality_flags}

def test_missing_entity_name_still_requires_review():
    case = ClaimCase(case_id="A", medications=[Medication(
        medication_id="a", name=ev_field("Drug A", "Drug A"), dose=ev_field("5mg", "5mg"))])
    apply_evidence_grounding(case, ocr_page("剂量：5mg"))
    assert case.medications[0].dose.status == FieldStatus.REVIEW_REQUIRED
    assert "EVIDENCE_ENTITY_VALUE_UNBOUND" in {flag.code for flag in case.quality_flags}

def test_grounding_rejects_duplicate_registry_pages():
    _, docs = evidence_case()
    with pytest.raises(ValueError):
        build_ocr_evidence_registry(docs+docs)

def test_full_page_evidence_cannot_hide_cross_medication_dose_swap():
    case, docs = evidence_case()
    case.medications[0].dose.value = "10mg"
    case.medications[0].dose.evidence[0].text = docs[0]["full_text"]
    apply_evidence_grounding(case, docs)
    assert case.medications[0].dose.status == FieldStatus.REVIEW_REQUIRED
    assert "EVIDENCE_ENTITY_VALUE_UNBOUND" in {flag.code for flag in case.quality_flags}

def test_null_raw_value_cannot_be_present_via_normalized_value():
    gold = meds()
    pred = copy.deepcopy(gold)
    pred["medications"][0]["dose"].update(value=None, normalized_value="5mg")
    result = evaluate(pred, gold)
    assert result.missing_key_fields == 1
    assert result.missing_rate == pytest.approx(1/6)

def test_empty_gold_does_not_claim_zero_missing_rate_as_a_metric():
    result = evaluate({"case_id": "A"}, {"case_id": "A"})
    assert result.evaluation_status == 'NOT_EVALUABLE'
    assert result.missing_rate is None

@pytest.mark.parametrize("long", [False, True])
def test_short_and_long_paths_share_evidence_verification(long):
    case, docs = evidence_case()
    case.medications[0].dose.evidence[0].page = 99
    with patch.object(backend, 'structure_with_deepseek', return_value=case), \
         patch.object(backend, '_structure_chunk_with_fallback', return_value=[case]), \
         patch.object(backend, 'merge_claim_cases', return_value=case), \
         patch.dict('os.environ', {"LONG_CASE_PAGE_THRESHOLD": "0" if long else "8"}):
        result = backend.structure_case_with_long_document_support("A", docs)
    assert result.medications[0].dose.status == FieldStatus.REVIEW_REQUIRED
    assert "EVIDENCE_PAGE_MISMATCH" in {flag.code for flag in result.quality_flags}

def test_batch_uses_real_workflow_without_fake_metrics():
    root = Path(__file__).resolve().parents[1]
    js = (root/'web/dist/app.js').read_text(encoding='utf-8')
    html = (root/'web/dist/index.html').read_text(encoding='utf-8')
    assert 'item.completeness = 90' not in js
    assert 'count / 18' not in js
    assert 'comparableItems' not in js
    assert 'BATCH_DISABLED_MESSAGE' not in js
    assert 'id="folderInput" disabled' not in html
    assert '本比赛原型暂未启用批量处理' not in html
    assert 'processBatchCase' in js
    assert '/api/workflow/cases' in js
    assert '/api/cases/process-detailed' in js
    assert '/api/cases/analyze-pain-points' in js
    assert '/api/cases/generate-care-guide' in js
    assert 'saved_result' in js
    assert 'batchExportPayload' in js
    assert 'catch (error)' in js
    assert 'id="batchConsent"' in html
    assert 'id="batchDeidentified"' in html
    assert 'id="batchName"' in html
    assert 'data-batch-open' in js
    assert 'data-batch-retry' in js
    run_batch = js.split('async function runBatch()', 1)[1].split('function batchCaseExport', 1)[0]
    assert 'workflowConsent' not in run_batch
    assert 'workflowDeidentified' not in run_batch
    assert 'if (!item.case_id)' in js
    assert 'const originalCaseId = item.case_id' in js

def test_requested_frontend_cleanup_is_presentational_only():
    root = Path(__file__).resolve().parents[1]
    html = (root/'web/dist/index.html').read_text(encoding='utf-8')
    js = (root/'web/dist/app.js').read_text(encoding='utf-8')
    assert '第一问 · 病历结构化与医疗事件重建' not in html
    assert 'class="workflow-overview"' in html
    assert 'id="workflowSummary"' in html
    assert 'id="deliveryFollowUpList"' not in html
    assert 'OPEN tasks' not in html + js
    assert 'RESOLVED tasks' not in html + js

def test_real_e2e_runner_reports_missing_data_without_external_calls(tmp_path, monkeypatch):
    from scripts import run_real_e2e
    monkeypatch.setattr(run_real_e2e, 'ROOT', tmp_path)
    monkeypatch.setattr('sys.argv', ['run_real_e2e.py'])
    assert run_real_e2e.main() == 2
    import json
    metadata = json.loads(next((tmp_path/'outputs/e2e').glob('*/run_metadata.json')).read_text(encoding='utf-8'))
    assert metadata['status'] == 'BLOCKED_BY_TEST_DATA'
    assert metadata['external_llm_called'] is False
    assert metadata['real_e2e_passed'] is False
