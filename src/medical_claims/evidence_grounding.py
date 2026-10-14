"""Deterministic OCR provenance checks, not full medical semantic entailment."""
import re
import unicodedata
from pydantic import BaseModel
from .models import FieldValue, FieldStatus, QualityFlag, FlagSeverity

def normalize_evidence_text(text):
    text = unicodedata.normalize("NFKC", str(text)).casefold()
    # OCR engines disagree on whitespace and punctuation (including full-width
    # forms).  Preserve punctuation that changes numeric/clinical meaning:
    # decimals, ranges, routes/units and symbols such as antibody positivity.
    normalized = []
    dash_chars = {"-", "‐", "‑", "‒", "–", "—", "―", "−"}
    for index, char in enumerate(text):
        if char.isspace() or unicodedata.category(char).startswith("Z"):
            continue
        previous_is_digit = index > 0 and text[index - 1].isdigit()
        next_is_digit = index + 1 < len(text) and text[index + 1].isdigit()
        if char in {",", "."} and previous_is_digit and next_is_digit:
            normalized.append(".")
        elif char in dash_chars:
            normalized.append("-")
        elif char == "/" or not unicodedata.category(char).startswith("P"):
            normalized.append(char)
    return re.sub(r"-+", "-", "".join(normalized))

def iter_fields(node, path=""):
    if isinstance(node, FieldValue):
        yield path, node
    elif isinstance(node, BaseModel):
        for key in node.__class__.model_fields:
            yield from iter_fields(getattr(node, key), path+"."+key if path else key)
    elif isinstance(node, list):
        for index, item in enumerate(node):
            yield from iter_fields(item, f"{path}[{index}]")

def build_ocr_evidence_registry(documents):
    registry = {}
    for document in documents:
        key = (str(document["document_id"]), int(document["page"]))
        if key in registry:
            raise ValueError("OCR document_id/page 重复，证据来源不唯一")
        registry[key] = document
    return registry

def _text_sequences(page):
    """Return ordered OCR units without treating the whole page as one evidence region."""
    sequences = []
    blocks = [normalize_evidence_text(block.get("text", "")) for block in page.get("blocks", [])]
    blocks = [text for text in blocks if text]
    if blocks:
        sequences.append(blocks)
    lines = [normalize_evidence_text(line) for line in str(page.get("full_text", "")).splitlines()]
    lines = [text for text in lines if text]
    if lines and lines != blocks:
        sequences.append(lines)
    return sequences


def _spatial_row_regions(page):
    """Yield OCR text grouped by a narrow visual row on the same page.

    Laboratory tables are commonly emitted column-by-column, so adjacent list
    entries are not necessarily adjacent cells.  Bounding boxes let us recover
    one row without widening evidence to the whole page.
    """
    cells = []
    for block in page.get("blocks", []):
        bbox = block.get("bbox")
        text = normalize_evidence_text(block.get("text", ""))
        if not text or not bbox or len(bbox) != 4:
            continue
        x1, y1, x2, y2 = (float(value) for value in bbox)
        if x2 <= x1 or y2 <= y1:
            continue
        cells.append((x1, y1, x2, y2, text))
    seen = set()
    for anchor in cells:
        _, ay1, _, ay2, _ = anchor
        anchor_height = ay2 - ay1
        row = []
        for cell in cells:
            x1, y1, x2, y2, text = cell
            height = y2 - y1
            overlap = max(0.0, min(ay2, y2) - max(ay1, y1))
            # Ignore tall watermarks/headers crossing several table rows.
            comparable_height = height <= anchor_height * 2.5 and anchor_height <= height * 2.5
            if comparable_height and overlap >= min(anchor_height, height) * 0.45:
                row.append((x1, text))
        region = "".join(text for _, text in sorted(row))
        if region and region not in seen:
            seen.add(region)
            yield region

def _has_local_entity_binding(page, snippet, owner, peer_owners=(), required_value="",
                              allow_composed=False, spatial_regions=None):
    """Bind evidence inside one block or a short consecutive OCR region.

    A region is at most three adjacent OCR blocks/lines.  This tolerates OCR
    segmentation while refusing page-wide evidence and regions containing a
    competing entity of the same kind.
    """
    if not snippet or not owner:
        return False
    peers = tuple(peer for peer in peer_owners if peer and peer != owner)

    def supported(region, composed=False):
        return (((allow_composed and composed) or snippet in region) and owner in region
                and (not required_value or required_value in region)
                and not any(peer in region for peer in peers))

    # A short model-provided excerpt may already span several OCR blocks.
    # Capping its size prevents a full page from becoming universal evidence.
    # When composition is being used to establish that OCR really contains the
    # excerpt, the model-provided excerpt cannot prove itself.
    if not allow_composed and len(snippet) <= 240 and supported(snippet):
        return True
    for units in _text_sequences(page):
        for start in range(len(units)):
            region = ""
            for stop in range(start, min(len(units), start + 3)):
                region += units[stop]
                if supported(region):
                    return True
    regions = spatial_regions if spatial_regions is not None else _spatial_row_regions(page)
    for region in regions:
        if supported(region, composed=True):
            return True
    return False

def verify_case_evidence(case, documents):
    registry = build_ocr_evidence_registry(documents)
    ids = {key[0] for key in registry}
    flags = []
    spatial_cache = {}
    owners = {}
    owner_kinds = {}
    for kind in ("diagnoses", "procedures", "medications"):
        for index, entity in enumerate(getattr(case, kind)):
            path = f"{kind}[{index}]"
            owners[path] = normalize_evidence_text(entity.name.value or "")
            owner_kinds[path] = kind

    for examination_index, examination in enumerate(case.examinations):
        path = f"examinations[{examination_index}]"
        owners[path] = normalize_evidence_text(examination.name.value or "")
        owner_kinds[path] = "examinations"
        for result_index, result in enumerate(examination.results):
            result_path = f"{path}.results[{result_index}]"
            owners[result_path] = normalize_evidence_text(result.item_name.value or "")
            # Competing result names are limited to this examination/table.
            owner_kinds[result_path] = f"{path}.results"

    def cached_spatial_regions(evidence, page):
        key = (evidence.document_id, evidence.page)
        if key not in spatial_cache:
            spatial_cache[key] = tuple(_spatial_row_regions(page))
        return spatial_cache[key]

    def failures(evidence, owner="", peer_owners=(), check_owner=True,
                 required_value="", allow_local_composition=False):
        errors = []
        if evidence.document_id not in ids:
            return [("EVIDENCE_DOCUMENT_UNKNOWN", "引用了本案件不存在的文件")]
        page = registry.get((evidence.document_id, evidence.page))
        if page is None:
            return [("EVIDENCE_PAGE_MISMATCH", "证据page不存在或未提供明确页码")]
        snippet = normalize_evidence_text(evidence.text)
        full = normalize_evidence_text(page.get("full_text", ""))
        spatial_regions = (cached_spatial_regions(evidence, page)
                           if allow_local_composition or (owner and check_owner) else ())
        local_binding = bool(allow_local_composition and owner and required_value
                             and _has_local_entity_binding(
                                 page, snippet, owner, peer_owners, required_value,
                                 allow_composed=True,
                                 spatial_regions=spatial_regions,
                             ))
        text_found = bool(snippet and snippet in full) or local_binding
        if not text_found:
            errors.append(("EVIDENCE_TEXT_NOT_FOUND", "原文证据无法在对应OCR页定位"))
        blocks = page.get("blocks", [])
        if (text_found and owner and check_owner and not local_binding
                and not _has_local_entity_binding(
                    page, snippet, owner, peer_owners, spatial_regions=spatial_regions
                )):
            errors.append(("EVIDENCE_ENTITY_MISMATCH", "证据未绑定当前实体名称或相邻OCR证据区域"))
        if evidence.bbox is not None:
            valid = any(block.get("bbox") is not None and tuple(block["bbox"]) == tuple(evidence.bbox)
                        and snippet and snippet in normalize_evidence_text(block.get("text", "")) for block in blocks)
            if not valid:
                errors.append(("EVIDENCE_BBOX_INVALID", "bbox不对应包含该证据的OCR block"))
        return errors

    def record(errors, path, document_id=None):
        for code, reason in errors:
            flags.append(QualityFlag(code=code, severity=FlagSeverity.WARNING,
                         message="Evidence verification failed："+reason, field_path=path, document_id=document_id))

    for path, field in iter_fields(case):
        if field.value is None or field.status in {FieldStatus.NOT_PRESENT, FieldStatus.NOT_APPLICABLE}:
            continue
        owner = owners.get(path.rsplit(".", 1)[0], "")
        errors = []
        if not field.evidence and field.status != FieldStatus.DERIVED:
            errors = [("EVIDENCE_MISSING", "提取字段缺少原文证据")]
            record(errors, path)
        for evidence in field.evidence:
            owner_path = path.rsplit(".", 1)[0]
            owner_kind = owner_kinds.get(owner_path)
            peer_owners = [name for entity_path, name in owners.items()
                           if owner_kind and owner_kinds.get(entity_path) == owner_kind
                           and entity_path != owner_path and name]
            medication_attribute = path.startswith("medications[") and not path.endswith(".name")
            examination_field = path.startswith("examinations[")
            raw = normalize_evidence_text(field.value)
            found = failures(
                evidence, owner, peer_owners,
                check_owner=not medication_attribute and not examination_field,
                required_value=raw if examination_field else "",
                allow_local_composition=examination_field,
            )
            # Medication attributes must be supported by the bound source, not
            # simply borrow Drug A's name for Drug B's dose.
            if path.startswith("medications[") and field.status != FieldStatus.DERIVED:
                value_mismatch = bool(raw and raw not in normalize_evidence_text(evidence.text))
                if value_mismatch:
                    found.append(("EVIDENCE_VALUE_MISMATCH", "药品字段值未出现在该原文证据中"))
                page = registry.get((evidence.document_id, evidence.page))
                text_not_found = any(code == "EVIDENCE_TEXT_NOT_FOUND" for code, _ in found)
                if (not path.endswith(".name") and owner and raw and not value_mismatch
                        and page is not None and not text_not_found):
                    other_names = [name for entity_path, name in owners.items()
                                   if entity_path.startswith("medications[") and name != owner and name]
                    if not _has_local_entity_binding(page, normalize_evidence_text(evidence.text),
                                                     owner, other_names, raw,
                                                     spatial_regions=cached_spatial_regions(
                                                         evidence, page
                                                     )):
                        found.append(("EVIDENCE_ENTITY_VALUE_UNBOUND", "当前药名和字段值无法在无其他药名的相邻OCR证据区域中绑定"))
            errors.extend(found)
            record(found, path, evidence.document_id)
        if errors:
            if field.status == FieldStatus.EXTRACTED:
                field.status = FieldStatus.REVIEW_REQUIRED
            message = "Evidence verification failed；保留原值，请核对OCR原文与实体绑定。"
            if message not in field.warnings:
                field.warnings.append(message)
    for index, event in enumerate(case.events):
        if not event.evidence:
            record([("EVIDENCE_MISSING", "事件缺少原文证据")], f"events[{index}]")
        for evidence in event.evidence:
            record(failures(evidence), f"events[{index}].evidence", evidence.document_id)
    unique = {(flag.code, flag.field_path, flag.document_id, flag.message): flag for flag in flags}
    return list(unique.values())

def apply_evidence_grounding(case, documents):
    flags = verify_case_evidence(case, documents)
    unique = {(f.code, f.field_path, f.document_id, f.message): f for f in case.quality_flags + flags}
    case.quality_flags = list(unique.values())
    return case
