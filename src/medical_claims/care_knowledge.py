"""第三问病种知识卡读取与匹配。知识卡必须经医学人员审核后才能正式使用。"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import Field

from .care_models import ReviewStatus
from .models import StrictModel


class KnowledgeSource(StrictModel):
    title: str
    url: str
    organization: str
    accessed_at: str


class DiseaseKnowledgeCard(StrictModel):
    card_id: str
    disease: str
    aliases: list[str] = Field(default_factory=list)
    plain_explanation: str
    general_actions: list[str] = Field(default_factory=list)
    follow_up_questions: list[str] = Field(default_factory=list)
    red_flags: list[str] = Field(default_factory=list)
    prohibited_claims: list[str] = Field(default_factory=list)
    sources: list[KnowledgeSource] = Field(default_factory=list)
    review_status: ReviewStatus = ReviewStatus.DRAFT_PENDING_MEDICAL_REVIEW
    reviewer: str | None = None
    reviewed_at: str | None = None


def default_knowledge_root() -> Path:
    return Path(__file__).resolve().parents[2] / "knowledge" / "care_guides"


def load_knowledge_cards(root: Path | None = None) -> list[DiseaseKnowledgeCard]:
    directory = root or default_knowledge_root()
    if not directory.exists():
        return []
    return [
        DiseaseKnowledgeCard.model_validate_json(path.read_text(encoding="utf-8"))
        for path in sorted(directory.glob("*.json"))
    ]


def match_knowledge_card(
    diagnosis_names: list[str],
    disease_topic: str | None = None,
    cards: list[DiseaseKnowledgeCard] | None = None,
) -> DiseaseKnowledgeCard | None:
    available = cards if cards is not None else load_knowledge_cards()
    haystack = " ".join([disease_topic or "", *diagnosis_names]).lower()
    ranked: list[tuple[int, DiseaseKnowledgeCard]] = []
    for card in available:
        terms = [card.disease, *card.aliases]
        score = sum(len(term) for term in terms if term.lower() in haystack)
        if score:
            ranked.append((score, card))
    return max(ranked, key=lambda item: item[0])[1] if ranked else None


def knowledge_card_payload(card: DiseaseKnowledgeCard | None) -> dict[str, object] | None:
    """只向生成模型提供必要知识，不把审核人等管理字段混入提示词。"""

    if card is None:
        return None
    return {
        "card_id": card.card_id,
        "disease": card.disease,
        "plain_explanation": card.plain_explanation,
        "general_actions": card.general_actions,
        "follow_up_questions": card.follow_up_questions,
        "red_flags": card.red_flags,
        "prohibited_claims": card.prohibited_claims,
        "review_status": card.review_status.value,
    }

