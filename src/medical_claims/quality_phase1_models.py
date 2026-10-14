"""第四问 Phase 1 的独立能力画像与推荐数据契约。

这些模型不继承旧 ``QualityProfile`` / ``RecommendationReport``。旧模型继续用于
兼容既有接口；新主线以能力画像、患者情境和可追溯证据为核心。
"""

from __future__ import annotations

from enum import Enum

from pydantic import Field, model_validator

from .models import ClaimCase, StrictModel
from .pain_models import PainPointReport


class AssessmentStatus(str, Enum):
    EVALUATED = "evaluated"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    NOT_APPLICABLE = "not_applicable"
    NEEDS_REVIEW = "needs_review"


class QualityDimensionStatus(str, Enum):
    SCORED = "SCORED"
    NOT_SCORED = "NOT_SCORED"
    NEEDS_REVIEW = "NEEDS_REVIEW"


class QualityDimensionKey(str, Enum):
    DIAGNOSIS_TREATMENT_CONSISTENCY = "diagnosis_treatment_consistency"
    MEDICATION_RATIONALITY = "medication_rationality"
    PROCEDURE_RATIONALITY = "procedure_rationality"
    RISK_MANAGEMENT = "risk_management"
    DISCHARGE_FOLLOWUP = "discharge_followup"
    RECORD_COMPLETENESS = "record_completeness"


class QualityDimensionResult(StrictModel):
    dimension: QualityDimensionKey
    score: float | None = Field(ge=0, le=100)
    status: QualityDimensionStatus
    level: str | None
    reason: str = Field(min_length=1)
    evidence: list[str]
    limitations: list[str]

    @model_validator(mode="after")
    def validate_score_status(self) -> "QualityDimensionResult":
        if self.status in {QualityDimensionStatus.SCORED, QualityDimensionStatus.NEEDS_REVIEW}:
            if self.score is None:
                raise ValueError("已评分或待复核维度必须提供 score")
        elif self.score is not None:
            raise ValueError("NOT_SCORED 维度不得提供 score")
        return self


class LLMQualityEvaluation(StrictModel):
    dimensions: list[QualityDimensionResult]

    @model_validator(mode="after")
    def validate_dimensions(self) -> "LLMQualityEvaluation":
        expected = set(QualityDimensionKey)
        actual = [item.dimension for item in self.dimensions]
        if len(actual) != len(set(actual)):
            raise ValueError("品质评价维度不得重复")
        if set(actual) != expected:
            raise ValueError("品质评价必须完整包含六个固定维度")
        return self


class ComplexityLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    UNKNOWN = "unknown"


class AccessibilityLevel(str, Enum):
    SAME_CITY = "same_city"
    SAME_PROVINCE = "same_province"
    NEARBY_REGIONAL = "nearby_regional"
    CROSS_REGION = "cross_region"
    UNKNOWN = "unknown"


class HistoricalEvidenceLevel(str, Enum):
    NONE = "none"
    SINGLE_CASE = "single_case"
    LIMITED = "limited"
    SUBSTANTIAL = "substantial"


class GeographicRecommendationStrategy(str, Enum):
    LOCAL_FIRST = "local_first"
    PROVINCE_THEN_NEARBY = "province_then_nearby"
    UPGRADE_REFERRAL = "upgrade_referral"


class EvidenceVerificationStatus(str, Enum):
    VERIFIED = "verified"
    PARTIALLY_VERIFIED = "partially_verified"
    PENDING_VERIFICATION = "pending_verification"
    CONTRADICTED = "contradicted"


class PublicSource(StrictModel):
    source_id: str
    title: str
    url: str
    authority_type: str
    query_date: str
    evidence_grade: str = "A"
    evidence_confidence: float = Field(default=0.9, ge=0, le=1)
    verified: bool = True


class CapabilityEvidenceRecord(StrictModel):
    evidence_id: str
    source_id: str
    source_name: str
    source_type: str
    source_url: str
    query_date: str
    evidence_text: str = Field(min_length=1, max_length=500)
    evidence_field: str
    supported_claim: str
    reason: str | None = None
    evidence_grade: str = "A"
    evidence_confidence: float = Field(default=0.9, ge=0, le=1)
    verified: bool = True


class SuggestedEvidenceReason(StrictModel):
    reason: str
    evidence_type: str
    suggested_source_name: str | None = None
    suggested_source_url: str | None = None
    suggested_evidence_summary: str | None = None


class ProposedDimensionScore(StrictModel):
    score: float = Field(ge=0, le=100)
    reasoning: str


class ProposedHospitalScores(StrictModel):
    disease_specialty_match: ProposedDimensionScore
    geographic_convenience: ProposedDimensionScore
    hospital_professional_capability: ProposedDimensionScore
    clinician_resources: ProposedDimensionScore
    treatment_technology_capability: ProposedDimensionScore


class ProposedClinicianScores(StrictModel):
    disease_match: ProposedDimensionScore
    treatment_expertise: ProposedDimensionScore
    hospital_department_platform: ProposedDimensionScore
    clinical_qualification_capability: ProposedDimensionScore


class SuggestedHospitalCandidate(StrictModel):
    hospital_name: str
    city: str
    department: str
    recommendation_reasons: list[SuggestedEvidenceReason] = Field(min_length=2, max_length=3)
    dimension_scores: ProposedHospitalScores | None = None
    total_score: float | None = Field(default=None, ge=0, le=100)
    suggested_source_name: str | None = None
    suggested_source_url: str | None = None


class SuggestedClinicianCandidate(StrictModel):
    doctor_name: str
    hospital_name: str
    department: str
    specialty: str
    recommendation_reasons: list[SuggestedEvidenceReason] = Field(min_length=2, max_length=3)
    dimension_scores: ProposedClinicianScores | None = None
    total_score: float | None = Field(default=None, ge=0, le=100)
    suggested_source_name: str | None = None
    suggested_source_url: str | None = None


class DimensionAssessment(StrictModel):
    status: AssessmentStatus
    score: float | None = Field(default=None, ge=0, le=100)
    level: str | None = None
    confidence: float = Field(default=0, ge=0, le=1)
    reasoning: str
    evidence: list[str] = Field(default_factory=list)
    evidence_fields: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_evaluated_score(self) -> "DimensionAssessment":
        if self.status == AssessmentStatus.EVALUATED and self.score is None:
            raise ValueError("evaluated 评价必须有 score")
        if self.status in {AssessmentStatus.INSUFFICIENT_EVIDENCE, AssessmentStatus.NOT_APPLICABLE}:
            if self.score is not None:
                raise ValueError("证据不足或不适用的评价不得伪造 score")
        return self


class InstitutionFoundation(StrictModel):
    hospital_id: str
    hospital_name: str
    province: str | None = None
    city: str | None = None
    hospital_level: str | None = None
    hospital_type: str | None = None
    institution_role: str | None = None
    regional_medical_center: bool | None = None
    teaching_hospital: bool | None = None
    assessment: DimensionAssessment


class DiseaseSpecialtyCapability(StrictModel):
    disease_group: str
    related_departments: list[str] = Field(default_factory=list)
    key_specialties: list[str] = Field(default_factory=list)
    key_specialty_level: str | None = None
    disease_specific_clinic: str | None = None
    disease_specific_center: str | None = None
    specialty_capability_description: str | None = None
    specialty_match_level: str
    assessment: DimensionAssessment


class MedicalResourceCapability(StrictModel):
    senior_doctor_count: int | None = Field(default=None, ge=0)
    specialist_count: int | None = Field(default=None, ge=0)
    key_experts: list[str] = Field(default_factory=list)
    critical_care_capability: str | None = None
    multidisciplinary_capability: str | None = None
    surgery_or_intervention_capability: str | None = None
    key_equipment_or_technology: list[str] = Field(default_factory=list)
    resource_capability_level: str | None = None
    assessment: DimensionAssessment


class RegionalServiceCapability(StrictModel):
    local_service_role: str | None = None
    regional_referral_role: str | None = None
    complex_case_capability: str | None = None
    emergency_capability: str | None = None
    surrounding_city_coverage: str | None = None
    referral_suitability: str | None = None
    regional_role_level: str | None = None
    assessment: DimensionAssessment


class PatientServiceFeedback(StrictModel):
    feedback_count: int = Field(default=0, ge=0)
    communication_score: float | None = Field(default=None, ge=0, le=100)
    explanation_score: float | None = Field(default=None, ge=0, le=100)
    discharge_guidance_score: float | None = Field(default=None, ge=0, le=100)
    cost_transparency_score: float | None = Field(default=None, ge=0, le=100)
    follow_up_experience_score: float | None = Field(default=None, ge=0, le=100)
    overall_feedback_level: str | None = None
    assessment: DimensionAssessment


class HistoricalEvidenceSupport(StrictModel):
    historical_case_count: int = Field(default=0, ge=0)
    disease_specific_case_count: int = Field(default=0, ge=0)
    available_history: bool = False
    evidence_level: HistoricalEvidenceLevel = HistoricalEvidenceLevel.NONE
    history_summary: str
    supported_metrics: list[str] = Field(default_factory=list)
    evidence_coverage: float = Field(default=0, ge=0, le=1)
    source_ids: list[str] = Field(default_factory=list)
    assessment: DimensionAssessment


class HospitalCapabilityProfile(StrictModel):
    hospital_id: str
    disease_group: str
    institution_foundation: InstitutionFoundation
    disease_specialty: DiseaseSpecialtyCapability
    medical_resources: MedicalResourceCapability
    regional_service: RegionalServiceCapability
    patient_service_feedback: PatientServiceFeedback
    historical_evidence: HistoricalEvidenceSupport
    sources: list[PublicSource] = Field(default_factory=list)
    evidence_records: list[CapabilityEvidenceRecord] = Field(default_factory=list)
    profile_version: str = "hospital-capability-profile-v1"

    @model_validator(mode="after")
    def validate_sources(self) -> "HospitalCapabilityProfile":
        known = {source.source_id for source in self.sources}
        used: set[str] = set()
        dimensions = (
            self.institution_foundation.assessment,
            self.disease_specialty.assessment,
            self.medical_resources.assessment,
            self.regional_service.assessment,
            self.patient_service_feedback.assessment,
            self.historical_evidence.assessment,
        )
        for dimension in dimensions:
            used.update(dimension.source_ids)
        public_used = {item for item in used if not item.startswith("claim:") and not item.startswith("feedback:")}
        if not public_used.issubset(known):
            raise ValueError("医院画像引用了未登记的公开来源")
        if any(record.source_id not in known for record in self.evidence_records):
            raise ValueError("医院证据记录引用了未登记的公开来源")
        return self


class BasicQualification(StrictModel):
    clinician_id: str
    clinician_name: str
    hospital_id: str
    hospital_name: str
    department: str
    title: str | None = None
    professional_role: str | None = None
    assessment: DimensionAssessment


class ClinicianDiseaseExpertise(StrictModel):
    disease_group: str
    disease_expertise: list[str] = Field(default_factory=list)
    disease_specific_experience_description: str | None = None
    specialty_match_level: str
    assessment: DimensionAssessment


class ClinicianTreatmentExpertise(StrictModel):
    surgery_expertise: list[str] = Field(default_factory=list)
    intervention_expertise: list[str] = Field(default_factory=list)
    treatment_expertise: list[str] = Field(default_factory=list)
    complex_case_expertise: list[str] = Field(default_factory=list)
    special_population_expertise: list[str] = Field(default_factory=list)
    assessment: DimensionAssessment


class PlatformCapability(StrictModel):
    hospital_level: str | None = None
    department_strength: str | None = None
    key_specialty_support: str | None = None
    multidisciplinary_support: str | None = None
    critical_care_support: str | None = None
    advanced_technology_support: list[str] = Field(default_factory=list)
    assessment: DimensionAssessment


class CurrentCaseMatch(StrictModel):
    disease_match: str
    comorbidity_match: str | None = None
    treatment_need_match: str | None = None
    complexity_match: str | None = None
    overall_case_match: str
    assessment: DimensionAssessment


class ClinicianCapabilityProfile(StrictModel):
    clinician_id: str
    disease_group: str
    basic_qualification: BasicQualification
    disease_expertise: ClinicianDiseaseExpertise
    treatment_expertise: ClinicianTreatmentExpertise
    platform_capability: PlatformCapability
    current_case_match: CurrentCaseMatch
    historical_evidence: HistoricalEvidenceSupport
    sources: list[PublicSource] = Field(default_factory=list)
    evidence_records: list[CapabilityEvidenceRecord] = Field(default_factory=list)
    profile_version: str = "clinician-capability-profile-v1"

    @model_validator(mode="after")
    def validate_sources(self) -> "ClinicianCapabilityProfile":
        known = {source.source_id for source in self.sources}
        dimensions = (
            self.basic_qualification.assessment,
            self.disease_expertise.assessment,
            self.treatment_expertise.assessment,
            self.platform_capability.assessment,
            self.current_case_match.assessment,
            self.historical_evidence.assessment,
        )
        used = {source for item in dimensions for source in item.source_ids}
        public_used = {item for item in used if not item.startswith("claim:") and not item.startswith("feedback:")}
        if not public_used.issubset(known):
            raise ValueError("医生画像引用了未登记的公开来源")
        if any(record.source_id not in known for record in self.evidence_records):
            raise ValueError("医生证据记录引用了未登记的公开来源")
        return self


class TreatmentPattern(StrictModel):
    pattern_id: str
    disease_group: str
    readable_pattern_name: str
    treatment_description: str
    major_treatment: list[str] = Field(default_factory=list)
    procedures: list[str] = Field(default_factory=list)
    surgeries: list[str] = Field(default_factory=list)
    core_medications: list[str] = Field(default_factory=list)
    supportive_treatments: list[str] = Field(default_factory=list)
    discharge_plan: list[str] = Field(default_factory=list)
    evidence_fields: list[str] = Field(default_factory=list)


class HistoricalTreatmentReference(StrictModel):
    similar_historical_case_count: int = Field(default=0, ge=0)
    available: bool = False
    evidence_level: HistoricalEvidenceLevel = HistoricalEvidenceLevel.NONE
    average_fee: float | None = Field(default=None, ge=0)
    median_fee: float | None = Field(default=None, ge=0)
    average_length_of_stay: float | None = Field(default=None, ge=0)
    medication_observation: str | None = None
    treatment_standardization_observation: str | None = None
    readmission_observation: str | None = None
    common_pain_points: list[str] = Field(default_factory=list)
    risk_case_rate: float | None = Field(default=None, ge=0, le=1)
    evidence_coverage: float = Field(default=0, ge=0, le=1)
    source_ids: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class CurrentTreatmentEvaluation(StrictModel):
    case_id: str
    pattern: TreatmentPattern
    treatment_diagnosis_match: DimensionAssessment
    medication_rationality: DimensionAssessment
    procedure_rationality: DimensionAssessment
    treatment_completeness: DimensionAssessment
    discharge_continuity: DimensionAssessment
    risk_management: DimensionAssessment
    evidence_coverage: float = Field(ge=0, le=1)
    overall_confidence: float = Field(ge=0, le=1)
    key_findings: list[str] = Field(default_factory=list)
    review_reasons: list[str] = Field(default_factory=list)
    historical_reference: HistoricalTreatmentReference
    overall_score: float | None = Field(default=None, ge=0, le=100)
    overall_level: str | None = None
    scored_weight_coverage: float = Field(default=0, ge=0, le=1)
    evaluation_method: str = "RULES_FALLBACK"
    warnings: list[str] = Field(default_factory=list)
    evaluation_version: str = "current-treatment-evaluation-v2"


class DiseaseCatalogEntry(StrictModel):
    disease_group: str
    disease_label: str
    hospitals: list[HospitalCapabilityProfile] = Field(min_length=5)


class DiseaseProviderCatalog(StrictModel):
    diseases: list[DiseaseCatalogEntry] = Field(min_length=3)
    generated_from: str
    generated_at: str
    catalog_version: str = "disease-provider-catalog-v1"

    @model_validator(mode="after")
    def validate_unique_catalog(self) -> "DiseaseProviderCatalog":
        disease_groups = [item.disease_group for item in self.diseases]
        if len(disease_groups) != len(set(disease_groups)):
            raise ValueError("候选医院目录病种不得重复")
        for disease in self.diseases:
            keys = [profile.hospital_id for profile in disease.hospitals]
            if len(keys) != len(set(keys)):
                raise ValueError("同一病种候选医院不得重复")
        return self


class PatientRecommendationContext(StrictModel):
    case_id: str
    disease_group: str
    disease_label: str | None = None
    province: str | None = None
    city: str | None = None
    nearby_major_cities: list[str] = Field(default_factory=list)
    complexity: ComplexityLevel = ComplexityLevel.UNKNOWN
    comorbidities: list[str] = Field(default_factory=list)
    preferred_hospital_types: list[str] = Field(default_factory=list)
    needs_upgrade_referral: bool = False
    local_recommendation_limit: int | None = Field(default=None, ge=1, le=10)
    nearby_requires_public_tertiary_a: bool = False


class RecommendationIntent(StrictModel):
    """LLM 对本次就医需求的结构化理解；不包含最终推荐结论。"""

    primary_disease: str
    primary_disease_source: str = "case_structuring"
    treatment_goal: str
    primary_specialty: str
    required_specialty: str | None = None
    secondary_specialties: list[str] = Field(default_factory=list)
    required_capabilities: list[str] = Field(min_length=1)
    comorbidities: list[str] = Field(default_factory=list)
    case_complexity: ComplexityLevel
    geographic_strategy: GeographicRecommendationStrategy
    nearby_major_cities: list[str] = Field(min_length=2, max_length=5)
    local_recommendation_limit: int = Field(default=3, ge=1, le=5)
    nearby_requires_public_tertiary_a: bool = True
    search_queries: list[str] = Field(min_length=1, max_length=20)
    reasoning: str
    candidate_hospital_clues: list[str] = Field(default_factory=list)
    candidate_clinician_clues: list[str] = Field(default_factory=list)
    candidate_hospitals: list[SuggestedHospitalCandidate] = Field(default_factory=list, max_length=5)
    candidate_clinicians: list[SuggestedClinicianCandidate] = Field(default_factory=list, max_length=15)
    supporting_case_fields: list[str] = Field(default_factory=list)
    planner: str = "llm"

    @model_validator(mode="after")
    def validate_candidate_planner(self) -> "RecommendationIntent":
        if self.planner == "llm_scoring_planner_v3":
            if not self.required_specialty:
                raise ValueError("V2 推荐规划必须输出 required_specialty")
            if not 3 <= len(self.candidate_hospitals) <= 5:
                raise ValueError("V2 推荐规划必须输出 3 至 5 家候选医院")
            if any(item.dimension_scores is None for item in self.candidate_hospitals):
                raise ValueError("V2 推荐规划必须给每家医院输出固定五维分数")
            hospital_names = {item.hospital_name for item in self.candidate_hospitals}
            if any(item.hospital_name not in hospital_names for item in self.candidate_clinicians):
                raise ValueError("候选医生必须属于本次候选医院")
        return self


class GeographicAccessibility(StrictModel):
    patient_city: str | None = None
    patient_province: str | None = None
    hospital_city: str | None = None
    hospital_province: str | None = None
    same_city: bool = False
    same_province: bool = False
    nearby_major_city: bool = False
    distance_km: float | None = Field(default=None, ge=0)
    travel_level: AccessibilityLevel
    accessibility_level: str
    assessment: DimensionAssessment


class RecommendationReasonV2(StrictModel):
    dimension: str
    title: str
    explanation: str
    score: float = Field(ge=0, le=100)
    evidence_fields: list[str] = Field(min_length=1)
    source_ids: list[str] = Field(default_factory=list)
    evidence_record_ids: list[str] = Field(default_factory=list)
    evidence_confidence: float = Field(default=0.5, ge=0, le=1)
    verified: bool = False
    evidence_status: EvidenceVerificationStatus = EvidenceVerificationStatus.PENDING_VERIFICATION
    suggested_source_name: str | None = None
    suggested_source_url: str | None = None
    suggested_evidence_summary: str | None = None

    @model_validator(mode="after")
    def validate_traceability(self) -> "RecommendationReasonV2":
        historical = any(item.startswith(("claim:", "feedback:")) for item in self.source_ids)
        if self.verified and not self.evidence_record_ids and not historical:
            raise ValueError("正式推荐理由必须关联具体 evidence record")
        return self


class RecommendationDimensionScore(StrictModel):
    dimension: str
    label: str
    score: float = Field(ge=0, le=100)
    weight: float = Field(gt=0, le=1)
    reasoning: str
    evidence_confidence: float = Field(ge=0, le=1)
    evidence_status: EvidenceVerificationStatus = EvidenceVerificationStatus.PENDING_VERIFICATION
    source_ids: list[str] = Field(default_factory=list)
    evidence_record_ids: list[str] = Field(default_factory=list)


class HospitalRecommendationV2(StrictModel):
    rank: int = Field(ge=1)
    hospital_id: str
    hospital_name: str
    recommendation_score: float = Field(ge=0, le=100)
    recommendation_confidence: float = Field(ge=0, le=1)
    evidence_coverage: float = Field(ge=0, le=1)
    evidence_status: EvidenceVerificationStatus = EvidenceVerificationStatus.PENDING_VERIFICATION
    accessibility: GeographicAccessibility
    reasons: list[RecommendationReasonV2] = Field(min_length=2)
    dimension_scores: list[RecommendationDimensionScore] = Field(default_factory=list)
    evidence: list[str] = Field(min_length=2)
    limitations: list[str] = Field(default_factory=list)


class DiseaseCapabilityNeed(StrictModel):
    disease_group: str
    disease_label: str
    related_specialty_terms: list[str] = Field(default_factory=list)
    capability_terms: list[str] = Field(default_factory=list)
    resolution_source: str


class HospitalRecommendationReportV2(StrictModel):
    context: PatientRecommendationContext
    strategy: str
    recommendations: list[HospitalRecommendationV2] = Field(default_factory=list)
    excluded: dict[str, list[str]] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    candidate_profiles: list[HospitalCapabilityProfile] = Field(default_factory=list)
    candidate_clinician_profiles: list[ClinicianCapabilityProfile] = Field(default_factory=list)
    discovery_layers: list[str] = Field(default_factory=list)
    dynamic_discovery_attempted: bool = False
    capability_need: DiseaseCapabilityNeed | None = None
    recommendation_intent: RecommendationIntent | None = None
    discovery_diagnostics: DiscoveryDiagnostics | None = None
    engine_version: str = "capability-recommendation-engine-v1"


class ClinicianRecommendation(StrictModel):
    rank: int = Field(ge=1)
    clinician_id: str
    clinician_name: str
    hospital_id: str
    recommendation_score: float = Field(ge=0, le=100)
    recommendation_confidence: float = Field(ge=0, le=1)
    reasons: list[RecommendationReasonV2] = Field(min_length=2)
    dimension_scores: list[RecommendationDimensionScore] = Field(default_factory=list)
    evidence_confidence: float = Field(default=0.5, ge=0, le=1)
    evidence_status: EvidenceVerificationStatus = EvidenceVerificationStatus.PENDING_VERIFICATION
    limitations: list[str] = Field(default_factory=list)


class HospitalRecommendationRequestV2(StrictModel):
    """新医院推荐主线的案件情境输入。"""

    context: PatientRecommendationContext
    case: ClaimCase | None = None
    recommendation_intent: RecommendationIntent | None = None
    top_k: int = Field(default=5, ge=1, le=20)


class ProviderDiscoveryQuery(StrictModel):
    disease_group: str
    disease_label: str
    province: str | None = None
    city: str | None = None
    minimum_candidates: int = Field(default=5, ge=1, le=20)
    include_clinicians: bool = True
    capability_need: DiseaseCapabilityNeed | None = None
    recommendation_intent: RecommendationIntent | None = None
    nearby_major_cities: list[str] = Field(default_factory=list)
    complexity: ComplexityLevel = ComplexityLevel.UNKNOWN
    needs_upgrade_referral: bool = False


class DiscoveryCandidateDiagnostic(StrictModel):
    entity_type: str = "hospital"
    search_query: str
    search_title: str | None = None
    source_url: str
    authority_type: str | None = None
    page_read: bool = False
    hospital_name: str | None = None
    discovered_specialties: list[str] = Field(default_factory=list)
    evidence_text: str | None = None
    decision: str = "pending"
    rejection_reasons: list[str] = Field(default_factory=list)


class DiscoveryDiagnostics(StrictModel):
    disease_label: str
    city: str | None = None
    related_specialties: list[str] = Field(default_factory=list)
    generated_queries: list[str] = Field(default_factory=list)
    search_result_count: int = 0
    authoritative_candidate_count: int = 0
    page_read_count: int = 0
    hospital_identified_count: int = 0
    admitted_hospital_count: int = 0
    candidates: list[DiscoveryCandidateDiagnostic] = Field(default_factory=list)


class ProviderDiscoveryResult(StrictModel):
    query: ProviderDiscoveryQuery
    hospital_profiles: list[HospitalCapabilityProfile] = Field(default_factory=list)
    clinician_profiles: list[ClinicianCapabilityProfile] = Field(default_factory=list)
    discovery_layers: list[str] = Field(default_factory=list)
    dynamic_discovery_attempted: bool = False
    coverage_complete: bool = False
    warnings: list[str] = Field(default_factory=list)
    diagnostics: DiscoveryDiagnostics | None = None


class CapabilityCacheWriteRequest(StrictModel):
    hospital_profiles: list[HospitalCapabilityProfile] = Field(default_factory=list)
    clinician_profiles: list[ClinicianCapabilityProfile] = Field(default_factory=list)


class ClinicianRecommendationRequestV2(StrictModel):
    """医生能力画像推荐输入；不接收旧版推荐结果。"""

    disease_group: str
    profiles: list[ClinicianCapabilityProfile]
    allowed_hospital_ids: list[str] = Field(default_factory=list)
    recommendation_intent: RecommendationIntent | None = None
    top_k: int = Field(default=5, ge=1, le=20)


class TreatmentEvaluationRequestV2(StrictModel):
    """当前病例实际治疗方案评价输入。"""

    case: ClaimCase
    disease_group: str
    pain_report: PainPointReport | None = None
    historical_reference: HistoricalTreatmentReference | None = None


HospitalRecommendationReportV2.model_rebuild()
