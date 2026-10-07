from __future__ import annotations

import copy
from pathlib import Path

from medical_claims.multisource_quality import (
    EvidenceSufficiency,
    build_multisource_candidate_pools,
    load_multisource_inputs,
)
from web.backend.app import app, get_multisource_candidate_pools


ROOT = Path(__file__).resolve().parents[1]


def inputs():
    return load_multisource_inputs(ROOT)


def test_three_priority_diseases_generate_truthful_candidate_pools():
    public, dataset = inputs()
    result = build_multisource_candidate_pools(public, dataset)
    pools = {item.disease_group: item for item in result.diseases}
    assert set(pools) == {"pneumonia", "influenza", "hypertension"}
    assert pools["pneumonia"].candidate_count == 9
    assert pools["influenza"].candidate_count == 7
    assert pools["hypertension"].candidate_count == 5
    assert result.generated_final_recommendation is False
    assert all(item.generated_final_recommendation is False for item in pools.values())


def test_every_candidate_has_two_traceable_recommendation_evidence_fields():
    public, dataset = inputs()
    result = build_multisource_candidate_pools(public, dataset)
    for pool in result.diseases:
        for candidate in pool.candidates:
            assert candidate.evidence_sufficiency == EvidenceSufficiency.SUFFICIENT
            assert len(set(candidate.recommendation_evidence_fields)) >= 2
            sources = {item.source_id: item for item in candidate.profile.sources}
            reasons = [
                item
                for item in candidate.profile.public_evidence
                if item.usable_for_recommendation
            ]
            assert len(reasons) >= 2
            for reason in reasons:
                assert reason.source_ids
                assert all(source_id in sources for source_id in reason.source_ids)
                assert any(sources[source_id].url for source_id in reason.source_ids)


def test_hospital_grade_alone_never_enters_candidate_pool():
    public, dataset = inputs()
    modified = copy.deepcopy(public)
    target = next(
        item for item in modified["hospitals"]
        if "肺炎" in item["local_case_disease_candidates"] and not item["disease_public_evidence"]
    )
    target["fields"]["hospital_grade"] = {
        "value": "三级甲等",
        "status": "available",
        "source_ids": target["fields"]["standard_name"]["source_ids"],
    }
    result = build_multisource_candidate_pools(modified, dataset)
    pool = next(item for item in result.diseases if item.disease_group == "pneumonia")
    excluded = next(item for item in pool.excluded if item.hospital_id == target["hospital_id"])
    assert not excluded.eligible_for_candidate_pool
    assert "hospital_grade" not in excluded.recommendation_evidence_fields


def test_single_historical_case_is_reference_not_hospital_quality_conclusion():
    public, dataset = inputs()
    result = build_multisource_candidate_pools(public, dataset)
    singleton = next(
        item
        for pool in result.diseases
        for item in pool.candidates
        if item.profile.historical_claims.official_deidentified_case_count == 1
    )
    history = singleton.profile.historical_claims
    assert history.stable_quality_conclusion_available is False
    assert history.usable_for_recommendation is False
    assert len(history.evidence) == 6
    assert all(not item.usable_for_recommendation for item in history.evidence)
    assert all(item.sample_size == 0 for item in history.evidence)
    assert "不能形成医院整体质量结论" in history.note


def test_missing_disease_specialty_evidence_is_excluded():
    public, dataset = inputs()
    result = build_multisource_candidate_pools(public, dataset)
    pneumonia = next(item for item in result.diseases if item.disease_group == "pneumonia")
    assert pneumonia.insufficient_count == 1
    assert any("缺少当前病种" in "；".join(item.failed_conditions) for item in pneumonia.excluded)


def test_candidate_pool_api_returns_same_evidence_gate():
    assert "/api/quality/multisource-candidate-pools" in {route.path for route in app.routes}
    data = get_multisource_candidate_pools()
    assert data.generated_final_recommendation is False
    assert {item.disease_group: item.candidate_count for item in data.diseases} == {
        "pneumonia": 9,
        "influenza": 7,
        "hypertension": 5,
    }
