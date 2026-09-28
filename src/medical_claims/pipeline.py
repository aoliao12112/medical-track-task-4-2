"""第一问端到端编排骨架。"""

from __future__ import annotations

from .models import ClaimCase
from .providers import DocumentInput, LlmProvider, MedicalNlpProvider, OcrProvider
from .validation import validate_case


class ClaimPipeline:
    def __init__(
        self,
        ocr_provider: OcrProvider,
        medical_nlp_provider: MedicalNlpProvider,
        llm_provider: LlmProvider,
    ) -> None:
        self.ocr_provider = ocr_provider
        self.medical_nlp_provider = medical_nlp_provider
        self.llm_provider = llm_provider

    def process(self, case_id: str, documents: list[DocumentInput]) -> ClaimCase:
        if not documents:
            raise ValueError("一个案件至少需要一份医疗文档")

        ocr_documents = [self.ocr_provider.recognize(doc) for doc in documents]
        entities = [
            entity
            for document in ocr_documents
            for entity in self.medical_nlp_provider.extract_entities(document)
        ]

        result = self.llm_provider.structure_case(
            case_id=case_id,
            documents=ocr_documents,
            entities=entities,
            output_schema=ClaimCase.model_json_schema(),
        )
        if result.case_id != case_id:
            raise ValueError("模型返回的 case_id 与请求不一致")

        result.quality_flags.extend(validate_case(result))
        result.events.sort(
            key=lambda event: (
                event.event_time is None,
                event.event_time.isoformat() if event.event_time else "",
            )
        )
        return ClaimCase.model_validate(result.model_dump())
