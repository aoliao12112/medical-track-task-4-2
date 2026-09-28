"""外部能力接口。

官方 AI 中台接口明确后，只需实现这些抽象类，不应修改流水线。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from pydantic import Field

from .models import ClaimCase, Evidence, StrictModel


class DocumentInput(StrictModel):
    document_id: str
    path: Path
    mime_type: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class OcrBlock(StrictModel):
    text: str
    page: int = Field(ge=1)
    confidence: float | None = Field(default=None, ge=0, le=1)
    bbox: tuple[float, float, float, float] | None = None

    def as_evidence(self, document_id: str) -> Evidence:
        return Evidence(
            document_id=document_id,
            page=self.page,
            text=self.text,
            bbox=self.bbox,
        )


class OcrDocument(StrictModel):
    document_id: str
    document_type: str | None = None
    blocks: list[OcrBlock] = Field(default_factory=list)
    provider_metadata: dict[str, Any] = Field(default_factory=dict)


class MedicalEntity(StrictModel):
    entity_type: str
    text: str
    normalized_text: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    evidence: Evidence
    attributes: dict[str, Any] = Field(default_factory=dict)


class OcrProvider(ABC):
    @abstractmethod
    def recognize(self, document: DocumentInput) -> OcrDocument:
        """将图片或扫描 PDF 识别为带页码、坐标和置信度的文本块。"""


class MedicalNlpProvider(ABC):
    @abstractmethod
    def extract_entities(self, document: OcrDocument) -> list[MedicalEntity]:
        """提取诊断、药品、手术、检查、日期、金额等医学实体。"""


class LlmProvider(ABC):
    @abstractmethod
    def structure_case(
        self,
        case_id: str,
        documents: list[OcrDocument],
        entities: list[MedicalEntity],
        output_schema: dict[str, Any],
    ) -> ClaimCase:
        """跨文档归并实体并生成满足指定 Schema 的案件结果。"""

