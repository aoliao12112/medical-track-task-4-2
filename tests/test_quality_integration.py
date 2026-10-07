"""Explicit synthetic Phase B fixtures; all databases are temporary."""
from pathlib import Path
from decimal import Decimal
import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from medical_claims.care_engine import build_fallback_draft, finalize_care_guide
from medical_claims.care_models import GenerationMethod
from medical_claims.models import ClaimCase, Diagnosis, DiagnosisType, Certainty, Evidence, FieldValue, FieldStatus, Medication
from medical_claims.pain_engine import analyze_pain_points
from medical_claims.profile_records import (
    ProfileRecordInput, ConfirmedProviderContext, build_profile_case_record, resolve_disease_group,
)
from medical_claims.provider_extraction import build_safe_context
from medical_claims.provider_models import ClinicianRole
from medical_claims.providers import OcrDocument, OcrBlock
from medical_claims.quality_models import ProfileEntityType, QualityDimension, MetricStatus
from medical_claims.quality_integration import build_saved_portfolio
from medical_claims.quality_engine import recommend_profiles
from medical_claims.quality_coverage import audit_quality_coverage
from medical_claims.storage import CaseStorage
from medical_claims.workflow_models import CreateCaseRequest
from web.backend.app import app


def field(value):
    return FieldValue(value=value, confidence=.95, status=FieldStatus.EXTRACTED,
                      evidence=[Evidence(document_id="fixture", page=1, text="explicit synthetic fixture")])


def fixture(case_id, institution="测试甲医院", disease="pneumonia", extra_dimensions=False):
    names = {"fracture": "桡骨骨折", "pneumonia": "社区获得性肺炎", "diabetes": "2型糖尿病"}
    case = ClaimCase(case_id=case_id, diagnoses=[Diagnosis(diagnosis_id="dx-1", name=field(names[disease]),
                     diagnosis_type=DiagnosisType.PRIMARY, certainty=Certainty.CONFIRMED)],
                     medications=[Medication(medication_id="m1", name=field("测试药物"), dose=field("fixture dose"),
                                            frequency=field("fixture frequency"), route=field("fixture route"))])
    case.costs.total_amount = field(Decimal("1200"))
    documents = [OcrDocument(document_id="fixture", blocks=[
        OcrBlock(text="医院名称：" + institution, page=1, confidence=.95),
        OcrBlock(text="主治医师：张三", page=1, confidence=.95),
        OcrBlock(text="报告医师：李四", page=1, confidence=.95)])]
    context, _ = build_safe_context(case_id, documents, b"synthetic-test-key")
    confirmed = [item.clinician_id for item in context.clinicians if item.role == ClinicianRole.ATTENDING_PHYSICIAN]
    return ProfileRecordInput(case=case, pain_report=analyze_pain_points(case, [], []),
        provider=ConfirmedProviderContext(context=context, institution_confirmed=True, confirmed_clinician_ids=confirmed),
        data_source="synthetic_test", stable_patient_token="TEST-PATIENT-" + case_id if extra_dimensions else None,
        readmitted_within_30_days=False if extra_dimensions else None,
        patient_feedback_score=4.5 if extra_dimensions else None)


class QualityIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "competition.db"
        self.env = patch.dict(os.environ, {"COMPETITION_DB_PATH": str(self.path),
            "PROVIDER_HMAC_KEY_PATH": str(Path(self.tmp.name) / "hmac.key")})
        self.env.start()
        self.storage = CaseStorage()
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.env.stop()
        self.tmp.cleanup()

    def import_rows(self, rows):
        response = self.client.post("/api/quality/records/import", json={"records": [row.model_dump(mode="json") for row in rows]})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def ready_current_case(self):
        metadata = self.storage.create_case(CreateCaseRequest(claim_event_id="test-trigger", data_source="synthetic_test",
                           consent_status="authorized", deidentification_status="confirmed"))
        row = fixture(metadata.case_id)
        self.storage.save_structured(metadata.case_id, row.case)
        self.storage.save_pain(metadata.case_id, row.pain_report, row.case)
        draft = build_fallback_draft(row.case, row.pain_report, None, "肺炎")
        care = finalize_care_guide(row.case, draft, None, GenerationMethod.RULES_FALLBACK, [])
        self.storage.save_care(metadata.case_id, care, row.case, row.pain_report)
        response = self.client.post(f"/api/workflow/cases/{metadata.case_id}/quality-context",
                                    json={"provider": row.provider.model_dump(mode="json")})
        self.assertEqual(response.status_code, 200, response.text)
        return row

    def build(self):
        response = self.client.post("/api/quality/profiles/build", json={"use_saved_records": True, "minimum_sample_size": 3})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_assembly_confirmed_context(self):
        row = fixture("assembly")
        output = build_profile_case_record(row)
        self.assertIsNotNone(output.record)
        self.assertEqual(output.record.disease_group, "pneumonia")
        self.assertEqual(output.record.case.case_id, "assembly")
        self.assertEqual(len(output.record.clinician_ids), 1)
        self.assertTrue(output.record.treatment_plan_id.startswith("PLAN-"))

    def test_unconfirmed_institution_excluded(self):
        row = fixture("unconfirmed")
        row.provider.institution_confirmed = False
        output = build_profile_case_record(row)
        self.assertEqual(output.institution_id, "unavailable")
        self.assertIsNone(output.record)
        self.import_rows([row])
        self.assertEqual(self.build()["institution_count"], 0)

    def test_reporting_doctor_never_becomes_treating_doctor(self):
        row = fixture("reporter")
        row.provider.confirmed_clinician_ids = [item.clinician_id for item in row.provider.context.clinicians]
        output = build_profile_case_record(row)
        self.assertEqual(len(output.clinician_ids), 1)
        reporter = next(item for item in row.provider.context.clinicians if item.role == ClinicianRole.REPORTING_PHYSICIAN)
        self.assertNotIn(reporter.clinician_id, output.clinician_ids)

    def test_missing_outcome_data_are_unavailable(self):
        self.import_rows([fixture("missing-" + str(i)) for i in range(3)])
        portfolio = build_saved_portfolio(self.storage)
        for profile in portfolio.profiles:
            for dimension in (QualityDimension.READMISSION_CONTROL, QualityDimension.PATIENT_FEEDBACK, QualityDimension.MEDICATION_RATIONALITY):
                metric = next(m for m in profile.metrics if m.dimension == dimension)
                self.assertEqual(metric.status, MetricStatus.DATA_UNAVAILABLE)
                self.assertIsNone(metric.score)

    def test_small_samples_refuse(self):
        self.import_rows([fixture("small-1"), fixture("small-2")])
        portfolio = build_saved_portfolio(self.storage)
        for entity_type in ProfileEntityType:
            report = recommend_profiles(portfolio, "pneumonia", entity_type)
            self.assertFalse(report.generated)
            self.assertFalse(report.recommendations)

    def test_smoke_fixture_to_portfolio_to_current_api_and_reload(self):
        current = self.ready_current_case()
        self.import_rows([fixture("history-" + str(i)) for i in range(3)])
        portfolio_json = self.build()
        self.assertEqual({p["entity_type"] for p in portfolio_json["profiles"]}, {"institution", "clinician", "treatment_plan"})
        response = self.client.get(f"/api/workflow/cases/{current.case.case_id}/recommendations")
        self.assertEqual(response.status_code, 200, response.text)
        query = response.json()
        self.assertEqual(query["case_id"], current.case.case_id)
        self.assertEqual(query["disease_group"], "pneumonia")
        self.assertEqual(len(query["reports"]), 3)
        portfolio = self.storage.load_portfolio()
        for entry in query["reports"]:
            self.assertEqual(entry["status"], "available")
            item = entry["report"]["recommendations"][0]
            self.assertGreaterEqual(len(item["reasons"]), 2)
            profile = next(p for p in portfolio.profiles if p.entity_id == item["entity_id"])
            for reason in item["reasons"]:
                metric = next(m for m in profile.metrics if m.dimension.value == reason["dimension"])
                self.assertEqual(metric.status, MetricStatus.CALCULATED)
                self.assertEqual(metric.score, reason["score"])
                self.assertEqual(metric.evidence_fields, reason["evidence_fields"])
        reopened = CaseStorage(self.path)
        self.assertEqual(reopened.load_portfolio().model_dump(mode="json"), portfolio_json)
        self.assertEqual(len(reopened.load_profile_records()), 4)

    def test_disease_resolution_is_conservative(self):
        for group in ("fracture", "pneumonia", "diabetes"):
            self.assertEqual(resolve_disease_group(fixture("group", disease=group).case), group)
        case = fixture("uncertain").case
        case.diagnoses[0].certainty = Certainty.SUSPECTED
        self.assertEqual(resolve_disease_group(case), "unavailable")
        case.diagnoses[0].certainty = Certainty.CONFIRMED
        case.diagnoses[0].diagnosis_type = DiagnosisType.HISTORY
        self.assertEqual(resolve_disease_group(case), "unavailable")
        case.diagnoses[0].diagnosis_type = DiagnosisType.PRIMARY
        case.diagnoses[0].name = field("其他未支持病种")
        self.assertEqual(resolve_disease_group(case), "unavailable")

    def test_unknown_disease_not_enrolled(self):
        row = fixture("unknown")
        row.case.diagnoses[0].certainty = Certainty.UNKNOWN
        result = self.import_rows([row])
        self.assertEqual(result["dataset"]["excluded_cases"], 1)
        self.assertEqual(self.build()["disease_count"], 0)

    def test_duplicate_import_does_not_inflate_sample_size(self):
        row = fixture("same")
        self.import_rows([row])
        self.import_rows([row])
        self.assertEqual(len(self.storage.load_profile_records()), 1)
        response = self.client.post("/api/quality/records/import", json={"records": [row.model_dump(mode="json")] * 2})
        self.assertEqual(response.status_code, 422)

    def test_restructure_invalidates_context_record_and_portfolio(self):
        current = self.ready_current_case()
        self.build()
        self.storage.save_structured(current.case.case_id, current.case)
        self.assertIsNone(self.storage.load_portfolio())
        self.assertIsNone(self.storage.get_profile_context(current.case.case_id))
        self.assertEqual(len(self.storage.load_profile_records()), 0)

    def test_not_evaluated_then_empty_insufficient_evidence(self):
        current = self.ready_current_case()
        path = f"/api/workflow/cases/{current.case.case_id}/recommendations"
        self.assertEqual(self.client.get(path).json()["reports"][0]["status"], "not_evaluated")
        self.build()
        result = self.client.get(path).json()
        self.assertTrue(all(entry["status"] == "insufficient_evidence" for entry in result["reports"]))
        self.assertIn("samples=1", " ".join(result["reports"][0]["report"]["warnings"]))

    def test_missing_case_returns_404(self):
        for suffix in ("recommendations", "quality-context"):
            self.assertEqual(self.client.get("/api/workflow/cases/missing/" + suffix).status_code, 404)

    def test_provider_case_link_mismatch(self):
        current = self.ready_current_case()
        wrong = fixture("different")
        response = self.client.post(f"/api/workflow/cases/{current.case.case_id}/quality-context",
                                    json={"provider": wrong.provider.model_dump(mode="json")})
        self.assertEqual(response.status_code, 422)

    def test_no_false_kr4_pass_from_synthetic_fixtures(self):
        rows = [fixture(f"{disease}-{hospital}-{i}", institution=f"测试第{hospital}人民医院", disease=disease, extra_dimensions=True)
                for disease in ("fracture", "pneumonia", "diabetes") for hospital in range(5) for i in range(3)]
        self.import_rows(rows)
        portfolio = build_saved_portfolio(self.storage)
        coverage = audit_quality_coverage(portfolio, self.storage.profile_dataset_summary())
        self.assertEqual(coverage["ready_disease_count"], 3)
        self.assertEqual(coverage["status"], "NOT_READY")
        self.assertFalse(coverage["formal_kr4_pass"])
        for disease in coverage["diseases"]:
            self.assertEqual(disease["qualified_institution_count"], 5)
            self.assertGreaterEqual(disease["available_dimension_count"], 5)

    def test_legacy_preview_does_not_replace_confirmed_portfolio(self):
        self.assertIsNone(self.storage.load_portfolio())
        record = build_profile_case_record(fixture("preview")).record
        response = self.client.post("/api/quality/profiles/build", json={"records": [record.model_dump(mode="json")], "minimum_sample_size": 3})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNone(self.storage.load_portfolio())
        self.assertEqual(len(self.storage.load_profile_records()), 0)

    def test_confirmed_context_extraction_uses_saved_ocr_without_raw_names(self):
        current = self.ready_current_case()
        self.storage.save_ocr(current.case.case_id, [{"document_id": "source", "page": 1,
            "full_text": "医院名称：测试甲医院\n主治医师：张三", "blocks": []}])
        response = self.client.get(f"/api/workflow/cases/{current.case.case_id}/quality-context")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn("测试甲医院", response.text)
        self.assertNotIn("张三", response.text)
        self.assertIsNotNone(response.json()["candidates"]["institution"])

    def test_portfolio_availability_is_not_just_feature_presence(self):
        self.assertEqual(self.client.get("/api/quality/portfolio").json()["status"], "not_evaluated")
        self.import_rows([fixture("single")])
        self.build()
        snapshot = self.client.get("/api/quality/portfolio").json()
        self.assertEqual(snapshot["status"], "insufficient_evidence")
        self.assertTrue(snapshot["portfolio"]["profiles"])

    def test_unconfirmed_current_provider_refuses_even_with_history(self):
        current = self.ready_current_case()
        current.provider.institution_confirmed = False
        response = self.client.post(f"/api/workflow/cases/{current.case.case_id}/quality-context",
                                    json={"provider": current.provider.model_dump(mode="json")})
        self.assertEqual(response.status_code, 200)
        self.import_rows([fixture("verified-history-" + str(i)) for i in range(3)])
        self.build()
        response = self.client.get(f"/api/workflow/cases/{current.case.case_id}/recommendations")
        for entry in response.json()["reports"]:
            self.assertEqual(entry["status"], "insufficient_evidence")
            self.assertIn("unconfirmed", " ".join(entry["report"]["warnings"]))

    def test_conflicted_or_low_confidence_institution_is_not_enrolled(self):
        for name in ("conflicted", "low-confidence"):
            row = fixture(name)
            if name == "conflicted":
                row.provider.context.institution.review_required = True
            else:
                row.provider.context.institution.confidence = .4
            self.assertIsNone(build_profile_case_record(row).record)

    def test_unknown_authorization_does_not_partially_save_context(self):
        case = self.storage.create_case(CreateCaseRequest(claim_event_id="unknown", data_source="user_upload",
                     consent_status="unknown", deidentification_status="unknown"))
        row = fixture(case.case_id)
        self.storage.save_structured(case.case_id, row.case)
        response = self.client.post(f"/api/workflow/cases/{case.case_id}/quality-context",
                                    json={"provider": row.provider.model_dump(mode="json")})
        self.assertEqual(response.status_code, 409)
        self.assertIsNone(self.storage.get_profile_context(case.case_id))

    def test_pain_rerun_invalidates_portfolio_and_rebuilds_current_record(self):
        current = self.ready_current_case()
        self.build()
        self.storage.save_pain(current.case.case_id, current.pain_report, current.case)
        self.assertIsNone(self.storage.load_portfolio())
        self.assertIsNotNone(self.storage.get_profile_context(current.case.case_id))
        self.assertEqual(self.build()["case_count"], 1)
